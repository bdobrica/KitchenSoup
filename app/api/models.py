from typing import Annotated, cast
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request

from app.registry.schemas import ArchiveImport, CatalogEntry, HuggingFaceImport, ModelView
from app.services.models import ModelService

router = APIRouter(prefix="/api/v1", tags=["models"])


def model_service(request: Request) -> ModelService:
    service = getattr(request.app.state, "model_service", None)
    if service is None:
        raise HTTPException(503, "Model registry is not configured")
    return cast(ModelService, service)


Service = Annotated[ModelService, Depends(model_service)]


@router.get("/model-catalog", response_model=list[CatalogEntry])
def catalog(service: Service) -> list[CatalogEntry]:
    return service.catalog()


@router.post("/model-catalog/{key}/register", response_model=ModelView)
def choose(key: str, service: Service) -> ModelView:
    return service.choose(key)


@router.get("/models", response_model=list[ModelView])
def list_models(service: Service) -> list[ModelView]:
    return service.list()


@router.get("/models/{model_id}", response_model=ModelView)
def get_model(model_id: UUID, service: Service) -> ModelView:
    return service.get(model_id)


@router.post("/model-imports/huggingface", response_model=ModelView, status_code=201)
def import_huggingface(body: HuggingFaceImport, service: Service) -> ModelView:
    return service.import_huggingface(body)


@router.post("/model-imports/archive", response_model=ModelView, status_code=201)
def import_archive(body: ArchiveImport, service: Service) -> ModelView:
    return service.import_archive(body)
