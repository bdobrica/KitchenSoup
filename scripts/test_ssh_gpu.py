"""Opt-in SSH/SFTP FP16 Soup training followed by private vLLM inference.

Uses synthetic data only. The remote machine needs Docker images, not a KitchenSoup agent.
"""

import argparse
import hashlib
import json
import shutil
import stat
import tempfile
import time
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4
from zipfile import ZipFile

from app.deployments.local import container_name as serving_name
from app.deployments.materialize import DeploymentMaterializer
from app.deployments.schemas import ChatRequest, DeploymentSpec
from app.deployments.ssh import SshDockerDeploymentExecutor
from app.executors.local_docker import container_name
from app.executors.materialize import SUFFIXES
from app.executors.ssh_docker import SshDockerTrainingExecutor
from app.targets.schemas import SshConfig
from app.targets.ssh import SshTransport
from app.training.schemas import PlanModelSource, PlanPreview
from app.training.soup import RunnerInput

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=2222)
    parser.add_argument("--username", required=True)
    parser.add_argument("--key-ref", required=True)
    parser.add_argument("--secret-directory", type=Path, required=True)
    parser.add_argument("--known-hosts", type=Path, required=True)
    parser.add_argument("--remote-workspace", required=True)
    args = parser.parse_args()
    config = SshConfig(
        host=args.host, port=args.port, username=args.username, workspace=args.remote_workspace
    )
    transport = SshTransport(
        config,
        args.key_ref,
        known_hosts=args.known_hosts,
        secret_directory=args.secret_directory,
        env_names=set(),
    )
    uid, gid = transport.identity()
    fixture_id = uuid4()
    remote_fixture = config.workspace + "/fixtures/" + str(fixture_id)
    run_id, cancel_id, deployment_id = uuid4(), uuid4(), uuid4()
    with tempfile.TemporaryDirectory(prefix="kitchensoup-ssh-smoke-") as temp:
        root = Path(temp)
        fixture = root / "fixture"
        fixture.mkdir()
        (fixture / "bundle").mkdir()
        # Stable native tokenizer metadata is written before the input inventory is hashed.
        script = (
            (ROOT / "scripts/soup_smoke_fixture.py")
            .read_text()
            .replace("hidden_size=32", "hidden_size=128")
        )
        script = script.replace(
            "fast.save_pretrained(model)",
            """fast.save_pretrained(model)
import json
config_path = model / "tokenizer_config.json"
config_data = json.loads(config_path.read_text())
config_data["tokenizer_class"] = "PreTrainedTokenizerFast"
config_path.write_text(json.dumps(config_data))""",
        )
        (fixture / "fixture.py").write_text(script)
        shutil.copy(ROOT / "tests/fixtures/training/conversations.jsonl", fixture / "fixture.jsonl")
        training = None
        serving = None
        spec = None
        try:
            transport.upload(fixture, remote_fixture)
            result = transport.docker(
                [
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
                    f"{uid}:{gid}",
                    "--env",
                    "HOME=/tmp",
                    "--env",
                    "OMP_NUM_THREADS=2",
                    "--mount",
                    f"type=bind,src={remote_fixture}/bundle,dst=/bundle",
                    "--mount",
                    f"type=bind,src={remote_fixture}/fixture.py,dst=/fixture.py,readonly",
                    "--mount",
                    f"type=bind,src={remote_fixture}/fixture.jsonl,dst=/fixture.jsonl,readonly",
                    "--entrypoint",
                    "python",
                    config.training_image,
                    "/fixture.py",
                    "/bundle",
                    "fp16",
                ]
            )
            if result.returncode:
                raise RuntimeError("Synthetic fixture generation failed: " + result.stderr[-2000:])

            # This bounded download is only for the synthetic test fixture, not production outputs.
            def fetch(remote: str, local: Path) -> None:
                local.mkdir(exist_ok=True)
                with transport.sftp() as sftp:
                    entries = sftp.listdir_attr(remote)
                assert len(entries) < 100
                for entry in entries:
                    assert entry.filename not in (".", "..") and "/" not in entry.filename
                    target = local / entry.filename
                    if entry.st_mode is not None and stat.S_ISDIR(entry.st_mode):
                        fetch(remote + "/" + entry.filename, target)
                    else:
                        assert entry.st_size is not None and entry.st_size < 4 * 1024**2
                        transport.download(remote + "/" + entry.filename, target, entry.st_size)

            bundle = root / "bundle"
            fetch(remote_fixture + "/bundle", bundle)
            run = RunnerInput.model_validate_json((bundle / "run.json").read_bytes())

            class BundleMaterializer:
                def materialize(
                    self, identifier: UUID, plan: PlanPreview, destination: Path
                ) -> None:
                    shutil.copytree(bundle, destination, dirs_exist_ok=True)
                    value = run.model_copy(update={"run_id": identifier})
                    assert value.plan == plan
                    (destination / "run.json").write_text(value.model_dump_json(by_alias=True))

            training = SshDockerTrainingExecutor(root / "training", BundleMaterializer(), transport)
            training.memory_gib, training.timeout = 4, 240
            image = training.image_identity()
            name = training.submit(run_id, run.plan, image)
            print("SSH/SFTP bundle uploaded; FP16 Soup training started", flush=True)
            deadline = time.monotonic() + 300
            while time.monotonic() < deadline:
                observed = training.status(name, image)
                if observed.state not in ("RUNNING", "PREPARING"):
                    break
                time.sleep(2)
            assert observed.state == "SUCCEEDED", observed
            assert training.logs(name, "stdout", 0).cursor > 0
            with training.output(name, image) as output:
                adapter_bytes = output.read()
            training.cleanup(name, image)
            assert training.status(name, image).state == "MISSING"
            assert training.logs(name, "stdout", 0).cursor > 0
            print(
                json.dumps(
                    {
                        "training_image": image,
                        "precision": "fp16",
                        "adapter_bytes": len(adapter_bytes),
                        "verified_output": True,
                    }
                ),
                flush=True,
            )

            # Cancel a second real offline launch immediately; never override launch arguments.
            canceled = training.submit(cancel_id, run.plan, image)
            assert training.cancel(canceled, image).state == "CANCELED"
            training.cleanup(canceled, image)

            base_id, adapter_id = uuid4(), uuid4()
            archives = {base_id: root / "base.zip", adapter_id: root / "adapter.zip"}
            archives[adapter_id].write_bytes(adapter_bytes)
            with ZipFile(archives[base_id], "w") as archive:
                for path in (bundle / "model").iterdir():
                    if path.suffix in SUFFIXES:
                        archive.write(path, path.name)
            digests = {
                key: hashlib.sha256(path.read_bytes()).hexdigest() for key, path in archives.items()
            }

            class ArtifactMaterializer:
                def artifact(
                    self, identifier: UUID, sha256: str, destination: Path, limit: int
                ) -> None:
                    payload = archives[identifier].read_bytes()
                    assert hashlib.sha256(payload).hexdigest() == sha256 and len(payload) <= limit
                    destination.write_bytes(payload)

            materializer: Any = ArtifactMaterializer()
            serving = SshDockerDeploymentExecutor(
                root / "serving", DeploymentMaterializer(materializer), transport
            )
            spec = DeploymentSpec(
                image_digest=serving.image_identity(),
                base_source=PlanModelSource(
                    source_id=uuid4(),
                    kind="upload",
                    location="synthetic fixture",
                    revision=None,
                    license="CC0",
                    artifact_id=base_id,
                    artifact_sha256=digests[base_id],
                ),
                artifact_id=adapter_id,
                artifact_sha256=digests[adapter_id],
                adapter=True,
                precision="fp16",
                max_model_len=256,
            )
            serving.deploy(deployment_id, spec)
            print("SSH/SFTP trained adapter uploaded; vLLM starting on private bridge", flush=True)
            deadline = time.monotonic() + 600
            while time.monotonic() < deadline:
                observed_serving = serving.status(deployment_id, spec)
                if observed_serving.state == "RUNNING":
                    break
                if observed_serving.state in ("FAILED", "LOST"):
                    logs = transport.docker(["logs", "--tail", "80", serving_name(deployment_id)])
                    raise RuntimeError(
                        "Synthetic vLLM failed: " + logs.stdout[-4000:] + logs.stderr[-4000:]
                    )
                time.sleep(2)
            else:
                raise RuntimeError("SSH vLLM readiness timed out")
            answer = serving.chat(
                deployment_id,
                spec,
                ChatRequest.model_validate(
                    {"messages": [{"role": "user", "content": "Say hello"}]}
                ),
            )
            assert isinstance(answer.content, str)
            info = serving.inspect(deployment_id, spec)
            assert info is not None and not info["HostConfig"].get("PortBindings")
            serving.stop(deployment_id, spec)
            assert serving.status(deployment_id, spec).state == "LOST"
            print(
                json.dumps(
                    {
                        "serving_image": spec.image_digest,
                        "ssh_only_inference": True,
                        "stop_cleanup": True,
                    }
                ),
                flush=True,
            )
        finally:
            for identifier in (run_id, cancel_id):
                transport.docker(["rm", "--force", container_name(identifier)])
                transport.remove(config.workspace + "/training/" + str(identifier))
            if serving is not None and spec is not None:
                serving.stop(deployment_id, spec)
            transport.remove(remote_fixture)
    print(
        "SSH FP16 training, status, logs, verified download, "
        "cancellation, vLLM chat and cleanup passed",
        flush=True,
    )


if __name__ == "__main__":
    main()
