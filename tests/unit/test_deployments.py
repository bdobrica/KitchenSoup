import json
import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import Mock
from uuid import uuid4

import httpx
import pytest
from pydantic import ValidationError

from app.deployments import local
from app.deployments.local import LocalDockerDeploymentExecutor
from app.deployments.materialize import DeploymentMaterializer
from app.deployments.schemas import ChatRequest, DeploymentCreate, DeploymentSpec
from app.executors.base import ExecutorError
from app.training.schemas import PlanModelSource


def spec() -> DeploymentSpec:
    return DeploymentSpec(
        image_digest="sha256:" + "a" * 64,
        base_source=PlanModelSource(
            source_id=uuid4(),
            kind="upload",
            location="test",
            revision=None,
            license="CC0",
            artifact_id=uuid4(),
            artifact_sha256="b" * 64,
        ),
        artifact_id=uuid4(),
        artifact_sha256="c" * 64,
        adapter=True,
        precision="fp16",
    )


class Docker:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.exists = False
        self.network_exists = False
        self.info: dict[str, Any] = {}
        self.unavailable = False

    def __call__(self, args: list[str]) -> subprocess.CompletedProcess[str]:
        self.calls.append(args)
        code, out, err = 0, "", ""
        if args[:2] == ["image", "inspect"]:
            out = "sha256:" + "a" * 64
        elif args[:2] == ["network", "inspect"]:
            if self.network_exists:
                out = json.dumps([{"Internal": True, "Labels": self.info["Config"]["Labels"]}])
            else:
                code, err = 1, "No such network"
        elif args[:2] == ["network", "create"]:
            self.network_exists = True
        elif args[:2] == ["network", "rm"]:
            self.network_exists = False
        elif args[0] == "create":
            self.exists = True
            self.info = {
                "Config": {
                    "Image": args[args.index("--mount") + 2],
                    "Labels": dict(
                        args[i + 1].split("=", 1) for i, a in enumerate(args) if a == "--label"
                    ),
                },
                "State": {"Running": True},
                "NetworkSettings": {
                    "Networks": {args[args.index("--name") + 1]: {"IPAddress": "172.20.0.2"}}
                },
            }
        elif args[:2] == ["container", "inspect"]:
            if not self.exists or self.unavailable:
                code, err = 1, "unavailable" if self.unavailable else "No such container"
            else:
                out = json.dumps([self.info])
        elif args[0] == "rm":
            self.exists = False
        return subprocess.CompletedProcess(args, code, out, err)


def test_local_serving_identity_isolation_proxy_and_stop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    backend = Docker()
    monkeypatch.setattr(local, "docker", backend)
    executor = LocalDockerDeploymentExecutor(tmp_path / "jobs", Mock(spec=DeploymentMaterializer))
    snapshot = spec()
    identifier = uuid4()
    assert executor.image_identity() == snapshot.image_digest
    executor.deploy(identifier, snapshot)
    command = next(c for c in backend.calls if c[0] == "create")
    assert "--publish" not in command and "--network" in command
    assert "exec" in command[command.index("--tmpfs") + 1].split(":", 1)[1].split(",")
    assert "--read-only" in command and "--cap-drop" in command and "--enable-lora" in command
    assert "--trust-remote-code" not in command and "half" in command
    assert not any("docker.sock" in arg for arg in command)
    captured: list[dict[str, Any]] = []

    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "172.20.0.2"
        if request.method == "GET":
            return httpx.Response(200)
        captured.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": "Hello"}}]})

    client = httpx.Client
    monkeypatch.setattr(
        httpx, "Client", lambda **kw: client(transport=httpx.MockTransport(handle), **kw)
    )
    assert executor.status(identifier, snapshot).state == "RUNNING"
    body = ChatRequest.model_validate({"messages": [{"role": "user", "content": "Question"}]})
    assert executor.chat(identifier, snapshot, body).content == "Hello"
    assert captured[0]["model"] == "tuned" and captured[0]["max_tokens"] == 128
    backend.info["Config"]["Labels"]["kitchensoup.deployment"] = str(uuid4())
    with pytest.raises(ExecutorError, match="identity"):
        executor.stop(identifier, snapshot)
    assert backend.exists
    backend.info["Config"]["Labels"]["kitchensoup.deployment"] = str(identifier)
    backend.unavailable = True
    with pytest.raises(ExecutorError, match="unavailable"):
        executor.status(identifier, snapshot)
    backend.unavailable = False
    executor.stop(identifier, snapshot)
    assert (
        not backend.exists
        and not backend.network_exists
        and not executor.workspace(identifier).exists()
    )
    executor.stop(identifier, snapshot)
    assert executor.status(identifier, snapshot).state == "LOST"


@pytest.mark.parametrize("address", ["0.0.0.0", "8.8.8.8", "127.0.0.1"])
def test_proxy_rejects_non_private_binding(address: str) -> None:
    with pytest.raises(ExecutorError, match="private"):
        LocalDockerDeploymentExecutor.endpoint(
            {"NetworkSettings": {"Networks": {"owned": {"IPAddress": address}}}}, "owned"
        )


def test_chat_and_deployment_requests_are_bounded() -> None:
    bodies: tuple[dict[str, Any], ...] = (
        {"messages": []},
        {"messages": [{"role": "user", "content": "x" * 8001}]},
        {"messages": [{"role": "user", "content": "x" * 6000}] * 3},
        {"messages": [{"role": "assistant", "content": "Hi"}]},
        {"messages": [{"role": "user", "content": "Hi"}], "base_url": "http://attacker"},
    )
    for body in bodies:
        with pytest.raises(ValidationError):
            ChatRequest.model_validate(body)
    with pytest.raises(ValidationError):
        DeploymentCreate(model_version_id=uuid4(), lease_minutes=1441)


def test_deployment_snapshot_contract_matches_generated_schema() -> None:
    schema = Path("docs/contracts/deployment-v1.schema.json")
    assert json.loads(schema.read_text()) == DeploymentSpec.model_json_schema()
