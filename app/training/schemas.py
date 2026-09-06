from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

Intent = Literal["conversation_imitation", "task_from_examples", "learn_from_documents", "advanced"]
RecipeID = Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9-]{0,99}$")]
Epochs = Annotated[int, Field(strict=True, ge=1, le=20)]
Rate = Annotated[float, Field(strict=True, gt=0, le=0.01, allow_inf_nan=False)]
SequenceLength = Annotated[int, Field(strict=True, ge=128, le=32768)]
BatchSize = Annotated[int, Field(strict=True, ge=1, le=64)]
Accumulation = Annotated[int, Field(strict=True, ge=1, le=256)]
Rank = Annotated[int, Field(strict=True, ge=1, le=256)]
Alpha = Annotated[int, Field(strict=True, ge=1, le=512)]
Dropout = Annotated[float, Field(strict=True, ge=0, lt=1, allow_inf_nan=False)]
Seed = Annotated[int, Field(strict=True, ge=0, le=2147483647)]
Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class PlanModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class TrainingParameters(PlanModel):
    epochs: Epochs
    learning_rate: Rate
    max_sequence_length: SequenceLength
    batch_size: BatchSize
    gradient_accumulation_steps: Accumulation
    lora_rank: Rank
    lora_alpha: Alpha
    lora_dropout: Dropout
    seed: Seed
    optimizer: Literal["adamw"] = "adamw"
    precision: Literal["bf16"] = "bf16"
    scheduler: Literal["linear"] = "linear"
    warmup_ratio: Annotated[float, Field(strict=True, ge=0, le=0)] = 0.0
    weight_decay: Annotated[float, Field(strict=True, ge=0, le=0)] = 0.0
    quantization: Literal["none"] = "none"
    lora_target: Literal["all_linear"] = "all_linear"


class TrainingOverrides(PlanModel):
    epochs: Epochs | None = None
    learning_rate: Rate | None = None
    max_sequence_length: SequenceLength | None = None
    batch_size: BatchSize | None = None
    gradient_accumulation_steps: Accumulation | None = None
    lora_rank: Rank | None = None
    lora_alpha: Alpha | None = None
    lora_dropout: Dropout | None = None
    seed: Seed | None = None


class RecipeRef(PlanModel):
    id: RecipeID
    version: Annotated[int, Field(strict=True, ge=1)]


class Recipe(PlanModel):
    schema_name: Literal["kitchensoup.recipe/v1"] = Field(alias="schema")
    id: RecipeID
    version: Annotated[int, Field(strict=True, ge=1)]
    name: str = Field(min_length=1, max_length=200)
    intent: Literal["conversation_imitation", "task_from_examples", "learn_from_documents"]
    example_kind: Literal["conversation", "document"]
    objective: Literal["final_assistant", "all_text_tokens"]
    formatting: Literal["model_chat_template", "plain_text"]
    overlength: Literal["reject"] = "reject"
    defaults: TrainingParameters

    @model_validator(mode="after")
    def consistent_mode(self) -> "Recipe":
        expected = (
            ("document", "all_text_tokens", "plain_text")
            if self.intent == "learn_from_documents"
            else ("conversation", "final_assistant", "model_chat_template")
        )
        if (self.example_kind, self.objective, self.formatting) != expected:
            raise ValueError("Recipe intent, example kind, objective and formatting disagree")
        return self


class AppSpec(PlanModel):
    schema_name: Literal["kitchensoup.appspec/v1"] = Field(
        default="kitchensoup.appspec/v1", alias="schema"
    )
    intent: Intent
    base_model_version_id: UUID
    dataset_version_id: UUID
    recipe: RecipeRef | None = None
    overrides: TrainingOverrides = Field(default_factory=TrainingOverrides)

    @model_validator(mode="after")
    def advanced_recipe(self) -> "AppSpec":
        if self.intent == "advanced" and self.recipe is None:
            raise ValueError("Advanced intent requires an explicit versioned recipe")
        return self


class PlanModelSource(PlanModel):
    source_id: UUID
    kind: Literal["catalog", "huggingface", "upload"]
    location: str
    revision: str | None
    license: str
    artifact_id: UUID | None
    artifact_sha256: Digest | None


class ResolvedPlan(PlanModel):
    schema_name: Literal["kitchensoup.resolved-plan/v1"] = Field(
        default="kitchensoup.resolved-plan/v1", alias="schema"
    )
    base_model_version_id: UUID
    base_model_name: str
    base_model_source: PlanModelSource
    dataset_version_id: UUID
    dataset_id: UUID
    dataset_name: str
    dataset_version: int
    dataset_manifest_sha256: Digest
    examples_artifact_id: UUID
    examples_sha256: Digest
    example_count: int = Field(gt=0)
    recipe: Recipe
    recipe_sha256: Digest
    training: TrainingParameters
    output: Literal["lora_adapter"] = "lora_adapter"
    warnings: list[str]


class PlanContent(PlanModel):
    appspec: AppSpec
    resolved: ResolvedPlan


class PlanPreview(PlanContent):
    sha256: Digest


class TrainingPlanView(PlanPreview):
    id: UUID
    created_at: datetime
