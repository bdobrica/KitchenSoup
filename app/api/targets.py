from typing import Annotated, cast
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response

from app.ingestion.sources import SourceError
from app.services.targets import KubernetesTargetTest, TargetService
from app.targets.runpod_schemas import RunpodTargetTest
from app.targets.sagemaker_schemas import SageMakerTargetTest
from app.targets.schemas import TargetCreate, TargetTest, TargetView

router = APIRouter(prefix="/api/v1/execution-targets", tags=["Execution targets"])


def service(request: Request, response: Response) -> TargetService:
    response.headers["Cache-Control"] = "no-store"
    value = getattr(request.app.state, "target_service", None)
    if value is None:
        raise SourceError(503, "Execution target metadata is not configured")
    return cast(TargetService, value)


Service = Annotated[TargetService, Depends(service)]


@router.get("", response_model=list[TargetView])
def list_targets(targets: Service) -> list[TargetView]:
    return targets.list()


@router.post("", response_model=TargetView, status_code=201)
def create_target(body: TargetCreate, targets: Service) -> TargetView:
    return targets.create(body)


@router.get("/{identifier}", response_model=TargetView)
def get_target(identifier: UUID, targets: Service) -> TargetView:
    return targets.get(identifier)


@router.post(
    "/{identifier}/test",
    response_model=TargetTest | KubernetesTargetTest | SageMakerTargetTest | RunpodTargetTest,
)
def test_target(
    identifier: UUID, targets: Service
) -> TargetTest | KubernetesTargetTest | SageMakerTargetTest | RunpodTargetTest:
    return targets.test(identifier)
