from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from app.training.schemas import PlanModelSource

State = Literal[
    "QUEUED", "PREPARING", "STARTING", "RUNNING", "STOP_REQUESTED", "STOPPED", "FAILED", "LOST"
]
TERMINAL = {"STOPPED", "FAILED", "LOST"}


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DeploymentCreate(StrictModel):
    model_version_id: UUID
    execution_target_id: UUID | None = None
    artifact_id: UUID | None = None
    precision: Literal["fp16", "bf16"] = "fp16"
    lease_minutes: int = Field(default=30, ge=1, le=1440)


class LeaseExtension(StrictModel):
    minutes: int = Field(default=30, ge=1, le=1440)


class DeploymentSpec(StrictModel):
    schema_name: Literal["kitchensoup.deployment/v1"] = Field(
        default="kitchensoup.deployment/v1", alias="schema"
    )
    image_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    base_source: PlanModelSource
    artifact_id: UUID
    artifact_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    representation: Literal["full", "awq"] = "full"
    adapter: bool
    precision: Literal["fp16", "bf16"]
    max_model_len: int = Field(default=2048, ge=256, le=2048)


class DeploymentView(StrictModel):
    id: UUID
    model_version_id: UUID
    execution_target_id: UUID | None = None
    artifact_id: UUID
    status: State
    external_id: str
    expires_at: datetime
    keep_running: bool
    created_at: datetime
    precision: Literal["fp16", "bf16"]
    image_digest: str


Content = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=8000)]


class ChatMessage(StrictModel):
    role: Literal["system", "user", "assistant"]
    content: Content


class ChatRequest(StrictModel):
    messages: list[ChatMessage] = Field(min_length=1, max_length=32)

    @model_validator(mode="after")
    def bounded(self) -> "ChatRequest":
        if sum(len(m.content) for m in self.messages) > 16000:
            raise ValueError("Conversation exceeds the playground limit")
        if self.messages[-1].role != "user":
            raise ValueError("End with a user message")
        return self


class ChatResponse(StrictModel):
    content: str = Field(max_length=65536)
