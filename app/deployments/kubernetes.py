"""Private Kubernetes vLLM serving with API-mediated emptyDir initialization."""

import copy
import hashlib
import http.client
import json
import shutil
import time
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from kubernetes import client
from kubernetes.stream import portforward

from app.deployments.base import DeploymentStatus
from app.deployments.local import LocalDockerDeploymentExecutor, container_name
from app.deployments.materialize import DeploymentMaterializer
from app.deployments.schemas import ChatRequest, ChatResponse, DeploymentSpec
from app.executors.base import ExecutorError
from app.targets.kubernetes import WRITE, KubernetesTransport
from app.targets.kubernetes_schemas import KubernetesConfig
from app.training.runner import read_bounded


def manifests(
    name: str, launch: dict[str, Any], spec: DeploymentSpec, target: KubernetesConfig
) -> dict[str, dict[str, Any]]:
    labels = {"kitchensoup.deployment": launch["id"]}
    meta = {
        "name": name,
        "labels": labels,
        "annotations": {"kitchensoup.identity": launch["nonce"], "kitchensoup.spec": launch["sha"]},
    }
    security = {
        "allowPrivilegeEscalation": False,
        "readOnlyRootFilesystem": True,
        "capabilities": {"drop": ["ALL"]},
    }
    args = [
        "--model",
        "/models/model",
        "--served-model-name",
        "playground",
        "--host",
        "0.0.0.0",
        "--port",
        "8000",
        "--dtype",
        "half" if spec.precision == "fp16" else "bfloat16",
        "--load-format",
        "safetensors",
        "--max-model-len",
        str(spec.max_model_len),
        "--max-num-seqs",
        "1",
        "--gpu-memory-utilization",
        "0.75",
        "--enforce-eager",
        "--disable-log-requests",
        "--disable-log-stats",
        "--uvicorn-log-level",
        "warning",
    ]
    if spec.adapter:
        args += [
            "--enable-lora",
            "--max-loras",
            "1",
            "--max-lora-rank",
            "64",
            "--lora-modules",
            "tuned=/models/adapter",
        ]
    pod: dict[str, Any] = {
        "automountServiceAccountToken": False,
        "serviceAccountName": target.service_account,
        "nodeSelector": target.node_selector,
        "tolerations": [t.model_dump(exclude_none=True) for t in target.tolerations],
        "securityContext": {
            "runAsNonRoot": True,
            "runAsUser": 10001,
            "runAsGroup": 10001,
            "fsGroup": 10001,
            "seccompProfile": {"type": "RuntimeDefault"},
        },
        "volumes": [
            {"name": "models", "emptyDir": {"sizeLimit": f"{target.workspace_gib}Gi"}},
            {"name": "tmp", "emptyDir": {"sizeLimit": "2Gi"}},
            {"name": "shm", "emptyDir": {"medium": "Memory", "sizeLimit": "1Gi"}},
        ],
        "initContainers": [
            {
                "name": "transfer",
                "image": target.deployment_image,
                "imagePullPolicy": "IfNotPresent",
                "securityContext": security,
                "command": [
                    "python3",
                    "-c",
                    "import pathlib,time\n"
                    "while not pathlib.Path('/workspace/ready').exists(): time.sleep(1)\n",
                ],
                "resources": {
                    "requests": {"cpu": "100m", "memory": "64Mi"},
                    "limits": {"cpu": "1", "memory": "256Mi"},
                },
                "volumeMounts": [
                    {"name": "models", "mountPath": "/workspace"},
                    {"name": "tmp", "mountPath": "/tmp"},
                ],
            }
        ],
        "containers": [
            {
                "name": "server",
                "image": target.deployment_image,
                "imagePullPolicy": "IfNotPresent",
                "securityContext": security,
                "args": args,
                "ports": [{"name": "http", "containerPort": 8000}],
                "readinessProbe": {
                    "httpGet": {"path": "/health", "port": "http"},
                    "periodSeconds": 5,
                    "timeoutSeconds": 2,
                },
                "resources": {
                    "requests": {"cpu": "4", "memory": "16Gi", "nvidia.com/gpu": target.gpu_count},
                    "limits": {"cpu": "4", "memory": "16Gi", "nvidia.com/gpu": target.gpu_count},
                },
                "volumeMounts": [
                    {"name": "models", "mountPath": "/models", "readOnly": True},
                    {"name": "tmp", "mountPath": "/tmp"},
                    {"name": "shm", "mountPath": "/dev/shm"},
                ],
            }
        ],
    }
    if target.runtime_class:
        pod["runtimeClassName"] = target.runtime_class
    return {
        "policy": {
            "apiVersion": "networking.k8s.io/v1",
            "kind": "NetworkPolicy",
            "metadata": copy.deepcopy(meta),
            "spec": {
                "podSelector": {"matchLabels": labels},
                "policyTypes": ["Ingress", "Egress"],
                "ingress": [],
                "egress": [],
            },
        },
        "service": {
            "apiVersion": "v1",
            "kind": "Service",
            "metadata": copy.deepcopy(meta),
            "spec": {
                "type": "ClusterIP",
                "selector": labels,
                "ports": [{"port": 8000, "targetPort": "http", "name": "http"}],
            },
        },
        "deployment": {
            "apiVersion": "apps/v1",
            "kind": "Deployment",
            "metadata": copy.deepcopy(meta),
            "spec": {
                "replicas": 1,
                "strategy": {"type": "Recreate"},
                "selector": {"matchLabels": labels},
                "template": {"metadata": copy.deepcopy(meta), "spec": pod},
            },
        },
    }


