import copy
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import httpx
import pytest
from pydantic import ValidationError

from app.executors.base import ExecutorError
from app.executors.runpod import RunpodTrainingExecutor
from app.providers.credentials import LocalCredentialResolver
from app.targets.runpod import RunpodClient
from app.targets.runpod_schemas import RunpodConfig
from app.targets.schemas import TargetCreate
from tests.unit.test_local_executor import IMAGE


def configuration(**overrides: Any) -> RunpodConfig:
    return RunpodConfig.model_validate(
        {
            "ssh_credential_ref": "env://SSH_KEY",
            "gpu_id": "Tesla V100-PCIE-16GB",
            "allowed_gpu_ids": ["Tesla V100-PCIE-16GB"],
            "training_image": "example/trainer@" + IMAGE,
            "max_hourly_price_usd": 0.5,
            **overrides,
        }
    )


def catalog() -> dict[str, Any]:
    return {
        "id": configuration().gpu_id,
        "manufacturer": "NVIDIA",
        "memory": 16,
        "secure": True,
        "community": True,
        "price": {"secure": 0.2, "community": 0.19},
        "availability": "LOW",
        "dataCenters": [{"id": "US-TX-1", "availability": "LOW"}],
    }


@pytest.mark.parametrize(
    "overrides",
    [
        {"gpu_count": 2},
        {"gpu_id": "other"},
        {"training_image": "example:latest"},
        {"max_runtime_minutes": 61},
        {"max_hourly_price_usd": float("nan")},
        {"ssh_credential_ref": "inline-key"},
        {"data_center_ids": ["../other"]},
        {"ssh_host_trust": "accept-any"},
    ],
)
def test_configuration_rejects_unbounded_authority(overrides: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        configuration(**overrides)


def test_target_kind_matches_configuration() -> None:
    body = {"name": "Runpod", "credential_ref": "env://KEY", "configuration": configuration()}
    assert TargetCreate(kind="runpod_pod", **body).configuration == configuration()
    with pytest.raises(ValidationError):
        TargetCreate(kind="ssh_docker", **body)


def test_api_bounds_safe_retries_and_never_retries_post(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RUNPOD_TEST_KEY", "private-test-value")
    seen: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        assert request.headers["Authorization"] == "Bearer private-test-value"
        assert request.url.host == "api.runpod.io"
        return httpx.Response(503, json={"detail": "private-test-value"})

    client_type = httpx.Client
    monkeypatch.setattr(
        "app.targets.runpod.httpx.Client",
        lambda **kw: client_type(**kw, transport=httpx.MockTransport(respond)),
    )
    monkeypatch.setattr("app.targets.runpod.time.sleep", lambda _: None)
    client = RunpodClient(
        "env://RUNPOD_TEST_KEY", LocalCredentialResolver({"RUNPOD_TEST_KEY"}, tmp_path)
    )
    with pytest.raises(ExecutorError) as error:
        client.request("POST", "/pods", {"name": "attempt"})
    assert len(seen) == 1 and "private-test-value" not in str(error.value)
    with pytest.raises(ExecutorError):
        client.request("GET", "/pods")
    assert len(seen) == 4


@pytest.mark.parametrize(
    "change",
    [
        {"memory": 4},
        {"manufacturer": "AMD"},
        {"availability": "NONE"},
        {"price": {"secure": 1}},
        {"price": {"secure": float("nan")}},
        {"price": {}},
        {"secure": False},
        {"id": "unapproved"},
    ],
)
def test_gpu_price_vram_and_availability_fail_closed(change: dict[str, Any]) -> None:
    client = RunpodClient("env://KEY", MagicMock())
    client.request = MagicMock(return_value={**catalog(), **change})  # type: ignore[method-assign]
    with pytest.raises(ExecutorError):
        client.quote(configuration())


def test_catalog_placement_and_pagination() -> None:
    client = RunpodClient("env://KEY", MagicMock())
    client.request = MagicMock(return_value=catalog())  # type: ignore[method-assign]
    assert client.quote(configuration(data_center_ids=["US-TX-1"])) == 0.2
    with pytest.raises(ExecutorError):
        client.quote(configuration(data_center_ids=["EU-XX-1"]))
    client.request = MagicMock(  # type: ignore[method-assign]
        side_effect=[
            {
                "pods": [{"id": "a"}],
                "pagination": {"hasNextPage": True, "nextCursor": "next/token"},
            },
            {"pods": [{"id": "b"}], "pagination": {"hasNextPage": False}},
        ]
    )
    assert client.pods() == [{"id": "a"}, {"id": "b"}]
    assert client.request.call_args_list[0].args[1] == "/pods?limit=100"
    assert "next%2Ftoken" in client.request.call_args_list[1].args[1]


@pytest.mark.parametrize(
    "direct",
    [
        None,
        {"host": "127.0.0.1", "port": 22},
        {"host": "169.254.169.254", "port": 22},
        {"host": "example.com", "port": 22},
    ],
)
def test_only_full_public_ip_ssh_is_used(direct: Any, tmp_path: Path) -> None:
    executor = RunpodTrainingExecutor(
        tmp_path,
        MagicMock(),
        MagicMock(config=configuration()),
        known_hosts=tmp_path / "known_hosts",
        secret_directory=tmp_path,
        env_names=set(),
    )
    with pytest.raises(ExecutorError):
        executor.connect_pod({"ssh": {"direct": direct, "proxy": {"host": "ssh.runpod.io"}}})


def test_host_trust_and_nonroot_identity_are_explicit(tmp_path: Path) -> None:
    executor = RunpodTrainingExecutor(
        tmp_path,
        MagicMock(),
        MagicMock(config=configuration()),
        known_hosts=tmp_path / "known_hosts",
        secret_directory=tmp_path,
        env_names=set(),
    )
    executor.connect_pod({"ssh": {"direct": {"host": "8.8.8.8", "port": 2222, "username": "root"}}})
    assert executor.transport.config.username == "runner"
    assert executor.transport.known_hosts == tmp_path / "known_hosts"
    assert executor.transport.config.port == 2222


def test_ownership_uses_nonce_image_and_configuration() -> None:
    from app.db.models import RunpodResource
    from app.targets.runpod_lifecycle import RunpodLifecycle

    request = {
        "name": "kitchensoup-id",
        "image": "repo@" + IMAGE,
        "env": {"KITCHENSOUP_NONCE": "nonce", "KITCHENSOUP_RESOURCE": "uuid"},
        "gpu": {"id": "T4", "count": 1},
        "cloud": "COMMUNITY",
        "disk": 30,
        "dataCenterIds": [],
    }
    row = RunpodResource(request=request)
    assert RunpodLifecycle.owned(row, request)
    for field, value in [
        ("env", {}),
        ("image", "other"),
        ("cloud", "SECURE"),
        ("gpu", {"id": "T4", "count": 2}),
    ]:
        pod = copy.deepcopy(request)
        pod[field] = value
        if field in ("image", "env"):
            assert not RunpodLifecycle.owned(row, pod)
        else:
            assert RunpodLifecycle.owned(row, pod)
            assert not RunpodLifecycle.compatible(row, pod)


def test_completion_between_polls_never_verifies_uncollected_outputs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from datetime import UTC, datetime, timedelta
    from uuid import uuid4

    from app.db.models import RunpodResource
    from app.executors.local_docker import container_name

    owner = uuid4()
    lifecycle = MagicMock(config=configuration())
    lifecycle.resource.return_value = RunpodResource(
        expires_at=datetime.now(UTC) + timedelta(minutes=15),
        outcome=None,
    )
    executor = RunpodTrainingExecutor(
        tmp_path,
        MagicMock(),
        lifecycle,
        known_hosts=tmp_path / "known_hosts",
        secret_directory=tmp_path,
        env_names=set(),
    )
    name = container_name(owner)
    workspace = executor.workspace(name)
    workspace.mkdir()
    (workspace / "launch-intent").touch()
    inspect = MagicMock(side_effect=[{"Running": True}, {"Running": False, "ExitCode": 0}])
    monkeypatch.setattr(executor, "inspect", inspect)
    assert executor.status(name, IMAGE).state == "RUNNING"
    assert inspect.call_count == 1
    lifecycle.pending.assert_not_called()
    lifecycle.cleanup.assert_not_called()
