"""Opt-in real vLLM FP16 serving using synthetic offline base/LoRA archives."""

import argparse
import hashlib
import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4
from zipfile import ZipFile

from app.deployments.kubernetes import KubernetesDeploymentExecutor
from app.deployments.local import LocalDockerDeploymentExecutor, container_name
from app.deployments.materialize import DeploymentMaterializer
from app.deployments.schemas import ChatRequest, DeploymentSpec
from app.executors.local_docker import docker
from app.executors.materialize import SUFFIXES
from app.targets.kubernetes import KubernetesTransport
from app.targets.kubernetes_schemas import KubernetesConfig
from app.training.schemas import PlanModelSource

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kubernetes-namespace")
    parser.add_argument("--image", help="Pinned Kubernetes serving repository@sha256:digest")
    parser.add_argument("--kubeconfig-ref")
    parser.add_argument("--secret-directory", type=Path)
    options = parser.parse_args()
    if options.kubernetes_namespace and not all(
        (options.image, options.kubeconfig_ref, options.secret_directory)
    ):
        parser.error("Kubernetes requires --image, --kubeconfig-ref and --secret-directory")
    with tempfile.TemporaryDirectory(prefix="kitchensoup-vllm-smoke-") as temp:
        root = Path(temp)
        bundle, adapter = root / "bundle", root / "adapter"
        bundle.mkdir()
        adapter.mkdir()
        fixture = root / "fixture.py"
        fixture.write_text(
            (ROOT / "scripts/soup_smoke_fixture.py")
            .read_text()
            .replace("hidden_size=32", "hidden_size=128")
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
        executor = LocalDockerDeploymentExecutor(
            root / "jobs", DeploymentMaterializer(materializer)
        )
        if options.kubernetes_namespace:
            transport = KubernetesTransport(
                KubernetesConfig(
                    namespace=options.kubernetes_namespace,
                    training_image=options.image,
                    deployment_image=options.image,
                ),
                options.kubeconfig_ref,
                options.secret_directory,
                set(),
            )
            executor = KubernetesDeploymentExecutor(
                root / "jobs", DeploymentMaterializer(materializer), transport
            )
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
        for tuned in (False, True):
            identifier = uuid4()
            selected = adapter_id if tuned else base_id
            spec = DeploymentSpec(
                image_digest=image,
                base_source=source,
                artifact_id=selected,
                artifact_sha256=digests[selected],
                adapter=tuned,
                precision="fp16",
                max_model_len=256,
            )
            try:
                executor.deploy(identifier, spec)
                if isinstance(executor, KubernetesDeploymentExecutor):
                    executor = KubernetesDeploymentExecutor(
                        root / "jobs", DeploymentMaterializer(materializer), executor.transport
                    )
                deadline = time.monotonic() + 600
                while time.monotonic() < deadline:
                    observed = executor.status(identifier, spec)
                    if observed.state == "RUNNING":
                        break
                    if observed.state in ("FAILED", "LOST"):
                        if isinstance(executor, KubernetesDeploymentExecutor):
                            raise RuntimeError(
                                "Synthetic Kubernetes serving failed; inspect fixture Pod logs"
                            )
                        # Logs contain only synthetic fixture data, never user content.
                        logs = docker(["logs", "--tail", "120", container_name(identifier)])
                        raise RuntimeError(
                            "Synthetic vLLM startup failed: "
                            + logs.stdout[-8000:]
                            + logs.stderr[-8000:]
                        )
                    time.sleep(2)
                else:
                    raise RuntimeError("Synthetic vLLM startup timed out")
                answer = executor.chat(
                    identifier,
                    spec,
                    ChatRequest.model_validate(
                        {"messages": [{"role": "user", "content": "Say hello"}]}
                    ),
                )
                assert isinstance(answer.content, str)
                print(
                    json.dumps(
                        {
                            "image_digest": image,
                            "precision": "fp16",
                            "adapter": tuned,
                            "inference": True,
                        }
                    ),
                    flush=True,
                )
            finally:
                executor.stop(identifier, spec)
            assert executor.status(identifier, spec).state == "LOST"
            assert not executor.workspace(identifier).exists()
        print("FP16 base/LoRA serving, private-network proxy and cleanup passed", flush=True)


if __name__ == "__main__":
    main()
