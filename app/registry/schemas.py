from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator

Repository = Annotated[
    str, Field(pattern=r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,95}/[A-Za-z0-9_][A-Za-z0-9_.-]{0,95}$")
]
Revision = Annotated[
    str, Field(min_length=1, max_length=200, pattern=r"^[A-Za-z0-9_][A-Za-z0-9_./-]*$")
]
Commit = Annotated[str, Field(pattern=r"^[0-9a-f]{40}$")]
Name = Annotated[str, Field(min_length=1, max_length=200, pattern=r"\S")]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CatalogEntry(StrictModel):
    key: Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9-]{0,199}$")]
    name: Name
    repository: Repository
    revision: Commit
    architecture: str
    parameter_count: int = Field(gt=0)
    context_length: int = Field(gt=0)
    license: Name
    license_url: HttpUrl
    gated: Literal[False]
    soup_compatibility: str
    vllm_compatibility: str
    supported_recipes: list[str]
    quantization_support: list[str]
    vram_guidance: str


class Catalog(StrictModel):
    schema_version: Literal["1"]
    models: list[CatalogEntry] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_keys(self) -> "Catalog":
        if len({entry.key for entry in self.models}) != len(self.models):
            raise ValueError("Catalog keys must be unique")
        return self


class HuggingFaceImport(StrictModel):
    name: Name
    repository: Repository
    revision: Revision = "main"
    notes: str | None = Field(default=None, max_length=4000)


class ArchiveImport(StrictModel):
    name: Name
    artifact_id: UUID
    license: Name
    license_url: HttpUrl | None = None
    notes: str | None = Field(default=None, max_length=4000)


class SourceView(StrictModel):
    kind: str
    location: str
    revision: str | None
    license: str
    license_url: str | None
    gated: bool
    notes: str | None
    artifact_id: UUID | None
    catalog_entry_id: UUID | None


class VersionView(StrictModel):
    id: UUID
    version: int
    sources: list[SourceView]


class ModelView(StrictModel):
    id: UUID
    name: str
    description: str | None
    versions: list[VersionView]
