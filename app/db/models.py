"""Initial relational metadata; workflow-specific validation belongs to later services."""

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    MetaData,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    metadata = MetaData(
        naming_convention={
            "ix": "ix_%(table_name)s_%(column_0_name)s",
            "uq": "uq_%(table_name)s_%(column_0_name)s",
            "ck": "ck_%(table_name)s_%(constraint_name)s",
            "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
            "pk": "pk_%(table_name)s",
        }
    )


class Entity(Base):
    __abstract__ = True

    id: Mapped[UUID] = mapped_column(
        primary_key=True, default=uuid4, server_default=text("gen_random_uuid()")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), server_onupdate=func.now()
    )


class ModelCatalogEntry(Entity):
    __tablename__ = "model_catalog_entries"
    key: Mapped[str] = mapped_column(String(200), unique=True)
    name: Mapped[str]
    repository: Mapped[str]
    revision: Mapped[str]
    license: Mapped[str]
    gated: Mapped[bool] = mapped_column(default=False, server_default=text("false"))
    details: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb")
    )


class Model(Entity):
    __tablename__ = "models"
    name: Mapped[str]
    description: Mapped[str | None]


class ModelVersion(Entity):
    __tablename__ = "model_versions"
    __table_args__ = (
        UniqueConstraint("model_id", "version"),
        CheckConstraint("version > 0", name="positive_version"),
    )
    model_id: Mapped[UUID] = mapped_column(ForeignKey("models.id"), index=True)
    version: Mapped[int]
    description: Mapped[str | None]


class ModelVersionParent(Entity):
    __tablename__ = "model_version_parents"
    __table_args__ = (
        UniqueConstraint("child_id", "parent_id"),
        CheckConstraint("child_id <> parent_id", name="no_self_parent"),
    )
    child_id: Mapped[UUID] = mapped_column(ForeignKey("model_versions.id"), index=True)
    parent_id: Mapped[UUID] = mapped_column(ForeignKey("model_versions.id"), index=True)


class Artifact(Entity):
    __tablename__ = "artifacts"
    __table_args__ = (
        UniqueConstraint("bucket", "object_key"),
        CheckConstraint("size_bytes >= 0", name="nonnegative_size"),
        CheckConstraint("sha256 ~ '^[0-9a-f]{64}$'", name="valid_sha256"),
    )
    model_version_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("model_versions.id"), index=True
    )
    bucket: Mapped[str]
    object_key: Mapped[str]
    sha256: Mapped[str] = mapped_column(String(64))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    format: Mapped[str]


class ArtifactUpload(Entity):
    __tablename__ = "artifact_uploads"
    __table_args__ = (CheckConstraint("size_bytes >= 0", name="nonnegative_size"),)
    object_key: Mapped[str] = mapped_column(unique=True)
    filename: Mapped[str]
    content_type: Mapped[str]
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    artifact_id: Mapped[UUID | None] = mapped_column(ForeignKey("artifacts.id"), unique=True)


class ModelSource(Entity):
    __tablename__ = "model_sources"
    model_version_id: Mapped[UUID] = mapped_column(ForeignKey("model_versions.id"), index=True)
    catalog_entry_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("model_catalog_entries.id"), index=True
    )
    artifact_id: Mapped[UUID | None] = mapped_column(ForeignKey("artifacts.id"), index=True)
    kind: Mapped[str]
    location: Mapped[str]
    revision: Mapped[str | None]
    license: Mapped[str]
    license_url: Mapped[str | None]
    gated: Mapped[bool] = mapped_column(default=False, server_default=text("false"))
    notes: Mapped[str | None]


class ArtifactDerivation(Entity):
    __tablename__ = "artifact_derivations"
    __table_args__ = (
        UniqueConstraint("child_id", "parent_id"),
        CheckConstraint("child_id <> parent_id", name="no_self_parent"),
    )
    child_id: Mapped[UUID] = mapped_column(ForeignKey("artifacts.id"), index=True)
    parent_id: Mapped[UUID] = mapped_column(ForeignKey("artifacts.id"), index=True)
    operation: Mapped[str]


class Dataset(Entity):
    __tablename__ = "datasets"
    name: Mapped[str]
    description: Mapped[str | None]
    license: Mapped[str | None]


class DatasetVersion(Entity):
    __tablename__ = "dataset_versions"
    __table_args__ = (
        UniqueConstraint("dataset_id", "version"),
        CheckConstraint("version > 0", name="positive_version"),
        CheckConstraint("sha256 ~ '^[0-9a-f]{64}$'", name="valid_sha256"),
        CheckConstraint("jsonb_typeof(manifest) = 'object'", name="manifest_object"),
    )
    dataset_id: Mapped[UUID] = mapped_column(ForeignKey("datasets.id"), index=True)
    version: Mapped[int]
    manifest_version: Mapped[str]
    manifest: Mapped[dict[str, Any]] = mapped_column(JSONB)
    sha256: Mapped[str] = mapped_column(String(64))
    manifest_artifact_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("artifacts.id"), index=True
    )


class DatasetSource(Entity):
    __tablename__ = "dataset_sources"
    dataset_id: Mapped[UUID] = mapped_column(ForeignKey("datasets.id"), index=True)
    artifact_id: Mapped[UUID] = mapped_column(ForeignKey("artifacts.id"), index=True)
    kind: Mapped[str]
    filename: Mapped[str]
    license: Mapped[str | None]