class KubernetesDeploymentExecutor(LocalDockerDeploymentExecutor):
    """Reuse private workspace checks; all external operations use Kubernetes."""

    def __init__(
        self, root: Path, materializer: DeploymentMaterializer, transport: KubernetesTransport
    ) -> None:
        super().__init__(root, materializer)
        self.transport = transport

    def image_identity(self) -> str:
        image = self.transport.config.deployment_image
        if image is None:
            raise ExecutorError("Create a Kubernetes target with a pinned serving image")
        return image.rsplit("@", 1)[1]

    @staticmethod
    def spec_hash(spec: DeploymentSpec) -> str:
        return hashlib.sha256(spec.model_dump_json(by_alias=True).encode()).hexdigest()

    def launch(self, identifier: UUID, spec: DeploymentSpec) -> dict[str, Any] | None:
        path = self.workspace(identifier) / "launch.json"
        if not path.exists():
            return None
        try:
            result: dict[str, Any] = json.loads(read_bounded(path, 16384))
            if (
                result["id"] != str(identifier)
                or result["sha"] != self.spec_hash(spec)
                or self.image_identity() != spec.image_digest
            ):
                raise ValueError
            return result
        except (ValueError, KeyError, OSError):
            raise ExecutorError(
                "Kubernetes serving identity is unavailable or mismatched"
            ) from None

    def save(self, identifier: UUID, launch: dict[str, Any]) -> None:
        path = self.workspace(identifier) / "launch.json"
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(launch))
        temporary.replace(path)

    def absent(self, identifier: UUID) -> None:
        """Missing local identity is never proof that external resources disappeared."""
        t = self.transport
        for method in (
            t.apps.read_namespaced_deployment,
            t.core.read_namespaced_service,
            t.network.read_namespaced_network_policy,
        ):
            if t.call(method, container_name(identifier), missing_ok=True) is not None:
                raise ExecutorError("Serving resources exist without retained local identity")

    def verify(self, resource: dict[str, Any], launch: dict[str, Any], kind: str) -> None:
        meta = resource.get("metadata", {})
        if (
            meta.get("labels", {}).get("kitchensoup.deployment") != launch["id"]
            or meta.get("annotations", {}).get("kitchensoup.identity") != launch["nonce"]
            or meta.get("annotations", {}).get("kitchensoup.spec") != launch["sha"]
            or launch["uids"].get(kind + "/" + meta.get("name", "")) not in (None, meta.get("uid"))
        ):
            raise ExecutorError("Kubernetes serving resource identity mismatch")

    def get(self, kind: str, name: str, launch: dict[str, Any]) -> dict[str, Any] | None:
        t = self.transport
        method = {
            "deployment": t.apps.read_namespaced_deployment,
            "service": t.core.read_namespaced_service,
            "policy": t.network.read_namespaced_network_policy,
        }[kind]
        result = t.call(method, name, missing_ok=True)
        if result is not None:
            self.verify(result, launch, kind)
            if kind == "deployment":
                pod = result["spec"]["template"]["spec"]
                if any(
                    c["image"] != t.config.deployment_image
                    for c in pod["containers"] + pod["initContainers"]
                ):
                    raise ExecutorError("Kubernetes serving image mismatch")
        return dict(result) if result is not None else None

    def pods(self, name: str, launch: dict[str, Any]) -> list[dict[str, Any]]:
        t = self.transport
        deployment = self.get("deployment", name, launch)
        if deployment is None:
            return []
        # List children before parents: otherwise a newly created ReplicaSet/Pod
        # between the two reads falsely appears to have an unknown owner.
        pods = t.call(
            t.core.list_namespaced_pod, label_selector="kitchensoup.deployment=" + launch["id"]
        )["items"]
        replicas = t.call(
            t.apps.list_namespaced_replica_set,
            label_selector="kitchensoup.deployment=" + launch["id"],
        )["items"]
        owners = {
            r["metadata"]["uid"]
            for r in replicas
            if any(
                o.get("uid") == deployment["metadata"]["uid"] and o.get("controller")
                for o in r["metadata"].get("ownerReferences", [])
            )
        }
        for pod in pods:
            self.verify(pod, launch, "pod")
            if not any(
                o.get("uid") in owners and o.get("controller")
                for o in pod["metadata"].get("ownerReferences", [])
            ) or any(
                c["image"] != t.config.deployment_image
                for c in pod["spec"]["containers"] + pod["spec"]["initContainers"]
            ):
                raise ExecutorError("Kubernetes serving Pod ownership/image mismatch")
        return list(pods)

    def deploy(self, identifier: UUID, spec: DeploymentSpec) -> None:
        if spec.image_digest != self.image_identity():
            raise ExecutorError("Serving image differs from retained deployment")
        path, name, t = self.workspace(identifier), container_name(identifier), self.transport
        if path.exists():
            raise ExecutorError("Deployment already prepared; create a new attempt")
        path.mkdir(mode=0o700)
        launch: dict[str, Any] = {
            "id": str(identifier),
            "sha": self.spec_hash(spec),
            "nonce": str(uuid4()),
            "uids": {},
        }
        self.save(identifier, launch)
        models = path / "models"
        models.mkdir(mode=0o700)
        try:
            self.materializer.materialize(spec, models)
        except Exception:
            raise ExecutorError(
                "Serving model preparation failed; verify registered inputs"
            ) from None
        documents = manifests(name, launch, spec, t.config)
        for kind, method in (
            ("policy", t.network.create_namespaced_network_policy),
            ("service", t.core.create_namespaced_service),
            ("deployment", t.apps.create_namespaced_deployment),
        ):
            result = t.call(method, body=documents[kind])
            launch["uids"][kind + "/" + name] = result["metadata"]["uid"]
            self.save(identifier, launch)
        deadline = time.monotonic() + t.config.preparation_timeout
        while time.monotonic() < deadline:
            pods = self.pods(name, launch)
            if len(pods) > 1:
                raise ExecutorError("Unexpected Kubernetes serving replicas")
            if pods and any(
                s.get("state", {}).get("running")
                for s in pods[0].get("status", {}).get("initContainerStatuses", [])
            ):
                break
            time.sleep(1)
        else:
            raise ExecutorError("Model initialization timed out; check image and GPU scheduling")
        pod = pods[0]
        podname = pod["metadata"]["name"]
        launch["pod"] = podname
        launch["uids"]["pod/" + podname] = pod["metadata"]["uid"]
        self.save(identifier, launch)
        for file in sorted(models.rglob("*")):
            if file.relative_to(models).parts[0] not in ("model", "adapter"):
                continue  # Materialization archives stay on the trusted host.
            if file.is_symlink():
                raise ExecutorError("Serving models cannot contain links")
            if file.is_file():
                self.pods(name, launch)
                with file.open("rb") as source:
                    t.execute(
                        podname,
                        ["python3", "-c", WRITE, file.relative_to(models).as_posix()],
                        source=source,
                    )
        t.execute(
            podname,
            [
                "python3",
                "-c",
                "import pathlib; pathlib.Path('/workspace/ready').touch(exist_ok=False)",
            ],
        )

    def binding(
        self, identifier: UUID, spec: DeploymentSpec
    ) -> tuple[dict[str, Any], dict[str, Any] | None, str]:
        launch = self.launch(identifier, spec)
        name = container_name(identifier)
        if launch is None:
            self.absent(identifier)
            return {}, None, ""
        service = self.get("service", name, launch)
        pods = self.pods(name, launch)
        if service is None or not pods or not launch.get("pod"):
            return launch, None, ""
        if (
            service["spec"].get("type") != "ClusterIP"
            or service["spec"]["selector"] != {"kitchensoup.deployment": str(identifier)}
            or service["spec"]["ports"]
            != [{"name": "http", "port": 8000, "protocol": "TCP", "targetPort": "http"}]
        ):
            raise ExecutorError("Kubernetes serving Service binding mismatch")
        if len(pods) != 1 or pods[0]["metadata"]["name"] != launch["pod"]:
            return launch, None, ""
        endpoint = f"http://{name}.{self.transport.config.namespace}.svc:8000"
        return launch, pods[0], endpoint

    def status(self, identifier: UUID, spec: DeploymentSpec) -> DeploymentStatus:
        _, pod, endpoint = self.binding(identifier, spec)
        if pod is None:
            return DeploymentStatus("LOST")
        status = pod.get("status", {})
        containers = status.get("containerStatuses", [])
        if status.get("phase") == "Failed" or any(
            s.get("restartCount", 0) or s.get("state", {}).get("terminated") for s in containers
        ):
            return DeploymentStatus("FAILED")
        ready = any(
            c.get("type") == "Ready" and c.get("status") == "True"
            for c in status.get("conditions", [])
        )
        return DeploymentStatus("RUNNING" if ready else "STARTING", endpoint)

    def proxy(self, pod: str, payload: dict[str, Any]) -> bytes:
        forward = connection = None
        try:
            with client.ApiClient(self.transport.api.configuration) as api:
                forward = portforward(
                    client.CoreV1Api(api).connect_get_namespaced_pod_portforward,
                    pod,
                    self.transport.config.namespace,
                    ports="8000",
                    _request_timeout=30,
                )
                sock = forward.socket(8000)
                sock.settimeout(120)
                connection = http.client.HTTPConnection("localhost", 8000, timeout=120)
                connection.sock = sock
                connection.request(
                    "POST",
                    "/v1/chat/completions",
                    body=json.dumps(payload),
                    headers={"Content-Type": "application/json"},
                )
                response = connection.getresponse()
                data = response.read(1024**2 + 1)
                if response.status != 200 or len(data) > 1024**2:
                    raise ValueError
                return data
        except Exception:
            raise ExecutorError("Kubernetes playground inference unavailable") from None
        finally:
            if connection is not None:
                connection.close()
            if forward is not None:
                forward.close()

    def chat(self, identifier: UUID, spec: DeploymentSpec, body: ChatRequest) -> ChatResponse:
        _, pod, _ = self.binding(identifier, spec)
        if pod is None or not any(
            c.get("type") == "Ready" and c.get("status") == "True"
            for c in pod.get("status", {}).get("conditions", [])
        ):
            raise ExecutorError("Kubernetes playground is not ready")
        if any(
            s.get("restartCount", 0) for s in pod.get("status", {}).get("containerStatuses", [])
        ):
            raise ExecutorError("Kubernetes playground restarted; create a new deployment")
        try:
            data = self.proxy(
                pod["metadata"]["name"],
                {
                    "model": "tuned" if spec.adapter else "playground",
                    "messages": [m.model_dump() for m in body.messages],
                    "stream": False,
                    "max_tokens": 128,
                    "temperature": 0,
                },
            )
            return ChatResponse(content=json.loads(data)["choices"][0]["message"]["content"])
        except (ValueError, KeyError, IndexError, TypeError):
            raise ExecutorError("Invalid playground inference response") from None

    def stop(self, identifier: UUID, spec: DeploymentSpec) -> None:
        launch = self.launch(identifier, spec)
        if launch is None:
            self.absent(identifier)
            if self.workspace(identifier).exists():
                raise ExecutorError("Serving workspace has no retained identity")
            return
        t, name = self.transport, container_name(identifier)
        self.pods(name, launch)
        for kind, method in (
            ("deployment", t.apps.delete_namespaced_deployment),
            ("service", t.core.delete_namespaced_service),
            ("policy", t.network.delete_namespaced_network_policy),
        ):
            resource = self.get(kind, name, launch)
            if resource is None:
                continue
            t.call(
                method,
                name,
                body={
                    "propagationPolicy": "Foreground",
                    "preconditions": {"uid": resource["metadata"]["uid"]},
                },
                missing_ok=True,
            )
            deadline = time.monotonic() + 60
            while self.get(kind, name, launch) is not None:
                if time.monotonic() > deadline:
                    raise ExecutorError("Kubernetes serving cleanup pending; will retry")
                time.sleep(1)
        shutil.rmtree(self.workspace(identifier))
