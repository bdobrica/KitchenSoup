import json
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError

from app.config import Settings
from app.db.models import EvaluationPrompt, EvaluationResult, EvaluationTask
from app.evaluation.schemas import SuiteCreate
from app.executors.base import ExecutorError
from app.ingestion.sources import SourceError
from app.jobs.locking import claim
from app.jobs.processor import JobProcessor
from app.main import create_app
from app.services.evaluations import EvaluationService
from app.services.runs import RunCreate
from app.services.versions import DatasetVersionService
from app.training.schemas import PlanPreview
from tests.integration.test_lineage import complete_output
from tests.integration.test_training_plans import inputs
from tests.integration.test_training_runs import setup_service, versions

__all__ = ["versions"]


class Inference:
    def __init__(self) -> None:
        self.calls: list[tuple[str, UUID | None]] = []
        self.fail = False
        self.stopped: list[UUID] = []

    def generate(
        self,
        task_id: UUID,
        run_id: UUID,
        plan: PlanPreview,
        image: str,
        prompt: str,
        adapter_id: UUID | None,
    ) -> str:
        self.calls.append((prompt, adapter_id))
        if self.fail and len(self.calls) == 2:
            raise ExecutorError("Synthetic failure")
        return ("Fine: " if adapter_id else "Base: ") + prompt + "<script>inert</script>"

    @contextmanager
    def prepare(
        self,
        task_id: UUID,
        run_id: UUID,
        plan: PlanPreview,
        image: str,
        adapter_id: UUID | None,
    ) -> Iterator[Callable[[str, bool], str]]:
        yield lambda prompt, tuned: self.generate(
            task_id, run_id, plan, image, prompt, adapter_id if tuned else None
        )

    def stop(self, task_id: UUID, image: str) -> None:
        self.stopped.append(task_id)


