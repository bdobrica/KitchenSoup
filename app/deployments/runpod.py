"""Owned, leased direct vLLM Pods with an authenticated bounded HTTP proxy."""

import hashlib
import json
import os
import secrets
import shutil
import time
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

import httpx

from app.deployments.base import DeploymentStatus
from app.deployments.local import LocalDockerDeploymentExecutor
from app.deployments.materialize import DeploymentMaterializer
from app.deployments.schemas import ChatRequest, ChatResponse, DeploymentSpec
from app.executors.base import ExecutorError, JobStatus
from app.targets.runpod_lifecycle import RunpodLifecycle
from app.targets.runpod_ssh import RunpodConnection
from app.training.runner import read_bounded


class UnprotectedEndpoint(ExecutorError):
    pass


class RunpodDeploymentExecutor(RunpodConnection, LocalDockerDeploymentExecutor):
    def __init__(
        self,
        root: Path,
        materializer: DeploymentMaterializer,
        lifecycle: RunpodLifecycle,
        *,
        known_hosts: Path,
        secret_directory: Path,
        env_names: set[str],
    ) -> None:
        super().__init__(root, materializer, image=lifecycle.config.training_image)
        self.lifecycle = lifecycle
        self.known_hosts, self.secret_directory, self.env_names = (
            known_hosts,
            secret_directory,
            env_names,
        )

    def connection_workspace(self, owner: UUID) -> Path:
        return self.workspace(owner)

    def image_identity(self) -> str:
        return self.image.rsplit("@", 1)[1]

    def validate(self, spec: DeploymentSpec) -> None:
        if spec.image_digest != self.image_identity():
            raise ExecutorError("Serving image differs from immutable target")
        if spec.representation == "awq" and spec.precision != "fp16":
            raise ExecutorError("AWQ serving requires FP16")

    def remote(self, identifier: UUID) -> str:
        return "/home/runner/jobs/" + str(identifier)

    def deploy(self, identifier: UUID, spec: DeploymentSpec) -> None:
        self.validate(spec)
        path = self.workspace(identifier)
        if path.exists():
            raise ExecutorError("Serving preparation was already attempted; reconcile or relaunch")
        path.mkdir(mode=0o700)
        try:
            self.materializer.materialize(spec, path)
            model_config = json.loads(read_bounded(path / "model/config.json", 1024**2))
            model_limit = model_config.get("max_position_embeddings", spec.max_model_len)
            if type(model_limit) is not int or model_limit < 256:
                raise ExecutorError("Unsupported model context limit")
            model_len = min(spec.max_model_len, model_limit)
            weights = sum(p.stat().st_size for p in path.rglob("*.safetensors"))
            if weights * 1.2 + 2 * 1024**3 > self.lifecycle.config.min_vram_gb * 1024**3:
                raise ExecutorError("Model exceeds conservative serving VRAM budget")
            staged_bytes = sum(p.stat().st_size for p in path.rglob("*") if p.is_file())
            if staged_bytes > min(9, self.lifecycle.config.disk_gb - 15) * 1024**3:
                raise ExecutorError("Model exceeds serving disk or bounded transfer budget")
            with open(
                path / "inference.key", "x", opener=lambda p, f: os.open(p, f, 0o600)
            ) as stream:
                stream.write(secrets.token_urlsafe(32))
            self.lifecycle.create(identifier, "deployment")
            row = self.lifecycle.resource(identifier)
            assert row is not None
            deadline = time.monotonic() + min(
                900, max(0, (row.expires_at - datetime.now(UTC)).total_seconds())
            )
            while True:
                if self.lifecycle.deployment_stop_requested(identifier):
                    raise ExecutorError("Deployment stopped or expired during preparation")
                pod = self.lifecycle.inspect(identifier)
                if pod is None:
                    raise ExecutorError("Inference Pod disappeared during preparation")
                try:
                    self.connect_pod(pod)
                    self.provisioned_trust(identifier)
                    probe = self.transport.command(
                        ["python3", "/runner/runpod_serving.py", "probe"]
                    )
                    if probe.returncode:
                        raise ExecutorError("Serving GPU preflight failed")
                except ExecutorError:
                    if time.monotonic() >= deadline:
                        raise
                    time.sleep(2)
                    continue
                break
            hardware = json.loads(probe.stdout)
            if (
                not hardware["cuda"]
                or hardware["count"] != 1
                or hardware["vram_gb"] < self.lifecycle.config.min_vram_gb
                or (spec.precision == "bf16" and not hardware["bf16"])
            ):
                raise ExecutorError("Actual GPU does not meet serving precision/VRAM requirements")
            if spec.representation == "awq" and tuple(hardware["capability"]) < (7, 5):
                raise ExecutorError("Selected GPU cannot serve AWQ")
            remaining = int((row.expires_at - datetime.now(UTC)).total_seconds())
            if remaining <= 0:
                raise ExecutorError("Serving preparation exceeded deadline")
            (path / "serving.json").write_text(
                json.dumps(
                    {
                        "dtype": "half" if spec.precision == "fp16" else "bfloat16",
                        "max_model_len": model_len,
                        "adapter": spec.adapter,
                        "remaining_seconds": remaining,
                        "expires_at": row.expires_at.timestamp(),
                        "snapshot": spec.model_dump(mode="json", by_alias=True),
                        "hardware": hardware,
                    }
                )
            )
            records = self.root / "records"
            records.mkdir(mode=0o700, exist_ok=True)
            with open(
                records / (str(identifier) + ".json"), "x", opener=lambda p, f: os.open(p, f, 0o600)
            ) as record:
                record.write((path / "serving.json").read_text())
            manifest = {
                p.relative_to(path).as_posix(): hashlib.file_digest(
                    p.open("rb"), "sha256"
                ).hexdigest()
                for p in path.rglob("*")
                if p.is_file()
            }
            (path / "manifest.json").write_text(json.dumps(manifest))
            self.transport.upload(path, self.remote(identifier))
            if self.lifecycle.deployment_stop_requested(identifier):
                raise ExecutorError("Deployment stopped during transfer")
            (path / "launch-intent").touch()
            result = self.transport.command(
                [
                    "python3",
                    "/runner/runpod_serving.py",
                    "launch",
                    self.remote(identifier),
                    hashlib.sha256((path / "manifest.json").read_bytes()).hexdigest(),
                ]
            )
            if result.returncode:
                raise ExecutorError("Serving verification or launch failed")
        except Exception:
            raise ExecutorError("Runpod serving preparation failed; termination required") from None

    def endpoint_for(self, identifier: UUID, spec: DeploymentSpec) -> str:
        self.validate(spec)
        row = self.lifecycle.resource(identifier)
        if (
            row is None
            or row.kind != "deployment"
            or row.cleanup != "NONE"
            or datetime.now(UTC) >= row.expires_at
        ):
            raise ExecutorError("Owned inference resource is unavailable or expired")
        pod = self.lifecycle.inspect(identifier)
        if pod is None or "8000/http" not in pod.get("ports", []):
            raise ExecutorError("Owned inference Pod is unavailable")
        pod_id = self.lifecycle.client.pod_id(pod["id"])
        return f"https://{pod_id}-8000.proxy.runpod.net"

    def headers(self, identifier: UUID) -> dict[str, str]:
        try:
            key = read_bounded(self.workspace(identifier) / "inference.key", 128).decode().strip()
        except (OSError, ValueError):
            raise ExecutorError("Private inference credential unavailable") from None
        if len(key) != 43 or any(
            c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for c in key
        ):
            raise ExecutorError("Private inference credential unavailable")
        return {"Authorization": "Bearer " + key}

    def request_owned(
        self,
        identifier: UUID,
        endpoint: str,
        method: str,
        route: str,
        payload: dict[str, Any] | None = None,
        *,
        anonymous: bool = False,
    ) -> bytes:
        try:
            with httpx.Client(
                timeout=httpx.Timeout(90, connect=10), trust_env=False, follow_redirects=False
            ) as client:
                with client.stream(
                    method,
                    endpoint + route,
                    headers={} if anonymous else self.headers(identifier),
                    json=payload,
                ) as response:
                    if anonymous:
                        if response.status_code == 200:
                            raise UnprotectedEndpoint(
                                "Public inference authentication is not enforced"
                            )
                        if response.status_code != 401:
                            raise ExecutorError("Inference authentication readiness unavailable")
                        return b""
                    if response.status_code != 200:
                        raise ExecutorError("Authenticated inference unavailable")
                    data = bytearray()
                    deadline = time.monotonic() + 90
                    for chunk in response.iter_bytes(65536):
                        data.extend(chunk)
                        if len(data) > 1024**2 or time.monotonic() >= deadline:
                            raise ExecutorError("Inference response exceeded playground bounds")
                    return bytes(data)
        except (httpx.HTTPError, OSError, ValueError):
            raise ExecutorError("Runpod inference connection unavailable") from None

    def status(self, identifier: UUID, spec: DeploymentSpec) -> DeploymentStatus:
        self.validate(spec)
        row = self.lifecycle.resource(identifier)
        if row is None:
            return DeploymentStatus("LOST")
        if not (self.workspace(identifier) / "launch-intent").exists():
            return DeploymentStatus("FAILED")
        pod = self.lifecycle.inspect(identifier)
        if pod is None:
            return DeploymentStatus("LOST")
        if pod.get("status") in ("EXITED", "STOPPED", "FAILED", "TERMINATED"):
            return DeploymentStatus("FAILED")
        self.connect_pod(pod)
        self.provisioned_trust(identifier)
        if self.transport.read(self.remote(identifier) + "/exit.json", 1024, missing_ok=True):
            return DeploymentStatus("FAILED")
        endpoint = self.endpoint_for(identifier, spec)
        try:
            for route in ("/v1/models", "/tokenize", "/invocations"):
                self.request_owned(identifier, endpoint, "GET", route, anonymous=True)
            models = json.loads(self.request_owned(identifier, endpoint, "GET", "/v1/models"))
            required = {"playground", "tuned"} if spec.adapter else {"playground"}
            if not required <= {item["id"] for item in models["data"]}:
                raise ValueError
        except UnprotectedEndpoint:
            return DeploymentStatus("FAILED")
        except (ExecutorError, ValueError, KeyError, TypeError):
            return DeploymentStatus("STARTING")
        return DeploymentStatus("RUNNING", endpoint)

    def stop(self, identifier: UUID, spec: DeploymentSpec) -> None:
        self.validate(spec)
        row = self.lifecycle.resource(identifier)
        if row is not None:
            if (self.workspace(identifier) / "launch-intent").exists() and row.state != "RELEASED":
                try:
                    content = self.logs(identifier, spec)
                    directory = self.root / "logs"
                    directory.mkdir(mode=0o700, exist_ok=True)
                    with open(
                        directory / (str(identifier) + ".log"),
                        "w",
                        opener=lambda p, f: os.open(p, f, 0o600),
                    ) as stream:
                        stream.write(content)
                except (ExecutorError, OSError):
                    pass  # Logs never prevent provider termination.
            self.lifecycle.pending(identifier, JobStatus(state="CANCELED"))
            self.lifecycle.cleanup(identifier)
        path = self.workspace(identifier)
        if path.exists():
            shutil.rmtree(path)

    @staticmethod
    def payload(spec: DeploymentSpec, body: ChatRequest, stream: bool) -> dict[str, Any]:
        return {
            "model": "tuned" if spec.adapter else "playground",
            "messages": [m.model_dump() for m in body.messages],
            "stream": stream,
            "max_tokens": 128,
            "temperature": 0,
        }

    def chat(self, identifier: UUID, spec: DeploymentSpec, body: ChatRequest) -> ChatResponse:
        endpoint = self.endpoint_for(identifier, spec)
        try:
            result = json.loads(
                self.request_owned(
                    identifier,
                    endpoint,
                    "POST",
                    "/v1/chat/completions",
                    self.payload(spec, body, False),
                )
            )
            return ChatResponse(content=result["choices"][0]["message"]["content"])
        except (ValueError, KeyError, IndexError, TypeError):
            raise ExecutorError("Invalid inference response") from None

    def stream_chat(
        self, identifier: UUID, spec: DeploymentSpec, body: ChatRequest
    ) -> Iterator[str]:
        endpoint = self.endpoint_for(identifier, spec)
        total, content_size = 0, 0
        deadline = time.monotonic() + 90
        try:
            with httpx.Client(
                timeout=httpx.Timeout(15, connect=10), trust_env=False, follow_redirects=False
            ) as client:
                with client.stream(
                    "POST",
                    endpoint + "/v1/chat/completions",
                    headers=self.headers(identifier),
                    json=self.payload(spec, body, True),
                ) as response:
                    if response.status_code != 200:
                        raise ExecutorError("Streaming inference unavailable")
                    pending = b""
                    for chunk in response.iter_bytes(4096):
                        total += len(chunk)
                        if total > 1024**2 or time.monotonic() >= deadline:
                            raise ExecutorError("Streaming inference exceeded playground bounds")
                        pending += chunk
                        if len(pending) > 65536:
                            raise ExecutorError("Streaming inference line exceeded limit")
                        while b"\n" in pending:
                            line, pending = pending.split(b"\n", 1)
                            if not line.startswith(b"data: "):
                                continue
                            raw = line[6:].strip()
                            if raw == b"[DONE]":
                                return
                            value = json.loads(raw)
                            text = value["choices"][0]["delta"].get("content") or ""
                            if not isinstance(text, str):
                                raise ValueError
                            content_size += len(text)
                            if content_size > 65536:
                                raise ExecutorError("Streaming content exceeded playground limit")
                            if text:
                                yield text
                    raise ExecutorError("Streaming inference ended before completion")
        except (httpx.HTTPError, ValueError, KeyError, TypeError, IndexError):
            raise ExecutorError("Streaming inference unavailable") from None

    def logs(self, identifier: UUID, spec: DeploymentSpec) -> str:
        self.validate(spec)
        retained = self.root / "logs" / (str(identifier) + ".log")
        resource = self.lifecycle.resource(identifier)
        if resource is None or resource.state == "RELEASED":
            return (
                read_bounded(retained, 65536).decode()
                if retained.exists()
                else "Inference Pod unavailable"
            )
        pod = self.lifecycle.inspect(identifier)
        if pod is None:
            return (
                read_bounded(retained, 65536).decode()
                if retained.exists()
                else "Inference Pod unavailable"
            )
        self.connect_pod(pod)
        self.provisioned_trust(identifier)
        data = self.transport.read(self.remote(identifier) + "/server.log", 65536, missing_ok=True)
        if not data:
            return "Waiting for vLLM startup output"
        key = self.headers(identifier)["Authorization"].removeprefix("Bearer ")
        text = data.decode("utf-8", errors="replace").replace(key, "[REDACTED]")
        return text.encode("utf-8")[:65536].decode("utf-8", errors="ignore")
