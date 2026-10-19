import copy
import io
import json
import shutil
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, Mock

import pytest
import yaml
from pydantic import ValidationError

from app.executors.base import ExecutorError
from app.executors.kubernetes import KubernetesTrainingExecutor
from app.targets.kubernetes import WRITE, KubernetesTransport, kubeconfig, resources
from app.targets.kubernetes_schemas import KubernetesConfig
from app.targets.schemas import TargetCreate
from app.training.runner import execute
from app.training.soup import RunnerInput
from tests.unit.test_local_executor import IMAGE, BundleMaterializer, bundle

__all__ = ["bundle"]


def configuration() -> KubernetesConfig:
    return KubernetesConfig(namespace="training", training_image="example/trainer@" + IMAGE)


@pytest.mark.parametrize(
    "field,value",
    [
        ("namespace", "../default"),
        ("training_image", "trainer:latest"),
        ("training_image", "sha256:" + "a" * 64),
        ("gpu_count", 0),
        ("runtime_class", "nvidia;id"),
        ("cleanup_policy", "always"),
    ],
)
def test_configuration_rejects_unsafe_or_unpinned_values(field: str, value: Any) -> None:
    body = configuration().model_dump(by_alias=True)
    body[field] = value
    with pytest.raises(ValidationError):
        KubernetesConfig.model_validate(body)


def test_target_kind_cannot_reinterpret_configuration() -> None:
    with pytest.raises(ValidationError):
        TargetCreate(name="bad", credential_ref="env://KUBE", configuration=configuration())


