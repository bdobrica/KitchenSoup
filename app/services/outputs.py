"""Idempotent registration of executor-verified adapter representations."""

from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import (
    Artifact,
    ArtifactDerivation,
    DatasetVersion,
    Model,
    ModelSource,
    ModelVersion,
    ModelVersionParent,
    TrainingRun,
)
from app.executors.base import ExecutorError, TrainingExecutor
from app.services.artifacts import ArtifactService
from app.storage.hashing import hash_content
from app.training.schemas import PlanPreview


class OutputRegistrar:
    def __init__(self, artifacts: ArtifactService, executor: TrainingExecutor) -> None:
        self.artifacts, self.executor = artifacts, executor

    def register(self, session: Session, run: TrainingRun) -> UUID:
        existing = session.scalar(
            select(ModelVersion).where(ModelVersion.training_run_id == run.id)
        )
        if existing is not None:
            return existing.id
        parent = session.get(ModelVersion, run.base_model_version_id)
        assert parent is not None
        base = session.get(Model, parent.model_id)
        assert base is not None
        model = Model(
            id=uuid5(NAMESPACE_URL, f"kitchensoup:run:{run.id}:model"),
            name=f"{base.name} fine-tune {str(run.id)[:8]}",
        )
        session.add(model)
        session.flush()
        version = ModelVersion(
            id=uuid5(NAMESPACE_URL, f"kitchensoup:run:{run.id}:version"),
            model_id=model.id,
            version=1,
            training_run_id=run.id,
        )
        session.add(version)
        session.flush()
        with self.executor.output(
            run.external_id or "", run.resolved_config["image_digest"]
        ) as body:
            digest, size = hash_content(body, max_bytes=9 * 1024**3)
            key = f"models/v1/{version.id}/adapter.zip"
            body.seek(0)
            self.artifacts.store.put(key, body)
        artifact = Artifact(
            id=uuid5(NAMESPACE_URL, f"kitchensoup:run:{run.id}:adapter"),
            model_version_id=version.id,
            bucket=self.artifacts.bucket,
            object_key=key,
            sha256=digest,
            size_bytes=size,
            format="kitchensoup.lora-adapter+zip/v1",
        )
        session.add(artifact)
        session.flush()
        session.add(ModelVersionParent(child_id=version.id, parent_id=parent.id))
        # Copy original source provenance; it does not assert that adapter bytes
        # came from the Hub. training_run_id and parent edges explain derivation.
        retained = PlanPreview.model_validate(
            run.resolved_config["plan"]
        ).resolved.base_model_source
        source = session.get(ModelSource, retained.source_id)
        if source is None or source.model_version_id != parent.id:
            raise ExecutorError("The retained base source is unavailable")
        session.add(
            ModelSource(
                model_version_id=version.id,
                kind=retained.kind,
                location=retained.location,
                revision=retained.revision,
                license=retained.license,
                license_url=source.license_url,
                gated=False,
                notes=source.notes,
                artifact_id=retained.artifact_id,
                catalog_entry_id=source.catalog_entry_id,
            )
        )
        parents = {retained.artifact_id} if retained.artifact_id is not None else set()
        dataset = session.get(DatasetVersion, run.dataset_version_id)
        assert dataset is not None
        if dataset.manifest_artifact_id is not None:
            parents.add(dataset.manifest_artifact_id)
        examples = run.resolved_config["plan"]["resolved"]["examples_artifact_id"]
        parents.add(UUID(examples))
        for identifier in parents:
            session.add(
                ArtifactDerivation(
                    child_id=artifact.id, parent_id=identifier, operation="fine-tune"
                )
            )
        session.flush()
        return version.id
