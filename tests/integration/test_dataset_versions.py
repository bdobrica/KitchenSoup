import hashlib
import json
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.db.models import DatasetVersion
from app.ingestion.documents import DocumentSelection
from app.ingestion.selection import ConversationSelection
from app.ingestion.sources import SourceError
from app.ingestion.versions import DatasetVersionCreate
from app.main import create_app
from app.services.artifacts import ArtifactService
from app.services.conversations import ConversationService
from app.services.datasets import DatasetService
from app.services.documents import DocumentIngestionService
from app.services.versions import DatasetVersionService
from app.storage.base import StorageError
from app.storage.s3 import S3ArtifactStore
from tests.integration.test_chatgpt_conversations import source
from tests.integration.test_document_ingestion import FixtureRunner, attach


@pytest.fixture
def service(factory: sessionmaker[Session]) -> Iterator[DatasetVersionService]:
    store = S3ArtifactStore(Settings(_env_file=None))
    store.provision(["http://localhost:8000"])
    yield DatasetVersionService(
        DatasetService(factory, ArtifactService(store, factory, bucket=store.bucket))
    )
    store.close()


def test_snapshot_selection_pagination_and_immutable_history(
    service: DatasetVersionService,
) -> None:
    conversations = ConversationService(service.datasets)
    dataset, source_id, raw = source(conversations)
    conversations.import_source(dataset, source_id)
    selected = [row.id for row in conversations.list_conversations(dataset)]
    conversations.select(dataset, ConversationSelection(conversation_ids=selected))
    first = service.create(dataset, DatasetVersionCreate())
    assert first.manifest.statistics.conversation_count == 2
    assert first.manifest.statistics.source_count == 1
    assert first.manifest.sources[0].artifact.artifact_id == raw
    assert first.manifest.warnings
    page = service.preview(dataset, first.id, 0, 100)
    assert len(page.items) == first.manifest.statistics.example_count >= 2
    assert all(item.messages[-1].role == "assistant" for item in page.items)
    assert service.preview(dataset, first.id, 1, 1).items == page.items[1:2]
    assert service.preview(dataset, first.id, 100000, 20).items == []
    canonical_bytes = first.model_dump_json()
    conversations.select(dataset, ConversationSelection(conversation_ids=selected[:1]))
    second = service.create(dataset, DatasetVersionCreate())
    assert second.version == 2 and second.manifest.statistics.conversation_count == 1
    assert service.get(dataset, first.id).model_dump_json() == canonical_bytes
    assert service.preview(dataset, first.id, 0, 100) == page
    with service.datasets.factory() as session:
        with pytest.raises(DBAPIError, match="immutable"):
            session.execute(
                text("UPDATE dataset_versions SET manifest='{}' WHERE id=:id"), {"id": first.id}
            )
            session.commit()
    artifact = service.datasets.artifacts.get(first.manifest_artifact_id)
    with service.datasets.artifacts.store.get(artifact.object_key) as body:
        data = body.read()
    assert hashlib.sha256(data).hexdigest() == first.sha256
    assert json.loads(data) == first.manifest.model_dump(mode="json", by_alias=True)


def test_versions_serialize_concurrent_creation_and_preview_api(
    service: DatasetVersionService,
) -> None:
    conversations = ConversationService(service.datasets)
    dataset, source_id, _ = source(conversations)
    conversations.import_source(dataset, source_id)
    request = DatasetVersionCreate(
        conversation_ids=[c.id for c in conversations.list_conversations(dataset)]
    )
    with ThreadPoolExecutor(max_workers=2) as pool:
        versions = list(pool.map(lambda _: service.create(dataset, request), range(2)))
    assert {v.version for v in versions} == {1, 2}
    assert versions[0].manifest.examples.sha256 == versions[1].manifest.examples.sha256
    with TestClient(
        create_app(Settings(_env_file=None), dataset_service=service.datasets)
    ) as client:
        path = f"/api/v1/datasets/{dataset}/versions/{versions[0].id}"
        assert client.get(path).status_code == 200
        response = client.get(path + "/examples?limit=1")
        assert response.headers["cache-control"] == "no-store"
        assert len(response.json()["items"]) == 1
        assert client.get(path + "/examples?offset=-1").status_code == 422
        assert client.get(path + "/examples?limit=101").status_code == 422
        assert (
            client.get(f"/api/v1/datasets/{uuid4()}/versions/{versions[0].id}").status_code == 404
        )
        assert client.get(f"/datasets/{dataset}/versions/{versions[0].id}").status_code == 200


