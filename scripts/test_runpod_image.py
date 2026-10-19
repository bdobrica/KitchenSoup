"""Local SSH/SFTP and direct-runner smoke; no Runpod API or cloud spending."""

import argparse
import hashlib
import json
import os
import secrets
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from uuid import uuid4

import httpx
import paramiko

from app.executors.base import ExecutorError
from app.targets.schemas import SshConfig
from app.targets.ssh import SshTransport
from app.training.soup import RunnerInput, RunnerManifest

ROOT = Path(__file__).resolve().parents[1]


def generate_fixture(root: Path) -> Path:
    bundle = root / "fixture-bundle"
    bundle.mkdir()
    script = root / "fixture.py"
    script.write_text(
        (ROOT / "scripts/soup_smoke_fixture.py")
        .read_text()
        .replace("hidden_size=32", "hidden_size=128")
        + '\nimport json\np=model/"tokenizer_config.json"\nd=json.loads(p.read_text())\n'
        'd["tokenizer_class"]="PreTrainedTokenizerFast"\np.write_text(json.dumps(d))\n'
        'run=RunnerInput.model_validate_json((root/"run.json").read_bytes())\n'
        "run.model_files=[file_record(model,p) for p in sorted(model.iterdir()) if p.is_file()]\n"
        '(root/"run.json").write_text(run.model_dump_json(by_alias=True))\n'
    )
    subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--network",
            "none",
            "--user",
            f"{os.getuid()}:{os.getgid()}",
            "--env",
            "HOME=/tmp",
            "--env",
            "OMP_NUM_THREADS=2",
            "--entrypoint",
            "python",
            "--volume",
            f"{bundle}:/bundle",
            "--volume",
            f"{script}:/fixture.py:ro",
            "--volume",
            f"{ROOT / 'tests/fixtures/training/conversations.jsonl'}:/fixture.jsonl:ro",
            "kitchensoup-runpod-trainer:local",
            "/fixture.py",
            "/bundle",
            "fp16",
        ],
        check=True,
        timeout=90,
    )
    return bundle


