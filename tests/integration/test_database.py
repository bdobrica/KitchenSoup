from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from alembic.config import Config
from sqlalchemy import Engine, inspect, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from alembic import command
from app.db.models import (
    Artifact,
    ArtifactDerivation,
    ArtifactUpload,
    Base,
    Conversation,
    ConversationImport,
    Dataset,
    DatasetSource,
    DatasetVersion,
    Deployment,
    Document,
    DocumentIngestion,
    EvaluationPrompt,
    EvaluationResult,
    EvaluationSuite,
    ExecutionTarget,
    JobEvent,
    LLMProvider,
    Model,
    ModelCatalogEntry,
    ModelSource,
    ModelVersion,
    ModelVersionParent,
    TrainingRun,
)
from app.db.repositories import ModelRepository
from app.db.session import unit_of_work


def test_migration_round_trip_and_no_schema_drift(engine: Engine) -> None:
    with engine.begin() as connection:
        config = Config("alembic.ini")
        config.attributes["connection"] = connection
        command.downgrade(config, "base")
        assert set(inspect(connection).get_table_names()) == {"alembic_version"}
        command.upgrade(config, "head")
        command.upgrade(config, "head")
        assert set(inspect(connection).get_table_names()) == set(Base.metadata.tables) | {
            "alembic_version"
        }
        command.check(config)


def test_repository_crud_and_explicit_commit(factory: sessionmaker[Session]) -> None:
    with unit_of_work(factory) as session:
        created = ModelRepository(session).add("Original")
        identifier = created.id
        assert isinstance(identifier, UUID)
        session.commit()
    with unit_of_work(factory) as session:
        repository = ModelRepository(session)
        model = repository.get(identifier)
        assert model is not None
        assert model.created_at.tzinfo is not None
        assert repository.list() == [model]
        model.name = "Updated"
        session.commit()
    with unit_of_work(factory) as session:
        repository = ModelRepository(session)
        model = repository.get(identifier)
        assert model is not None and model.name == "Updated"
        assert model.updated_at > model.created_at
        repository.delete(model)
        session.commit()
    with unit_of_work(factory) as session:
        assert ModelRepository(session).get(identifier) is None


def test_uncommitted_work_and_exceptions_roll_back(factory: sessionmaker[Session]) -> None:
    with unit_of_work(factory) as session:
        ModelRepository(session).add("Discarded")
    with pytest.raises(RuntimeError, match="abort"):
        with unit_of_work(factory) as session:
            ModelRepository(session).add("Also discarded")
            raise RuntimeError("abort")
    with unit_of_work(factory) as session:
        assert ModelRepository(session).list() == []


def seed_graph(session: Session) -> tuple[TrainingRun, DatasetVersion, JobEvent]:
    catalog = ModelCatalogEntry(
        key="fixture",
        name="Fixture",
        repository="fixture/repo",
        revision="revision",
        license="Apache-2.0",
    )
    model = Model(name="Fixture")
    dataset = Dataset(name="Fixture")
    target = ExecutionTarget(name="Local", kind="docker", credential_ref="env://TEST_TARGET")
    provider = LLMProvider(
        name="Fixture",
        base_url="https://example.invalid/v1",
        api_key_ref="env://TEST_PROVIDER",
        model_names=["fixture"],
    )
    suite = EvaluationSuite(name="Fixture")
    session.add_all([catalog, model, dataset, target, provider, suite])
    session.flush()
    base = ModelVersion(model_id=model.id, version=1)
    child = ModelVersion(model_id=model.id, version=2)
    prompt = EvaluationPrompt(suite_id=suite.id, position=0, prompt="Synthetic prompt")
    session.add_all([base, child, prompt])
    session.flush()
    raw = Artifact(
        bucket="fixtures", object_key="raw", sha256="a" * 64, size_bytes=10, format="zip"
    )
    output = Artifact(
        model_version_id=child.id,
        bucket="fixtures",
        object_key="output",
        sha256="b" * 64,
        size_bytes=20,
        format="adapter",
    )
    session.add_all([raw, output])
    session.flush()
    session.add(
        ArtifactUpload(
            object_key="uploads/fixture",
            filename="fixture.zip",
            content_type="application/zip",
            size_bytes=10,
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            artifact_id=raw.id,
        )
    )
    version = DatasetVersion(
        dataset_id=dataset.id,
        version=1,
        manifest_version="fixture/v1",
        manifest={"sources": [str(raw.id)]},
        sha256="c" * 64,
    )
    source = DatasetSource(
        dataset_id=dataset.id, artifact_id=raw.id, kind="upload", filename="fixture.zip"
    )
    session.add_all(
        [
            version,
            DocumentIngestion(
                dataset_id=dataset.id,
                version=1,
                status="succeeded",
                output_artifact_id=output.id,
                logs_artifact_id=raw.id,
                manifest_artifact_id=raw.id,
                manifest={"fixture": True},
            ),
            source,
            ModelVersionParent(child_id=child.id, parent_id=base.id),
            ModelSource(
                model_version_id=base.id,
                catalog_entry_id=catalog.id,
                kind="huggingface",
                location="fixture/repo",
                revision="revision",
                license="Apache-2.0",
            ),
            ArtifactDerivation(child_id=output.id, parent_id=raw.id, operation="fixture"),
        ]
    )
    session.flush()
    imported = ConversationImport(
        source_id=source.id,
        canonical_artifact_id=raw.id,
        importer="fixture",
        importer_version="1",
        schema_version="fixture/v1",
    )
    run = TrainingRun(
        base_model_version_id=base.id,
        dataset_version_id=version.id,
        execution_target_id=target.id,
        evaluation_suite_id=suite.id,
        appspec_version="fixture/v1",
        appspec={"intent": "fixture"},
        resolved_config={"fixture": True},
        sha256="d" * 64,
    )
    session.add_all(
        [imported, run, Document(source_id=source.id, title="Fixture", media_type="text/plain")]
    )
    session.flush()
    event = JobEvent(training_run_id=run.id, sequence=0, status="QUEUED")
    session.add_all(
        [
            event,
            Conversation(
                import_id=imported.id,
                source_conversation_id="source-1",
                title="Fixture",
                message_count=2,
            ),
            EvaluationResult(
                training_run_id=run.id,
                prompt_id=prompt.id,
                model_version_id=child.id,
                response_artifact_id=output.id,
            ),
            Deployment(
                model_version_id=child.id,
                artifact_id=output.id,
                execution_target_id=target.id,
                expires_at=datetime.now(UTC) + timedelta(hours=1),
            ),
        ]
    )
    session.flush()
    return run, version, event


