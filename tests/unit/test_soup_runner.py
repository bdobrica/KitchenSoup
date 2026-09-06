import hashlib
import json
import sys
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.ingestion.versions import (
    DatasetManifest,
    DatasetStatistics,
    TrainingExample,
    VersionArtifact,
)
from app.training.recipes import digest, resolve_recipe
from app.training.runner import execute, file_record, invoke_cli, verify_bundle
from app.training.schemas import AppSpec, PlanContent, PlanModelSource, PlanPreview, ResolvedPlan
from app.training.soup import RunnerError, RunnerFile, RunnerInput, RunnerManifest, translate
from app.training.tokenization import tokenize_example


@pytest.fixture
def bundle(tmp_path: Path) -> tuple[Path, RunnerInput]:
    root = tmp_path / "input"
    root.mkdir()
    model = root / "model"
    model.mkdir()
    (model / "config.json").write_text('{"model_type":"qwen2"}')
    (model / "tokenizer_config.json").write_text("{}")
    (model / "model.safetensors").write_bytes(b"synthetic weights; not numerically valid")
    examples = Path("tests/fixtures/training/conversations.jsonl").read_bytes()
    (root / "examples.jsonl").write_bytes(examples)
    identifier = uuid4()
    example_ref = VersionArtifact(
        artifact_id=identifier,
        sha256=hashlib.sha256(examples).hexdigest(),
        size_bytes=len(examples),
        format="kitchensoup.training-example/v1",
    )
    manifest = DatasetManifest(
        schema_name="kitchensoup.dataset-manifest/v1",
        dataset_id=identifier,
        dataset_version_id=identifier,
        version=1,
        license="CC0",
        sources=[],
        conversations=[],
        documents=[],
        examples=example_ref,
        statistics=DatasetStatistics(example_count=2),
        warnings=[],
    )
    manifest_bytes = manifest.model_dump_json(by_alias=True).encode()
    (root / "manifest.json").write_bytes(manifest_bytes)
    spec = AppSpec(
        intent="conversation_imitation",
        base_model_version_id=identifier,
        dataset_version_id=identifier,
    )
    recipe, parameters = resolve_recipe(spec)
    source = PlanModelSource(
        source_id=identifier,
        kind="huggingface",
        location="fixture/model",
        revision="a" * 40,
        license="CC0",
        artifact_id=None,
        artifact_sha256=None,
    )
    resolved = ResolvedPlan(
        base_model_version_id=identifier,
        base_model_name="Fixture",
        base_model_source=source,
        dataset_version_id=identifier,
        dataset_id=identifier,
        dataset_name="Fixture",
        dataset_version=1,
        dataset_manifest_sha256=hashlib.sha256(manifest_bytes).hexdigest(),
        examples_artifact_id=identifier,
        examples_sha256=example_ref.sha256,
        example_count=2,
        recipe=recipe,
        recipe_sha256=digest(recipe),
        training=parameters,
        warnings=[],
    )
    content = PlanContent(appspec=spec, resolved=resolved)
    run = RunnerInput(
        schema_name="kitchensoup.training-input/v1",
        run_id=identifier,
        plan=PlanPreview(**content.model_dump(), sha256=digest(content)),
        model_source=source,
        model_files=[file_record(model, path) for path in sorted(model.iterdir())],
    )
    (root / "run.json").write_text(run.model_dump_json(by_alias=True))
    return root, run


def test_translation_maps_reviewed_parameters_without_engine_imports(
    bundle: tuple[Path, RunnerInput],
) -> None:
    _, run = bundle
    config = json.loads(translate(run))
    assert config["base"] == "/input/model"
    assert config["training"]["lr"] == run.plan.resolved.training.learning_rate
    assert config["training"]["lora"]["target_modules"] == "all-linear"
    assert config["training"]["scheduler"] == "linear"
    assert config["training"]["warmup_ratio"] == config["training"]["weight_decay"] == 0
    assert config["data"]["format"] == "pre_tokenized"
    assert config["data"]["val_split"] == 0 and config["training"]["packing"] is False
    assert translate(run) == translate(
        RunnerInput.model_validate_json(run.model_dump_json(by_alias=True))
    )
    assert not any(name.startswith("soup_cli") for name in sys.modules)


