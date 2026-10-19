"""Bounded operator configuration for managed training and leased serving."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class SageMakerConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_name: Literal["kitchensoup.sagemaker-target/v1"] = Field(
        default="kitchensoup.sagemaker-target/v1", alias="schema"
    )
    region: str = Field(pattern=r"^[a-z]{2}(?:-[a-z]+)+-[0-9]$")
    role_arn: str = Field(pattern=r"^arn:aws:iam::[0-9]{12}:role/[A-Za-z0-9+=,.@_/-]+$")
    training_image: str = Field(
        max_length=255,
        pattern=r"^[0-9]{12}\.dkr\.ecr\.[a-z0-9-]+\.amazonaws\.com/[a-z0-9._/-]+@sha256:[0-9a-f]{64}$",
    )
    staging_bucket: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,61}[a-z0-9]$")
    staging_prefix: str = Field(
        default="kitchensoup-training/v1",
        pattern=r"^[a-zA-Z0-9_-]+(?:/[a-zA-Z0-9_-]+)*$",
        max_length=200,
    )
    instance_type: Literal["ml.g4dn.xlarge", "ml.g5.xlarge", "ml.g6.xlarge"] = "ml.g4dn.xlarge"
    volume_gib: int = Field(default=30, ge=20, le=100)
    max_runtime_seconds: int = Field(default=600, ge=60, le=3600)
    deployment_image: str | None = Field(
        default=None,
        max_length=255,
        pattern=r"^[0-9]{12}\.dkr\.ecr\.[a-z0-9-]+\.amazonaws\.com/[a-z0-9._/-]+@sha256:[0-9a-f]{64}$",
    )
    max_deployment_lease_minutes: int = Field(default=30, ge=1, le=60)

    @model_validator(mode="after")
    def regional_image(self) -> "SageMakerConfig":
        if f".ecr.{self.region}.amazonaws.com/" not in self.training_image:
            raise ValueError("ECR image and training job must use the same region")
        if self.deployment_image and (
            f".ecr.{self.region}.amazonaws.com/" not in self.deployment_image
            or self.deployment_image == self.training_image
        ):
            raise ValueError("Serving requires a separate pinned regional ECR image")
        return self


class SageMakerTargetTest(BaseModel):
    status: Literal["ok"] = "ok"
    message: str = "Regional unversioned S3 staging and pinned ECR image verified"
    gpu: str = "Not probed; check GPU quota, job permissions and PassRole separately"
    training_image_digest: str
