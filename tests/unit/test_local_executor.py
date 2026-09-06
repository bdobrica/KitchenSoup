import json
import shutil
import subprocess
from pathlib import Path
from uuid import UUID

import pytest

from app.executors import local_docker
from app.executors.base import ExecutorError
from app.executors.local_docker import LocalDockerTrainingExecutor, container_name
from app.training.runner import execute
from app.training.schemas import PlanPreview
from app.training.soup import RunnerInput
from tests.unit.test_soup_runner import bundle as runner_bundle

bundle = runner_bundle

IMAGE = "sha256:" + "a" * 64


class BundleMaterializer:
    def __init__(self, root: Path) -> None:
        self.root = root

    def materialize(self, run_id: UUID, plan: PlanPreview, destination: Path) -> None:
        shutil.copytree(self.root, destination, dirs_exist_ok=True)


class DockerFixture:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.state = "created"
        self.labels: dict[str, str] = {}
        self.image = IMAGE
        self.name = ""
        self.missing = True
        self.unavailable = False

    def __call__(self, args: list[str]) -> subprocess.CompletedProcess[str]:
        self.calls.append(args)
        if args[:2] == ["image", "inspect"]:
            return subprocess.CompletedProcess(args, 0, IMAGE, "")
        if args[0] == "create":
            self.missing = False
            self.name = args[args.index("--name") + 1]
            self.labels = dict(
                args[i + 1].split("=", 1) for i, v in enumerate(args) if v == "--label"
            )
        if args[0] == "start":
            self.state = "running"
        if args[0] == "stop":
            self.state = "exited"
        if args[0] == "rm":
            self.missing = True
        if args[:2] == ["container", "inspect"]:
            if self.missing or self.unavailable or args[2] != self.name:
                return subprocess.CompletedProcess(
                    args, 1, "", "unavailable" if self.unavailable else "No such container"
                )
            return subprocess.CompletedProcess(
                args,
                0,
                json.dumps(
                    [
                        {
                            "Config": {"Image": self.image, "Labels": self.labels},
                            "State": {
                                "Running": self.state == "running",
                                "Status": self.state,
                                "ExitCode": 0,
                            },
                        }
                    ]
                ),
                "",
            )
        return subprocess.CompletedProcess(args, 0, "fixture", "")