def test_document_rows_and_duplicate_extractions(service: DatasetVersionService) -> None:
    conversations = ConversationService(service.datasets)
    dataset, _, _ = source(conversations)
    documents = DocumentIngestionService(service.datasets, FixtureRunner())
    document_id = attach(documents, dataset)
    ingestion = documents.ingest(dataset, DocumentSelection(source_ids=[document_id]))
    request = DatasetVersionCreate(conversation_ids=[], document_ingestion_ids=[ingestion.id])
    version = service.create(dataset, request)
    stats = version.manifest.statistics
    assert (
        stats.conversation_count,
        stats.message_count,
        stats.example_count,
        stats.ignored_item_count,
    ) == (0, 0, 1, 1)
    item = service.preview(dataset, version.id, 0, 1).items[0]
    assert item.kind == "document" and item.text == "Synthetic document.\n"
    assert item.source_id == document_id and item.document_ingestion_id == ingestion.id
    repeated = documents.ingest(dataset, DocumentSelection(source_ids=[document_id]))
    with pytest.raises(SourceError, match="only one"):
        service.create(
            dataset,
            DatasetVersionCreate(
                conversation_ids=[], document_ingestion_ids=[ingestion.id, repeated.id]
            ),
        )


def test_invalid_selection_tampering_and_storage_failure_do_not_create_versions(
    service: DatasetVersionService, monkeypatch: pytest.MonkeyPatch
) -> None:
    conversations = ConversationService(service.datasets)
    dataset, source_id, _ = source(conversations)
    imported = conversations.import_source(dataset, source_id)
    with pytest.raises(SourceError, match="no usable"):
        service.create(dataset, DatasetVersionCreate(conversation_ids=[]))
    with pytest.raises(SourceError, match="outside"):
        service.create(dataset, DatasetVersionCreate(conversation_ids=[uuid4()]))
    request = DatasetVersionCreate(
        conversation_ids=[c.id for c in conversations.list_conversations(dataset)]
    )

    def fail(*args: object, **kwargs: object) -> None:
        raise StorageError("Synthetic storage failure")

    with monkeypatch.context() as patch:
        patch.setattr(service.datasets.artifacts.store, "put", fail)
        with pytest.raises(StorageError):
            service.create(dataset, request)
    assert imported.canonical_artifact_id
    artifact = service.datasets.artifacts.get(imported.canonical_artifact_id)
    service.datasets.artifacts.store.put(artifact.object_key, BytesIO(b"changed"))
    with pytest.raises(SourceError, match="hash"):
        service.create(dataset, request)
    with service.datasets.factory() as session:
        assert session.scalar(select(func.count()).select_from(DatasetVersion)) == 0


def test_preview_checks_registered_output_hash(service: DatasetVersionService) -> None:
    conversations = ConversationService(service.datasets)
    dataset, source_id, _ = source(conversations)
    conversations.import_source(dataset, source_id)
    version = service.create(
        dataset,
        DatasetVersionCreate(
            conversation_ids=[c.id for c in conversations.list_conversations(dataset)]
        ),
    )
    artifact = service.datasets.artifacts.get(version.manifest.examples.artifact_id)
    service.datasets.artifacts.store.put(artifact.object_key, BytesIO(b"changed"))
    with pytest.raises(SourceError, match="hash"):
        service.preview(dataset, version.id, 0, 20)
