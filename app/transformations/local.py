"""Host-authorized offline Docker transformation lifecycle and verified output."""

import hashlib
import json
import os
import re
import shutil
from pathlib import Path
from typing import BinaryIO, Protocol
from uuid import UUID
from zipfile import ZIP_STORED, ZipFile

from app.deployments.materialize import DeploymentMaterializer
from app.deployments.schemas import DeploymentSpec
from app.executors.base import ExecutorError
from app.executors.local_docker import docker
from app.registry.inspection import RegistryError, inspect_archive
from app.training.runner import file_record, read_bounded
from app.transformations.schemas import TransformSpec


class InvalidTransformOutput(ExecutorError):
    """A completed output failed verification and cannot be registered."""


class TransformExecutor(Protocol):
    def image_identity(self) -> str: ...
    def start(self, identifier: UUID, spec: TransformSpec) -> None: ...
    def status(self, identifier: UUID, spec: TransformSpec) -> str: ...
    def output(self, identifier: UUID, spec: TransformSpec, stream: BinaryIO) -> None: ...
    def cleanup(self, identifier: UUID, spec: TransformSpec) -> None: ...


def name(identifier: UUID) -> str:
    return "kitchensoup-transform-" + str(identifier)


class LocalTransformExecutor:
    def __init__(
        self,
        root: Path,
        materializer: DeploymentMaterializer,
        image: str = "kitchensoup-model-transform:local",
    ) -> None:
        self.root = root.resolve()
        if any(c in str(self.root) for c in (",", "\n", "\r")):
            raise ExecutorError("Unsupported transformation workspace")
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.materializer, self.image = materializer, image

    def workspace(self, identifier: UUID) -> Path:
        path = self.root / str(identifier)
        if path.is_symlink():
            raise ExecutorError("Linked transformation workspace")
        return path

    def image_identity(self) -> str:
        result = docker(["image", "inspect", self.image, "--format", "{{.Id}}"])
        identity = result.stdout.strip()
        if result.returncode or not re.fullmatch(r"sha256:[0-9a-f]{64}", identity):
            raise ExecutorError("Build the pinned transformation image first")
        return identity

    def inspect(self, identifier: UUID, spec: TransformSpec) -> dict[str, object] | None:
        result = docker(["container", "inspect", name(identifier)])
        if result.returncode:
            if "No such" in result.stderr:
                return None
            raise ExecutorError("Transformation Docker inspection unavailable")
        try:
            info = json.loads(result.stdout)[0]
            if info["Config"]["Image"] != spec.image_digest or info["Config"]["Labels"].get(
                "kitchensoup.transformation"
            ) != str(identifier):
                raise ValueError
            state: dict[str, object] = info["State"]
            return state
        except (ValueError, KeyError, TypeError, IndexError):
            raise ExecutorError("Transformation container identity mismatch") from None

    def start(self, identifier: UUID, spec: TransformSpec) -> None:
        if os.getuid() == 0:
            raise ExecutorError("Run transformations as a non-root host user")
        workspace = self.workspace(identifier)
        workspace.mkdir(mode=0o700)
        inputs, output = workspace / "input", workspace / "output"
        inputs.mkdir(mode=0o700)
        output.mkdir(mode=0o700)
        self.materializer.materialize(
            DeploymentSpec(
                image_digest=spec.image_digest,
                base_source=spec.base_source,
                artifact_id=spec.artifact_id,
                artifact_sha256=spec.artifact_sha256,
                adapter=spec.operation == "merge",
                precision=spec.precision,
            ),
            inputs,
        )
        payload = spec.model_dump_json(by_alias=True).encode()
        (inputs / "transform.json").write_bytes(payload)
        records = [
            file_record(inputs, p).model_dump(mode="json")
            for folder in ("model", "adapter")
            for p in sorted((inputs / folder).rglob("*"))
            if p.is_file()
        ]
        (inputs / "inventory.json").write_text(json.dumps(records))
        args = [
            "create",
            "--pull",
            "never",
            "--name",
            name(identifier),
            "--label",
            "kitchensoup.transformation=" + str(identifier),
            "--gpus",
            "all",
            "--network",
            "none",
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
            f"{os.getuid()}:{os.getgid()}",
            "--mount",
            f"type=bind,src={inputs},dst=/input,readonly",
            "--mount",
            f"type=bind,src={output},dst=/output",
            spec.image_digest,
        ]
        if docker(args).returncode or docker(["start", name(identifier)]).returncode:
            raise ExecutorError("Transformation could not start")

    def status(self, identifier: UUID, spec: TransformSpec) -> str:
        state = self.inspect(identifier, spec)
        if state is None:
            return "LOST"
        if state.get("Running"):
            return "RUNNING"
        if state.get("Status") == "exited" and state.get("ExitCode") == 0:
            return "SUCCEEDED"
        return "FAILED"

    def output(self, identifier: UUID, spec: TransformSpec, stream: BinaryIO) -> None:
        if self.status(identifier, spec) != "SUCCEEDED":
            raise ExecutorError("Transformation has no verified successful output")
        try:
            self._output(identifier, spec, stream)
        except (ExecutorError, OSError, ValueError, RegistryError, KeyError, TypeError):
            raise InvalidTransformOutput("Transformation output verification failed") from None

    def _output(self, identifier: UUID, spec: TransformSpec, stream: BinaryIO) -> None:
        workspace = self.workspace(identifier)
        root = workspace / "output"
        expected = hashlib.sha256(
            read_bounded(workspace / "input/transform.json", 1024**2)
        ).hexdigest()
        result = json.loads(read_bounded(root / "result.json", 4 * 1024**2))
        if result.get("input_sha256") != expected:
            raise ExecutorError("Transformation output input mismatch")
        model = root / "model"
        if model.is_symlink() or not model.is_dir():
            raise ExecutorError("Missing transformation output")
        paths = sorted(model.rglob("*"))
        if len(paths) > 4096 or any(p.is_symlink() for p in paths):
            raise ExecutorError("Unsafe output inventory")
        config = json.loads(read_bounded(model / "config.json", 1024**2))
        if spec.operation == "awq" and not config.get("quantization_config"):
            raise ExecutorError("AWQ output has no quantization configuration")
        actual = [file_record(model, p) for p in paths if not p.is_dir()]
        if (
            [r.model_dump(mode="json") for r in actual] != result.get("files")
            or not actual
            or sum(r.size_bytes for r in actual) > 8 * 1024**3
        ):
            raise ExecutorError("Transformation output hash/inventory mismatch")
        with ZipFile(stream, "w", compression=ZIP_STORED) as archive:
            for record in actual:
                path = model / record.path
                # Recheck while copying; never follow an output link.
                if file_record(model, path) != record:
                    raise ExecutorError("Transformation output changed")
                archive.write(path, record.path)
        stream.seek(0)
        inspect_archive(stream, allow_awq=spec.operation == "awq")
        stream.seek(0)

    def cleanup(self, identifier: UUID, spec: TransformSpec) -> None:
        if self.inspect(identifier, spec) is not None:
            if docker(["rm", "--force", name(identifier)]).returncode:
                raise ExecutorError("Transformation cleanup unavailable")
        path = self.workspace(identifier)
        if path.exists():
            shutil.rmtree(path)
