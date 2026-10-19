import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock
from uuid import uuid4

import httpx
import pytest
from pydantic import ValidationError

from app.deployments.runpod import RunpodDeploymentExecutor
from app.deployments.schemas import ChatRequest, DeploymentSpec
from app.executors.base import ExecutorError
from app.targets.runpod_schemas import RunpodConfig
from app.training.schemas import PlanModelSource
from tests.unit.test_runpod import configuration


def executor(tmp_path: Path) -> tuple[RunpodDeploymentExecutor, Any, DeploymentSpec]:
    life = MagicMock()
    life.config = RunpodConfig.model_validate(
        configuration(deployment_image="quay.io/example/vllm@sha256:" + "b" * 64)
    ).serving()
    backend = RunpodDeploymentExecutor(
        tmp_path,
        MagicMock(),
        life,
        known_hosts=tmp_path / "hosts",
        secret_directory=tmp_path,
        env_names=set(),
    )
    owner = uuid4()
    backend.workspace(owner).mkdir()
    (backend.workspace(owner) / "inference.key").write_text("a" * 43)
    spec = DeploymentSpec(
        image_digest="sha256:" + "b" * 64,
        base_source=PlanModelSource(
            source_id=uuid4(),
            kind="upload",
            location="fixture",
            revision=None,
            license="CC0",
            artifact_id=uuid4(),
            artifact_sha256="a" * 64,
        ),
        artifact_id=uuid4(),
        artifact_sha256="a" * 64,
        adapter=False,
        precision="bf16",
    )
    return backend, owner, spec


def test_separate_images_gpu_allowlist_and_independent_overrides() -> None:
    old = RunpodConfig.model_validate(configuration())
    with pytest.raises(ValueError, match="separately pinned"):
        old.serving()
    with pytest.raises(ValidationError, match="allowlist"):
        RunpodConfig.model_validate(configuration(deployment_gpu_id="unapproved"))
    config = RunpodConfig.model_validate(configuration(deployment_image=old.training_image))
    with pytest.raises(ValueError, match="separate images"):
        config.serving()
    config = RunpodConfig.model_validate(
        configuration(
            deployment_image="quay.io/example/vllm@sha256:" + "b" * 64,
            allowed_gpu_ids=[old.gpu_id, "another GPU"],
            deployment_gpu_id="another GPU",
            deployment_disk_gb=40,
            deployment_min_vram_gb=16,
            max_deployment_lease_minutes=20,
        )
    )
    serving = config.serving()
    assert serving.gpu_id == "another GPU" and serving.disk_gb == 40
    assert serving.min_vram_gb == 16 and serving.max_runtime_minutes == 20
    assert config.gpu_id == old.gpu_id and config.training_image == old.training_image


@pytest.mark.parametrize("status", [200, 302, 403, 500])
def test_readiness_requires_unauthenticated_401(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, status: int
) -> None:
    backend, owner, _ = executor(tmp_path)
    original = httpx.Client
    monkeypatch.setattr(
        httpx,
        "Client",
        lambda **kw: original(
            transport=httpx.MockTransport(lambda request: httpx.Response(status)), **kw
        ),
    )
    with pytest.raises(ExecutorError, match="authentication"):
        backend.request_owned(
            owner, "https://testpod-8000.proxy.runpod.net", "GET", "/v1/models", anonymous=True
        )


