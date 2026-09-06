"""Real Docker lifecycle smoke; --gpu exercises the production BF16 launch unchanged."""

import argparse
import json
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from unittest.mock import patch
from uuid import UUID, uuid4

from app.executors import local_docker
from app.executors.local_docker import LocalDockerTrainingExecutor, container_name, docker
from app.training.schemas import PlanPreview
from app.training.soup import RunnerInput

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument("--gpu", action="store_true")
args = parser.parse_args()


class FixtureMaterializer:
    def __init__(self, bundle: Path) -> None:
        self.bundle = bundle

    def materialize(self, run_id: UUID, plan: PlanPreview, destination: Path) -> None:
        shutil.copytree(self.bundle, destination, dirs_exist_ok=True)
        run = RunnerInput.model_validate_json((destination / "run.json").read_bytes())
        run.run_id = run_id
        assert run.plan == plan
        (destination / "run.json").write_text(run.model_dump_json(by_alias=True))


with tempfile.TemporaryDirectory(prefix="kitchensoup-executor-smoke-") as directory:
    root = Path(directory)
    bundle = root / "bundle"
    bundle.mkdir()
    executor = LocalDockerTrainingExecutor(
        root / "jobs", FixtureMaterializer(bundle), memory_gib=4, timeout=180
    )
    image = executor.image_identity()
    fixture_name = "kitchensoup-fixture-" + uuid4().hex
    runs: list[UUID] = []
    try:
        result = docker(
            [
                "run",
                "--rm",
                "--name",
                fixture_name,
                "--network",
                "none",
                "--read-only",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges",
                "--memory",
                "4g",
                "--pids-limit",
                "256",
                "--tmpfs",
                "/tmp:size=512m",
                "--user",
                f"{os.getuid()}:{os.getgid()}",
                "-e",
                "HOME=/tmp",
                "-e",
                "OMP_NUM_THREADS=2",
                "--mount",
                f"type=bind,src={bundle},dst=/bundle",
                "--mount",
                f"type=bind,src={ROOT / 'scripts/soup_smoke_fixture.py'},dst=/fixture.py,readonly",
                "--mount",
                f"type=bind,src={ROOT / 'tests/fixtures/training/conversations.jsonl'},"
                "dst=/fixture.jsonl,readonly",
                "--entrypoint",
                "python",
                image,
                "/fixture.py",
                "/bundle",
            ]
        )
        if result.returncode:
            raise RuntimeError("Synthetic model generation failed")
        plan = RunnerInput.model_validate_json((bundle / "run.json").read_bytes()).plan

        def validation_command(command: list[str]) -> subprocess.CompletedProcess[str]:
            command = list(command)
            if command[0] == "create":
                index = command.index("--gpus")
                del command[index : index + 2]
                command.append("--validate-only")
            return docker(command)

        identifier = uuid4()
        runs.append(identifier)
        # The CPU test override exists only in this synthetic test harness.
        with patch.object(local_docker, "docker", docker if args.gpu else validation_command):
            external = executor.submit(identifier, plan, image)
        deadline = time.monotonic() + 240
        while time.monotonic() < deadline:
            status = executor.status(external, image)
            if status.state not in ("RUNNING", "PREPARING"):
                break
            time.sleep(1)
        else:
            raise RuntimeError("Training smoke timed out")
        manifest = json.loads(
            (executor.workspace(external) / "output/output-manifest.json").read_text()
        )
        assert manifest["status"] == ("succeeded" if args.gpu else "validated"), manifest["error"]
        assert status.state == ("SUCCEEDED" if args.gpu else "FAILED")
        assert executor.logs(external, "stdout", 0).cursor > 0
        executor.cleanup(external, image)
        assert executor.status(external, image).state == "MISSING"
        assert executor.logs(external, "stdout", 0).cursor > 0

        def sleeping_command(command: list[str]) -> subprocess.CompletedProcess[str]:
            command = list(command)
            if command[0] == "create":
                index = command.index("--gpus")
                del command[index : index + 2]
                index = command.index(image)
                command = command[:index] + [
                    "--entrypoint",
                    "python",
                    image,
                    "-c",
                    "import time; from pathlib import Path; "
                    'Path("/output/stdout.log").write_text("ready\\n"); time.sleep(120)',
                ]
            return docker(command)

        identifier = uuid4()
        runs.append(identifier)
        with patch.object(local_docker, "docker", sleeping_command):
            external = executor.submit(identifier, plan, image)
        assert executor.status(external, image).state == "RUNNING"
        assert executor.cancel(external, image).state == "CANCELED"
        executor.cleanup(external, image)
        print(
            "Local executor smoke passed: immutable bundle, pinned image, "
            "status, logs, cancel and cleanup."
            + (
                " Actual BF16 training passed."
                if args.gpu
                else " CPU validation only; GPU training not claimed."
            )
        )
    finally:
        for name in [fixture_name, *(container_name(run) for run in runs)]:
            docker(["rm", "--force", name])
