"""SSH training adapter reuses local manifest verification and container isolation."""

import subprocess
from pathlib import Path, PurePosixPath
from typing import Literal
from uuid import UUID

from pydantic import ValidationError

from app.executors.base import ExecutorError, InputMaterializer, JobStatus, LogChunk
from app.executors.local_docker import LocalDockerTrainingExecutor
from app.targets.ssh import SshTransport
from app.training.runner import file_record
from app.training.soup import RunnerManifest


class SshDockerTrainingExecutor(LocalDockerTrainingExecutor):
    def __init__(
        self, root: Path, materializer: InputMaterializer, transport: SshTransport
    ) -> None:
        # Explicit constructor arguments keep host resource settings local to the adapter.
        super().__init__(root, materializer, image=transport.config.training_image)
        self.transport = transport

    def identity(self) -> tuple[int, int]:
        return self.transport.identity()

    def remote(self, workspace: Path) -> str:
        return self.transport.config.workspace + "/training/" + str(UUID(workspace.name))

    def docker(self, arguments: list[str]) -> subprocess.CompletedProcess[str]:
        args = list(arguments)
        if args[0] == "create":
            name = args[args.index("--name") + 1]
            local = self.workspace(name)
            remote = self.remote(local)
            self.transport.upload(local, remote)
            args = [arg.replace("src=" + str(local) + "/", "src=" + remote + "/") for arg in args]
        return self.transport.docker(args)

    def status(self, external_id: str, image: str) -> JobStatus:
        state = self.inspect(external_id, image)
        if (
            state is not None
            and not state.get("Running")
            and state.get("Status") != "created"
            and not (self.workspace(external_id) / "canceled").exists()
        ):
            self.collect(external_id)
        return super().status(external_id, image)

    def collect(self, external_id: str) -> None:
        workspace = self.workspace(external_id)
        root = workspace / "output"
        remote = self.remote(workspace) + "/output/"
        data = self.transport.read(remote + "output-manifest.json", 8 * 1024**2, missing_ok=True)
        if not data:
            return
        if len(data) > 8 * 1024**2:
            raise ExecutorError("Remote output manifest exceeds its limit")
        try:
            manifest = RunnerManifest.model_validate_json(data)
        except ValidationError:
            # Retain the invalid manifest for the common verifier to classify as failed.
            (root / "output-manifest.json").write_bytes(data)
            return
        files = manifest.adapters
        if (
            len(files) > 4096
            or len({item.path for item in files}) != len(files)
            or sum(item.size_bytes for item in files) > 8 * 1024**3
        ):
            raise ExecutorError("Remote adapter inventory exceeds its limit")
        for item in files:
            path = PurePosixPath(item.path)
            if (
                not item.path.startswith("adapter/")
                or path.is_absolute()
                or ".." in path.parts
                or str(path) != item.path
            ):
                raise ExecutorError("Invalid remote adapter path")
            dest = root / item.path
            if any(p.is_symlink() for p in [dest, *dest.parents]):
                raise ExecutorError("Local adapter staging cannot contain links")
            if not dest.exists() or file_record(root, dest) != item:
                self.transport.download(remote + item.path, dest, item.size_bytes)
        (root / "output-manifest.json").write_bytes(data)

    def logs(self, external_id: str, stream: Literal["stdout", "stderr"], cursor: int) -> LogChunk:
        if stream not in ("stdout", "stderr") or not 0 <= cursor <= 8 * 1024**2:
            raise ExecutorError("Invalid log stream or cursor")
        workspace = self.workspace(external_id)
        if (workspace / "remote-cleaned").exists():
            return super().logs(external_id, stream, cursor)
        data = self.transport.read(
            self.remote(workspace) + "/output/" + stream + ".log",
            min(65536, 8 * 1024**2 - cursor),
            cursor,
            missing_ok=True,
        )
        # Logs are an intentionally partial read; cap even if the remote file keeps growing.
        data = data[: min(65536, 8 * 1024**2 - cursor)]
        return LogChunk(text=data.decode(errors="replace"), cursor=cursor + len(data))

    def cleanup(self, external_id: str, image: str) -> None:
        workspace = self.workspace(external_id)
        if not (workspace / "remote-cleaned").exists() and workspace.exists():
            status = self.status(external_id, image)
            if status.state in ("RUNNING", "PREPARING"):
                raise ExecutorError("Cancel the run before cleaning up its container")
            for stream in ("stdout", "stderr"):
                data = self.transport.read(
                    self.remote(workspace) + "/output/" + stream + ".log",
                    8 * 1024**2,
                    missing_ok=True,
                )
                (workspace / "output" / (stream + ".log")).write_bytes(data[: 8 * 1024**2])
        super().cleanup(external_id, image)
        self.transport.remove(self.remote(workspace))
        if workspace.exists():
            (workspace / "remote-cleaned").touch()
