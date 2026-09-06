"""Snapshot selected originals, call Soup, and register immutable derived artifacts."""

import hashlib
import json
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import Artifact, ArtifactDerivation, Document, DocumentIngestion
from app.db.session import unit_of_work
from app.ingestion.documents import (
    MAX_INPUT,
    MAX_OUTPUT,
    SUPPORTED,
    DocumentIngestionManifest,
    DocumentIngestionView,
    DocumentInputSnapshot,
    DocumentRunner,
    DocumentSelection,
    output_rows,
)
from app.ingestion.sources import SourceError
from app.services.datasets import DatasetService
from app.storage.hashing import hash_content


class DocumentIngestionService:
    def __init__(self, datasets: DatasetService, runner: DocumentRunner) -> None:
        self.datasets = datasets
        self.runner = runner

    def list_ingestions(self, dataset_id: UUID) -> list[DocumentIngestionView]:
        with unit_of_work(self.datasets.factory) as session:
            self.datasets._dataset(session, dataset_id)
            return [
                DocumentIngestionView.model_validate(row)
                for row in session.scalars(
                    select(DocumentIngestion)
                    .where(DocumentIngestion.dataset_id == dataset_id)
                    .order_by(DocumentIngestion.version.desc())
                )
            ]

    def _artifact(
        self, session: Session, key: str, data: bytes, format: str, parents: list[UUID]
    ) -> Artifact:
        artifacts = self.datasets.artifacts
        artifacts.store.put(key, BytesIO(data))
        artifact = Artifact(
            bucket=artifacts.bucket,
            object_key=key,
            sha256=hashlib.sha256(data).hexdigest(),
            size_bytes=len(data),
            format=format,
        )
        session.add(artifact)
        session.flush()
        for parent in set(parents):
            session.add(
                ArtifactDerivation(
                    child_id=artifact.id, parent_id=parent, operation="soup-data-ingest/v1"
                )
            )
        return artifact

    def ingest(self, dataset_id: UUID, request: DocumentSelection) -> DocumentIngestionView:
        try:
            return self._ingest(dataset_id, request)
        except OSError:
            raise SourceError(503, "Temporary ingestion workspace unavailable") from None

    def _ingest(self, dataset_id: UUID, request: DocumentSelection) -> DocumentIngestionView:
        # A dataset lock serializes selection snapshots, removal, and version allocation.
        with unit_of_work(self.datasets.factory) as session, TemporaryDirectory() as workspace:
            self.datasets._dataset(session, dataset_id, lock=True)
            sources = [
                self.datasets._source(session, dataset_id, identifier)
                for identifier in sorted(set(request.source_ids))
            ]
            if not any(Path(source.filename).suffix.lower() in SUPPORTED for source in sources):
                raise SourceError(
                    422, "Select at least one PDF, DOCX, Markdown or plain text source"
                )
            snapshots = []
            total = 0
            # Verify and materialize every input before invoking Soup, including ignored inputs.
            for source in sources:
                raw = session.get(Artifact, source.artifact_id)
                assert raw is not None
                if raw.bucket != self.datasets.artifacts.bucket:
                    raise SourceError(409, "Artifact belongs to a different configured store")
                if raw.size_bytes > MAX_INPUT:
                    raise SourceError(413, "Ingestion accepts at most 16 MiB per selected source")
                total += raw.size_bytes
                if total > 64 * 1024**2:
                    raise SourceError(413, "Ingestion accepts at most 64 MiB of selected sources")
                with self.datasets.artifacts.store.get(raw.object_key, max_bytes=MAX_INPUT) as body:
                    digest, size = hash_content(body, max_bytes=MAX_INPUT)
                    if (digest, size) != (raw.sha256, raw.size_bytes):
                        raise SourceError(
                            409, "Stored source no longer matches its registered hash"
                        )
                    body.seek(0)
                    (Path(workspace) / str(source.id)).write_bytes(body.read())
                snapshots.append(
                    DocumentInputSnapshot(
                        source_id=source.id,
                        artifact_id=raw.id,
                        sha256=raw.sha256,
                        filename=source.filename,
                        license=source.license,
                        disposition="not_attempted",
                        warnings=[],
                        example_count=0,
                        ignored_rows=0,
                    )
                )
            identifier = uuid4()
            version = (
                session.scalar(
                    select(func.max(DocumentIngestion.version)).where(
                        DocumentIngestion.dataset_id == dataset_id
                    )
                )
                or 0
            ) + 1
            logs: list[dict[str, object]] = []
            output = bytearray()
            soup_version = image_id = ""
            failed = False
            count = 0
            for snapshot in snapshots:
                extension = Path(snapshot.filename).suffix.lower()
                if extension not in SUPPORTED:
                    snapshot.disposition = "ignored"
                    snapshot.warnings.append("unsupported_document_type")
                    continue
                if failed:
                    snapshot.warnings.append("earlier_source_failed")
                    continue
                result = self.runner.ingest(
                    (Path(workspace) / str(snapshot.source_id)).read_bytes(), extension
                )
                if image_id and (image_id, soup_version) != (result.image_id, result.soup_version):
                    raise SourceError(
                        409, "Soup image changed during ingestion; retry with a stable runner"
                    )
                soup_version, image_id = result.soup_version, result.image_id
                logs.append(
                    {
                        "source_id": str(snapshot.source_id),
                        "exit_code": result.exit_code,
                        "stdout": result.stdout,
                        "stderr": result.stderr,
                        "truncated": result.logs_truncated,
                    }
                )
                if result.logs_truncated:
                    snapshot.warnings.append("logs_truncated")
                if result.stderr:
                    snapshot.warnings.append("soup_stderr")
                # Explicitly communicate upstream format limitations without parsing documents.
                if extension == ".pdf":
                    snapshot.warnings.append("pdf_text_only_no_ocr")
                if extension == ".docx":
                    snapshot.warnings.append("docx_paragraphs_only")
                if result.exit_code != 0:
                    snapshot.disposition = "failed"
                    snapshot.warnings.append("soup_command_failed")
                    failed = True
                    continue
                try:
                    rows, ignored = output_rows(result)
                    snapshot.ignored_rows = ignored
                    if ignored:
                        snapshot.warnings.append("empty_text_rows_ignored")
                    for row in rows:
                        # Preserve Soup row fields and attach stable application provenance.
                        line = (
                            json.dumps(
                                {"source_id": str(snapshot.source_id), "data": row},
                                ensure_ascii=False,
                                sort_keys=True,
                            )
                            + "\n"
                        )
                        output.extend(line.encode("utf-8"))
                        if len(output) > MAX_OUTPUT:
                            raise SourceError(502, "Combined ingestion output exceeds 32 MiB")
                    snapshot.example_count = len(rows)
                    count += len(rows)
                    snapshot.disposition = "ingested"
                    if not rows:
                        snapshot.warnings.append("no_text_extracted")
                except (SourceError, UnicodeError):
                    snapshot.disposition = "failed"
                    snapshot.warnings.append("invalid_or_oversized_soup_output")
                    failed = True
            failed |= count == 0
            parents = [snapshot.artifact_id for snapshot in snapshots]
            prefix = f"derived/documents/v1/{identifier}"
            log_artifact = self._artifact(
                session,
                prefix + "/logs.json",
                json.dumps(logs).encode(),
                "application/json",
                parents,
            )
            output_artifact = (
                None
                if failed
                else self._artifact(
                    session,
                    prefix + "/dataset.jsonl",
                    bytes(output),
                    "kitchensoup.document-examples/v1",
                    parents,
                )
            )
            manifest = DocumentIngestionManifest(
                schema="kitchensoup.document-ingestion/v1",
                ingestion_id=identifier,
                dataset_id=dataset_id,
                version=version,
                status="failed" if failed else "succeeded",
                soup_version=soup_version,
                image_id=image_id,
                sources=snapshots,
                output_artifact_id=output_artifact.id if output_artifact else None,
                logs_artifact_id=log_artifact.id,
                example_count=0 if failed else count,
            )
            manifest_artifact = self._artifact(
                session,
                prefix + "/manifest.json",
                manifest.model_dump_json(by_alias=True).encode(),
                "kitchensoup.document-ingestion/v1",
                parents + [log_artifact.id] + ([output_artifact.id] if output_artifact else []),
            )
            ingestion = DocumentIngestion(
                id=identifier,
                dataset_id=dataset_id,
                version=version,
                status=manifest.status,
                output_artifact_id=manifest.output_artifact_id,
                logs_artifact_id=log_artifact.id,
                manifest_artifact_id=manifest_artifact.id,
                manifest=manifest.model_dump(mode="json", by_alias=True),
            )
            session.add(ingestion)
            if output_artifact:
                for document in session.scalars(
                    select(Document).where(
                        Document.source_id.in_(
                            [s.source_id for s in snapshots if s.disposition == "ingested"]
                        )
                    )
                ):
                    if document.canonical_artifact_id is None:
                        document.canonical_artifact_id = output_artifact.id
            session.flush()
            view = DocumentIngestionView.model_validate(ingestion)
            session.commit()
            return view
