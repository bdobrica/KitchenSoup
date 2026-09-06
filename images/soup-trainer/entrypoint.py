"""Image-only Transformers/Arrow preparation. Soup is invoked exclusively as a CLI."""

import argparse
import importlib.metadata
import json
from pathlib import Path

from app.ingestion.versions import TrainingExample
from app.training.runner import execute
from app.training.soup import SOUP_VERSION, RunnerError, RunnerInput
from app.training.tokenization import tokenize_example


class LocalTokenizer:
    def __init__(self, tokenizer):
        self.tokenizer = tokenizer

    def chat(self, messages, generation_prompt):
        return self.tokenizer.apply_chat_template(
            messages,
            tokenize=True,
            add_generation_prompt=generation_prompt,
            return_dict=False,
        )

    def text(self, content):
        return self.tokenizer.encode(content, add_special_tokens=True, truncation=False)


def prepare(root: Path, output: Path, run: RunnerInput, validate_only: bool) -> None:
    import torch
    from datasets import Dataset
    from transformers import AutoConfig, AutoTokenizer

    if importlib.metadata.version("soup-cli") != SOUP_VERSION:
        raise RunnerError("Installed Soup version does not match the translator")
    if not validate_only:
        if not torch.cuda.is_available() or not torch.cuda.is_bf16_supported(
            including_emulation=False
        ):
            raise RunnerError("The reviewed BF16 recipe requires a native BF16 CUDA device")
    config = AutoConfig.from_pretrained(
        root / "model", local_files_only=True, trust_remote_code=False
    )
    if run.plan.resolved.training.max_sequence_length > config.max_position_embeddings:
        raise RunnerError("Sequence limit exceeds model context capacity")
    tokenizer = AutoTokenizer.from_pretrained(
        root / "model", local_files_only=True, trust_remote_code=False
    )
    adapter = LocalTokenizer(tokenizer)
    maximum = run.plan.resolved.training.max_sequence_length
    count = 0
    # JSONL spool bounds resident memory; Arrow builds from the validated spool.
    with (root / "examples.jsonl").open() as source, (output / "train.jsonl").open("w") as target:
        for line in source:
            if not line.strip():
                raise RunnerError("Empty example row")
            example = TrainingExample.model_validate_json(line)
            row = tokenize_example(example, run.plan.resolved.recipe.example_kind, adapter, maximum)
            target.write(json.dumps(row, separators=(",", ":")) + "\n")
            count += 1
            if count > 100000 or target.tell() > 512 * 1024**2:
                raise RunnerError("Tokenized dataset exceeds runner limits")
    if count != run.plan.resolved.example_count:
        raise RunnerError("Example count does not match the reviewed dataset")
    Dataset.from_json(str(output / "train.jsonl")).save_to_disk(str(output / "tokenized"))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--image-digest", required=True)
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument("--timeout", type=int, default=86400)
    args = parser.parse_args()
    if not 1 <= args.timeout <= 604800:
        parser.error("timeout must be between one second and seven days")
    raise SystemExit(
        execute(
            Path("/input"),
            Path("/output"),
            args.image_digest,
            prepare,
            validate_only=args.validate_only,
            timeout=args.timeout,
        )
    )
