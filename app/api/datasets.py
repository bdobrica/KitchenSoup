from typing import Annotated, cast
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from app.api.artifacts import DownloadResponse
from app.ingestion.schemas import (
    DatasetCreate,
    DatasetSourceView,
    DatasetView,
    SourceAttach,
    SourceRemoved,
)
from app.services.datasets import DatasetService

router = APIRouter(prefix="/api/v1", tags=["datasets"])


def dataset_service(request: Request, response: Response) -> DatasetService:
    response.headers["Cache-Control"] = "no-store"
    service = getattr(request.app.state, "dataset_service", None)
    if service is None:
        raise HTTPException(503, "Dataset storage is not configured")
    return cast(DatasetService, service)


Service = Annotated[DatasetService, Depends(dataset_service)]


@router.post("/datasets", response_model=DatasetView, status_code=201)
def create_dataset(body: DatasetCreate, service: Service) -> DatasetView:
    return service.create(body)


@router.get("/datasets", response_model=list[DatasetView])
def list_datasets(service: Service) -> list[DatasetView]:
    return service.list()


@router.get("/datasets/{dataset_id}", response_model=DatasetView)
def get_dataset(dataset_id: UUID, service: Service) -> DatasetView:
    return service.get(dataset_id)


@router.post("/datasets/{dataset_id}/sources", response_model=DatasetSourceView)
def attach_source(dataset_id: UUID, body: SourceAttach, service: Service) -> DatasetSourceView:
    return service.attach(dataset_id, body)


@router.post("/datasets/{dataset_id}/sources/{source_id}/download", response_model=DownloadResponse)
def download_source(dataset_id: UUID, source_id: UUID, service: Service) -> DownloadResponse:
    return DownloadResponse(
        url=service.download(dataset_id, source_id), expires_in=service.artifacts.url_ttl
    )


@router.delete("/datasets/{dataset_id}/sources/{source_id}", response_model=SourceRemoved)
def remove_source(dataset_id: UUID, source_id: UUID, service: Service) -> SourceRemoved:
    return service.remove(dataset_id, source_id)
