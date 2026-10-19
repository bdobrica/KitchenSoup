import shutil
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.config import Settings
from app.ingestion.sources import SourceError
from app.jobs.processor import JobProcessor
from app.main import create_app
from app.services.runs import RunCreate, TrainingRunService
from app.services.targets import TargetService
from app.services.versions import DatasetVersionService
from app.targets.schemas import TargetCreate
from tests.integration.test_lineage import complete_output
from tests.integration.test_training_plans import inputs
from tests.integration.test_training_runs import setup_service, versions
from tests.unit.test_kubernetes import Cluster, configuration

__all__ = ["versions"]


def body() -> TargetCreate:
    return TargetCreate(
        name="Kubernetes fixture",
        kind="kubernetes",
        credential_ref="file:///run/secrets/config",
        configuration=configuration(),
    )


def test_kubernetes_target_api_opt_in_and_sql_immutability(versions: DatasetVersionService) -> None:
    artifacts = versions.datasets.artifacts
    targets = TargetService(artifacts, Settings(_env_file=None))
    with TestClient(create_app(Settings(_env_file=None), target_service=targets)) as client:
        api = "/api/v1/execution-targets"
        payload = body().model_dump(mode="json", by_alias=True)
        assert client.post(api, json=payload).status_code == 503
        targets.settings.kubernetes_execution_enabled = True
        response = client.post(api, json=payload)
        assert response.status_code == 201, response.text
        identifier = response.json()["id"]
        assert response.json()["configuration"] == payload["configuration"]
        assert response.headers["cache-control"] == "no-store"
        assert client.get(api).json()[0]["kind"] == "kubernetes"
        assert client.get(api + "/" + identifier).json() == response.json()
        assert client.post(api, json=payload).status_code == 409
        payload["kind"] = "ssh_docker"
        assert client.post(api, json=payload).status_code == 422
    for assignment in (
        "kind='local_docker'",
        "configuration='{}'::jsonb",
        "credential_ref='env://OTHER'",
        "name='changed'",
    ):
        with artifacts.factory() as session, pytest.raises(DBAPIError):
            session.execute(
                text(f"UPDATE execution_targets SET {assignment} WHERE id=:id"), {"id": identifier}
            )
            session.commit()
    with artifacts.factory() as session, pytest.raises(DBAPIError):
        session.execute(text("DELETE FROM execution_targets WHERE id=:id"), {"id": identifier})
        session.commit()
    with artifacts.factory() as session, pytest.raises(DBAPIError):
        session.execute(
            text(
                "INSERT INTO execution_targets (id,name,kind,configuration) "
                "VALUES (:id,'invalid','kubernetes','{}'::jsonb)"
            ),
            {"id": uuid4()},
        )
        session.commit()


def test_kubernetes_job_to_registry_restart_clone_cancel_and_no_local_fallback(
    versions: DatasetVersionService,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    local, backend = setup_service(versions, tmp_path, monkeypatch)
    targets = TargetService(
        versions.datasets.artifacts,
        Settings(
            _env_file=None,
            kubernetes_execution_enabled=True,
            kubernetes_staging_workspace=str(tmp_path / "staging"),
        ),
    )
    target = targets.create(body())
    cluster = Cluster(tmp_path / "remote")
    monkeypatch.setattr(targets, "kubernetes_transport", lambda _: cluster.transport)
    service = TrainingRunService(
        local.plans, None, artifacts=versions.datasets.artifacts, targets=targets
    )
    plan = local.plans.create(inputs(versions))
    run = service.create(RunCreate(plan_id=plan.id, execution_target_id=target.id))
    JobProcessor(service).tick("worker")
    assert service.get(run.id).status == "SUBMITTED"
    assert not backend.calls  # A selected Kubernetes target never touches Docker.
    restarted = TrainingRunService(
        local.plans, None, artifacts=versions.datasets.artifacts, targets=targets
    )
    executor = restarted.executor_for(run.id)
    output = complete_output(TrainingRunService(local.plans, executor), backend, run)
    shutil.copytree(output, cluster.root / "output", dirs_exist_ok=True)
    shutil.rmtree(output / "adapter")
    cluster.exit_code = 0
    JobProcessor(restarted).tick("reconciler")
    result = restarted.get(run.id)
    assert result.status == "SUCCEEDED" and result.model_version_id is not None
    assert result.plan_sha256 == plan.sha256 and result.execution_target_id == target.id
    restarted.cleanup(run.id)
    assert not cluster.objects
    clone = restarted.clone(run.id)
    assert clone.execution_target_id == target.id and clone.image_digest == run.image_digest
    assert restarted.cancel(clone.id).status == "CANCELED"
    targets.settings.kubernetes_execution_enabled = False
    with pytest.raises(SourceError, match="disabled"):
        restarted.executor_for(run.id)
