"""Explicit local GPU authority using the operator's pinned trainer runtime."""

import hashlib
import json
import os
import shutil
import tempfile
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from uuid import UUID

from app.evaluation.schemas import InferenceInput, InferenceOutput
from app.executors.base import ExecutorError
from app.executors.local_docker import LocalDockerTrainingExecutor, docker
from app.executors.materialize import RegisteredInputMaterializer
from app.storage.archives import ArchiveLimits, extract_zip
from app.training.runner import file_record, read_bounded
from app.training.schemas import PlanPreview


class LocalInferenceRunner:
    def __init__(
        self, executor: LocalDockerTrainingExecutor, materializer: RegisteredInputMaterializer
    ) -> None:
        self.executor, self.materializer = executor, materializer

    @staticmethod
    def name(task_id: UUID) -> str:
        return "kitchensoup-evaluation-" + str(task_id)

    def stop(self, task_id: UUID, image: str) -> None:
        name = self.name(task_id)
        found = docker(["container", "inspect", name])
        if found.returncode:
            if "No such" in found.stderr:
                return
            raise ExecutorError("Evaluation Docker inspection unavailable")
        try:
            info = json.loads(found.stdout)[0]
            if info["Config"]["Image"] != image or info["Config"]["Labels"].get(
                "kitchensoup.evaluation"
            ) != str(task_id):
                raise ValueError
        except (ValueError, KeyError, TypeError, IndexError):
            raise ExecutorError("Evaluation container identity mismatch") from None
        if docker(["rm", "--force", name]).returncode:
            raise ExecutorError("Evaluation container cleanup unavailable")

    @contextmanager
    def prepare(
        self,
        task_id: UUID,
        run_id: UUID,
        plan: PlanPreview,
        image: str,
        adapter_id: UUID | None,
    ) -> Iterator[Callable[[str, bool], str]]:
        if os.getuid() == 0:
            raise ExecutorError("Run local evaluation as a non-root host user")
        # Private temporary inputs are independent of retained trainer workspaces.
        with tempfile.TemporaryDirectory(prefix="evaluation-", dir=self.executor.root) as temp:
            workspace = Path(temp)
            inputs, output = workspace / "input", workspace / "output"
            inputs.mkdir(mode=0o700)
            output.mkdir(mode=0o700)
            (inputs / "adapter").mkdir(mode=0o700)
            try:
                self.materializer.materialize(run_id, plan, inputs)
                if adapter_id is not None:
                    artifact = self.materializer.artifacts.get(adapter_id)
                    archive = workspace / "adapter.zip"
                    self.materializer.artifact(adapter_id, artifact.sha256, archive, 9 * 1024**3)
                    with (
                        archive.open("rb") as reader,
                        extract_zip(reader, ArchiveLimits(max_expanded_bytes=9 * 1024**3)) as root,
                    ):
                        shutil.copytree(root / "adapter", workspace / "adapter")

            except Exception:
                raise ExecutorError(
                    "Evaluation input preparation failed; "
                    "verify registered inputs, network and disk"
                ) from None

            def generate(prompt: str, fine_tuned: bool) -> str:
                output.joinpath("response.json").unlink(missing_ok=True)
                return self._generate(
                    task_id,
                    image,
                    prompt,
                    inputs,
                    output,
                    workspace / "adapter" if fine_tuned else None,
                )

            yield generate

    def _generate(
        self,
        task_id: UUID,
        image: str,
        prompt: str,
        inputs: Path,
        output: Path,
        adapter: Path | None,
    ) -> str:
        inventory = (
            [file_record(adapter, path) for path in sorted(adapter.rglob("*")) if path.is_file()]
            if adapter
            else []
        )
        request = InferenceInput(
            run_sha256=hashlib.sha256(read_bounded(inputs / "run.json", 8 * 1024**2)).hexdigest(),
            prompt=prompt,
            adapter_files=inventory,
        )
        payload = request.model_dump_json(by_alias=True).encode()
        (inputs / "inference.json").write_bytes(payload)
        name = self.name(task_id)
        args = [
            "create",
            "--pull",
            "never",
            "--name",
            name,
            "--label",
            "kitchensoup.evaluation=" + str(task_id),
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
            f"{self.executor.memory_gib}g",
            "--memory-swap",
            f"{self.executor.memory_gib}g",
            "--cpus",
            str(self.executor.cpus),
            "--shm-size",
            "256m",
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,size=512m",
            "--ulimit",
            "fsize=1048576:1048576",
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
            "--env",
            "HF_HOME=/tmp/hf",
            "--entrypoint",
            "python",
            image,
            "-m",
            "app.evaluation.runner",
        ]
        if adapter is not None:
            args[args.index("--env") : args.index("--env")] = [
                "--mount",
                f"type=bind,src={adapter},dst=/input/adapter,readonly",
            ]
        try:
            if docker(args).returncode or docker(["start", name]).returncode:
                raise ExecutorError(
                    "Evaluation container could not start; rebuild the trainer image"
                )
            deadline = time.monotonic() + 600
            while True:
                info = docker(["container", "inspect", name])
                if info.returncode:
                    raise ExecutorError("Evaluation status unavailable")
                state = json.loads(info.stdout)[0]["State"]
                if not state.get("Running"):
                    if state.get("ExitCode") != 0:
                        raise ExecutorError(
                            "Evaluation failed; verify context, model and GPU capacity"
                        )
                    break
                if time.monotonic() >= deadline:
                    raise ExecutorError("Evaluation exceeded its ten minute limit")
                time.sleep(1)
            answer = InferenceOutput.model_validate_json(
                read_bounded(output / "response.json", 1024**2)
            )
            if answer.input_sha256 != hashlib.sha256(payload).hexdigest():
                raise ExecutorError("Evaluation response input mismatch")
            return answer.response
        finally:
            self.stop(task_id, image)
