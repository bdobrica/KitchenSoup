from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.deployments.schemas import DeploymentCreate
from app.ingestion.sources import SourceError
from app.main import create_app
from app.services.deployments import DeploymentService
from app.services.targets import TargetService
from app.services.versions import DatasetVersionService
from app.targets.schemas import TargetCreate
from tests.integration.test_deployments import expire, trained
from tests.integration.test_training_runs import versions
from tests.unit.test_sagemaker_deployments import executor_for

__all__ = ["versions"]


@pytest.mark.parametrize("end", ["stop", "expire", "restart"])
def test_managed_playground_leases_and_restart_cleanup(
    versions: DatasetVersionService, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, end: str
) -> None:
    model = trained(versions, tmp_path, monkeypatch)
    executor, provider = executor_for(tmp_path, monkeypatch)
    artifacts = versions.datasets.artifacts
    targets = TargetService(
        artifacts,
        Settings(
            _env_file=None,
            sagemaker_execution_enabled=True,
            aws_approved_profiles="default",
            sagemaker_staging_workspace=str(tmp_path / "managed"),
        ),
    )
    target = targets.create(
        TargetCreate(
            name="AWS serving " + uuid4().hex,
            kind="sagemaker",
            credential_ref="aws-profile://default",
            configuration=executor.configuration,
        )
    )
    monkeypatch.setattr(
        "app.deployments.materialize.DeploymentMaterializer.materialize",
        lambda self, spec, path: executor.materializer.materialize(spec, path),
    )
    service = DeploymentService(artifacts, targets=targets)
    identifier = service.create(
        DeploymentCreate(model_version_id=model, execution_target_id=target.id, lease_minutes=5)
    ).id
    assert service.get(identifier).bounded_lease
    with pytest.raises(SourceError, match="bounded lease"):
        service.action(identifier, "keep")
    row = service.action(identifier, "extend", 1440)
    assert row.expires_at == row.created_at + timedelta(minutes=30)
    with pytest.raises(SourceError, match="Lease exceeds"):
        service.create(
            DeploymentCreate(
                model_version_id=model, execution_target_id=target.id, lease_minutes=31
            )
        )
    if end == "restart":
        original = service.update

        def crash(*args: object) -> None:
            raise SystemExit("worker death")

        monkeypatch.setattr(service, "update", crash)
        with pytest.raises(SystemExit):
            service.process(identifier, "worker")
        assert service.get(identifier).status == "PREPARING"
        monkeypatch.setattr(service, "update", original)
        provider.state = "InService"
        service = DeploymentService(artifacts, targets=targets)
        service.process(identifier, "worker")
    else:
        service.process(identifier, "worker")
        assert service.get(identifier).status == "STARTING"
        provider.state = "InService"
        service.process(identifier, "reconciler")
    assert service.get(identifier).status == "RUNNING"
    with service.factory() as session:
        service.row(session, identifier).endpoint = "http://169.254.169.254/"
        session.commit()
    with TestClient(create_app(Settings(_env_file=None), deployment_service=service)) as client:
        view = client.get(f"/api/v1/deployments/{identifier}")
        assert view.status_code == 200
        assert "169.254" not in view.text and "EndpointArn" not in view.text
    if end == "expire":
        expire(service, identifier)
    else:
        service.action(identifier, "stop")
    provider.unknown = True
    service.tick("reconciler")
    assert service.get(identifier).status == "STOP_REQUESTED"
    provider.unknown = False
    DeploymentService(artifacts, targets=targets).tick("reconciler")
    assert service.get(identifier).status == "STOPPED"
    assert not provider.objects and provider.creates == ["model", "endpoint_config", "endpoint"]
