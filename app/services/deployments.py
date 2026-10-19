"""PostgreSQL-authoritative temporary serving and explicit lease actions."""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.db.models import (
    Artifact,
    Deployment,
    ExecutionTarget,
    ModelSource,
    ModelVersion,
    TrainingRun,
)
from app.deployments.base import DeploymentExecutor
from app.deployments.local import container_name
from app.deployments.schemas import (
    TERMINAL,
    ChatRequest,
    ChatResponse,
    DeploymentCreate,
    DeploymentSpec,
    DeploymentView,
)
from app.executors.base import ExecutorError
from app.ingestion.sources import SourceError
from app.jobs.locking import claim
from app.services.artifacts import ArtifactService
from app.services.targets import TargetService
from app.training.schemas import PlanModelSource, PlanPreview
from app.transformations.schemas import AWQ, MERGED


class DeploymentService:
    def __init__(
        self,
        artifacts: ArtifactService,
        executor: DeploymentExecutor | None = None,
        targets: TargetService | None = None,
    ) -> None:
        self.artifacts, self.factory, self.executor = artifacts, artifacts.factory, executor
        self.targets = targets

    def enabled(self, target_id: UUID | None = None) -> DeploymentExecutor:
        if target_id is not None:
            if self.targets is None:
                raise SourceError(503, "SSH serving is not configured")
            return self.targets.deployment(target_id)
        if self.executor is None:
            raise SourceError(503, "Local deployment is disabled; enable the host serving executor")
        return self.executor

    def executor_for(self, identifier: UUID) -> DeploymentExecutor:
        with self.factory() as session:
            row = self.row(session, identifier)
            target = session.get(ExecutionTarget, row.execution_target_id)
            if target is None or target.kind not in ("ssh_docker", "local_docker_deployment"):
                raise SourceError(503, "The retained serving target is not supported")
            return self.enabled(target.id if target.kind == "ssh_docker" else None)

    @staticmethod
    def row(session: Session, identifier: UUID, *, lock: bool = False) -> Deployment:
        query = select(Deployment).where(Deployment.id == identifier)
        row = session.scalar(query.with_for_update() if lock else query)
        if row is None or row.snapshot.get("schema") != "kitchensoup.deployment/v1":
            raise SourceError(404, "Playground deployment not found")
        return row

    @staticmethod
    def view(row: Deployment) -> DeploymentView:
        spec = DeploymentSpec.model_validate(row.snapshot)
        return DeploymentView(
            id=row.id,
            model_version_id=row.model_version_id,
            execution_target_id=row.execution_target_id,
            artifact_id=row.artifact_id,
            status=row.status,
            external_id=row.external_id or container_name(row.id),
            expires_at=row.expires_at,
            keep_running=row.keep_running,
            created_at=row.created_at,
            precision=spec.precision,
            image_digest=spec.image_digest,
        )

    def get(self, identifier: UUID) -> DeploymentView:
        with self.factory() as session:
            return self.view(self.row(session, identifier))

    def list(self) -> list[DeploymentView]:
        with self.factory() as session:
            return [
                self.view(row)
                for row in session.scalars(
                    select(Deployment)
                    .where(Deployment.snapshot["schema"].astext == "kitchensoup.deployment/v1")
                    .order_by(Deployment.created_at.desc())
                )
            ]

    def create(self, body: DeploymentCreate) -> DeploymentView:
        executor = self.enabled(body.execution_target_id)
        with self.factory() as session:
            version = session.get(ModelVersion, body.model_version_id)
            if version is None:
                raise SourceError(404, "Registered model version not found")
            adapter = version.training_run_id is not None
            if adapter:
                run = session.get(TrainingRun, version.training_run_id)
                if (
                    run is None
                    or run.status != "SUCCEEDED"
                    or run.resolved_config.get("schema") != "kitchensoup.local-run/v1"
                ):
                    raise SourceError(409, "Choose a successfully registered local training output")
                plan = PlanPreview.model_validate(run.resolved_config["plan"])
                source = plan.resolved.base_model_source
                candidates = list(
                    session.scalars(
                        select(Artifact).where(
                            Artifact.model_version_id == version.id,
                            Artifact.format.in_(
                                ["kitchensoup.lora-adapter+zip/v1", AWQ, *MERGED.values()]
                            ),
                        )
                    )
                )
            else:
                sources = list(
                    session.scalars(
                        select(ModelSource).where(ModelSource.model_version_id == version.id)
                    )
                )
                if len(sources) != 1 or sources[0].gated:
                    raise SourceError(422, "Choose one registered ungated source")
                src = sources[0]
                artifact = session.get(Artifact, src.artifact_id) if src.artifact_id else None
                # Public Hub sources can be served once represented by a registered archive.
                # Adapter deployments materialize their exact reviewed public base directly.
                if src.kind != "upload" or artifact is None:
                    raise SourceError(
                        409,
                        "Import a full model archive, or choose a registered fine-tuned adapter",
                    )
                source = PlanModelSource(
                    source_id=src.id,
                    kind="upload",
                    location=src.location,
                    revision=src.revision,
                    license=src.license,
                    artifact_id=artifact.id,
                    artifact_sha256=artifact.sha256,
                )
                candidates = [artifact]
            if body.artifact_id is None:
                defaults = [
                    a
                    for a in candidates
                    if not adapter or a.format == "kitchensoup.lora-adapter+zip/v1"
                ]
                chosen = defaults[0] if len(defaults) == 1 else None
            else:
                chosen = next((a for a in candidates if a.id == body.artifact_id), None)
            if chosen is None:
                raise SourceError(
                    422, "Select a compatible artifact belonging to this model version"
                )
            if chosen.format in (*MERGED.values(), AWQ):
                if chosen.format == AWQ and body.precision != "fp16":
                    raise SourceError(422, "AWQ serving requires FP16")
                adapter = False
                source = PlanModelSource(
                    source_id=source.source_id,
                    kind="upload",
                    location=f"artifact:{chosen.id}",
                    revision=None,
                    license=source.license,
                    artifact_id=chosen.id,
                    artifact_sha256=chosen.sha256,
                )
            spec = DeploymentSpec(
                image_digest=executor.image_identity(),
                base_source=source,
                artifact_id=chosen.id,
                artifact_sha256=chosen.sha256,
                adapter=adapter,
                representation="awq" if chosen.format == AWQ else "full",
                precision=body.precision,
            )
            if body.execution_target_id is None:
                session.execute(
                    insert(ExecutionTarget)
                    .values(
                        id=uuid4(),
                        name="local-vllm",
                        kind="local_docker_deployment",
                        configuration={},
                    )
                    .on_conflict_do_nothing(index_elements=[ExecutionTarget.name])
                )
                target = session.scalar(
                    select(ExecutionTarget).where(ExecutionTarget.name == "local-vllm")
                )
                if (
                    target is None
                    or target.kind != "local_docker_deployment"
                    or target.configuration
                ):
                    raise SourceError(409, "Local serving target configuration conflict")
            else:
                target = session.get(ExecutionTarget, body.execution_target_id)
                if target is None or target.kind != "ssh_docker":
                    raise SourceError(404, "SSH execution target not found")
            identifier = uuid4()
            session.add(
                Deployment(
                    id=identifier,
                    model_version_id=version.id,
                    artifact_id=chosen.id,
                    execution_target_id=target.id,
                    status="QUEUED",
                    external_id=container_name(identifier),
                    endpoint=None,
                    expires_at=datetime.now(UTC) + timedelta(minutes=body.lease_minutes),
                    keep_running=False,
                    snapshot=spec.model_dump(mode="json", by_alias=True),
                )
            )
            session.commit()
        return self.get(identifier)

    def action(self, identifier: UUID, action: str, minutes: int = 30) -> DeploymentView:
        self.executor_for(identifier)
        with self.factory() as session:
            row = self.row(session, identifier, lock=True)
            if row.status in TERMINAL or row.status == "STOP_REQUESTED":
                if action == "stop":
                    return self.view(row)
                raise SourceError(
                    409, "This deployment is stopping or terminal; create a new playground"
                )
            now = datetime.now(UTC)
            if action != "stop" and not row.keep_running and row.expires_at <= now:
                raise SourceError(409, "The playground lease has expired")
            if action == "stop":
                row.status = "STOP_REQUESTED"
            elif action == "keep":
                row.keep_running = True
            elif action == "extend":
                row.keep_running = False
                row.expires_at = min(
                    max(row.expires_at, now) + timedelta(minutes=minutes), now + timedelta(days=1)
                )
            session.commit()
        return self.get(identifier)

    def chat(self, identifier: UUID, body: ChatRequest) -> ChatResponse:
        executor = self.executor_for(identifier)
        with claim(self.factory, identifier) as acquired:
            if not acquired:
                raise SourceError(409, "Another deployment operation is in progress; retry shortly")
            with self.factory() as session:
                row = self.row(session, identifier)
                if row.status != "RUNNING" or (
                    not row.keep_running and row.expires_at <= datetime.now(UTC)
                ):
                    raise SourceError(409, "The playground is not ready or its lease has expired")
                spec = DeploymentSpec.model_validate(row.snapshot)
            return executor.chat(identifier, spec, body)

    def tick(self, role: str) -> None:
        if self.executor is None and self.targets is None:
            return
        with self.factory() as session:
            ids = list(
                session.scalars(
                    select(Deployment.id).where(
                        Deployment.snapshot["schema"].astext == "kitchensoup.deployment/v1",
                        Deployment.status.not_in(TERMINAL),
                    )
                )
            )
        for identifier in ids:
            try:
                self.process(identifier, role)
            except (ExecutorError, SourceError):
                # Daemon/identity/cleanup failures never falsely record a stopped GPU.
                continue

    def process(self, identifier: UUID, role: str) -> None:
        executor = self.executor_for(identifier)
        with claim(self.factory, identifier) as acquired:
            if not acquired:
                return
            with self.factory() as session:
                row = self.row(session, identifier, lock=True)
                if row.status in TERMINAL:
                    return
                spec = DeploymentSpec.model_validate(row.snapshot)
                if not row.keep_running and row.expires_at <= datetime.now(UTC):
                    row.status = "STOP_REQUESTED"
                    session.commit()
                status = row.status
                created_at = row.created_at
                if status == "QUEUED" and role == "worker":
                    row.status = "PREPARING"
                    session.commit()
            if status == "STOP_REQUESTED":
                executor.stop(identifier, spec)
                self.update(identifier, "STOPPED", None)
            elif status == "QUEUED" and role == "worker":
                try:
                    executor.deploy(identifier, spec)
                except (ExecutorError, SourceError):
                    executor.stop(identifier, spec)
                    self.update(identifier, "FAILED", None)
                    return
                self.update(identifier, "STARTING", None)
            elif status == "PREPARING" and role == "worker":
                # Never replay materialization/start after uncertain process death.
                observed = executor.status(identifier, spec)
                if observed.state == "RUNNING":
                    self.update(identifier, "RUNNING", observed.endpoint)
                else:
                    executor.stop(identifier, spec)
                    self.update(identifier, "FAILED", None)
            elif status in ("STARTING", "RUNNING") and role == "reconciler":
                observed = executor.status(identifier, spec)
                if observed.state in ("FAILED", "LOST") or (
                    observed.state == "STARTING"
                    and datetime.now(UTC) - created_at > timedelta(minutes=15)
                ):
                    executor.stop(identifier, spec)
                    self.update(identifier, "LOST" if observed.state == "LOST" else "FAILED", None)
                else:
                    self.update(identifier, observed.state, observed.endpoint)

    def update(self, identifier: UUID, status: str, endpoint: str | None) -> None:
        with self.factory() as session:
            row = self.row(session, identifier, lock=True)
            # A stop request during preparation or observation always wins.
            if row.status != "STOP_REQUESTED" or status == "STOPPED":
                row.status = status
                row.endpoint = endpoint
            session.commit()
