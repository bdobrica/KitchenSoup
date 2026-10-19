"""Immutable SSH configuration and executor construction from registered targets."""

import json
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.config import Settings
from app.db.models import ExecutionTarget
from app.deployments.materialize import DeploymentMaterializer
from app.deployments.ssh import SshDockerDeploymentExecutor
from app.executors.base import ExecutorError
from app.executors.materialize import RegisteredInputMaterializer
from app.executors.ssh_docker import SshDockerTrainingExecutor
from app.ingestion.sources import SourceError
from app.services.artifacts import ArtifactService
from app.targets.schemas import SshConfig, TargetCreate, TargetTest, TargetView
from app.targets.ssh import SshTransport


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
            credential_ref=row.credential_ref or "",
            configuration=SshConfig.model_validate(row.configuration),
        )

    def get(self, identifier: UUID) -> TargetView:
        with self.factory() as session:
            row = session.get(ExecutionTarget, identifier)
            if row is None or row.kind != "ssh_docker":
                raise SourceError(404, "SSH execution target not found")
            return self.view(row)

    def list(self) -> list[TargetView]:
        with self.factory() as session:
            return [
                self.view(row)
                for row in session.scalars(
                    select(ExecutionTarget)
                    .where(ExecutionTarget.kind == "ssh_docker")
                    .order_by(ExecutionTarget.created_at, ExecutionTarget.id)
                )
            ]

    def create(self, body: TargetCreate) -> TargetView:
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
        return SshTransport(
            row.configuration,
            row.credential_ref,
            known_hosts=Path(self.settings.ssh_known_hosts),
            secret_directory=Path(self.settings.ssh_secret_directory),
            env_names={n.strip() for n in self.settings.ssh_env_names.split(",") if n.strip()},
        )

    def training(self, identifier: UUID) -> SshDockerTrainingExecutor:
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

    def deployment(self, identifier: UUID) -> SshDockerDeploymentExecutor:
        return SshDockerDeploymentExecutor(
            Path(self.settings.ssh_staging_workspace) / str(identifier) / "deployments",
            DeploymentMaterializer(RegisteredInputMaterializer(self.artifacts)),
            self.transport(identifier),
        )

    def test(self, identifier: UUID) -> TargetTest:
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
