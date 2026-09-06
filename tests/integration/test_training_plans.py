from collections.abc import Iterator
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.db.models import (
    Artifact,
    DatasetVersion,
    Model,
    ModelSource,
    ModelVersion,
    TrainingPlan,
    TrainingRun,
)
from app.ingestion.documents import DocumentSelection
from app.ingestion.sources import SourceError
from app.ingestion.versions import DatasetVersionCreate
from app.main import create_app
from app.services.artifacts import ArtifactService
from app.services.conversations import ConversationService
from app.services.datasets import DatasetService
from app.services.documents import DocumentIngestionService
from app.services.training import TrainingPlanService
from app.services.versions import DatasetVersionService
from app.storage.s3 import S3ArtifactStore
from app.training.schemas import AppSpec, TrainingPlanView
from tests.integration.test_chatgpt_conversations import source
from tests.integration.test_document_ingestion import FixtureRunner, attach


@pytest.fixture
def versions(factory: sessionmaker[Session]) -> Iterator[DatasetVersionService]:
    store = S3ArtifactStore(Settings(_env_file=None))
    store.provision(["http://localhost:8000"])
    yield DatasetVersionService(
        DatasetService(factory, ArtifactService(store, factory, bucket=store.bucket))
    )
    store.close()


def inputs(versions: DatasetVersionService) -> AppSpec:
    conversations = ConversationService(versions.datasets)
    dataset, source_id, _ = source(conversations)
    conversations.import_source(dataset, source_id)
    version = versions.create(
        dataset,
        DatasetVersionCreate(
            conversation_ids=[c.id for c in conversations.list_conversations(dataset)]
        ),
    )
    with versions.datasets.factory() as session:
        model = Model(name="Synthetic plan model")
        session.add(model)
        session.flush()
        base = ModelVersion(model_id=model.id, version=1)
        session.add(base)
        session.flush()
        session.add(
            ModelSource(
                model_version_id=base.id,
                kind="catalog",
                location="fixture/model",
                revision="a" * 40,
                license="Apache-2.0",
            )
        )
        session.commit()
        return AppSpec(
            intent="conversation_imitation",
            base_model_version_id=base.id,
            dataset_version_id=version.id,
        )


def test_plan_snapshot_hash_review_guard_and_no_run(versions: DatasetVersionService) -> None:
    spec = inputs(versions)
    service = TrainingPlanService(versions.datasets.factory)
    preview = service.preview(spec)
    with versions.datasets.factory() as session:
        assert session.scalar(select(func.count()).select_from(TrainingPlan)) == 0
    saved = service.create(spec, preview.sha256)
    assert saved.resolved == preview.resolved and saved.appspec == spec
    assert TrainingPlanView.model_validate_json(saved.model_dump_json(by_alias=True)) == saved
    with versions.datasets.factory() as session:
        registered = session.scalar(select(ModelSource))
        assert registered is not None
        registered.revision = "b" * 40
        session.commit()
    assert service.get(saved.id) == saved
    assert service.preview(spec).sha256 != saved.sha256
    with pytest.raises(SourceError, match="changed"):
        service.create(spec, preview.sha256)
    with versions.datasets.factory() as session:
        assert session.scalar(select(func.count()).select_from(TrainingRun)) == 0
        assert session.scalar(select(func.count()).select_from(TrainingPlan)) == 1
        with pytest.raises(DBAPIError, match="immutable"):
            session.execute(
                text("UPDATE training_plans SET appspec='{}' WHERE id=:id"), {"id": saved.id}
            )
            session.commit()
    with versions.datasets.factory() as session:
        with pytest.raises(IntegrityError):
            session.execute(
                text("DELETE FROM dataset_versions WHERE id=:id"), {"id": spec.dataset_version_id}
            )
            session.commit()


def test_incompatible_inputs_and_unpinned_model_do_not_save(
    versions: DatasetVersionService,
) -> None:
    spec = inputs(versions)
    service = TrainingPlanService(versions.datasets.factory)
    for changes in ({"intent": "learn_from_documents"}, {"dataset_version_id": uuid4()}):
        with pytest.raises(SourceError):
            service.create(AppSpec.model_validate(spec.model_dump() | changes))
    with versions.datasets.factory() as session:
        source_row = session.scalar(select(ModelSource))
        assert source_row is not None
        source_row.revision = "main"
        session.commit()
    with pytest.raises(SourceError, match="exact registered commit"):
        service.create(spec)
    with versions.datasets.factory() as session:
        assert session.scalar(select(func.count()).select_from(TrainingPlan)) == 0


