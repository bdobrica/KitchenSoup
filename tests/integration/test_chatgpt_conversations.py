import hashlib
import json
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from pathlib import Path
from uuid import UUID, uuid4
from zipfile import ZipFile

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.db.models import Artifact, ArtifactDerivation, Conversation, ConversationImport
from app.ingestion.schemas import DatasetCreate, SourceAttach
from app.ingestion.selection import ConversationSelection
from app.ingestion.sources import SourceError
from app.main import create_app
from app.services.artifacts import ArtifactService
from app.services.conversations import ConversationService
from app.services.datasets import DatasetService
from app.storage.base import StorageError
from app.storage.keys import upload_key
from app.storage.s3 import S3ArtifactStore


@pytest.fixture
def service(factory: sessionmaker[Session]) -> Iterator[ConversationService]:
    store = S3ArtifactStore(Settings(_env_file=None))
    store.provision(["http://localhost:8000"])
    yield ConversationService(
        DatasetService(factory, ArtifactService(store, factory, bucket=store.bucket))
    )
    store.close()


def source(service: ConversationService, payload: bytes | None = None) -> tuple[UUID, UUID, UUID]:
    if payload is None:
        payload = Path("tests/fixtures/chatgpt/conversations.json").read_bytes()
    body = BytesIO()
    with ZipFile(body, "w") as archive:
        archive.writestr("export/conversations.json", payload)
        archive.writestr("export/chat.html", "Synthetic fixture")
    dataset = service.datasets.create(DatasetCreate(name="ChatGPT fixture"))
    artifacts = service.datasets.artifacts
    grant = artifacts.initiate("export.zip", "application/zip", len(body.getvalue()))
    artifacts.store.put(upload_key(grant.upload_id, "export.zip"), BytesIO(body.getvalue()))
    artifact = artifacts.complete(grant.upload_id)
    attached = service.datasets.attach(
        dataset.id, SourceAttach(artifact_id=artifact.id, filename="export.zip")
    )
    return dataset.id, attached.id, artifact.id


def test_import_canonical_lineage_and_repeatability(service: ConversationService) -> None:
    dataset_id, source_id, raw_id = source(service)
    raw = service.datasets.artifacts.get(raw_id)
    with ThreadPoolExecutor(max_workers=2) as executor:
        imports = list(
            executor.map(lambda _: service.import_source(dataset_id, source_id), range(2))
        )
    assert imports[0] == imports[1]
    imported = imports[0]
    assert imported.canonical_artifact_id is not None
    artifact = service.datasets.artifacts.get(imported.canonical_artifact_id)
    with service.datasets.artifacts.store.get(artifact.object_key) as body:
        data = body.read()
    assert hashlib.sha256(data).hexdigest() == artifact.sha256
    canonical = [json.loads(line) for line in data.splitlines()]
    assert canonical[0]["schema"] == "kitchensoup.conversation/v1"
    assert canonical[0]["conversation_id"] == "synthetic-chat-001"
    assert len(service.list_conversations(dataset_id)) == 2
    with service.datasets.factory() as session:
        assert session.scalar(select(func.count()).select_from(ConversationImport)) == 1
        edge = session.scalar(select(ArtifactDerivation))
        assert edge is not None and edge.parent_id == raw_id and edge.child_id == artifact.id
    with service.datasets.artifacts.store.get(raw.object_key) as body:
        assert hashlib.sha256(body.read()).hexdigest() == raw.sha256
    with pytest.raises(SourceError):
        service.datasets.remove(dataset_id, source_id)


def test_selection_api_is_dataset_scoped_and_persistent(service: ConversationService) -> None:
    dataset_id, source_id, _ = source(service)
    with TestClient(
        create_app(Settings(_env_file=None), dataset_service=service.datasets)
    ) as client:
        result = client.post(f"/api/v1/datasets/{dataset_id}/sources/{source_id}/imports/chatgpt")
        assert result.status_code == 200
        rows = client.get(f"/api/v1/datasets/{dataset_id}/conversations").json()
        assert len(rows) == 2 and not any(row["selected"] for row in rows)
        ids = [rows[0]["id"]]
        selected = client.put(
            f"/api/v1/datasets/{dataset_id}/conversation-selection", json={"conversation_ids": ids}
        )
        assert selected.status_code == 200
        assert sum(row["selected"] for row in selected.json()) == 1
        assert client.get(f"/api/v1/datasets/{dataset_id}/conversations").json() == selected.json()
        assert (
            client.put(
                f"/api/v1/datasets/{dataset_id}/conversation-selection",
                json={"conversation_ids": [str(uuid4())]},
            ).status_code
            == 422
        )
        assert (
            sum(
                row["selected"]
                for row in client.get(f"/api/v1/datasets/{dataset_id}/conversations").json()
            )
            == 1
        )
    assert not any(
        row.selected
        for row in service.select(dataset_id, ConversationSelection(conversation_ids=[]))
    )


@pytest.mark.parametrize("failure", ["late_parse", "write"])
def test_failed_import_preserves_raw_and_rolls_back(
    service: ConversationService, failure: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    data = json.loads(Path("tests/fixtures/chatgpt/conversations.json").read_text())
    if failure == "late_parse":
        data.append({"id": "bad", "mapping": {}})
    dataset_id, source_id, raw_id = source(service, json.dumps(data).encode())
    if failure == "write":

        def fail(*args: object, **kwargs: object) -> None:
            raise StorageError("Synthetic storage failure")

        monkeypatch.setattr(service.datasets.artifacts.store, "put", fail)
    with pytest.raises((SourceError, StorageError)):
        service.import_source(dataset_id, source_id)
    with service.datasets.factory() as session:
        assert session.scalar(select(func.count()).select_from(ConversationImport)) == 0
        assert session.scalar(select(func.count()).select_from(Conversation)) == 0
        assert session.scalar(select(func.count()).select_from(Artifact)) == 1
    raw = service.datasets.artifacts.get(raw_id)
    with service.datasets.artifacts.store.get(raw.object_key) as body:
        assert hashlib.sha256(body.read()).hexdigest() == raw.sha256
