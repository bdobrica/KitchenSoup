import json
import time
from collections.abc import Iterator
from typing import Annotated, Literal, cast
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from fastapi.responses import StreamingResponse
from redis.exceptions import RedisError

from app.executors.base import LogChunk
from app.ingestion.sources import SourceError
from app.jobs.states import TERMINAL
from app.services.runs import RunCreate, RunView, TrainingRunService

router = APIRouter(prefix="/api/v1/training-runs", tags=["training runs"])


def run_service(request: Request, response: Response) -> TrainingRunService:
    response.headers["Cache-Control"] = "no-store"
    service = getattr(request.app.state, "training_run_service", None)
    if service is None:
        raise SourceError(503, "Local training is disabled; enable the host Docker executor")
    return cast(TrainingRunService, service)


Service = Annotated[TrainingRunService, Depends(run_service)]


@router.post("", response_model=RunView, status_code=201)
def create_run(body: RunCreate, service: Service) -> RunView:
    return service.create(body)


@router.get("", response_model=list[RunView])
def list_runs(service: Service) -> list[RunView]:
    return service.list_runs()


@router.get("/{run_id}", response_model=RunView)
def get_run(run_id: UUID, service: Service) -> RunView:
    return service.get(run_id)


@router.get("/{run_id}/logs", response_model=LogChunk)
def run_logs(
    run_id: UUID,
    service: Service,
    stream: Literal["stdout", "stderr"] = "stdout",
    cursor: Annotated[int, Query(ge=0, le=8 * 1024**2)] = 0,
) -> LogChunk:
    return service.logs(run_id, stream, cursor)


@router.post("/{run_id}/cancel", response_model=RunView)
def cancel_run(run_id: UUID, service: Service) -> RunView:
    return service.cancel(run_id)


@router.delete("/{run_id}/container", status_code=204)
def cleanup_run(run_id: UUID, service: Service) -> None:
    service.cleanup(run_id)


@router.post("/{run_id}/clone", response_model=RunView, status_code=201)
def clone_run(run_id: UUID, service: Service) -> RunView:
    return service.clone(run_id)


@router.get(
    "/{run_id}/events",
    response_class=StreamingResponse,
    responses={200: {"content": {"text/event-stream": {}}}},
)
def run_events(
    run_id: UUID,
    service: Service,
    after: Annotated[int, Query(ge=-1, le=2147483647)] = -1,
    last_event_id: Annotated[str | None, Header()] = None,
) -> StreamingResponse:
    service.get(run_id)  # Validate existence before starting the stream.
    if last_event_id is not None:
        try:
            after = int(last_event_id)
            if not -1 <= after <= 2147483647:
                raise ValueError
        except ValueError:
            raise SourceError(422, "Invalid event cursor") from None

    def stream() -> Iterator[str]:
        cursor = after
        # Bound connection lifetime; EventSource reconnects with Last-Event-ID.
        # PostgreSQL replay repairs lost publications and disconnected browsers.
        pubsub = None
        try:
            if service.queue is not None:
                try:
                    pubsub = service.queue.subscribe()
                except RedisError:
                    if pubsub is not None:
                        pubsub.close()
                    pubsub = None
            for _ in range(30):
                for event in service.events(run_id, cursor):
                    cursor = event.sequence
                    yield f"id: {cursor}\nevent: transition\ndata: {event.model_dump_json()}\n\n"
                run = service.get(run_id)
                if run.status in TERMINAL:
                    # A transition can commit between the event query and snapshot.
                    # Drain it before closing so a completed run has full history.
                    for event in service.events(run_id, cursor):
                        cursor = event.sequence
                        yield (
                            f"id: {cursor}\nevent: transition\ndata: {event.model_dump_json()}\n\n"
                        )
                yield "event: status\ndata: " + json.dumps(run.model_dump(mode="json")) + "\n\n"
                if run.status in TERMINAL:
                    return
                if pubsub is not None:
                    try:
                        pubsub.get_message(ignore_subscribe_messages=True, timeout=2)
                    except RedisError:
                        pubsub.close()
                        pubsub = None
                else:
                    time.sleep(2)
                yield ": heartbeat\n\n"
        finally:
            if pubsub is not None:
                pubsub.close()

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-store",
            "X-Accel-Buffering": "no",
        },
    )
