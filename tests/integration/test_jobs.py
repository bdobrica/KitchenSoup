from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from uuid import uuid4

import pytest
from sqlalchemy import Engine, select, text
from sqlalchemy.exc import DBAPIError

from app.config import Settings
from app.db.models import JobEvent, TrainingRun
from app.executors.base import JobStatus
from app.jobs.locking import claim
from app.jobs.processor import JobProcessor
from app.jobs.queue import JobQueue
from app.jobs.states import TERMINAL, TRANSITIONS, JobState
from app.services.runs import RunCreate, RunEvent, TrainingRunService
from app.services.versions import DatasetVersionService
from tests.integration.test_training_plans import inputs
from tests.integration.test_training_runs import setup_service, versions

__all__ = ["versions"]


def test_claim_duplicate_dispatch_restart_loss_and_clone(
    versions: DatasetVersionService, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, backend = setup_service(versions, tmp_path, monkeypatch)
    plan = service.plans.create(inputs(versions))
    run = service.create(RunCreate(plan_id=plan.id))
    with claim(service.factory, run.id) as acquired:
        assert acquired
        JobProcessor(service).process(run.id, "worker")
        assert service.get(run.id).status == "QUEUED"
    # New services recover solely from PostgreSQL, without any queue wakeup.
    restarted = TrainingRunService(service.plans, service.executor)
    JobProcessor(restarted).tick("worker")
    JobProcessor(restarted).tick("worker")
    assert sum(c[0] == "create" for c in backend.calls) == 1
    assert restarted.get(run.id).status == "SUBMITTED"
    backend.unavailable = True
    JobProcessor(restarted).tick("reconciler")
    assert restarted.get(run.id).status == "SUBMITTED"
    backend.unavailable = False
    JobProcessor(restarted).tick("reconciler")
    assert restarted.get(run.id).status == "RUNNING"
    backend.missing = True
    JobProcessor(restarted).tick("reconciler")
    assert restarted.get(run.id).status == "LOST"
    clone = restarted.clone(run.id)
    assert clone.id != run.id and clone.external_id != run.external_id
    assert clone.plan_sha256 == run.plan_sha256 and clone.image_digest == run.image_digest
    with service.factory() as session:
        old = session.get(TrainingRun, run.id)
        new = session.get(TrainingRun, clone.id)
        assert old is not None and new is not None
        assert old.appspec == new.appspec and old.resolved_config == new.resolved_config
    assert [e.status for e in restarted.events(run.id)] == [
        "QUEUED",
        "PREPARING",
        "SUBMITTED",
        "RUNNING",
        "LOST",
    ]


def test_cancel_during_submit_and_cancel_before_dispatch(
    versions: DatasetVersionService, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, backend = setup_service(versions, tmp_path, monkeypatch)
    plan = service.plans.create(inputs(versions))
    queued = service.create(RunCreate(plan_id=plan.id))
    assert service.cancel(queued.id).status == "CANCELED"
    JobProcessor(service).tick("worker")
    assert not any(c[0] == "create" for c in backend.calls)
    run = service.create(RunCreate(plan_id=plan.id))
    entered, release = Event(), Event()
    submit = service.executor.submit

    def blocked(*args: object) -> str:
        entered.set()
        assert release.wait(10)
        return submit(*args)  # type: ignore[arg-type]

    monkeypatch.setattr(service.executor, "submit", blocked)
    with ThreadPoolExecutor() as pool:
        future = pool.submit(JobProcessor(service).process, run.id, "worker")
        assert entered.wait(5)
        try:
            assert service.cancel(run.id).status == "CANCEL_REQUESTED"
            JobProcessor(service).process(run.id, "worker")  # claim prevents duplicate submit
        finally:
            release.set()
        future.result(timeout=10)
    assert service.get(run.id).status == "CANCEL_REQUESTED"
    JobProcessor(service).tick("worker")
    assert service.get(run.id).status == "CANCELED"
    assert service.cancel(run.id).status == "CANCELED"
    assert [e.status for e in service.events(run.id)] == [
        "QUEUED",
        "PREPARING",
        "CANCEL_REQUESTED",
        "CANCELED",
    ]


def test_recover_abandoned_preparation_without_relaunch(
    versions: DatasetVersionService, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, backend = setup_service(versions, tmp_path, monkeypatch)
    plan = service.plans.create(inputs(versions))
    run = service.create(RunCreate(plan_id=plan.id))
    with service.factory() as session:
        row = service.row(session, run.id)
        service.transition(session, row, JobState.PREPARING)
        session.commit()
    JobProcessor(service).tick("worker")
    assert service.get(run.id).status == "FAILED"
    assert not any(c[0] == "create" for c in backend.calls)


def test_successful_completion_wins_cancel_race_and_keeps_terminal_state(
    versions: DatasetVersionService, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, _ = setup_service(versions, tmp_path, monkeypatch)
    plan = service.plans.create(inputs(versions))
    run = service.create(RunCreate(plan_id=plan.id))
    JobProcessor(service).tick("worker")
    service.cancel(run.id)
    monkeypatch.setattr(service.executor, "cancel", lambda *args: JobStatus(state="SUCCEEDED"))
    JobProcessor(service).tick("worker")
    assert service.get(run.id).status == "SUCCEEDED"
    assert [e.status for e in service.events(run.id)][-2:] == ["POSTPROCESSING", "SUCCEEDED"]
    JobProcessor(service).tick("reconciler")
    assert service.cancel(run.id).status == "SUCCEEDED"


def test_postgres_transition_matrix_and_atomic_event_rollback(
    versions: DatasetVersionService, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, _ = setup_service(versions, tmp_path, monkeypatch)
    plan = service.plans.create(inputs(versions))
    # Reach each source state through a valid path, then test all outgoing edges
    # directly in SQL. Savepoints roll back both state and trigger-emitted events.
    paths = {
        "QUEUED": [],
        "PREPARING": ["PREPARING"],
        "SUBMITTED": ["PREPARING", "SUBMITTED"],
        "RUNNING": ["PREPARING", "SUBMITTED", "RUNNING"],
        "POSTPROCESSING": ["PREPARING", "SUBMITTED", "RUNNING", "POSTPROCESSING"],
        "CANCEL_REQUESTED": ["PREPARING", "CANCEL_REQUESTED"],
        "SUCCEEDED": ["PREPARING", "SUBMITTED", "RUNNING", "POSTPROCESSING", "SUCCEEDED"],
        "FAILED": ["PREPARING", "FAILED"],
        "CANCELED": ["CANCELED"],
        "LOST": ["PREPARING", "SUBMITTED", "LOST"],
    }
    for source in JobState:
        run = service.create(RunCreate(plan_id=plan.id))
        with service.factory() as session:
            for target in paths[source]:
                session.execute(
                    text("UPDATE training_runs SET status=:state WHERE id=:id"),
                    {"state": target, "id": run.id},
                )
            session.commit()
        baseline = len(service.events(run.id))
        for target in [*JobState, "MISSING"]:
            with service.factory() as session:

                def change(target: str = target, identifier: object = run.id) -> None:
                    session.execute(
                        text("UPDATE training_runs SET status=:state WHERE id=:id"),
                        {"state": target, "id": identifier},
                    )

                if target == source or target in TRANSITIONS[source]:
                    change()
                    events = list(
                        session.scalars(select(JobEvent).where(JobEvent.training_run_id == run.id))
                    )
                    assert len(events) == baseline + (target != source)
                else:
                    with pytest.raises(DBAPIError, match="Invalid job"):
                        change()
            assert len(service.events(run.id)) == baseline
        assert service.get(run.id).status == source
        assert (source in TERMINAL) == (not TRANSITIONS[source])


def test_valkey_hints_pubsub_and_outage_do_not_own_state(
    versions: DatasetVersionService, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, _ = setup_service(versions, tmp_path, monkeypatch)
    queue = JobQueue(Settings(_env_file=None).valkey_url)
    service.queue = queue
    queue.client.delete(queue.key)
    subscriber = queue.subscribe()
    subscriber.get_message(timeout=2)
    try:
        plan = service.plans.create(inputs(versions))
        run = service.create(RunCreate(plan_id=plan.id))
        message = subscriber.get_message(ignore_subscribe_messages=True, timeout=2)
        assert message is not None and message["data"] == str(run.id).encode()
        queue.wake(run.id)
        queue.wake(uuid4())  # A nonexistent UUID cannot grant execution authority.
        queue.client.lpush(queue.key, b"untrusted parameters")
        JobProcessor(service).tick("worker")
        assert service.get(run.id).status == "SUBMITTED"
        # Deleting all hints cannot lose cancellation or a queued run.
        service.cancel(run.id)
        queue.client.delete(queue.key)
        JobProcessor(service).tick("worker")
        assert service.get(run.id).status == "CANCELED"
        service.queue = JobQueue("redis://127.0.0.1:1/0")
        retry = service.clone(run.id)
        JobProcessor(service).tick("worker")
        assert service.get(retry.id).status == "SUBMITTED"
        service.queue.close()
    finally:
        subscriber.close()
        queue.client.delete(queue.key)
        queue.close()


def test_separate_process_workers_and_reconciler_survive_web_restart(
    versions: DatasetVersionService, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import subprocess
    import sys

    from fastapi.testclient import TestClient

    from app.main import create_app

    service, backend = setup_service(versions, tmp_path, monkeypatch)
    plan = service.plans.create(inputs(versions))
    with TestClient(create_app(Settings(_env_file=None), training_run_service=service)) as web:
        response = web.post("/api/v1/training-runs", json={"plan_id": str(plan.id)})
        assert response.status_code == 201
        identifier = response.json()["id"]
    # The web process is gone. A separate interpreter reconstructs all services
    # from the disposable PostgreSQL database. Only the external executor is fake.
    program = """
import sys
from pathlib import Path
from uuid import UUID
from sqlalchemy.orm import sessionmaker
from app.config import Settings
from app.db.session import create_database_engine
from app.executors.base import JobStatus
from app.executors.local_docker import container_name
from app.jobs.processor import JobProcessor
from app.services.runs import TrainingRunService
from app.services.training import TrainingPlanService
class Executor:
    def submit(self, run_id, plan, image):
        marker = Path(sys.argv[2])
        assert not marker.exists(), "duplicate launch"
        marker.write_text(str(run_id))
        return container_name(run_id)
    def status(self, name, image):
        assert Path(sys.argv[2]).read_text() == sys.argv[1]
        return JobStatus(state="RUNNING")
engine = create_database_engine(Settings(_env_file=None))
try:
    plans = TrainingPlanService(sessionmaker(engine, expire_on_commit=False))
    service = TrainingRunService(plans, Executor())
    JobProcessor(service).process(UUID(sys.argv[1]), sys.argv[3])
finally:
    engine.dispose()
"""
    marker = tmp_path / "synthetic-external-job"
    for role in ("worker", "worker", "reconciler"):
        subprocess.run(
            [sys.executable, "-c", program, identifier, str(marker), role],
            check=True,
            timeout=20,
            capture_output=True,
        )
    assert not any(c[0] == "create" for c in backend.calls)
    restarted = TrainingRunService(service.plans, service.executor)
    with TestClient(create_app(Settings(_env_file=None), training_run_service=restarted)) as web:
        assert web.get(f"/api/v1/training-runs/{identifier}").json()["status"] == "RUNNING"
    assert marker.read_text() == identifier


def test_live_sse_delivers_committed_progress_through_completion(
    versions: DatasetVersionService, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from uuid import UUID

    from fastapi.testclient import TestClient

    from app.main import create_app

    service, _ = setup_service(versions, tmp_path, monkeypatch)
    queue = JobQueue(Settings(_env_file=None).valkey_url)
    service.queue = queue
    plan = service.plans.create(inputs(versions))
    run = service.create(RunCreate(plan_id=plan.id))
    entered = Event()
    read_events = service.events

    def events(identifier: UUID, after: int = -1) -> list[RunEvent]:
        entered.set()
        return read_events(identifier, after)

    monkeypatch.setattr(service, "events", events)
    try:
        with TestClient(create_app(Settings(_env_file=None), training_run_service=service)) as web:
            with ThreadPoolExecutor() as pool:
                response = pool.submit(web.get, f"/api/v1/training-runs/{run.id}/events")
                assert entered.wait(5)
                JobProcessor(service).tick("worker")
                JobProcessor(service).tick("reconciler")
                service.cancel(run.id)
                JobProcessor(service).tick("worker")
                stream = response.result(timeout=10)
                assert stream.status_code == 200
                assert stream.headers["cache-control"] == "no-store"
                for state in (
                    "QUEUED",
                    "PREPARING",
                    "SUBMITTED",
                    "RUNNING",
                    "CANCEL_REQUESTED",
                    "CANCELED",
                ):
                    assert f'"status":"{state}"' in stream.text
                assert [line for line in stream.text.splitlines() if line.startswith("id:")] == [
                    f"id: {i}" for i in range(6)
                ]
    finally:
        queue.client.delete(queue.key)
        queue.close()


@pytest.mark.parametrize("started", [True, False])
def test_recovery_adopts_launched_container_or_stops_unstarted_container(
    versions: DatasetVersionService,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    started: bool,
) -> None:
    from app.training.schemas import PlanPreview

    service, backend = setup_service(versions, tmp_path, monkeypatch)
    plan = service.plans.create(inputs(versions))
    run = service.create(RunCreate(plan_id=plan.id))
    with service.factory() as session:
        row = service.row(session, run.id)
        service.transition(session, row, JobState.PREPARING)
        session.commit()
    # Simulate death after Docker accepted creation/start, before SUBMITTED commit.
    service.executor.submit(
        run.id,
        PlanPreview(appspec=plan.appspec, resolved=plan.resolved, sha256=plan.sha256),
        run.image_digest,
    )
    if not started:
        backend.state = "created"
    JobProcessor(TrainingRunService(service.plans, service.executor)).tick("worker")
    assert service.get(run.id).status == ("RUNNING" if started else "FAILED")
    assert sum(c[0] == "create" for c in backend.calls) == 1
    assert (backend.state == "running") == started


def test_upgrade_retains_existing_run_snapshot_and_adds_only_current_baseline(
    versions: DatasetVersionService, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from alembic.config import Config

    from alembic import command

    service, _ = setup_service(versions, tmp_path, monkeypatch)
    plan = service.plans.create(inputs(versions))
    run = service.create(RunCreate(plan_id=plan.id))
    with service.factory() as session:
        engine = session.get_bind()
        assert isinstance(engine, Engine)
        original = service.row(session, run.id).resolved_config
    # Emulate the previous release: state could change without an event. Existing
    # events survive downgrade, and upgrade records the current baseline only.
    with engine.begin() as connection:
        config = Config("alembic.ini")
        config.attributes["connection"] = connection
        command.downgrade(config, "0009_training_plan_guards")
    try:
        with engine.begin() as connection:
            connection.execute(
                text("UPDATE training_runs SET status='RUNNING' WHERE id=:id"), {"id": run.id}
            )
    finally:
        with engine.begin() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "head")
    events = service.events(run.id)
    assert [(e.sequence, e.status) for e in events] == [(0, "QUEUED"), (1, "RUNNING")]
    assert events[1].message == "Lifecycle enforcement baseline"
    with service.factory() as session:
        row = service.row(session, run.id)
        assert row.resolved_config == original
        service.transition(session, row, JobState.POSTPROCESSING)
        service.transition(session, row, JobState.SUCCEEDED)
        session.commit()
    assert [e.sequence for e in service.events(run.id)] == [0, 1, 2, 3]
