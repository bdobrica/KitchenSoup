from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from io import BytesIO
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.db.models import (
    ArtifactUpload,
    ConversationImport,
    DatasetVersion,
    Document,
    RawSourceDeletion,
)
from app.ingestion.schemas import DatasetCreate, SourceAttach
from app.ingestion.sources import SourceError
from app.main import create_app
from app.services.artifacts import ArtifactService, UploadError
from app.services.datasets import DatasetService
from app.storage.base import ObjectNotFound, StorageError
from app.storage.keys import artifact_key, upload_key
from app.storage.s3 import S3ArtifactStore
from tests.integration.test_dataset_sources import upload


@pytest.fixture
def datasets(factory: sessionmaker[Session]) -> Iterator[DatasetService]:
    store = S3ArtifactStore(Settings(_env_file=None))
    store.provision(["http://localhost:8000"])
    try:
        yield DatasetService(factory, ArtifactService(store, factory, bucket=store.bucket))
    finally:
        store.close()


def source(service: DatasetService) -> tuple[UUID, UUID, UUID]:
    dataset = service.create(DatasetCreate(name="Retention fixture"))
    artifact_id = upload(service, "notes.txt", b"Synthetic original")
    attached = service.attach(
        dataset.id, SourceAttach(artifact_id=artifact_id, filename="notes.txt")
    )
    return dataset.id, attached.id, artifact_id


def test_confirmed_raw_deletion_preserves_metadata_and_rejects_replays(
    datasets: DatasetService,
) -> None:
    dataset, attached, artifact = source(datasets)
    raw_key = datasets.artifacts.get(artifact).object_key
    staging_key = upload_key(artifact, "notes.txt")
    datasets.artifacts.store.put(staging_key, BytesIO(b"Synthetic replayed staging"))
    path = f"/api/v1/datasets/{dataset}/sources/{attached}/delete-raw"
    with TestClient(
        create_app(
            Settings(_env_file=None), artifact_service=datasets.artifacts, dataset_service=datasets
        )
    ) as client:
        assert client.post(path, json={"confirm": False}).status_code == 422
        assert client.post(path, json={"confirm": "true"}).status_code == 422
        assert client.post(path, json={"confirm": True}).json() == {
            "artifact_id": str(artifact),
            "deleted": True,
        }
        assert client.post(path, json={"confirm": True}).status_code == 200
        assert client.post(f"/api/v1/artifacts/{artifact}/download").status_code == 410
        assert client.post(f"/api/v1/artifact-uploads/{artifact}/complete").status_code == 410
        assert client.get(f"/api/v1/artifacts/{artifact}").status_code == 200
    for key in (raw_key, staging_key):
        with pytest.raises(ObjectNotFound):
            datasets.artifacts.store.stat(key)
    assert not datasets.get(dataset).sources[0].raw_available
    assert datasets.artifacts.get(artifact).size_bytes == len(b"Synthetic original")
    with pytest.raises(UploadError, match="explicitly deleted"):
        datasets.attach(dataset, SourceAttach(artifact_id=artifact, filename="notes.txt"))
    with datasets.factory() as session:
        row = session.get(RawSourceDeletion, artifact)
        assert row is not None and row.deleted_at is not None
    with pytest.raises(DBAPIError, match="monotonic"):
        with datasets.factory.begin() as session:
            session.execute(
                text("UPDATE raw_source_deletions SET deleted_at=NULL WHERE artifact_id=:id"),
                {"id": artifact},
            )
    with pytest.raises(DBAPIError, match="retained"):
        with datasets.factory.begin() as session:
            session.execute(
                text("DELETE FROM raw_source_deletions WHERE artifact_id=:id"), {"id": artifact}
            )


