import json
from pathlib import Path

import httpx
import pytest
from pydantic import BaseModel, ConfigDict, SecretStr, ValidationError

from app.providers.credentials import LocalCredentialResolver
from app.providers.openai_compatible import OpenAICompatibleProvider, strict_schema
from app.providers.schemas import ChatRequest, ProviderConfig, ProviderError, ProviderMessage


class SyntheticCredentials:
    def resolve(self, reference: str | None) -> SecretStr:
        return SecretStr("synthetic-test-value")


def request() -> ChatRequest:
    return ChatRequest(
        model="alias", messages=[ProviderMessage(role="user", content="Synthetic prompt")]
    )


def completion(content: str = "OK", **choice: object) -> dict[str, object]:
    result: dict[str, object] = {
        "message": {"role": "assistant", "content": content},
        "finish_reason": "stop",
    }
    result.update(choice)
    return {"choices": [result]}


@pytest.mark.parametrize(
    "url",
    [
        "https://api.openai.com/v1",
        "http://gateway:4000",
        "http://gateway:4000/v1/",
        "https://gateway.example/proxy/openai/v1",
    ],
)
def test_url_joining_auth_and_explicit_models(url: str) -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        assert str(req.url) == url.rstrip("/") + "/chat/completions"
        assert req.headers["authorization"] == "Bearer synthetic-test-value"
        body = json.loads(req.content)
        assert body["model"] == "alias" and body["store"] is False and body["stream"] is False
        assert body["max_completion_tokens"] == 4096
        return httpx.Response(200, json=completion())

    client = OpenAICompatibleProvider(
        ProviderConfig(base_url=url, model_names=["alias"]),
        SyntheticCredentials(),
        httpx.MockTransport(handler),
    )
    assert client.chat(request()).content == "OK"
    with pytest.raises(ProviderError, match="configured"):
        client.chat(request().model_copy(update={"model": "unconfigured"}))


class Answer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    default: str
    count: int


def test_structured_output_wire_and_local_validation() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        payload = json.loads(req.content)
        schema = payload["response_format"]["json_schema"]
        assert schema["strict"] is True
        assert schema["schema"]["required"] == ["default", "count"]
        assert "default" in schema["schema"]["properties"]
        assert schema["schema"]["additionalProperties"] is False
        return httpx.Response(200, json=completion('{"default":"ok","count":2}'))

    client = OpenAICompatibleProvider(
        ProviderConfig(model_names=["alias"]), SyntheticCredentials(), httpx.MockTransport(handler)
    )
    assert client.structured_output(request(), Answer).count == 2
    for payload in (
        '{"default":"ok","count":"2"}',
        '{"default":"ok","count":2,"extra":1}',
        "not JSON",
    ):
        bad = OpenAICompatibleProvider(
            ProviderConfig(model_names=["alias"]),
            SyntheticCredentials(),
            httpx.MockTransport(
                lambda req, content=payload: httpx.Response(200, json=completion(content))
            ),
        )
        with pytest.raises(ProviderError, match="schema"):
            bad.structured_output(request(), Answer)


@pytest.mark.parametrize("status", [301, 307, 400, 401, 403, 429, 500])
def test_errors_are_sanitized_and_redirects_not_followed(status: int) -> None:
    calls = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(req)
        return httpx.Response(
            status,
            headers={"Location": "https://elsewhere.invalid"},
            text="synthetic-test-value sensitive diagnostic",
        )

    client = OpenAICompatibleProvider(
        ProviderConfig(model_names=["alias"]), SyntheticCredentials(), httpx.MockTransport(handler)
    )
    with pytest.raises(ProviderError) as error:
        client.chat(request())
    assert len(calls) == 1 and "synthetic-test-value" not in str(error.value)
    assert "sensitive diagnostic" not in str(error.value)


@pytest.mark.parametrize(
    "payload",
    [
        completion(finish_reason="length"),
        completion(message={"refusal": "no", "content": None}),
        completion(message={"content": "OK", "tool_calls": [{}]}),
        {"choices": []},
        {"choices": None},
    ],
)
def test_refusal_partial_and_invalid_responses(payload: dict[str, object]) -> None:
    client = OpenAICompatibleProvider(
        ProviderConfig(model_names=["alias"]),
        SyntheticCredentials(),
        httpx.MockTransport(lambda req: httpx.Response(200, json=payload)),
    )
    with pytest.raises(ProviderError):
        client.chat(request())


