"""Immutable, bounded configuration for directly managed GPU Pods."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.providers.schemas import ProviderConfig

PinnedImage = Annotated[
    str, Field(max_length=255, pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._/:-]*@sha256:[0-9a-f]{64}$")
]


class RunpodConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_name: Literal["kitchensoup.runpod-target/v1"] = Field(
        default="kitchensoup.runpod-target/v1", alias="schema"
    )
    ssh_credential_ref: str = Field(max_length=512)
    gpu_id: str = Field(min_length=1, max_length=120, pattern=r"^[a-zA-Z0-9 ._-]+$")
    allowed_gpu_ids: list[str] = Field(min_length=1, max_length=16)
    gpu_count: Literal[1] = 1
    min_vram_gb: int = Field(default=8, ge=1, le=192)
    cloud: Literal["COMMUNITY", "SECURE"] = "SECURE"
    data_center_ids: list[Annotated[str, Field(pattern=r"^[A-Z0-9-]{1,30}$")]] = Field(
        default_factory=list, max_length=16
    )
    disk_gb: int = Field(default=30, ge=20, le=100)
    training_image: PinnedImage
    transfer_mode: Literal["ssh-sftp"] = "ssh-sftp"
    ssh_host_trust: Literal["known-hosts", "provisioned-ephemeral"] = "known-hosts"
    max_hourly_price_usd: float = Field(gt=0, le=10, allow_inf_nan=False)
    max_runtime_minutes: int = Field(default=15, ge=1, le=60)
    max_concurrent_pods: int = Field(default=1, ge=1, le=4)

    @field_validator("ssh_credential_ref")
    @classmethod
    def reference(cls, value: str) -> str:
        ProviderConfig.validate_reference(value)
        return value

    @model_validator(mode="after")
    def approved_gpu(self) -> "RunpodConfig":
        if self.gpu_id not in self.allowed_gpu_ids:
            raise ValueError("Selected GPU must be in the target allowlist")
        return self


class RunpodTargetTest(BaseModel):
    status: Literal["ok"] = "ok"
    message: str = "Runpod API and GPU catalog verified; no Pod allocated"
    gpu: str
    training_image_digest: str
    hourly_price_usd: float
    maximum_compute_estimate_usd: float
    warning: str = "GPU estimate excludes storage and other charges; idle Pods remain billable"
