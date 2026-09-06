from datetime import datetime
from typing import Annotated, cast
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from app.services.artifacts import ArtifactService

router = APIRouter(prefix="/api/v1", tags=["artifacts"])


class UploadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    filename: str = Field(min_length=1, max_length=255)
    content_type: str = Field(
        default="application/octet-stream",
        min_length=1,
        max_length=255,
        pattern=r"^[a-zA-Z0-9!#$&^_.+-]+/[a-zA-Z0-9!#$&^_.+-]+$",
    )
    size_bytes: int = Field(ge=0, le=5 * 1024**3, strict=True)


class UploadResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    upload_id: UUID
    url: str
    headers: dict[str, str]
    expires_at: datetime
    complete_by: datetime


class ArtifactResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    sha256: str
    size_bytes: int
    format: str
    created_at: datetime


class DownloadResponse(BaseModel):
    url: str
    expires_in: int


def artifact_service(request: Request, response: Response) -> ArtifactService:
    response.headers["Cache-Control"] = "no-store"
    service = getattr(request.app.state, "artifact_service", None)
    if service is None:
        raise HTTPException(status_code=503, detail="Artifact storage is not configured")
    return cast(ArtifactService, service)


Service = Annotated[ArtifactService, Depends(artifact_service)]


@router.post("/artifact-uploads", response_model=UploadResponse, status_code=201)
def initiate_upload(body: UploadRequest, service: Service) -> UploadResponse:
    return UploadResponse.model_validate(
        service.initiate(body.filename, body.content_type, body.size_bytes)
    )


@router.post("/artifact-uploads/{upload_id}/complete", response_model=ArtifactResponse)
def complete_upload(upload_id: UUID, service: Service) -> ArtifactResponse:
    return ArtifactResponse.model_validate(service.complete(upload_id))


@router.get("/artifacts/{artifact_id}", response_model=ArtifactResponse)
def get_artifact(artifact_id: UUID, service: Service) -> ArtifactResponse:
    return ArtifactResponse.model_validate(service.get(artifact_id))


@router.post("/artifacts/{artifact_id}/download", response_model=DownloadResponse)
def download_artifact(artifact_id: UUID, service: Service) -> DownloadResponse:
    return DownloadResponse(url=service.download(artifact_id), expires_in=service.url_ttl)
