from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from app.config import Settings
from app.db.models import ModelVersion, TrainingRun
from app.deployments.schemas import ChatRequest, DeploymentCreate
from app.ingestion.sources import SourceError
from app.jobs.processor import JobProcessor
from app.main import create_app
from app.services.deployments import DeploymentService
from app.services.runs import RunCreate, TrainingRunService
from app.services.targets import TargetService
from app.services.versions import DatasetVersionService
from app.targets.schemas import SshConfig, TargetCreate
from tests.integration.test_deployments import Serving, expire
from tests.integration.test_lineage import complete_output
from tests.integration.test_training_plans import inputs
from tests.integration.test_training_runs import setup_service, versions

__all__ = ["versions"]


def target_body() -> TargetCreate:
    return TargetCreate(
        name="SSH fixture",
        credential_ref="file:///run/secrets/gpu",
        configuration=SshConfig(
            host="localhost", username="operator", port=2222, workspace="/tmp/kitchensoup-ssh-test"
        ),
    )


def test_target_api_opt_in_safe_validation_and_immutable_configuration(
    versions: DatasetVersionService,
) -> None:
    artifacts = versions.datasets.artifacts
    disabled = TargetService(artifacts, Settings(_env_file=None))
    with TestClient(create_app(Settings(_env_file=None), target_service=disabled)) as client:
        assert client.get("/api/v1/execution-targets").json() == []
        assert (
            client.post(
                "/api/v1/execution-targets",
                json=target_body().model_dump(mode="json", by_alias=True),
            ).status_code
            == 503
        )
    targets = TargetService(artifacts, Settings(_env_file=None, ssh_execution_enabled=True))
    with TestClient(create_app(Settings(_env_file=None), target_service=targets)) as client:
        api = "/api/v1/execution-targets"
        assert client.get("/settings/execution-targets").status_code == 200
        body = target_body().model_dump(mode="json", by_alias=True)
        response = client.post(api, json=body)
        assert response.status_code == 201, response.text
        assert response.headers["cache-control"] == "no-store"
        identifier = response.json()["id"]
        assert client.get(api + "/" + identifier).json()["configuration"] == body["configuration"]
        assert len(client.get(api).json()) == 1
        assert client.post(api, json=body).status_code == 409
        body["credential_ref"] = "sensitive-inline-key"
        invalid = client.post(api, json=body)
        assert invalid.status_code == 422 and "sensitive-inline-key" not in invalid.text
        assert client.get(api + "/" + str(uuid4())).status_code == 404
    # Guard target identity independently of the application; existing run FKs cannot be redirected.
    for assignment in (
        "name='changed'",
        "kind='local_docker'",
        "credential_ref='env://OTHER'",
        "configuration='{}'::jsonb",
    ):
        with artifacts.factory() as session, pytest.raises(DBAPIError):
            session.execute(
                text(f"UPDATE execution_targets SET {assignment} WHERE id=:id"), {"id": identifier}
            )
            session.commit()
    with artifacts.factory() as session, pytest.raises(DBAPIError):
        session.execute(text("DELETE FROM execution_targets WHERE id=:id"), {"id": identifier})
        session.commit()


def test_same_plan_routes_to_target_registration_restart_clone_and_lease_cleanup(
    versions: DatasetVersionService,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    local, backend = setup_service(versions, tmp_path, monkeypatch)
    targets = TargetService(
        versions.datasets.artifacts, Settings(_env_file=None, ssh_execution_enabled=True)
    )
    target = targets.create(target_body())
    selected = []

    def training(identifier: object) -> object:
        targets.enabled()
        assert identifier == target.id
        selected.append(identifier)
        return local.executor

    # Unit/live SSH tests cover transport; real PostgreSQL/store cover routing here.
    monkeypatch.setattr(targets, "training", training)
    remote = TrainingRunService(
        local.plans, None, artifacts=versions.datasets.artifacts, targets=targets
    )
    plan = local.plans.create(inputs(versions))
    with pytest.raises(SourceError, match="target"):
        remote.create(RunCreate(plan_id=plan.id))
    local_run = local.create(RunCreate(plan_id=plan.id))
    local.cancel(local_run.id)
    run = remote.create(RunCreate(plan_id=plan.id, execution_target_id=target.id))
    assert run.plan_sha256 == local_run.plan_sha256
    assert run.execution_target_id == target.id and run.plan_sha256 == plan.sha256
    restarted = TrainingRunService(
        local.plans, None, artifacts=versions.datasets.artifacts, targets=targets
    )
    processor = JobProcessor(restarted)
    processor.tick("worker")
    assert restarted.get(run.id).status == "SUBMITTED"
    processor.tick("worker")
    assert len([call for call in backend.calls if call[0] == "create"]) == 1
    complete_output(local, backend, run)
    processor.tick("reconciler")
    result = restarted.get(run.id)
    assert result.status == "SUCCEEDED" and result.model_version_id is not None
    with remote.factory() as session:
        version = session.scalar(select(ModelVersion).where(ModelVersion.training_run_id == run.id))
        assert version is not None
        row = session.get(TrainingRun, run.id)
        assert row is not None and row.resolved_config["plan"] == plan.model_dump(
            mode="json", by_alias=True, exclude={"id", "created_at"}
        )
    assert selected
    clone = restarted.clone(run.id)
    assert clone.execution_target_id == target.id and clone.plan_sha256 == run.plan_sha256
    assert restarted.cancel(clone.id).status == "CANCELED"
    # Loss of operator opt-in does not reroute work onto a local daemon.
    targets.settings.ssh_execution_enabled = False
    with pytest.raises(SourceError, match="disabled"):
        restarted.executor_for(run.id)
    targets.settings.ssh_execution_enabled = True

    serving = Serving()
    monkeypatch.setattr(targets, "deployment", lambda identifier: serving)
    deployments = DeploymentService(versions.datasets.artifacts, targets=targets)
    deployment = deployments.create(
        DeploymentCreate(model_version_id=result.model_version_id, execution_target_id=target.id)
    )
    assert deployment.execution_target_id == target.id
    deployments.tick("worker")
    deployments = DeploymentService(versions.datasets.artifacts, targets=targets)
    deployments.tick("reconciler")
    assert deployments.get(deployment.id).status == "RUNNING"
    assert (
        deployments.chat(
            deployment.id,
            ChatRequest.model_validate({"messages": [{"role": "user", "content": "Hi"}]}),
        ).content
        == "<script>inert</script>"
    )
    deployments.action(deployment.id, "keep")
    deployments.action(deployment.id, "extend", 1)
    expire(deployments, deployment.id)
    serving.fail_stop = True
    deployments.tick("reconciler")
    assert deployments.get(deployment.id).status == "STOP_REQUESTED"
    serving.fail_stop = False
    deployments.tick("reconciler")
    assert deployments.get(deployment.id).status == "STOPPED" and serving.stops == [deployment.id]
