"""Immutable dataset creation and artifact-backed paginated previews."""

import hashlib
import json
from io import BytesIO
from uuid import UUID, uuid4

from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import (
    Artifact,
    ArtifactDerivation,
    Conversation,
    ConversationImport,
    DatasetSource,
    DatasetVersion,
    DocumentIngestion,
)
from app.db.session import unit_of_work
from app.ingestion.conversations import CanonicalConversation
from app.ingestion.documents import DocumentIngestionManifest
from app.ingestion.sources import SourceError
from app.ingestion.versions import (
    DatasetManifest,
    DatasetStatistics,
    DatasetVersionCreate,
    DatasetVersionView,
    DatasetWarning,
    ExamplePage,
    TrainingExample,
    VersionArtifact,
    VersionConversation,
    VersionDocument,
    VersionSource,
    conversation_examples,
)
from app.services.datasets import DatasetService
from app.storage.hashing import hash_content

MAX_BYTES = 128 * 1024**2
MAX_OUTPUT = 64 * 1024**2


class DatasetVersionService:
    def __init__(self, datasets: DatasetService) -> None:
        self.datasets = datasets

    def _read(
        self, session: Session, identifier: UUID, maximum: int = MAX_BYTES
    ) -> tuple[Artifact, bytes]:
        artifact = session.get(Artifact, identifier)
        if artifact is None:
            raise SourceError(409, "Referenced dataset artifact is missing")
        if artifact.bucket != self.datasets.artifacts.bucket:
            raise SourceError(409, "Artifact belongs to a different configured store")
        with self.datasets.artifacts.store.get(artifact.object_key, max_bytes=maximum) as body:
            digest, size = hash_content(body, max_bytes=maximum)
            if (digest, size) != (artifact.sha256, artifact.size_bytes):
                raise SourceError(409, "Dataset artifact no longer matches its registered hash")
            body.seek(0)
            return artifact, body.read()

    @staticmethod
    def _reference(artifact: Artifact) -> VersionArtifact:
        return VersionArtifact(
            artifact_id=artifact.id,
            sha256=artifact.sha256,
            size_bytes=artifact.size_bytes,
            format=artifact.format,
        )

    def _write(
        self, session: Session, key: str, body: bytes, format: str, parents: set[UUID]
    ) -> Artifact:
        self.datasets.artifacts.store.put(key, BytesIO(body))
        artifact = Artifact(
            bucket=self.datasets.artifacts.bucket,
            object_key=key,
            sha256=hashlib.sha256(body).hexdigest(),
            size_bytes=len(body),
            format=format,
        )
        session.add(artifact)
        session.flush()
        for identifier in parents:
            session.add(
                ArtifactDerivation(
                    child_id=artifact.id, parent_id=identifier, operation="kitchensoup.examples/v1"
                )
            )
        return artifact

    @staticmethod
    def _view(row: DatasetVersion) -> DatasetVersionView:
        if (
            row.manifest_version != "kitchensoup.dataset-manifest/v1"
            or row.manifest_artifact_id is None
        ):
            raise SourceError(409, "This dataset version uses an unsupported manifest")
        try:
            manifest = DatasetManifest.model_validate(row.manifest)
        except ValidationError:
            raise SourceError(409, "Stored dataset manifest is invalid") from None
        return DatasetVersionView(
            id=row.id,
            dataset_id=row.dataset_id,
            version=row.version,
            created_at=row.created_at,
            sha256=row.sha256,
            manifest_artifact_id=row.manifest_artifact_id,
            manifest=manifest,
        )

    def list_versions(self, dataset_id: UUID) -> list[DatasetVersionView]:
        with unit_of_work(self.datasets.factory) as session:
            self.datasets._dataset(session, dataset_id)
            return [
                self._view(row)
                for row in session.scalars(
                    select(DatasetVersion)
                    .where(DatasetVersion.dataset_id == dataset_id)
                    .order_by(DatasetVersion.version.desc())
                )
            ]

    def get(self, dataset_id: UUID, version_id: UUID) -> DatasetVersionView:
        with unit_of_work(self.datasets.factory) as session:
            return self._view(self._row(session, dataset_id, version_id))

    def _row(self, session: Session, dataset_id: UUID, version_id: UUID) -> DatasetVersion:
        self.datasets._dataset(session, dataset_id)
        row = session.scalar(
            select(DatasetVersion).where(
                DatasetVersion.id == version_id, DatasetVersion.dataset_id == dataset_id
            )
        )
        if row is None:
            raise SourceError(404, "Dataset version not found")
        return row

    def create(self, dataset_id: UUID, request: DatasetVersionCreate) -> DatasetVersionView:
        try:
            return self._create(dataset_id, request)
        except (ValidationError, ValueError, UnicodeError, KeyError, TypeError):
            raise SourceError(409, "A selected canonical artifact is invalid") from None

    def _create(self, dataset_id: UUID, request: DatasetVersionCreate) -> DatasetVersionView:
        with unit_of_work(self.datasets.factory) as session:
            dataset = self.datasets._dataset(session, dataset_id, lock=True)
            rows = list(
                session.scalars(
                    select(Conversation)
                    .join(ConversationImport)
                    .join(DatasetSource)
                    .where(DatasetSource.dataset_id == dataset_id)
                    .order_by(Conversation.id)
                )
            )
            wanted = (
                set(request.conversation_ids)
                if request.conversation_ids is not None
                else {candidate.id for candidate in rows if candidate.selected}
            )
            if not wanted <= {row.id for row in rows}:
                raise SourceError(422, "Selection contains conversations outside this dataset")
            selected = [row for row in rows if row.id in wanted]
            if len(selected) > 10_000:
                raise SourceError(422, "Select at most 10000 conversations")
            stats = DatasetStatistics(conversation_count=len(selected))
            warnings: list[DatasetWarning] = []
            sources: dict[UUID, VersionSource] = {}
            conversations: list[VersionConversation] = []
            documents: list[VersionDocument] = []
            parents: set[UUID] = set()
            output = bytearray()
            consumed = 0

            def account_input(size: int) -> None:
                nonlocal consumed
                consumed += size
                if consumed > 256 * 1024**2:
                    raise SourceError(422, "Selected canonical inputs exceed 256 MiB")

            def source_snapshot(source_id: UUID) -> None:
                if source_id in sources:
                    return
                source = self.datasets._source(session, dataset_id, source_id)
                raw = session.get(Artifact, source.artifact_id)
                assert raw is not None
                # Raw hashes are provenance; canonical/derived bytes used below are reverified.
                sources[source_id] = VersionSource(
                    source_id=source.id,
                    filename=source.filename,
                    license=source.license,
                    artifact=self._reference(raw),
                )
                parents.add(raw.id)

            def append(example: TrainingExample) -> None:
                output.extend((example.model_dump_json(exclude_none=True) + "\n").encode())
                stats.example_count += 1
                if len(output) > MAX_OUTPUT or stats.example_count > 100_000:
                    raise SourceError(422, "Dataset exceeds 64 MiB or 100000 examples")

            grouped: dict[UUID, list[Conversation]] = {}
            for selected_row in selected:
                grouped.setdefault(selected_row.import_id, []).append(selected_row)
            for import_id in sorted(grouped):
                imported = session.get(ConversationImport, import_id)
                assert imported is not None
                if imported.canonical_artifact_id is None:
                    raise SourceError(409, "Conversation import has no canonical artifact")
                artifact, body = self._read(session, imported.canonical_artifact_id)
                account_input(len(body))
                if artifact.format != "kitchensoup.conversation/v1":
                    raise SourceError(409, "Unsupported canonical conversation format")
                source_snapshot(imported.source_id)
                parents.add(artifact.id)
                indexed = {row.source_conversation_id: row for row in grouped[import_id]}
                seen: set[str] = set()
                for line in body.splitlines():
                    canonical = CanonicalConversation.model_validate_json(line)
                    if canonical.conversation_id in seen:
                        raise SourceError(409, "Duplicate canonical conversation identifier")
                    seen.add(canonical.conversation_id)
                    row = indexed.get(canonical.conversation_id)
                    if row is None:
                        continue
                    conversations.append(
                        VersionConversation(
                            conversation_id=row.id,
                            source_conversation_id=canonical.conversation_id,
                            import_id=import_id,
                            source_id=imported.source_id,
                            canonical=self._reference(artifact),
                        )
                    )
                    examples, message_count, ignored = conversation_examples(
                        canonical, row.id, imported.source_id
                    )
                    stats.message_count += message_count
                    stats.ignored_item_count += ignored
                    if ignored:
                        warnings.append(
                            DatasetWarning(
                                code="unusable_messages_or_targets",
                                source_id=imported.source_id,
                                conversation_id=row.id,
                                count=ignored,
                            )
                        )
                    for code in canonical.metadata.warnings:
                        warnings.append(
                            DatasetWarning(
                                code=code, source_id=imported.source_id, conversation_id=row.id
                            )
                        )
                    before = stats.example_count
                    for example in examples:
                        append(example)
                    if stats.example_count == before:
                        stats.ignored_item_count += 1
                        warnings.append(
                            DatasetWarning(
                                code="conversation_without_usable_target",
                                source_id=imported.source_id,
                                conversation_id=row.id,
                                count=1,
                            )
                        )
                if not set(indexed) <= seen:
                    raise SourceError(
                        409, "Selected conversation is missing from its canonical artifact"
                    )
            document_sources: set[UUID] = set()
            for ingestion_id in sorted(set(request.document_ingestion_ids)):
                ingestion = session.get(DocumentIngestion, ingestion_id)
                if ingestion is None or ingestion.dataset_id != dataset_id:
                    raise SourceError(422, "Document extraction is outside this dataset")
                if ingestion.status != "succeeded" or ingestion.output_artifact_id is None:
                    raise SourceError(422, "Select a successful document extraction")
                record, manifest_body = self._read(session, ingestion.manifest_artifact_id)
                manifest = DocumentIngestionManifest.model_validate_json(manifest_body)
                if (
                    manifest.model_dump(mode="json", by_alias=True) != ingestion.manifest
                    or manifest.ingestion_id != ingestion.id
                    or manifest.dataset_id != dataset_id
                    or manifest.output_artifact_id != ingestion.output_artifact_id
                    or manifest.status != "succeeded"
                ):
                    raise SourceError(409, "Document extraction manifest mismatch")
                eligible = {s.source_id for s in manifest.sources if s.disposition == "ingested"}
                if eligible & document_sources:
                    raise SourceError(422, "Select only one document extraction per source")
                document_sources.update(eligible)
                artifact, body = self._read(session, ingestion.output_artifact_id, 32 * 1024**2)
                account_input(len(body) + len(manifest_body))
                if artifact.format != "kitchensoup.document-examples/v1":
                    raise SourceError(409, "Unsupported document example format")
                parents.update([artifact.id, record.id])
                documents.append(
                    VersionDocument(
                        ingestion_id=ingestion_id,
                        manifest=self._reference(record),
                        output=self._reference(artifact),
                    )
                )
                for item in manifest.sources:
                    source_snapshot(item.source_id)
                    if (
                        sources[item.source_id].artifact.sha256 != item.sha256
                        or sources[item.source_id].artifact.artifact_id != item.artifact_id
                    ):
                        raise SourceError(409, "Document source provenance mismatch")
                    stats.ignored_item_count += item.ignored_rows + int(
                        item.disposition == "ignored"
                    )
                    for document_code in item.warnings:
                        warnings.append(
                            DatasetWarning(code=document_code, source_id=item.source_id)
                        )
                count = 0
                for index, line in enumerate(body.splitlines()):
                    row = json.loads(line)
                    source_id = UUID(row["source_id"])
                    text = row["data"]["text"]
                    if source_id not in eligible or not isinstance(text, str) or not text.strip():
                        raise SourceError(409, "Invalid document example")
                    append(
                        TrainingExample(
                            kind="document",
                            source_id=source_id,
                            document_ingestion_id=ingestion_id,
                            document_row=index,
                            text=text,
                        )
                    )
                    count += 1
                if count != manifest.example_count:
                    raise SourceError(409, "Document extraction row count mismatch")
            if not stats.example_count:
                raise SourceError(422, "The selection contains no usable training examples")
            identifier = uuid4()
            number = (
                session.scalar(
                    select(func.max(DatasetVersion.version)).where(
                        DatasetVersion.dataset_id == dataset_id
                    )
                )
                or 0
            ) + 1
            stats.source_count, stats.size_bytes = len(sources), len(output)
            prefix = f"datasets/v1/{identifier}"
            examples_artifact = self._write(
                session,
                prefix + "/examples.jsonl",
                bytes(output),
                "kitchensoup.training-example/v1",
                parents,
            )
            dataset_manifest = DatasetManifest(
                schema_name="kitchensoup.dataset-manifest/v1",
                dataset_id=dataset_id,
                dataset_version_id=identifier,
                version=number,
                license=dataset.license,
                sources=sorted(sources.values(), key=lambda s: s.source_id),
                conversations=conversations,
                documents=documents,
                examples=self._reference(examples_artifact),
                statistics=stats,
                warnings=warnings,
            )
            body = dataset_manifest.model_dump_json(by_alias=True).encode()
            manifest_artifact = self._write(
                session,
                prefix + "/manifest.json",
                body,
                "kitchensoup.dataset-manifest/v1",
                parents | {examples_artifact.id},
            )
            version_row = DatasetVersion(
                id=identifier,
                dataset_id=dataset_id,
                version=number,
                manifest_version="kitchensoup.dataset-manifest/v1",
                manifest=dataset_manifest.model_dump(mode="json", by_alias=True),
                sha256=manifest_artifact.sha256,
                manifest_artifact_id=manifest_artifact.id,
            )
            session.add(version_row)
            session.flush()
            result = self._view(version_row)
            session.commit()
            return result

    def preview(self, dataset_id: UUID, version_id: UUID, offset: int, limit: int) -> ExamplePage:
        with unit_of_work(self.datasets.factory) as session:
            view = self._view(self._row(session, dataset_id, version_id))
            artifact, body = self._read(session, view.manifest.examples.artifact_id, MAX_OUTPUT)
            if self._reference(artifact) != view.manifest.examples:
                raise SourceError(409, "Dataset example provenance mismatch")
            lines = body.splitlines()
            if len(lines) != view.manifest.statistics.example_count:
                raise SourceError(409, "Dataset example count mismatch")
            try:
                items = [
                    TrainingExample.model_validate_json(line)
                    for line in lines[offset : offset + limit]
                ]
            except ValidationError:
                raise SourceError(409, "Stored training examples are invalid") from None
            return ExamplePage(offset=offset, limit=limit, total=len(lines), items=items)
