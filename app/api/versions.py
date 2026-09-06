from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from app.api.datasets import dataset_service
from app.ingestion.versions import DatasetVersionCreate, DatasetVersionView, ExamplePage
from app.services.datasets import DatasetService
from app.services.versions import DatasetVersionService

router = APIRouter(prefix="/api/v1/datasets", tags=["dataset versions"])


def version_service(
    datasets: Annotated[DatasetService, Depends(dataset_service)],
) -> DatasetVersionService:
    return DatasetVersionService(datasets)


Service = Annotated[DatasetVersionService, Depends(version_service)]


@router.post("/{dataset_id}/versions", response_model=DatasetVersionView, status_code=201)
def create_version(
    dataset_id: UUID, body: DatasetVersionCreate, service: Service
) -> DatasetVersionView:
    return service.create(dataset_id, body)


@router.get("/{dataset_id}/versions", response_model=list[DatasetVersionView])
def list_versions(dataset_id: UUID, service: Service) -> list[DatasetVersionView]:
    return service.list_versions(dataset_id)


@router.get("/{dataset_id}/versions/{version_id}", response_model=DatasetVersionView)
def get_version(dataset_id: UUID, version_id: UUID, service: Service) -> DatasetVersionView:
    return service.get(dataset_id, version_id)


@router.get("/{dataset_id}/versions/{version_id}/examples", response_model=ExamplePage)
def preview_examples(
    dataset_id: UUID,
    version_id: UUID,
    service: Service,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> ExamplePage:
    return service.preview(dataset_id, version_id, offset, limit)
