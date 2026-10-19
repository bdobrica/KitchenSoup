import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from pathlib import Path
from typing import BinaryIO
from uuid import uuid4
from zipfile import ZipFile

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError

from app.config import Settings
from app.db.models import (
    Artifact,
    ArtifactDerivation,
    ModelSource,
    ModelVersion,
    ModelVersionParent,
)
from app.executors.base import ExecutorError
from app.executors.local_docker import LocalDockerTrainingExecutor
from app.ingestion.sources import SourceError
from app.jobs.locking import claim
from app.jobs.processor import JobProcessor
from app.main import create_app
from app.registry.schemas import ArchiveImport
from app.services.lineage import graph
from app.services.models import ModelService
from app.services.runs import RunCreate, RunView, TrainingRunService
from app.services.versions import DatasetVersionService
from app.storage.base import StorageError
from app.storage.keys import upload_key
from app.training.runner import file_record
from app.training.soup import RunnerManifest
from tests.integration.test_model_registry import Resolver
from tests.integration.test_training_plans import inputs
from tests.integration.test_training_runs import setup_service, versions
from tests.unit.test_local_executor import DockerFixture

__all__ = ["versions"]


def complete_output(service: TrainingRunService, backend: DockerFixture, run: RunView) -> Path:
    assert isinstance(service.executor, LocalDockerTrainingExecutor)
    workspace = service.executor.workspace(run.external_id)
    output = workspace / "output"
    adapter = output / "adapter"
    adapter.mkdir()
    (adapter / "adapter_config.json").write_text('{"peft_type":"LORA"}')
    (adapter / "adapter_model.safetensors").write_bytes(b"synthetic adapter bytes")
    launch = json.loads((workspace / "launch.json").read_text())
    manifest = RunnerManifest(
        run_id=run.id,
        status="succeeded",
        stage="complete",
        exit_code=0,
        soup_exit_code=0,
        error=None,
        image_digest=run.image_digest,
        input_sha256=launch["input_sha256"],
        plan_sha256=run.plan_sha256,
        command=["soup", "train"],
        files=[],
        adapters=[file_record(output, path) for path in sorted(adapter.iterdir())],
        logs_truncated=False,
    )
    (output / "output-manifest.json").write_text(manifest.model_dump_json(by_alias=True))
    backend.state = "exited"
    return output


