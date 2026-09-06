"""Versioned canonical conversations and the importer port."""

from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

WarningCode = Literal[
    "attachments_ignored",
    "multimodal_ignored",
    "tool_content_ignored",
    "unknown_role_ignored",
    "alternate_branches_ignored",
    "hidden_messages_ignored",
    "unsupported_content_ignored",
    "developer_role_normalized",
]


class CanonicalBase(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class TextPart(CanonicalBase):
    type: Literal["text"] = "text"
    text: str


class CanonicalMessage(CanonicalBase):
    message_id: str
    role: Literal["system", "user", "assistant"]
    content: list[TextPart] = Field(min_length=1)


class ConversationMetadata(CanonicalBase):
    source_created_at: datetime | None = None
    importer_version: str
    warnings: list[WarningCode]


class CanonicalConversation(CanonicalBase):
    schema_name: Literal["kitchensoup.conversation/v1"] = Field(alias="schema")
    conversation_id: str
    title: str
    source: str = Field(min_length=1, max_length=100)
    messages: list[CanonicalMessage]
    metadata: ConversationMetadata


class ConversationImporter(Protocol):
    name: str
    version: str

    def detect(self, root: Path) -> list[Path]: ...
    def parse(self, paths: list[Path]) -> Iterator[CanonicalConversation]: ...
