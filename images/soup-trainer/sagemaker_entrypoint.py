"""SageMaker filesystem adapter; the existing Soup CLI runner owns training."""

import argparse
import os
import resource
import shutil
import stat
import sys
from pathlib import Path

from entrypoint import prepare

from app.training.runner import execute, read_bounded
from app.training.soup import RunnerManifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image-digest", required=True)
    parser.add_argument("--timeout", type=int, required=True)
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args()
    if not 60 <= args.timeout <= 3600:
        parser.error("Managed training timeout must be between 60 and 3600 seconds")
    # SageMaker supplies /opt/ml/input/data/bundle, /opt/ml/model and SIGTERM.
    # Keep preparation/cache data outside the artifact directory. Publish only
    # the same manifest, bounded logs and verified adapters used by other executors.
    resource.setrlimit(resource.RLIMIT_FSIZE, (1024**3, 1024**3))
    # SageMaker owns the volume mounts. Initialize the two writable locations,
    # then drop root before the runner or any model/data parsing occurs.
    workspace = Path("/opt/ml/output/kitchensoup")
    destination = Path("/opt/ml/model")
    for directory in (Path("/opt/ml/output"), workspace, destination, Path("/opt/ml/output/cache")):
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chown(directory, 10001, 10001)
    # Native File-mode downloads can be root-owned with private file modes.
    # Change metadata only; no model/data parser runs before the privilege drop.
    inputs = Path("/opt/ml/input/data/bundle")
    paths = [Path("/opt/ml/input"), Path("/opt/ml/input/data"), inputs]
    for index, path in enumerate(inputs.rglob("*")):
        if index >= 8192:
            raise RuntimeError("Managed input inventory exceeds limits")
        paths.append(path)
    for path in paths:
        mode = path.lstat().st_mode
        if not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
            raise RuntimeError("Managed inputs must be regular files and directories")
        os.chown(path, 10001, 10001, follow_symlinks=False)
        path.chmod(0o500 if stat.S_ISDIR(mode) else 0o400)
    os.setgroups([])
    os.setgid(10001)
    os.setuid(10001)
    code = execute(
        Path("/input"),
        Path("/output"),
        args.image_digest,
        prepare,
        timeout=args.timeout,
        validate_only=args.validate_only,
    )
    manifest_bytes = read_bounded(workspace / "output-manifest.json", 8 * 1024**2)
    manifest = RunnerManifest.model_validate_json(manifest_bytes)
    destination.mkdir(parents=True, exist_ok=True)
    for item in manifest.adapters:
        source, target = workspace / item.path, destination / item.path
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    for stream in ("stdout", "stderr"):
        source = workspace / (stream + ".log")
        if source.exists():
            shutil.copyfile(source, destination / source.name)
            # SageMaker uploads model files only for a successful job. Retain a
            # bounded diagnostic tail in CloudWatch even when the CLI fails.
            with source.open("rb") as log:
                log.seek(max(0, source.stat().st_size - 65536))
                target_stream = sys.stdout if stream == "stdout" else sys.stderr
                target_stream.write(log.read(65536).decode("utf-8", errors="replace"))
                target_stream.flush()
    (destination / "output-manifest.json").write_bytes(manifest_bytes)
    print(
        f"KitchenSoup runner: status={manifest.status} stage={manifest.stage} exit={code}",
        flush=True,
    )
    return code


if __name__ == "__main__":
    os.environ["HF_HOME"] = "/opt/ml/output/cache"
    os.environ["HF_DATASETS_CACHE"] = "/opt/ml/output/cache/datasets"
    os.umask(0o077)
    raise SystemExit(main())
