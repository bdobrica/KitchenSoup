from typing import Annotated, cast
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Request, Response

from app.ingestion.sources import SourceError
from app.services.training import TrainingPlanService
from app.training.recipes import load_recipes
from app.training.schemas import AppSpec, PlanPreview, Recipe, TrainingPlanView

router = APIRouter(prefix="/api/v1", tags=["training plans"])


def plan_service(request: Request, response: Response) -> TrainingPlanService:
    response.headers["Cache-Control"] = "no-store"
    service = getattr(request.app.state, "training_plan_service", None)
    if service is None:
        raise SourceError(503, "Training plan metadata is not configured")
    return cast(TrainingPlanService, service)


Service = Annotated[TrainingPlanService, Depends(plan_service)]


@router.get("/recipes", response_model=list[Recipe])
def recipes() -> list[Recipe]:
    return load_recipes()


@router.post("/training-plans/preview", response_model=PlanPreview)
def preview_plan(body: AppSpec, service: Service) -> PlanPreview:
    return service.preview(body)


@router.post("/training-plans", response_model=TrainingPlanView, status_code=201)
def create_plan(
    body: AppSpec,
    service: Service,
    expected_sha256: Annotated[
        str | None, Header(alias="X-Plan-SHA256", pattern=r"^[0-9a-f]{64}$")
    ] = None,
) -> TrainingPlanView:
    return service.create(body, expected_sha256)


@router.get("/training-plans", response_model=list[TrainingPlanView])
def list_plans(service: Service) -> list[TrainingPlanView]:
    return service.list_plans()


@router.get("/training-plans/{plan_id}", response_model=TrainingPlanView)
def get_plan(plan_id: UUID, service: Service) -> TrainingPlanView:
    return service.get(plan_id)
