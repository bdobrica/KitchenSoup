from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import BinaryIO
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError

from app.config import Settings
from app.db.models import Artifact, ArtifactDerivation, ModelVersion
from app.deployments.schemas import DeploymentCreate, DeploymentSpec
from app.executors.base import ExecutorError
from app.jobs.locking import claim
from app.main import create_app
from app.services.deployments import DeploymentService
from app.services.lineage import base_requirement
from app.services.training import TrainingPlanService
from app.services.transformations import TransformationService
from app.services.versions import DatasetVersionService
from app.transformations.schemas import AWQ, MERGED, TransformCreate, TransformSpec
from tests.integration.test_deployments import Serving, trained
from tests.integration.test_training_plans import inputs
from tests.integration.test_training_runs import versions

__all__ = ["versions"]


class Backend:
    def __init__(self) -> None:
        self.starts: list[UUID] = []
        self.state = "SUCCEEDED"
        self.fail_cleanup = False
        self.outputs = 0

    def image_identity(self) -> str:
        return "sha256:" + "b" * 64

    def start(self, identifier: UUID, spec: TransformSpec) -> None:
        self.starts.append(identifier)

    def status(self, identifier: UUID, spec: TransformSpec) -> str:
        return self.state

    def output(self, identifier: UUID, spec: TransformSpec, stream: BinaryIO) -> None:
        self.outputs += 1
        stream.write(b"synthetic verified executor output")

    def cleanup(self, identifier: UUID, spec: TransformSpec) -> None:
        if self.fail_cleanup:
            raise ExecutorError("Synthetic Docker outage")


def test_representations_identity_lineage_and_reuse(
    versions: DatasetVersionService, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    model_id = trained(versions, tmp_path, monkeypatch)
    artifacts = versions.datasets.artifacts
    backend = Backend()
    service = TransformationService(artifacts, backend)
    with artifacts.factory() as session:
        lora = session.scalar(select(Artifact).where(Artifact.model_version_id == model_id))
        assert lora is not None
        lora_id = lora.id
        count = session.scalar(select(func.count()).select_from(ModelVersion))
    body = TransformCreate(operation="merge")
    with ThreadPoolExecutor(max_workers=2) as pool:
        rows = list(pool.map(lambda _: service.create(lora_id, body), range(2)))
    assert rows[0].id == rows[1].id
    row = rows[0]
    with claim(service.factory, row.id) as acquired:
        assert acquired
        service.tick("worker")
    assert backend.starts == []
    service.tick("worker")
    assert service.get(row.id).status == "RUNNING"
    service.tick("worker")
    assert backend.starts == [row.id]
    merged = service.get(row.id)
    assert merged.status == "SUCCEEDED" and merged.output_artifact_id
    assert service.create(lora_id, body).output_artifact_id == merged.output_artifact_id
    assert artifacts.get(merged.output_artifact_id).format == MERGED["fp16"]
    with artifacts.factory() as session:
        assert base_requirement(session, model_id).ready
        assert session.scalar(select(func.count()).select_from(ModelVersion)) == count
        edges = list(
            session.scalars(
                select(ArtifactDerivation).where(
                    ArtifactDerivation.child_id == merged.output_artifact_id
                )
            )
        )
        assert lora_id in [edge.parent_id for edge in edges]
    spec = inputs(versions).model_copy(update={"base_model_version_id": model_id})
    plan = TrainingPlanService(artifacts.factory).preview(spec)
    assert plan.resolved.base_model_source.artifact_id == merged.output_artifact_id
    assert (
        plan.resolved.base_model_source.artifact_sha256
        == artifacts.get(merged.output_artifact_id).sha256
    )
    quant = service.create(
        merged.output_artifact_id, TransformCreate(operation="awq", calibration=["hello " * 128])
    )
    service.tick("worker")
    service.tick("reconciler")
    awq = service.get(quant.id)
    assert awq.status == "SUCCEEDED" and awq.output_artifact_id
    assert artifacts.get(awq.output_artifact_id).format == AWQ
    serving = DeploymentService(artifacts, Serving())
    deployed = serving.create(
        DeploymentCreate(model_version_id=model_id, artifact_id=awq.output_artifact_id)
    )
    with artifacts.factory() as session:
        snapshot = DeploymentSpec.model_validate(serving.row(session, deployed.id).snapshot)
        assert snapshot.representation == "awq" and not snapshot.adapter
    assert serving.create(DeploymentCreate(model_version_id=model_id)).artifact_id == lora_id
    for sql in (
        "UPDATE artifact_transformations SET snapshot='{}' WHERE id=:id",
        "UPDATE artifact_transformations SET status='RUNNING' WHERE id=:id",
        "UPDATE artifact_transformations SET output_artifact_id=NULL WHERE id=:id",
        "DELETE FROM artifact_transformations WHERE id=:id",
    ):
        with artifacts.factory() as session, pytest.raises(DBAPIError):
            session.execute(text(sql), {"id": row.id})
    with TestClient(create_app(Settings(_env_file=None), transformation_service=service)) as client:
        response = client.post(
            f"/api/v1/artifacts/{lora_id}/transformations", json={"operation": "merge"}
        )
        assert response.status_code == 202 and response.json()["id"] == str(row.id)
        assert "calibration" not in response.json()
        assert client.get(f"/api/v1/model-versions/{model_id}/transformations").status_code == 200
        assert (
            client.get(f"/api/v1/transformations/{row.id}").headers["cache-control"] == "no-store"
        )


def test_crash_recovery_and_registration_cleanup_retry(
    versions: DatasetVersionService, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    model = trained(versions, tmp_path, monkeypatch)
    artifacts = versions.datasets.artifacts
    with artifacts.factory() as session:
        lora = session.scalar(select(Artifact.id).where(Artifact.model_version_id == model))
        assert lora
    backend = Backend()
    service = TransformationService(artifacts, backend)
    row = service.create(lora, TransformCreate(operation="merge"))
    service.update(row.id, "PREPARING")
    backend.state = "LOST"
    service.tick("reconciler")
    assert service.get(row.id).status == "LOST" and not backend.starts
    row = service.create(lora, TransformCreate(operation="merge", precision="bf16"))
    service.update(row.id, "PREPARING")
    backend.state = "SUCCEEDED"
    backend.fail_cleanup = True
    service.tick("reconciler")
    assert service.get(row.id).status == "POSTPROCESSING"
    output = service.get(row.id).output_artifact_id
    assert output and backend.outputs == 1
    backend.fail_cleanup = False
    service = TransformationService(artifacts, backend)
    service.tick("worker")
    assert service.get(row.id).status == "SUCCEEDED" and backend.outputs == 1
    assert service.get(row.id).output_artifact_id == output


def test_invalid_output_cleans_up_without_registration(
    versions: DatasetVersionService, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.transformations.local import InvalidTransformOutput

    model = trained(versions, tmp_path, monkeypatch)
    artifacts = versions.datasets.artifacts
    with artifacts.factory() as session:
        lora = session.scalar(select(Artifact.id).where(Artifact.model_version_id == model))
        assert lora
    backend = Backend()
    service = TransformationService(artifacts, backend)
    row = service.create(lora, TransformCreate(operation="merge"))

    def corrupt(identifier: UUID, spec: TransformSpec, stream: BinaryIO) -> None:
        raise InvalidTransformOutput("Synthetic hash mismatch")

    monkeypatch.setattr(backend, "output", corrupt)
    service.tick("worker")
    service.tick("reconciler")
    assert service.get(row.id).status == "FAILED"
    assert service.get(row.id).output_artifact_id is None
