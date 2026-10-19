"""Trusted Kubernetes API access. Kubeconfig plugins and external file reads are forbidden."""

import base64
import copy
import os
import stat
import time
from pathlib import Path
from typing import Any, BinaryIO

import yaml
from kubernetes import client, config
from kubernetes.stream import stream

from app.executors.base import ExecutorError
from app.providers.schemas import ProviderConfig
from app.targets.kubernetes_schemas import KubernetesConfig


def kubeconfig(reference: str, secret_directory: Path, env_names: set[str]) -> dict[str, Any]:
    """Resolve embedded-data kubeconfigs only; never invoke exec/auth-provider plugins."""
    try:
        ProviderConfig.validate_reference(reference)
        if reference.startswith("env://"):
            name = reference[6:]
            if name not in env_names:
                raise ValueError
            data = os.environ.get(name, "").encode()
        else:
            root = secret_directory.resolve(strict=True)
            path = Path(reference[7:]).resolve(strict=True)
            if not path.is_relative_to(root) or path == root:
                raise ValueError
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(fd, "rb") as source:
                info = os.fstat(source.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o007:
                    raise ValueError
                data = source.read(1024**2 + 1)
        if not data or len(data) > 1024**2:
            raise ValueError
        value = yaml.safe_load(data)
        if not isinstance(value, dict):
            raise ValueError
        # A kubeconfig is executable configuration by default. Restrict it to
        # embedded CA/client certificates or a static token and verified HTTPS.
        for entry in value.get("users", []):
            if set(entry["user"]) - {"client-certificate-data", "client-key-data", "token"}:
                raise ValueError
        for entry in value.get("clusters", []):
            cluster = entry["cluster"]
            if (
                set(cluster) - {"server", "certificate-authority-data", "tls-server-name"}
                or not cluster.get("server", "").startswith("https://")
                or not cluster.get("certificate-authority-data")
            ):
                raise ValueError
        return dict(value)
    except Exception:
        raise ExecutorError(
            "Kubeconfig reference is unavailable or unsafe; use embedded TLS data"
        ) from None


class KubernetesTransport:
    def __init__(
        self,
        target: KubernetesConfig,
        reference: str,
        secret_directory: Path,
        env_names: set[str],
    ) -> None:
        self.config = target
        value = kubeconfig(reference, secret_directory, env_names)
        try:
            configuration = client.Configuration()
            config.load_kube_config_from_dict(
                copy.deepcopy(value),
                context=target.context,
                client_configuration=configuration,
                persist_config=False,
            )
            self.api = client.ApiClient(configuration)
            self.core = client.CoreV1Api(self.api)
            self.batch = client.BatchV1Api(self.api)
            self.network = client.NetworkingV1Api(self.api)
        except Exception:
            raise ExecutorError("Kubernetes configuration could not be loaded") from None

    def call(self, method: Any, *args: Any, missing_ok: bool = False, **kwargs: Any) -> Any:
        try:
            result = method(
                *args, namespace=self.config.namespace, _request_timeout=(10, 60), **kwargs
            )
            return self.api.sanitize_for_serialization(result)
        except client.ApiException as error:
            if missing_ok and error.status == 404:
                return None
            raise ExecutorError(
                "Kubernetes API request failed; verify access and namespace"
            ) from None
        except Exception:
            raise ExecutorError("Kubernetes API unavailable; status is unknown") from None

    def execute(
        self, pod: str, command: list[str], limit: int = 65536, source: BinaryIO | None = None
    ) -> str:
        """Bound both websocket output and duration; binary inputs travel as base64 stdin."""
        # stream() temporarily replaces ApiClient.request: use a separate client
        # so REST calls never inherit the websocket transport.
        connection = None
        try:
            with client.ApiClient(self.api.configuration) as api:
                connection = stream(
                    client.CoreV1Api(api).connect_get_namespaced_pod_exec,
                    pod,
                    self.config.namespace,
                    container="transfer",
                    command=command,
                    stderr=True,
                    stdin=source is not None,
                    stdout=True,
                    tty=False,
                    _preload_content=False,
                    _request_timeout=30,
                )
                deadline, output = time.monotonic() + 120, ""
                while connection.is_open():
                    if time.monotonic() > deadline:
                        raise ValueError("timeout")
                    if source is not None:
                        chunk = source.read(48 * 1024)
                        if chunk:
                            connection.write_stdin(base64.b64encode(chunk).decode() + "\n")
                        else:
                            connection.write_stdin("END\n")
                            source = None
                    connection.update(timeout=0.01)
                    output += connection.read_stdout(timeout=0)
                    if len(output) > limit or connection.read_stderr(timeout=0):
                        raise ValueError("output")
                if connection.returncode != 0:
                    raise ValueError("exit")
                return output
        except Exception:
            raise ExecutorError(
                "Kubernetes artifact transfer failed or exceeded its bounds"
            ) from None
        finally:
            if connection is not None:
                connection.close()

    def read(self, pod: str, relative: str, limit: int, offset: int = 0) -> bytes:
        result = self.execute(
            pod,
            ["python", "-c", READ, relative, str(limit), str(offset)],
            ((limit + 2) // 3) * 4 + 16,
        )
        try:
            data = base64.b64decode(result, validate=True)
            if len(data) > limit:
                raise ValueError
            return data
        except ValueError:
            raise ExecutorError("Invalid Kubernetes transfer response") from None


# Only fixed operator code is executed. No tar extraction, shell commands or
# runner-controlled programs are used for transfer. The trainer sees /input RO.
SAFE_PATH = """
import os,sys,stat,base64,pathlib
root=pathlib.Path('/workspace')
p=root/sys.argv[1]
if not p.is_relative_to(root) or '..' in p.parts: raise ValueError('path')
if any(x.is_symlink() for x in [p,*p.parents]): raise ValueError('link')
"""
READ = (
    SAFE_PATH
    + """
try:
 fd=os.open(p,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
except FileNotFoundError:
 sys.exit(0)
with os.fdopen(fd,'rb') as f:
 if not stat.S_ISREG(os.fstat(f.fileno()).st_mode): raise ValueError('file')
 f.seek(int(sys.argv[3]))
 print(base64.b64encode(f.read(int(sys.argv[2]))).decode(),end='')
"""
)
WRITE = (
    SAFE_PATH
    + """
p.parent.mkdir(parents=True,exist_ok=True)
fd=os.open(p,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
with os.fdopen(fd,'wb') as f:
 for line in sys.stdin:
  if line == 'END\\n': break
  f.write(base64.b64decode(line.strip(),validate=True))
"""
)


def metadata(name: str, launch: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": name,
        "labels": {"kitchensoup.run": launch["run_id"]},
        "annotations": {
            "kitchensoup.identity": launch["nonce"],
            "kitchensoup.input": launch["input_sha256"],
        },
    }


def resources(
    name: str,
    launch: dict[str, Any],
    target: KubernetesConfig,
    memory_gib: int,
    cpus: int,
    timeout: int,
) -> dict[str, dict[str, Any]]:
    """Ordinary Job plus private RWO staging. No credentials or API token in Pods."""
    security = {
        "allowPrivilegeEscalation": False,
        "readOnlyRootFilesystem": True,
        "capabilities": {"drop": ["ALL"]},
    }
    common: dict[str, Any] = {
        "restartPolicy": "Never",
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
            {"name": "workspace", "persistentVolumeClaim": {"claimName": name}},
            {"name": "tmp", "emptyDir": {"sizeLimit": "512Mi"}},
            {"name": "shm", "emptyDir": {"medium": "Memory", "sizeLimit": "256Mi"}},
        ],
    }
    io = copy.deepcopy(common)
    io["containers"] = [
        {
            "name": "transfer",
            "image": target.training_image,
            "imagePullPolicy": "IfNotPresent",
            "securityContext": security,
            "command": ["python", "-c", "import threading; threading.Event().wait()"],
            "resources": {
                "requests": {"cpu": "100m", "memory": "64Mi"},
                "limits": {"cpu": "1", "memory": "256Mi"},
            },
            "volumeMounts": [
                {"name": "workspace", "mountPath": "/workspace"},
                {"name": "tmp", "mountPath": "/tmp"},
            ],
        }
    ]
    trainer = copy.deepcopy(common)
    if target.runtime_class:
        trainer["runtimeClassName"] = target.runtime_class
    trainer["affinity"] = {
        "podAffinity": {
            "requiredDuringSchedulingIgnoredDuringExecution": [
                {
                    "labelSelector": {"matchLabels": {"kitchensoup.transfer": launch["run_id"]}},
                    "topologyKey": "kubernetes.io/hostname",
                }
            ]
        }
    }
    trainer["containers"] = [
        {
            "name": "trainer",
            "image": target.training_image,
            "imagePullPolicy": "IfNotPresent",
            "securityContext": security,
            "command": [
                "python",
                "-c",
                "import os,resource,sys; "
                "resource.setrlimit(resource.RLIMIT_FSIZE,(1073741824,1073741824)); "
                "os.execv(sys.executable,[sys.executable,'/runner/entrypoint.py',*sys.argv[1:]])",
            ],
            "args": ["--image-digest", launch["image"], "--timeout", str(timeout)],
            "resources": {
                "requests": {
                    "cpu": str(cpus),
                    "memory": f"{memory_gib}Gi",
                    "nvidia.com/gpu": target.gpu_count,
                },
                "limits": {
                    "cpu": str(cpus),
                    "memory": f"{memory_gib}Gi",
                    "nvidia.com/gpu": target.gpu_count,
                },
            },
            "volumeMounts": [
                {"name": "workspace", "mountPath": "/input", "subPath": "input", "readOnly": True},
                {"name": "workspace", "mountPath": "/output", "subPath": "output"},
                {"name": "tmp", "mountPath": "/tmp"},
                {"name": "shm", "mountPath": "/dev/shm"},
            ],
        }
    ]
    pvc_spec: dict[str, Any] = {
        "accessModes": ["ReadWriteOnce"],
        "resources": {"requests": {"storage": f"{target.workspace_gib}Gi"}},
    }
    if target.storage_class:
        pvc_spec["storageClassName"] = target.storage_class
    podmeta = metadata(name + "-io", launch)
    podmeta["labels"]["kitchensoup.transfer"] = launch["run_id"]
    return {
        "policy": {
            "apiVersion": "networking.k8s.io/v1",
            "kind": "NetworkPolicy",
            "metadata": metadata(name, launch),
            "spec": {
                "podSelector": {"matchLabels": {"kitchensoup.run": launch["run_id"]}},
                "policyTypes": ["Ingress", "Egress"],
                "ingress": [],
                "egress": [],
            },
        },
        "pvc": {
            "apiVersion": "v1",
            "kind": "PersistentVolumeClaim",
            "metadata": metadata(name, launch),
            "spec": pvc_spec,
        },
        "pod": {"apiVersion": "v1", "kind": "Pod", "metadata": podmeta, "spec": io},
        "job": {
            "apiVersion": "batch/v1",
            "kind": "Job",
            "metadata": metadata(name, launch),
            "spec": {
                "backoffLimit": 0,
                "activeDeadlineSeconds": timeout + 120,
                "template": {"metadata": metadata(name, launch), "spec": trainer},
            },
        },
    }
