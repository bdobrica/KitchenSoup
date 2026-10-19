from collections.abc import AsyncIterator
from typing import Annotated, cast
from uuid import UUID

import anyio
from fastapi import APIRouter, Depends, Request, Response
from starlette.concurrency import iterate_in_threadpool, run_in_threadpool
from starlette.responses import StreamingResponse

from app.deployments.schemas import (
    ChatRequest,
    ChatResponse,
    DeploymentCreate,
    DeploymentLogs,
    DeploymentView,
    LeaseExtension,
)
from app.ingestion.sources import SourceError
from app.services.deployments import DeploymentService

router = APIRouter(prefix="/api/v1/deployments", tags=["deployments"])


def deployment_service(request: Request, response: Response) -> DeploymentService:
    response.headers["Cache-Control"] = "no-store"
    service = getattr(request.app.state, "deployment_service", None)
    if service is None:
        raise SourceError(503, "Deployment requires artifact storage")
    return cast(DeploymentService, service)


Service = Annotated[DeploymentService, Depends(deployment_service)]


@router.post("", response_model=DeploymentView, status_code=202)
def create(body: DeploymentCreate, service: Service) -> DeploymentView:
    return service.create(body)


@router.get("", response_model=list[DeploymentView])
def deployments(service: Service) -> list[DeploymentView]:
    return service.list()


@router.get("/{identifier}", response_model=DeploymentView)
def get(identifier: UUID, service: Service) -> DeploymentView:
    return service.get(identifier)


@router.post("/{identifier}/stop", response_model=DeploymentView, status_code=202)
def stop(identifier: UUID, service: Service) -> DeploymentView:
    return service.action(identifier, "stop")


@router.post("/{identifier}/extend", response_model=DeploymentView)
def extend(identifier: UUID, body: LeaseExtension, service: Service) -> DeploymentView:
    return service.action(identifier, "extend", body.minutes)


@router.post("/{identifier}/keep", response_model=DeploymentView)
def keep(identifier: UUID, service: Service) -> DeploymentView:
    return service.action(identifier, "keep")


@router.post(
    "/{identifier}/chat/completions",
    response_model=ChatResponse,
    responses={200: {"content": {"text/event-stream": {"schema": {"type": "string"}}}}},
)
def chat(identifier: UUID, body: ChatRequest, service: Service) -> ChatResponse | StreamingResponse:
    if not body.stream:
        return service.chat(identifier, body)
    source = service.stream_chat(identifier, body)
    first = next(source, None)

    async def stream() -> AsyncIterator[bytes]:
        try:
            if first is not None:
                yield first
            async for chunk in iterate_in_threadpool(source):
                yield chunk
        finally:
            with anyio.CancelScope(shield=True):
                await run_in_threadpool(source.close)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


@router.get("/{identifier}/logs", response_model=DeploymentLogs)
def logs(identifier: UUID, service: Service) -> DeploymentLogs:
    return DeploymentLogs(content=service.logs(identifier))