def test_input_hash_and_semantic_tampering_fail(bundle: tuple[Path, RunnerInput]) -> None:
    _, run = bundle
    data = run.model_dump(mode="json", by_alias=True)
    data["plan"]["resolved"]["training"]["epochs"] = 7
    with pytest.raises(ValidationError, match="hash mismatch"):
        RunnerInput.model_validate(data)
    changed = run.plan.resolved.model_copy(update={"base_model_version_id": uuid4()})
    content = PlanContent(appspec=run.plan.appspec, resolved=changed)
    data["plan"] = {**content.model_dump(mode="json", by_alias=True), "sha256": digest(content)}
    with pytest.raises(ValidationError, match="references disagree"):
        RunnerInput.model_validate(data)
    data = run.model_dump(mode="json", by_alias=True) | {"schema": "kitchensoup.training-input/v2"}
    with pytest.raises(ValidationError):
        RunnerInput.model_validate(data)


@pytest.mark.parametrize("path", ["../escape", "/absolute", "a/../b", "a//b", "a\\b", "."])
def test_runner_paths_are_confined(path: str) -> None:
    with pytest.raises(ValidationError):
        RunnerFile(path=path, sha256="a" * 64, size_bytes=1)


@pytest.mark.parametrize("filename", ["model/model.safetensors", "manifest.json", "examples.jsonl"])
def test_actual_bytes_are_verified(bundle: tuple[Path, RunnerInput], filename: str) -> None:
    root, run = bundle
    verify_bundle(root, run)
    with (root / filename).open("ab") as body:
        body.write(b"changed")
    with pytest.raises(RunnerError):
        verify_bundle(root, run)


def test_symlink_and_custom_code_rejected(bundle: tuple[Path, RunnerInput]) -> None:
    root, run = bundle
    (root / "model" / "external.json").symlink_to(root / "manifest.json")
    with pytest.raises(RunnerError, match="symlinks"):
        verify_bundle(root, run)


class FixtureTokenizer:
    def chat(self, messages: list[dict[str, str]], generation_prompt: bool) -> list[int]:
        result = []
        for index, message in enumerate(messages):
            result.extend([1 if message["role"] == "user" else 2, 10 + index, 3])
        if generation_prompt:
            result.append(2)
        return result

    def text(self, content: str) -> list[int]:
        return [10, 11, 12]


def test_final_assistant_mask_preserves_context_but_excludes_earlier_targets() -> None:
    example = TrainingExample.model_validate_json(
        Path("tests/fixtures/training/conversations.jsonl").read_text().splitlines()[1]
    )
    row = tokenize_example(example, "conversation", FixtureTokenizer(), 128)
    assert row["input_ids"] == [1, 10, 3, 2, 11, 3, 1, 12, 3, 2, 13, 3]
    assert row["labels"] == [-100] * 10 + [13, 3]
    with pytest.raises(RunnerError, match="no truncation"):
        tokenize_example(example, "conversation", FixtureTokenizer(), 11)
    doc = TrainingExample(kind="document", source_id=uuid4(), text="Synthetic document")
    assert tokenize_example(doc, "document", FixtureTokenizer(), 128)["labels"] == [10, 11, 12]
    with pytest.raises(RunnerError, match="kind"):
        tokenize_example(doc, "conversation", FixtureTokenizer(), 128)


def test_cli_logs_exit_timeout_and_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SYNTHETIC_SECRET", "private-fixture")
    monkeypatch.setattr("app.training.runner.LOG_LIMIT", 32)
    command = [
        sys.executable,
        "-c",
        'import os,sys; assert "SYNTHETIC_SECRET" not in os.environ; print("x"*80); '
        'print("err",file=sys.stderr); sys.exit(7)',
    ]
    code, truncated = invoke_cli(tmp_path, command, 10)
    assert (code, truncated) == (7, True)
    assert len((tmp_path / "stdout.log").read_bytes()) == 32
    assert (tmp_path / "stderr.log").read_text() == "err\n"
    other = tmp_path / "timeout"
    other.mkdir()
    assert invoke_cli(other, [sys.executable, "-c", "import time; time.sleep(30)"], 1)[0] == 124


