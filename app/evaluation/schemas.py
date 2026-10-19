from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.training.soup import RunnerFile


class EvaluationModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


PromptText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000)]
Preference = Literal["base_better", "fine_tuned_better", "same"]


class SuiteCreate(EvaluationModel):
    name: str = Field(min_length=1, max_length=200)
    prompts: list[PromptText] = Field(min_length=3, max_length=10)


class PromptView(EvaluationModel):
    id: UUID
    position: int
    prompt: str


class SuiteView(EvaluationModel):
    id: UUID
    name: str
    prompts: list[PromptView]


class PreferenceUpdate(EvaluationModel):
    preference: Preference


class TaskCreate(EvaluationModel):
    training_run_id: UUID


class Comparison(EvaluationModel):
    prompt_id: UUID
    prompt: str
    base_response: str | None
    fine_tuned_response: str | None
    preference: Preference | None


class EvaluationView(EvaluationModel):
    id: UUID
    training_run_id: UUID
    status: Literal["QUEUED", "RUNNING", "SUCCEEDED", "FAILED"]
    report_artifact_id: UUID | None
    comparisons: list[Comparison]
    summary: dict[str, int]


class InferenceInput(EvaluationModel):
    schema_name: Literal["kitchensoup.inference-input/v1"] = Field(
        default="kitchensoup.inference-input/v1", alias="schema"
    )
    run_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    prompt: PromptText
    adapter_files: list[RunnerFile] = Field(default_factory=list, max_length=4096)
    max_new_tokens: Literal[128] = 128


class InferenceOutput(EvaluationModel):
    schema_name: Literal["kitchensoup.inference-output/v1"] = Field(
        default="kitchensoup.inference-output/v1", alias="schema"
    )
    input_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    response: str = Field(max_length=65536)


class GenerationSettings(EvaluationModel):
    max_new_tokens: Literal[128] = 128
    do_sample: Literal[False] = False
    num_beams: Literal[1] = 1
    precision: Literal["bf16", "fp16"]


class EvaluationReport(EvaluationModel):
    schema_name: Literal["kitchensoup.evaluation-report/v1"] = Field(
        default="kitchensoup.evaluation-report/v1", alias="schema"
    )
    training_run_id: UUID
    evaluation_suite_id: UUID
    base_model_version_id: UUID
    fine_tuned_model_version_id: UUID
    plan_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    image_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    generation: GenerationSettings
    comparisons: list[Comparison] = Field(min_length=3, max_length=10)
    summary: dict[str, int]
