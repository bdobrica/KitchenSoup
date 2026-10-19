"""Operator-configured execution targets; no infrastructure fields enter AppSpec."""

from datetime import datetime
from pathlib import PurePosixPath
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.providers.schemas import ProviderConfig
from app.targets.kubernetes_schemas import KubernetesConfig
from app.targets.runpod_schemas import RunpodConfig
from app.targets.sagemaker_schemas import SageMakerConfig


class SshConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_name: Literal["kitchensoup.ssh-target/v1"] = Field(
        default="kitchensoup.ssh-target/v1", alias="schema"
    )
    host: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9.-]{0,252}$")
    port: int = Field(default=22, ge=1, le=65535)
    username: str = Field(pattern=r"^[a-z_][a-z0-9_-]{0,63}$")
    workspace: str = Field(max_length=512)
    training_image: str = Field(default="kitchensoup-soup-trainer:local", max_length=256)
    deployment_image: str = Field(default="kitchensoup-vllm:local", max_length=256)

    @field_validator("workspace")
    @classmethod
    def workspace_path(cls, value: str) -> str:
        path = PurePosixPath(value)
        if (
            not path.is_absolute()
            or len(path.parts) < 3
            or ".." in path.parts
            or str(path) != value
            or any(
                c not in "/._-abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
                for c in value
            )
        ):
            raise ValueError("Use a dedicated absolute remote directory without shell characters")
        return value

    @field_validator("training_image", "deployment_image")
    @classmethod
    def image_name(cls, value: str) -> str:
        import re

        if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._/:@-]*", value):
            raise ValueError("Use a Docker image name, not command arguments")
        return value


class TargetCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=200, pattern=r"\S")
    kind: Literal["ssh_docker", "kubernetes", "sagemaker", "runpod_pod"] = "ssh_docker"
    credential_ref: str = Field(max_length=512)
    configuration: SshConfig | KubernetesConfig | SageMakerConfig | RunpodConfig

    @model_validator(mode="after")
    def matching_kind(self) -> "TargetCreate":
        expected = {
            "ssh_docker": SshConfig,
            "kubernetes": KubernetesConfig,
            "sagemaker": SageMakerConfig,
            "runpod_pod": RunpodConfig,
        }
        if not isinstance(self.configuration, expected[self.kind]):
            raise ValueError("Target kind must match its versioned configuration")
        if self.kind == "sagemaker" and not self.credential_ref.startswith("aws-profile://"):
            raise ValueError("SageMaker requires an approved AWS profile reference")
        if self.kind != "sagemaker" and self.credential_ref.startswith("aws-profile://"):
            raise ValueError("AWS profiles are only supported for SageMaker")
        return self

    @field_validator("credential_ref")
    @classmethod
    def reference(cls, value: str) -> str:
        import re

        if re.fullmatch(r"aws-profile://[A-Za-z0-9_-][A-Za-z0-9_.-]{0,127}", value):
            return value
        ProviderConfig.validate_reference(value)
        return value


class TargetView(TargetCreate):
    id: UUID
    created_at: datetime


class TargetTest(BaseModel):
    status: Literal["ok"] = "ok"
    message: str = "SSH, Docker, GPU access and runtime images verified"
    uid: int
    gid: int
    gpu: str
    training_image_digest: str
    deployment_image_digest: str
