from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ConversationView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    import_id: UUID
    source_conversation_id: str
    title: str
    message_count: int
    source_created_at: datetime | None
    selected: bool
    warnings: list[str]


class ChatGPTImportView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    source_id: UUID
    canonical_artifact_id: UUID | None
    importer: str
    importer_version: str
    schema_version: str


class ConversationSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    conversation_ids: list[UUID] = Field(max_length=10000)
