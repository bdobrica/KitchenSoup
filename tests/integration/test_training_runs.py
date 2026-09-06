from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.config import Settings
from app.db.models import TrainingRun
from app.executors import local_docker, materialize
from app.executors.base import ExecutorError
from app.executors.local_docker import LocalDockerTrainingExecutor
from app.executors.materialize import RegisteredInputMaterializer
from app.main import create_app
from app.services.runs import RunCreate, TrainingRunService
from app.services.training import TrainingPlanService
from app.services.versions import DatasetVersionService
from tests.integration.test_training_plans import inputs
from tests.integration.test_training_plans import versions as plan_versions
from tests.unit.test_local_executor import DockerFixture

versions = plan_versions


def setup_service(
    versions: DatasetVersionService, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[TrainingRunService, DockerFixture]:
    backend = DockerFixture()
    monkeypatch.setattr(local_docker, "docker", backend)

    def download(repository: str, revision: str, destination: Path) -> None:
        assert repository == "fixture/model" and revision == "a" * 40
        (destination / "config.json").write_text('{"model_type":"qwen2"}')
        (destination / "tokenizer_config.json").write_text("{}")
        (destination / "model.safetensors").write_bytes(b"synthetic fixture")

    monkeypatch.setattr(materialize, "download_model", download)
    plans = TrainingPlanService(versions.datasets.factory)
    executor = LocalDockerTrainingExecutor(
        tmp_path / "jobs", RegisteredInputMaterializer(versions.datasets.artifacts)
    )
    return TrainingRunService(plans, executor), backend


def test_saved_plan_to_run_api_status_logs_cancel_cleanup_and_restart(
    versions: DatasetVersionService,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spec = inputs(versions)
    service, backend = setup_service(versions, tmp_path, monkeypatch)
    plan = service.plans.create(spec)
    with TestClient(create_app(Settings(_env_file=None), training_run_service=service)) as client:
        response = client.post(
            "/api/v1/training-runs", json={"plan_id": str(plan.id), "image": "untrusted"}
        )
        assert response.status_code == 422
        response = client.post("/api/v1/training-runs", json={"plan_id": str(plan.id)})
        assert response.status_code == 201, response.text
        run = response.json()
        assert run["status"] == "RUNNING" and run["plan_sha256"] == plan.sha256
        identifier = run["id"]
        base = f"/api/v1/training-runs/{identifier}"
        assert client.get(base).headers["cache-control"] == "no-store"
        assert client.get(base + "/logs").json() == {"text": "", "cursor": 0}
        assert client.get(base + "/logs?cursor=-1").status_code == 422
        assert client.delete(base + "/container").status_code == 503
        assert client.post(base + "/cancel").json()["status"] == "CANCELED"
        assert client.delete(base + "/container").status_code == 204
        assert client.get(base).json()["status"] == "CANCELED"
        assert client.get("/training-runs/" + identifier).status_code == 200
        assert len(client.get("/api/v1/training-runs").json()) == 1
    with versions.datasets.factory() as session:
        row = session.scalar(select(TrainingRun))
        assert row is not None and row.external_id == run["external_id"]
        assert row.resolved_config["image_digest"] == run["image_digest"]
        assert row.resolved_config["plan"]["sha256"] == plan.sha256
        # A fresh service uses the persisted identity after process restart.
        restarted = TrainingRunService(service.plans, service.executor)
        assert restarted.get(row.id).status == "CANCELED"
    assert backend.missing


def test_preparation_failure_retains_attempt_and_does_not_launch(
    versions: DatasetVersionService,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spec = inputs(versions)
    service, backend = setup_service(versions, tmp_path, monkeypatch)
    plan = service.plans.create(spec)

    def fail(*args: object) -> None:
        raise RuntimeError("sensitive provider response")

    monkeypatch.setattr(materialize, "download_model", fail)
    run = service.create(RunCreate(plan_id=plan.id))
    assert run.status == "FAILED" and run.external_id
    assert "sensitive" not in run.model_dump_json()
    assert service.get(run.id).status == "FAILED"
    assert not any(c[0] == "create" for c in backend.calls)
    with pytest.raises(ExecutorError):
        RegisteredInputMaterializer(versions.datasets.artifacts).artifact(
            uuid4(), "a" * 64, tmp_path / "missing", 100
        )


def test_uploaded_model_and_exact_dataset_bytes_are_materialized(
    versions: DatasetVersionService,
    model_archive: bytes,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from io import BytesIO

    from app.registry.schemas import ArchiveImport
    from app.services.models import ModelService
    from app.storage.keys import upload_key
    from tests.integration.test_model_registry import Resolver

    spec = inputs(versions)
    artifacts = versions.datasets.artifacts
    grant = artifacts.initiate("model.zip", "application/zip", len(model_archive))
    artifacts.store.put(upload_key(grant.upload_id, "model.zip"), BytesIO(model_archive))
    artifact = artifacts.complete(grant.upload_id)
    models = ModelService(artifacts.factory, artifacts, Resolver())
    model = models.import_archive(
        ArchiveImport(
            name="Synthetic uploaded training model", artifact_id=artifact.id, license="CC0"
        )
    )
    spec.base_model_version_id = model.versions[0].id
    service, backend = setup_service(versions, tmp_path, monkeypatch)
    plan = service.plans.create(spec)
    run = service.create(RunCreate(plan_id=plan.id))
    assert run.status == "RUNNING"
    assert any(c[0] == "create" for c in backend.calls)
    # Mutating object bytes cannot change the registered hash or enter a subsequent run.
    with artifacts.factory() as session:
        from app.db.models import Artifact

        row = session.get(Artifact, plan.resolved.examples_artifact_id)
        assert row is not None
        artifacts.store.put(row.object_key, BytesIO(b"tampered"))
    backend.calls.clear()
    failed = service.create(RunCreate(plan_id=plan.id))
    assert failed.status == "FAILED"
    assert not any(c[0] == "create" for c in backend.calls)