@pytest.mark.parametrize("mode", ["success", "bad-json", "truncated", "oversize", "redirect"])
def test_stream_authentication_bounds_and_forced_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    backend, owner, spec = executor(tmp_path)
    monkeypatch.setattr(
        backend, "endpoint_for", lambda *args: "https://testpod-8000.proxy.runpod.net"
    )

    def respond(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer " + "a" * 43
        payload = json.loads(request.content)
        assert payload["model"] == "playground" and payload["max_tokens"] == 128
        assert payload["stream"] is True and payload["temperature"] == 0
        data = b'data: {"choices":[{"delta":{"content":"hello"}}]}\n\ndata: [DONE]\n\n'
        if mode == "bad-json":
            data = b"data: malformed\n\n"
        elif mode == "truncated":
            data = data.split(b"data: [DONE]")[0]
        elif mode == "oversize":
            data = b"x" * (1024**2 + 1)
        return httpx.Response(302 if mode == "redirect" else 200, content=data)

    original = httpx.Client
    monkeypatch.setattr(
        httpx, "Client", lambda **kw: original(transport=httpx.MockTransport(respond), **kw)
    )
    stream = backend.stream_chat(
        owner, spec, ChatRequest(messages=[{"role": "user", "content": "synthetic"}])
    )
    if mode == "success":
        assert list(stream) == ["hello"]
    else:
        with pytest.raises(ExecutorError):
            list(stream)


def test_log_secret_redaction(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    backend, owner, spec = executor(tmp_path)
    backend.transport = MagicMock()
    backend.transport.read.return_value = b"startup secret=" + b"a" * 43
    monkeypatch.setattr(backend, "connect_pod", lambda *args: None)
    monkeypatch.setattr(backend, "provisioned_trust", lambda *args: None)
    assert "[REDACTED]" in backend.logs(owner, spec)
    assert "a" * 43 not in backend.logs(owner, spec)
    backend.transport.read.return_value = b"\xff" * 65537
    assert len(backend.logs(owner, spec).encode("utf-8")) <= 65536
    (backend.root / "logs").mkdir()
    (backend.root / "logs" / (str(owner) + ".log")).write_text("retained startup output")
    life = backend.lifecycle
    assert isinstance(life, MagicMock)
    life.inspect.return_value = None
    assert backend.logs(owner, spec) == "retained startup output"


@pytest.mark.parametrize(
    "route", ["/v1/models", "/v1/chat/completions", "/tokenize", "/invocations", "/metrics"]
)
def test_image_middleware_protects_auxiliary_routes(
    monkeypatch: pytest.MonkeyPatch, route: str
) -> None:
    import importlib.util

    from starlette.applications import Starlette
    from starlette.responses import JSONResponse
    from starlette.routing import Route
    from starlette.testclient import TestClient

    source = Path(__file__).resolve().parents[2] / "images/vllm/runpod_auth.py"
    definition = importlib.util.spec_from_file_location("runpod_auth_fixture", source)
    assert definition and definition.loader
    module = importlib.util.module_from_spec(definition)
    definition.loader.exec_module(module)
    monkeypatch.setenv("VLLM_API_KEY", "a" * 43)
    app = Starlette(routes=[Route(route, lambda request: JSONResponse({"ok": True}))])
    with TestClient(module.InferenceAuth(app)) as client:
        assert client.get(route).status_code == 401
        assert client.get(route, headers={"Authorization": "Bearer wrong"}).status_code == 401
        assert client.get(route, headers={"Authorization": "Bearer " + "a" * 43}).status_code == 200


@pytest.mark.parametrize("upstream", [404, 429, 500, 502, 503, 504, 200, 401])
@pytest.mark.parametrize("adapter", [False, True])
def test_proxy_warmup_is_starting_but_anonymous_success_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, upstream: int, adapter: bool
) -> None:
    from datetime import UTC, datetime, timedelta
    from types import SimpleNamespace

    backend, owner, spec = executor(tmp_path)
    spec = spec.model_copy(update={"adapter": adapter})
    life = backend.lifecycle
    assert isinstance(life, MagicMock)
    life.resource.return_value = SimpleNamespace(
        kind="deployment", cleanup="NONE", expires_at=datetime.now(UTC) + timedelta(minutes=5)
    )
    life.inspect.return_value = {
        "id": "testpod",
        "status": "RUNNING",
        "ports": ["22/tcp", "8000/http"],
    }
    life.client.pod_id.side_effect = lambda value: value
    backend.transport = MagicMock()
    backend.transport.read.return_value = b""
    backend.workspace(owner).joinpath("launch-intent").touch()
    monkeypatch.setattr(backend, "connect_pod", lambda *args: None)
    monkeypatch.setattr(backend, "provisioned_trust", lambda *args: None)

    def respond(request: httpx.Request) -> httpx.Response:
        if "authorization" in request.headers:
            return httpx.Response(200, json={"data": [{"id": "playground"}]})
        return httpx.Response(upstream)

    original = httpx.Client
    monkeypatch.setattr(
        httpx, "Client", lambda **kw: original(transport=httpx.MockTransport(respond), **kw)
    )
    expected = (
        "RUNNING"
        if upstream == 401 and not adapter
        else "FAILED"
        if upstream == 200
        else "STARTING"
    )
    assert backend.status(owner, spec).state == expected
