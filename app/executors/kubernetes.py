"""Normal Kubernetes Job executor with API-only, credential-free PVC staging."""

import json
import re
import stat
import time
from pathlib import Path, PurePosixPath
from typing import Any, Literal
from uuid import UUID, uuid4

from app.executors.base import ExecutorError, InputMaterializer, JobStatus, LogChunk
from app.executors.local_docker import LocalDockerTrainingExecutor, container_name
from app.targets.kubernetes import WRITE, KubernetesTransport, resources
from app.training.runner import file_record, read_bounded
from app.training.schemas import PlanPreview
from app.training.soup import RunnerInput, RunnerManifest


class InvalidOutput(ExecutorError):
    """Definitive output contract violation, distinct from temporary API failure."""


class KubernetesTrainingExecutor(LocalDockerTrainingExecutor):
    """Reuse the common bounded output ZIP/manifest verifier; never invoke Docker."""

    def __init__(
        self, root: Path, materializer: InputMaterializer, transport: KubernetesTransport
    ) -> None:
        super().__init__(root, materializer, image=transport.config.training_image)
        self.transport = transport

    def image_identity(self) -> str:
        return self.transport.config.training_image.rsplit("@", 1)[1]

    def launch(self, external_id: str, image: str | None = None) -> dict[str, Any]:
        try:
            value = json.loads(read_bounded(self.workspace(external_id) / "launch.json", 16384))
            if image is not None and (value["image"] != image or image != self.image_identity()):
                raise ValueError
            return dict(value)
        except (OSError, ValueError, KeyError):
            raise ExecutorError("Kubernetes launch identity is unavailable or mismatched") from None

    def verify(self, resource: dict[str, Any], launch: dict[str, Any], kind: str = "pod") -> None:
        meta = resource.get("metadata", {})
        if (
            meta.get("labels", {}).get("kitchensoup.run") != launch["run_id"]
            or meta.get("annotations", {}).get("kitchensoup.identity") != launch["nonce"]
            or meta.get("annotations", {}).get("kitchensoup.input") != launch["input_sha256"]
            or (
                launch.get("uids", {}).get(kind + "/" + meta.get("name", ""))
                not in (None, meta.get("uid"))
            )
        ):
            raise ExecutorError("Kubernetes resource identity does not match this run")

    def get(self, kind: str, name: str, launch: dict[str, Any]) -> dict[str, Any] | None:
        t = self.transport
        method = {
            "job": t.batch.read_namespaced_job,
            "pod": t.core.read_namespaced_pod,
            "pvc": t.core.read_namespaced_persistent_volume_claim,
            "policy": t.network.read_namespaced_network_policy,
        }[kind]
        result = t.call(method, name, missing_ok=True)
        if result is not None:
            self.verify(result, launch, kind)
            return dict(result)
        return None

    def transfer_pod(self, name: str, launch: dict[str, Any]) -> str:
        pod = self.get("pod", name + "-io", launch)
        if pod is None or pod.get("status", {}).get("phase") != "Running":
            raise ExecutorError("Kubernetes staging Pod is unavailable")
        return name + "-io"

    def submit(self, run_id: UUID, plan: PlanPreview, image: str) -> str:
        if image != self.image_identity() or not re.fullmatch(r"sha256:[0-9a-f]{64}", image):
            raise ExecutorError("A retained digest-pinned trainer image is required")
        name, t = container_name(run_id), self.transport
        with self.lock(name) as workspace:
            if workspace.exists():
                raise ExecutorError("Run workspace already exists; create a new run")
            workspace.mkdir(mode=0o700)
            inputs = workspace / "input"
            inputs.mkdir(mode=0o700)
            (workspace / "output").mkdir(mode=0o700)
            try:
                self.materializer.materialize(run_id, plan, inputs)
                run = RunnerInput.model_validate_json(
                    read_bounded(inputs / "run.json", 8 * 1024**2)
                )
                if run.run_id != run_id or run.plan != plan:
                    raise ExecutorError("Materialized run disagrees with the submission")
                launch: dict[str, Any] = {
                    "image": image,
                    "run_id": str(run_id),
                    "nonce": str(uuid4()),
                    "input_sha256": file_record(inputs, inputs / "run.json").sha256,
                    "plan_sha256": plan.sha256,
                    "uids": {},
                }
                (workspace / "launch.json").write_text(json.dumps(launch))
                manifests = resources(
                    name, launch, t.config, self.memory_gib, self.cpus, self.timeout
                )
                (workspace / "resources.json").write_text(json.dumps(manifests))
                for kind, method in (
                    ("policy", t.network.create_namespaced_network_policy),
                    ("pvc", t.core.create_namespaced_persistent_volume_claim),
                    ("pod", t.core.create_namespaced_pod),
                ):
                    result = t.call(method, body=manifests[kind])
                    launch["uids"][kind + "/" + result["metadata"]["name"]] = result["metadata"][
                        "uid"
                    ]
                    (workspace / "launch.json").write_text(json.dumps(launch))
                deadline = time.monotonic() + t.config.preparation_timeout
                while time.monotonic() < deadline:
                    pod = self.get("pod", name + "-io", launch)
                    if pod and pod.get("status", {}).get("phase") == "Running":
                        break
                    if pod and pod.get("status", {}).get("phase") in ("Failed", "Succeeded"):
                        raise ExecutorError("Kubernetes staging Pod failed")
                    time.sleep(1)
                else:
                    raise ExecutorError(
                        "Kubernetes staging timed out; check image, storage and scheduling"
                    )
                for path in sorted(inputs.rglob("*")):
                    if path.is_dir() and not path.is_symlink():
                        continue
                    if path.is_symlink() or not stat.S_ISREG(path.stat().st_mode):
                        raise ExecutorError("Training inputs must be regular files")
                    with path.open("rb") as source:
                        t.execute(
                            name + "-io",
                            ["python", "-c", WRITE, "input/" + path.relative_to(inputs).as_posix()],
                            source=source,
                        )
                t.execute(
                    name + "-io",
                    [
                        "python",
                        "-c",
                        "from pathlib import Path; Path('/workspace/output').mkdir(mode=0o700)",
                    ],
                )
                result = t.call(t.batch.create_namespaced_job, body=manifests["job"])
                launch["uids"]["job/" + name] = result["metadata"]["uid"]
                (workspace / "launch.json").write_text(json.dumps(launch))
            except ExecutorError:
                raise
            except Exception:
                raise ExecutorError(
                    "Kubernetes input preparation failed; retry by cloning this run"
                ) from None
        return name

    def inspect(self, external_id: str, image: str) -> dict[str, Any] | None:
        workspace = self.workspace(external_id)
        if not (workspace / "launch.json").exists():
            return None
        launch = self.launch(external_id, image)
        job = self.get("job", external_id, launch)
        if job is None:
            return None
        containers = job["spec"]["template"]["spec"]["containers"]
        if len(containers) != 1 or containers[0]["image"] != self.image:
            raise ExecutorError("Kubernetes Job image does not match this run")
        pods = self.transport.call(
            self.transport.core.list_namespaced_pod,
            label_selector="kitchensoup.run=" + launch["run_id"],
        )["items"]
        trainers = []
        for pod in pods:
            if pod["metadata"]["name"] == external_id + "-io":
                continue
            self.verify(pod, launch)
            if not any(
                o["uid"] == job["metadata"]["uid"] and o.get("controller")
                for o in pod["metadata"].get("ownerReferences", [])
            ):
                raise ExecutorError("Kubernetes Pod owner does not match this Job")
            if pod["spec"]["containers"][0]["image"] != self.image:
                raise ExecutorError("Kubernetes Pod image does not match this Job")
            trainers.append(pod)
        failed = any(
            c["type"] == "Failed" and c["status"] == "True"
            for c in job.get("status", {}).get("conditions", [])
        )
        if failed:
            return {"Running": False, "Status": "exited", "ExitCode": 1}
        if not trainers:
            if any(
                c["type"] == "Complete" and c["status"] == "True"
                for c in job.get("status", {}).get("conditions", [])
            ):
                return {"Running": False, "Status": "exited", "ExitCode": 0}
            return {"Running": True, "Status": "pending"}
        if len(trainers) != 1:
            raise ExecutorError(
                "Unexpected Kubernetes retry; automatic training retries are disabled"
            )
        pod = trainers[0]
        statuses = pod.get("status", {}).get("containerStatuses", [])
        if statuses and "terminated" in statuses[0].get("state", {}):
            return {
                "Running": False,
                "Status": "exited",
                "ExitCode": statuses[0]["state"]["terminated"]["exitCode"],
            }
        if pod.get("status", {}).get("phase") == "Failed":
            return {"Running": False, "Status": "exited", "ExitCode": 1}
        # A pending Job can progress by itself, unlike a created Docker container.
        return {"Running": True, "Status": "running"}

    def collect(self, external_id: str) -> None:
        workspace = self.workspace(external_id)
        launch = self.launch(external_id)
        pod = self.transfer_pod(external_id, launch)
        data = self.transport.read(pod, "output/output-manifest.json", 8 * 1024**2)
        if not data:
            return
        root = workspace / "output"
        try:
            manifest = RunnerManifest.model_validate_json(data)
        except ValueError:
            (root / "output-manifest.json").write_bytes(data)
            return
        if (
            len(manifest.adapters) > 4096
            or len({f.path for f in manifest.adapters}) != len(manifest.adapters)
            or sum(f.size_bytes for f in manifest.adapters) > 8 * 1024**3
        ):
            raise InvalidOutput("Kubernetes output inventory exceeds its bounds")
        for item in manifest.adapters:
            path = PurePosixPath(item.path)
            if (
                not item.path.startswith("adapter/")
                or ".." in path.parts
                or path.is_absolute()
                or str(path) != item.path
            ):
                raise InvalidOutput("Invalid Kubernetes output path")
            dest = root / item.path
            if any(p.is_symlink() for p in [dest, *dest.parents]):
                raise ExecutorError("Unsafe local output staging")
            if dest.exists() and file_record(root, dest) == item:
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            with dest.open("wb") as output:
                offset = 0
                while offset < item.size_bytes:
                    chunk = self.transport.read(
                        pod, "output/" + item.path, min(1024**2, item.size_bytes - offset), offset
                    )
                    if not chunk:
                        raise InvalidOutput("Truncated Kubernetes adapter output")
                    output.write(chunk)
                    offset += len(chunk)
        (root / "output-manifest.json").write_bytes(data)

    def status(self, external_id: str, image: str) -> JobStatus:
        state = self.inspect(external_id, image)
        if state and not state["Running"] and state["ExitCode"] == 0:
            try:
                self.collect(external_id)
            except InvalidOutput:
                return JobStatus(
                    state="FAILED", exit_code=0, message="Runner output verification failed"
                )
        return super().status(external_id, image)

    def logs(self, external_id: str, stream: Literal["stdout", "stderr"], cursor: int) -> LogChunk:
        if stream not in ("stdout", "stderr") or not 0 <= cursor <= 8 * 1024**2:
            raise ExecutorError("Invalid log stream or cursor")
        workspace = self.workspace(external_id)
        if (workspace / "remote-cleaned").exists():
            return super().logs(external_id, stream, cursor)
        launch = self.launch(external_id)
        data = self.transport.read(
            self.transfer_pod(external_id, launch),
            "output/" + stream + ".log",
            min(65536, 8 * 1024**2 - cursor),
            cursor,
        )
        return LogChunk(text=data.decode(errors="replace"), cursor=cursor + len(data))

    def delete_job(self, name: str, image: str) -> None:
        launch = self.launch(name, image)
        job = self.get("job", name, launch)
        if job:
            self.transport.call(
                self.transport.batch.delete_namespaced_job,
                name,
                body={
                    "propagationPolicy": "Foreground",
                    "preconditions": {"uid": job["metadata"]["uid"]},
                },
            )
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline:
                if self.get("job", name, launch) is None:
                    return
                time.sleep(1)
            raise ExecutorError("Kubernetes Job deletion is pending; refresh shortly")

    def cancel(self, external_id: str, image: str) -> JobStatus:
        with self.lock(external_id) as workspace:
            state = self.status(external_id, image)
            if state.state in ("SUCCEEDED", "FAILED"):
                return state
            if not (workspace / "launch.json").exists():
                return JobStatus(state="MISSING")
            self.delete_job(external_id, image)
            (workspace / "canceled").touch()
            return JobStatus(state="CANCELED")

    def cleanup(self, external_id: str, image: str) -> None:
        with self.lock(external_id) as workspace:
            if not (workspace / "launch.json").exists() or (workspace / "remote-cleaned").exists():
                return
            state = self.status(external_id, image)
            if state.state in ("RUNNING", "PREPARING"):
                raise ExecutorError("Cancel the Job before cleanup")
            launch, t = self.launch(external_id, image), self.transport
            pod = self.get("pod", external_id + "-io", launch)
            if pod and pod.get("status", {}).get("phase") == "Running":
                for stream in ("stdout", "stderr"):
                    data = t.read(external_id + "-io", "output/" + stream + ".log", 8 * 1024**2)
                    (workspace / "output" / (stream + ".log")).write_bytes(data)
            self.delete_job(external_id, image)
            for kind, name, method in (
                ("pod", external_id + "-io", t.core.delete_namespaced_pod),
                ("pvc", external_id, t.core.delete_namespaced_persistent_volume_claim),
                ("policy", external_id, t.network.delete_namespaced_network_policy),
            ):
                resource = self.get(kind, name, launch)
                if resource:
                    t.call(
                        method, name, body={"preconditions": {"uid": resource["metadata"]["uid"]}}
                    )
                    deadline = time.monotonic() + 60
                    while self.get(kind, name, launch) is not None:
                        if time.monotonic() > deadline:
                            raise ExecutorError("Kubernetes cleanup is pending; refresh shortly")
                        time.sleep(1)
            (workspace / "remote-cleaned").touch()