@pytest.mark.parametrize("uploaded", [False, True])
def test_completed_run_registers_reusable_model_and_independent_lineages(
    versions: DatasetVersionService,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    model_archive: bytes,
    uploaded: bool,
) -> None:
    service, backend = setup_service(versions, tmp_path, monkeypatch)
    spec = inputs(versions)
    artifacts = versions.datasets.artifacts
    models = ModelService(service.factory, artifacts, Resolver())
    source_artifact = None
    if uploaded:
        grant = artifacts.initiate("model.zip", "application/zip", len(model_archive))
        artifacts.store.put(upload_key(grant.upload_id, "model.zip"), BytesIO(model_archive))
        source_artifact = artifacts.complete(grant.upload_id).id
        base = models.import_archive(
            ArchiveImport(
                name='Base "<script>evil</script>',
                artifact_id=source_artifact,
                license="CC0",
                license_url="https://example.com/license",
            )
        )
        spec.base_model_version_id = base.versions[0].id
    plan = service.plans.create(spec)
    with service.factory() as session:
        original = session.scalar(
            select(ModelSource).where(ModelSource.model_version_id == spec.base_model_version_id)
        )
        assert original is not None
        original.license = "Changed after plan review"
        original.license_url = "https://example.com/license"
        original.notes = "Retained license declaration"
        session.commit()
    run = service.create(RunCreate(plan_id=plan.id))
    processor = JobProcessor(service)
    processor.tick("worker")
    complete_output(service, backend, run)
    processor.tick("reconciler")
    result = service.get(run.id)
    assert result.status == "SUCCEEDED" and result.model_version_id and result.model_id
    with claim(service.factory, run.id) as acquired:
        assert acquired
        processor.process(run.id, "reconciler")
    processor.tick("reconciler")
    registered = models.get(result.model_id)
    version = registered.versions[0]
    assert version.training_run_id == run.id and version.parent_ids == [spec.base_model_version_id]
    assert len(version.artifact_ids) == 1
    assert version.sources[0].license == plan.resolved.base_model_source.license
    assert version.sources[0].revision == plan.resolved.base_model_source.revision
    assert version.sources[0].license_url == "https://example.com/license"
    assert version.sources[0].notes == "Retained license declaration"
    artifact = artifacts.get(version.artifact_ids[0])
    assert artifact.format == "kitchensoup.lora-adapter+zip/v1"
    with artifacts.store.get(artifact.object_key) as body:
        contents = body.read()
    assert (
        len(contents) == artifact.size_bytes
        and hashlib.sha256(contents).hexdigest() == artifact.sha256
    )
    with ZipFile(BytesIO(contents)) as bundle:
        assert set(bundle.namelist()) == {
            "output-manifest.json",
            "adapter/adapter_config.json",
            "adapter/adapter_model.safetensors",
        }
        assert bundle.read("adapter/adapter_model.safetensors") == b"synthetic adapter bytes"
    with service.factory() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(ModelVersion)
                .where(ModelVersion.training_run_id == run.id)
            )
            == 1
        )
        model_graph = graph(session, version.id, "model")
        artifact_graph = graph(session, artifact.id, "artifact")
        assert {node.id for node in model_graph.nodes} == {version.id, spec.base_model_version_id}
        expected_parents = {plan.resolved.examples_artifact_id}
        if source_artifact:
            expected_parents.add(source_artifact)
        assert expected_parents <= {edge.parent_id for edge in artifact_graph.edges}
        assert all(
            edge.operation == "fine-tune"
            for edge in artifact_graph.edges
            if edge.child_id == artifact.id
        )
        assert "<script>" not in model_graph.mermaid
        assert not (
            {node.id for node in model_graph.nodes} & {node.id for node in artifact_graph.nodes}
        )
        assert service.registrar is not None
        assert service.registrar.register(session, service.row(session, run.id)) == version.id
    with TestClient(
        create_app(
            Settings(_env_file=None),
            artifact_service=artifacts,
            model_service=models,
            training_run_service=service,
        )
    ) as client:
        assert any(
            model["id"] == str(result.model_id) for model in client.get("/api/v1/models").json()
        )
        assert client.get(f"/api/v1/model-versions/{version.id}/lineage").json()["kind"] == "model"
        assert client.get(f"/api/v1/artifacts/{artifact.id}/lineage").json()["kind"] == "artifact"
        requirement = client.get(f"/api/v1/model-versions/{version.id}/base-requirement").json()
        assert requirement["requires_merge"] and not requirement["ready"]
        assert client.get(
            f"/api/v1/model-versions/{spec.base_model_version_id}/base-requirement"
        ).json()["ready"]
        assert client.get(f"/api/v1/model-versions/{uuid4()}/lineage").status_code == 404
        assert client.get(f"/api/v1/artifacts/{uuid4()}/lineage").status_code == 404
        assert client.get(f"/models/{result.model_id}").status_code == 200
        assert client.get(f"/artifacts?artifact_id={artifact.id}").status_code == 200
    spec.base_model_version_id = version.id
    with pytest.raises(SourceError, match="merged representation"):
        service.plans.preview(spec)
    with service.factory() as session, pytest.raises(DBAPIError, match="immutable"):
        session.execute(
            text("UPDATE model_versions SET training_run_id=NULL WHERE id=:id"), {"id": version.id}
        )
    with service.factory() as session, pytest.raises(DBAPIError):
        session.add(ModelVersion(model_id=result.model_id, version=2, training_run_id=run.id))
        session.flush()
    service.cleanup(run.id)
    assert artifacts.get(artifact.id).sha256 == hashlib.sha256(contents).hexdigest()


