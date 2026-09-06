from typing import Annotated, Literal, cast
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response

from app.executors.base import LogChunk
from app.ingestion.sources import SourceError
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
