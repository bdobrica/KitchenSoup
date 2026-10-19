"""Fixed direct serving entry point; no caller-supplied command or remote code."""

import hashlib
import json
import os
import resource
import subprocess
import sys
import time
from pathlib import Path
from uuid import UUID


def main():
    action = sys.argv[1]
    if action == "probe":
        import torch

        print(
            json.dumps(
                {
                    "cuda": torch.cuda.is_available(),
                    "count": torch.cuda.device_count(),
                    "vram_gb": torch.cuda.get_device_properties(0).total_memory / 1024**3,
                    "bf16": torch.cuda.is_bf16_supported(including_emulation=False),
                    "capability": torch.cuda.get_device_capability(0),
                }
            )
        )
        return
    path = Path(sys.argv[2])
    if str(path) != "/home/runner/jobs/" + str(UUID(path.name)) or path.is_symlink():
        raise ValueError("Invalid workspace")
    os.chdir(path)
    if action == "launch":
        with (path / "started").open("x") as marker:
            marker.write("launch intent")
        manifest_bytes = (path / "manifest.json").read_bytes()
        if hashlib.sha256(manifest_bytes).hexdigest() != sys.argv[3]:
            raise ValueError("Manifest hash mismatch")
        manifest = json.loads(manifest_bytes)
        for name, digest in manifest.items():
            target = path / name
            if target.is_symlink() or not target.is_relative_to(path) or ".." in target.parts:
                raise ValueError("Invalid file")
            if hashlib.file_digest(target.open("rb"), "sha256").hexdigest() != digest:
                raise ValueError("Input hash mismatch")
        with (path / "server.log").open("wb") as log:
            subprocess.Popen(
                [sys.executable, __file__, "serve", str(path)],
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=log,
                start_new_session=True,
                env={
                    "PATH": "/usr/local/bin:/usr/bin:/bin",
                    "HOME": "/tmp",
                    "PYTHONPATH": "/runner",
                    "HF_HUB_OFFLINE": "1",
                    "TRANSFORMERS_OFFLINE": "1",
                    "HF_HUB_DISABLE_IMPLICIT_TOKEN": "1",
                    "VLLM_NO_USAGE_STATS": "1",
                    "DO_NOT_TRACK": "1",
                    "VLLM_USE_V1": "0",
                    "VLLM_ATTENTION_BACKEND": "XFORMERS",
                },
            )
        return
    if action != "serve":
        raise ValueError("Unsupported action")
    config = json.loads((path / "serving.json").read_text())
    resource.setrlimit(resource.RLIMIT_FSIZE, (8 * 1024**2, 8 * 1024**2))
    env = dict(os.environ)
    env["VLLM_API_KEY"] = (path / "inference.key").read_text().strip()
    args = [
        sys.executable,
        "-m",
        "vllm.entrypoints.openai.api_server",
        "--model",
        str(path / "model"),
        "--served-model-name",
        "playground",
        "--host",
        "0.0.0.0",
        "--port",
        "8000",
        "--dtype",
        config["dtype"],
        "--load-format",
        "safetensors",
        "--max-model-len",
        str(config["max_model_len"]),
        "--max-num-seqs",
        "1",
        "--gpu-memory-utilization",
        "0.75",
        "--enforce-eager",
        "--disable-log-requests",
        "--middleware",
        "runpod_auth.InferenceAuth",
        "--disable-log-stats",
        "--uvicorn-log-level",
        "warning",
    ]
    if config["adapter"]:
        args += [
            "--enable-lora",
            "--max-loras",
            "1",
            "--max-lora-rank",
            "64",
            "--lora-modules",
            "tuned=" + str(path / "adapter"),
        ]
    try:
        result = subprocess.run(
            args,
            env=env,
            timeout=max(1, min(config["remaining_seconds"], config["expires_at"] - time.time())),
            check=False,
        )
        code = result.returncode
    except subprocess.TimeoutExpired:
        code = 124
    (path / "exit.json").write_text(json.dumps({"exit_code": code}))


if __name__ == "__main__":
    main()
