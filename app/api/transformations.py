from typing import Annotated, cast
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response

from app.ingestion.sources import SourceError
from app.services.transformations import TransformationService
from app.transformations.schemas import TransformCreate, TransformView

router = APIRouter(prefix="/api/v1", tags=["representations"])


def service(request: Request, response: Response) -> TransformationService:
    response.headers["Cache-Control"] = "no-store"
    result = getattr(request.app.state, "transformation_service", None)
    if result is None:
        raise SourceError(503, "Transformations require artifact storage")
    return cast(TransformationService, result)


Service = Annotated[TransformationService, Depends(service)]


@router.post(
    "/artifacts/{identifier}/transformations", response_model=TransformView, status_code=202
)
def create(identifier: UUID, body: TransformCreate, service: Service) -> TransformView:
    return service.create(identifier, body)


@router.get("/transformations/{identifier}", response_model=TransformView)
def get(identifier: UUID, service: Service) -> TransformView:
    return service.get(identifier)


@router.get("/model-versions/{identifier}/transformations", response_model=list[TransformView])
def jobs(identifier: UUID, service: Service) -> list[TransformView]:
    return service.list(identifier)