def validate_serving(root: Path, bundle: Path, transport: SshTransport, name: str) -> None:
    identifier = uuid4()
    workspace = root / str(identifier)
    workspace.mkdir()
    shutil.copytree(bundle / "model", workspace / "model")
    key = secrets.token_urlsafe(32)
    (workspace / "inference.key").write_text(key)
    (workspace / "serving.json").write_text(
        json.dumps(
            {
                "dtype": "half",
                "max_model_len": 256,
                "adapter": False,
                "remaining_seconds": 240,
                "expires_at": time.time() + 240,
            }
        )
    )
    manifest = {
        p.relative_to(workspace).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in workspace.rglob("*")
        if p.is_file()
    }
    data = json.dumps(manifest).encode()
    (workspace / "manifest.json").write_bytes(data)
    remote = "/home/runner/jobs/" + str(identifier)
    transport.upload(workspace, remote)
    result = transport.command(
        ["python3", "/runner/runpod_serving.py", "launch", remote, hashlib.sha256(data).hexdigest()]
    )
    if result.returncode:
        raise RuntimeError("Direct serving launch failed")
    port = (
        subprocess.check_output(["docker", "port", name, "8000"], text=True)
        .strip()
        .rsplit(":", 1)[1]
    )
    endpoint = "http://127.0.0.1:" + port
    deadline = time.monotonic() + 180
    with httpx.Client(timeout=15, trust_env=False) as client:
        while True:
            if transport.read(remote + "/exit.json", 1024, missing_ok=True):
                print(
                    transport.read(remote + "/server.log", 65536)
                    .decode(errors="replace")
                    .replace(key, "[REDACTED]")
                )
                raise RuntimeError("Direct serving process failed")
            try:
                response = client.get(
                    endpoint + "/v1/models", headers={"Authorization": "Bearer " + key}
                )
                if response.status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            if time.monotonic() >= deadline:
                raise RuntimeError("Local serving readiness exceeded deadline")
            time.sleep(2)
        for route in ("/v1/models", "/tokenize", "/invocations", "/metrics"):
            assert client.get(endpoint + route).status_code == 401
        body = {
            "model": "playground",
            "messages": [{"role": "user", "content": "Hello"}],
            "max_tokens": 16,
            "temperature": 0,
        }
        response = client.post(
            endpoint + "/v1/chat/completions", headers={"Authorization": "Bearer " + key}, json=body
        )
        assert response.status_code == 200 and isinstance(
            response.json()["choices"][0]["message"]["content"], str
        )
        with client.stream(
            "POST",
            endpoint + "/v1/chat/completions",
            headers={"Authorization": "Bearer " + key},
            json={**body, "stream": True},
        ) as response:
            assert response.status_code == 200 and "[DONE]" in response.read().decode()
    print("Local FP16 Runpod serving: SSH/SFTP, authentication and both inference modes passed")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gpu", action="store_true")
    parser.add_argument(
        "--serving", action="store_true", help="Local FP16 serving image check (requires --gpu)"
    )
    args = parser.parse_args()
    if args.serving and not args.gpu:
        parser.error("--serving requires --gpu")
    name = "kitchensoup-runpod-image-" + uuid4().hex
    with tempfile.TemporaryDirectory(prefix="kitchensoup-runpod-image-") as directory:
        root = Path(directory)
        bundle = generate_fixture(root)
        client_key, host_key = paramiko.RSAKey.generate(3072), paramiko.RSAKey.generate(3072)
        key_path = root / "client.key"
        client_key.write_private_key_file(str(key_path))
        key_path.chmod(0o600)
        host_path = root / "host.key"
        host_key.write_private_key_file(str(host_path))
        host_path.chmod(0o600)
        environment = os.environ.copy()
        environment["PUBLIC_KEY"] = client_key.get_name() + " " + client_key.get_base64()
        environment["KITCHENSOUP_SSH_HOST_KEY"] = host_path.read_text()
        try:
            subprocess.run(
                [
                    "docker",
                    "run",
                    "--detach",
                    "--rm",
                    "--name",
                    name,
                    "--publish",
                    "127.0.0.1::22",
                    "--env",
                    "PUBLIC_KEY",
                    "--env",
                    "KITCHENSOUP_SSH_HOST_KEY",
                    *(["--publish", "127.0.0.1::8000"] if args.serving else []),
                    *(["--gpus", "all"] if args.gpu else []),
                    "kitchensoup-runpod-vllm:local"
                    if args.serving
                    else "kitchensoup-runpod-trainer:local",
                ],
                env=environment,
                check=True,
                stdout=subprocess.DEVNULL,
                timeout=30,
            )
            port = int(
                subprocess.check_output(["docker", "port", name, "22"], text=True)
                .strip()
                .rsplit(":", 1)[1]
            )
            known_hosts = root / "known_hosts"
            known_hosts.write_text(
                f"[127.0.0.1]:{port} {host_key.get_name()} {host_key.get_base64()}\n"
            )
            transport = SshTransport(
                SshConfig(
                    host="127.0.0.1", port=port, username="runner", workspace="/home/runner/jobs"
                ),
                "file://" + str(key_path),
                known_hosts=known_hosts,
                secret_directory=root,
                env_names=set(),
            )
            deadline = time.monotonic() + 30
            while True:
                try:
                    assert transport.identity() == (10001, 10001)
                    break
                except ExecutorError:
                    if time.monotonic() >= deadline:
                        raise
                    time.sleep(1)
            if args.serving:
                validate_serving(root, bundle, transport, name)
                return
            run = RunnerInput.model_validate_json((bundle / "run.json").read_bytes())
            workspace = root / str(run.run_id)
            shutil.copytree(bundle, workspace / "input")
            (workspace / "output").mkdir()
            remote = "/home/runner/jobs/" + str(run.run_id)
            transport.upload(workspace, remote)
            image = "sha256:" + "a" * 64
            if args.gpu:
                result = transport.command(
                    [
                        "python",
                        "/runner/runpod_process.py",
                        "launch",
                        remote,
                        image,
                        "180",
                        hashlib.sha256((bundle / "run.json").read_bytes()).hexdigest(),
                    ]
                )
                if result.returncode:
                    raise RuntimeError(
                        transport.read(remote + "/output/output-manifest.json", 16384).decode()
                    )
                deadline = time.monotonic() + 240
                while not transport.read(remote + "/exit.json", 1024, missing_ok=True):
                    if time.monotonic() > deadline:
                        raise RuntimeError("Direct runner exceeded local smoke deadline")
                    time.sleep(2)
            else:
                assert (
                    transport.command(["ln", "-s", remote, "/home/runner/active"]).returncode == 0
                )
                code = (
                    "from pathlib import Path; from app.training.runner import execute; "
                    "from entrypoint import prepare; "
                    "raise SystemExit(execute(Path('/input'), Path('/output'),"
                    f"{image!r}, prepare, validate_only=True))"
                )
                result = transport.command(
                    ["env", "PYTHONPATH=/runner", "python", "-c", code], timeout=90
                )
                if result.returncode:
                    for filename in ("output-manifest.json", "stdout.log", "stderr.log"):
                        print(
                            transport.read(
                                remote + "/output/" + filename, 32768, missing_ok=True
                            ).decode(errors="replace")
                        )
                    raise RuntimeError("Local direct runner validation failed")
            data = transport.read(remote + "/output/output-manifest.json", 8 * 1024**2)
            manifest = RunnerManifest.model_validate_json(data)
            assert manifest.run_id == run.run_id and manifest.image_digest == image
            assert manifest.status == ("succeeded" if args.gpu else "validated")
            assert manifest.soup_exit_code == 0
            print(
                "Local Runpod image SSH/SFTP, non-root identity and direct runner:", manifest.status
            )
        finally:
            subprocess.run(
                ["docker", "rm", "--force", name],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )


if __name__ == "__main__":
    main()
