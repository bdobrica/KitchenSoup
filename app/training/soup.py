"""Soup 0.74 CLI translation and immutable runner wire contracts (no Soup imports)."""

import json
import re
from pathlib import PurePosixPath
from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator

from app.training.recipes import digest
from app.training.schemas import PlanContent, PlanModel, PlanModelSource, PlanPreview


class RunnerError(ValueError):
    """Safe, application-authored runner failure."""


SOUP_VERSION = "0.74.0"
TRANSLATOR_VERSION = "kitchensoup.soup-translator/v1"


class RunnerFile(PlanModel):
    path: str = Field(min_length=1, max_length=512)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(ge=0)

    @model_validator(mode="after")
    def relative_path(self) -> "RunnerFile":
        path = PurePosixPath(self.path)
        if (
            path.is_absolute()
            or ".." in path.parts
            or "\\" in self.path
            or str(path) != self.path
            or self.path == "."
            or any(ord(c) < 32 for c in self.path)
        ):
            raise ValueError("Runner file paths must be normalized relative paths")
        return self


class RunnerInput(PlanModel):
    schema_name: Literal["kitchensoup.training-input/v1"] = Field(alias="schema")
    run_id: UUID
    plan: PlanPreview
    model_source: PlanModelSource
    model_files: list[RunnerFile] = Field(min_length=1, max_length=4096)

    @model_validator(mode="after")
    def consistent_snapshot(self) -> "RunnerInput":
        spec, resolved = self.plan.appspec, self.plan.resolved
        if digest(PlanContent(appspec=spec, resolved=resolved)) != self.plan.sha256:
            raise ValueError("Training plan hash mismatch")
        if (
            spec.base_model_version_id != resolved.base_model_version_id
            or spec.dataset_version_id != resolved.dataset_version_id
            or self.model_source != resolved.base_model_source
        ):
            raise ValueError("Training input references disagree")
        recipe = resolved.recipe
        if digest(recipe) != resolved.recipe_sha256:
            raise ValueError("Recipe hash mismatch")
        if spec.intent not in ("advanced", recipe.intent):
            raise ValueError("Intent does not match retained recipe")
        if spec.recipe and (spec.recipe.id, spec.recipe.version) != (recipe.id, recipe.version):
            raise ValueError("Recipe reference does not match retained recipe")
        parameters = recipe.defaults.model_dump() | spec.overrides.model_dump(exclude_none=True)
        if parameters != resolved.training.model_dump():
            raise ValueError("Resolved parameters do not match requested recipe and overrides")
        if self.model_source.kind in ("catalog", "huggingface"):
            if not re.fullmatch(r"[0-9a-f]{40}", self.model_source.revision or ""):
                raise ValueError("A full registered model commit is required")
        elif self.model_source.artifact_id is None or self.model_source.artifact_sha256 is None:
            raise ValueError("An uploaded source needs an artifact identity and hash")
        paths = [item.path for item in self.model_files]
        if len(paths) != len(set(paths)):
            raise ValueError("Duplicate model files")
        return self


class RunnerManifest(PlanModel):
    schema_name: Literal["kitchensoup.training-output/v1"] = Field(
        default="kitchensoup.training-output/v1", alias="schema"
    )
    run_id: UUID | None
    status: Literal["succeeded", "validated", "failed"]
    stage: Literal["preflight", "training", "complete"]
    exit_code: int
    soup_exit_code: int | None
    error: str | None
    soup_version: str = SOUP_VERSION
    translator_version: str = TRANSLATOR_VERSION
    image_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    input_sha256: str | None
    plan_sha256: str | None
    command: list[str]
    files: list[RunnerFile]
    adapters: list[RunnerFile]
    logs_truncated: bool


def translate(run: RunnerInput) -> str:
    """JSON is a YAML 1.2 subset; avoid a second serializer in the control plane."""
    p = run.plan.resolved.training
    config = {
        "base": "/input/model",
        "task": "sft",
        "modality": "text",
        "backend": "transformers",
        "data": {
            "train": "/output/train.jsonl",
            "format": "pre_tokenized",
            "tokenized_path": "/output/tokenized",
            "val_split": 0.0,
            "max_length": p.max_sequence_length,
            "train_on_responses_only": False,
            "train_on_prompt": False,
            "mask_history": False,
            "streaming": False,
        },
        "training": {
            "epochs": p.epochs,
            "lr": p.learning_rate,
            "batch_size": p.batch_size,
            "gradient_accumulation_steps": p.gradient_accumulation_steps,
            "lora": {
                "r": p.lora_rank,
                "alpha": p.lora_alpha,
                "dropout": p.lora_dropout,
                "target_modules": "all-linear",
            },
            "quantization": p.quantization,
            "optimizer": "adamw_torch",
            "scheduler": p.scheduler,
            "warmup_ratio": p.warmup_ratio,
            "weight_decay": p.weight_decay,
            "seed": p.seed,
            "data_seed": p.seed,
            "packing": False,
            "multipack": False,
            "auto_mixed_precision": False,
            "gradient_checkpointing": True,
            "use_flash_attn": False,
            "max_grad_norm": 1.0,
            "logging_steps": 1,
            "save_steps": 1000000,
        },
        "output": "/output/adapter",
    }
    return json.dumps(config, sort_keys=True, indent=2, allow_nan=False) + "\n"
