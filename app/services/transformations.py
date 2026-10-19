"""Immutable, deduplicated transformations; PostgreSQL owns jobs and representation identity."""

import hashlib
import json
from datetime import UTC, datetime, timedelta
from tempfile import TemporaryFile
from uuid import UUID, uuid4, uuid5

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.db.models import (
    Artifact,
    ArtifactDerivation,
    ArtifactTransformation,
    ModelVersion,
    TrainingRun,
)
from app.executors.base import ExecutorError
from app.ingestion.sources import SourceError
from app.jobs.locking import claim
from app.services.artifacts import ArtifactService
from app.storage.base import StorageError
from app.storage.hashing import hash_content
from app.training.schemas import PlanModelSource, PlanPreview
from app.transformations.local import InvalidTransformOutput, TransformExecutor
from app.transformations.schemas import (
    AWQ,
    LORA,
    MERGED,
    TERMINAL,
    TransformCreate,
    TransformSpec,
    TransformView,
)


class TransformationService:
    def __init__(
        self, artifacts: ArtifactService, executor: TransformExecutor | None = None
    ) -> None:
        self.artifacts, self.factory, self.executor = artifacts, artifacts.factory, executor

    def enabled(self) -> TransformExecutor:
        if self.executor is None:
            raise SourceError(503, "Local artifact transformations are disabled")
        return self.executor

    @staticmethod
    def view(row: ArtifactTransformation) -> TransformView:
        spec = TransformSpec.model_validate(row.snapshot)
        return TransformView(
            id=row.id,
            model_version_id=row.model_version_id,
            input_artifact_id=row.input_artifact_id,
            output_artifact_id=row.output_artifact_id,
            operation=spec.operation,
            precision=spec.precision,
            status=row.status,
            created_at=row.created_at,
        )

    @staticmethod
    def row(session: Session, identifier: UUID) -> ArtifactTransformation:
        row = session.get(ArtifactTransformation, identifier)
        if row is None:
            raise SourceError(404, "Transformation not found")
        return row

    def get(self, identifier: UUID) -> TransformView:
        with self.factory() as session:
            return self.view(self.row(session, identifier))

    def list(self, model_version_id: UUID) -> list[TransformView]:
        with self.factory() as session:
            return [
                self.view(row)
                for row in session.scalars(
                    select(ArtifactTransformation)
                    .where(ArtifactTransformation.model_version_id == model_version_id)
                    .order_by(ArtifactTransformation.created_at)
                )
            ]

    def create(self, artifact_id: UUID, body: TransformCreate) -> TransformView:
        executor = self.enabled()
        with self.factory() as session:
            artifact = session.get(Artifact, artifact_id)
            if artifact is None or artifact.model_version_id is None:
                raise SourceError(404, "Registered model representation not found")
            version = session.get(ModelVersion, artifact.model_version_id)
            assert version is not None
            if body.operation == "merge":
                if artifact.format != LORA or version.training_run_id is None:
                    raise SourceError(
                        422, "Merge requires a registered trained LoRA representation"
                    )
                run = session.get(TrainingRun, version.training_run_id)
                if run is None or run.status != "SUCCEEDED":
                    raise SourceError(409, "Training output is not successful")
                source = PlanPreview.model_validate(
                    run.resolved_config["plan"]
                ).resolved.base_model_source
            else:
                if artifact.format not in MERGED.values():
                    raise SourceError(422, "Create a merged representation before AWQ quantization")
                source = PlanModelSource(
                    source_id=artifact.id,
                    kind="upload",
                    location=f"artifact:{artifact.id}",
                    revision=None,
                    license="inherited",
                    artifact_id=artifact.id,
                    artifact_sha256=artifact.sha256,
                )
            spec = TransformSpec(
                **body.model_dump(),
                model_version_id=version.id,
                artifact_id=artifact.id,
                artifact_sha256=artifact.sha256,
                base_source=source,
                image_digest=executor.image_identity(),
            )
            payload = spec.model_dump(mode="json", by_alias=True)
            identity = hashlib.sha256(
                json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
            identifier = uuid4()
            session.execute(
                insert(ArtifactTransformation)
                .values(
                    id=identifier,
                    model_version_id=version.id,
                    input_artifact_id=artifact.id,
                    snapshot=payload,
                    sha256=identity,
                    status="QUEUED",
                )
                .on_conflict_do_nothing(index_elements=[ArtifactTransformation.sha256])
            )
            row = session.scalar(
                select(ArtifactTransformation).where(ArtifactTransformation.sha256 == identity)
            )
            assert row is not None
            result = self.view(row)
            session.commit()
            return result

    def update(self, identifier: UUID, status: str) -> None:
        with self.factory() as session:
            row = self.row(session, identifier)
            row.status = status
            session.commit()

    def tick(self, role: str) -> None:
        if self.executor is None:
            return
        with self.factory() as session:
            identifiers = list(
                session.scalars(
                    select(ArtifactTransformation.id).where(
                        ArtifactTransformation.status.not_in(TERMINAL)
                    )
                )
            )
        for identifier in identifiers:
            try:
                self.process(identifier, role)
            except (ExecutorError, StorageError):
                # Unavailable Docker/storage never falsely reports resource release or success.
                continue

    def process(self, identifier: UUID, role: str) -> None:
        executor = self.enabled()
        with claim(self.factory, identifier) as acquired:
            if not acquired:
                return
            with self.factory() as session:
                row = self.row(session, identifier)
                spec = TransformSpec.model_validate(row.snapshot)
                status, created = row.status, row.created_at
            if status in TERMINAL:
                return
            if status == "QUEUED" and role == "worker":
                self.update(identifier, "PREPARING")
                try:
                    executor.start(identifier, spec)
                except Exception:
                    executor.cleanup(identifier, spec)
                    self.update(identifier, "FAILED")
                    return
                self.update(identifier, "RUNNING")
            elif status in ("PREPARING", "RUNNING"):
                observed = executor.status(identifier, spec)
                if observed == "RUNNING" and datetime.now(UTC) - created < timedelta(hours=24):
                    if status == "PREPARING":
                        self.update(identifier, "RUNNING")
                    return
                if observed == "SUCCEEDED":
                    self.update(identifier, "POSTPROCESSING")
                    self.finish(identifier, spec)
                else:
                    executor.cleanup(identifier, spec)
                    self.update(identifier, "LOST" if observed == "LOST" else "FAILED")
            elif status == "POSTPROCESSING":
                self.finish(identifier, spec)

    def finish(self, identifier: UUID, spec: TransformSpec) -> None:
        try:
            self.register(identifier, spec)
        except InvalidTransformOutput:
            self.enabled().cleanup(identifier, spec)
            self.update(identifier, "FAILED")

    def register(self, identifier: UUID, spec: TransformSpec) -> None:
        executor = self.enabled()
        output_id = uuid5(identifier, "representation")
        # Retry a committed registration/cleanup without rewriting registered bytes.
        with self.factory() as session:
            registered = session.get(Artifact, output_id) is not None
        if not registered:
            with TemporaryFile() as stream:
                executor.output(identifier, spec, stream)
                stream.seek(0)
                digest, size = hash_content(stream, max_bytes=9 * 1024**3)
                stream.seek(0)
                key = f"models/{spec.model_version_id}/representations/{identifier}.zip"
                self.artifacts.store.put(key, stream)
            with self.factory() as session:
                row = self.row(session, identifier)
                session.add(
                    Artifact(
                        id=output_id,
                        model_version_id=spec.model_version_id,
                        bucket=self.artifacts.bucket,
                        object_key=key,
                        sha256=digest,
                        size_bytes=size,
                        format=AWQ if spec.operation == "awq" else MERGED[spec.precision],
                    )
                )
                session.flush()
                parents = {spec.artifact_id}
                if spec.operation == "merge" and spec.base_source.artifact_id:
                    parents.add(spec.base_source.artifact_id)
                for parent in parents:
                    session.add(
                        ArtifactDerivation(
                            child_id=output_id,
                            parent_id=parent,
                            operation="awq-int4" if spec.operation == "awq" else "lora-merge",
                        )
                    )
                row.output_artifact_id = output_id
                session.commit()
        executor.cleanup(identifier, spec)
        self.update(identifier, "SUCCEEDED")
