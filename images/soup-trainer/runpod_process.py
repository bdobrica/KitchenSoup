"""Image-owned direct execution helper; no provider SDK, daemon or credentials."""

import hashlib
import json
import os
import resource
import subprocess
import sys
from pathlib import Path
from uuid import UUID

from entrypoint import prepare

from app.training.runner import execute, read_bounded, verify_bundle
from app.training.soup import RunnerInput


def main():
    if sys.argv[1:] == ["probe"]:
        import torch

        print(
            json.dumps(
                {
                    "cuda": torch.cuda.is_available(),
                    "count": torch.cuda.device_count(),
                    "name": torch.cuda.get_device_name(0),
                    "capability": torch.cuda.get_device_capability(0),
                    "bf16": torch.cuda.is_bf16_supported(including_emulation=False),
                    "vram_gb": torch.cuda.get_device_properties(0).total_memory / 1024**3,
                }
            )
        )
        return
    mode, directory, image, timeout, input_sha = sys.argv[1:]
    workspace = Path(directory)
    if workspace != Path("/home/runner/jobs") / str(UUID(workspace.name)) or workspace.is_symlink():
        raise SystemExit("Invalid workspace")
    if os.getuid() != 10001 or not 1 <= int(timeout) <= 3600:
        raise SystemExit("Invalid workload identity or timeout")
    inputs = workspace / "input"
    data = read_bounded(inputs / "run.json", 8 * 1024**2)
    if hashlib.sha256(data).hexdigest() != input_sha:
        raise SystemExit("Transferred input identity mismatch")
    run = RunnerInput.model_validate_json(data)
    if str(run.run_id) != workspace.name:
        raise SystemExit("Transferred run identity mismatch")
    verify_bundle(inputs, run)
    if mode == "launch":
        # Keep translator v2's fixed /input and /output paths unchanged. A Pod is
        # dedicated to one attempt; this link can be claimed only once.
        Path("/home/runner/active").symlink_to(workspace, target_is_directory=True)
        # Claim before spawning; an uncertain response must not replay this launch.
        with (workspace / "started").open("x") as marker:
            marker.write("started")
        env = {
            "PATH": "/usr/local/bin:/usr/bin:/bin",
            "PYTHONPATH": "/runner",
            "HOME": "/home/runner",
            "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1",
        }
        for name in ("LD_LIBRARY_PATH", "CUDA_VISIBLE_DEVICES"):
            if name in os.environ:
                env[name] = os.environ[name]
        subprocess.Popen(
            [sys.executable, __file__, "run", directory, image, timeout, input_sha],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=env,
            start_new_session=True,
        )
    elif mode == "run":
        resource.setrlimit(resource.RLIMIT_FSIZE, (1024**3, 1024**3))
        if Path("/home/runner/active").resolve() != workspace:
            raise SystemExit("Active workspace mismatch")
        code = execute(Path("/input"), Path("/output"), image, prepare, timeout=int(timeout))
        temporary = workspace / "exit.tmp"
        temporary.write_text(json.dumps({"exit_code": code}))
        temporary.replace(workspace / "exit.json")
    else:
        raise SystemExit("Unknown operation")


if __name__ == "__main__":
    main()
