"""Host-only vLLM containers, private model mounts and internal-network proxy access."""

import json
import os
import re
import shutil
import subprocess
from ipaddress import IPv4Address, IPv4Network
from pathlib import Path
from typing import Any
from uuid import UUID

import httpx

from app.deployments.base import DeploymentStatus
from app.deployments.materialize import DeploymentMaterializer
from app.deployments.schemas import ChatRequest, ChatResponse, DeploymentSpec
from app.executors.base import ExecutorError
from app.executors.local_docker import docker


def container_name(identifier: UUID) -> str:
    return "kitchensoup-playground-" + str(identifier)


class LocalDockerDeploymentExecutor:
    def __init__(
        self,
        root: Path,
        materializer: DeploymentMaterializer,
        *,
        image: str = "kitchensoup-vllm:local",
    ) -> None:
        self.root = root.resolve()
        if any(c in str(self.root) for c in (",", "\n", "\r")):
            raise ExecutorError("Deployment workspace path contains unsupported characters")
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.materializer, self.image = materializer, image

    def docker(self, arguments: list[str]) -> subprocess.CompletedProcess[str]:
        return docker(arguments)

    def identity(self) -> tuple[int, int]:
        return os.getuid(), os.getgid()

    def image_identity(self) -> str:
        result = self.docker(["image", "inspect", self.image, "--format", "{{.Id}}"])
        identity = result.stdout.strip()
        if result.returncode or not re.fullmatch(r"sha256:[0-9a-f]{64}", identity):
            raise ExecutorError("Build the pinned vLLM image before deploying")
        return identity

    def workspace(self, identifier: UUID) -> Path:
        path = self.root / str(identifier)
        if path.is_symlink():
            raise ExecutorError("Deployment workspace cannot be a link")
        return path

    def inspect(self, identifier: UUID, spec: DeploymentSpec) -> dict[str, Any] | None:
        result = self.docker(["container", "inspect", container_name(identifier)])
        if result.returncode:
            if "No such" in result.stderr:
                return None
            raise ExecutorError("Deployment Docker inspection unavailable")
        try:
            info: dict[str, Any] = json.loads(result.stdout)[0]
            if info["Config"]["Image"] != spec.image_digest or info["Config"]["Labels"].get(
                "kitchensoup.deployment"
            ) != str(identifier):
                raise ValueError
            return info
        except (ValueError, KeyError, TypeError, IndexError):
            raise ExecutorError("Deployment container identity mismatch") from None

    def network(self, identifier: UUID, *, create: bool = False) -> bool:
        name = container_name(identifier)
        found = self.docker(["network", "inspect", name])
        if found.returncode:
            if "No such" not in found.stderr and "not found" not in found.stderr:
                raise ExecutorError("Deployment network inspection unavailable")
            if not create:
                return False
            if self.docker(
                [
                    "network",
                    "create",
                    "--internal",
                    "--label",
                    "kitchensoup.deployment=" + str(identifier),
                    name,
                ]
            ).returncode:
                raise ExecutorError("Deployment network creation failed")
            return True
        info = json.loads(found.stdout)[0]
        if info.get("Internal") is not True or info.get("Labels", {}).get(
            "kitchensoup.deployment"
        ) != str(identifier):
            raise ExecutorError("Deployment network identity mismatch")
        return True

    def deploy(self, identifier: UUID, spec: DeploymentSpec) -> None:
        if self.identity()[0] == 0:
            raise ExecutorError("Run local serving as a non-root host user")
        path = self.workspace(identifier)
        if path.exists() or self.inspect(identifier, spec) is not None:
            raise ExecutorError("Deployment already prepared; create a new attempt")
        path.mkdir(mode=0o700)
        try:
            self.materializer.materialize(spec, path)
        except Exception:
            raise ExecutorError(
                "Serving model preparation failed; verify registered inputs and disk capacity"
            ) from None
        self.network(identifier, create=True)
        name = container_name(identifier)
        args = [
            "create",
            "--pull",
            "never",
            "--name",
            name,
            "--label",
            "kitchensoup.deployment=" + str(identifier),
            "--gpus",
            "all",
            "--network",
            name,
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--pids-limit",
            "512",
            "--memory",
            "16g",
            "--memory-swap",
            "16g",
            "--cpus",
            "4",
            "--shm-size",
            "1g",
            "--tmpfs",
            "/tmp:rw,exec,nosuid,nodev,size=2g",
            "--log-driver",
            "local",
            "--log-opt",
            "max-size=1m",
            "--log-opt",
            "max-file=2",
            "--user",
            f"{self.identity()[0]}:{self.identity()[1]}",
            "--mount",
            f"type=bind,src={path},dst=/models,readonly",
            spec.image_digest,
            "--model",
            "/models/model",
            "--served-model-name",
            "playground",
            "--host",
            "0.0.0.0",
            "--port",
            "8000",
            "--dtype",
            "half" if spec.precision == "fp16" else "bfloat16",
            "--load-format",
            "safetensors",
            "--max-model-len",
            str(spec.max_model_len),
            "--max-num-seqs",
            "1",
            "--gpu-memory-utilization",
            "0.75",
            "--enforce-eager",
            "--disable-log-requests",
            "--disable-log-stats",
            "--uvicorn-log-level",
            "warning",
        ]
        if spec.adapter:
            args.extend(
                [
                    "--enable-lora",
                    "--max-loras",
                    "1",
                    "--max-lora-rank",
                    "64",
                    "--lora-modules",
                    "tuned=/models/adapter",
                ]
            )
        if self.docker(args).returncode or self.docker(["start", name]).returncode:
            raise ExecutorError(
                "Serving container could not start; check vLLM image and GPU capacity"
            )

    @staticmethod
    def endpoint(info: dict[str, Any], network: str) -> str:
        try:
            networks = info["NetworkSettings"]["Networks"]
            if set(networks) != {network}:
                raise ValueError
            address = IPv4Address(networks[network]["IPAddress"])
            if not any(
                address in IPv4Network(subnet)
                for subnet in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
            ):
                raise ValueError
            return f"http://{address}:8000"
        except (KeyError, ValueError, TypeError):
            raise ExecutorError(
                "Deployment endpoint is not a private Docker network binding"
            ) from None

    def status(self, identifier: UUID, spec: DeploymentSpec) -> DeploymentStatus:
        info = self.inspect(identifier, spec)
        if info is None:
            return DeploymentStatus("LOST")
        if not info["State"].get("Running"):
            return DeploymentStatus("FAILED")
        self.network(identifier)
        endpoint = self.endpoint(info, container_name(identifier))
        try:
            self.request(endpoint, "GET", "/health", None, 2)
            healthy = True
        except ExecutorError:
            healthy = False
        return DeploymentStatus("RUNNING" if healthy else "STARTING", endpoint)

    def request(
        self, endpoint: str, method: str, path: str, payload: dict[str, Any] | None, timeout: int
    ) -> bytes:
        try:
            with httpx.Client(timeout=timeout, trust_env=False, follow_redirects=False) as client:
                with client.stream(method, endpoint + path, json=payload) as response:
                    if response.status_code != 200:
                        raise ExecutorError("Inference unavailable; check model/GPU compatibility")
                    body = bytearray()
                    for chunk in response.iter_bytes(65536):
                        body.extend(chunk)
                        if len(body) > 1024**2:
                            raise ExecutorError("Serving response exceeded the playground limit")
                    return bytes(body)
        except httpx.HTTPError:
            raise ExecutorError("Playground inference unavailable") from None

    def stop(self, identifier: UUID, spec: DeploymentSpec) -> None:
        if self.inspect(identifier, spec) is not None:
            if self.docker(["rm", "--force", container_name(identifier)]).returncode:
                raise ExecutorError("Deployment stop failed; will retry")
        if self.network(identifier):
            if self.docker(["network", "rm", container_name(identifier)]).returncode:
                raise ExecutorError("Deployment network cleanup failed; will retry")
        try:
            path = self.workspace(identifier)
            if path.exists():
                shutil.rmtree(path)
        except OSError:
            raise ExecutorError("Deployment workspace cleanup failed; will retry") from None

    def chat(self, identifier: UUID, spec: DeploymentSpec, body: ChatRequest) -> ChatResponse:
        info = self.inspect(identifier, spec)
        if info is None or not info["State"].get("Running"):
            raise ExecutorError("Playground is no longer running")
        self.network(identifier)
        endpoint = self.endpoint(info, container_name(identifier))
        try:
            payload = self.request(
                endpoint,
                "POST",
                "/v1/chat/completions",
                {
                    "model": "tuned" if spec.adapter else "playground",
                    "messages": [m.model_dump() for m in body.messages],
                    "stream": False,
                    "max_tokens": 128,
                    "temperature": 0,
                },
                120,
            )
            result = json.loads(payload)
            return ChatResponse(content=result["choices"][0]["message"]["content"])
        except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError):
            raise ExecutorError(
                "Playground inference unavailable; retry or start a new deployment"
            ) from None
