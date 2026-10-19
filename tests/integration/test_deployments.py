from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.config import Settings
from app.deployments.base import DeploymentStatus
from app.deployments.schemas import ChatRequest, ChatResponse, DeploymentCreate, DeploymentSpec
from app.executors.base import ExecutorError
from app.ingestion.sources import SourceError
from app.jobs.locking import claim
from app.jobs.processor import JobProcessor
from app.main import create_app
from app.services.deployments import DeploymentService
from app.services.runs import RunCreate
from app.services.versions import DatasetVersionService
from tests.integration.test_lineage import complete_output
from tests.integration.test_training_plans import inputs
from tests.integration.test_training_runs import setup_service, versions

__all__ = ["versions"]


class Serving:
    def __init__(self) -> None:
        self.starts: list[UUID] = []
        self.stops: list[UUID] = []
        self.observed = DeploymentStatus("RUNNING", "http://127.0.0.1:54321")
        self.fail_stop = False
        self.fail_start = False

    def image_identity(self) -> str:
        return "sha256:" + "b" * 64

    def deploy(self, identifier: UUID, spec: DeploymentSpec) -> None:
        self.starts.append(identifier)
        if self.fail_start:
            raise ExecutorError("Synthetic launch failure")

    def status(self, identifier: UUID, spec: DeploymentSpec) -> DeploymentStatus:
        return self.observed

    def stop(self, identifier: UUID, spec: DeploymentSpec) -> None:
        if self.fail_stop:
            raise ExecutorError("Synthetic daemon outage")
        self.stops.append(identifier)

    def chat(self, identifier: UUID, spec: DeploymentSpec, body: ChatRequest) -> ChatResponse:
        return ChatResponse(content="<script>inert</script>")