def test_lifecycle_is_confined_and_retains_logs(
    bundle: tuple[Path, RunnerInput],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, run = bundle
    backend = DockerFixture()
    monkeypatch.setattr(local_docker, "docker", backend)
    executor = LocalDockerTrainingExecutor(tmp_path / "jobs", BundleMaterializer(root))
    assert executor.image_identity() == IMAGE
    external = executor.submit(run.run_id, run.plan, IMAGE)
    command = next(c for c in backend.calls if c[0] == "create")
    assert external == container_name(run.run_id)
    assert command[command.index("--gpus") + 1] == "all"
    assert command[command.index("--network") + 1] == "none"
    assert "--read-only" in command and "--cap-drop" in command
    assert all("docker.sock" not in c for c in command)
    assert command[-5:] == [IMAGE, "--image-digest", IMAGE, "--timeout", "86400"]
    assert executor.status(external, IMAGE).state == "RUNNING"
    with pytest.raises(ExecutorError, match="Cancel"):
        executor.cleanup(external, IMAGE)
    with pytest.raises(ExecutorError, match="already exists"):
        executor.submit(run.run_id, run.plan, IMAGE)
    output = executor.workspace(external) / "output"
    (output / "stdout.log").write_text("hello\nworld\n")
    assert executor.logs(external, "stdout", 6).text == "world\n"
    assert executor.cancel(external, IMAGE).state == "CANCELED"
    assert executor.cancel(external, IMAGE).state == "CANCELED"
    executor.cleanup(external, IMAGE)
    executor.cleanup(external, IMAGE)
    assert executor.status(external, IMAGE).state == "MISSING"
    assert executor.logs(external, "stdout", 0).text == "hello\nworld\n"
    with pytest.raises(ExecutorError):
        executor.workspace("../../etc")


def test_exit_zero_is_not_success_and_foreign_container_is_not_canceled(
    bundle: tuple[Path, RunnerInput],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, run = bundle
    backend = DockerFixture()
    monkeypatch.setattr(local_docker, "docker", backend)
    executor = LocalDockerTrainingExecutor(tmp_path / "jobs", BundleMaterializer(root))
    external = executor.submit(run.run_id, run.plan, IMAGE)
    backend.state = "exited"
    assert executor.status(external, IMAGE).state == "FAILED"
    backend.image = "sha256:" + "b" * 64
    with pytest.raises(ExecutorError, match="identity"):
        executor.cancel(external, IMAGE)
    assert not any(c[0] == "stop" for c in backend.calls)
    backend.unavailable = True
    with pytest.raises(ExecutorError, match="unknown"):
        executor.status(external, IMAGE)


def test_untrusted_log_links_and_fifo_are_rejected(
    bundle: tuple[Path, RunnerInput],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import os

    root, run = bundle
    backend = DockerFixture()
    monkeypatch.setattr(local_docker, "docker", backend)
    executor = LocalDockerTrainingExecutor(tmp_path / "jobs", BundleMaterializer(root))
    external = executor.submit(run.run_id, run.plan, IMAGE)
    output = executor.workspace(external) / "output"
    (output / "stdout.log").symlink_to(root / "run.json")
    with pytest.raises(ExecutorError):
        executor.logs(external, "stdout", 0)
    os.mkfifo(output / "stderr.log")
    with pytest.raises(ExecutorError):
        executor.logs(external, "stderr", 0)


def test_success_requires_manifest_identity_and_adapter_hashes(
    bundle: tuple[Path, RunnerInput],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.training import runner

    root, run = bundle
    backend = DockerFixture()
    monkeypatch.setattr(local_docker, "docker", backend)
    executor = LocalDockerTrainingExecutor(tmp_path / "jobs", BundleMaterializer(root))
    external = executor.submit(run.run_id, run.plan, IMAGE)
    output = executor.workspace(external) / "output"

    def cli(path: Path, command: list[str], timeout: int) -> tuple[int, bool]:
        adapter = path / "adapter"
        adapter.mkdir()
        (adapter / "adapter_config.json").write_text("{}")
        (adapter / "adapter_model.safetensors").write_bytes(b"synthetic")
        return 0, False

    monkeypatch.setattr(runner, "invoke_cli", cli)
    result = execute(executor.workspace(external) / "input", output, IMAGE, lambda *_: None)
    assert result == 0
    backend.state = "exited"
    assert executor.status(external, IMAGE).state == "SUCCEEDED"
    (output / "adapter/adapter_model.safetensors").write_bytes(b"tampered")
    assert executor.status(external, IMAGE).state == "FAILED"


def test_created_but_unstarted_container_can_be_canceled_and_cleaned(
    bundle: tuple[Path, RunnerInput],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, run = bundle
    backend = DockerFixture()
    monkeypatch.setattr(local_docker, "docker", backend)
    executor = LocalDockerTrainingExecutor(tmp_path / "jobs", BundleMaterializer(root))
    external = executor.submit(run.run_id, run.plan, IMAGE)
    backend.state = "created"

    def stop_created(args: list[str]) -> subprocess.CompletedProcess[str]:
        if args[0] == "stop":
            return subprocess.CompletedProcess(args, 0, "", "")
        return backend(args)

    monkeypatch.setattr(local_docker, "docker", stop_created)
    assert executor.cancel(external, IMAGE).state == "CANCELED"
    executor.cleanup(external, IMAGE)
    assert backend.missing


def test_runner_logs_are_visible_before_the_cli_exits(tmp_path: Path) -> None:
    import sys
    import time
    from concurrent.futures import ThreadPoolExecutor

    from app.training.runner import invoke_cli

    release = tmp_path / "release"
    code = (
        "import sys,time; from pathlib import Path; print('ready', flush=True); "
        "\nwhile not Path(sys.argv[1]).exists(): time.sleep(0.01)"
    )
    with ThreadPoolExecutor() as pool:
        future = pool.submit(
            invoke_cli, tmp_path, [sys.executable, "-u", "-c", code, str(release)], 10
        )
        try:
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                path = tmp_path / "stdout.log"
                if path.exists() and path.read_text() == "ready\n":
                    break
                time.sleep(0.01)
            else:
                pytest.fail("Runner buffered live log output until process exit")
            assert not future.done()
        finally:
            release.touch()
        assert future.result() == (0, False)
