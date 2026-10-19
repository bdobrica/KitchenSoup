"""Direct Soup execution in an owned Pod with verified SFTP output retention."""

import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from uuid import UUID

from app.executors.base import ExecutorError, InputMaterializer, JobStatus, LogChunk
from app.executors.local_docker import LocalDockerTrainingExecutor, container_name
from app.executors.ssh_docker import SshDockerTrainingExecutor
from app.targets.runpod_lifecycle import RunpodLifecycle
from app.targets.runpod_ssh import RunpodConnection
from app.training.runner import file_record, read_bounded, verify_bundle
from app.training.schemas import PlanPreview
from app.training.soup import RunnerInput, validate_precision


class RunpodTrainingExecutor(RunpodConnection, SshDockerTrainingExecutor):
    def __init__(
        self,
        root: Path,
        materializer: InputMaterializer,
        lifecycle: RunpodLifecycle,
        *,
        known_hosts: Path,
        secret_directory: Path,
        env_names: set[str],
    ) -> None:
        LocalDockerTrainingExecutor.__init__(self, root, materializer)
        self.lifecycle = lifecycle
        self.known_hosts, self.secret_directory, self.env_names = (
            known_hosts,
            secret_directory,
            env_names,
        )

    def image_identity(self) -> str:
        return self.lifecycle.config.training_image.rsplit("@", 1)[1]

    def owner(self, external_id: str, image: str) -> UUID:
        owner = UUID(self.workspace(external_id).name)
        if image != self.image_identity():
            raise ExecutorError("Retained image differs from immutable Runpod target")
        return owner

    def connection_workspace(self, owner: UUID) -> Path:
        return self.workspace(container_name(owner))

    def remote(self, workspace: Path) -> str:
        return "/home/runner/jobs/" + str(UUID(workspace.name))

    def submit(self, run_id: UUID, plan: PlanPreview, image: str) -> str:
        name = container_name(run_id)
        self.owner(name, image)
        with self.lock(name) as workspace:
            if workspace.exists():
                raise ExecutorError("Runpod workspace already exists; reconcile or clone")
            workspace.mkdir(mode=0o700)
            (workspace / "input").mkdir(mode=0o700)
            (workspace / "output").mkdir(mode=0o700)
            try:
                inputs = workspace / "input"
                self.materializer.materialize(run_id, plan, inputs)
                run = RunnerInput.model_validate_json(
                    read_bounded(inputs / "run.json", 8 * 1024**2)
                )
                if run.run_id != run_id or run.plan != plan:
                    raise ExecutorError("Materialized Runpod input does not match reviewed run")
                verify_bundle(inputs, run)
                launch = {
                    "image": image,
                    "input_sha256": file_record(inputs, inputs / "run.json").sha256,
                    "plan_sha256": plan.sha256,
                }
                (workspace / "launch.json").write_text(json.dumps(launch))
                self.lifecycle.create(run_id)
                resource = self.lifecycle.resource(run_id)
                assert resource is not None
                deadline = time.monotonic() + min(
                    600, max(0, (resource.expires_at - datetime.now(UTC)).total_seconds())
                )
                while True:
                    if self.lifecycle.training_cancel_requested(run_id):
                        self.finish(run_id, JobStatus(state="CANCELED"))
                        return name
                    pod = self.lifecycle.inspect(run_id)
                    if pod is None:
                        raise ExecutorError("Runpod disappeared during preparation")
                    try:
                        self.connect_pod(pod)
                        self.provisioned_trust(run_id)
                        probe = self.transport.command(
                            ["python", "/runner/runpod_process.py", "probe"]
                        )
                        if probe.returncode:
                            raise ExecutorError("Runpod GPU preflight failed")
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
                ):
                    raise ExecutorError("Runpod actual CUDA/VRAM does not meet target requirements")
                validate_precision(plan.resolved.training.precision, hardware["bf16"])
                (workspace / "hardware.json").write_text(
                    json.dumps(
                        {
                            **hardware,
                            "requested_precision": plan.resolved.training.precision,
                            "effective_precision": "bf16" if hardware["bf16"] else "fp16",
                        }
                    )
                )
                row = self.lifecycle.resource(run_id)
                assert row is not None
                self.transport.transfer_timeout = max(
                    1, int((row.expires_at - datetime.now(UTC)).total_seconds())
                )
                self.transport.upload(workspace, self.remote(workspace))
                remaining = int((row.expires_at - datetime.now(UTC)).total_seconds())
                if remaining <= 0:
                    raise ExecutorError("Runpod preparation exceeded runtime deadline")
                (workspace / "launch-intent").touch()
                result = self.transport.command(
                    [
                        "python",
                        "/runner/runpod_process.py",
                        "launch",
                        self.remote(workspace),
                        image,
                        str(remaining),
                        launch["input_sha256"],
                    ]
                )
                if result.returncode:
                    raise ExecutorError("Runpod input verification or launch failed")
            except Exception:
                row = self.lifecycle.resource(run_id)
                if row is not None:
                    if not (workspace / "launch-intent").exists():
                        # Bootstrap never starts Soup by itself. Before launch intent
                        # a preparation failure can terminate through the API even
                        # when SSH or the image never becomes ready.
                        self.finish(
                            run_id, JobStatus(state="FAILED", message="Runpod preparation failed")
                        )
                    else:
                        # A lost launch response is recovered, never launched twice.
                        self.lifecycle.warning(
                            run_id, "Preparation interrupted; reconcile execution and cleanup"
                        )
                raise ExecutorError(
                    "Runpod preparation interrupted; worker will reconcile ownership"
                ) from None
        return name

    def inspect(self, external_id: str, image: str) -> dict[str, Any] | None:
        owner = self.owner(external_id, image)
        row = self.lifecycle.resource(owner)
        if row is None:
            return None
        if row.outcome is not None:
            outcome = JobStatus.model_validate(row.outcome)
            return {
                "Running": False,
                "ExitCode": outcome.exit_code or (0 if outcome.state == "SUCCEEDED" else 1),
            }
        pod = self.lifecycle.inspect(owner)
        if pod is None:
            return None
        self.connect_pod(pod)
        self.provisioned_trust(owner)
        remote = self.remote(self.workspace(external_id))
        data = self.transport.read(remote + "/exit.json", 1024, missing_ok=True)
        if data:
            try:
                code = json.loads(data)["exit_code"]
                if type(code) is not int or not -255 <= code <= 255:
                    raise ValueError
                return {"Running": False, "ExitCode": code}
            except (ValueError, KeyError, TypeError):
                raise ExecutorError("Runpod process status is invalid") from None
        started = self.transport.read(remote + "/started", 128, missing_ok=True)
        return {"Running": bool(started), "Status": "running" if started else "created"}

    def status(self, external_id: str, image: str) -> JobStatus:
        owner = self.owner(external_id, image)
        row = self.lifecycle.resource(owner)
        if row is None:
            return JobStatus(state="MISSING")
        if row.outcome is not None:
            return JobStatus.model_validate(row.outcome)
        if datetime.now(UTC) >= row.expires_at:
            return self.finish(
                owner, JobStatus(state="FAILED", message="Runpod runtime deadline exceeded")
            )
        if not (self.workspace(external_id) / "launch-intent").exists():
            return JobStatus(state="PREPARING")
        state = self.inspect(external_id, image)
        if state is None:
            return self.finish(
                owner, JobStatus(state="FAILED", message="Owned Runpod Pod disappeared")
            )
        if state.get("Running"):
            return JobStatus(state="RUNNING")
        if state.get("Status") == "created":
            return JobStatus(state="PREPARING")
        # Do not re-observe a running process through the common verifier: it
        # could complete between observations, before local collection occurs.
        self.collect(external_id)
        # Common verifier checks run/plan/input/image identity and every output hash.
        result = LocalDockerTrainingExecutor.status(self, external_id, image)
        if result.state in ("SUCCEEDED", "FAILED", "CANCELED"):
            for stream in ("stdout", "stderr"):
                data = self.transport.read(
                    self.remote(self.workspace(external_id)) + "/output/" + stream + ".log",
                    8 * 1024**2,
                    missing_ok=True,
                )
                (self.workspace(external_id) / "output" / (stream + ".log")).write_bytes(
                    data[: 8 * 1024**2]
                )
            return self.finish(owner, result)
        return result

    def finish(self, owner: UUID, result: JobStatus) -> JobStatus:
        self.lifecycle.pending(owner, result)
        try:
            self.lifecycle.cleanup(owner)
        except ExecutorError:
            pass  # Durable pending cleanup is independently retried, even for terminal runs.
        row = self.lifecycle.resource(owner)
        assert row is not None and row.outcome is not None
        return JobStatus.model_validate(row.outcome)

    def cancel(self, external_id: str, image: str) -> JobStatus:
        owner = self.owner(external_id, image)
        row = self.lifecycle.resource(owner)
        if row is None:
            return JobStatus(state="MISSING")
        if row.outcome is not None:
            return JobStatus.model_validate(row.outcome)
        # Termination is authoritative cancellation; it does not require working SSH.
        return self.finish(owner, JobStatus(state="CANCELED"))

    def cleanup(self, external_id: str, image: str) -> None:
        owner = self.owner(external_id, image)
        row = self.lifecycle.resource(owner)
        if row is not None and row.outcome is None:
            raise ExecutorError("Cancel the run before cleanup")
        self.lifecycle.cleanup(owner)

    def logs(self, external_id: str, stream: Literal["stdout", "stderr"], cursor: int) -> LogChunk:
        owner = UUID(self.workspace(external_id).name)
        row = self.lifecycle.resource(owner)
        if row is None or row.outcome is not None:
            return LocalDockerTrainingExecutor.logs(self, external_id, stream, cursor)
        pod = self.lifecycle.inspect(owner)
        if pod is None:
            return LocalDockerTrainingExecutor.logs(self, external_id, stream, cursor)
        self.connect_pod(pod)
        self.provisioned_trust(owner)
        return SshDockerTrainingExecutor.logs(self, external_id, stream, cursor)
