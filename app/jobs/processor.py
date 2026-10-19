"""Short worker commands and reconciliation, serialized by PostgreSQL claims."""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select

from app.db.models import RunpodResource, TrainingRun
from app.executors.base import ExecutorError, JobStatus
from app.executors.runpod import RunpodTrainingExecutor
from app.ingestion.sources import SourceError
from app.jobs.locking import claim
from app.jobs.states import TERMINAL, JobState
from app.services.outputs import OutputRegistrar
from app.services.runs import TrainingRunService
from app.storage.base import StorageError
from app.training.schemas import PlanPreview


class JobProcessor:
    def __init__(self, service: TrainingRunService) -> None:
        self.service = service

    def tick(self, role: str) -> None:
        if role == "reconciler":
            self.reconcile_runpod()
        states = (
            (JobState.QUEUED, JobState.PREPARING, JobState.CANCEL_REQUESTED)
            if role == "worker"
            else (JobState.SUBMITTED, JobState.RUNNING, JobState.POSTPROCESSING)
        )
        # Hints never supply parameters, images, authority or desired state.
        if role == "worker" and self.service.queue is not None:
            identifier = self.service.queue.pop()
            if identifier is not None:
                self.process(identifier, role)
        with self.service.factory() as session:
            identifiers = list(
                session.scalars(
                    select(TrainingRun.id)
                    .where(
                        TrainingRun.resolved_config["schema"].astext == "kitchensoup.local-run/v1",
                        TrainingRun.status.in_(states),
                    )
                    .order_by(TrainingRun.updated_at, TrainingRun.id)
                )
            )
        for identifier in identifiers:
            self.process(identifier, role)

    def reconcile_runpod(self) -> None:
        """Resource cleanup survives terminal run state and lost worker wakeups."""
        with self.service.factory() as session:
            owners = list(
                session.scalars(
                    select(RunpodResource.owner_id).where(
                        RunpodResource.kind == "training", RunpodResource.state != "RELEASED"
                    )
                )
            )
        for owner in owners:
            with claim(self.service.factory, owner) as acquired:
                if not acquired:
                    continue
                try:
                    executor = self.service.executor_for(owner)
                    if not isinstance(executor, RunpodTrainingExecutor):
                        continue
                    row = executor.lifecycle.resource(owner)
                    assert row is not None
                    if row.cleanup == "PENDING" or datetime.now(UTC) >= row.expires_at:
                        if row.outcome is None:
                            executor.lifecycle.pending(
                                owner,
                                JobStatus(
                                    state="FAILED", message="Runpod runtime deadline exceeded"
                                ),
                            )
                        executor.lifecycle.cleanup(owner)
                except (ExecutorError, StorageError, SourceError):
                    continue

    def process(self, identifier: UUID, role: str) -> None:
        with claim(self.service.factory, identifier) as acquired:
            if not acquired:
                return
            with self.service.factory() as session:
                row = session.get(TrainingRun, identifier)
                if row is None or row.resolved_config.get("schema") != "kitchensoup.local-run/v1":
                    return
                state = JobState(row.status)
                name, image = row.external_id or "", row.resolved_config["image_digest"]
                plan = PlanPreview.model_validate(row.resolved_config["plan"])
            try:
                if state == JobState.POSTPROCESSING:
                    self.finish(identifier)
                    return
                if role == "worker" and state == JobState.QUEUED:
                    with self.service.factory() as session:
                        row = self.service.row(session, identifier)
                        if row.status != JobState.QUEUED:
                            return
                        self.service.transition(session, row, JobState.PREPARING)
                        session.commit()
                    self.service.notify(identifier)
                    try:
                        external = self.service.executor_for(identifier).submit(
                            identifier, plan, image
                        )
                        if external != name:
                            raise ExecutorError("Unexpected external identity")
                    except (ExecutorError, StorageError, SourceError):
                        # Ambiguous launches are inspected; never resubmit an attempt.
                        self.recover(identifier, name, image)
                    else:
                        with self.service.factory() as session:
                            row = self.service.row(session, identifier)
                            if row.status == JobState.PREPARING:
                                self.service.transition(session, row, JobState.SUBMITTED)
                            session.commit()
                elif role == "worker" and state == JobState.PREPARING:
                    # Recovery after a worker crash: adopt an existing launch, or fail
                    # an abandoned preparation. Retried input always gets a new UUID.
                    self.recover(identifier, name, image)
                elif role == "worker" and state == JobState.CANCEL_REQUESTED:
                    self.observe(
                        identifier,
                        self.service.executor_for(identifier).cancel(name, image),
                        canceling=True,
                    )
                elif role == "reconciler" and state in (
                    JobState.SUBMITTED,
                    JobState.RUNNING,
                    JobState.POSTPROCESSING,
                ):
                    self.observe(
                        identifier, self.service.executor_for(identifier).status(name, image)
                    )
            except (ExecutorError, StorageError, SourceError):
                # Unavailability is not absence or failure; preserve durable state
                # and retry on the next database scan. Do not persist raw diagnostics.
                return
            finally:
                if self.service.queue is not None:
                    self.service.queue.publish(identifier)

    def recover(self, identifier: UUID, name: str, image: str) -> None:
        observation = self.service.executor_for(identifier).status(name, image)
        if observation.state == "PREPARING":
            # A created but unstarted container cannot train by itself. Stop it
            # before failing this abandoned attempt; never replay docker start.
            stopped = self.service.executor_for(identifier).cancel(name, image)
            if stopped.state in ("RUNNING", "PREPARING"):
                return
            observation = stopped if stopped.state == "SUCCEEDED" else JobStatus(state="FAILED")
        self.observe(identifier, observation, preparing=True)

    def observe(
        self,
        identifier: UUID,
        observation: JobStatus,
        *,
        preparing: bool = False,
        canceling: bool = False,
    ) -> None:
        with self.service.factory() as session:
            row = self.service.row(session, identifier)
            if row.status in TERMINAL:
                return
            # Cancellation may have been requested while submit/status was in flight.
            if row.status == JobState.CANCEL_REQUESTED and not canceling:
                return

            def change(state: JobState) -> None:
                self.service.transition(session, row, state)

            if observation.state == "MISSING":
                change(
                    JobState.CANCELED
                    if canceling
                    else JobState.FAILED
                    if preparing or row.status == JobState.POSTPROCESSING
                    else JobState.LOST
                )
            elif observation.state == "FAILED":
                change(JobState.FAILED)
            elif observation.state == "CANCELED":
                if row.status != JobState.CANCEL_REQUESTED:
                    change(JobState.CANCEL_REQUESTED)
                change(JobState.CANCELED)
            elif observation.state in ("RUNNING", "SUCCEEDED"):
                if row.status == JobState.PREPARING:
                    change(JobState.SUBMITTED)
                if row.status == JobState.SUBMITTED:
                    change(JobState.RUNNING)
                if observation.state == "SUCCEEDED":
                    if row.status in (JobState.RUNNING, JobState.CANCEL_REQUESTED):
                        change(JobState.POSTPROCESSING)
                    # Commit POSTPROCESSING before storage I/O so a restart retries
                    # registration without resubmitting or losing external success.
            session.commit()
        if observation.state == "SUCCEEDED":
            self.finish(identifier)

    def finish(self, identifier: UUID) -> None:
        with self.service.factory() as session:
            row = self.service.row(session, identifier)
            if row.status != JobState.POSTPROCESSING:
                return
            registrar = self.service.registrar
            if self.service.targets is not None and self.service.artifacts is not None:
                registrar = OutputRegistrar(
                    self.service.artifacts, self.service.executor_for(identifier)
                )
            if registrar is None:
                raise ExecutorError("Output registration requires artifact storage")
            registrar.register(session, row)
            self.service.transition(session, row, JobState.SUCCEEDED)
            session.commit()
