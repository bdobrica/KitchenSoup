from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request

from app.api.datasets import dataset_service
from app.ingestion.documents import DocumentIngestionView, DocumentSelection, SoupHTTPRunner
from app.services.datasets import DatasetService
from app.services.documents import DocumentIngestionService

router = APIRouter(prefix="/api/v1/datasets", tags=["document ingestion"])


def document_service(
    request: Request, datasets: Annotated[DatasetService, Depends(dataset_service)]
) -> DocumentIngestionService:
    return DocumentIngestionService(datasets, SoupHTTPRunner(request.app.state.soup_ingestion_url))


Service = Annotated[DocumentIngestionService, Depends(document_service)]


@router.post("/{dataset_id}/document-ingestions", response_model=DocumentIngestionView)
def ingest_documents(
    dataset_id: UUID, body: DocumentSelection, service: Service
) -> DocumentIngestionView:
    return service.ingest(dataset_id, body)


@router.get("/{dataset_id}/document-ingestions", response_model=list[DocumentIngestionView])
def list_ingestions(dataset_id: UUID, service: Service) -> list[DocumentIngestionView]:
    return service.list_ingestions(dataset_id)
