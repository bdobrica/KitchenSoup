"""Offline, single-attempt CLI runner. Inputs are materialized by a trusted launcher."""

import hashlib
import json
import os
import re
import signal
import stat
import subprocess
import threading
from collections.abc import Callable
from pathlib import Path

from app.ingestion.versions import DatasetManifest
from app.training.soup import RunnerError, RunnerFile, RunnerInput, RunnerManifest, translate

MAX_INPUT = 8 * 1024**2
MAX_EXAMPLES = 64 * 1024**2
LOG_LIMIT = 8 * 1024**2


def file_record(root: Path, path: Path, maximum: int = 64 * 1024**3) -> RunnerFile:
    if not stat.S_ISREG(path.lstat().st_mode):
        raise RunnerError("Only regular files may enter a training bundle")
    size = path.stat().st_size
    if size > maximum:
        raise RunnerError("Training file exceeds the size limit")
    digest = hashlib.sha256()
    with path.open("rb") as body:
        while chunk := body.read(1024**2):
            digest.update(chunk)
    return RunnerFile(
        path=path.relative_to(root).as_posix(), sha256=digest.hexdigest(), size_bytes=size
    )


def read_bounded(path: Path, maximum: int) -> bytes:
    if not stat.S_ISREG(path.lstat().st_mode):
        raise RunnerError("Input must be a regular file")
    with path.open("rb") as body:
        data = body.read(maximum + 1)
    if len(data) > maximum:
        raise RunnerError("Input exceeds the size limit")
    return data


def verify_bundle(root: Path, run: RunnerInput) -> None:
    model = root / "model"
    if model.is_symlink() or not model.is_dir():
        raise RunnerError("Missing materialized model directory")
    actual = []
    for path in sorted(model.rglob("*")):
        if path.is_symlink():
            raise RunnerError("Model bundle cannot contain symlinks")
        if path.is_dir():
            continue
        if path.suffix not in {".json", ".safetensors", ".txt", ".model", ".tiktoken", ".jinja"}:
            raise RunnerError("Model bundle contains an unsupported file type")
        actual.append(file_record(model, path))
        if len(actual) > 4096:
            raise RunnerError("Too many model files")
    if actual != sorted(run.model_files, key=lambda item: item.path):
        raise RunnerError("Materialized model does not match its file hashes")
    config = json.loads(read_bounded(model / "config.json", MAX_INPUT))
    token_config = json.loads(read_bounded(model / "tokenizer_config.json", MAX_INPUT))
    if (
        config.get("auto_map")
        or token_config.get("auto_map")
        or config.get("model_type") not in {"qwen2", "llama", "mistral"}
        or config.get("quantization_config")
    ):
        raise RunnerError("Model requires unsupported architecture, quantization or custom code")
    if not any(item.path.endswith(".safetensors") for item in actual):
        raise RunnerError("Model has no safetensors weights")
    resolved = run.plan.resolved
    manifest_bytes = read_bounded(root / "manifest.json", MAX_INPUT)
    if hashlib.sha256(manifest_bytes).hexdigest() != resolved.dataset_manifest_sha256:
        raise RunnerError("Dataset manifest hash mismatch")
    manifest = DatasetManifest.model_validate_json(manifest_bytes)
    if (
        manifest.dataset_version_id != resolved.dataset_version_id
        or manifest.dataset_id != resolved.dataset_id
        or manifest.version != resolved.dataset_version
        or manifest.statistics.example_count != resolved.example_count
        or manifest.examples.artifact_id != resolved.examples_artifact_id
        or manifest.examples.sha256 != resolved.examples_sha256
    ):
        raise RunnerError("Dataset manifest references disagree with the reviewed plan")
    examples = file_record(root, root / "examples.jsonl", MAX_EXAMPLES)
    if (
        examples.sha256 != resolved.examples_sha256
        or examples.size_bytes != manifest.examples.size_bytes
    ):
        raise RunnerError("Examples hash or size mismatch")


