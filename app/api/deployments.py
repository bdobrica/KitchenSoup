from typing import Annotated, cast
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response

from app.deployments.schemas import (
    ChatRequest,
    ChatResponse,
    DeploymentCreate,
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


@router.post("/{identifier}/chat/completions", response_model=ChatResponse)
def chat(identifier: UUID, body: ChatRequest, service: Service) -> ChatResponse:
    return service.chat(identifier, body)