def test_all_core_entities_persist_and_can_be_queried(factory: sessionmaker[Session]) -> None:
    with unit_of_work(factory) as session:
        seed_graph(session)
        session.commit()
    with unit_of_work(factory) as session:
        for table in Base.metadata.sorted_tables:
            row = session.execute(select(table)).first()
            assert row is not None, table.name
            assert isinstance(row._mapping["id"], UUID)
            assert row._mapping["created_at"].tzinfo is not None
        artifact = session.scalar(select(Artifact).where(Artifact.object_key == "output"))
        assert artifact is not None and artifact.model_version_id is not None


def test_foreign_keys_uniqueness_and_hashes(factory: sessionmaker[Session]) -> None:
    with pytest.raises(IntegrityError):
        with unit_of_work(factory) as session:
            session.add(ModelVersion(model_id=uuid4(), version=1))
            session.flush()
    with unit_of_work(factory) as session:
        model = ModelRepository(session).add("Fixture")
        identifier = model.id
        session.add(ModelVersion(model_id=identifier, version=1))
        session.commit()
    with pytest.raises(IntegrityError):
        with unit_of_work(factory) as session:
            session.add(ModelVersion(model_id=identifier, version=1))
            session.flush()
    with pytest.raises(IntegrityError):
        with unit_of_work(factory) as session:
            session.add(
                Artifact(
                    bucket="fixture",
                    object_key="bad",
                    sha256="invalid",
                    size_bytes=1,
                    format="text",
                )
            )
            session.flush()
    with pytest.raises(IntegrityError):
        with unit_of_work(factory) as session:
            session.add(ExecutionTarget(name="Bad", kind="ssh", credential_ref="not-a-reference"))
            session.flush()


def test_identifiers_are_immutable_even_via_sql(factory: sessionmaker[Session]) -> None:
    with unit_of_work(factory) as session:
        model = ModelRepository(session).add("Fixture")
        identifier = model.id
        session.commit()
    with pytest.raises(DBAPIError, match="immutable"):
        with unit_of_work(factory) as session:
            session.execute(
                text("UPDATE models SET id = :new WHERE id = :old"),
                {"new": uuid4(), "old": identifier},
            )


def test_snapshots_and_events_are_immutable_but_run_status_can_change(
    factory: sessionmaker[Session],
) -> None:
    with unit_of_work(factory) as session:
        run, version, event = seed_graph(session)
        run_id, version_id, event_id = run.id, version.id, event.id
        session.commit()
    for statement, identifier in (
        ("UPDATE dataset_versions SET manifest = '{}'::jsonb WHERE id = :id", version_id),
        ("UPDATE training_runs SET appspec = '{}'::jsonb WHERE id = :id", run_id),
        ("UPDATE job_events SET status = 'RUNNING' WHERE id = :id", event_id),
        ("DELETE FROM job_events WHERE id = :id", event_id),
    ):
        with pytest.raises(DBAPIError, match="immutable"):
            with unit_of_work(factory) as session:
                session.execute(text(statement), {"id": identifier})
    with unit_of_work(factory) as session:
        loaded = session.get(TrainingRun, run_id)
        assert loaded is not None
        loaded.status = "PREPARING"
        session.commit()
