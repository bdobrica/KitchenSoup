"""Opt-in real vLLM FP16 serving using synthetic offline base/LoRA archives."""

import hashlib
import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4
from zipfile import ZipFile

from app.deployments.local import LocalDockerDeploymentExecutor, container_name
from app.deployments.materialize import DeploymentMaterializer
from app.deployments.schemas import ChatRequest, DeploymentSpec
from app.executors.local_docker import docker
from app.executors.materialize import SUFFIXES
from app.training.schemas import PlanModelSource
from app.transformations.local import LocalTransformExecutor, name
from app.transformations.schemas import TransformSpec

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="kitchensoup-transform-smoke-") as temp:
        root = Path(temp)
        bundle, adapter = root / "bundle", root / "adapter"
        bundle.mkdir()
        adapter.mkdir()
        fixture = root / "fixture.py"
        fixture.write_text(
            (ROOT / "scripts/soup_smoke_fixture.py")
            .read_text()
            .replace("hidden_size=32", "hidden_size=128")
            .replace("intermediate_size=64", "intermediate_size=256")
            + """
import json
# The trainer's newer Transformers serializes this synthetic generic tokenizer as
# TokenizersBackend. Use the stable native class understood by the serving image.
tokenizer_path = model / "tokenizer_config.json"
tokenizer_config = json.loads(tokenizer_path.read_text())
tokenizer_config["tokenizer_class"] = "PreTrainedTokenizerFast"
tokenizer_path.write_text(json.dumps(tokenizer_config))
from peft import get_peft_model, LoraConfig
base = Qwen2ForCausalLM.from_pretrained(model, local_files_only=True)
adapted = get_peft_model(base, LoraConfig(
    task_type="CAUSAL_LM", r=2, target_modules=["q_proj", "v_proj"]))
for module in adapted.modules():
    if hasattr(module, "lora_B") and "default" in module.lora_B:
        module.lora_B["default"].weight.data.fill_(0.05)
adapted.save_pretrained("/adapter", safe_serialization=True)
"""
        )
        args = [
            "run",
            "--rm",
            "--pull",
            "never",
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
            "kitchensoup-soup-trainer:local",
            "/fixture.py",
            "/bundle",
            "fp16",
        ]
        result = docker(args)
        if result.returncode:
            raise RuntimeError("Synthetic model generation failed: " + result.stderr[-2000:])
        base_id, adapter_id = uuid4(), uuid4()
        archives: dict[UUID, Path] = {}
        for identifier, source, prefix in (
            (base_id, bundle / "model", ""),
            (adapter_id, adapter, "adapter/"),
        ):
            archive = root / f"{identifier}.zip"
            with ZipFile(archive, "w") as target:
                for path in source.iterdir():
                    if path.suffix in SUFFIXES:
                        target.write(path, prefix + path.name)
            archives[identifier] = archive
        digests = {
            key: hashlib.sha256(path.read_bytes()).hexdigest() for key, path in archives.items()
        }

        class FixtureMaterializer:
            # Test-only stand-in for registered object storage. Production's materializer
            # still inspects and safely extracts these exact synthetic archives.
            def artifact(
                self, identifier: UUID, sha256: str, destination: Path, limit: int
            ) -> None:
                payload = archives[identifier].read_bytes()
                assert hashlib.sha256(payload).hexdigest() == sha256 and len(payload) <= limit
                destination.write_bytes(payload)

        materializer: Any = FixtureMaterializer()
        executor = LocalTransformExecutor(root / "jobs", DeploymentMaterializer(materializer))
        image = executor.image_identity()
        source = PlanModelSource(
            source_id=uuid4(),
            kind="upload",
            location="synthetic model",
            revision=None,
            license="CC0",
            artifact_id=base_id,
            artifact_sha256=digests[base_id],
        )
        model_id = uuid4()
        for operation in ("merge", "awq"):
            identifier = uuid4()
            selected = adapter_id if operation == "merge" else base_id
            spec = TransformSpec(
                operation=operation,
                precision="fp16",
                image_digest=image,
                model_version_id=model_id,
                artifact_id=selected,
                artifact_sha256=digests[selected],
                base_source=source,
                calibration=["hello goodbye " * 64] if operation == "awq" else [],
            )
            try:
                executor.start(identifier, spec)
                deadline = time.monotonic() + 600
                while time.monotonic() < deadline:
                    observed = executor.status(identifier, spec)
                    if observed == "SUCCEEDED":
                        break
                    if observed != "RUNNING":
                        logs = docker(["logs", "--tail", "100", name(identifier)])
                        raise RuntimeError(
                            "Synthetic transform failed: "
                            + logs.stdout[-8000:]
                            + logs.stderr[-8000:]
                        )
                    time.sleep(1)
                else:
                    raise RuntimeError("Synthetic transform timed out")
                result = root / (operation + ".zip")
                with result.open("w+b") as stream:
                    executor.output(identifier, spec, stream)
                with ZipFile(result) as archive:
                    config = json.loads(archive.read("config.json"))
                    assert (
                        config.get("quantization_config", {}).get("bits") == 4
                        if operation == "awq"
                        else not config.get("quantization_config")
                    )
                    if operation == "awq":
                        assert config["quantization_config"]["quant_method"] == "awq"
                        assert any(
                            b"qweight" in archive.read(n)[:65536]
                            for n in archive.namelist()
                            if n.endswith(".safetensors")
                        )
                archives[base_id] = result
                digests[base_id] = hashlib.sha256(result.read_bytes()).hexdigest()
                source = source.model_copy(update={"artifact_sha256": digests[base_id]})
                print(
                    json.dumps(
                        {
                            "image_digest": image,
                            "operation": operation,
                            "precision": "fp16",
                            "verified": True,
                            "size_bytes": result.stat().st_size,
                        }
                    ),
                    flush=True,
                )
            finally:
                executor.cleanup(identifier, spec)
            assert not executor.workspace(identifier).exists()
            assert executor.status(identifier, spec) == "LOST"
        serving = LocalDockerDeploymentExecutor(
            root / "serving", DeploymentMaterializer(materializer)
        )
        identifier = uuid4()
        deploy = DeploymentSpec(
            image_digest=serving.image_identity(),
            base_source=source,
            artifact_id=base_id,
            artifact_sha256=digests[base_id],
            adapter=False,
            representation="awq",
            precision="fp16",
            max_model_len=256,
        )
        try:
            serving.deploy(identifier, deploy)
            deadline = time.monotonic() + 600
            while time.monotonic() < deadline:
                observed = serving.status(identifier, deploy)
                if observed.state == "RUNNING":
                    break
                if observed.state in ("FAILED", "LOST"):
                    logs = docker(["logs", "--tail", "100", container_name(identifier)])
                    raise RuntimeError(
                        "Synthetic AWQ serving failed: " + logs.stdout[-8000:] + logs.stderr[-8000:]
                    )
                time.sleep(2)
            else:
                raise RuntimeError("AWQ serving timeout")
            answer = serving.chat(
                identifier,
                deploy,
                ChatRequest.model_validate(
                    {"messages": [{"role": "user", "content": "Say hello"}]}
                ),
            )
            assert isinstance(answer.content, str)
            print("AWQ vLLM inference passed", flush=True)
        finally:
            serving.stop(identifier, deploy)
        assert not serving.workspace(identifier).exists()
        print(
            "Offline FP16 merge, AWQ INT4, inference, verified output and cleanup passed",
            flush=True,
        )


if __name__ == "__main__":
    main()