@pytest.mark.parametrize(
    "section,entry",
    [
        ("users", {"exec": {"command": "touch", "args": ["/tmp/unsafe"]}}),
        ("users", {"auth-provider": {"name": "gcp"}}),
        ("users", {"client-key": "/etc/shadow"}),
        ("users", {"tokenFile": "/etc/shadow"}),
        ("clusters", {"server": "http://localhost", "certificate-authority-data": "YQ=="}),
        ("clusters", {"server": "https://localhost", "insecure-skip-tls-verify": True}),
        ("clusters", {"server": "https://localhost", "certificate-authority": "/etc/passwd"}),
    ],
)
def test_kubeconfig_cannot_execute_plugins_or_read_external_secrets(
    section: str,
    entry: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = {section: [{"name": "unsafe", "user" if section == "users" else "cluster": entry}]}
    monkeypatch.setenv("KUBE", yaml.safe_dump(body))
    with pytest.raises(ExecutorError, match="unsafe"):
        kubeconfig("env://KUBE", tmp_path, {"KUBE"})


def test_kubeconfig_uses_only_operator_approved_references(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    value = {"users": [{"name": "user", "user": {"token": "synthetic-token"}}]}
    monkeypatch.setenv("KUBE", yaml.safe_dump(value))
    assert kubeconfig("env://KUBE", tmp_path, {"KUBE"}) == value
    with pytest.raises(ExecutorError):
        kubeconfig("env://KUBE", tmp_path, set())
    path = tmp_path / "config"
    path.write_text(yaml.safe_dump(value))
    path.chmod(0o600)
    assert kubeconfig(path.as_uri(), tmp_path, set()) == value
    path.chmod(0o644)
    with pytest.raises(ExecutorError):
        kubeconfig(path.as_uri(), tmp_path, set())
    with pytest.raises(ExecutorError):
        kubeconfig("file:///etc/passwd", tmp_path, set())


def test_manifests_preserve_offline_runner_boundary() -> None:
    launch = {"run_id": "run", "nonce": "nonce", "input_sha256": "b" * 64, "image": IMAGE}
    manifests = resources("job", launch, configuration(), 4, 2, 240)
    job = manifests["job"]
    spec = job["spec"]["template"]["spec"]
    assert job["kind"] == "Job" and job["spec"]["backoffLimit"] == 0
    assert "ttlSecondsAfterFinished" not in job["spec"]
    assert not spec["automountServiceAccountToken"]
    assert spec["runtimeClassName"] == "nvidia"
    assert spec["securityContext"]["runAsNonRoot"]
    trainer = spec["containers"][0]
    assert trainer["resources"]["limits"]["nvidia.com/gpu"] == 1
    assert trainer["securityContext"]["capabilities"]["drop"] == ["ALL"]
    assert trainer["securityContext"]["readOnlyRootFilesystem"]
    assert "RLIMIT_FSIZE" in trainer["command"][-1]
    assert "/runner/entrypoint.py" in trainer["command"][-1]
    assert next(m for m in trainer["volumeMounts"] if m["mountPath"] == "/input")["readOnly"]
    assert manifests["policy"]["spec"]["egress"] == []
    assert manifests["policy"]["spec"]["policyTypes"] == ["Ingress", "Egress"]
    assert "secret" not in json.dumps(manifests).lower()


def test_upload_progresses_without_stdout_and_websocket_reads_never_block(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.targets import kubernetes

    transport = object.__new__(KubernetesTransport)
    transport.config = configuration()
    transport.api = Mock()
    connection = Mock()
    connection.is_open.side_effect = [True, True, False]
    connection.returncode = 0
    connection.read_stdout.return_value = ""
    connection.read_stderr.return_value = ""
    monkeypatch.setattr(kubernetes, "stream", lambda *a, **kw: connection)
    monkeypatch.setattr("app.targets.kubernetes.client.ApiClient", MagicMock())
    transport.execute("pod", ["python", "-c", WRITE, "input/file"], source=io.BytesIO(b"bytes"))
    assert connection.write_stdin.call_count == 2
    assert connection.write_stdin.call_args.args == ("END\n",)
    assert all(c.kwargs == {"timeout": 0} for c in connection.read_stdout.call_args_list)
    assert all(c.kwargs == {"timeout": 0} for c in connection.read_stderr.call_args_list)
    connection.close.assert_called_once()


class Cluster:
    """API boundary fake; runner bytes still pass the real manifest and ZIP verifier."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.transport = Mock(spec=KubernetesTransport)
        self.transport.config = configuration()
        self.transport.core, self.transport.batch, self.transport.network = Mock(), Mock(), Mock()
        self.transport.call.side_effect = self.call
        self.transport.execute.side_effect = self.execute
        self.transport.read.side_effect = self.read
        self.objects: dict[tuple[str, str], Any] = {}
        self.exit_code: int | None = None
        self.unknown = False
        self.no_trainer_pod = False

    def call(self, method: Mock, *args: Any, **kwargs: Any) -> Any:
        if self.unknown:
            raise ExecutorError("Kubernetes API unavailable; status is unknown")
        operation = method._mock_name or ""
        kind = (
            "job"
            if "job" in operation
            else "pvc"
            if "persistent_volume_claim" in operation
            else "policy"
            if "network_policy" in operation
            else "pod"
        )
        if operation.startswith("create"):
            body = copy.deepcopy(kwargs["body"])
            name = body["metadata"]["name"]
            body["metadata"]["uid"] = kind + "-uid"
            body["status"] = {"phase": "Running"}
            self.objects[kind, name] = body
            return body
        if operation.startswith("list"):
            if self.no_trainer_pod:
                return {"items": []}
            job = next((v for (k, _), v in self.objects.items() if k == "job"), None)
            if job is None:
                return {"items": []}
            pod = copy.deepcopy(job["spec"]["template"])
            pod["metadata"]["name"] += "-abcde"
            pod["metadata"]["ownerReferences"] = [{"uid": "job-uid", "controller": True}]
            pod["status"] = {
                "containerStatuses": [
                    {
                        "state": {"running": {}}
                        if self.exit_code is None
                        else {"terminated": {"exitCode": self.exit_code}}
                    }
                ]
            }
            return {"items": [pod]}
        if operation.startswith("delete"):
            return self.objects.pop((kind, args[0]), None)
        return self.objects.get((kind, args[0]))

    def execute(self, pod: str, command: list[str], **kwargs: Any) -> str:
        if command[2] == WRITE:
            path = self.root / command[3]
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(kwargs["source"].read())
        else:
            (self.root / "output").mkdir()
        return ""

    def read(self, pod: str, path: str, limit: int, offset: int = 0) -> bytes:
        try:
            return (self.root / path).read_bytes()[offset : offset + limit]
        except FileNotFoundError:
            return b""


def test_job_lifecycle_registration_restart_hash_verification_and_cleanup(
    bundle: tuple[Path, RunnerInput],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.training import runner

    source, run = bundle
    cluster = Cluster(tmp_path / "remote")
    materializer = BundleMaterializer(source)
    executor = KubernetesTrainingExecutor(tmp_path / "local", materializer, cluster.transport)
    name = executor.submit(run.run_id, run.plan, IMAGE)
    assert executor.status(name, IMAGE).state == "RUNNING"
    with pytest.raises(ExecutorError, match="Cancel"):
        executor.cleanup(name, IMAGE)
    with pytest.raises(ExecutorError, match="already exists"):
        executor.submit(run.run_id, run.plan, IMAGE)

    def cli(path: Path, command: list[str], timeout: int) -> tuple[int, bool]:
        adapter = path / "adapter"
        adapter.mkdir()
        (adapter / "adapter_config.json").write_text("{}")
        (adapter / "adapter_model.safetensors").write_bytes(b"synthetic")
        return 0, False

    monkeypatch.setattr(runner, "invoke_cli", cli)
    assert execute(cluster.root / "input", cluster.root / "output", IMAGE, lambda *_: None) == 0
    (cluster.root / "output/stdout.log").write_text("private log")
    cluster.exit_code = 0
    restarted = KubernetesTrainingExecutor(executor.root, materializer, cluster.transport)
    assert restarted.status(name, IMAGE).state == "SUCCEEDED"
    with restarted.output(name, IMAGE) as output:
        assert output.read(2) == b"PK"
    assert restarted.logs(name, "stdout", 0).text == "private log"
    job = cluster.objects["job", name]
    job["metadata"]["uid"] = "replaced"
    with pytest.raises(ExecutorError, match="identity"):
        restarted.cancel(name, IMAGE)
    job["metadata"]["uid"] = "job-uid"
    cluster.unknown = True
    with pytest.raises(ExecutorError, match="unknown"):
        restarted.status(name, IMAGE)
    cluster.unknown = False
    (cluster.root / "output/adapter/adapter_model.safetensors").write_bytes(b"corrupt")
    shutil.rmtree(restarted.workspace(name) / "output/adapter")
    assert restarted.status(name, IMAGE).state == "FAILED"
    restarted.cleanup(name, IMAGE)
    restarted.cleanup(name, IMAGE)
    assert not cluster.objects
    assert restarted.logs(name, "stdout", 0).text == "private log"


def test_pending_job_can_progress_and_cancellation_deletes_only_verified_job(
    bundle: tuple[Path, RunnerInput],
    tmp_path: Path,
) -> None:
    source, run = bundle
    cluster = Cluster(tmp_path / "remote")
    executor = KubernetesTrainingExecutor(
        tmp_path / "local", BundleMaterializer(source), cluster.transport
    )
    name = executor.submit(run.run_id, run.plan, IMAGE)
    cluster.no_trainer_pod = True
    assert executor.status(name, IMAGE).state == "RUNNING"
    assert executor.cancel(name, IMAGE).state == "CANCELED"
    assert ("job", name) not in cluster.objects
    executor.cleanup(name, IMAGE)
    assert not cluster.objects
