from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.training.schemas import PlanModelSource

MERGED = {p: f"kitchensoup.merged-{p}+zip/v1" for p in ("fp16", "bf16")}
AWQ = "kitchensoup.awq-int4+zip/v1"
LORA = "kitchensoup.lora-adapter+zip/v1"
TERMINAL = ("SUCCEEDED", "FAILED", "LOST")


class TransformCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation: Literal["merge", "awq"]
    precision: Literal["fp16", "bf16"] = "fp16"
    calibration: list[str] = Field(default_factory=list, max_length=128)

    @model_validator(mode="after")
    def calibration_rules(self) -> "TransformCreate":
        if self.operation == "awq":
            if self.precision != "fp16" or not self.calibration:
                raise ValueError("AWQ requires FP16 and explicit calibration text")
            if any(not s.strip() or len(s) > 8000 for s in self.calibration):
                raise ValueError("Calibration samples must contain 1–8000 characters")
            if sum(map(len, self.calibration)) > 65536:
                raise ValueError("Calibration exceeds 65536 characters")
        elif self.calibration:
            raise ValueError("Merge does not accept calibration text")
        return self


class TransformSpec(TransformCreate):
    schema_name: Literal["kitchensoup.transformation/v1"] = Field(
        default="kitchensoup.transformation/v1", alias="schema"
    )
    image_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    model_version_id: UUID
    artifact_id: UUID
    artifact_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    base_source: PlanModelSource


class TransformView(BaseModel):
    id: UUID
    model_version_id: UUID
    input_artifact_id: UUID
    output_artifact_id: UUID | None
    operation: Literal["merge", "awq"]
    precision: Literal["fp16", "bf16"]
    status: str
    created_at: datetime
