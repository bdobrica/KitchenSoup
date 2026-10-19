import hashlib
import json
from pathlib import Path
from unittest.mock import Mock
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.evaluation.local import LocalInferenceRunner
from app.evaluation.runner import verify_input
from app.evaluation.schemas import EvaluationReport, InferenceInput, InferenceOutput, SuiteCreate
from app.executors.base import ExecutorError
from app.executors.local_docker import LocalDockerTrainingExecutor
from app.executors.materialize import RegisteredInputMaterializer
from app.training.runner import file_record
from app.training.soup import RunnerError, RunnerInput
from tests.unit.test_soup_runner import bundle

__all__ = ["bundle"]


@pytest.mark.parametrize("count", [0, 2, 11])
def test_suite_bounds(count: int) -> None:
    with pytest.raises(ValidationError):
        SuiteCreate(name="Bounds", prompts=["Prompt"] * count)


def test_no_blank_or_excessive_prompt() -> None:
    for bad in ["   ", "x" * 4001]:
        with pytest.raises(ValidationError):
            SuiteCreate(name="Bounds", prompts=["One", "Two", bad])


def test_offline_input_binds_exact_base_and_adapter_bytes(bundle: tuple[Path, RunnerInput]) -> None:
    root, _ = bundle
    request = InferenceInput(
        run_sha256=hashlib.sha256((root / "run.json").read_bytes()).hexdigest(), prompt="Question"
    )
    (root / "inference.json").write_text(request.model_dump_json(by_alias=True))
    assert verify_input(root)[0] == request
    adapter = root / "adapter"
    adapter.mkdir()
    (adapter / "adapter_config.json").write_text('{"peft_type":"LORA","task_type":"CAUSAL_LM"}')
    (adapter / "adapter_model.safetensors").write_bytes(b"synthetic weights")
    with pytest.raises(RunnerError, match="Uninventoried"):
        verify_input(root)
    request.adapter_files = [file_record(adapter, p) for p in sorted(adapter.iterdir())]
    (root / "inference.json").write_text(request.model_dump_json(by_alias=True))
    verify_input(root)
    (adapter / "adapter_model.safetensors").write_bytes(b"changed")
    with pytest.raises(RunnerError, match="inventory"):
        verify_input(root)
    (adapter / "adapter_model.safetensors").unlink()
    (adapter / "adapter_model.safetensors").symlink_to(root / "examples.jsonl")
    with pytest.raises(RunnerError, match="links"):
        verify_input(root)


@pytest.mark.parametrize(
    "name,model",
    [
        ("inference-input", InferenceInput),
        ("inference-output", InferenceOutput),
        ("evaluation-report", EvaluationReport),
    ],
)
def test_generated_evaluation_contracts(
    name: str, model: type[InferenceInput | InferenceOutput | EvaluationReport]
) -> None:
    assert (
        json.loads(Path(f"docs/contracts/{name}-v1.schema.json").read_text())
        == model.model_json_schema()
    )


def test_materialization_failure_is_normalized_without_private_details(
    bundle: tuple[Path, RunnerInput],
    tmp_path: Path,
) -> None:
    _, run = bundle
    materializer = Mock(spec=RegisteredInputMaterializer)
    materializer.materialize.side_effect = RuntimeError("private network payload")
    executor = LocalDockerTrainingExecutor(tmp_path / "jobs", materializer)
    runner = LocalInferenceRunner(executor, materializer)
    with pytest.raises(ExecutorError, match="input preparation failed") as error:
        with runner.prepare(uuid4(), run.run_id, run.plan, "sha256:" + "a" * 64, None):
            pytest.fail("Unverified input escaped")
    assert "private" not in str(error.value)
