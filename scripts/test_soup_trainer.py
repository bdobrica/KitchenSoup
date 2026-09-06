"""Exercise the pinned image offline with a generated tiny model and synthetic examples."""

import atexit
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
IMAGE = "kitchensoup-soup-trainer:local"
image_id = subprocess.check_output(
    ["docker", "image", "inspect", IMAGE, "--format", "{{.Id}}"], text=True
).strip()
gpu = "--gpu" in sys.argv
container_name = "kitchensoup-trainer-test-" + uuid4().hex


def cleanup_container() -> None:
    subprocess.run(
        ["docker", "rm", "--force", container_name],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )


atexit.register(cleanup_container)
common = [
    "docker",
    "run",
    "--rm",
    "--name",
    container_name,
    "--network",
    "none",
    "--read-only",
    "--cap-drop",
    "ALL",
    "--security-opt",
    "no-new-privileges",
    "--pids-limit",
    "256",
    "--memory",
    "4g",
    "--tmpfs",
    "/tmp:rw,nosuid,nodev,size=512m",
    "--shm-size",
    "256m",
    "--user",
    f"{os.getuid()}:{os.getgid()}",
    "-e",
    "HOME=/tmp",
    "-e",
    "OMP_NUM_THREADS=2",
]
if gpu:
    result = subprocess.run(
        common
        + [
            "--gpus",
            "all",
            "--entrypoint",
            "python",
            image_id,
            "-c",
            "import torch,sys; sys.exit(0 if torch.cuda.is_available() and "
            "torch.cuda.is_bf16_supported(including_emulation=False) else 77)",
        ]
    )
    if result.returncode == 77:
        print("SKIPPED GPU training: current recipes require a native BF16 CUDA GPU.")
        raise SystemExit(0)
    if result.returncode:
        raise SystemExit(
            f"GPU smoke prerequisite failed (Docker exit {result.returncode}); "
            "check the NVIDIA container runtime and driver setup."
        )
with tempfile.TemporaryDirectory(prefix="kitchensoup-trainer-") as directory:
    root = Path(directory)
    bundle, output = root / "input", root / "output"
    bundle.mkdir()
    output.mkdir()
    subprocess.run(
        common
        + [
            "--volume",
            f"{bundle}:/bundle",
            "--volume",
            f"{ROOT / 'scripts/soup_smoke_fixture.py'}:/fixture.py:ro",
            "--volume",
            f"{ROOT / 'tests/fixtures/training/conversations.jsonl'}:/fixture.jsonl:ro",
            "--entrypoint",
            "python",
            image_id,
            "/fixture.py",
            "/bundle",
        ],
        check=True,
    )
    command = (
        common
        + (["--gpus", "all"] if gpu else [])
        + [
            "--volume",
            f"{bundle}:/input:ro",
            "--volume",
            f"{output}:/output",
            image_id,
            "--image-digest",
            image_id,
            "--timeout",
            "180",
        ]
    )
    if not gpu:
        command.append("--validate-only")
    result = subprocess.run(command)
    manifest = json.loads((output / "output-manifest.json").read_text())
    if result.returncode:
        # Synthetic-only diagnostics, never used for real run workspaces.
        for name in ("stderr.log", "stdout.log"):
            if (output / name).exists():
                print((output / name).read_text()[-5000:])
        print(manifest["error"])
    result.check_returncode()
    assert manifest["status"] == ("succeeded" if gpu else "validated")
    assert manifest["image_digest"] == image_id and manifest["soup_exit_code"] == 0
    assert "Unknown config" not in (output / "stderr.log").read_text()
    token_rows = [json.loads(line) for line in (output / "train.jsonl").read_text().splitlines()]
    assert len(token_rows) == 2
    assert all(row["labels"][0] == -100 for row in token_rows)
    assert all(any(value != -100 for value in row["labels"][1:]) for row in token_rows)
    assert (
        sum(value != -100 for value in token_rows[1]["labels"]) < len(token_rows[1]["labels"]) // 2
    )
    if gpu:
        assert {Path(item["path"]).name for item in manifest["adapters"]} >= {
            "adapter_config.json",
            "adapter_model.safetensors",
        }
    else:
        assert manifest["adapters"] == []
    if not gpu:
        # A separate CLI-only CPU probe exercises Soup/TRL's actual training API.
        # It is not a successful BF16 runner attempt: Soup uses FP32 on CPU.
        probe = root / "cpu-probe"
        probe.mkdir()
        for name in ("soup.yaml", "train.jsonl"):
            shutil.copyfile(output / name, probe / name)
        shutil.copytree(output / "tokenized", probe / "tokenized")
        with (probe / "cli.log").open("w") as log:
            trained = subprocess.run(
                common
                + [
                    "--volume",
                    f"{bundle}:/input:ro",
                    "--volume",
                    f"{probe}:/output",
                    "--workdir",
                    "/output",
                    "--entrypoint",
                    "soup",
                    image_id,
                    "train",
                    "--config",
                    "/output/soup.yaml",
                    "--yes",
                ],
                stdout=log,
                stderr=subprocess.STDOUT,
                timeout=180,
            )
        if trained.returncode:
            print((probe / "cli.log").read_text()[-8000:])
        trained.check_returncode()
        assert (probe / "adapter/adapter_config.json").is_file()
        assert (probe / "adapter/adapter_model.safetensors").stat().st_size > 0
        print(
            "CPU CLI-only training probe passed; "
            "BF16 runner execution still requires compatible GPU"
        )

    # An immutable attempt cannot be overwritten, including a second launch after success.
    before = (output / "output-manifest.json").read_bytes()
    retry = subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    assert retry.returncode != 0 and (output / "output-manifest.json").read_bytes() == before
    print(
        "Soup image smoke passed: pinned CLI, offline input hashes, final-target masks, "
        "status, logs and immutable output"
    )
