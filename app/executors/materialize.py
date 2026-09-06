"""Materialize registered bytes; no credentials or URLs enter the runner bundle."""

import hashlib
import re
import shutil
import time
from pathlib import Path
from urllib.parse import quote, urljoin, urlsplit
from uuid import UUID

import httpx

from app.db.models import Artifact, DatasetVersion
from app.executors.base import ExecutorError
from app.registry.inspection import inspect_archive
from app.services.artifacts import ArtifactService
from app.storage.archives import ArchiveLimits, extract_zip
from app.training.runner import file_record, verify_bundle
from app.training.schemas import PlanPreview
from app.training.soup import RunnerFile, RunnerInput

SUFFIXES = {".json", ".safetensors", ".txt", ".model", ".tiktoken", ".jinja"}
MAX_MODEL = 8 * 1024**3


class RegisteredInputMaterializer:
    def __init__(self, artifacts: ArtifactService) -> None:
        self.artifacts = artifacts

    def artifact(self, identifier: UUID, sha256: str, destination: Path, limit: int) -> None:
        with self.artifacts.factory() as session:
            row = session.get(Artifact, identifier)
            if row is None or row.sha256 != sha256 or row.size_bytes > limit:
                raise ExecutorError("Registered training artifact is missing or exceeds limits")
            key, size = row.object_key, row.size_bytes
        with (
            self.artifacts.store.get(key, max_bytes=limit) as source,
            destination.open("xb") as out,
        ):
            digest = hashlib.sha256()
            count = 0
            while chunk := source.read(1024**2):
                count += len(chunk)
                if count > size:
                    raise ExecutorError("Training artifact size mismatch")
                digest.update(chunk)
                out.write(chunk)
        if count != size or digest.hexdigest() != sha256:
            raise ExecutorError("Training artifact hash or size mismatch")

    def materialize(self, run_id: UUID, plan: PlanPreview, destination: Path) -> None:
        resolved = plan.resolved
        with self.artifacts.factory() as session:
            dataset = session.get(DatasetVersion, resolved.dataset_version_id)
            if dataset is None or dataset.manifest_artifact_id is None:
                raise ExecutorError("Registered dataset manifest is missing")
            manifest_id = dataset.manifest_artifact_id
        self.artifact(
            manifest_id,
            resolved.dataset_manifest_sha256,
            destination / "manifest.json",
            8 * 1024**2,
        )
        self.artifact(
            resolved.examples_artifact_id,
            resolved.examples_sha256,
            destination / "examples.jsonl",
            64 * 1024**2,
        )
        model = destination / "model"
        model.mkdir(mode=0o700)
        source = resolved.base_model_source
        if source.kind == "upload":
            assert source.artifact_id is not None and source.artifact_sha256 is not None
            archive = destination / "model.zip"
            try:
                self.artifact(source.artifact_id, source.artifact_sha256, archive, MAX_MODEL)
                with archive.open("rb") as reader:
                    inspect_archive(reader)
                    with extract_zip(reader, ArchiveLimits(max_expanded_bytes=MAX_MODEL)) as root:
                        config = next(root.rglob("config.json"))
                        for path in config.parent.rglob("*"):
                            if path.is_file() and path.suffix in SUFFIXES:
                                target = model / path.relative_to(config.parent)
                                target.parent.mkdir(parents=True, exist_ok=True)
                                shutil.copyfile(path, target)
            finally:
                archive.unlink(missing_ok=True)
        else:
            download_model(source.location, source.revision or "", model)
        run = RunnerInput(
            schema_name="kitchensoup.training-input/v1",
            run_id=run_id,
            plan=plan,
            model_source=source,
            model_files=[file_record(model, p) for p in sorted(model.rglob("*")) if p.is_file()],
        )
        verify_bundle(destination, run)
        (destination / "run.json").write_text(run.model_dump_json(by_alias=True))


def download_model(repository: str, revision: str, destination: Path) -> None:
    """Anonymous exact-commit download with bounded, explicitly allowlisted redirects."""
    if not re.fullmatch(r"[\w.-]+/[\w.-]+", repository) or not re.fullmatch(
        r"[0-9a-f]{40}", revision
    ):
        raise ExecutorError("Model requires a public repository and exact commit")
    start = time.monotonic()
    total = 0
    with httpx.Client(timeout=60, trust_env=False, follow_redirects=False) as client:

        def fetch(url: str, path: Path, maximum: int) -> None:
            nonlocal total
            for _ in range(6):
                parsed = urlsplit(url)
                host = parsed.hostname or ""
                if (
                    parsed.scheme != "https"
                    or parsed.username
                    or parsed.password
                    or parsed.port not in (None, 443)
                    or not any(
                        host == h or host.endswith("." + h) for h in ("huggingface.co", "hf.co")
                    )
                ):
                    raise ExecutorError("Model download redirected outside the allowed hosts")
                with client.stream("GET", url) as response:
                    if response.is_redirect:
                        url = urljoin(url, response.headers.get("location", ""))
                        continue
                    if response.status_code != 200:
                        raise ExecutorError("Public model download failed; verify availability")
                    size = 0
                    with path.open("xb") as out:
                        for chunk in response.iter_bytes(1024**2):
                            size += len(chunk)
                            total += len(chunk)
                            if (
                                size > maximum
                                or total > MAX_MODEL
                                or time.monotonic() - start > 1800
                            ):
                                raise ExecutorError("Model download exceeded its resource limits")
                            out.write(chunk)
                    return
            raise ExecutorError("Too many model download redirects")

        metadata = destination / "hub-metadata"
        fetch(
            f"https://huggingface.co/api/models/{repository}/revision/{revision}",
            metadata,
            4 * 1024**2,
        )
        import json

        info = json.loads(metadata.read_bytes())
        metadata.unlink()
        if (
            info.get("sha") != revision
            or info.get("gated") is not False
            or info.get("private") is not False
        ):
            raise ExecutorError("Model revision is not public and ungated")
        names = [item["rfilename"] for item in info["siblings"]]
        if len(names) > 4096 or len(names) != len(set(names)):
            raise ExecutorError("Model file inventory exceeds limits or contains duplicates")
        for name in sorted(names):
            RunnerFile(path=name, sha256="0" * 64, size_bytes=0)
            if Path(name).suffix not in SUFFIXES:
                continue
            target = destination / name
            target.parent.mkdir(parents=True, exist_ok=True)
            fetch(
                f"https://huggingface.co/{repository}/resolve/{revision}/{quote(name, safe='/')}",
                target,
                MAX_MODEL,
            )
