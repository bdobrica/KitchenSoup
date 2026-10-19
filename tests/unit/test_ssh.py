import io
import json
import os
import shutil
import stat
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from unittest.mock import Mock
from uuid import uuid4

import paramiko
import pytest
from pydantic import ValidationError

from app.deployments.materialize import DeploymentMaterializer
from app.deployments.schemas import ChatRequest
from app.deployments.ssh import SshDockerDeploymentExecutor
from app.executors.base import ExecutorError
from app.executors.ssh_docker import SshDockerTrainingExecutor
from app.targets.schemas import SshConfig, TargetCreate
from app.targets.ssh import SshTransport
from app.training.runner import execute
from app.training.soup import RunnerInput
from tests.unit.test_deployments import Docker, spec
from tests.unit.test_local_executor import IMAGE, BundleMaterializer, DockerFixture, bundle

__all__ = ["bundle"]


def transport(tmp_path: Path) -> SshTransport:
    return SshTransport(
        SshConfig(host="localhost", username="operator", workspace=str(tmp_path / "remote")),
        "env://GPU_KEY",
        known_hosts=tmp_path / "known_hosts",
        secret_directory=tmp_path,
        env_names={"GPU_KEY"},
    )


def test_expired_allocation_deadline_blocks_even_empty_transfers_before_connect(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ssh = transport(tmp_path)
    ssh.transfer_deadline = 0
    connection = Mock(side_effect=AssertionError("No SSH after allocation expiry"))
    monkeypatch.setattr(ssh, "connect", connection)
    with pytest.raises(ExecutorError, match="timed out"):
        ssh.upload(tmp_path, "/remote")
    with pytest.raises(ExecutorError, match="timed out"):
        ssh.download("/remote/empty", tmp_path / "empty", 0)
    connection.assert_not_called()


@pytest.mark.parametrize(
    "field,value",
    [
        ("host", "host; touch /tmp/x"),
        ("host", "-oProxyCommand=bad"),
        ("username", "root;id"),
        ("workspace", "/"),
        ("workspace", "/tmp/a/../b"),
        ("workspace", "/tmp/a,b"),
        ("workspace", "/tmp/a\nb"),
        ("training_image", "image --privileged"),
        ("port", 0),
    ],
)
def test_target_rejects_injection_and_unsafe_paths(field: str, value: Any) -> None:
    body: dict[str, Any] = {
        "host": "localhost",
        "username": "operator",
        "workspace": "/tmp/private",
    }
    body[field] = value
    with pytest.raises(ValidationError):
        SshConfig.model_validate(body)


@pytest.mark.parametrize(
    "reference", ["raw-private-key", "aws-profile://default", "file:///tmp/../key"]
)
def test_target_requires_supported_secret_reference(reference: str) -> None:
    with pytest.raises(ValidationError):
        TargetCreate(
            name="gpu",
            credential_ref=reference,
            configuration=SshConfig(host="localhost", username="user", workspace="/tmp/private"),
        )


def test_keys_are_resolved_only_from_approved_locations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    key = paramiko.RSAKey.generate(1024)
    buffer = io.StringIO()
    key.write_private_key(buffer)
    monkeypatch.setenv("GPU_KEY", buffer.getvalue())
    ssh = transport(tmp_path)
    assert ssh.key().get_fingerprint() == key.get_fingerprint()
    ssh.env_names.clear()
    with pytest.raises(ExecutorError, match="reference"):
        ssh.key()
    path = tmp_path / "key"
    path.write_text(buffer.getvalue())
    path.chmod(0o600)
    ssh.reference = "file://" + str(path)
    assert ssh.key().get_fingerprint() == key.get_fingerprint()
    path.chmod(0o644)
    with pytest.raises(ExecutorError):
        ssh.key()
    ssh.reference = "file:///etc/passwd"
    with pytest.raises(ExecutorError):
        ssh.key()
    monkeypatch.setenv("GPU_KEY", "sensitive-invalid-key")
    ssh.reference, ssh.env_names = "env://GPU_KEY", {"GPU_KEY"}
    with pytest.raises(ExecutorError) as error:
        ssh.key()
    assert "sensitive" not in str(error.value)


def test_ssh_rejects_unknown_hosts_and_disables_implicit_credentials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ssh = transport(tmp_path)
    client = Mock(spec=paramiko.SSHClient)
    client.connect.side_effect = paramiko.SSHException("sensitive diagnostic")
    monkeypatch.setattr(paramiko, "SSHClient", lambda: client)
    monkeypatch.setattr(ssh, "key", lambda: Mock(spec=paramiko.PKey))
    with pytest.raises(ExecutorError, match="known-hosts") as error:
        with ssh.connect():
            pytest.fail("Unverified connection accepted")
    assert isinstance(client.set_missing_host_key_policy.call_args.args[0], paramiko.RejectPolicy)
    assert client.connect.call_args.kwargs["allow_agent"] is False
    assert client.connect.call_args.kwargs["look_for_keys"] is False
    assert "sensitive" not in str(error.value)
    client.close.assert_called_once()


def test_sftp_bounds_and_rejects_remote_links(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ssh = transport(tmp_path)
    sftp = Mock(spec=paramiko.SFTPClient)

    @contextmanager
    def session() -> Any:
        yield sftp

    monkeypatch.setattr(ssh, "sftp", session)
    sftp.lstat.return_value = paramiko.SFTPAttributes.from_stat(os.stat(tmp_path))
    stream = Mock()
    stream.__enter__ = Mock(return_value=stream)
    stream.__exit__ = Mock(return_value=False)
    stream.read.return_value = b"12345"
    sftp.open.return_value = stream
    # Directory ancestors are checked, then the leaf must be a regular file.
    with pytest.raises(ExecutorError, match="regular"):
        ssh.read("/tmp/private/log", 4)
    sftp.lstat.side_effect = [
        paramiko.SFTPAttributes.from_stat(os.stat(tmp_path)),
        paramiko.SFTPAttributes.from_stat(os.stat(tmp_path)),
        paramiko.SFTPAttributes.from_stat(os.stat(__file__)),
    ]
    assert ssh.read("/tmp/private/log", 4, offset=2) == b"12345"
    stream.read.assert_called_once_with(5)
    stream.seek.assert_called_once_with(2)
    attrs = paramiko.SFTPAttributes()
    attrs.st_mode = stat.S_IFLNK | 0o777
    sftp.lstat.side_effect = None
    sftp.lstat.return_value = attrs
    with pytest.raises(ExecutorError, match="link"):
        ssh.read("/tmp/private/log", 4)
    with pytest.raises(ExecutorError, match="cleanup"):
        ssh.remove(ssh.config.workspace)


def test_ssh_training_reuses_verification_and_remote_identity(
    bundle: tuple[Path, RunnerInput],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.training import runner

    source, run = bundle
    ssh = transport(tmp_path)
    backend = DockerFixture()
    monkeypatch.setattr(ssh, "docker", backend)
    monkeypatch.setattr(ssh, "identity", lambda: (1001, 1002))
    monkeypatch.setattr(ssh, "upload", lambda local, remote: shutil.copytree(local, remote))

    def read(remote: str, limit: int, offset: int = 0, *, missing_ok: bool = False) -> bytes:
        try:
            return Path(remote).read_bytes()[offset : offset + limit + 1]
        except FileNotFoundError:
            if missing_ok:
                return b""
            raise

    def download(remote: str, local: Path, size: int) -> None:
        local.parent.mkdir(parents=True, exist_ok=True)
        local.write_bytes(Path(remote).read_bytes())

    monkeypatch.setattr(ssh, "read", read)
    monkeypatch.setattr(ssh, "download", download)
    monkeypatch.setattr(ssh, "remove", lambda remote: shutil.rmtree(remote, ignore_errors=True))
    executor = SshDockerTrainingExecutor(tmp_path / "staging", BundleMaterializer(source), ssh)
    external = executor.submit(run.run_id, run.plan, IMAGE)
    command = next(c for c in backend.calls if c[0] == "create")
    remote = Path(executor.remote(executor.workspace(external)))
    assert command[command.index("--user") + 1] == "1001:1002"
    assert any("src=" + str(remote) + "/input" in c for c in command)
    assert not any("src=" + str(executor.root) in c for c in command)
    assert "--network" in command and "none" in command

    def cli(path: Path, command: list[str], timeout: int) -> tuple[int, bool]:
        adapter = path / "adapter"
        adapter.mkdir()
        (adapter / "adapter_config.json").write_text("{}")
        (adapter / "adapter_model.safetensors").write_bytes(b"synthetic")
        return 0, False

    monkeypatch.setattr(runner, "invoke_cli", cli)
    assert execute(remote / "input", remote / "output", IMAGE, lambda *_: None) == 0
    (remote / "output/stdout.log").write_text("private log")
    backend.state = "exited"
    assert executor.status(external, IMAGE).state == "SUCCEEDED"
    with executor.output(external, IMAGE) as output:
        assert output.read(2) == b"PK"
    # A fresh adapter resumes from the retained workspace and remote container.
    restarted = SshDockerTrainingExecutor(executor.root, BundleMaterializer(source), ssh)
    assert restarted.status(external, IMAGE).state == "SUCCEEDED"
    backend.image = "sha256:" + "b" * 64
    with pytest.raises(ExecutorError, match="identity"):
        restarted.cleanup(external, IMAGE)
    assert remote.exists()
    backend.image = IMAGE
    (remote / "output/adapter/adapter_model.safetensors").write_bytes(b"corrupt")
    # Force a new download to establish that manifest hash verification is retained.
    (executor.workspace(external) / "output/adapter/adapter_model.safetensors").unlink()
    assert restarted.status(external, IMAGE).state == "FAILED"
    restarted.cleanup(external, IMAGE)
    assert not remote.exists()
    assert restarted.logs(external, "stdout", 0).text == "private log"


def test_ssh_serving_only_proxies_verified_private_docker_endpoint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ssh = transport(tmp_path)
    backend = Docker()
    monkeypatch.setattr(ssh, "docker", backend)
    monkeypatch.setattr(ssh, "identity", lambda: (1001, 1002))
    uploads: list[tuple[Path, str]] = []
    removed: list[str] = []
    requests: list[tuple[str, str, Any]] = []
    monkeypatch.setattr(ssh, "upload", lambda local, remote: uploads.append((local, remote)))
    monkeypatch.setattr(ssh, "remove", removed.append)

    def request(endpoint: str, method: str, path: str, payload: Any, timeout: int) -> bytes:
        requests.append((endpoint, path, payload))
        return json.dumps({"choices": [{"message": {"content": "safe"}}]}).encode()

    monkeypatch.setattr(ssh, "request", request)
    executor = SshDockerDeploymentExecutor(
        tmp_path / "jobs", Mock(spec=DeploymentMaterializer), ssh
    )
    identifier, snapshot = uuid4(), spec()
    executor.deploy(identifier, snapshot)
    assert uploads[0][1] == ssh.config.workspace + "/deployments/" + str(identifier)
    assert executor.status(identifier, snapshot).state == "RUNNING"
    assert (
        executor.chat(
            identifier,
            snapshot,
            ChatRequest.model_validate({"messages": [{"role": "user", "content": "Hi"}]}),
        ).content
        == "safe"
    )
    assert requests[-1][0] == "http://172.20.0.2:8000"
    assert requests[-1][2]["model"] == "tuned"
    backend.info["NetworkSettings"]["Networks"] = {"foreign": {"IPAddress": "172.20.0.2"}}
    with pytest.raises(ExecutorError):
        executor.chat(
            identifier,
            snapshot,
            ChatRequest.model_validate({"messages": [{"role": "user", "content": "Hi"}]}),
        )
    assert len(requests) == 2
    executor.stop(identifier, snapshot)
    assert removed == [ssh.config.workspace + "/deployments/" + str(identifier)]


def test_sftp_missing_file_preserves_missing_semantics(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ssh = transport(tmp_path)
    client = Mock(spec=paramiko.SSHClient)
    sftp = Mock(spec=paramiko.SFTPClient)
    sftp.__enter__ = Mock(return_value=sftp)
    sftp.__exit__ = Mock(return_value=False)
    sftp.lstat.side_effect = FileNotFoundError()
    client.open_sftp.return_value = sftp
    monkeypatch.setattr(paramiko, "SSHClient", lambda: client)
    monkeypatch.setattr(ssh, "key", lambda: Mock(spec=paramiko.PKey))
    assert ssh.read("/tmp/private/missing", 100, missing_ok=True) == b""
    client.close.assert_called_once()


def test_connection_probe_uses_bounded_offline_gpu_container(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import subprocess
    from datetime import UTC, datetime
    from types import SimpleNamespace

    from app.config import Settings
    from app.services.targets import TargetService
    from app.targets.schemas import TargetView

    artifacts: Any = SimpleNamespace(factory=None)
    targets = TargetService(
        artifacts,
        Settings(
            _env_file=None,
            ssh_execution_enabled=True,
            ssh_staging_workspace=str(tmp_path / "staging"),
        ),
    )
    view = TargetView(
        id=uuid4(),
        created_at=datetime.now(UTC),
        name="gpu",
        credential_ref="env://GPU_KEY",
        configuration=transport(tmp_path).config,
    )
    monkeypatch.setattr(targets, "get", lambda _: view)
    ssh = transport(tmp_path)
    monkeypatch.setattr(targets, "transport", lambda _: ssh)
    monkeypatch.setattr(ssh, "identity", lambda: (1001, 1002))
    commands: list[list[str]] = []

    def docker(args: list[str]) -> subprocess.CompletedProcess[str]:
        commands.append(args)
        output = (
            IMAGE
            if args[:2] == ["image", "inspect"]
            else json.dumps(
                {"cuda": True, "name": "Synthetic GPU", "capability": [7, 5], "bf16": False}
            )
        )
        return subprocess.CompletedProcess(args, 0, output, "")

    monkeypatch.setattr(ssh, "docker", docker)
    result = targets.test(view.id)
    assert "Synthetic GPU" in result.gpu and result.training_image_digest == IMAGE
    probe = commands[-1]
    assert probe[0] == "run" and "--rm" in probe and "--gpus" in probe
    assert probe[probe.index("--network") + 1] == "none"
    assert probe[probe.index("--user") + 1] == "1001:1002"
    assert "--read-only" in probe and "--tmpfs" in probe and "--memory" in probe
    assert "GPU_KEY" not in " ".join(probe)
    monkeypatch.setattr(
        ssh, "docker", lambda args: subprocess.CompletedProcess(args, 1, "", "sensitive diagnostic")
    )
    with pytest.raises(ExecutorError) as error:
        targets.test(view.id)
    assert "sensitive" not in str(error.value)
