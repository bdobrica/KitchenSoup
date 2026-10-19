"""Immutable execution-target configuration and executor construction."""

import json
from pathlib import Path
from uuid import UUID, uuid4

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.config import Settings
from app.db.models import ExecutionTarget
from app.deployments.base import DeploymentExecutor
from app.deployments.kubernetes import KubernetesDeploymentExecutor
from app.deployments.materialize import DeploymentMaterializer
from app.deployments.ssh import SshDockerDeploymentExecutor
from app.executors.base import ExecutorError, TrainingExecutor
from app.executors.kubernetes import KubernetesTrainingExecutor
from app.executors.materialize import RegisteredInputMaterializer
from app.executors.ssh_docker import SshDockerTrainingExecutor
from app.ingestion.sources import SourceError
from app.services.artifacts import ArtifactService
from app.targets.kubernetes import KubernetesTransport
from app.targets.kubernetes_schemas import KubernetesConfig
from app.targets.schemas import SshConfig, TargetCreate, TargetTest, TargetView
from app.targets.ssh import SshTransport


class KubernetesTargetTest(BaseModel):
    status: str = "ok"
    message: str = "Kubernetes namespace and GPU capacity verified"
    gpu: str
    training_image_digest: str


class TargetService:
    def __init__(self, artifacts: ArtifactService, settings: Settings) -> None:
        self.artifacts, self.factory, self.settings = artifacts, artifacts.factory, settings

    def enabled(self) -> None:
        if not self.settings.ssh_execution_enabled:
            raise SourceError(503, "SSH execution is disabled by the operator")

    @staticmethod
    def view(row: ExecutionTarget) -> TargetView:
        return TargetView(
            id=row.id,
            created_at=row.created_at,
            name=row.name,
            kind=row.kind,
            credential_ref=row.credential_ref or "",
            configuration=(
                KubernetesConfig if row.kind == "kubernetes" else SshConfig
            ).model_validate(row.configuration),
        )

    def get(self, identifier: UUID) -> TargetView:
        with self.factory() as session:
            row = session.get(ExecutionTarget, identifier)
            if row is None or row.kind not in ("ssh_docker", "kubernetes"):
                raise SourceError(404, "Execution target not found")
            return self.view(row)

    def list(self) -> list[TargetView]:
        with self.factory() as session:
            return [
                self.view(row)
                for row in session.scalars(
                    select(ExecutionTarget)
                    .where(ExecutionTarget.kind.in_(("ssh_docker", "kubernetes")))
                    .order_by(ExecutionTarget.created_at, ExecutionTarget.id)
                )
            ]

    def create(self, body: TargetCreate) -> TargetView:
        if body.kind == "kubernetes":
            self.kubernetes_enabled()
        else:
            self.enabled()
        identifier = uuid4()
        try:
            with self.factory() as session:
                session.add(
                    ExecutionTarget(
                        id=identifier,
                        name=body.name,
                        kind=body.kind,
                        credential_ref=body.credential_ref,
                        configuration=body.configuration.model_dump(mode="json", by_alias=True),
                    )
                )
                session.commit()
        except IntegrityError:
            raise SourceError(409, "An execution target with this name already exists") from None
        return self.get(identifier)

    def transport(self, identifier: UUID) -> SshTransport:
        self.enabled()
        row = self.get(identifier)
        if not isinstance(row.configuration, SshConfig):
            raise SourceError(409, "This target does not support Docker-over-SSH serving")
        return SshTransport(
            row.configuration,
            row.credential_ref,
            known_hosts=Path(self.settings.ssh_known_hosts),
            secret_directory=Path(self.settings.ssh_secret_directory),
            env_names={n.strip() for n in self.settings.ssh_env_names.split(",") if n.strip()},
        )

    def kubernetes_enabled(self) -> None:
        if not self.settings.kubernetes_execution_enabled:
            raise SourceError(503, "Kubernetes execution is disabled by the operator")

    def kubernetes_transport(self, identifier: UUID) -> KubernetesTransport:
        self.kubernetes_enabled()
        row = self.get(identifier)
        if not isinstance(row.configuration, KubernetesConfig):
            raise SourceError(409, "Kubernetes target required")
        return KubernetesTransport(
            row.configuration,
            row.credential_ref,
            Path(self.settings.kubernetes_secret_directory),
            {n.strip() for n in self.settings.kubernetes_env_names.split(",") if n.strip()},
        )

    def training(self, identifier: UUID) -> TrainingExecutor:
        if self.get(identifier).kind == "kubernetes":
            self.kubernetes_enabled()
            kubernetes_executor = KubernetesTrainingExecutor(
                Path(self.settings.kubernetes_staging_workspace) / str(identifier),
                RegisteredInputMaterializer(self.artifacts),
                self.kubernetes_transport(identifier),
            )
            (
                kubernetes_executor.memory_gib,
                kubernetes_executor.cpus,
                kubernetes_executor.timeout,
            ) = (
                self.settings.training_memory_gib,
                self.settings.training_cpus,
                self.settings.training_timeout,
            )
            return kubernetes_executor
        executor = SshDockerTrainingExecutor(
            Path(self.settings.ssh_staging_workspace) / str(identifier) / "training",
            RegisteredInputMaterializer(self.artifacts),
            self.transport(identifier),
        )
        executor.memory_gib, executor.cpus, executor.timeout = (
            self.settings.training_memory_gib,
            self.settings.training_cpus,
            self.settings.training_timeout,
        )
        return executor

    def deployment(self, identifier: UUID) -> DeploymentExecutor:
        if self.get(identifier).kind == "kubernetes":
            self.kubernetes_enabled()
            return KubernetesDeploymentExecutor(
                Path(self.settings.kubernetes_staging_workspace) / str(identifier) / "deployments",
                DeploymentMaterializer(RegisteredInputMaterializer(self.artifacts)),
                self.kubernetes_transport(identifier),
            )
        return SshDockerDeploymentExecutor(
            Path(self.settings.ssh_staging_workspace) / str(identifier) / "deployments",
            DeploymentMaterializer(RegisteredInputMaterializer(self.artifacts)),
            self.transport(identifier),
        )

    def test(self, identifier: UUID) -> TargetTest | KubernetesTargetTest:
        if self.get(identifier).kind == "kubernetes":
            kube = self.kubernetes_transport(identifier)
            # Namespace-scoped Pod view works with the documented least-privilege Role.
            kube.call(kube.core.list_namespaced_pod, limit=1)
            # Node listing is optional infrastructure visibility; permission failures
            # remain explicit rather than claiming a GPU probe ran.
            try:
                nodes = kube.core.list_node(_request_timeout=(10, 30)).items
                count = sum(int(n.status.allocatable.get("nvidia.com/gpu", "0")) for n in nodes)
            except Exception:
                raise ExecutorError(
                    "GPU capacity probe needs node-list access; execution uses namespace RBAC"
                ) from None
            if count < kube.config.gpu_count:
                raise ExecutorError("Cluster does not advertise sufficient NVIDIA GPUs")
            return KubernetesTargetTest(
                gpu=f"{count} NVIDIA GPU(s) advertised",
                training_image_digest=kube.config.training_image.rsplit("@", 1)[1],
            )
        transport = self.transport(identifier)
        uid, gid = transport.identity()
        if transport.docker(["version", "--format", "{{.Server.Version}}"]).returncode:
            raise ExecutorError("Remote Docker is unavailable to the SSH user")
        training, serving = (
            self.training(identifier).image_identity(),
            self.deployment(identifier).image_identity(),
        )
        probe = (
            'import json,torch; print(json.dumps({"cuda":torch.cuda.is_available(),'
            '"name":torch.cuda.get_device_name(0),'
            '"capability":torch.cuda.get_device_capability(0),'
            '"bf16":torch.cuda.is_bf16_supported(including_emulation=False)}))'
        )
        result = transport.docker(
            [
                "run",
                "--rm",
                "--pull",
                "never",
                "--network",
                "none",
                "--read-only",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges",
                "--pids-limit",
                "128",
                "--memory",
                "2g",
                "--memory-swap",
                "2g",
                "--cpus",
                "2",
                "--tmpfs",
                "/tmp:rw,noexec,nosuid,nodev,size=64m",
                "--gpus",
                "all",
                "--user",
                f"{uid}:{gid}",
                "--entrypoint",
                "python",
                training,
                "-c",
                probe,
            ]
        )
        try:
            data = json.loads(result.stdout)
            if result.returncode or not data["cuda"]:
                raise ValueError
            gpu = f"{data['name']}; compute {data['capability']}; native BF16 {data['bf16']}"
        except (ValueError, KeyError, TypeError):
            raise ExecutorError(
                "Remote NVIDIA container access unavailable; verify GPU runtime"
            ) from None
        return TargetTest(
            uid=uid,
            gid=gid,
            gpu=gpu,
            training_image_digest=training,
            deployment_image_digest=serving,
        )
