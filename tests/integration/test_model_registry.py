from collections.abc import Iterator
from io import BytesIO
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.db.models import Model, ModelCatalogEntry, ModelSource
from app.main import create_app
from app.registry.catalog import load_catalog, sync_catalog
from app.registry.huggingface import ResolvedModel
from app.registry.inspection import RegistryError
from app.registry.schemas import ArchiveImport, HuggingFaceImport
from app.services.artifacts import ArtifactService
from app.services.models import ModelService
from app.storage.keys import upload_key
from app.storage.s3 import S3ArtifactStore


class Resolver:
    def resolve(self, repository: str, revision: str) -> ResolvedModel:
        return ResolvedModel(repository, "a" * 40, "apache-2.0", "https://huggingface.co/org/model")


@pytest.fixture
def registry(factory: sessionmaker[Session]) -> Iterator[ModelService]:
    store = S3ArtifactStore(Settings(_env_file=None))
    store.provision(["http://localhost:8000"])
    yield ModelService(factory, ArtifactService(store, factory, bucket=store.bucket), Resolver())
    store.close()


def test_catalog_sync_selection_and_snapshot(
    registry: ModelService, factory: sessionmaker[Session]
) -> None:
    sync_catalog(factory)
    entry = registry.catalog()[0]
    model = registry.choose(entry.key)
    assert registry.choose(entry.key) == model
    sync_catalog(factory)
    assert registry.choose(entry.key) == model
    assert model.versions[0].sources[0].revision == entry.revision
    catalog = load_catalog()
    catalog.models[0].license = "changed-declaration"
    sync_catalog(factory, catalog)
    assert registry.get(model.id).versions[0].sources[0].license == "apache-2.0"
    catalog.models.pop(0)
    sync_catalog(factory, catalog)
    assert len(registry.catalog()) == 1
    with pytest.raises(RegistryError):
        registry.choose(entry.key)
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(ModelCatalogEntry)) == 2


def test_registry_api(registry: ModelService) -> None:
    sync_catalog(registry.factory)
    with TestClient(create_app(Settings(_env_file=None), model_service=registry)) as client:
        assert len(client.get("/api/v1/model-catalog").json()) == 2
        response = client.post(
            "/api/v1/model-imports/huggingface",
            json={"name": "My model", "repository": "org/model", "revision": "main"},
        )
        assert response.status_code == 201
        result = response.json()
        assert result["versions"][0]["sources"][0]["revision"] == "a" * 40
        assert client.get(f"/api/v1/models/{result['id']}").json() == result
        assert len(client.get("/api/v1/models").json()) == 1
        assert client.get(f"/api/v1/models/{uuid4()}").status_code == 404
        assert client.get("/models").status_code == 200
        assert client.get(f"/models/{result['id']}").status_code == 200
        assert (
            client.post(
                "/api/v1/model-imports/huggingface",
                json={"name": "x", "repository": "https://invalid.example/model"},
            ).status_code
            == 422
        )


def test_archive_registration_and_failure_rollback(
    registry: ModelService, model_archive: bytes, factory: sessionmaker[Session]
) -> None:
    grant = registry.artifacts.initiate("model.zip", "application/zip", len(model_archive))
    registry.artifacts.store.put(upload_key(grant.upload_id, "model.zip"), BytesIO(model_archive))
    artifact = registry.artifacts.complete(grant.upload_id)
    model = registry.import_archive(
        ArchiveImport(name="Custom", artifact_id=artifact.id, license="user-declared")
    )
    source = model.versions[0].sources[0]
    assert source.artifact_id == artifact.id and source.license == "user-declared"
    assert source.revision is None
    # Replacing an operator-controlled object must not permit stale hash registration.
    registry.artifacts.store.put(artifact.object_key, BytesIO(b"invalid"))
    with pytest.raises(RegistryError, match="hash"):
        registry.import_archive(
            ArchiveImport(name="Bad", artifact_id=artifact.id, license="unknown")
        )
    grant = registry.artifacts.initiate("invalid.zip", "application/zip", 3)
    registry.artifacts.store.put(upload_key(grant.upload_id, "invalid.zip"), BytesIO(b"bad"))
    artifact = registry.artifacts.complete(grant.upload_id)
    with pytest.raises(RegistryError):
        registry.import_archive(
            ArchiveImport(name="Bad", artifact_id=artifact.id, license="unknown")
        )
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(Model)) == 1
        assert session.scalar(select(func.count()).select_from(ModelSource)) == 1


def test_failed_provider_leaves_no_model(
    registry: ModelService, factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail(repository: str, revision: str) -> ResolvedModel:
        raise RegistryError(503, "Provider unavailable")

    monkeypatch.setattr(registry.resolver, "resolve", fail)
    with pytest.raises(RegistryError):
        registry.import_huggingface(HuggingFaceImport(name="Fail", repository="org/model"))
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(Model)) == 0
