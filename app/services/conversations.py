"""Canonical import registration, source lineage and dataset-local selection."""

import hashlib
from tempfile import SpooledTemporaryFile
from typing import BinaryIO, cast
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import (
    Artifact,
    ArtifactDerivation,
    Conversation,
    ConversationImport,
    DatasetSource,
)
from app.db.session import unit_of_work
from app.ingestion.chatgpt import ChatGPTImporter
from app.ingestion.conversations import ConversationImporter
from app.ingestion.selection import ChatGPTImportView, ConversationSelection, ConversationView
from app.ingestion.sources import SourceError
from app.services.datasets import DatasetService
from app.storage.archives import ArchiveError, extract_zip
from app.storage.hashing import hash_content


class ConversationService:
    def __init__(
        self, datasets: DatasetService, importer: ConversationImporter | None = None
    ) -> None:
        self.datasets = datasets
        self.importer = importer if importer is not None else ChatGPTImporter()

    def import_source(self, dataset_id: UUID, source_id: UUID) -> ChatGPTImportView:
        artifacts = self.datasets.artifacts
        with unit_of_work(self.datasets.factory) as session:
            self.datasets._dataset(session, dataset_id, lock=True)
            source = self.datasets._source(session, dataset_id, source_id)
            existing = session.scalar(
                select(ConversationImport).where(
                    ConversationImport.source_id == source.id,
                    ConversationImport.importer == self.importer.name,
                    ConversationImport.importer_version == self.importer.version,
                )
            )
            if existing is not None:
                return ChatGPTImportView.model_validate(existing)
            if source.kind != "archive" or not source.filename.lower().endswith(".zip"):
                raise SourceError(422, "Choose a complete ChatGPT ZIP export source")
            raw = session.get(Artifact, source.artifact_id)
            assert raw is not None
            if raw.bucket != artifacts.bucket:
                raise SourceError(409, "Artifact belongs to a different configured store")
            import_id = uuid4()
            imported = ConversationImport(
                id=import_id,
                source_id=source.id,
                importer=self.importer.name,
                importer_version=self.importer.version,
                schema_version="kitchensoup.conversation/v1",
            )
            session.add(imported)
            session.flush()
            try:
                with (
                    artifacts.store.get(raw.object_key, max_bytes=artifacts.max_bytes) as body,
                    SpooledTemporaryFile(max_size=8 * 1024**2, mode="w+b") as output,
                ):
                    digest, size = hash_content(body, max_bytes=artifacts.max_bytes)
                    if digest != raw.sha256 or size != raw.size_bytes:
                        raise SourceError(
                            409, "Stored export no longer matches its registered hash"
                        )
                    body.seek(0)
                    sha = hashlib.sha256()
                    size = 0
                    with extract_zip(body) as root:
                        paths = self.importer.detect(root)
                        for canonical in self.importer.parse(paths):
                            line = (canonical.model_dump_json(by_alias=True) + "\n").encode("utf-8")
                            size += len(line)
                            if size > 128 * 1024**2:
                                raise SourceError(
                                    422, "Canonical conversation output exceeds 128 MiB"
                                )
                            output.write(line)
                            sha.update(line)
                            session.add(
                                Conversation(
                                    import_id=import_id,
                                    source_conversation_id=canonical.conversation_id,
                                    title=canonical.title,
                                    message_count=len(canonical.messages),
                                    source_created_at=canonical.metadata.source_created_at,
                                    warnings=canonical.metadata.warnings,
                                )
                            )
                    output.seek(0)
                    key = f"canonical/conversations/v1/{import_id}/conversations.jsonl"
                    artifacts.store.put(key, cast(BinaryIO, output))
                    artifact = Artifact(
                        bucket=artifacts.bucket,
                        object_key=key,
                        sha256=sha.hexdigest(),
                        size_bytes=size,
                        format="kitchensoup.conversation/v1",
                    )
                    session.add(artifact)
                    session.flush()
                    imported.canonical_artifact_id = artifact.id
                    session.add(
                        ArtifactDerivation(
                            child_id=artifact.id,
                            parent_id=raw.id,
                            operation=f"{self.importer.name}-import/v{self.importer.version}",
                        )
                    )
                    session.flush()
                    result = ChatGPTImportView.model_validate(imported)
                    session.commit()
                    return result
            except ArchiveError as error:
                raise SourceError(422, str(error)) from None
            except UnicodeError:
                raise SourceError(422, "Export contains invalid Unicode text") from None
            except OSError:
                raise SourceError(503, "Temporary import workspace unavailable") from None

    @staticmethod
    def _rows(session: Session, dataset_id: UUID) -> list[Conversation]:
        return list(
            session.scalars(
                select(Conversation)
                .join(ConversationImport)
                .join(DatasetSource)
                .where(DatasetSource.dataset_id == dataset_id)
                .order_by(Conversation.source_created_at, Conversation.id)
            )
        )

    def list_conversations(self, dataset_id: UUID) -> list[ConversationView]:
        with unit_of_work(self.datasets.factory) as session:
            self.datasets._dataset(session, dataset_id)
            return [ConversationView.model_validate(row) for row in self._rows(session, dataset_id)]

    def select(self, dataset_id: UUID, request: ConversationSelection) -> list[ConversationView]:
        with unit_of_work(self.datasets.factory) as session:
            self.datasets._dataset(session, dataset_id, lock=True)
            rows = self._rows(session, dataset_id)
            selected = set(request.conversation_ids)
            if not selected <= {row.id for row in rows}:
                raise SourceError(422, "Selection contains conversations outside this dataset")
            for row in rows:
                row.selected = row.id in selected
            session.flush()
            result = [ConversationView.model_validate(row) for row in rows]
            session.commit()
            return result
