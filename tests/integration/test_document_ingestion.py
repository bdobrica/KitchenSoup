import hashlib
import json
from collections.abc import Iterator
from io import BytesIO
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from app.api.documents import document_service
from app.config import Settings
from app.db.models import Artifact, ArtifactDerivation, DocumentIngestion
from app.ingestion.documents import DocumentSelection, SoupResult
from app.ingestion.schemas import DatasetCreate, SourceAttach
from app.ingestion.sources import SourceError
from app.main import create_app
from app.services.artifacts import ArtifactService
from app.services.datasets import DatasetService
from app.services.documents import DocumentIngestionService
from app.storage.s3 import S3ArtifactStore


class FixtureRunner:
    def __init__(self) -> None:
        self.exit_code = 0
        self.calls = 0

    def ingest(self, content: bytes, extension: str) -> SoupResult:
        self.calls += 1
        assert content == b"Synthetic document.\n" and extension == ".txt"
        return SoupResult(
            schema_name="kitchensoup.soup-ingest-response/v1",
            soup_version="0.74.0",
            image_id="sha256:" + "a" * 64,
            exit_code=self.exit_code,
            output='{"text":"Synthetic document.\\n","source":"source.txt"}\n{"text":""}\n',
            stdout="Wrote 2 rows",
            stderr="" if not self.exit_code else "Fixture failure",
            logs_truncated=False,
        )


@pytest.fixture
def service(factory: sessionmaker[Session]) -> Iterator[DocumentIngestionService]:
    store = S3ArtifactStore(Settings(_env_file=None))
    store.provision(["http://localhost:8000"])
    yield DocumentIngestionService(
        DatasetService(factory, ArtifactService(store, factory, bucket=store.bucket)),
        FixtureRunner(),
    )
    store.close()


def attach(service: DocumentIngestionService, dataset: UUID, filename: str = "notes.txt") -> UUID:
    content = b"Synthetic document.\n"
    artifacts = service.datasets.artifacts
    key = f"raw/v1/{uuid4()}/{filename}"
    artifacts.store.put(key, BytesIO(content))
    with service.datasets.factory() as session:
        artifact = Artifact(
            bucket=artifacts.bucket,
            object_key=key,
            sha256=hashlib.sha256(content).hexdigest(),
            size_bytes=len(content),
            format="text/plain",
        )
        session.add(artifact)
        session.commit()
        identifier = artifact.id
    return service.datasets.attach(
        dataset, SourceAttach(artifact_id=identifier, filename=filename)
    ).id


def test_document_ingestion_provenance_replay_and_immutable_history(
    service: DocumentIngestionService,
) -> None:
    dataset = service.datasets.create(DatasetCreate(name="Synthetic ingestion")).id
    source = attach(service, dataset)
    ignored = attach(service, dataset, "data.csv")
    request = DocumentSelection(source_ids=[source, ignored, source])
    first = service.ingest(dataset, request)
    second = service.ingest(dataset, request)
    assert (first.version, second.version) == (1, 2)
    assert first.status == "succeeded" and first.manifest.example_count == 1
    assert first.output_artifact_id != second.output_artifact_id
    assert first.output_artifact_id and second.output_artifact_id
    a = service.datasets.artifacts.get(first.output_artifact_id)
    b = service.datasets.artifacts.get(second.output_artifact_id)
    assert a.sha256 == b.sha256
    assert a.object_key.startswith("derived/documents/v1/")
    assert any(s.disposition == "ignored" for s in first.manifest.sources)
    assert any("empty_text_rows_ignored" in s.warnings for s in first.manifest.sources)
    with service.datasets.artifacts.store.get(a.object_key) as body:
        row = json.loads(body.read())
    assert row["source_id"] == str(source)
    for snapshot in first.manifest.sources:
        raw = service.datasets.artifacts.get(snapshot.artifact_id)
        with service.datasets.artifacts.store.get(raw.object_key) as body:
            assert hashlib.sha256(body.read()).hexdigest() == snapshot.sha256
    with service.datasets.factory() as session:
        assert session.scalar(select(func.count()).select_from(ArtifactDerivation)) == 16
        with pytest.raises(DBAPIError, match="immutable"):
            session.execute(
                text("UPDATE document_ingestions SET version=99 WHERE id=:id"), {"id": first.id}
            )
            session.commit()
    with pytest.raises(SourceError, match="history"):
        service.datasets.remove(dataset, ignored)
    assert len(service.list_ingestions(dataset)) == 2


def test_failed_cli_keeps_logs_and_raw_without_partial_dataset(
    service: DocumentIngestionService,
) -> None:
    assert isinstance(service.runner, FixtureRunner)
    service.runner.exit_code = 1
    dataset = service.datasets.create(DatasetCreate(name="Failed ingestion")).id
    source = attach(service, dataset)
    result = service.ingest(dataset, DocumentSelection(source_ids=[source]))
    assert result.status == "failed" and result.output_artifact_id is None
    assert result.manifest.sources[0].disposition == "failed"
    log = service.datasets.artifacts.get(result.logs_artifact_id)
    with service.datasets.artifacts.store.get(log.object_key) as body:
        assert json.loads(body.read())[0]["exit_code"] == 1
    assert service.datasets.get(dataset).sources[0].documents[0].canonical_artifact_id is None


def test_foreign_source_and_tampering_fail_before_runner(service: DocumentIngestionService) -> None:
    assert isinstance(service.runner, FixtureRunner)
    a = service.datasets.create(DatasetCreate(name="A")).id
    b = service.datasets.create(DatasetCreate(name="B")).id
    source = attach(service, b)
    with pytest.raises(SourceError, match="not found"):
        service.ingest(a, DocumentSelection(source_ids=[source]))
    raw = service.datasets.artifacts.get(service.datasets.get(b).sources[0].artifact_id)
    service.datasets.artifacts.store.put(raw.object_key, BytesIO(b"changed"))
    with pytest.raises(SourceError, match="hash"):
        service.ingest(b, DocumentSelection(source_ids=[source]))
    assert service.runner.calls == 0
    with service.datasets.factory() as session:
        assert session.scalar(select(func.count()).select_from(DocumentIngestion)) == 0


def test_ingestion_http_contract(service: DocumentIngestionService) -> None:
    dataset = service.datasets.create(DatasetCreate(name="API ingestion")).id
    source = attach(service, dataset)
    app = create_app(Settings(_env_file=None), dataset_service=service.datasets)
    app.dependency_overrides[document_service] = lambda: service
    with TestClient(app) as client:
        response = client.post(
            f"/api/v1/datasets/{dataset}/document-ingestions", json={"source_ids": [str(source)]}
        )
        assert response.status_code == 200
        assert response.json()["status"] == "succeeded"
        listing = client.get(f"/api/v1/datasets/{dataset}/document-ingestions")
        assert listing.json()[0]["id"] == response.json()["id"]
