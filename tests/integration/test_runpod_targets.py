import copy
import json
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from app.config import Settings
from app.db.models import RunpodResource
from app.executors.base import ExecutorError, JobStatus
from app.executors.runpod import RunpodTrainingExecutor
from app.ingestion.sources import SourceError
from app.jobs.processor import JobProcessor
from app.jobs.states import JobState
from app.main import create_app
from app.services.runs import RunCreate, TrainingRunService
from app.services.targets import TargetService
from app.services.versions import DatasetVersionService
from app.targets.runpod import AllocationRejected, RunpodClient
from app.targets.schemas import TargetCreate
from tests.integration.test_lineage import complete_output
from tests.integration.test_training_plans import inputs
from tests.integration.test_training_runs import setup_service, versions
from tests.unit.test_local_executor import IMAGE
from tests.unit.test_runpod import configuration

__all__ = ["versions"]


class Provider:
    pod_id = staticmethod(RunpodClient.pod_id)

    def __init__(self) -> None:
        self.inventory: dict[str, dict[str, Any]] = {}
        self.creates = 0
        self.ambiguous = False
        self.unavailable = False
        self.refuse_cleanup = False
        self.omit = False
        self.rejected = False
        self.wrong_gpu = False

    def quote(self, config: Any) -> float:
        return 0.19

    def request(self, method: str, path: str, body: Any = None) -> dict[str, Any]:
        assert method == "POST" and path == "/pods"
        self.creates += 1
        if self.rejected:
            raise AllocationRejected("request rejected")
        pod = {
            **copy.deepcopy(body),
            "id": "testpod",
            "status": "RUNNING",
            "cost": 0.19,
            "ssh": {"direct": {"host": "8.8.8.8", "port": 2222}},
        }
        if self.wrong_gpu:
            pod["gpu"]["id"] = "unapproved GPU"
        if not self.omit:
            self.inventory[pod["id"]] = pod
        if self.ambiguous:
            raise ExecutorError("uncertain response")
        return pod

    def pods(self) -> list[dict[str, Any]]:
        if self.unavailable:
            raise ExecutorError("unavailable")
        return list(self.inventory.values())

    def get(self, identifier: str) -> dict[str, Any] | None:
        if self.unavailable:
            raise ExecutorError("unavailable")
        return self.inventory.get(identifier)

    def terminate(self, identifier: str) -> None:
        if self.refuse_cleanup:
            raise ExecutorError("cleanup unavailable")
        self.inventory.pop(identifier, None)


class Remote:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.launches: list[list[str]] = []
        self.fail_transfer = False

    def command(self, args: list[str]) -> subprocess.CompletedProcess[str]:
        if args[-1] == "probe":
            return subprocess.CompletedProcess(
                args,
                0,
                json.dumps(
                    {
                        "cuda": True,
                        "count": 1,
                        "vram_gb": 16,
                        "bf16": True,
                    }
                ),
                "",
            )
        assert args[2] == "launch"
        self.launches.append(args)
        (self.root / "started").write_text("started")
        return subprocess.CompletedProcess(args, 0, "", "")

    def identity(self) -> tuple[int, int]:
        return 10001, 10001

    def upload(self, source: Path, destination: str) -> None:
        if self.fail_transfer:
            raise ExecutorError("transfer unavailable")
        shutil.copytree(source, self.root, dirs_exist_ok=True)

    def read(self, path: str, limit: int, offset: int = 0, *, missing_ok: bool = False) -> bytes:
        suffix = path.split("/jobs/", 1)[1].split("/", 1)[1]
        source = self.root / suffix
        if missing_ok and not source.exists():
            return b""
        return source.read_bytes()[offset : offset + limit + 1]

    def download(self, path: str, destination: Path, size: int) -> None:
        data = self.read(path, size)
        assert len(data) == size
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)


