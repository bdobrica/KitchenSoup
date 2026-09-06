from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

Label = Annotated[str, Field(min_length=1, max_length=200, pattern=r"\S")]


class DatasetCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: Label
    description: str | None = Field(default=None, max_length=4000)
    license: Label | None = None


class SourceAttach(BaseModel):
    model_config = ConfigDict(extra="forbid")
    artifact_id: UUID
    filename: str = Field(min_length=1, max_length=255, pattern=r"^[^/\\\x00-\x1f]+$")
    license: Label | None = None


class DocumentView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    title: str
    media_type: str
    canonical_artifact_id: UUID | None


class DatasetSourceView(BaseModel):
    id: UUID
    artifact_id: UUID
    filename: str
    kind: str
    license: str | None
    sha256: str
    size_bytes: int
    created_at: datetime
    documents: list[DocumentView]


class DatasetView(BaseModel):
    id: UUID
    name: str
    description: str | None
    license: str | None
    created_at: datetime
    sources: list[DatasetSourceView]


class SourceRemoved(BaseModel):
    artifact_id: UUID
    raw_artifact_retained: bool = True
