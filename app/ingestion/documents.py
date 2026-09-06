"""Versioned document-ingestion contracts and replaceable Soup transport."""

import json
from typing import Literal, Protocol
from uuid import UUID

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.ingestion.sources import SourceError

MAX_INPUT = 16 * 1024**2
MAX_OUTPUT = 32 * 1024**2
SUPPORTED = {".txt", ".md", ".pdf", ".docx"}


class DocumentSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_ids: list[UUID] = Field(min_length=1, max_length=16)


class SoupResult(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    schema_name: Literal["kitchensoup.soup-ingest-response/v1"] = Field(alias="schema")
    soup_version: str = Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$")
    image_id: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    exit_code: int
    output: str = Field(max_length=MAX_OUTPUT)
    stdout: str = Field(max_length=256 * 1024)
    stderr: str = Field(max_length=256 * 1024)
    logs_truncated: bool


class DocumentRunner(Protocol):
    def ingest(self, content: bytes, extension: str) -> SoupResult: ...


class SoupHTTPRunner:
    def __init__(self, url: str) -> None:
        self.url = url.rstrip("/")

    def ingest(self, content: bytes, extension: str) -> SoupResult:
        if not self.url:
            raise SourceError(503, "Document ingestion is disabled; start the Soup runner")
        if extension not in SUPPORTED or len(content) > MAX_INPUT:
            raise SourceError(422, "Unsupported or oversized document input")
        try:
            with httpx.Client(timeout=90, trust_env=False, follow_redirects=False) as client:
                with client.stream(
                    "POST",
                    self.url + "/v1/ingest/" + extension,
                    content=content,
                    headers={"Content-Type": "application/octet-stream"},
                ) as response:
                    response.raise_for_status()
                    data = bytearray()
                    for chunk in response.iter_bytes():
                        data.extend(chunk)
                        if len(data) > 6 * MAX_OUTPUT + 4 * 1024**2:
                            raise SourceError(502, "Soup runner response exceeds its size limit")
            return SoupResult.model_validate_json(data)
        except (httpx.HTTPError, ValidationError):
            raise SourceError(
                503, "Soup runner unavailable or returned an invalid response"
            ) from None


def output_rows(result: SoupResult) -> tuple[list[dict[str, object]], int]:
    """Validate Soup's wire output; parsing documents remains exclusively in Soup."""
    rows: list[dict[str, object]] = []
    ignored = 0
    for line in result.output.splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError:
            raise SourceError(502, "Soup returned invalid JSONL") from None
        if not isinstance(row, dict) or not isinstance(row.get("text"), str):
            raise SourceError(502, "Soup returned an unsupported dataset row")
        if not row["text"].strip():
            ignored += 1
        else:
            rows.append(row)
        if len(rows) + ignored > 100_000:
            raise SourceError(502, "Soup output exceeds the row limit")
    return rows, ignored


class DocumentInputSnapshot(BaseModel):
    source_id: UUID
    artifact_id: UUID
    sha256: str
    filename: str
    license: str | None
    disposition: Literal["ingested", "ignored", "failed", "not_attempted"]
    warnings: list[str]
    example_count: int
    ignored_rows: int


class DocumentIngestionManifest(BaseModel):
    schema_name: Literal["kitchensoup.document-ingestion/v1"] = Field(alias="schema")
    ingestion_id: UUID
    dataset_id: UUID
    version: int
    status: Literal["succeeded", "failed"]
    runner_mode: Literal["soup-data-ingest/v1"] = "soup-data-ingest/v1"
    soup_version: str
    image_id: str
    command: list[str] = ["soup", "data", "ingest", "source{extension}", "--output", "output.jsonl"]
    sources: list[DocumentInputSnapshot]
    output_artifact_id: UUID | None
    logs_artifact_id: UUID
    example_count: int


class DocumentIngestionView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    dataset_id: UUID
    version: int
    status: str
    output_artifact_id: UUID | None
    logs_artifact_id: UUID
    manifest_artifact_id: UUID
    manifest: DocumentIngestionManifest