def test_storage_failure_recovers_postprocessing_without_relaunch_or_duplicate_registration(
    versions: DatasetVersionService,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, backend = setup_service(versions, tmp_path, monkeypatch)
    plan = service.plans.create(inputs(versions))
    run = service.create(RunCreate(plan_id=plan.id))
    processor = JobProcessor(service)
    processor.tick("worker")
    complete_output(service, backend, run)
    store = versions.datasets.artifacts.store
    put = store.put
    keys = []
    attempted_hashes = []

    def interrupted(key: str, source: BinaryIO) -> object:
        keys.append(key)
        attempted_hashes.append(hashlib.sha256(source.read()).hexdigest())
        source.seek(0)
        with ThreadPoolExecutor() as pool:
            assert pool.submit(service.get, run.id).result(timeout=3).status == "POSTPROCESSING"
            assert (
                pool.submit(service.events, run.id).result(timeout=3)[-1].status == "POSTPROCESSING"
            )
        # Duplicate delivery cannot enter the registration transaction.
        JobProcessor(service).process(run.id, "reconciler")
        put(key, source)
        raise StorageError("Synthetic lost write acknowledgement")

    monkeypatch.setattr(store, "put", interrupted)
    processor.tick("reconciler")
    assert service.get(run.id).status == "POSTPROCESSING"
    with service.factory() as session:
        assert (
            session.scalar(select(ModelVersion).where(ModelVersion.training_run_id == run.id))
            is None
        )
    monkeypatch.setattr(store, "put", put)
    # A fresh service recovers registration from committed PG state.
    restarted = TrainingRunService(
        service.plans, service.executor, artifacts=versions.datasets.artifacts
    )
    JobProcessor(restarted).tick("reconciler")
    result = restarted.get(run.id)
    assert result.status == "SUCCEEDED" and result.model_version_id
    with service.factory() as session:
        artifact = session.scalar(
            select(Artifact).where(Artifact.model_version_id == result.model_version_id)
        )
        assert artifact is not None and artifact.object_key == keys[0]
        assert artifact.sha256 == attempted_hashes[0]
    assert sum(call[0] == "create" for call in backend.calls) == 1
    assert [e.status for e in service.events(run.id)][-2:] == ["POSTPROCESSING", "SUCCEEDED"]


def test_unverified_adapter_is_never_registered(
    versions: DatasetVersionService,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, backend = setup_service(versions, tmp_path, monkeypatch)
    plan = service.plans.create(inputs(versions))
    run = service.create(RunCreate(plan_id=plan.id))
    processor = JobProcessor(service)
    processor.tick("worker")
    output = complete_output(service, backend, run)
    (output / "adapter/adapter_model.safetensors").write_bytes(b"changed")
    with pytest.raises(ExecutorError, match="Verified"):
        with service.executor.output(run.external_id, run.image_digest):
            pytest.fail("Unverified output escaped")
    processor.tick("reconciler")
    assert service.get(run.id).status == "FAILED"
    assert not service.get(run.id).model_version_id


def test_lineage_guards_reject_transitive_cycles_and_rewriting_provenance(
    versions: DatasetVersionService,
) -> None:
    spec = inputs(versions)
    factory = versions.datasets.factory
    with factory() as session:
        parent = session.get(ModelVersion, spec.base_model_version_id)
        assert parent is not None
        child = ModelVersion(model_id=parent.model_id, version=2)
        grandchild = ModelVersion(model_id=parent.model_id, version=3)
        session.add_all([child, grandchild])
        session.flush()
        session.add_all(
            [
                ModelVersionParent(child_id=child.id, parent_id=parent.id),
                ModelVersionParent(child_id=grandchild.id, parent_id=child.id),
            ]
        )
        a = Artifact(
            bucket="fixture", object_key="a", sha256="a" * 64, size_bytes=1, format="fixture"
        )
        b = Artifact(
            bucket="fixture", object_key="b", sha256="b" * 64, size_bytes=1, format="fixture"
        )
        c = Artifact(
            bucket="fixture", object_key="c", sha256="c" * 64, size_bytes=1, format="fixture"
        )
        session.add_all([a, b, c])
        session.flush()
        session.add_all(
            [
                ArtifactDerivation(child_id=b.id, parent_id=a.id, operation="test"),
                ArtifactDerivation(child_id=c.id, parent_id=b.id, operation="test"),
            ]
        )
        session.commit()
        identifiers = [
            ("model_version_parents", parent.id, grandchild.id),
            ("artifact_derivations", a.id, c.id),
        ]
    for table, child_id, parent_id in identifiers:
        with factory() as session, pytest.raises(DBAPIError, match="cycle"):
            operation = ", operation" if table == "artifact_derivations" else ""
            value = ", 'test'" if operation else ""
            session.execute(
                text(
                    f"INSERT INTO {table} (child_id,parent_id{operation}) "
                    f"VALUES (:child,:parent{value})"
                ),
                {"child": child_id, "parent": parent_id},
            )
        with factory() as session, pytest.raises(DBAPIError, match="immutable"):
            session.execute(text(f"DELETE FROM {table}"))
