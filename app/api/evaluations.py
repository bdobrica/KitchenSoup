from typing import Annotated, cast
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response

from app.evaluation.schemas import (
    EvaluationView,
    PreferenceUpdate,
    SuiteCreate,
    SuiteView,
    TaskCreate,
)
from app.ingestion.sources import SourceError
from app.services.evaluations import EvaluationService

router = APIRouter(prefix="/api/v1", tags=["evaluations"])


def evaluation_service(request: Request, response: Response) -> EvaluationService:
    response.headers["Cache-Control"] = "no-store"
    service = getattr(request.app.state, "evaluation_service", None)
    if service is None:
        raise SourceError(503, "Evaluation requires artifact storage")
    return cast(EvaluationService, service)


Service = Annotated[EvaluationService, Depends(evaluation_service)]


@router.get("/evaluation-suites", response_model=list[SuiteView])
def suites(service: Service) -> list[SuiteView]:
    return service.suites()


@router.post("/evaluation-suites", response_model=SuiteView, status_code=201)
def create_suite(body: SuiteCreate, service: Service) -> SuiteView:
    return service.create_suite(body)


@router.get("/evaluation-suites/{identifier}", response_model=SuiteView)
def get_suite(identifier: UUID, service: Service) -> SuiteView:
    return service.suite(identifier)


@router.post("/evaluations", response_model=EvaluationView, status_code=202)
def create_evaluation(body: TaskCreate, service: Service) -> EvaluationView:
    return service.create(body.training_run_id)


@router.get("/evaluations", response_model=list[EvaluationView])
def evaluations(training_run_id: UUID, service: Service) -> list[EvaluationView]:
    return service.list(training_run_id)


@router.get("/evaluations/{identifier}", response_model=EvaluationView)
def get_evaluation(identifier: UUID, service: Service) -> EvaluationView:
    return service.get(identifier)


@router.put("/evaluations/{identifier}/preferences/{prompt_id}", response_model=EvaluationView)
def preference(
    identifier: UUID,
    prompt_id: UUID,
    body: PreferenceUpdate,
    service: Service,
) -> EvaluationView:
    return service.preference(identifier, prompt_id, body.preference)


@router.post("/evaluations/{identifier}/report", response_model=EvaluationView)
def report(identifier: UUID, service: Service) -> EvaluationView:
    return service.report(identifier)
