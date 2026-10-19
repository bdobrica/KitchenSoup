import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from app.registry.inspection import RegistryError, validate_structure
from app.transformations.schemas import TransformCreate, TransformSpec


@pytest.mark.parametrize(
    "body",
    [
        {"operation": "awq"},
        {"operation": "awq", "calibration": [" "]},
        {"operation": "awq", "calibration": ["x" * 8001]},
        {"operation": "awq", "calibration": ["x" * 8000] * 9},
        {"operation": "awq", "precision": "bf16", "calibration": ["hello"]},
        {"operation": "merge", "calibration": ["hello"]},
        {"operation": "merge", "base_url": "https://untrusted.example"},
    ],
)
def test_transform_requests_are_bounded(body: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        TransformCreate.model_validate(body)


def test_contract_and_awq_validation() -> None:
    assert (
        json.loads(Path("docs/contracts/transformation-v1.schema.json").read_text())
        == TransformSpec.model_json_schema()
    )
    names = {"tokenizer_config.json", "tokenizer.json", "model.safetensors"}
    config: dict[str, Any] = {
        "model_type": "qwen2",
        "architectures": ["Qwen2ForCausalLM"],
        "quantization_config": {
            "quant_method": "awq",
            "bits": 4,
            "group_size": 128,
            "version": "gemm",
            "zero_point": True,
        },
    }
    validate_structure(names, config, allow_awq=True)
    with pytest.raises(RegistryError):
        validate_structure(names, config)
    config["quantization_config"]["bits"] = 8
    with pytest.raises(RegistryError):
        validate_structure(names, config, allow_awq=True)


def test_executor_isolation_identity_and_verified_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, model_archive: bytes
) -> None:
    import hashlib
    import subprocess
    from io import BytesIO
    from unittest.mock import Mock
    from uuid import uuid4
    from zipfile import ZipFile

    from app.deployments.materialize import DeploymentMaterializer
    from app.executors.base import ExecutorError
    from app.training.runner import file_record
    from app.training.schemas import PlanModelSource
    from app.transformations import local

    identifier = uuid4()
    snapshot = TransformSpec(
        operation="merge",
        model_version_id=uuid4(),
        artifact_id=uuid4(),
        artifact_sha256="b" * 64,
        image_digest="sha256:" + "a" * 64,
        base_source=PlanModelSource(
            source_id=uuid4(),
            kind="upload",
            location="fixture",
            revision=None,
            license="CC0",
            artifact_id=uuid4(),
            artifact_sha256="c" * 64,
        ),
    )
    calls: list[list[str]] = []
    info: dict[str, Any] = {
        "Config": {
            "Image": snapshot.image_digest,
            "Labels": {"kitchensoup.transformation": str(identifier)},
        },
        "State": {"Status": "exited", "ExitCode": 0, "Running": False},
    }
    missing = False

    def docker(args: list[str]) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        if args[:2] == ["container", "inspect"]:
            return subprocess.CompletedProcess(
                args,
                1 if missing else 0,
                stdout=json.dumps([info]),
                stderr="No such container" if missing else "",
            )
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(local, "docker", docker)
    executor = local.LocalTransformExecutor(tmp_path / "jobs", Mock(spec=DeploymentMaterializer))
    executor.start(identifier, snapshot)
    command = next(c for c in calls if c[0] == "create")
    assert command[command.index("--network") + 1] == "none"
    assert "--read-only" in command and "--cap-drop" in command and "--user" in command
    assert command[-1] == snapshot.image_digest and "--gpus" in command
    assert not any("docker.sock" in arg or "TOKEN=" in arg for arg in command)
    root = executor.workspace(identifier)
    output = root / "output"
    model = output / "model"
    model.mkdir()
    with ZipFile(BytesIO(model_archive)) as archive:
        for item in archive.infolist():
            relative = Path(item.filename)
            if relative.name and not item.is_dir():
                target = model / relative.relative_to(relative.parts[0])
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(archive.read(item))
    records = [
        file_record(model, p).model_dump(mode="json")
        for p in sorted(model.rglob("*"))
        if p.is_file()
    ]
    payload = (root / "input/transform.json").read_bytes()
    (output / "result.json").write_text(
        json.dumps({"input_sha256": hashlib.sha256(payload).hexdigest(), "files": records})
    )
    executor.output(identifier, snapshot, BytesIO())
    first = next(model.rglob("*.safetensors"))
    first.write_bytes(b"tampered")
    with pytest.raises(ExecutorError, match="verification"):
        executor.output(identifier, snapshot, BytesIO())
    info["Config"]["Labels"]["kitchensoup.transformation"] = "wrong"
    with pytest.raises(ExecutorError, match="identity"):
        executor.cleanup(identifier, snapshot)
    assert root.exists()
    info["Config"]["Labels"]["kitchensoup.transformation"] = str(identifier)
    executor.cleanup(identifier, snapshot)
    assert not root.exists()
    missing = True
    executor.cleanup(identifier, snapshot)
    assert executor.status(identifier, snapshot) == "LOST"
