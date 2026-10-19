import json
from datetime import timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.deployments.runpod import RunpodDeploymentExecutor
from app.deployments.schemas import ChatRequest, DeploymentCreate, DeploymentSpec
from app.executors.base import ExecutorError
from app.ingestion.sources import SourceError
from app.main import create_app
from app.services.deployments import DeploymentService
from app.services.targets import TargetService
from app.services.versions import DatasetVersionService
from app.targets.schemas import TargetCreate
from tests.integration.test_deployments import expire, trained
from tests.integration.test_runpod_targets import Provider, Remote
from tests.integration.test_training_runs import versions
from tests.unit.test_runpod import configuration

__all__ = ["versions"]


def setup_serving(
    versions: DatasetVersionService, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[DeploymentService, Provider, Remote, UUID, UUID]:
    model = trained(versions, tmp_path, monkeypatch)
    provider, remote = Provider(), Remote(tmp_path / "remote")
    monkeypatch.setattr("app.services.targets.RunpodClient", lambda *args: provider)
    monkeypatch.setattr(
        RunpodDeploymentExecutor,
        "connect_pod",
        lambda self, pod: setattr(self, "transport", remote),
    )

    def materialize(self: Any, spec: Any, path: Path) -> None:
        (path / "model").mkdir()
        (path / "model/config.json").write_text('{"model_type":"qwen2"}')
        (path / "model/model.safetensors").write_bytes(b"fixture")

    monkeypatch.setattr(
        "app.deployments.materialize.DeploymentMaterializer.materialize", materialize
    )
    monkeypatch.setattr(
        RunpodDeploymentExecutor,
        "request_owned",
        lambda self, *args, **kwargs: b'{"data":[{"id":"playground"},{"id":"tuned"}]}',
    )
    targets = TargetService(
        versions.datasets.artifacts,
        Settings(
            _env_file=None,
            runpod_execution_enabled=True,
            runpod_staging_workspace=str(tmp_path / "stage"),
        ),
    )
    config = configuration(
        deployment_image="quay.io/example/vllm@sha256:" + "b" * 64, max_deployment_lease_minutes=15
    )
    target = targets.create(
        TargetCreate(
            name="Runpod serving fixture",
            kind="runpod_pod",
            credential_ref="env://RUNPOD_API_KEY",
            configuration=config,
        )
    )
    service = DeploymentService(versions.datasets.artifacts, targets=targets)
    return service, provider, remote, model, target.id


def create(service: DeploymentService, model: UUID, target: UUID) -> UUID:
    return service.create(
        DeploymentCreate(model_version_id=model, execution_target_id=target, lease_minutes=5)
    ).id


def test_runpod_serving_identity_leases_restart_and_stop(
    versions: DatasetVersionService, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, provider, remote, model, target = setup_serving(versions, tmp_path, monkeypatch)
    identifier = create(service, model, target)
    row = service.get(identifier)
    assert row.bounded_lease and row.maximum_lease_minutes == 15
    service.tick("worker")
    assert service.get(identifier).status == "STARTING"
    service.tick("reconciler")
    assert service.get(identifier).status == "RUNNING"
    assert provider.creates == 1 and len(remote.launches) == 1
    executor = service.executor_for(identifier)
    assert isinstance(executor, RunpodDeploymentExecutor)
    resource = executor.lifecycle.resource(identifier)
    assert resource and resource.kind == "deployment"
    assert resource.request["image"].startswith("quay.io/example/vllm@")
    assert resource.request["ports"] == ["22/tcp", "8000/http"]
    assert "inference.key" not in json.dumps(resource.request)
    with pytest.raises(SourceError, match="bounded lease"):
        service.action(identifier, "keep")
    extended = service.action(identifier, "extend", 1000)
    assert extended.expires_at == extended.created_at + timedelta(minutes=15)
    with pytest.raises(SourceError, match="Lease exceeds"):
        service.create(
            DeploymentCreate(model_version_id=model, execution_target_id=target, lease_minutes=16)
        )
    # Saved endpoint corruption never influences request authority.
    with service.factory() as session:
        service.row(session, identifier).endpoint = "http://169.254.169.254/metadata"
        session.commit()
    with service.factory() as session:
        spec = DeploymentSpec.model_validate(service.row(session, identifier).snapshot)
    assert executor.endpoint_for(identifier, spec) == "https://testpod-8000.proxy.runpod.net"
    assert executor.workspace(identifier).joinpath("inference.key").stat().st_mode & 0o077 == 0
    provider.refuse_cleanup = True
    service.action(identifier, "stop")
    service.tick("reconciler")
    assert service.get(identifier).status == "STOP_REQUESTED"
    assert service.get(identifier).resource_cleanup == "PENDING"
    provider.refuse_cleanup = False
    restarted = DeploymentService(service.artifacts, targets=service.targets)
    restarted.tick("reconciler")
    assert restarted.get(identifier).status == "STOPPED"
    assert restarted.get(identifier).resource_cleanup == "CONFIRMED"
    assert not provider.inventory and provider.creates == 1
    assert not executor.workspace(identifier).exists()
    retained = json.loads((executor.root / "records" / (str(identifier) + ".json")).read_text())
    assert retained["snapshot"]["artifact_id"] == str(row.artifact_id)
    assert retained["max_model_len"] == 2048
    assert restarted.logs(identifier) == "Waiting for vLLM startup output"


@pytest.mark.parametrize(
    "failure", ["ambiguous", "transfer", "expired", "missing", "lost-start-response"]
)
def test_failures_never_reallocate_and_confirm_cleanup(
    versions: DatasetVersionService, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    service, provider, remote, model, target = setup_serving(versions, tmp_path, monkeypatch)
    identifier = create(service, model, target)
    provider.ambiguous = failure == "ambiguous"
    remote.fail_transfer = failure == "transfer"
    if failure == "lost-start-response":
        update = service.update

        def crash(owner: UUID, status: str, endpoint: str | None) -> None:
            if status == "STARTING":
                raise SystemExit("worker death")
            update(owner, status, endpoint)

        monkeypatch.setattr(service, "update", crash)
        with pytest.raises(SystemExit):
            service.tick("worker")
        assert service.get(identifier).status == "PREPARING"
        DeploymentService(service.artifacts, targets=service.targets).tick("worker")
        assert service.get(identifier).status == "RUNNING"
    else:
        service.tick("worker")
    if failure == "expired":
        expire(service, identifier)
    elif failure == "missing":
        provider.inventory.clear()
    service.tick("reconciler")
    service.action(identifier, "stop")
    service.tick("worker")
    assert service.get(identifier).status in ("FAILED", "LOST", "STOPPED")
    assert service.get(identifier).resource_cleanup == "CONFIRMED"
    assert not provider.inventory and provider.creates == 1


def test_streaming_proxy_hides_credentials_and_releases_claim_on_disconnect(
    versions: DatasetVersionService, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, provider, _, model, target = setup_serving(versions, tmp_path, monkeypatch)
    identifier = create(service, model, target)
    service.tick("worker")
    service.tick("reconciler")
    closed: list[bool] = []

    def chunks(*args: Any) -> Any:
        try:
            yield "hello"
            yield " world"
        finally:
            closed.append(True)

    monkeypatch.setattr(RunpodDeploymentExecutor, "stream_chat", chunks)
    body = ChatRequest(messages=[{"role": "user", "content": "synthetic"}])
    source = service.stream_chat(identifier, body)
    assert b"hello" in next(source)
    source.close()
    assert closed == [True]
    with TestClient(create_app(Settings(_env_file=None), deployment_service=service)) as client:
        response = client.post(
            f"/api/v1/deployments/{identifier}/chat/completions",
            json={"messages": [{"role": "user", "content": "synthetic"}], "stream": True},
        )
        assert (
            response.status_code == 200 and "text/event-stream" in response.headers["content-type"]
        )
        assert "hello" in response.text and "[DONE]" in response.text
        view = client.get(f"/api/v1/deployments/{identifier}").text
        assert "proxy.runpod.net" not in view and "Bearer" not in view
        assert "inference.key" not in view and "RUNPOD_API_KEY" not in view

    def fail(*args: Any) -> Any:
        raise ExecutorError("do not expose backend details")
        yield "unreachable"

    monkeypatch.setattr(RunpodDeploymentExecutor, "stream_chat", fail)
    result = b"".join(service.stream_chat(identifier, body))
    assert b"event: error" in result and b"backend details" not in result
    assert service.get(identifier).status == "STOP_REQUESTED"
    service.tick("reconciler")
    assert not provider.inventory


def test_uncertain_absent_allocation_remains_reserved_after_restart(
    versions: DatasetVersionService, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, provider, _, model, target = setup_serving(versions, tmp_path, monkeypatch)
    identifier = create(service, model, target)
    provider.ambiguous = provider.omit = True
    service.tick("worker")
    restarted = DeploymentService(service.artifacts, targets=service.targets)
    restarted.tick("worker")
    restarted.action(identifier, "stop")
    restarted.tick("reconciler")
    assert provider.creates == 1 and not provider.inventory
    assert restarted.get(identifier).status == "STOP_REQUESTED"
    assert restarted.get(identifier).resource_cleanup == "PENDING"
    executor = restarted.executor_for(identifier)
    assert isinstance(executor, RunpodDeploymentExecutor)
    # Uncertain serving capacity is shared with training, including after restart.
    assert restarted.targets is not None
    training = restarted.targets.training(target)
    with pytest.raises(ExecutorError, match="capacity"):
        training.lifecycle.reserve(UUID("00000000-0000-0000-0000-000000000001"))  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    "failure", ["precision", "bf16", "transfer-stop", "unprotected", "endpoint-failure"]
)
def test_preflight_stop_and_unrecoverable_endpoint_failure_terminate(
    versions: DatasetVersionService, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    service, provider, remote, model, target = setup_serving(versions, tmp_path, monkeypatch)
    identifier = service.create(
        DeploymentCreate(
            model_version_id=model,
            execution_target_id=target,
            lease_minutes=5,
            precision="bf16" if failure == "bf16" else "fp16",
        )
    ).id
    if failure in ("precision", "bf16"):
        # Fail actual VRAM despite a compatible provider catalog.
        command = remote.command

        def probe(args: list[str]) -> Any:
            result = command(args)
            if args[-1] == "probe":
                result.stdout = json.dumps(
                    {
                        "cuda": True,
                        "count": 1,
                        "vram_gb": 1 if failure == "precision" else 16,
                        "bf16": False,
                    }
                )
            return result

        monkeypatch.setattr(remote, "command", probe)
    elif failure == "transfer-stop":
        upload = remote.upload

        def cancel(source: Path, destination: str) -> None:
            upload(source, destination)
            service.action(identifier, "stop")

        monkeypatch.setattr(remote, "upload", cancel)
    service.tick("worker")
    if failure == "unprotected":
        from app.deployments.runpod import UnprotectedEndpoint

        def fail(*args: Any, **kw: Any) -> bytes:
            raise UnprotectedEndpoint("authentication missing")

        monkeypatch.setattr(RunpodDeploymentExecutor, "request_owned", fail)
    service.tick("reconciler")
    if failure == "endpoint-failure":
        assert service.get(identifier).status == "RUNNING"

        def unavailable(*args: Any, **kw: Any) -> bytes:
            raise ExecutorError("upstream timeout")

        monkeypatch.setattr(RunpodDeploymentExecutor, "request_owned", unavailable)
        service.tick("reconciler")
    assert service.get(identifier).status in ("FAILED", "STOPPED")
    assert service.get(identifier).resource_cleanup == "CONFIRMED"
    assert not provider.inventory
    if failure in ("precision", "bf16", "transfer-stop"):
        assert not remote.launches


def test_terminal_deployment_cleanup_is_reconciled_after_restart(
    versions: DatasetVersionService, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, provider, _, model, target = setup_serving(versions, tmp_path, monkeypatch)
    identifier = create(service, model, target)
    service.tick("worker")
    service.update(identifier, "FAILED", None)
    provider.refuse_cleanup = True
    service.tick("reconciler")
    assert service.get(identifier).status == "FAILED"
    assert service.get(identifier).resource_cleanup == "PENDING"
    provider.refuse_cleanup = False
    DeploymentService(service.artifacts, targets=service.targets).tick("reconciler")
    assert service.get(identifier).status == "FAILED"
    assert service.get(identifier).resource_cleanup == "CONFIRMED"
    assert not provider.inventory and provider.creates == 1


def test_model_loading_proxy_errors_do_not_terminate_starting_pod(
    versions: DatasetVersionService, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, provider, _, model, target = setup_serving(versions, tmp_path, monkeypatch)
    identifier = create(service, model, target)
    healthy = RunpodDeploymentExecutor.request_owned

    def warming(*args: Any, **kwargs: Any) -> bytes:
        raise ExecutorError("Inference authentication readiness unavailable")

    monkeypatch.setattr(RunpodDeploymentExecutor, "request_owned", warming)
    service.tick("worker")
    service.tick("reconciler")
    assert service.get(identifier).status == "STARTING"
    assert provider.inventory and provider.creates == 1
    monkeypatch.setattr(RunpodDeploymentExecutor, "request_owned", healthy)
    service.tick("reconciler")
    assert service.get(identifier).status == "RUNNING"
    service.action(identifier, "stop")
    service.tick("reconciler")
    assert service.get(identifier).resource_cleanup == "CONFIRMED"
    assert not provider.inventory
