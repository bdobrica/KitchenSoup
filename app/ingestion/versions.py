"""Versioned dataset snapshots and engine-independent training examples."""

from collections.abc import Iterator
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.ingestion.conversations import CanonicalConversation


class VersionModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class DatasetVersionCreate(VersionModel):
    # Omission snapshots the saved conversation draft; [] explicitly selects none.
    conversation_ids: list[UUID] | None = Field(default=None, max_length=10_000)
    document_ingestion_ids: list[UUID] = Field(default_factory=list, max_length=16)


class ExampleMessage(VersionModel):
    role: Literal["system", "user", "assistant"]
    content: str


class TrainingExample(VersionModel):
    kind: Literal["conversation", "document"]
    source_id: UUID
    conversation_id: UUID | None = None
    source_conversation_id: str | None = None
    target_message_id: str | None = None
    document_ingestion_id: UUID | None = None
    document_row: int | None = None
    messages: list[ExampleMessage] = Field(default_factory=list)
    text: str | None = None


class DatasetStatistics(VersionModel):
    source_count: int = 0
    conversation_count: int = 0
    message_count: int = 0
    example_count: int = 0
    ignored_item_count: int = 0
    size_bytes: int = 0


class DatasetWarning(VersionModel):
    code: str
    source_id: UUID
    conversation_id: UUID | None = None
    count: int | None = None


class VersionArtifact(VersionModel):
    artifact_id: UUID
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(ge=0)
    format: str


class VersionSource(VersionModel):
    source_id: UUID
    filename: str
    license: str | None
    artifact: VersionArtifact


class VersionConversation(VersionModel):
    conversation_id: UUID
    source_conversation_id: str
    import_id: UUID
    source_id: UUID
    canonical: VersionArtifact


class VersionDocument(VersionModel):
    ingestion_id: UUID
    manifest: VersionArtifact
    output: VersionArtifact


class DatasetManifest(VersionModel):
    schema_name: Literal["kitchensoup.dataset-manifest/v1"] = Field(alias="schema")
    dataset_id: UUID
    dataset_version_id: UUID
    version: int = Field(ge=1)
    license: str | None
    converter: Literal["kitchensoup.examples/v1"] = "kitchensoup.examples/v1"
    sources: list[VersionSource]
    conversations: list[VersionConversation]
    documents: list[VersionDocument]
    examples: VersionArtifact
    statistics: DatasetStatistics
    warnings: list[DatasetWarning]


class DatasetVersionView(VersionModel):
    id: UUID
    dataset_id: UUID
    version: int
    created_at: datetime
    sha256: str
    manifest_artifact_id: UUID
    manifest: DatasetManifest


class ExamplePage(VersionModel):
    offset: int
    limit: int
    total: int
    items: list[TrainingExample]


def conversation_examples(
    canonical: CanonicalConversation, identifier: UUID, source_id: UUID
) -> tuple[Iterator[TrainingExample], int, int]:
    """Yield cumulative contexts lazily so the output cap bounds repeated context."""
    context: list[ExampleMessage] = []
    targets: list[tuple[int, str]] = []
    ignored = 0
    user_seen = False
    for message in canonical.messages:
        content = "\n".join(part.text for part in message.content)
        if not content.strip():
            ignored += 1
            continue
        context.append(ExampleMessage(role=message.role, content=content))
        if message.role == "user":
            user_seen = True
        elif message.role == "assistant":
            if user_seen:
                targets.append((len(context), message.message_id))
            else:
                ignored += 1

    def generate() -> Iterator[TrainingExample]:
        for end, message_id in targets:
            yield TrainingExample(
                kind="conversation",
                source_id=source_id,
                conversation_id=identifier,
                source_conversation_id=canonical.conversation_id,
                target_message_id=message_id,
                messages=context[:end],
            )

    return generate(), len(context), ignored
