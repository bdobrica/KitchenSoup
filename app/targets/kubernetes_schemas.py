"""Versioned operator configuration for Kubernetes training."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Toleration(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: str = Field(default="", max_length=253)
    operator: Literal["Equal", "Exists"] = "Equal"
    value: str = Field(default="", max_length=63)
    effect: Literal["NoSchedule", "PreferNoSchedule", "NoExecute"]
    tolerationSeconds: int | None = Field(default=None, ge=0)


class KubernetesConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_name: Literal["kitchensoup.kubernetes-target/v1"] = Field(
        default="kitchensoup.kubernetes-target/v1", alias="schema"
    )
    namespace: str = Field(pattern=r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
    training_image: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._/:-]*@sha256:[0-9a-f]{64}$")
    deployment_image: str | None = Field(
        default=None, pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._/:-]*@sha256:[0-9a-f]{64}$"
    )
    context: str | None = Field(default=None, min_length=1, max_length=253)
    runtime_class: str | None = Field(default="nvidia", pattern=r"^[a-z0-9][a-z0-9.-]{0,252}$")
    service_account: str = Field(default="default", pattern=r"^[a-z0-9][a-z0-9.-]{0,252}$")
    gpu_count: int = Field(default=1, ge=1, le=8)
    node_selector: dict[str, str] = Field(
        default_factory=lambda: {"nvidia.com/gpu.present": "true"}, max_length=32
    )
    tolerations: list[Toleration] = Field(default_factory=list, max_length=32)
    storage_class: str | None = Field(default=None, pattern=r"^[a-z0-9][a-z0-9.-]{0,252}$")
    workspace_gib: int = Field(default=32, ge=1, le=1024)
    preparation_timeout: int = Field(default=300, ge=10, le=3600)
    cleanup_policy: Literal["explicit"] = "explicit"
