import copy
import json
from pathlib import Path
from typing import Any
from unittest.mock import Mock
from uuid import uuid4

import pytest

from app.deployments.kubernetes import KubernetesDeploymentExecutor, manifests
from app.deployments.schemas import ChatRequest, DeploymentSpec
from app.executors.base import ExecutorError
from app.targets.kubernetes import KubernetesTransport
from app.targets.kubernetes_schemas import KubernetesConfig
from app.training.schemas import PlanModelSource
from tests.unit.test_local_executor import IMAGE


def config() -> KubernetesConfig:
    return KubernetesConfig(
        namespace="serving",
        training_image="example/trainer@" + IMAGE,
        deployment_image="example/vllm@" + IMAGE,
    )


def specification() -> DeploymentSpec:
    return DeploymentSpec(
        image_digest=IMAGE,
        base_source=PlanModelSource(
            source_id=uuid4(),
            kind="upload",
            location="fixture",
            revision=None,
            artifact_id=uuid4(),
            artifact_sha256="a" * 64,
            license="CC0",
        ),
        artifact_id=uuid4(),
        artifact_sha256="b" * 64,
        adapter=True,
        precision="fp16",
    )


class ServingCluster:
    def __init__(self) -> None:
        self.transport = Mock(spec=KubernetesTransport)
        self.transport.config = config()
        self.transport.core, self.transport.apps, self.transport.network = Mock(), Mock(), Mock()
        self.transport.call.side_effect = self.call
        self.objects: dict[tuple[str, str], Any] = {}
        self.ready = False
        self.unknown = False
        self.pod_uid = "pod-uid"
        self.pod_name = "pod-name"
        self.restart = 0
        self.listed_pods = False

    def call(self, method: Mock, *args: Any, **kwargs: Any) -> Any:
        if self.unknown:
            raise ExecutorError("API unavailable")
        op = method._mock_name or ""
        kind = "deployment" if "deployment" in op else "service" if "service" in op else "policy"
        if op.startswith("create"):
            body = copy.deepcopy(kwargs["body"])
            body["metadata"]["uid"] = kind + "-uid"
            if kind == "service":
                body["spec"]["ports"][0]["protocol"] = "TCP"
            self.objects[kind, body["metadata"]["name"]] = body
            return body
        if op.startswith("list"):
            dep = next((v for (k, _), v in self.objects.items() if k == "deployment"), None)
            if dep is None:
                return {"items": []}
            if "replica_set" in op:
                assert self.listed_pods  # Parent discovery must follow child discovery.
                return {
                    "items": [
                        {
                            "metadata": {
                                "uid": "replica-uid",
                                "ownerReferences": [{"uid": "deployment-uid", "controller": True}],
                            }
                        }
                    ]
                }
            self.listed_pods = True
            pod = copy.deepcopy(dep["spec"]["template"])
            pod["metadata"].update(
                name=self.pod_name,
                uid=self.pod_uid,
                ownerReferences=[{"uid": "replica-uid", "controller": True}],
            )
            pod["status"] = {
                "phase": "Running",
                "initContainerStatuses": [{"state": {"running": {"startedAt": "now"}}}],
                "containerStatuses": [{"restartCount": self.restart}],
                "conditions": [{"type": "Ready", "status": "True" if self.ready else "False"}],
            }
            return {"items": [pod]}
        if op.startswith("delete"):
            stored = self.objects.pop((kind, args[0]), None)
            assert kwargs["body"]["preconditions"]["uid"] == stored["metadata"]["uid"]
            return stored
        return self.objects.get((kind, args[0]))


def executor(tmp_path: Path) -> tuple[KubernetesDeploymentExecutor, ServingCluster]:
    cluster = ServingCluster()
    materializer = Mock()

    def materialize(spec: DeploymentSpec, path: Path) -> None:
        (path / "model").mkdir()
        (path / "model" / "config.json").write_text("{}")
        (path / "adapter.zip").write_bytes(b"host-only materialization archive")

    materializer.materialize.side_effect = materialize
    return KubernetesDeploymentExecutor(tmp_path, materializer, cluster.transport), cluster


def test_serving_manifest_is_private_tokenless_and_bounded() -> None:
    spec = specification()
    docs = manifests("serve", {"id": "id", "nonce": "nonce", "sha": "sha"}, spec, config())
    pod = docs["deployment"]["spec"]["template"]["spec"]
    assert not pod["automountServiceAccountToken"]
    assert "hostNetwork" not in pod
    assert pod["securityContext"]["runAsNonRoot"]
    assert docs["service"]["spec"]["type"] == "ClusterIP"
    assert docs["policy"]["spec"]["ingress"] == docs["policy"]["spec"]["egress"] == []
    assert pod["volumes"][0]["emptyDir"]["sizeLimit"] == "32Gi"
    assert len(pod["initContainers"]) == 1
    server = pod["containers"][0]
    assert server["resources"]["limits"]["nvidia.com/gpu"] == 1
    assert server["readinessProbe"]["httpGet"]["path"] == "/health"
    assert server["volumeMounts"][0]["readOnly"]
    assert "--disable-log-requests" in server["args"]
    assert "tuned=/models/adapter" in server["args"]
    assert all(
        c["securityContext"]["readOnlyRootFilesystem"]
        for c in pod["containers"] + pod["initContainers"]
    )


