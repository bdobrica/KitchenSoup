from datetime import datetime
from pathlib import PurePosixPath
from typing import Literal
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ProviderError(Exception):
    def __init__(self, status: int, message: str) -> None:
        self.status = status
        super().__init__(message)


class ProviderConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(default="OpenAI", min_length=1, max_length=200, pattern=r"\S")
    base_url: str = Field(default="https://api.openai.com/v1", max_length=2048)
    api_key_ref: str | None = Field(default="env://OPENAI_API_KEY", max_length=512)
    model_names: list[str] = Field(min_length=1, max_length=100)

    @field_validator("base_url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        try:
            parsed = urlsplit(value)
            port = parsed.port
        except ValueError:
            raise ValueError("Use an HTTP(S) API base URL") from None
        if (
            parsed.scheme not in ("http", "https")
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or "?" in value
            or "#" in value
            or "\\" in value
            or any(ord(c) <= 32 or ord(c) == 127 for c in value)
            or (port is not None and not 1 <= port <= 65535)
        ):
            raise ValueError("Use an HTTP(S) base URL without credentials, query or fragment")
        return value.rstrip("/")

    @field_validator("api_key_ref")
    @classmethod
    def validate_reference(cls, value: str | None) -> str | None:
        import re

        if value is None:
            return None
        if re.fullmatch(r"env://[A-Za-z_][A-Za-z0-9_]*", value):
            return value
        if value.startswith("file:///"):
            path = value[len("file://") :]
            if (
                not any(c in path for c in ("%", "?", "#", "\\"))
                and not any(ord(c) <= 32 or ord(c) == 127 for c in path)
                and ".." not in PurePosixPath(path).parts
                and not path.endswith("/")
            ):
                return value
        raise ValueError("Use env://NAME or file:///absolute/path; never enter the key itself")

    @field_validator("model_names")
    @classmethod
    def validate_models(cls, names: list[str]) -> list[str]:
        if any(
            not name or len(name) > 200 or any(ord(c) <= 32 or ord(c) == 127 for c in name)
            for name in names
        ):
            raise ValueError("Use explicit nonempty model names without whitespace")
        return list(dict.fromkeys(names))


class ProviderView(ProviderConfig):
    model_config = ConfigDict(extra="forbid", from_attributes=True)
    id: UUID
    created_at: datetime
    updated_at: datetime


class ProviderTestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model: str = Field(min_length=1, max_length=200)
    mode: Literal["chat", "structured"] = "chat"


class ProviderTestResult(BaseModel):
    provider_id: UUID
    model: str
    mode: Literal["chat", "structured"]
    status: Literal["ok"] = "ok"
    message: str = "Synthetic provider test succeeded; no dataset content was sent"


class ProviderMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: Literal["system", "user", "assistant"]
    content: str = Field(max_length=256 * 1024, repr=False)


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model: str = Field(min_length=1, max_length=200)
    messages: list[ProviderMessage] = Field(min_length=1, max_length=100, repr=False)
    max_completion_tokens: int = Field(default=4096, ge=1, le=16384)


class ChatResponse(BaseModel):
    content: str = Field(repr=False)
