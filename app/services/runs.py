"""Explicit local submission and on-demand observation; durable dispatch comes later."""

from datetime import datetime
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.db.models import ExecutionTarget, TrainingRun
from app.executors.base import ExecutorError, JobStatus, LogChunk, TrainingExecutor
from app.executors.local_docker import container_name
from app.ingestion.sources import SourceError
from app.services.training import TrainingPlanService
from app.training.schemas import PlanPreview


class RunCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    plan_id: UUID


class RunView(BaseModel):
    id: UUID
    created_at: datetime
    plan_id: UUID
    plan_sha256: str
    image_digest: str
    external_id: str
    status: str
    observation: JobStatus | None = None


class TrainingRunService:
    def __init__(self, plans: TrainingPlanService, executor: TrainingExecutor) -> None:
        self.plans, self.executor, self.factory = plans, executor, plans.factory

    @staticmethod
    def view(row: TrainingRun, observation: JobStatus | None = None) -> RunView:
        return RunView(
            id=row.id,
            created_at=row.created_at,
            plan_id=UUID(row.resolved_config["plan_id"]),
            plan_sha256=row.sha256,
            image_digest=row.resolved_config["image_digest"],
            external_id=row.external_id or "",
            status=row.status,
            observation=observation,
        )

    def row(self, session: Session, identifier: UUID) -> TrainingRun:
        row = session.scalar(
            select(TrainingRun).where(TrainingRun.id == identifier).with_for_update()
        )
        if row is None or row.resolved_config.get("schema") != "kitchensoup.local-run/v1":
            raise SourceError(404, "Local training run not found")
        return row

    def create(self, body: RunCreate) -> RunView:
        saved = self.plans.get(body.plan_id)
        plan = PlanPreview(appspec=saved.appspec, resolved=saved.resolved, sha256=saved.sha256)
        image = self.executor.image_identity()
        identifier = uuid4()
        name = container_name(identifier)
        with self.factory() as session:
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
            row = TrainingRun(
                id=identifier,
                base_model_version_id=saved.appspec.base_model_version_id,
                dataset_version_id=saved.appspec.dataset_version_id,
                execution_target_id=target.id,
                appspec_version=saved.appspec.schema_name,
                appspec=saved.appspec.model_dump(mode="json", by_alias=True),
                resolved_config={
                    "schema": "kitchensoup.local-run/v1",
                    "plan_id": str(saved.id),
                    "image_digest": image,
                    "plan": plan.model_dump(mode="json", by_alias=True),
                },
                sha256=plan.sha256,
                status="PREPARING",
                external_id=name,
            )
            session.add(row)
            session.commit()
        # Persist the deterministic identity before any engine side effects.
        try:
            external_id = self.executor.submit(identifier, plan, image)
            if external_id != name:
                raise ExecutorError("Executor returned an unexpected container identity")
        except ExecutorError as error:
            # A start timeout may occur after Docker accepted the launch. Observe before
            # declaring failure; an unavailable daemon leaves the attempt recoverable.
            try:
                observation = self.executor.status(name, image)
                if observation.state == "MISSING":
                    observation = JobStatus(state="FAILED")
            except ExecutorError:
                observation = JobStatus(state="PREPARING")
            observation.message = str(error)
            with self.factory() as session:
                row = self.row(session, identifier)
                row.status = observation.state
                session.commit()
                return self.view(row, observation)
        return self.get(identifier)

    def get(self, identifier: UUID) -> RunView:
        with self.factory() as session:
            row = self.row(session, identifier)
            observation = self.executor.status(
                row.external_id or "", row.resolved_config["image_digest"]
            )
            # Never rewrite retained terminal results when containers have been removed.
            if (
                row.status not in ("SUCCEEDED", "FAILED", "CANCELED")
                and observation.state != "MISSING"
            ):
                row.status = observation.state
            session.commit()
            return self.view(row, observation)

    def list_runs(self) -> list[RunView]:
        with self.factory() as session:
            return [
                self.view(row)
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
            row = self.row(session, identifier)
            return self.executor.logs(row.external_id or "", stream, cursor)

    def cancel(self, identifier: UUID) -> RunView:
        with self.factory() as session:
            row = self.row(session, identifier)
            observation = self.executor.cancel(
                row.external_id or "", row.resolved_config["image_digest"]
            )
            if observation.state != "MISSING":
                row.status = observation.state
            session.commit()
            return self.view(row, observation)

    def cleanup(self, identifier: UUID) -> None:
        self.get(identifier)  # Retain final observation before removing the container.
        with self.factory() as session:
            row = self.row(session, identifier)
            self.executor.cleanup(row.external_id or "", row.resolved_config["image_digest"])
