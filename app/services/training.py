"""Resolve and retain reviewable plans without submitting training runs."""

import re
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import Artifact, Dataset, DatasetVersion, Model, ModelSource, ModelVersion
from app.db.models import TrainingPlan as PlanRecord
from app.db.session import unit_of_work
from app.ingestion.sources import SourceError
from app.ingestion.versions import DatasetManifest
from app.training.recipes import digest, resolve_recipe
from app.training.schemas import (
    AppSpec,
    PlanContent,
    PlanModelSource,
    PlanPreview,
    ResolvedPlan,
    TrainingPlanView,
)


class TrainingPlanService:
    def __init__(self, factory: sessionmaker[Session]) -> None:
        self.factory = factory

    def _resolve(self, session: Session, spec: AppSpec) -> PlanPreview:
        recipe, parameters = resolve_recipe(spec)
        version = session.get(ModelVersion, spec.base_model_version_id)
        dataset = session.get(DatasetVersion, spec.dataset_version_id)
        if version is None or dataset is None:
            raise SourceError(404, "Model version or dataset version not found")
        sources = list(
            session.scalars(select(ModelSource).where(ModelSource.model_version_id == version.id))
        )
        if len(sources) != 1 or sources[0].gated:
            raise SourceError(422, "Choose a model version with one registered, ungated source")
        source = sources[0]
        artifact = session.get(Artifact, source.artifact_id) if source.artifact_id else None
        if source.kind in ("catalog", "huggingface"):
            if not source.revision or not re.fullmatch(r"[0-9a-f]{40}", source.revision):
                raise SourceError(422, "Base model must have an exact registered commit")
        elif source.kind != "upload" or artifact is None:
            raise SourceError(422, "Unsupported base model source; register a model first")
        try:
            manifest = DatasetManifest.model_validate(dataset.manifest)
        except ValidationError as error:
            raise SourceError(409, "Dataset manifest version or content is unsupported") from error
        if (
            dataset.manifest_version != manifest.schema_name
            or manifest.dataset_version_id != dataset.id
            or manifest.dataset_id != dataset.dataset_id
            or manifest.version != dataset.version
        ):
            raise SourceError(409, "Dataset manifest does not match its registered version")
        if manifest.statistics.example_count <= 0:
            raise SourceError(422, "Dataset has no usable training examples")
        if recipe.example_kind == "conversation":
            compatible = bool(manifest.conversations) and not manifest.documents
        else:
            compatible = bool(manifest.documents) and not manifest.conversations
        if not compatible:
            raise SourceError(
                422,
                "Recipe requires only "
                + recipe.example_kind
                + " examples; create a separate dataset version for mixed sources",
            )
        model = session.get(Model, version.model_id)
        dataset_name = session.get(Dataset, dataset.dataset_id)
        assert model is not None and dataset_name is not None
        warnings = [
            "Training has not started. Engine translation, hardware checks "
            "and execution validation are pending.",
            "These are initial defaults, not measured quality or GPU memory guarantees. "
            "BF16-capable hardware is required.",
            "Examples exceeding the sequence limit must be rejected by the trainer; "
            "no silent truncation is permitted.",
            "Review model and source licenses and the dataset preview before training.",
        ]
        if manifest.warnings:
            warnings.append("The dataset has import or conversion warnings; review its preview.")
        if recipe.example_kind == "document":
            warnings.append(
                "Fine-tuning is unreliable for adding or updating factual knowledge. "
                "Consider retrieval over your documents instead. "
                "This recipe adapts to document text; it does not generate question-answer pairs."
            )
        resolved = ResolvedPlan(
            base_model_version_id=version.id,
            base_model_name=model.name,
            base_model_source=PlanModelSource.model_validate(
                {
                    "source_id": source.id,
                    "kind": source.kind,
                    "location": source.location,
                    "revision": source.revision,
                    "license": source.license,
                    "artifact_id": source.artifact_id,
                    "artifact_sha256": artifact.sha256 if artifact else None,
                }
            ),
            dataset_version_id=dataset.id,
            dataset_id=dataset.dataset_id,
            dataset_name=dataset_name.name,
            dataset_version=dataset.version,
            dataset_manifest_sha256=dataset.sha256,
            examples_artifact_id=manifest.examples.artifact_id,
            examples_sha256=manifest.examples.sha256,
            example_count=manifest.statistics.example_count,
            recipe=recipe,
            recipe_sha256=digest(recipe),
            training=parameters,
            warnings=warnings,
        )
        content = PlanContent(appspec=spec, resolved=resolved)
        return PlanPreview(**content.model_dump(), sha256=digest(content))

    def preview(self, spec: AppSpec) -> PlanPreview:
        with unit_of_work(self.factory) as session:
            return self._resolve(session, spec)

    def create(self, spec: AppSpec, expected_sha256: str | None = None) -> TrainingPlanView:
        with unit_of_work(self.factory) as session:
            preview = self._resolve(session, spec)
            if expected_sha256 is not None and expected_sha256 != preview.sha256:
                raise SourceError(
                    409, "Training plan changed; preview the current configuration again"
                )
            row = PlanRecord(
                base_model_version_id=spec.base_model_version_id,
                dataset_version_id=spec.dataset_version_id,
                appspec_version=spec.schema_name,
                appspec=spec.model_dump(mode="json", by_alias=True),
                resolved_config=preview.resolved.model_dump(mode="json", by_alias=True),
                sha256=preview.sha256,
            )
            session.add(row)
            session.flush()
            view = self._view(row)
            session.commit()
            return view

    @staticmethod
    def _view(row: PlanRecord) -> TrainingPlanView:
        try:
            content = PlanContent(
                appspec=AppSpec.model_validate(row.appspec),
                resolved=ResolvedPlan.model_validate(row.resolved_config),
            )
        except ValidationError as error:
            raise SourceError(
                409, "Stored training plan version or content is unsupported"
            ) from error
        if (
            row.appspec_version != content.appspec.schema_name
            or row.base_model_version_id != content.appspec.base_model_version_id
            or row.dataset_version_id != content.appspec.dataset_version_id
            or digest(content) != row.sha256
        ):
            raise SourceError(409, "Stored training plan does not match its snapshot hash")
        return TrainingPlanView(
            **content.model_dump(), sha256=row.sha256, id=row.id, created_at=row.created_at
        )

    def get(self, identifier: UUID) -> TrainingPlanView:
        with unit_of_work(self.factory) as session:
            row = session.get(PlanRecord, identifier)
            if row is None:
                raise SourceError(404, "Training plan not found")
            return self._view(row)

    def list_plans(self) -> list[TrainingPlanView]:
        with unit_of_work(self.factory) as session:
            return [
                self._view(row)
                for row in session.scalars(
                    select(PlanRecord).order_by(PlanRecord.created_at.desc(), PlanRecord.id)
                )
            ]
