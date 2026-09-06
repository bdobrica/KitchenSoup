import hashlib
from io import BytesIO
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.storage.base import ObjectTooLarge
from app.storage.hashing import hash_content
from app.storage.keys import artifact_key, safe_filename, upload_key


def test_hashing_is_bounded_and_exact() -> None:
    data = b"synthetic fixture" * 100000
    assert hash_content(BytesIO(data), max_bytes=len(data)) == (
        hashlib.sha256(data).hexdigest(),
        len(data),
    )
    assert hash_content(BytesIO(), max_bytes=0) == (hashlib.sha256(b"").hexdigest(), 0)
    with pytest.raises(ObjectTooLarge):
        hash_content(BytesIO(data), max_bytes=1)


def test_keys_keep_staging_separate_and_cannot_traverse() -> None:
    identifier = UUID("00000000-0000-4000-8000-000000000001")
    name = "../../a\\b.html"
    safe = safe_filename(name)
    assert "/" not in safe and "\\" not in safe
    assert safe_filename("...") == "file"
    assert upload_key(identifier, name).startswith(f"uploads/v1/{identifier}/")
    assert artifact_key(identifier, name).startswith(f"raw/v1/{identifier}/")
    assert len(safe_filename("a" * 500)) == 128


def test_storage_api_is_unavailable_in_service_free_shell() -> None:
    with TestClient(create_app(Settings(_env_file=None))) as client:
        assert client.get("/artifacts").status_code == 200
        response = client.post("/api/v1/artifact-uploads", json={"filename": "x", "size_bytes": 1})
        assert response.status_code == 503
        assert client.get("/healthz").json() == {"status": "ok"}


def test_upload_request_rejects_extra_keys_and_bad_sizes() -> None:
    from pydantic import ValidationError

    from app.api.artifacts import UploadRequest

    for value in (-1, "4", True):
        with pytest.raises(ValidationError):
            UploadRequest.model_validate({"filename": "x", "size_bytes": value})
    with pytest.raises(ValidationError):
        UploadRequest.model_validate({"filename": "x", "size_bytes": 1, "key": "chosen/by/client"})


def test_published_openapi_matches_routes() -> None:
    import json
    from pathlib import Path

    published = json.loads(Path("docs/contracts/artifacts-v1.openapi.json").read_text())
    assert create_app(Settings(_env_file=None, app_name="KitchenSoup")).openapi() == published