def test_failure_manifest_and_attempt_immutability(
    bundle: tuple[Path, RunnerInput], tmp_path: Path
) -> None:
    root, run = bundle
    output = tmp_path / "output"

    def prepare(root: Path, output: Path, run: RunnerInput, validate_only: bool) -> None:
        raise RunnerError("BF16 hardware unavailable")

    assert execute(root, output, "sha256:" + "a" * 64, prepare) == 1
    body = (output / "output-manifest.json").read_bytes()
    manifest = RunnerManifest.model_validate_json(body)
    assert manifest.stage == "preflight" and manifest.soup_exit_code is None
    assert manifest.error == "BF16 hardware unavailable" and manifest.adapters == []
    assert manifest.plan_sha256 == run.plan.sha256
    with pytest.raises(RunnerError, match="empty"):
        execute(root, output, "sha256:" + "a" * 64, prepare)
    assert (output / "output-manifest.json").read_bytes() == body


@pytest.mark.parametrize(
    "name,model", [("training-input", RunnerInput), ("training-output", RunnerManifest)]
)
def test_generated_runner_schemas(name: str, model: type[RunnerInput | RunnerManifest]) -> None:
    assert (
        json.loads(Path(f"docs/contracts/{name}-v1.schema.json").read_text())
        == model.model_json_schema()
    )


@pytest.mark.parametrize("cli_code", [0, 7])
def test_only_successful_cli_outputs_are_advertised(
    bundle: tuple[Path, RunnerInput],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    cli_code: int,
) -> None:
    root, _ = bundle
    output = tmp_path / "attempt"

    def prepare(root: Path, output: Path, run: RunnerInput, validate_only: bool) -> None:
        (output / "train.jsonl").write_text("{}\n")

    def invoke(output: Path, command: list[str], timeout: int) -> tuple[int, bool]:
        assert command == ["soup", "train", "--config", str(output / "soup.yaml"), "--yes"]
        (output / "stdout.log").write_text("synthetic CLI output")
        (output / "stderr.log").write_text("")
        (output / "adapter").mkdir()
        (output / "adapter/adapter_config.json").write_text("{}")
        (output / "adapter/adapter_model.safetensors").write_bytes(b"synthetic adapter")
        return cli_code, False

    monkeypatch.setattr("app.training.runner.invoke_cli", invoke)
    assert execute(root, output, "sha256:" + "a" * 64, prepare) == cli_code
    manifest = RunnerManifest.model_validate_json((output / "output-manifest.json").read_bytes())
    assert manifest.soup_exit_code == cli_code
    assert manifest.status == ("succeeded" if cli_code == 0 else "failed")
    assert len(manifest.adapters) == (2 if cli_code == 0 else 0)
    for item in manifest.files + manifest.adapters:
        assert file_record(output, output / item.path) == item


def test_tokenizer_template_is_retained_but_python_and_remote_code_fail(
    bundle: tuple[Path, RunnerInput],
) -> None:
    root, run = bundle
    model = root / "model"
    template = model / "chat_template.jinja"
    template.write_text("{{ messages }}")
    run.model_files.append(file_record(model, template))
    verify_bundle(root, run)
    config = model / "config.json"
    config.write_text('{"model_type":"qwen2","auto_map":{"AutoModel":"payload.Model"}}')
    run.model_files = [file_record(model, model / item.path) for item in run.model_files]
    with pytest.raises(RunnerError, match="custom code"):
        verify_bundle(root, run)
    code = model / "payload.py"
    code.write_text("# synthetic unsupported code")
    run.model_files.append(file_record(model, code))
    with pytest.raises(RunnerError, match="file type"):
        verify_bundle(root, run)