def test_timeout_response_limit_and_missing_auth(tmp_path: Path) -> None:
    resolver = LocalCredentialResolver(set(), tmp_path)

    def timeout(req: httpx.Request) -> httpx.Response:
        assert "authorization" not in req.headers
        raise httpx.ReadTimeout("private upstream details")

    config = ProviderConfig(model_names=["alias"], api_key_ref=None)
    client = OpenAICompatibleProvider(config, resolver, httpx.MockTransport(timeout))
    with pytest.raises(ProviderError, match="timed out"):
        client.chat(request())
    client = OpenAICompatibleProvider(
        config,
        resolver,
        httpx.MockTransport(lambda req: httpx.Response(200, content=b"x" * (4 * 1024**2 + 1))),
    )
    with pytest.raises(ProviderError, match="4 MiB"):
        client.chat(request())


def test_env_and_file_resolution_rotation_and_bounds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    resolver = LocalCredentialResolver({"SYNTHETIC_PROVIDER_KEY"}, tmp_path)
    monkeypatch.setenv("SYNTHETIC_PROVIDER_KEY", "synthetic-first")
    value = resolver.resolve("env://SYNTHETIC_PROVIDER_KEY")
    assert (
        value
        and value.get_secret_value() == "synthetic-first"
        and "synthetic-first" not in repr(value)
    )
    monkeypatch.setenv("SYNTHETIC_PROVIDER_KEY", "synthetic-second")
    value = resolver.resolve("env://SYNTHETIC_PROVIDER_KEY")
    assert value and value.get_secret_value() == "synthetic-second"
    key = tmp_path / "key"
    key.write_text("synthetic-file\n")
    value = resolver.resolve(key.as_uri())
    assert value and value.get_secret_value() == "synthetic-file"
    for ref in ("env://PATH", "file:///etc/passwd", "file://" + str(tmp_path / "missing")):
        with pytest.raises(ProviderError):
            resolver.resolve(ref)
    outside = tmp_path.parent / (tmp_path.name + "-outside")
    outside.write_text("synthetic-outside")
    (tmp_path / "link").symlink_to(outside)
    with pytest.raises(ProviderError):
        resolver.resolve((tmp_path / "link").as_uri())
    key.write_bytes(b"x" * 8193)
    with pytest.raises(ProviderError):
        resolver.resolve(key.as_uri())
    monkeypatch.setenv("SYNTHETIC_PROVIDER_KEY", "bad\nheader")
    with pytest.raises(ProviderError):
        resolver.resolve("env://SYNTHETIC_PROVIDER_KEY")


@pytest.mark.parametrize(
    "url",
    [
        "https://user:pass@example.com/v1",
        "https://example.com?key=value",
        "file:///etc/passwd",
        "https://example.com/#secret",
        "http://example.com\\x",
        "http://example.com:99999",
    ],
)
def test_configuration_rejects_secret_urls_and_bad_schemes(url: str) -> None:
    with pytest.raises(ValidationError):
        ProviderConfig(base_url=url, model_names=["alias"])
    with pytest.raises(ValidationError):
        ProviderConfig(api_key_ref="not-a-reference", model_names=["alias"])


def test_openai_defaults_and_fixed_schema_requirement() -> None:
    config = ProviderConfig(model_names=["alias", "alias"])
    assert (
        config.base_url == "https://api.openai.com/v1"
        and config.api_key_ref == "env://OPENAI_API_KEY"
    )
    assert config.model_names == ["alias"]

    class Dynamic(BaseModel):
        values: dict[str, str]

    with pytest.raises(ProviderError):
        strict_schema(Dynamic)


def test_structured_output_rejects_missing_defaults_and_nested_extras() -> None:
    class Child(BaseModel):
        value: str = "default-value"

    class Nested(BaseModel):
        child: Child

    # Rebuild local forward references for Pydantic's schema generation.
    Nested.model_rebuild()
    for content in ('{"child":{}}', '{"child":{"value":"ok","unexpected":true}}'):
        provider = OpenAICompatibleProvider(
            ProviderConfig(model_names=["alias"]),
            SyntheticCredentials(),
            httpx.MockTransport(
                lambda req, body=content: httpx.Response(200, json=completion(body))
            ),
        )
        with pytest.raises(ProviderError, match="schema"):
            provider.structured_output(request(), Nested)


def test_structured_output_rejects_nonfinite_json_numbers() -> None:
    class Numeric(BaseModel):
        value: float

    provider = OpenAICompatibleProvider(
        ProviderConfig(model_names=["alias"]),
        SyntheticCredentials(),
        httpx.MockTransport(lambda req: httpx.Response(200, json=completion('{"value":Infinity}'))),
    )
    with pytest.raises(ProviderError, match="schema"):
        provider.structured_output(request(), Numeric)
