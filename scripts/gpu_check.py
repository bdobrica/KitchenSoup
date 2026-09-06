"""Check the local Docker NVIDIA runtime and recipe-native BF16 support."""

from uuid import uuid4

from app.executors.local_docker import docker

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
            "torch.cuda.is_bf16_supported(including_emulation=False) else 77)",
        ]
    )
    if result.returncode == 77:
        raise SystemExit("GPU check failed: current recipes require a native BF16 CUDA GPU.")
    if result.returncode:
        raise SystemExit(
            "GPU check failed: Docker could not expose CUDA; check the NVIDIA container runtime."
        )
    print("Docker CUDA and native BF16 are available. Workload VRAM fit is not measured.")
finally:
    docker(["rm", "--force", name])
