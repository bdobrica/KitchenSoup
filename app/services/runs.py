"""Immutable submission and PostgreSQL-authoritative lifecycle operations."""

from datetime import datetime
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.db.models import (
    EvaluationPrompt,
    EvaluationSuite,
    ExecutionTarget,
    JobEvent,
    ModelVersion,
    TrainingRun,
)
from app.executors.base import JobStatus, LogChunk, TrainingExecutor
from app.executors.local_docker import container_name
from app.ingestion.sources import SourceError
from app.jobs.locking import claim
from app.jobs.queue import JobQueue
from app.jobs.states import TERMINAL, JobState, validate_transition
from app.services.artifacts import ArtifactService
from app.services.outputs import OutputRegistrar
from app.services.targets import TargetService
from app.services.training import TrainingPlanService
from app.training.schemas import PlanPreview


class RunCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    plan_id: UUID
    evaluation_suite_id: UUID | None = None
    execution_target_id: UUID | None = None


class RunView(BaseModel):
    id: UUID
    created_at: datetime
    plan_id: UUID
    plan_sha256: str
    image_digest: str
    external_id: str
    status: JobState
    evaluation_suite_id: UUID | None = None
    execution_target_id: UUID | None = None
    observation: JobStatus | None = None
    model_version_id: UUID | None = None
    model_id: UUID | None = None


class RunEvent(BaseModel):
    sequence: int
    status: JobState
    created_at: datetime
    message: str | None