def test_deploy_restart_readiness_private_chat_and_idempotent_cleanup(tmp_path: Path) -> None:
    serving, cluster = executor(tmp_path)
    identifier, spec = uuid4(), specification()
    serving.deploy(identifier, spec)
    uploads = [c.args[1][3] for c in cluster.transport.execute.call_args_list if len(c.args[1]) > 3]
    assert uploads == ["model/config.json"]
    assert serving.status(identifier, spec).state == "STARTING"
    with pytest.raises(ExecutorError, match="already prepared"):
        serving.deploy(identifier, spec)
    serving = KubernetesDeploymentExecutor(tmp_path, serving.materializer, cluster.transport)
    cluster.ready = True
    assert serving.status(identifier, spec).state == "RUNNING"
    assert serving.status(identifier, spec).endpoint.endswith(".serving.svc:8000")  # type: ignore[union-attr]
    serving.proxy = Mock(  # type: ignore[method-assign]
        return_value=json.dumps({"choices": [{"message": {"content": "hello"}}]}).encode()
    )
    assert (
        serving.chat(
            identifier,
            spec,
            ChatRequest.model_validate({"messages": [{"role": "user", "content": "hi"}]}),
        ).content
        == "hello"
    )
    assert serving.proxy.call_args.args[0] == cluster.pod_name
    assert serving.proxy.call_args.args[1]["model"] == "tuned"
    assert serving.proxy.call_args.args[1]["max_tokens"] == 128
    serving.stop(identifier, spec)
    serving.stop(identifier, spec)
    assert not cluster.objects and not serving.workspace(identifier).exists()
    assert serving.status(identifier, spec).state == "LOST"


def test_api_outage_uid_replacement_and_container_restart_are_not_success(tmp_path: Path) -> None:
    serving, cluster = executor(tmp_path)
    identifier, spec = uuid4(), specification()
    serving.deploy(identifier, spec)
    cluster.unknown = True
    with pytest.raises(ExecutorError):
        serving.stop(identifier, spec)
    assert serving.workspace(identifier).exists() and len(cluster.objects) == 3
    cluster.unknown = False
    cluster.pod_uid = "replacement-uid"
    with pytest.raises(ExecutorError, match="identity mismatch"):
        serving.status(identifier, spec)
    cluster.pod_uid = "pod-uid"
    cluster.restart = 1
    assert serving.status(identifier, spec).state == "FAILED"
    cluster.ready = True
    with pytest.raises(ExecutorError, match="restarted"):
        serving.chat(
            identifier,
            spec,
            ChatRequest.model_validate({"messages": [{"role": "user", "content": "hi"}]}),
        )
    serving.stop(identifier, spec)


def test_missing_serving_image_preserves_training_only_targets(tmp_path: Path) -> None:
    serving, cluster = executor(tmp_path)
    cluster.transport.config.deployment_image = None
    with pytest.raises(ExecutorError, match="pinned serving image"):
        serving.image_identity()


def test_missing_local_identity_cannot_confirm_released_gpu(tmp_path: Path) -> None:
    serving, _ = executor(tmp_path)
    identifier, spec = uuid4(), specification()
    serving.deploy(identifier, spec)
    (serving.workspace(identifier) / "launch.json").unlink()
    with pytest.raises(ExecutorError, match="without retained"):
        serving.status(identifier, spec)
    with pytest.raises(ExecutorError, match="without retained"):
        serving.stop(identifier, spec)


@pytest.mark.parametrize("mutation", ["service", "image", "uid"])
def test_modified_bindings_are_rejected(tmp_path: Path, mutation: str) -> None:
    serving, cluster = executor(tmp_path)
    identifier, spec = uuid4(), specification()
    serving.deploy(identifier, spec)
    resource = next(
        v
        for (k, _), v in cluster.objects.items()
        if k == ("service" if mutation == "service" else "deployment")
    )
    if mutation == "service":
        resource["spec"]["selector"] = {"another": "model"}
    elif mutation == "image":
        resource["spec"]["template"]["spec"]["containers"][0]["image"] = "untrusted:latest"
    else:
        resource["metadata"]["uid"] = "replacement"
    with pytest.raises(ExecutorError, match="mismatch"):
        serving.status(identifier, spec)


def test_partial_creation_is_cleaned_without_replaying_model_upload(tmp_path: Path) -> None:
    serving, cluster = executor(tmp_path)
    identifier, spec = uuid4(), specification()
    original = cluster.call

    def fail_deployment(method: Mock, *args: Any, **kwargs: Any) -> Any:
        if method._mock_name == "create_namespaced_deployment":
            raise ExecutorError("API unavailable")
        return original(method, *args, **kwargs)

    cluster.transport.call.side_effect = fail_deployment
    with pytest.raises(ExecutorError):
        serving.deploy(identifier, spec)
    assert len(cluster.objects) == 2
    serving.stop(identifier, spec)
    assert not cluster.objects
    cluster.transport.execute.assert_not_called()
