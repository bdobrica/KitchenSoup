import hashlib
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from uuid import UUID, uuid4
from zipfile import ZipFile

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.db.models import Artifact, ConversationImport, DatasetSource, DatasetVersion, Document
from app.ingestion.schemas import DatasetCreate, SourceAttach
from app.ingestion.sources import SourceError
from app.main import create_app
from app.services.artifacts import ArtifactService
from app.services.datasets import DatasetService
from app.storage.keys import upload_key
from app.storage.s3 import S3ArtifactStore


@pytest.fixture
def datasets(factory: sessionmaker[Session]) -> Iterator[DatasetService]:
    store = S3ArtifactStore(Settings(_env_file=None))
    store.provision(["http://localhost:8000"])
    yield DatasetService(factory, ArtifactService(store, factory, bucket=store.bucket))
    store.close()


def upload(service: DatasetService, filename: str, payload: bytes) -> UUID:
    grant = service.artifacts.initiate(filename, "application/octet-stream", len(payload))
    service.artifacts.store.put(upload_key(grant.upload_id, filename), BytesIO(payload))
    return service.artifacts.complete(grant.upload_id).id


def test_dataset_upload_download_remove_api(datasets: DatasetService) -> None:
    payload = b"original source\r\n"
    with TestClient(
        create_app(
            Settings(_env_file=None), artifact_service=datasets.artifacts, dataset_service=datasets
        )
    ) as client:
        result = client.post(
            "/api/v1/datasets", json={"name": "Example", "license": "test license"}
        )
        assert result.status_code == 201
        dataset = result.json()
        assert dataset["license"] == "test license" and dataset["sources"] == []
        identifier = dataset["id"]
        assert client.get("/datasets").status_code == 200
        assert client.get(f"/datasets/{identifier}").status_code == 200
        grant = client.post(
            "/api/v1/artifact-uploads", json={"filename": "notes.txt", "size_bytes": len(payload)}
        ).json()
        assert httpx.put(grant["url"], headers=grant["headers"], content=payload).status_code == 200
        artifact = client.post(f"/api/v1/artifact-uploads/{grant['upload_id']}/complete").json()
        response = client.post(
            f"/api/v1/datasets/{identifier}/sources",
            json={"artifact_id": artifact["id"], "filename": "notes.txt"},
        )
        assert response.status_code == 200
        source = response.json()
        assert source["sha256"] == hashlib.sha256(payload).hexdigest()
        assert source["documents"][0]["canonical_artifact_id"] is None
        assert source["documents"][0]["media_type"] == "text/plain"
        assert len(client.get("/api/v1/datasets").json()) == 1
        assert client.get(f"/api/v1/datasets/{identifier}").json()["sources"] == [source]
        path = f"/api/v1/datasets/{identifier}/sources/{source['id']}"
        download = client.post(path + "/download")
        assert download.headers["cache-control"] == "no-store"
        assert httpx.get(download.json()["url"]).content == payload
        assert (
            client.delete(f"/api/v1/datasets/{uuid4()}/sources/{source['id']}").status_code == 404
        )
        removed = client.delete(path)
        assert removed.status_code == 200 and removed.json()["raw_artifact_retained"]
        assert client.get(f"/api/v1/datasets/{identifier}").json()["sources"] == []
        assert client.delete(path).status_code == 404
        retained = client.post(f"/api/v1/artifacts/{artifact['id']}/download").json()
        assert httpx.get(retained["url"]).content == payload
    with datasets.factory() as session:
        assert session.scalar(select(func.count()).select_from(Document)) == 0
        assert session.scalar(select(func.count()).select_from(Artifact)) == 1


def test_attach_idempotency_and_dataset_isolation(datasets: DatasetService) -> None:
    first = datasets.create(DatasetCreate(name="First"))
    second = datasets.create(DatasetCreate(name="Second"))
    artifact = upload(datasets, "x.md", b"# Title")
    request = SourceAttach(artifact_id=artifact, filename="x.md", license="declared")
    with ThreadPoolExecutor(max_workers=2) as executor:
        sources = list(executor.map(lambda _: datasets.attach(first.id, request), range(2)))
    assert sources[0] == sources[1]
    with pytest.raises(SourceError):
        datasets.remove(second.id, sources[0].id)
    with pytest.raises(SourceError):
        datasets.attach(first.id, request.model_copy(update={"license": "changed"}))
    other = datasets.attach(second.id, request)
    datasets.remove(first.id, sources[0].id)
    assert datasets.get(second.id).sources == [other]


def test_invalid_sources_leave_no_metadata_and_preserve_raw(datasets: DatasetService) -> None:
    dataset = datasets.create(DatasetCreate(name="Sources"))
    body = BytesIO()
    with ZipFile(body, "w") as archive:
        archive.writestr("../outside.txt", "invalid path")
    for filename, payload in [
        ("bad.zip", body.getvalue()),
        ("bad.pdf", b"not pdf"),
        ("bad.exe", b"not supported"),
    ]:
        artifact = upload(datasets, filename, payload)
        with pytest.raises(SourceError):
            datasets.attach(dataset.id, SourceAttach(artifact_id=artifact, filename=filename))
        raw = datasets.artifacts.get(artifact)
        with datasets.artifacts.store.get(raw.object_key) as content:
            assert content.read() == payload
    assert datasets.get(dataset.id).sources == []
    with datasets.factory() as session:
        assert session.scalar(select(func.count()).select_from(Document)) == 0


@pytest.mark.parametrize("dependency", ["canonical", "import", "version"])
def test_processed_sources_cannot_be_removed(datasets: DatasetService, dependency: str) -> None:
    dataset = datasets.create(DatasetCreate(name="Preserve provenance"))
    artifact = upload(datasets, "source.txt", b"source")
    source = datasets.attach(dataset.id, SourceAttach(artifact_id=artifact, filename="source.txt"))
    with datasets.factory.begin() as session:
        if dependency == "canonical":
            document = session.get(Document, source.documents[0].id)
            assert document is not None
            document.canonical_artifact_id = artifact
        elif dependency == "import":
            session.add(
                ConversationImport(
                    source_id=source.id,
                    importer="fixture",
                    importer_version="1",
                    schema_version="1",
                )
            )
        else:
            session.add(
                DatasetVersion(
                    dataset_id=dataset.id,
                    version=1,
                    manifest_version="test",
                    manifest={},
                    sha256="a" * 64,
                )
            )
    with pytest.raises(SourceError) as error:
        datasets.remove(dataset.id, source.id)
    assert error.value.status == 409
    assert len(datasets.get(dataset.id).sources) == 1
    with datasets.factory() as session:
        assert session.get(DatasetSource, source.id) is not None
