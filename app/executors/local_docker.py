"""Host-local Docker CLI adapter. No shell, remote Docker context or runner credentials."""

import fcntl
import json
import os
import re
import subprocess
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Literal
from uuid import UUID

from app.executors.base import ExecutorError, InputMaterializer, JobStatus, LogChunk
from app.training.runner import file_record, read_bounded
from app.training.schemas import PlanPreview
from app.training.soup import RunnerInput, RunnerManifest

PREFIX = "kitchensoup-training-"


def container_name(run_id: UUID) -> str:
    return PREFIX + str(run_id)


def docker(arguments: list[str]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ["docker", "--host", "unix:///var/run/docker.sock", *arguments],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=60,
            env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": "/nonexistent"},
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise ExecutorError("Local Docker is unavailable or timed out") from None


class LocalDockerTrainingExecutor:
    def __init__(
        self,
        root: Path,
        materializer: InputMaterializer,
        *,
        image: str = "kitchensoup-soup-trainer:local",
        memory_gib: int = 16,
        cpus: int = 4,
        timeout: int = 86400,
    ) -> None:
        self.root = root.resolve()
        if any(c in str(self.root) for c in (",", "\n", "\r")):
            raise ExecutorError("Training workspace path cannot contain commas or newlines")
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.materializer, self.image = materializer, image
        self.memory_gib, self.cpus, self.timeout = memory_gib, cpus, timeout

    def workspace(self, external_id: str) -> Path:
        if not external_id.startswith(PREFIX):
            raise ExecutorError("Invalid local training identifier")
        try:
            identifier = UUID(external_id[len(PREFIX) :])
        except ValueError:
            raise ExecutorError("Invalid local training identifier") from None
        if container_name(identifier) != external_id:
            raise ExecutorError("Invalid local training identifier")
        path = self.root / str(identifier)
        if path.is_symlink():
            raise ExecutorError("Training workspace cannot be a symlink")
        return path

    @contextmanager
    def lock(self, external_id: str) -> Iterator[Path]:
        path = self.workspace(external_id)
        with (self.root / (path.name + ".lock")).open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise ExecutorError(
                    "Training operation already in progress; refresh shortly"
                ) from None
            try:
                yield path
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def image_identity(self) -> str:
        result = docker(["image", "inspect", self.image, "--format", "{{.Id}}"])
        identity = result.stdout.strip()
        if result.returncode or not re.fullmatch(r"sha256:[0-9a-f]{64}", identity):
            raise ExecutorError("Build the pinned Soup trainer image before submitting")
        return identity

    def submit(self, run_id: UUID, plan: PlanPreview, image: str) -> str:
        if os.getuid() == 0:
            raise ExecutorError("Run the local training application as a non-root host user")
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", image):
            raise ExecutorError("A content-addressed image is required")
        name = container_name(run_id)
        with self.lock(name) as workspace:
            if workspace.exists():
                raise ExecutorError("Run workspace already exists; create a new run")
            workspace.mkdir(mode=0o700)
            inputs, output = workspace / "input", workspace / "output"
            inputs.mkdir(mode=0o700)
            output.mkdir(mode=0o700)
            try:
                self.materializer.materialize(run_id, plan, inputs)
                run = RunnerInput.model_validate_json(
                    read_bounded(inputs / "run.json", 8 * 1024**2)
                )
                if run.run_id != run_id or run.plan != plan:
                    raise ExecutorError("Materialized run disagrees with the submission")
                input_sha = file_record(inputs, inputs / "run.json").sha256
                (workspace / "launch.json").write_text(
                    json.dumps(
                        {
                            "image": image,
                            "input_sha256": input_sha,
                            "plan_sha256": plan.sha256,
                        }
                    )
                )
                args = [
                    "create",
                    "--pull",
                    "never",
                    "--name",
                    name,
                    "--label",
                    "kitchensoup.run=" + str(run_id),
                    "--label",
                    "kitchensoup.input=" + input_sha,
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
                    "256",
                    "--memory",
                    f"{self.memory_gib}g",
                    "--memory-swap",
                    f"{self.memory_gib}g",
                    "--cpus",
                    str(self.cpus),
                    "--shm-size",
                    "256m",
                    "--tmpfs",
                    "/tmp:rw,noexec,nosuid,size=512m",
                    "--ulimit",
                    "fsize=1073741824:1073741824",
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
                    image,
                    "--image-digest",
                    image,
                    "--timeout",
                    str(self.timeout),
                ]
                (workspace / "command.json").write_text(json.dumps(args))
                if docker(args).returncode:
                    raise ExecutorError(
                        "Docker could not create container; check GPU runtime and resources"
                    )
                if docker(["start", name]).returncode:
                    raise ExecutorError(
                        "Docker could not start training; check GPU runtime and resources"
                    )
            except ExecutorError:
                raise
            except Exception:
                raise ExecutorError(
                    "Training input preparation failed; verify registered inputs and available disk"
                ) from None
        return name

    def inspect(self, external_id: str, image: str) -> dict[str, Any] | None:
        workspace = self.workspace(external_id)
        result = docker(["container", "inspect", external_id])
        if result.returncode:
            if "No such container" in result.stderr or "No such object" in result.stderr:
                return None
            raise ExecutorError("Docker inspection failed; status is unknown")
        try:
            info = json.loads(result.stdout)[0]
            launch = json.loads(read_bounded(workspace / "launch.json", 4096))
            labels = info["Config"]["Labels"]
            if (
                info["Config"]["Image"] != image
                or launch["image"] != image
                or labels.get("kitchensoup.run") != workspace.name
                or labels.get("kitchensoup.input") != launch["input_sha256"]
            ):
                raise ValueError
            return dict(info["State"])
        except (KeyError, ValueError, OSError, IndexError, TypeError):
            raise ExecutorError("Container identity does not match this training run") from None

    def status(self, external_id: str, image: str) -> JobStatus:
        workspace = self.workspace(external_id)
        state = self.inspect(external_id, image)
        if state is None:
            return JobStatus(
                state="MISSING", message="Container is absent; retained run status is unchanged"
            )
        if state.get("Running"):
            return JobStatus(state="RUNNING")
        code = int(state.get("ExitCode", 1))
        if (workspace / "canceled").exists():
            return JobStatus(state="CANCELED", exit_code=code)
        if state.get("Status") == "created":
            return JobStatus(state="PREPARING")
        try:
            manifest = RunnerManifest.model_validate_json(
                read_bounded(workspace / "output" / "output-manifest.json", 8 * 1024**2)
            )
            launch = json.loads(read_bounded(workspace / "launch.json", 4096))
            if (
                code != 0
                or manifest.status != "succeeded"
                or manifest.exit_code != 0
                or manifest.soup_exit_code != 0
                or str(manifest.run_id) != workspace.name
                or manifest.image_digest != image
                or manifest.input_sha256 != launch["input_sha256"]
                or manifest.plan_sha256 != launch["plan_sha256"]
                or manifest.stage != "complete"
                or not {"adapter/adapter_config.json", "adapter/adapter_model.safetensors"}
                <= {item.path for item in manifest.adapters}
            ):
                raise ValueError
            for item in manifest.adapters:
                path = workspace / "output" / item.path
                if any(p.is_symlink() for p in [path, *path.parents]):
                    raise ValueError
                if file_record(workspace / "output", path) != item:
                    raise ValueError
            return JobStatus(state="SUCCEEDED", exit_code=code)
        except Exception:
            return JobStatus(
                state="FAILED",
                exit_code=code,
                message="Runner failed or a verified success manifest is unavailable",
            )

    def logs(self, external_id: str, stream: Literal["stdout", "stderr"], cursor: int) -> LogChunk:
        if stream not in ("stdout", "stderr") or not 0 <= cursor <= 8 * 1024**2:
            raise ExecutorError("Invalid log stream or cursor")
        path = self.workspace(external_id) / "output" / (stream + ".log")
        try:
            # O_NOFOLLOW and nonblocking protect the host from runner-created links/FIFOs.
            import stat

            descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(descriptor, "rb") as reader:
                if not stat.S_ISREG(os.fstat(reader.fileno()).st_mode):
                    raise ExecutorError("Training log is not a regular file")
                reader.seek(cursor)
                data = reader.read(min(65536, 8 * 1024**2 - cursor))
        except FileNotFoundError:
            data = b""
        except OSError:
            raise ExecutorError("Training log is unavailable") from None
        return LogChunk(text=data.decode("utf-8", errors="replace"), cursor=cursor + len(data))

    def cancel(self, external_id: str, image: str) -> JobStatus:
        with self.lock(external_id) as workspace:
            status = self.status(external_id, image)
            if status.state in ("RUNNING", "PREPARING"):
                (workspace / "canceled").touch()
                if docker(["stop", "--time", "10", external_id]).returncode:
                    raise ExecutorError("Docker cancellation failed; refresh before retrying")
                return self.status(external_id, image)
            return status

    def cleanup(self, external_id: str, image: str) -> None:
        with self.lock(external_id):
            state = self.inspect(external_id, image)
            if state is None:
                return
            if state.get("Running") or (
                state.get("Status") == "created"
                and not (self.workspace(external_id) / "canceled").exists()
            ):
                raise ExecutorError("Cancel the run before cleaning up its container")
            if docker(["rm", external_id]).returncode:
                raise ExecutorError("Docker container cleanup failed")
