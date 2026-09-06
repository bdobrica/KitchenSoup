"""Synchronous executor port, called from application threadpool services."""

from pathlib import Path
from typing import Literal, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.training.schemas import PlanPreview


class ExecutorError(Exception):
    """Safe operator-facing error; never a raw engine response."""


class JobStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")
    state: Literal["PREPARING", "RUNNING", "SUCCEEDED", "FAILED", "CANCELED", "MISSING"]
    exit_code: int | None = None
    message: str | None = None


class LogChunk(BaseModel):
    text: str
    cursor: int


class TrainingExecutor(Protocol):
    def image_identity(self) -> str: ...
    def submit(self, run_id: UUID, plan: PlanPreview, image: str) -> str: ...
    def status(self, external_id: str, image: str) -> JobStatus: ...
    def logs(
        self, external_id: str, stream: Literal["stdout", "stderr"], cursor: int
    ) -> LogChunk: ...
    def cancel(self, external_id: str, image: str) -> JobStatus: ...
    def cleanup(self, external_id: str, image: str) -> None: ...


class InputMaterializer(Protocol):
    def materialize(self, run_id: UUID, plan: PlanPreview, destination: Path) -> None: ...
