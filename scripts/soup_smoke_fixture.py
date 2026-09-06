"""Generate a tiny, random, local Qwen2 bundle inside the pinned trainer image."""

import hashlib
import sys
from pathlib import Path
from uuid import UUID

import torch
from tokenizers import Tokenizer
from tokenizers.models import WordLevel
from tokenizers.pre_tokenizers import Whitespace
from transformers import PreTrainedTokenizerFast, Qwen2Config, Qwen2ForCausalLM

from app.ingestion.versions import DatasetManifest, DatasetStatistics, VersionArtifact
from app.training.recipes import digest, resolve_recipe
from app.training.runner import file_record
from app.training.schemas import AppSpec, PlanContent, PlanModelSource, PlanPreview, ResolvedPlan
from app.training.soup import RunnerInput

root = Path(sys.argv[1])
root.mkdir(parents=True, exist_ok=True)
model = root / "model"
model.mkdir()
torch.manual_seed(42)
tokenizer = Tokenizer(
    WordLevel(
        {
            "[UNK]": 0,
            "[BOS]": 1,
            "[EOS]": 2,
            "[PAD]": 3,
            "user": 4,
            "assistant": 5,
            "Say": 6,
            "hello": 7,
            "Hello": 8,
            "goodbye": 9,
            "Goodbye": 10,
            ".": 11,
            ":": 12,
        },
        unk_token="[UNK]",
    )
)
tokenizer.pre_tokenizer = Whitespace()
fast = PreTrainedTokenizerFast(
    tokenizer_object=tokenizer,
    unk_token="[UNK]",
    bos_token="[BOS]",
    eos_token="[EOS]",
    pad_token="[PAD]",
)
fast.chat_template = (
    "{% for message in messages %}{{ message['role'] + ': ' + message['content'] + ' [EOS] ' }}"
    "{% endfor %}{% if add_generation_prompt %}{{ 'assistant: ' }}{% endif %}"
)
fast.save_pretrained(model)
config = Qwen2Config(
    vocab_size=13,
    hidden_size=32,
    intermediate_size=64,
    num_hidden_layers=1,
    num_attention_heads=2,
    num_key_value_heads=2,
    max_position_embeddings=256,
    bos_token_id=1,
    eos_token_id=2,
    pad_token_id=3,
)
Qwen2ForCausalLM(config).save_pretrained(model, safe_serialization=True)
examples = Path("/fixture.jsonl").read_bytes()
(root / "examples.jsonl").write_bytes(examples)
identifier = UUID("11111111-1111-4111-8111-111111111111")
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
    license="CC0-1.0",
    sources=[],
    conversations=[],
    documents=[],
    examples=example_ref,
    statistics=DatasetStatistics(example_count=2, size_bytes=len(examples)),
    warnings=[],
)
manifest_bytes = manifest.model_dump_json(by_alias=True).encode()
(root / "manifest.json").write_bytes(manifest_bytes)
spec = AppSpec.model_validate(
    {
        "intent": "conversation_imitation",
        "base_model_version_id": identifier,
        "dataset_version_id": identifier,
        "overrides": {
            "epochs": 1,
            "max_sequence_length": 128,
            "gradient_accumulation_steps": 1,
            "lora_rank": 2,
        },
    }
)
recipe, parameters = resolve_recipe(spec)
files = [file_record(model, path) for path in sorted(model.iterdir())]
source = PlanModelSource(
    source_id=identifier,
    kind="upload",
    location=f"artifact:{identifier}",
    revision=None,
    license="CC0-1.0",
    artifact_id=identifier,
    artifact_sha256=hashlib.sha256(b"synthetic materializer fixture").hexdigest(),
)
resolved = ResolvedPlan(
    base_model_version_id=identifier,
    base_model_name="Random tiny smoke model",
    base_model_source=source,
    dataset_version_id=identifier,
    dataset_id=identifier,
    dataset_name="Synthetic smoke examples",
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
    model_files=files,
)
(root / "run.json").write_text(run.model_dump_json(by_alias=True))
print("Created synthetic local smoke bundle; no model downloaded")
