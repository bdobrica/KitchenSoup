"""Bounded Chat Completions adapter; no SDK, model discovery or silent fallback."""

import json
from typing import Any, Protocol, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from app.providers.credentials import CredentialResolver
from app.providers.schemas import ChatRequest, ChatResponse, ProviderConfig, ProviderError

T = TypeVar("T", bound=BaseModel)


def reject_nonfinite(value: str) -> None:
    raise ValueError("Non-finite numbers are not JSON values")


class LLMProvider(Protocol):
    def chat(self, request: ChatRequest) -> ChatResponse: ...
    def structured_output(self, request: ChatRequest, response_model: type[T]) -> T: ...


def strict_schema(model: type[BaseModel]) -> dict[str, Any]:
    schema = model.model_json_schema()

    def visit(value: object) -> None:
        if isinstance(value, dict):
            if value.get("type") == "object":
                if not isinstance(value.get("properties"), dict) or value.get(
                    "additionalProperties"
                ) not in (None, False):
                    raise ProviderError(422, "Structured output requires fixed object properties")
                value["additionalProperties"] = False
                value["required"] = list(value["properties"])
            value.pop("default", None)
            for key in ("properties", "$defs", "definitions"):
                children = value.get(key, {})
                if isinstance(children, dict):
                    for child in children.values():
                        visit(child)
            for key in ("items", "anyOf", "allOf", "oneOf", "additionalProperties"):
                if key in value:
                    visit(value[key])
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(schema)
    if schema.get("type") != "object":
        raise ProviderError(422, "Structured output requires an object response model")
    return schema


class OpenAICompatibleProvider:
    def __init__(
        self,
        config: ProviderConfig,
        credentials: CredentialResolver,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.config = config.model_copy(deep=True)
        self.credentials = credentials
        self.transport = transport

    def _complete(self, request: ChatRequest, response_format: dict[str, Any] | None = None) -> str:
        if request.model not in self.config.model_names:
            raise ProviderError(422, "Choose a model configured for this provider")
        payload = request.model_dump()
        if response_format is not None:
            payload["response_format"] = response_format
        payload["stream"] = False
        payload["store"] = False
        try:
            encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode()
        except (ValueError, UnicodeError):
            raise ProviderError(422, "Provider request contains invalid text or schema") from None
        if len(encoded) > 1024 * 1024:
            raise ProviderError(422, "Provider request exceeds 1 MiB")
        secret = self.credentials.resolve(self.config.api_key_ref)
        headers = {"Content-Type": "application/json"}
        if secret is not None:
            headers["Authorization"] = "Bearer " + secret.get_secret_value()
        try:
            with httpx.Client(
                timeout=httpx.Timeout(60, connect=10),
                trust_env=False,
                follow_redirects=False,
                transport=self.transport,
            ) as client:
                with client.stream(
                    "POST",
                    self.config.base_url + "/chat/completions",
                    content=encoded,
                    headers=headers,
                ) as response:
                    if response.status_code in (401, 403):
                        raise ProviderError(502, "Provider rejected authentication or model access")
                    if response.status_code == 429:
                        raise ProviderError(503, "Provider rate limit reached; try again later")
                    if response.status_code >= 300:
                        raise ProviderError(
                            502,
                            "Provider rejected the request; check URL, model and supported options",
                        )
                    data = bytearray()
                    for chunk in response.iter_bytes():
                        data.extend(chunk)
                        if len(data) > 4 * 1024**2:
                            raise ProviderError(502, "Provider response exceeds 4 MiB")
            body = json.loads(data)
            choice = body["choices"][0]
            message = choice["message"]
            if message.get("refusal"):
                raise ProviderError(502, "Provider refused the request")
            if (
                choice.get("finish_reason") != "stop"
                or message.get("tool_calls")
                or message.get("function_call")
            ):
                raise ProviderError(
                    502, "Provider response was incomplete or requested unsupported tools"
                )
            content = message["content"]
            if not isinstance(content, str) or not content.strip():
                raise ProviderError(502, "Provider returned no usable text")
            return content
        except httpx.TimeoutException:
            raise ProviderError(503, "Provider request timed out") from None
        except (httpx.HTTPError, httpx.InvalidURL):
            raise ProviderError(503, "Provider connection failed") from None
        except (ValueError, KeyError, IndexError, TypeError, AttributeError, RecursionError):
            raise ProviderError(502, "Provider returned an invalid response") from None

    def chat(self, request: ChatRequest) -> ChatResponse:
        return ChatResponse(content=self._complete(request))

    def structured_output(self, request: ChatRequest, response_model: type[T]) -> T:
        schema = strict_schema(response_model)
        content = self._complete(
            request,
            {
                "type": "json_schema",
                "json_schema": {"name": "kitchensoup_response", "strict": True, "schema": schema},
            },
        )
        try:
            parsed = response_model.model_validate_json(content, strict=True)
            # Detect ignored extra properties and omitted defaults, including nested models.
            if json.loads(content, parse_constant=reject_nonfinite) != parsed.model_dump(
                mode="json", by_alias=True
            ):
                raise ValueError("Structured response changed during validation")
            return parsed
        except (ValidationError, ValueError):
            raise ProviderError(502, "Provider output did not match the requested schema") from None