def test_paired_evaluation_api_preferences_reports_and_immutable_inputs(
    versions: DatasetVersionService,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    training, backend = setup_service(versions, tmp_path, monkeypatch)
    runner = Inference()
    artifacts = versions.datasets.artifacts
    service = EvaluationService(artifacts, runner)
    with TestClient(
        create_app(
            Settings(_env_file=None),
            artifact_service=artifacts,
            training_run_service=training,
            evaluation_service=service,
        )
    ) as client:
        api = "/api/v1"
        assert (
            client.post(
                api + "/evaluation-suites", json={"name": "Too short", "prompts": ["one"]}
            ).status_code
            == 422
        )
        suite = client.post(
            api + "/evaluation-suites",
            json={
                "name": "Synthetic tests",
                "prompts": ["First?", "Second?", "Third?"],
            },
        ).json()
        plan = training.plans.create(inputs(versions))
        run = training.create(RunCreate(plan_id=plan.id, evaluation_suite_id=UUID(suite["id"])))
        assert training.get(run.id).evaluation_suite_id == UUID(suite["id"])
        assert (
            client.post(api + "/evaluations", json={"training_run_id": str(run.id)}).status_code
            == 409
        )
        processor = JobProcessor(training)
        processor.tick("worker")
        complete_output(training, backend, run)
        processor.tick("reconciler")
        created = client.post(api + "/evaluations", json={"training_run_id": str(run.id)})
        assert created.status_code == 202, created.text
        task = created.json()
        assert task["status"] == "QUEUED" and not runner.calls
        assert (
            client.post(api + "/evaluations", json={"training_run_id": str(run.id)}).json()["id"]
            == task["id"]
        )
        identifier = UUID(task["id"])
        with claim(service.factory, run.id) as acquired:
            assert acquired
            service.process(identifier)
            assert not runner.calls
        service.tick()
        assert len(runner.calls) == 6
        view = client.get(api + "/evaluations/" + str(identifier)).json()
        assert view["status"] == "SUCCEEDED" and view["report_artifact_id"]
        assert view["summary"]["unreviewed"] == 3
        for comparison in view["comparisons"]:
            assert comparison["base_response"].startswith("Base: ")
            assert comparison["fine_tuned_response"].startswith("Fine: ")
        old_report = UUID(view["report_artifact_id"])
        with artifacts.store.get(artifacts.get(old_report).object_key) as body:
            saved = body.read()
        for comparison, preference in zip(
            view["comparisons"], ["base_better", "fine_tuned_better", "same"], strict=True
        ):
            reviewed = client.put(
                f"{api}/evaluations/{identifier}/preferences/{comparison['prompt_id']}",
                json={"preference": preference},
            )
            assert reviewed.status_code == 200, reviewed.text
        current = reviewed.json()
        assert current["summary"] == {
            "base_better": 1,
            "fine_tuned_better": 1,
            "same": 1,
            "unreviewed": 0,
        }
        with artifacts.store.get(artifacts.get(old_report).object_key) as body:
            assert body.read() == saved
        new_report = artifacts.get(UUID(current["report_artifact_id"]))
        with artifacts.store.get(new_report.object_key) as body:
            report = json.loads(body.read())
        assert report["schema"] == "kitchensoup.evaluation-report/v1"
        assert report["plan_sha256"] == plan.sha256 and report["summary"] == current["summary"]
        assert client.post(f"{api}/evaluations/{identifier}/report").json()[
            "report_artifact_id"
        ] == str(new_report.id)
        assert (
            client.put(
                f"{api}/evaluations/{identifier}/preferences/{uuid4()}", json={"preference": "same"}
            ).status_code
            == 404
        )
        assert (
            client.put(
                f"{api}/evaluations/{identifier}/preferences/{suite['prompts'][0]['id']}",
                json={"preference": "invented"},
            ).status_code
            == 422
        )
        assert client.get("/evaluations/" + str(identifier)).status_code == 200
        assert client.get(api + "/evaluation-suites/" + suite["id"]).json() == suite
        service.tick()
        assert len(runner.calls) == 6
        again = service.create(run.id)
        service.tick()
        assert service.get(again.id).status == "SUCCEEDED" and len(runner.calls) == 6
    with service.factory() as session:
        assert session.scalar(select(func.count()).select_from(EvaluationResult)) == 6
    changes: list[tuple[str, dict[str, str | UUID]]] = [
        (
            "UPDATE evaluation_prompts SET prompt=:value WHERE suite_id=:id",
            {"value": "changed", "id": UUID(suite["id"])},
        ),
        (
            "INSERT INTO evaluation_prompts (suite_id,position,prompt) VALUES (:id,8,:value)",
            {"id": UUID(suite["id"]), "value": "later"},
        ),
        ("UPDATE evaluation_results SET response_artifact_id=:id", {"id": old_report}),
        ("UPDATE training_runs SET evaluation_suite_id=NULL WHERE id=:id", {"id": run.id}),
    ]
    for statement, parameters in changes:
        with service.factory() as session, pytest.raises(DBAPIError, match="immutable"):
            session.execute(text(statement), parameters)


def test_evaluation_partial_failure_explicit_resume_and_worker_crash(
    versions: DatasetVersionService,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    training, backend = setup_service(versions, tmp_path, monkeypatch)
    runner = Inference()
    service = EvaluationService(versions.datasets.artifacts, runner)
    suite = service.create_suite(SuiteCreate(name="Recovery", prompts=["One", "Two", "Three"]))
    run = training.create(
        RunCreate(plan_id=training.plans.create(inputs(versions)).id, evaluation_suite_id=suite.id)
    )
    processor = JobProcessor(training)
    processor.tick("worker")
    complete_output(training, backend, run)
    processor.tick("reconciler")
    task = service.create(run.id)
    runner.fail = True
    service.tick()
    failed = service.get(task.id)
    assert failed.status == "FAILED" and failed.comparisons[0].base_response
    assert failed.comparisons[0].fine_tuned_response is None
    service.tick()
    assert len(runner.calls) == 2
    runner.fail = False
    resumed = service.create(run.id)
    service.tick()
    assert service.get(resumed.id).status == "SUCCEEDED" and len(runner.calls) == 7
    crashed = service.create(run.id)
    with service.factory() as session:
        row = session.get(EvaluationTask, crashed.id)
        assert row
        row.status = "RUNNING"
        session.commit()
    EvaluationService(service.artifacts, runner).tick()
    assert service.get(crashed.id).status == "FAILED" and runner.stopped == [crashed.id]
    assert len(runner.calls) == 7
    with pytest.raises(SourceError, match="disabled"):
        EvaluationService(service.artifacts).create(run.id)
    # Direct SQL membership/model guards cover callers outside the HTTP service.
    with service.factory() as session:
        foreign_suite = service.create_suite(SuiteCreate(name="Other", prompts=["A", "B", "C"]))
        foreign_prompt = session.get(EvaluationPrompt, foreign_suite.prompts[0].id)
        assert foreign_prompt
        existing = session.scalar(select(EvaluationResult))
        assert existing
        session.add(
            EvaluationResult(
                training_run_id=run.id,
                prompt_id=foreign_prompt.id,
                model_version_id=existing.model_version_id,
                response_artifact_id=existing.response_artifact_id,
            )
        )
        with pytest.raises(DBAPIError, match="suite mismatch"):
            session.flush()
