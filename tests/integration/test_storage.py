import hashlib
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from io import BytesIO
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.db.models import Artifact, ArtifactUpload
from app.main import create_app
from app.services.artifacts import ArtifactService, UploadError
from app.storage.base import ObjectNotFound, ObjectTooLarge
from app.storage.s3 import S3ArtifactStore


@pytest.fixture
def store() -> Iterator[S3ArtifactStore]:
    store = S3ArtifactStore(Settings(_env_file=None))
    store.provision(["http://localhost:8000"])
    yield store
    store.close()


@pytest.fixture
def service(store: S3ArtifactStore, factory: sessionmaker[Session]) -> ArtifactService:
    return ArtifactService(store, factory, bucket=store.bucket)


def test_adapter_put_get_stat_delete_and_multipart(store: S3ArtifactStore) -> None:
    key = f"tests/{uuid4()}/multipart"
    data = b"x" * (9 * 1024**2)
    assert store.put(key, BytesIO(data)).size_bytes == len(data)
    assert store.stat(key).size_bytes == len(data)
    with store.get(key) as body:
        assert hashlib.sha256(body.read()).hexdigest() == hashlib.sha256(data).hexdigest()
    with pytest.raises(ObjectTooLarge):
        store.get(key, max_bytes=1)
    store.delete(key)
    store.delete(key)
    with pytest.raises(ObjectNotFound):
        store.stat(key)


def test_direct_upload_completion_and_download(
    service: ArtifactService, factory: sessionmaker[Session]
) -> None:
    payload = b"synthetic direct upload"
    with TestClient(create_app(Settings(_env_file=None), artifact_service=service)) as api:
        response = api.post(
            "/api/v1/artifact-uploads",
            json={
                "filename": "fixture.txt",
                "content_type": "text/plain",
                "size_bytes": len(payload),
            },
        )
        assert response.status_code == 201
        assert response.headers["cache-control"] == "no-store"
        grant = response.json()
        with httpx.Client() as browser:
            preflight = browser.options(
                grant["url"],
                headers={
                    "Origin": "http://localhost:8000",
                    "Access-Control-Request-Method": "PUT",
                    "Access-Control-Request-Headers": "content-type",
                },
            )
            assert preflight.status_code == 200
            assert preflight.headers["access-control-allow-origin"] == "http://localhost:8000"
            upload = browser.put(grant["url"], headers=grant["headers"], content=payload)
            assert upload.status_code == 200
            completed = api.post(f"/api/v1/artifact-uploads/{grant['upload_id']}/complete")
            assert completed.status_code == 200
            artifact = completed.json()
            assert artifact["sha256"] == hashlib.sha256(payload).hexdigest()
            assert artifact["size_bytes"] == len(payload)
            # Replaying a still-valid upload capability cannot replace registered bytes.
            assert (
                browser.put(
                    grant["url"], headers=grant["headers"], content=b"z" * len(payload)
                ).status_code
                == 200
            )
            assert (
                api.post(f"/api/v1/artifact-uploads/{grant['upload_id']}/complete").json()
                == artifact
            )
            result = api.post(f"/api/v1/artifacts/{artifact['id']}/download")
            assert result.status_code == 200
            downloaded = browser.get(result.json()["url"])
            assert downloaded.status_code == 200
            assert downloaded.content == payload
            assert downloaded.headers["content-disposition"] == "attachment"
            assert api.get(f"/api/v1/artifacts/{artifact['id']}").json() == artifact
        with factory() as session:
            assert session.scalar(select(func.count()).select_from(Artifact)) == 1


def test_missing_expired_mismatched_and_oversize_uploads(
    service: ArtifactService, factory: sessionmaker[Session]
) -> None:
    with pytest.raises(UploadError) as error:
        service.complete(uuid4())
    assert error.value.status == 404
    with pytest.raises(UploadError) as error:
        service.initiate("large", "text/plain", service.max_bytes + 1)
    assert error.value.status == 413
    grant = service.initiate("fixture", "text/plain", 2)
    with pytest.raises(ObjectNotFound):
        service.complete(grant.upload_id)
    with factory.begin() as session:
        upload = session.get(ArtifactUpload, grant.upload_id)
        assert upload is not None
        key = upload.object_key
    service.store.put(key, BytesIO(b"longer"))
    with pytest.raises(UploadError) as error:
        service.complete(grant.upload_id)
    assert error.value.status == 409
    with factory.begin() as session:
        upload = session.get(ArtifactUpload, grant.upload_id)
        assert upload is not None
        upload.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    with pytest.raises(UploadError) as error:
        service.complete(grant.upload_id)
    assert error.value.status == 410
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(Artifact)) == 0


def test_concurrent_completion_and_empty_upload(
    service: ArtifactService, factory: sessionmaker[Session]
) -> None:
    grant = service.initiate("empty", "application/octet-stream", 0)
    with httpx.Client() as client:
        assert client.put(grant.url, headers=grant.headers, content=b"").status_code == 200
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(service.complete, [grant.upload_id, grant.upload_id]))
    assert results[0].id == results[1].id
    assert results[0].sha256 == hashlib.sha256(b"").hexdigest()
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(Artifact)) == 1


def test_signed_put_enforces_size_and_type(store: S3ArtifactStore) -> None:
    url = store.presign_put(f"tests/{uuid4()}/signed", 60, content_type="text/plain", size_bytes=3)
    with httpx.Client() as client:
        assert (
            client.put(url, headers={"Content-Type": "text/plain"}, content=b"four").status_code
            == 403
        )
        assert (
            client.put(url, headers={"Content-Type": "text/html"}, content=b"abc").status_code
            == 403
        )
        assert (
            client.put(url, headers={"Content-Type": "text/plain"}, content=b"abc").status_code
            == 200
        )
    with pytest.raises(ValueError):
        store.presign_get("x", 0)