def setup_runpod(
    versions: DatasetVersionService,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    **overrides: Any,
) -> tuple[TrainingRunService, TargetService, Provider, Remote]:
    local, _ = setup_service(versions, tmp_path, monkeypatch)
    provider, remote = Provider(), Remote(tmp_path / "remote")
    monkeypatch.setattr("app.services.targets.RunpodClient", lambda *args: provider)
    monkeypatch.setattr(
        RunpodTrainingExecutor, "connect_pod", lambda self, pod: setattr(self, "transport", remote)
    )
    targets = TargetService(
        versions.datasets.artifacts,
        Settings(
            _env_file=None,
            runpod_execution_enabled=True,
            runpod_staging_workspace=str(tmp_path / "stage"),
        ),
    )
    target = targets.create(
        TargetCreate(
            name="Runpod fixture",
            kind="runpod_pod",
            credential_ref="env://RUNPOD_API_KEY",
            configuration=configuration(**overrides),
        )
    )
    service = TrainingRunService(
        local.plans, None, artifacts=versions.datasets.artifacts, targets=targets
    )
    service.create(
        RunCreate(plan_id=local.plans.create(inputs(versions)).id, execution_target_id=target.id)
    )
    return service, targets, provider, remote


def test_api_opt_in_sql_immutability_and_read_only_probe(
    versions: DatasetVersionService,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, targets, provider, _ = setup_runpod(versions, tmp_path, monkeypatch)
    run = service.list_runs()[0]
    assert run.execution_target_id is not None
    with TestClient(create_app(Settings(_env_file=None), target_service=targets)) as client:
        path = f"/api/v1/execution-targets/{run.execution_target_id}"
        response = client.post(path + "/test")
        assert response.status_code == 200, response.text
        assert response.json()["hourly_price_usd"] == 0.19 and provider.creates == 0
        assert client.get(path).json()["kind"] == "runpod_pod"
    with targets.factory() as session, pytest.raises(DBAPIError):
        session.execute(
            text("UPDATE execution_targets SET configuration='{}' WHERE id=:id"),
            {"id": run.execution_target_id},
        )
        session.commit()
    with pytest.raises(SourceError, match="Milestone 26"):
        targets.deployment(run.execution_target_id)
    targets.settings.runpod_execution_enabled = False
    with pytest.raises(SourceError, match="disabled"):
        service.executor_for(run.id)


def test_training_output_registration_and_terminal_cleanup_retry(
    versions: DatasetVersionService,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, targets, provider, remote = setup_runpod(versions, tmp_path, monkeypatch)
    run = service.list_runs()[0]
    JobProcessor(service).tick("worker")
    assert service.get(run.id).status == "SUBMITTED"
    assert provider.creates == 1 and len(remote.launches) == 1
    executor = service.executor_for(run.id)
    assert isinstance(executor, RunpodTrainingExecutor)
    output = complete_output(TrainingRunService(service.plans, executor), MagicMock(), run)
    shutil.copytree(output, remote.root / "output", dirs_exist_ok=True)
    (remote.root / "exit.json").write_text('{"exit_code":0}')
    provider.refuse_cleanup = True
    JobProcessor(service).tick("reconciler")
    result = service.get(run.id)
    assert result.status == "SUCCEEDED" and result.model_version_id is not None
    assert result.resource_cleanup == "PENDING" and result.resource_warning
    assert executor.lifecycle.resource(run.id).outcome["state"] == "SUCCEEDED"  # type: ignore[union-attr,index]
    provider.refuse_cleanup = False
    restarted = TrainingRunService(
        service.plans, None, artifacts=service.artifacts, targets=targets
    )
    JobProcessor(restarted).tick("reconciler")
    assert restarted.get(run.id).resource_cleanup == "CONFIRMED"
    assert not provider.inventory and provider.creates == 1
    with executor.output(run.external_id, IMAGE) as archive:
        assert archive.read(2) == b"PK"


@pytest.mark.parametrize("uncertain,omit", [(True, False), (True, True)])
def test_uncertain_allocation_never_retries_and_cleans_verified_match(
    versions: DatasetVersionService,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    uncertain: bool,
    omit: bool,
) -> None:
    service, _, provider, _ = setup_runpod(versions, tmp_path, monkeypatch)
    provider.ambiguous, provider.omit = uncertain, omit
    run = service.list_runs()[0]
    JobProcessor(service).tick("worker")
    JobProcessor(service).tick("worker")
    assert provider.creates == 1
    if omit:
        assert service.get(run.id).status == "FAILED"
        assert service.get(run.id).resource_warning
        executor = service.executor_for(run.id)
        assert isinstance(executor, RunpodTrainingExecutor)
        with pytest.raises(ExecutorError, match="capacity"):
            executor.lifecycle.reserve(uuid4())
    else:
        assert service.get(run.id).status == "FAILED"
        assert service.get(run.id).resource_cleanup == "CONFIRMED"
        assert not provider.inventory


@pytest.mark.parametrize("failure", ["transfer", "cancel", "timeout", "invalid-output"])
def test_failure_cancellation_timeout_and_invalid_artifacts_release_pod(
    versions: DatasetVersionService,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    service, _, provider, remote = setup_runpod(versions, tmp_path, monkeypatch)
    run = service.list_runs()[0]
    remote.fail_transfer = failure == "transfer"
    JobProcessor(service).tick("worker")
    if failure == "cancel":
        service.cancel(run.id)
        JobProcessor(service).tick("worker")
    elif failure == "timeout":
        executor = service.executor_for(run.id)
        assert isinstance(executor, RunpodTrainingExecutor)
        actual_resource = executor.lifecycle.resource

        def expired(owner: UUID) -> RunpodResource | None:
            row = actual_resource(owner)
            if row:
                row.expires_at = datetime(2000, 1, 1, tzinfo=UTC)
            return row

        monkeypatch.setattr(executor.lifecycle, "resource", expired)
        executor.status(run.external_id, IMAGE)
        JobProcessor(service).tick("reconciler")
    elif failure == "invalid-output":
        (remote.root / "exit.json").write_text('{"exit_code":0}')
        JobProcessor(service).tick("reconciler")
    assert service.get(run.id).status in ("FAILED", "CANCELED")
    assert service.get(run.id).resource_cleanup == "CONFIRMED" and not provider.inventory


def test_concurrent_reservations_and_ownership_guards(
    versions: DatasetVersionService,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, _, _, _ = setup_runpod(versions, tmp_path, monkeypatch)
    first = service.list_runs()[0]
    assert service.targets is not None
    other = service.targets.create(
        TargetCreate(
            name="Second Runpod target",
            kind="runpod_pod",
            credential_ref="env://RUNPOD_API_KEY",
            configuration=configuration(max_concurrent_pods=4),
        )
    )
    second = service.create(RunCreate(plan_id=first.plan_id, execution_target_id=other.id))
    executor = service.executor_for(first.id)
    assert isinstance(executor, RunpodTrainingExecutor)

    def reserve(owner: UUID) -> bool:
        try:
            selected = service.executor_for(owner)
            assert isinstance(selected, RunpodTrainingExecutor)
            selected.lifecycle.reserve(owner)
            return True
        except ExecutorError:
            return False

    with ThreadPoolExecutor(2) as pool:
        assert sorted(pool.map(reserve, [first.id, second.id])) == [False, True]
    with service.factory() as session:
        resource = session.scalar(select(RunpodResource))
        assert resource is not None
        owner = resource.owner_id
    with service.factory() as session, pytest.raises(DBAPIError):
        session.execute(
            text("UPDATE runpod_resources SET request='{}' WHERE owner_id=:id"), {"id": owner}
        )
        session.commit()
    with pytest.raises(ExecutorError, match="already attempted"):
        selected = service.executor_for(owner)
        assert isinstance(selected, RunpodTrainingExecutor)
        selected.lifecycle.reserve(owner)


def test_restart_adopts_started_process_without_repeating_create_or_launch(
    versions: DatasetVersionService,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, targets, provider, remote = setup_runpod(versions, tmp_path, monkeypatch)
    run = service.list_runs()[0]
    transition = service.transition

    def crash(session: Any, row: Any, state: JobState) -> None:
        if state == JobState.SUBMITTED:
            raise SystemExit("simulated worker death after remote launch")
        transition(session, row, state)

    monkeypatch.setattr(service, "transition", crash)
    with pytest.raises(SystemExit):
        JobProcessor(service).tick("worker")
    assert service.get(run.id).status == "PREPARING"
    restarted = TrainingRunService(
        service.plans, None, artifacts=service.artifacts, targets=targets
    )
    JobProcessor(restarted).tick("worker")
    assert restarted.get(run.id).status == "RUNNING"
    assert provider.creates == 1 and len(remote.launches) == 1
    restarted.cancel(run.id)
    JobProcessor(restarted).tick("worker")
    assert not provider.inventory


def test_definite_provider_refusal_releases_reservation_without_inventory_adoption(
    versions: DatasetVersionService,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, _, provider, _ = setup_runpod(versions, tmp_path, monkeypatch)
    provider.rejected = True
    run = service.list_runs()[0]
    JobProcessor(service).tick("worker")
    assert service.get(run.id).status == "FAILED"
    assert service.get(run.id).resource_cleanup == "CONFIRMED"
    assert provider.creates == 1 and not provider.inventory


def test_incompatible_allocation_is_owned_for_cleanup_but_never_executed(
    versions: DatasetVersionService,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, _, provider, remote = setup_runpod(versions, tmp_path, monkeypatch)
    provider.wrong_gpu = True
    run = service.list_runs()[0]
    JobProcessor(service).tick("worker")
    assert service.get(run.id).status == "FAILED"
    assert service.get(run.id).resource_cleanup == "CONFIRMED"
    assert not provider.inventory and not remote.launches


def test_cancellation_during_bootstrap_terminates_without_waiting_for_ssh(
    versions: DatasetVersionService,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, _, provider, remote = setup_runpod(versions, tmp_path, monkeypatch)
    run = service.list_runs()[0]
    create = provider.request

    def cancel_after_create(method: str, path: str, body: Any = None) -> dict[str, Any]:
        pod = create(method, path, body)
        service.cancel(run.id)
        return pod

    monkeypatch.setattr(provider, "request", cancel_after_create)
    JobProcessor(service).tick("worker")
    JobProcessor(service).tick("worker")
    assert service.get(run.id).status == "CANCELED"
    assert service.get(run.id).resource_cleanup == "CONFIRMED"
    assert not provider.inventory and not remote.launches


def test_ephemeral_host_trust_is_provisioned_before_create_and_removed_after_cleanup(
    versions: DatasetVersionService,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, _, provider, _ = setup_runpod(
        versions, tmp_path, monkeypatch, ssh_host_trust="provisioned-ephemeral"
    )
    run = service.list_runs()[0]
    executor = service.executor_for(run.id)
    assert isinstance(executor, RunpodTrainingExecutor)
    executor.lifecycle.create(run.id)
    row = executor.lifecycle.resource(run.id)
    assert row is not None
    assert "PRIVATE KEY" not in json.dumps(row.request)
    assert row.request["env"]["KITCHENSOUP_HOST_PUBLIC_KEY"].startswith("ssh-rsa ")
    assert "PRIVATE KEY" in provider.inventory["testpod"]["env"]["KITCHENSOUP_SSH_HOST_KEY"]
    assert executor.lifecycle.host_key_directory is not None
    key_path = executor.lifecycle.host_key_directory / (str(row.id) + ".key")
    assert key_path.stat().st_mode & 0o077 == 0
    executor.lifecycle.pending(run.id, JobStatus(state="CANCELED"))
    executor.lifecycle.cleanup(run.id)
    assert not key_path.exists() and not provider.inventory