class TrainingRunService:
    def __init__(
        self,
        plans: TrainingPlanService,
        executor: TrainingExecutor | None,
        queue: JobQueue | None = None,
        artifacts: ArtifactService | None = None,
        targets: TargetService | None = None,
    ) -> None:
        self.plans, self.local_executor, self.factory = plans, executor, plans.factory
        self.queue, self.targets, self.artifacts = queue, targets, artifacts
        self.registrar = (
            OutputRegistrar(artifacts, executor)
            if artifacts is not None and executor is not None
            else None
        )

    @property
    def executor(self) -> TrainingExecutor:
        if self.local_executor is None:
            raise SourceError(503, "Local training is disabled")
        return self.local_executor

    def executor_for(self, identifier: UUID) -> TrainingExecutor:
        with self.factory() as session:
            row = self.row(session, identifier, lock=False)
            target = session.get(ExecutionTarget, row.execution_target_id)
            if target is not None and target.kind == "ssh_docker":
                if self.targets is None:
                    raise SourceError(503, "SSH execution is not configured")
                return self.targets.training(target.id)
            if target is None or target.kind != "local_docker":
                raise SourceError(503, "The retained training target is not supported")
        if self.local_executor is None:
            raise SourceError(503, "Local training is disabled")
        return self.executor

    def notify(self, identifier: UUID) -> None:
        if self.queue is not None:
            self.queue.wake(identifier)
            self.queue.publish(identifier)

    @staticmethod
    def transition(session: Session, row: TrainingRun, state: JobState) -> None:
        validate_transition(row.status, state)
        row.status = state
        # PostgreSQL atomically appends the event, even for direct SQL writers.
        session.flush()

    @staticmethod
    def view(
        row: TrainingRun, observation: JobStatus | None = None, *, session: Session | None = None
    ) -> RunView:
        version = (
            session.scalar(select(ModelVersion).where(ModelVersion.training_run_id == row.id))
            if session is not None
            else None
        )
        return RunView(
            id=row.id,
            created_at=row.created_at,
            plan_id=UUID(row.resolved_config["plan_id"]),
            plan_sha256=row.sha256,
            image_digest=row.resolved_config["image_digest"],
            external_id=row.external_id or "",
            status=JobState(row.status),
            observation=observation,
            evaluation_suite_id=row.evaluation_suite_id,
            execution_target_id=row.execution_target_id,
            model_version_id=version.id if version else None,
            model_id=version.model_id if version else None,
        )

    def row(self, session: Session, identifier: UUID, *, lock: bool = True) -> TrainingRun:
        query = select(TrainingRun).where(TrainingRun.id == identifier)
        row = session.scalar(query.with_for_update() if lock else query)
        if row is None or row.resolved_config.get("schema") != "kitchensoup.local-run/v1":
            raise SourceError(404, "Local training run not found")
        return row

    def create(self, body: RunCreate) -> RunView:
        saved = self.plans.get(body.plan_id)
        plan = PlanPreview(appspec=saved.appspec, resolved=saved.resolved, sha256=saved.sha256)
        executor = self.local_executor
        if body.execution_target_id is not None:
            if self.targets is None:
                raise SourceError(503, "SSH execution is not configured")
            executor = self.targets.training(body.execution_target_id)
        if executor is None:
            raise SourceError(503, "Choose an enabled execution target")
        image = executor.image_identity()
        identifier = uuid4()
        name = container_name(identifier)
        with self.factory() as session:
            if body.evaluation_suite_id is not None:
                if (
                    session.scalar(
                        select(EvaluationSuite)
                        .where(EvaluationSuite.id == body.evaluation_suite_id)
                        .with_for_update()
                    )
                    is None
                ):
                    raise SourceError(404, "Evaluation suite not found")
                prompts = list(
                    session.scalars(
                        select(EvaluationPrompt).where(
                            EvaluationPrompt.suite_id == body.evaluation_suite_id
                        )
                    )
                )
                if not 3 <= len(prompts) <= 10:
                    raise SourceError(422, "An evaluation suite requires 3–10 prompts")
            if body.execution_target_id is None:
                session.execute(
                    insert(ExecutionTarget)
                    .values(id=uuid4(), name="local-docker", kind="local_docker", configuration={})
                    .on_conflict_do_nothing(index_elements=[ExecutionTarget.name])
                )
                target = session.scalar(
                    select(ExecutionTarget).where(ExecutionTarget.name == "local-docker")
                )
                if target is None or target.kind != "local_docker":
                    raise SourceError(409, "The local execution target has an incompatible kind")
            else:
                target = session.get(ExecutionTarget, body.execution_target_id)
                if target is None or target.kind != "ssh_docker":
                    raise SourceError(404, "SSH execution target not found")
            row = TrainingRun(
                id=identifier,
                base_model_version_id=saved.appspec.base_model_version_id,
                dataset_version_id=saved.appspec.dataset_version_id,
                execution_target_id=target.id,
                evaluation_suite_id=body.evaluation_suite_id,
                appspec_version=saved.appspec.schema_name,
                appspec=saved.appspec.model_dump(mode="json", by_alias=True),
                resolved_config={
                    "schema": "kitchensoup.local-run/v1",
                    "plan_id": str(saved.id),
                    "image_digest": image,
                    "plan": plan.model_dump(mode="json", by_alias=True),
                },
                sha256=plan.sha256,
                status="QUEUED",
                external_id=name,
            )
            session.add(row)
            session.commit()
        self.notify(identifier)
        return self.get(identifier)

    def get(self, identifier: UUID) -> RunView:
        with self.factory() as session:
            return self.view(self.row(session, identifier, lock=False), session=session)

    def events(self, identifier: UUID, after: int = -1) -> list[RunEvent]:
        with self.factory() as session:
            self.row(session, identifier, lock=False)
            return [
                RunEvent(
                    sequence=e.sequence,
                    status=JobState(e.status),
                    created_at=e.created_at,
                    message=e.message,
                )
                for e in session.scalars(
                    select(JobEvent)
                    .where(JobEvent.training_run_id == identifier, JobEvent.sequence > after)
                    .order_by(JobEvent.sequence)
                    .limit(100)
                )
            ]

    def clone(self, identifier: UUID) -> RunView:
        with self.factory() as session:
            original = self.row(session, identifier)
            if original.status not in TERMINAL:
                raise SourceError(409, "Only a terminal run can be retried")
            new_id = uuid4()
            row = TrainingRun(
                id=new_id,
                base_model_version_id=original.base_model_version_id,
                dataset_version_id=original.dataset_version_id,
                execution_target_id=original.execution_target_id,
                evaluation_suite_id=original.evaluation_suite_id,
                appspec_version=original.appspec_version,
                appspec=original.appspec,
                resolved_config=original.resolved_config,
                sha256=original.sha256,
                status="QUEUED",
                external_id=container_name(new_id),
            )
            session.add(row)
            session.commit()
        self.notify(new_id)
        return self.get(new_id)

    def list_runs(self) -> list[RunView]:
        with self.factory() as session:
            return [
                self.view(row, session=session)
                for row in session.scalars(
                    select(TrainingRun)
                    .where(
                        TrainingRun.resolved_config["schema"].astext == "kitchensoup.local-run/v1"
                    )
                    .order_by(TrainingRun.created_at.desc(), TrainingRun.id)
                )
            ]

    def logs(self, identifier: UUID, stream: Literal["stdout", "stderr"], cursor: int) -> LogChunk:
        with self.factory() as session:
            row = self.row(session, identifier, lock=False)
            return self.executor_for(identifier).logs(row.external_id or "", stream, cursor)

    def cancel(self, identifier: UUID) -> RunView:
        with self.factory() as session:
            row = self.row(session, identifier)
            if row.status == JobState.QUEUED:
                self.transition(session, row, JobState.CANCELED)
            elif row.status in (JobState.PREPARING, JobState.SUBMITTED, JobState.RUNNING):
                self.transition(session, row, JobState.CANCEL_REQUESTED)
            session.commit()
            result = self.view(row, session=session)
        self.notify(identifier)
        return result

    def cleanup(self, identifier: UUID) -> None:
        # Coordinate with background external effects using the same database claim.
        with claim(self.factory, identifier) as acquired:
            if not acquired:
                raise SourceError(409, "Training operation in progress; refresh shortly")
            with self.factory() as session:
                row = self.row(session, identifier)
                if row.status not in TERMINAL:
                    raise SourceError(409, "Wait for a terminal run before removing its container")
                self.executor_for(identifier).cleanup(
                    row.external_id or "", row.resolved_config["image_digest"]
                )