def trained(
    versions: DatasetVersionService, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> UUID:
    training, backend = setup_service(versions, tmp_path, monkeypatch)
    plan = training.plans.create(inputs(versions))
    run = training.create(RunCreate(plan_id=plan.id))
    processor = JobProcessor(training)
    processor.tick("worker")
    complete_output(training, backend, run)
    processor.tick("reconciler")
    result = training.get(run.id)
    assert result.model_version_id
    return result.model_version_id


def expire(service: DeploymentService, identifier: UUID) -> None:
    with service.factory() as session:
        row = service.row(session, identifier)
        row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        session.commit()


def test_playground_api_lifecycle_leases_proxy_and_guards(
    versions: DatasetVersionService, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    model = trained(versions, tmp_path, monkeypatch)
    backend = Serving()
    service = DeploymentService(versions.datasets.artifacts, backend)
    with TestClient(create_app(Settings(_env_file=None), deployment_service=service)) as client:
        api = "/api/v1/deployments"
        assert client.get("/deployments").status_code == 200
        assert (
            client.post(api, json={"model_version_id": str(model), "lease_minutes": 0}).status_code
            == 422
        )
        assert client.post(api, json={"model_version_id": str(uuid4())}).status_code == 404
        assert (
            client.post(
                api, json={"model_version_id": str(model), "artifact_id": str(uuid4())}
            ).status_code
            == 422
        )
        response = client.post(api, json={"model_version_id": str(model)})
        assert response.status_code == 202 and response.headers["cache-control"] == "no-store"
        row = response.json()
        identifier = UUID(row["id"])
        assert row["status"] == "QUEUED" and backend.starts == [] and "endpoint" not in row
        assert (
            client.post(
                f"{api}/{identifier}/chat/completions",
                json={"messages": [{"role": "user", "content": "Hi"}]},
            ).status_code
            == 409
        )
        with claim(service.factory, identifier) as acquired:
            assert acquired
            service.tick("worker")
        assert backend.starts == []
        service.tick("worker")
        service.tick("worker")
        assert backend.starts == [identifier] and service.get(identifier).status == "STARTING"
        # Recreate service to prove durable reconciliation independent of web memory.
        service = DeploymentService(versions.datasets.artifacts, backend)
        service.tick("reconciler")
        assert service.get(identifier).status == "RUNNING"
        assert (
            client.post(
                f"{api}/{identifier}/chat/completions",
                json={"messages": [{"role": "user", "content": "Hi"}]},
            ).json()["content"]
            == "<script>inert</script>"
        )
        assert (
            client.post(
                f"{api}/{identifier}/chat/completions",
                json={"messages": [{"role": "tool", "content": "Hi"}]},
            ).status_code
            == 422
        )
        old = service.get(identifier).expires_at
        assert client.post(f"{api}/{identifier}/extend", json={"minutes": 30}).status_code == 200
        assert service.get(identifier).expires_at > old
        assert client.post(f"{api}/{identifier}/keep").json()["keep_running"]
        expire(service, identifier)
        service.tick("reconciler")
        assert service.get(identifier).status == "RUNNING"
        assert (
            client.post(f"{api}/{identifier}/extend", json={"minutes": 30}).json()["keep_running"]
            is False
        )
        expire(service, identifier)
        assert client.post(f"{api}/{identifier}/keep").status_code == 409
        assert (
            client.post(
                f"{api}/{identifier}/chat/completions",
                json={"messages": [{"role": "user", "content": "Hi"}]},
            ).status_code
            == 409
        )
        backend.fail_stop = True
        service.tick("reconciler")
        assert service.get(identifier).status == "STOP_REQUESTED"
        backend.fail_stop = False
        service.tick("reconciler")
        assert service.get(identifier).status == "STOPPED" and backend.stops == [identifier]
        assert client.post(f"{api}/{identifier}/stop").status_code == 202
        assert client.post(f"{api}/{identifier}/extend", json={"minutes": 30}).status_code == 409
        assert len(client.get(api).json()) == 1
    for sql in (
        "UPDATE deployments SET snapshot='{}' WHERE id=:id",
        "UPDATE deployments SET status='RUNNING' WHERE id=:id",
    ):
        with service.factory() as session, pytest.raises(DBAPIError):
            session.execute(text(sql), {"id": identifier})
            session.commit()


def test_stop_recovery_start_failure_and_missing_container(
    versions: DatasetVersionService, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    model = trained(versions, tmp_path, monkeypatch)
    backend = Serving()
    service = DeploymentService(versions.datasets.artifacts, backend)
    row = service.create(DeploymentCreate(model_version_id=model))
    service.action(row.id, "stop")
    service.tick("reconciler")
    assert service.get(row.id).status == "STOPPED" and not backend.starts
    row = service.create(DeploymentCreate(model_version_id=model))
    backend.fail_start = True
    service.tick("worker")
    assert service.get(row.id).status == "FAILED"
    backend.fail_start = False
    row = service.create(DeploymentCreate(model_version_id=model))
    with service.factory() as session:
        item = service.row(session, row.id)
        item.status = "PREPARING"
        session.commit()
    backend.observed = DeploymentStatus("LOST")
    service.tick("worker")
    assert service.get(row.id).status == "FAILED" and row.id not in backend.starts
    row = service.create(DeploymentCreate(model_version_id=model))
    service.tick("worker")
    service.tick("reconciler")
    assert service.get(row.id).status == "LOST"
    row = service.create(DeploymentCreate(model_version_id=model))
    with service.factory() as session:
        item = service.row(session, row.id)
        item.status = "PREPARING"
        session.commit()
    backend.observed = DeploymentStatus("RUNNING", "http://127.0.0.1:54321")
    service.tick("worker")
    assert service.get(row.id).status == "RUNNING" and row.id not in backend.starts
    with pytest.raises(SourceError, match="disabled"):
        DeploymentService(versions.datasets.artifacts).create(
            DeploymentCreate(model_version_id=model)
        )


def test_uploaded_full_model_and_hub_only_base_selection(
    versions: DatasetVersionService, model_archive: bytes
) -> None:
    from io import BytesIO

    from app.registry.schemas import ArchiveImport
    from app.services.models import ModelService
    from app.storage.keys import upload_key
    from tests.integration.test_model_registry import Resolver

    artifacts = versions.datasets.artifacts
    service = DeploymentService(artifacts, Serving())
    hub = inputs(versions).base_model_version_id
    with pytest.raises(SourceError, match="full model archive"):
        service.create(DeploymentCreate(model_version_id=hub))
    grant = artifacts.initiate("model.zip", "application/zip", len(model_archive))
    artifacts.store.put(upload_key(grant.upload_id, "model.zip"), BytesIO(model_archive))
    registered = artifacts.complete(grant.upload_id)
    model = ModelService(artifacts.factory, artifacts, Resolver()).import_archive(
        ArchiveImport(name="Serving base", artifact_id=registered.id, license="CC0")
    )
    row = service.create(DeploymentCreate(model_version_id=model.versions[0].id))
    assert row.artifact_id == registered.id and row.precision == "fp16"
    with service.factory() as session:
        snapshot = DeploymentSpec.model_validate(service.row(session, row.id).snapshot)
    assert not snapshot.adapter and snapshot.base_source.artifact_sha256 == registered.sha256
    assert snapshot.artifact_sha256 == registered.sha256
