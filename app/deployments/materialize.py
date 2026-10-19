"""Verified model/adapter bytes only; no training data enters the serving runtime."""

import json
import shutil
from pathlib import Path

from app.deployments.schemas import DeploymentSpec
from app.executors.base import ExecutorError
from app.executors.materialize import (
    MAX_MODEL,
    SUFFIXES,
    RegisteredInputMaterializer,
    download_model,
)
from app.registry.inspection import inspect_archive, validate_structure
from app.storage.archives import ArchiveLimits, extract_zip
from app.training.runner import read_bounded


class DeploymentMaterializer:
    def __init__(self, materializer: RegisteredInputMaterializer) -> None:
        self.materializer = materializer

    def materialize(self, spec: DeploymentSpec, destination: Path) -> None:
        source = spec.base_source
        model = destination / "model"
        model.mkdir(mode=0o700)
        if source.kind == "upload":
            if source.artifact_id is None or source.artifact_sha256 is None:
                raise ExecutorError("Missing registered base artifact")
            archive = destination / "base.zip"
            self.materializer.artifact(
                source.artifact_id, source.artifact_sha256, archive, MAX_MODEL
            )
            with archive.open("rb") as stream:
                inspect_archive(stream, allow_awq=spec.representation == "awq")
                with extract_zip(stream, ArchiveLimits(max_expanded_bytes=MAX_MODEL)) as root:
                    config = next(root.rglob("config.json"))
                    for path in config.parent.rglob("*"):
                        if path.is_file() and path.suffix in SUFFIXES:
                            target = model / path.relative_to(config.parent)
                            target.parent.mkdir(parents=True, exist_ok=True)
                            shutil.copyfile(path, target)
            archive.unlink()
        else:
            download_model(source.location, source.revision or "", model)
        config = json.loads(read_bounded(model / "config.json", 1024**2))
        validate_structure(
            {p.name for p in model.iterdir()}, config, allow_awq=spec.representation == "awq"
        )
        for name in ("config.json", "tokenizer_config.json"):
            if json.loads(read_bounded(model / name, 1024**2)).get("auto_map"):
                raise ExecutorError("Custom model/tokenizer code is unsupported")
        if spec.adapter:
            archive = destination / "adapter.zip"
            self.materializer.artifact(spec.artifact_id, spec.artifact_sha256, archive, 9 * 1024**3)
            with (
                archive.open("rb") as stream,
                extract_zip(stream, ArchiveLimits(max_expanded_bytes=9 * 1024**3)) as root,
            ):
                adapter = root / "adapter"
                config = json.loads(read_bounded(adapter / "adapter_config.json", 1024**2))
                if (
                    config.get("peft_type") != "LORA"
                    or config.get("task_type") != "CAUSAL_LM"
                    or config.get("auto_mapping")
                    or config.get("loftq_config")
                ):
                    raise ExecutorError("Only native causal LoRA adapters are supported")
                files = list(adapter.rglob("*"))
                if not any(p.name.endswith(".safetensors") for p in files):
                    raise ExecutorError("Adapter safetensors weights are missing")
                for path in files:
                    if path.is_file() and path.suffix not in SUFFIXES:
                        raise ExecutorError("Unsupported adapter file")
                shutil.copytree(adapter, destination / "adapter")
            archive.unlink()