def invoke_cli(output: Path, command: list[str], timeout: int) -> tuple[int, bool]:
    """Drain private logs concurrently and kill the process group on timeout."""
    runtime = output / "runtime"
    runtime.mkdir()
    env = {
        "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
        "HOME": str(runtime),
        "TMPDIR": str(runtime),
        "HF_HOME": str(runtime / "hf"),
        "HF_HUB_OFFLINE": "1",
        "TRANSFORMERS_OFFLINE": "1",
        "HF_HUB_DISABLE_TELEMETRY": "1",
        "HF_HUB_DISABLE_IMPLICIT_TOKEN": "1",
        "WANDB_MODE": "disabled",
        "TOKENIZERS_PARALLELISM": "false",
        "PYTHONUNBUFFERED": "1",
        "OMP_NUM_THREADS": "2",
    }
    # NVIDIA runtime library discovery is infrastructure, never taken from AppSpec.
    for name in ("LD_LIBRARY_PATH", "CUDA_VISIBLE_DEVICES"):
        if name in os.environ:
            env[name] = os.environ[name]
    truncated = [False, False]
    with subprocess.Popen(
        command,
        cwd=output,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    ) as process:
        assert process.stdout is not None and process.stderr is not None

        def drain(stream: object, path: Path, index: int) -> None:
            # Separate file descriptors avoid interleaving stdout/stderr diagnostics.
            written = 0
            with path.open("wb") as destination:
                while chunk := stream.read(65536):  # type: ignore[attr-defined]
                    keep = chunk[: max(0, LOG_LIMIT - written)]
                    destination.write(keep)
                    written += len(keep)
                    if len(keep) != len(chunk):
                        truncated[index] = True

        threads = [
            threading.Thread(target=drain, args=(stream, output / name, index))
            for index, (stream, name) in enumerate(
                [(process.stdout, "stdout.log"), (process.stderr, "stderr.log")]
            )
        ]
        for thread in threads:
            thread.start()
        try:
            code = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
            code = 124
        finally:
            # Do not let descendant processes keep pipes alive after the CLI exits.
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            for thread in threads:
                thread.join()
    return code, any(truncated)


def execute(
    root: Path,
    output: Path,
    image_digest: str,
    prepare: Callable[[Path, Path, RunnerInput, bool], None],
    *,
    validate_only: bool = False,
    timeout: int = 86400,
) -> int:
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", image_digest):
        raise RunnerError("Launcher must supply the actual content-addressed image identity")
    output.mkdir(exist_ok=True, parents=True)
    if any(output.iterdir()):
        raise RunnerError(
            "Output directory must be empty; attempts cannot overwrite earlier output"
        )
    # Output is private, including potentially sensitive CLI diagnostics.
    os.umask(0o077)
    run = None
    input_hash = None
    stage = "preflight"
    command: list[str] = []
    code, soup_code, truncated, error = 1, None, False, None
    adapters: list[RunnerFile] = []
    try:
        payload = read_bounded(root / "run.json", MAX_INPUT)
        input_hash = hashlib.sha256(payload).hexdigest()
        run = RunnerInput.model_validate_json(payload)
        (output / "run.json").write_bytes(payload)
        verify_bundle(root, run)
        prepare(root, output, run, validate_only)
        (output / "soup.yaml").write_text(translate(run))
        command = ["soup", "train", "--config", str(output / "soup.yaml"), "--yes"]
        if validate_only:
            command.append("--dry-run")
        stage = "training"
        soup_code, truncated = invoke_cli(output, command, timeout)
        code = soup_code if soup_code >= 0 else 128 - soup_code
        if code == 0 and not validate_only:
            adapter = output / "adapter"
            for name in ("adapter_config.json", "adapter_model.safetensors"):
                if not (adapter / name).is_file():
                    raise RunnerError("Soup did not produce the required adapter files")
            for path in sorted(adapter.iterdir()):
                if path.is_file() and path.suffix in {
                    ".json",
                    ".safetensors",
                    ".txt",
                    ".model",
                    ".jinja",
                }:
                    adapters.append(file_record(output, path))
        if code == 0:
            stage = "complete"
        else:
            error = "Soup failed or timed out; inspect private stdout/stderr artifacts"
    except Exception as failure:
        # Do not serialize dependency exceptions: they may contain source text or tokens.
        code = 1
        error = (
            str(failure)
            if isinstance(failure, RunnerError)
            else (
                "Training preparation or output validation failed (" + type(failure).__name__ + ")"
            )
        )
        (output / "error.json").write_text(json.dumps({"error": error}))
        adapters = []
    files = [
        file_record(output, output / name)
        for name in (
            "run.json",
            "soup.yaml",
            "train.jsonl",
            "stdout.log",
            "stderr.log",
            "error.json",
        )
        if (output / name).is_file()
    ]
    manifest = RunnerManifest.model_validate(
        {
            "run_id": run.run_id if run else None,
            "status": ("validated" if validate_only else "succeeded") if code == 0 else "failed",
            "stage": stage,
            "exit_code": code,
            "soup_exit_code": soup_code,
            "error": error,
            "image_digest": image_digest,
            "input_sha256": input_hash,
            "plan_sha256": run.plan.sha256 if run else None,
            "command": command,
            "files": files,
            "adapters": adapters,
            "logs_truncated": truncated,
        }
    )
    temporary_manifest = output / "output-manifest.json.tmp"
    temporary_manifest.write_text(manifest.model_dump_json(by_alias=True, indent=2) + "\n")
    temporary_manifest.replace(output / "output-manifest.json")
    return code
