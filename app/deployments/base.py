from dataclasses import dataclass
from typing import Literal, Protocol
from uuid import UUID

from app.deployments.schemas import ChatRequest, ChatResponse, DeploymentSpec


@dataclass(frozen=True)
class DeploymentStatus:
    state: Literal["STARTING", "RUNNING", "FAILED", "LOST"]
    endpoint: str | None = None


class DeploymentExecutor(Protocol):
    def image_identity(self) -> str: ...
    def deploy(self, identifier: UUID, spec: DeploymentSpec) -> None: ...
    def status(self, identifier: UUID, spec: DeploymentSpec) -> DeploymentStatus: ...
    def stop(self, identifier: UUID, spec: DeploymentSpec) -> None: ...
    def chat(self, identifier: UUID, spec: DeploymentSpec, body: ChatRequest) -> ChatResponse: ...