def test_document_recipe_and_mixed_dataset_rejection(versions: DatasetVersionService) -> None:
    spec = inputs(versions)
    with versions.datasets.factory() as session:
        version = session.get(DatasetVersion, spec.dataset_version_id)
        assert version is not None
        dataset = version.dataset_id
    documents = DocumentIngestionService(versions.datasets, FixtureRunner())
    document = attach(documents, dataset)
    ingestion = documents.ingest(dataset, DocumentSelection(source_ids=[document]))
    document_version = versions.create(
        dataset, DatasetVersionCreate(conversation_ids=[], document_ingestion_ids=[ingestion.id])
    )
    service = TrainingPlanService(versions.datasets.factory)
    doc_spec = AppSpec(
        intent="learn_from_documents",
        base_model_version_id=spec.base_model_version_id,
        dataset_version_id=document_version.id,
    )
    saved = service.create(doc_spec)
    assert saved.resolved.recipe.objective == "all_text_tokens"
    assert any("retrieval" in warning for warning in saved.resolved.warnings)
    mixed = versions.create(
        dataset,
        DatasetVersionCreate(
            conversation_ids=[
                c.id for c in ConversationService(versions.datasets).list_conversations(dataset)
            ],
            document_ingestion_ids=[ingestion.id],
        ),
    )
    for intent in ("conversation_imitation", "learn_from_documents"):
        with pytest.raises(SourceError, match="mixed sources"):
            service.create(
                AppSpec.model_validate(
                    spec.model_dump() | {"intent": intent, "dataset_version_id": mixed.id}
                )
            )


def test_plan_api_and_pages(versions: DatasetVersionService) -> None:
    spec = inputs(versions)
    with TestClient(
        create_app(Settings(_env_file=None), dataset_service=versions.datasets)
    ) as client:
        assert len(client.get("/api/v1/recipes").json()) == 3
        body = spec.model_dump(mode="json", by_alias=True)
        preview = client.post("/api/v1/training-plans/preview", json=body)
        assert preview.status_code == 200 and preview.headers["cache-control"] == "no-store"
        assert client.get("/api/v1/training-plans").json() == []
        assert (
            client.post(
                "/api/v1/training-plans", json=body, headers={"X-Plan-SHA256": "0" * 64}
            ).status_code
            == 409
        )
        response = client.post(
            "/api/v1/training-plans", json=body, headers={"X-Plan-SHA256": preview.json()["sha256"]}
        )
        assert response.status_code == 201
        result = response.json()
        assert client.get(f"/api/v1/training-plans/{result['id']}").json() == result
        assert client.get("/api/v1/training-plans").json() == [result]
        assert client.get(f"/api/v1/training-plans/{uuid4()}").status_code == 404
        assert (
            client.post(
                "/api/v1/training-plans", json=body | {"schema": "kitchensoup.appspec/v2"}
            ).status_code
            == 422
        )
        assert client.get("/training-plans").status_code == 200
        assert client.get(f"/training-plans/{result['id']}").status_code == 200


def test_uploaded_model_keeps_artifact_hash(versions: DatasetVersionService) -> None:
    spec = inputs(versions)
    with versions.datasets.factory() as session:
        source_row = session.scalar(select(ModelSource))
        artifact = session.scalar(select(Artifact))
        assert source_row is not None and artifact is not None
        source_row.kind = "upload"
        source_row.revision = None
        source_row.artifact_id = artifact.id
        source_row.location = f"artifact:{artifact.id}"
        artifact_id, sha256 = artifact.id, artifact.sha256
        session.commit()
    saved = TrainingPlanService(versions.datasets.factory).create(spec)
    assert saved.resolved.base_model_source.artifact_id == artifact_id
    assert saved.resolved.base_model_source.artifact_sha256 == sha256
    assert isinstance(saved.id, UUID)
