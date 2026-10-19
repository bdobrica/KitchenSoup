"""Immutable prompt suites, durable paired generations and human preferences."""

import hashlib
import json
from collections.abc import Callable
from contextlib import AbstractContextManager
from io import BytesIO
from typing import Protocol, cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import (
    Artifact,
    ArtifactDerivation,
    EvaluationPrompt,
    EvaluationResult,
    EvaluationSuite,
    EvaluationTask,
    ModelVersion,
    TrainingRun,
)
from app.evaluation.schemas import (
    Comparison,
    EvaluationReport,
    EvaluationView,
    Preference,
    PromptView,
    SuiteCreate,
    SuiteView,
)
from app.executors.base import ExecutorError
from app.ingestion.sources import SourceError
from app.jobs.locking import claim
from app.services.artifacts import ArtifactService
from app.storage.base import StorageError
from app.training.schemas import PlanPreview


class InferenceRunner(Protocol):
    def prepare(
        self,
        task_id: UUID,
        run_id: UUID,
        plan: PlanPreview,
        image: str,
        adapter_id: UUID | None,
    ) -> AbstractContextManager[Callable[[str, bool], str]]: ...
    def stop(self, task_id: UUID, image: str) -> None: ...


class EvaluationService:
    def __init__(self, artifacts: ArtifactService, runner: InferenceRunner | None = None) -> None:
        self.artifacts, self.factory, self.runner = artifacts, artifacts.factory, runner

    def suites(self) -> list[SuiteView]:
        with self.factory() as session:
            ids = list(
                session.scalars(select(EvaluationSuite.id).order_by(EvaluationSuite.created_at))
            )
        return [self.suite(identifier) for identifier in ids]

    def suite(self, identifier: UUID) -> SuiteView:
        with self.factory() as session:
            row = session.get(EvaluationSuite, identifier)
            if row is None:
                raise SourceError(404, "Evaluation suite not found")
            return SuiteView(
                id=row.id,
                name=row.name,
                prompts=[
                    PromptView(id=p.id, position=p.position, prompt=p.prompt)
                    for p in session.scalars(
                        select(EvaluationPrompt)
                        .where(EvaluationPrompt.suite_id == identifier)
                        .order_by(EvaluationPrompt.position)
                    )
                ],
            )

    def create_suite(self, body: SuiteCreate) -> SuiteView:
        with self.factory() as session:
            row = EvaluationSuite(name=body.name)
            session.add(row)
            session.flush()
            session.add_all(
                [
                    EvaluationPrompt(suite_id=row.id, position=i, prompt=text)
                    for i, text in enumerate(body.prompts)
                ]
            )
            session.commit()
            identifier = row.id
        return self.suite(identifier)

    @staticmethod
    def run(session: Session, identifier: UUID) -> TrainingRun:
        row = session.get(TrainingRun, identifier)
        if row is None or row.resolved_config.get("schema") != "kitchensoup.local-run/v1":
            raise SourceError(404, "Local training run not found")
        if row.status != "SUCCEEDED" or row.evaluation_suite_id is None:
            raise SourceError(409, "Complete training with a preselected evaluation suite first")
        return row

    def create(self, run_id: UUID) -> EvaluationView:
        if self.runner is None:
            raise SourceError(503, "Local GPU evaluation is disabled")
        # Shared with training cleanup and evaluation processing for this run.
        with claim(self.factory, run_id) as acquired:
            if not acquired:
                raise SourceError(409, "A run operation is in progress")
            with self.factory() as session:
                run = self.run(session, run_id)
                from app.db.models import ExecutionTarget

                target = session.get(ExecutionTarget, run.execution_target_id)
                if target is not None and target.kind in (
                    "ssh_docker",
                    "kubernetes",
                    "sagemaker",
                    "runpod_pod",
                ):
                    raise SourceError(
                        409,
                        "Paired evaluation requires a local training run; "
                        "use an SSH playground for this model",
                    )
                active = session.scalar(
                    select(EvaluationTask).where(
                        EvaluationTask.training_run_id == run_id,
                        EvaluationTask.status.in_(["QUEUED", "RUNNING"]),
                    )
                )
                if active is not None:
                    identifier = active.id
                else:
                    task = EvaluationTask(training_run_id=run_id, status="QUEUED")
                    session.add(task)
                    session.commit()
                    identifier = task.id
        return self.get(identifier)

    def list(self, run_id: UUID) -> list[EvaluationView]:
        with self.factory() as session:
            ids = list(
                session.scalars(
                    select(EvaluationTask.id)
                    .where(EvaluationTask.training_run_id == run_id)
                    .order_by(EvaluationTask.created_at.desc())
                )
            )
        return [self.get(identifier) for identifier in ids]

    def response(self, identifier: UUID) -> str:
        artifact = self.artifacts.get(identifier)
        if artifact.bucket != self.artifacts.bucket:
            raise SourceError(409, "Evaluation artifact store mismatch")
        with self.artifacts.store.get(artifact.object_key, max_bytes=1024**2) as source:
            body = source.read(1024**2 + 1)
        if len(body) != artifact.size_bytes or hashlib.sha256(body).hexdigest() != artifact.sha256:
            raise SourceError(409, "Evaluation response hash mismatch")
        value = json.loads(body)
        return str(value["response"])

    def get(self, identifier: UUID) -> EvaluationView:
        with self.factory() as session:
            task = session.get(EvaluationTask, identifier)
            if task is None:
                raise SourceError(404, "Evaluation not found")
            run = self.run(session, task.training_run_id)
            tuned = session.scalar(
                select(ModelVersion).where(ModelVersion.training_run_id == run.id)
            )
            if tuned is None:
                raise SourceError(409, "Registered trained model unavailable")
            rows = list(
                session.scalars(
                    select(EvaluationResult).where(EvaluationResult.training_run_id == run.id)
                )
            )
            prompts = list(
                session.scalars(
                    select(EvaluationPrompt)
                    .where(EvaluationPrompt.suite_id == run.evaluation_suite_id)
                    .order_by(EvaluationPrompt.position)
                )
            )
            comparisons = []
            counts = dict(base_better=0, fine_tuned_better=0, same=0, unreviewed=0)
            for prompt in prompts:
                base = next(
                    (
                        r
                        for r in rows
                        if r.prompt_id == prompt.id
                        and r.model_version_id == run.base_model_version_id
                    ),
                    None,
                )
                fine = next(
                    (
                        r
                        for r in rows
                        if r.prompt_id == prompt.id and r.model_version_id == tuned.id
                    ),
                    None,
                )
                preference = cast(Preference | None, fine.preference if fine else None)
                counts[preference or "unreviewed"] += 1
                comparisons.append(
                    Comparison(
                        prompt_id=prompt.id,
                        prompt=prompt.prompt,
                        base_response=self.response(base.response_artifact_id) if base else None,
                        fine_tuned_response=self.response(fine.response_artifact_id)
                        if fine
                        else None,
                        preference=preference,
                    )
                )
            return EvaluationView(
                id=task.id,
                training_run_id=run.id,
                status=task.status,
                report_artifact_id=task.report_artifact_id,
                comparisons=comparisons,
                summary=counts,
            )

    def store(
        self, session: Session, key: str, payload: dict[str, object], format: str
    ) -> Artifact:
        if format == "kitchensoup.evaluation-report+json/v1":
            payload = EvaluationReport.model_validate(payload).model_dump(
                mode="json", by_alias=True
            )
        data = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()
        digest = hashlib.sha256(data).hexdigest()
        key = f"evaluations/v1/{key}/{digest}.json"
        existing = session.scalar(
            select(Artifact).where(
                Artifact.bucket == self.artifacts.bucket, Artifact.object_key == key
            )
        )
        if existing:
            return existing
        self.artifacts.store.put(key, BytesIO(data))
        artifact = Artifact(
            bucket=self.artifacts.bucket,
            object_key=key,
            sha256=digest,
            size_bytes=len(data),
            format=format,
        )
        session.add(artifact)
        session.flush()
        return artifact

    def report(self, identifier: UUID) -> EvaluationView:
        view = self.get(identifier)
        if view.status != "SUCCEEDED":
            raise SourceError(409, "Wait for both model responses before exporting")
        with claim(self.factory, view.training_run_id) as acquired:
            if not acquired:
                raise SourceError(409, "A run operation is in progress")
            self.write_report(identifier)
        return self.get(identifier)

    def write_report(self, identifier: UUID) -> None:
        view = self.get(identifier)
        with self.factory() as session:
            task = session.get(EvaluationTask, identifier)
            assert task is not None
            run = self.run(session, task.training_run_id)
            tuned = session.scalar(
                select(ModelVersion).where(ModelVersion.training_run_id == run.id)
            )
            assert tuned is not None
            artifact = self.store(
                session,
                str(identifier) + "/report",
                {
                    "schema": "kitchensoup.evaluation-report/v1",
                    "training_run_id": str(run.id),
                    "evaluation_suite_id": str(run.evaluation_suite_id),
                    "base_model_version_id": str(run.base_model_version_id),
                    "fine_tuned_model_version_id": str(tuned.id),
                    "plan_sha256": run.sha256,
                    "image_digest": run.resolved_config["image_digest"],
                    "generation": {
                        "max_new_tokens": 128,
                        "do_sample": False,
                        "num_beams": 1,
                        "precision": run.resolved_config["plan"]["resolved"]["training"][
                            "precision"
                        ],
                    },
                    "comparisons": [c.model_dump(mode="json") for c in view.comparisons],
                    "summary": view.summary,
                },
                "kitchensoup.evaluation-report+json/v1",
            )
            parents = list(
                session.scalars(
                    select(EvaluationResult.response_artifact_id).where(
                        EvaluationResult.training_run_id == run.id
                    )
                )
            )
            if task.report_artifact_id != artifact.id:
                # New content-addressed reports preserve older user-review snapshots.
                if not session.scalar(
                    select(ArtifactDerivation.id).where(ArtifactDerivation.child_id == artifact.id)
                ):
                    session.add_all(
                        [
                            ArtifactDerivation(
                                child_id=artifact.id,
                                parent_id=parent,
                                operation="evaluation-report",
                            )
                            for parent in parents
                        ]
                    )
                task.report_artifact_id = artifact.id
            session.commit()

    def preference(
        self, identifier: UUID, prompt_id: UUID, preference: Preference
    ) -> EvaluationView:
        view = self.get(identifier)
        if view.status != "SUCCEEDED":
            raise SourceError(409, "Wait for both model responses before reviewing")
        with claim(self.factory, view.training_run_id) as acquired:
            if not acquired:
                raise SourceError(409, "A run operation is in progress")
            with self.factory() as session:
                run = self.run(session, view.training_run_id)
                prompt = session.get(EvaluationPrompt, prompt_id)
                if prompt is None or prompt.suite_id != run.evaluation_suite_id:
                    raise SourceError(404, "Evaluation prompt not found in this suite")
                tuned = session.scalar(
                    select(ModelVersion).where(ModelVersion.training_run_id == run.id)
                )
                assert tuned is not None
                row = session.scalar(
                    select(EvaluationResult).where(
                        EvaluationResult.training_run_id == run.id,
                        EvaluationResult.prompt_id == prompt_id,
                        EvaluationResult.model_version_id == tuned.id,
                    )
                )
                assert row is not None
                row.preference = preference
                # Reports are snapshots; clear every current pointer until re-export.
                for task in session.scalars(
                    select(EvaluationTask).where(EvaluationTask.training_run_id == run.id)
                ):
                    task.report_artifact_id = None
                session.commit()
            self.write_report(identifier)
        return self.get(identifier)

    def tick(self) -> None:
        with self.factory() as session:
            ids = list(
                session.scalars(
                    select(EvaluationTask.id)
                    .where(EvaluationTask.status.in_(["QUEUED", "RUNNING"]))
                    .order_by(EvaluationTask.created_at)
                )
            )
        for identifier in ids:
            try:
                self.process(identifier)
            except (ExecutorError, StorageError):
                # Unavailable recovery cleanup remains retryable on the next scan.
                continue

    def process(self, identifier: UUID) -> None:
        if self.runner is None:
            return
        with self.factory() as session:
            task = session.get(EvaluationTask, identifier)
            if task is None:
                return
            run_id = task.training_run_id
        with claim(self.factory, run_id) as acquired:
            if not acquired:
                return
            with self.factory() as session:
                task = session.get(EvaluationTask, identifier)
                assert task is not None
                if task.status not in ("QUEUED", "RUNNING"):
                    return
                run = self.run(session, run_id)
                image = run.resolved_config["image_digest"]
                if task.status == "RUNNING":
                    # A prior worker died. Stop only the verified owned container;
                    # never silently replay uncertain GPU work.
                    self.runner.stop(identifier, image)
                    task.status = "FAILED"
                    session.commit()
                    return
                task.status = "RUNNING"
                plan = PlanPreview.model_validate(run.resolved_config["plan"])
                tuned = session.scalar(
                    select(ModelVersion).where(ModelVersion.training_run_id == run.id)
                )
                if tuned is None:
                    raise SourceError(409, "Registered trained model unavailable")
                adapter = session.scalar(
                    select(Artifact).where(
                        Artifact.model_version_id == tuned.id,
                        Artifact.format == "kitchensoup.lora-adapter+zip/v1",
                    )
                )
                if adapter is None:
                    raise SourceError(409, "Registered adapter unavailable")
                targets = [(run.base_model_version_id, None), (tuned.id, adapter.id)]
                suite_id = run.evaluation_suite_id
                assert suite_id is not None
                session.commit()
            try:
                prompts = self.suite(suite_id).prompts
                with self.factory() as session:
                    existing = set(
                        session.execute(
                            select(
                                EvaluationResult.prompt_id, EvaluationResult.model_version_id
                            ).where(EvaluationResult.training_run_id == run_id)
                        ).tuples()
                    )
                pending = [
                    (prompt, version_id, adapter_id)
                    for prompt in prompts
                    for version_id, adapter_id in targets
                    if (prompt.id, version_id) not in existing
                ]
                if pending:
                    with self.runner.prepare(
                        identifier, run_id, plan, image, adapter.id
                    ) as generate:
                        for prompt, version_id, adapter_id in pending:
                            response = generate(prompt.prompt, adapter_id is not None)
                            if len(response) > 65536:
                                raise ExecutorError("Evaluation response exceeds limit")
                            with self.factory() as session:
                                artifact = self.store(
                                    session,
                                    f"{run_id}/{prompt.id}/{version_id}",
                                    {
                                        "schema": "kitchensoup.evaluation-response/v1",
                                        "response": response,
                                        "prompt_id": str(prompt.id),
                                        "model_version_id": str(version_id),
                                    },
                                    "kitchensoup.evaluation-response+json/v1",
                                )
                                if adapter_id:
                                    session.add(
                                        ArtifactDerivation(
                                            child_id=artifact.id,
                                            parent_id=adapter_id,
                                            operation="evaluation",
                                        )
                                    )
                                session.add(
                                    EvaluationResult(
                                        training_run_id=run_id,
                                        prompt_id=prompt.id,
                                        model_version_id=version_id,
                                        response_artifact_id=artifact.id,
                                    )
                                )
                                session.commit()
                with self.factory() as session:
                    task = session.get(EvaluationTask, identifier)
                    assert task is not None
                    task.status = "SUCCEEDED"
                    session.commit()
                self.write_report(identifier)
            except (ExecutorError, StorageError, SourceError, ValueError, OSError):
                with self.factory() as session:
                    task = session.get(EvaluationTask, identifier)
                    assert task is not None
                    task.status = "FAILED"
                    session.commit()