@pytest.mark.parametrize("failed_copy", ["raw", "staging"])
def test_storage_failure_is_durable_and_retryable(
    datasets: DatasetService, monkeypatch: pytest.MonkeyPatch, failed_copy: str
) -> None:
    dataset, attached, artifact = source(datasets)
    original = datasets.artifacts.store.delete

    def unavailable(key: str) -> None:
        if key.startswith("raw/" if failed_copy == "raw" else "uploads/"):
            raise StorageError("Storage unavailable")
        original(key)

    monkeypatch.setattr(datasets.artifacts.store, "delete", unavailable)
    with pytest.raises(StorageError):
        datasets.delete_raw(dataset, attached, confirm=True)
    with datasets.factory() as session:
        row = session.get(RawSourceDeletion, artifact)
        assert row is not None and row.deleted_at is None
    with pytest.raises(UploadError):
        datasets.download(dataset, attached)
    with pytest.raises(SourceError):
        datasets.remove(dataset, attached)
    monkeypatch.setattr(datasets.artifacts.store, "delete", original)
    assert datasets.delete_raw(dataset, attached, confirm=True).deleted


def test_shared_source_is_protected(datasets: DatasetService) -> None:
    dataset, attached, artifact = source(datasets)
    other = datasets.create(DatasetCreate(name="Other dataset"))
    datasets.attach(other.id, SourceAttach(artifact_id=artifact, filename="notes.txt"))
    with pytest.raises(SourceError, match="shared or referenced"):
        datasets.delete_raw(dataset, attached, confirm=True)
    assert datasets.get(dataset).sources[0].raw_available


@pytest.mark.parametrize("dependency", ["canonical", "import", "version"])
def test_processed_originals_are_protected(datasets: DatasetService, dependency: str) -> None:
    dataset, attached, artifact = source(datasets)
    with datasets.factory.begin() as session:
        if dependency == "canonical":
            document = session.scalar(select(Document).where(Document.source_id == attached))
            assert document is not None
            document.canonical_artifact_id = artifact
        elif dependency == "import":
            session.add(
                ConversationImport(
                    source_id=attached, importer="fixture", importer_version="1", schema_version="1"
                )
            )
        else:
            session.add(
                DatasetVersion(
                    dataset_id=dataset,
                    version=1,
                    manifest_version="fixture",
                    manifest={},
                    sha256="a" * 64,
                )
            )
    with pytest.raises(SourceError):
        datasets.delete_raw(dataset, attached, confirm=True)
    assert datasets.artifacts.store.stat(datasets.artifacts.get(artifact).object_key).size_bytes > 0


def test_sweep_expires_only_reservation_owned_objects(datasets: DatasetService) -> None:
    service = datasets.artifacts
    grant = service.initiate("expired.txt", "text/plain", 1)
    fresh = service.initiate("fresh.txt", "text/plain", 1)
    _, _, registered = source(datasets)
    expired_staging = upload_key(grant.upload_id, "expired.txt")
    expired_final = artifact_key(grant.upload_id, "expired.txt")
    for key in (expired_staging, expired_final, upload_key(fresh.upload_id, "fresh.txt")):
        service.store.put(key, BytesIO(b"x"))
    # Replayed staging for a completed receipt must expire without deleting its final object.
    registered_staging = upload_key(registered, "notes.txt")
    service.store.put(registered_staging, BytesIO(b"x"))
    with datasets.factory() as session:
        for identifier in (grant.upload_id, registered):
            row = session.get(ArtifactUpload, identifier)
            assert row is not None
            row.expires_at = datetime.now(UTC) - timedelta(days=2)
        session.commit()
    assert service.sweep_uploads(limit=1) == 1
    assert service.sweep_uploads(limit=1) == 1  # A completed batch cannot starve later receipts.
    assert service.sweep_uploads() == 0
    for key in (expired_staging, expired_final, registered_staging):
        with pytest.raises(ObjectNotFound):
            service.store.stat(key)
    assert service.store.stat(upload_key(fresh.upload_id, "fresh.txt")).size_bytes == 1
    assert service.store.stat(service.get(registered).object_key).size_bytes > 0
    assert service.complete(registered).id == registered
    with datasets.factory() as session:
        assert len(list(session.scalars(select(ArtifactUpload)))) == 3
