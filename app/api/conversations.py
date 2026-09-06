from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends

from app.api.datasets import dataset_service
from app.ingestion.selection import ChatGPTImportView, ConversationSelection, ConversationView
from app.services.conversations import ConversationService
from app.services.datasets import DatasetService

router = APIRouter(prefix="/api/v1/datasets", tags=["conversations"])


def conversation_service(
    datasets: Annotated[DatasetService, Depends(dataset_service)],
) -> ConversationService:
    return ConversationService(datasets)


Service = Annotated[ConversationService, Depends(conversation_service)]


@router.post("/{dataset_id}/sources/{source_id}/imports/chatgpt", response_model=ChatGPTImportView)
def import_chatgpt(dataset_id: UUID, source_id: UUID, service: Service) -> ChatGPTImportView:
    return service.import_source(dataset_id, source_id)


@router.get("/{dataset_id}/conversations", response_model=list[ConversationView])
def list_conversations(dataset_id: UUID, service: Service) -> list[ConversationView]:
    return service.list_conversations(dataset_id)


@router.put("/{dataset_id}/conversation-selection", response_model=list[ConversationView])
def select_conversations(
    dataset_id: UUID, body: ConversationSelection, service: Service
) -> list[ConversationView]:
    return service.select(dataset_id, body)
