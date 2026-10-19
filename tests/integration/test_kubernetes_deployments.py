from pathlib import Path
from unittest.mock import Mock

import pytest

from app.config import Settings
from app.deployments.schemas import DeploymentCreate
from app.ingestion.sources import SourceError
from app.services.deployments import DeploymentService
from app.services.targets import TargetService
from app.services.versions import DatasetVersionService
from app.targets.schemas import TargetCreate
from tests.integration.test_deployments import expire, trained
from tests.integration.test_training_runs import versions
from tests.unit.test_kubernetes_deployments import ServingCluster, config

__all__ = ["versions"]


def test_kubernetes_serving_routing_restart_expiry_stop_and_disabled_target(
    versions: DatasetVersionService, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    model = trained(versions, tmp_path, monkeypatch)
    targets = TargetService(
        versions.datasets.artifacts,
        Settings(
            _env_file=None,
            kubernetes_execution_enabled=True,
            kubernetes_staging_workspace=str(tmp_path / "staging"),
        ),
    )
    target = targets.create(
        TargetCreate(
            name="Kubernetes serving",
            kind="kubernetes",
            credential_ref="file:///run/secrets/kubeconfig",
            configuration=config(),
        )
    )
    cluster = ServingCluster()
    monkeypatch.setattr(targets, "kubernetes_transport", lambda _: cluster.transport)
    # Serving model verification is covered separately; this test exercises real
    # executor manifests/identity against API fixtures and PostgreSQL lease authority.
    monkeypatch.setattr(
        "app.deployments.materialize.DeploymentMaterializer.materialize",
        lambda self, spec, path: (path / "config.json").write_text("{}"),
    )
    service = DeploymentService(versions.datasets.artifacts, targets=targets)
    row = service.create(DeploymentCreate(model_version_id=model, execution_target_id=target.id))
    assert row.execution_target_id == target.id and row.status == "QUEUED"
    service.process(row.id, "worker")
    assert service.get(row.id).status == "STARTING"
    cluster.ready = True
    service = DeploymentService(versions.datasets.artifacts, targets=targets)
    service.process(row.id, "reconciler")
    assert service.get(row.id).status == "RUNNING"
    expire(service, row.id)
    cluster.unknown = True
    service.tick("reconciler")
    assert service.get(row.id).status == "STOP_REQUESTED"
    assert cluster.objects
    cluster.unknown = False
    service.process(row.id, "reconciler")
    assert service.get(row.id).status == "STOPPED" and not cluster.objects
    targets.settings.kubernetes_execution_enabled = False
    with pytest.raises(SourceError, match="disabled"):
        service.executor_for(row.id)


def test_kubernetes_target_rejects_serving_without_image(
    versions: DatasetVersionService, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.executors.base import ExecutorError

    model = trained(versions, tmp_path, monkeypatch)
    targets = TargetService(
        versions.datasets.artifacts,
        Settings(
            _env_file=None,
            kubernetes_execution_enabled=True,
            kubernetes_staging_workspace=str(tmp_path),
        ),
    )
    configuration = config()
    configuration.deployment_image = None
    target = targets.create(
        TargetCreate(
            name="Training only",
            kind="kubernetes",
            credential_ref="env://KUBE",
            configuration=configuration,
        )
    )
    transport = Mock()
    transport.config = configuration
    monkeypatch.setattr(targets, "kubernetes_transport", lambda _: transport)
    service = DeploymentService(versions.datasets.artifacts, targets=targets)
    with pytest.raises(ExecutorError, match="pinned serving image"):
        service.create(DeploymentCreate(model_version_id=model, execution_target_id=target.id))