class Document(Entity):
    __tablename__ = "documents"
    source_id: Mapped[UUID] = mapped_column(ForeignKey("dataset_sources.id"), index=True)
    canonical_artifact_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("artifacts.id"), index=True
    )
    title: Mapped[str]
    media_type: Mapped[str]


class ConversationImport(Entity):
    __tablename__ = "conversation_imports"
    source_id: Mapped[UUID] = mapped_column(ForeignKey("dataset_sources.id"), index=True)
    canonical_artifact_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("artifacts.id"), index=True
    )
    importer: Mapped[str]
    importer_version: Mapped[str]
    schema_version: Mapped[str]


class Conversation(Entity):
    __tablename__ = "conversations"
    __table_args__ = (
        UniqueConstraint("import_id", "source_conversation_id"),
        CheckConstraint("message_count >= 0", name="nonnegative_messages"),
    )
    import_id: Mapped[UUID] = mapped_column(ForeignKey("conversation_imports.id"), index=True)
    source_conversation_id: Mapped[str]
    title: Mapped[str]
    message_count: Mapped[int]
    source_created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    selected: Mapped[bool] = mapped_column(default=False, server_default=text("false"))
    warnings: Mapped[list[str]] = mapped_column(
        JSONB, default=list, server_default=text("'[]'::jsonb")
    )


class ExecutionTarget(Entity):
    __tablename__ = "execution_targets"
    __table_args__ = (
        CheckConstraint(
            "credential_ref IS NULL OR credential_ref ~ '^[a-z][a-z0-9+.-]*://.+$'",
            name="credential_reference",
        ),
    )
    name: Mapped[str] = mapped_column(unique=True)
    kind: Mapped[str]
    credential_ref: Mapped[str | None]
    configuration: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb")
    )


class LLMProvider(Entity):
    __tablename__ = "llm_providers"
    __table_args__ = (
        CheckConstraint(
            "api_key_ref IS NULL OR api_key_ref ~ '^[a-z][a-z0-9+.-]*://.+$'",
            name="credential_reference",
        ),
    )
    name: Mapped[str] = mapped_column(unique=True)
    base_url: Mapped[str]
    api_key_ref: Mapped[str | None]
    model_names: Mapped[list[str]] = mapped_column(
        JSONB, default=list, server_default=text("'[]'::jsonb")
    )


class EvaluationSuite(Entity):
    __tablename__ = "evaluation_suites"
    name: Mapped[str]
    description: Mapped[str | None]


class EvaluationPrompt(Entity):
    __tablename__ = "evaluation_prompts"
    __table_args__ = (
        UniqueConstraint("suite_id", "position"),
        CheckConstraint("position >= 0", name="nonnegative_position"),
    )
    suite_id: Mapped[UUID] = mapped_column(ForeignKey("evaluation_suites.id"), index=True)
    position: Mapped[int]
    prompt: Mapped[str]


class TrainingRun(Entity):
    __tablename__ = "training_runs"
    __table_args__ = (
        CheckConstraint("sha256 ~ '^[0-9a-f]{64}$'", name="valid_sha256"),
        CheckConstraint("jsonb_typeof(appspec) = 'object'", name="appspec_object"),
        CheckConstraint("jsonb_typeof(resolved_config) = 'object'", name="resolved_object"),
    )
    base_model_version_id: Mapped[UUID] = mapped_column(ForeignKey("model_versions.id"), index=True)
    dataset_version_id: Mapped[UUID] = mapped_column(ForeignKey("dataset_versions.id"), index=True)
    execution_target_id: Mapped[UUID] = mapped_column(
        ForeignKey("execution_targets.id"), index=True
    )
    evaluation_suite_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("evaluation_suites.id"), index=True
    )
    appspec_version: Mapped[str]
    appspec: Mapped[dict[str, Any]] = mapped_column(JSONB)
    resolved_config: Mapped[dict[str, Any]] = mapped_column(JSONB)
    sha256: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(default="QUEUED", server_default="QUEUED", index=True)
    external_id: Mapped[str | None]


class JobEvent(Entity):
    __tablename__ = "job_events"
    __table_args__ = (
        UniqueConstraint("training_run_id", "sequence"),
        CheckConstraint("sequence >= 0", name="nonnegative_sequence"),
    )
    training_run_id: Mapped[UUID] = mapped_column(ForeignKey("training_runs.id"), index=True)
    sequence: Mapped[int]
    status: Mapped[str]
    message: Mapped[str | None]


class EvaluationResult(Entity):
    __tablename__ = "evaluation_results"
    training_run_id: Mapped[UUID] = mapped_column(ForeignKey("training_runs.id"), index=True)
    prompt_id: Mapped[UUID] = mapped_column(ForeignKey("evaluation_prompts.id"), index=True)
    model_version_id: Mapped[UUID] = mapped_column(ForeignKey("model_versions.id"), index=True)
    response_artifact_id: Mapped[UUID] = mapped_column(ForeignKey("artifacts.id"), index=True)
    preference: Mapped[str | None]


class Deployment(Entity):
    __tablename__ = "deployments"
    model_version_id: Mapped[UUID] = mapped_column(ForeignKey("model_versions.id"), index=True)
    artifact_id: Mapped[UUID] = mapped_column(ForeignKey("artifacts.id"), index=True)
    execution_target_id: Mapped[UUID] = mapped_column(
        ForeignKey("execution_targets.id"), index=True
    )
    status: Mapped[str] = mapped_column(default="QUEUED", server_default="QUEUED", index=True)
    external_id: Mapped[str | None]
    endpoint: Mapped[str | None]
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
