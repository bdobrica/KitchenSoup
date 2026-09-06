from typing import Annotated, cast
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response

from app.providers.schemas import (
    ProviderConfig,
    ProviderError,
    ProviderTestRequest,
    ProviderTestResult,
    ProviderView,
)
from app.services.providers import ProviderService

router = APIRouter(prefix="/api/v1/llm-providers", tags=["LLM providers"])


def provider_service(request: Request, response: Response) -> ProviderService:
    response.headers["Cache-Control"] = "no-store"
    service = getattr(request.app.state, "provider_service", None)
    if service is None:
        raise ProviderError(503, "Provider metadata is not configured")
    return cast(ProviderService, service)


Service = Annotated[ProviderService, Depends(provider_service)]


@router.get("", response_model=list[ProviderView])
def list_providers(service: Service) -> list[ProviderView]:
    return service.list_providers()


@router.post("", response_model=ProviderView, status_code=201)
def create_provider(body: ProviderConfig, service: Service) -> ProviderView:
    return service.save(body)


@router.put("/{provider_id}", response_model=ProviderView)
def update_provider(provider_id: UUID, body: ProviderConfig, service: Service) -> ProviderView:
    return service.save(body, provider_id)


@router.post("/{provider_id}/test", response_model=ProviderTestResult)
def test_provider(
    provider_id: UUID, body: ProviderTestRequest, service: Service
) -> ProviderTestResult:
    return service.test(provider_id, body)
