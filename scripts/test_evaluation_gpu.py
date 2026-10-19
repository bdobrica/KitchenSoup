"""Opt-in real offline FP16/BF16 paired generation on a tiny synthetic model."""

import argparse
import json
import os
import shutil
import tempfile
from pathlib import Path
from uuid import UUID, uuid4
from zipfile import ZipFile

from app.evaluation.local import LocalInferenceRunner
from app.executors.local_docker import LocalDockerTrainingExecutor, docker
from app.training.schemas import PlanPreview
from app.training.soup import RunnerInput

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--precision", choices=("fp16", "bf16"), default="fp16")
args = parser.parse_args()

with tempfile.TemporaryDirectory(prefix="kitchensoup-evaluation-smoke-") as temp:
    root = Path(temp)
    bundle, adapter = root / "bundle", root / "adapter"
    bundle.mkdir()
    adapter.mkdir()
    fixture = root / "fixture.py"
    fixture.write_text(
        (ROOT / "scripts/soup_smoke_fixture.py")
        .read_text()
        .replace('"max_sequence_length": 128', '"max_sequence_length": 512')
        + """
from peft import get_peft_model, LoraConfig
base = Qwen2ForCausalLM.from_pretrained(model, local_files_only=True)
config = LoraConfig(task_type="CAUSAL_LM", r=2, target_modules=["q_proj", "v_proj"])
adapted = get_peft_model(base, config)
adapted.save_pretrained("/adapter", safe_serialization=True)
"""
    )
    image = docker(["image", "inspect", "kitchensoup-soup-trainer:local", "--format", "{{.Id}}"])
    if image.returncode:
        raise RuntimeError("Build the trainer image first")
    identity = image.stdout.strip()
    command = [
        "run",
        "--rm",
        "--network",
        "none",
        "--read-only",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--memory",
        "4g",
        "--pids-limit",
        "256",
        "--tmpfs",
        "/tmp:size=512m",
        "--user",
        f"{os.getuid()}:{os.getgid()}",
        "--env",
        "HOME=/tmp",
        "--env",
        "OMP_NUM_THREADS=2",
        "--env",
        "HF_HOME=/tmp/hf",
        "--mount",
        f"type=bind,src={bundle},dst=/bundle",
        "--mount",
        f"type=bind,src={adapter},dst=/adapter",
        "--mount",
        f"type=bind,src={fixture},dst=/fixture.py,readonly",
        "--mount",
        f"type=bind,src={ROOT / 'tests/fixtures/training/conversations.jsonl'},"
        "dst=/fixture.jsonl,readonly",
        "--entrypoint",
        "python",
        identity,
        "/fixture.py",
        "/bundle",
        args.precision,
    ]
    result = docker(command)
    if result.returncode:
        raise RuntimeError("Synthetic fixture creation failed: " + result.stderr[-2000:])
    archive = root / "adapter.zip"
    with ZipFile(archive, "w") as target:
        for path in adapter.iterdir():
            if path.suffix in {".json", ".safetensors"}:
                target.write(path, "adapter/" + path.name)
    run = RunnerInput.model_validate_json((bundle / "run.json").read_bytes())

    # Test-only materializer injects synthetic local bytes. Production uses verified storage.
    class FixtureArtifacts:
        def get(self, identifier: UUID) -> object:
            from types import SimpleNamespace

            return SimpleNamespace(sha256="a" * 64)

    class Materializer:
        artifacts = FixtureArtifacts()

        def materialize(self, run_id: UUID, plan: PlanPreview, destination: Path) -> None:
            shutil.copytree(bundle, destination, dirs_exist_ok=True)

        def artifact(self, identifier: UUID, sha256: str, destination: Path, limit: int) -> None:
            shutil.copyfile(archive, destination)

    materializer = Materializer()
    executor = LocalDockerTrainingExecutor(root / "jobs", materializer, memory_gib=4)
    runner = LocalInferenceRunner(executor, materializer)
    with runner.prepare(uuid4(), run.run_id, run.plan, identity, uuid4()) as generate:
        base = generate("Say hello", False)
        fine = generate("Say hello", True)
    assert isinstance(base, str) and isinstance(fine, str)
    print(
        json.dumps(
            {
                "precision": args.precision,
                "base_generated": True,
                "adapter_generated": True,
                "image_digest": identity,
                "synthetic_untrained_adapter": True,
            }
        )
    )
