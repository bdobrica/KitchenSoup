"""SageMaker's fixed /ping and /invocations boundary; no AWS credentials."""

import hashlib
import json
import os
import subprocess
import sys
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import uvicorn
from fastapi import FastAPI, Request, Response


def verify(root, expected):
    raw = (root / "manifest.json").read_bytes()
    if len(raw) > 1024**2 or hashlib.sha256(raw).hexdigest() != expected:
        raise ValueError("Manifest identity mismatch")
    manifest = json.loads(raw)
    actual = {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()}
    if actual != set(manifest) | {"manifest.json"}:
        raise ValueError("Unexpected serving files")
    for name, digest in manifest.items():
        target = root / name
        if ".." in Path(name).parts or Path(name).is_absolute() or target.is_symlink():
            raise ValueError("Invalid serving path")
        if hashlib.file_digest(target.open("rb"), "sha256").hexdigest() != digest:
            raise ValueError("Serving hash mismatch")
    settings = json.loads((root / "serving.json").read_text())
    if settings["precision"] not in ("fp16", "bf16") or type(settings["adapter"]) is not bool:
        raise ValueError("Invalid serving settings")
    if type(settings["max_model_len"]) is not int or not 256 <= settings["max_model_len"] <= 2048:
        raise ValueError("Invalid context")
    return settings


def application(process, adapter):
    @asynccontextmanager
    async def lifespan(app):
        try:
            yield
        finally:
            process.terminate()
            try:
                process.wait(timeout=20)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

    @app.get("/ping")
    async def ping():
        try:
            async with httpx.AsyncClient(trust_env=False, timeout=1) as client:
                result = await client.get("http://127.0.0.1:8000/health")
            return Response(
                status_code=200 if process.poll() is None and result.status_code == 200 else 503
            )
        except httpx.HTTPError:
            return Response(status_code=503)

    @app.post("/invocations")
    async def invoke(request: Request):
        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw) > 128 * 1024:
                return Response(status_code=413)
        try:
            body = json.loads(raw)
            messages = body["messages"]
            if not isinstance(messages, list) or not 1 <= len(messages) <= 32:
                raise ValueError
            total = 0
            for message in messages:
                if set(message) != {"role", "content"} or message["role"] not in (
                    "system",
                    "user",
                    "assistant",
                ):
                    raise ValueError
                content = message["content"]
                if not isinstance(content, str) or not 1 <= len(content) <= 8000:
                    raise ValueError
                total += len(content)
            if total > 16000 or messages[-1]["role"] != "user":
                raise ValueError
            payload = {
                "model": "tuned" if adapter else "playground",
                "messages": messages,
                "max_tokens": 128,
                "temperature": 0,
                "stream": False,
            }
            async with httpx.AsyncClient(trust_env=False, timeout=50) as client:
                async with client.stream(
                    "POST", "http://127.0.0.1:8000/v1/chat/completions", json=payload
                ) as result:
                    if result.status_code != 200:
                        return Response(status_code=502)
                    output = bytearray()
                    async for chunk in result.aiter_bytes():
                        output.extend(chunk)
                        if len(output) > 256 * 1024:
                            return Response(status_code=502)
            return Response(bytes(output), media_type="application/json")
        except (ValueError, KeyError, TypeError):
            return Response(status_code=400)
        except httpx.HTTPError:
            return Response(status_code=502)

    return app


def prepare_cache():
    # SageMaker starts custom containers as root. Drop before loading model bytes.
    cache = Path(tempfile.mkdtemp(prefix="kitchensoup-serving-", dir="/tmp"))
    if os.getuid() == 0:
        os.chown(cache, 10001, 10001)
        os.setgroups([])
        os.setgid(10001)
        os.setuid(10001)
    # Managed /tmp can be root-owned/non-writable. Use a private initialized home,
    # rather than relying on world-writable directories or preexisting caches.
    for name, directory in {
        "HOME": "",
        "HF_HOME": "hf",
        "TORCHINDUCTOR_CACHE_DIR": "torchinductor",
        "TRITON_CACHE_DIR": "triton",
        "VLLM_CACHE_ROOT": "vllm",
        "XDG_CACHE_HOME": "cache",
        "CUDA_CACHE_PATH": "cuda",
        "TMPDIR": "tmp",
    }.items():
        target = cache / directory
        target.mkdir(mode=0o700, exist_ok=True)
        os.environ[name] = str(target)
    tempfile.tempdir = str(cache / "tmp")
    return cache


def main():
    if sys.argv[1:] != ["serve"]:
        raise ValueError("Only SageMaker serve is supported")
    root = Path("/opt/ml/model")
    settings = verify(root, os.environ["KITCHENSOUP_MANIFEST_SHA256"])
    prepare_cache()
    import torch

    if not torch.cuda.is_available() or torch.cuda.device_count() != 1:
        raise ValueError("One CUDA GPU is required")
    if settings["precision"] == "bf16" and not torch.cuda.is_bf16_supported(
        including_emulation=False
    ):
        raise ValueError("Native BF16 required")
    args = [
        sys.executable,
        "-m",
        "vllm.entrypoints.openai.api_server",
        "--model",
        str(root / "model"),
        "--served-model-name",
        "playground",
        "--host",
        "127.0.0.1",
        "--port",
        "8000",
        "--dtype",
        "half" if settings["precision"] == "fp16" else "bfloat16",
        "--load-format",
        "safetensors",
        "--max-model-len",
        str(settings["max_model_len"]),
        "--max-num-seqs",
        "1",
        "--gpu-memory-utilization",
        "0.75",
        "--enforce-eager",
        "--disable-log-requests",
        "--disable-log-stats",
        "--uvicorn-log-level",
        "warning",
    ]
    if settings["adapter"]:
        args += [
            "--enable-lora",
            "--max-loras",
            "1",
            "--max-lora-rank",
            "64",
            "--lora-modules",
            "tuned=" + str(root / "adapter"),
        ]
    process = subprocess.Popen(args)
    uvicorn.run(
        application(process, settings["adapter"]),
        host="0.0.0.0",
        port=8080,
        access_log=False,
        log_level="warning",
    )


if __name__ == "__main__":
    main()
