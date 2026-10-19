"""Check the local Docker NVIDIA runtime and reviewed training precision."""

import argparse
from uuid import uuid4

from app.executors.local_docker import docker

parser = argparse.ArgumentParser()
parser.add_argument("--precision", choices=("bf16", "fp16"), default="bf16")
args = parser.parse_args()

image = docker(["image", "inspect", "kitchensoup-soup-trainer:local", "--format", "{{.Id}}"])
if image.returncode:
    raise SystemExit("Build the trainer first: make build-soup")
name = "kitchensoup-gpu-check-" + uuid4().hex
try:
    result = docker(
        [
            "run",
            "--rm",
            "--name",
            name,
            "--gpus",
            "all",
            "--network",
            "none",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--memory",
            "2g",
            "--pids-limit",
            "128",
            "--tmpfs",
            "/tmp:size=64m",
            "--entrypoint",
            "python",
            image.stdout.strip(),
            "-c",
            "import torch,sys; sys.exit(0 if torch.cuda.is_available() and "
            f"(('bf16' if torch.cuda.is_bf16_supported(including_emulation=False) else 'fp16') "
            f"== {args.precision!r}) else 77)",
        ]
    )
    if result.returncode == 77:
        raise SystemExit(
            "GPU check failed: CUDA is unavailable or "
            f"Soup cannot use {args.precision} on this GPU."
        )
    if result.returncode:
        raise SystemExit(
            "GPU check failed: Docker could not expose CUDA; check the NVIDIA container runtime."
        )
    print(
        f"Docker CUDA and {args.precision.upper()} are available. "
        "Workload VRAM fit is not measured."
    )
finally:
    docker(["rm", "--force", name])
