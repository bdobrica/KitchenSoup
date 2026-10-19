from pathlib import Path
from unittest.mock import MagicMock
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.config import Settings
from app.ingestion.sources import SourceError
from app.jobs.processor import JobProcessor
from app.main import create_app
from app.services.runs import RunCreate, TrainingRunService
from app.services.targets import TargetService
from app.services.versions import DatasetVersionService
from app.targets.schemas import TargetCreate
from tests.integration.test_lineage import complete_output
from tests.integration.test_training_plans import inputs
from tests.integration.test_training_runs import setup_service, versions
from tests.unit.test_sagemaker import configuration

__all__ = ["versions"]


def body() -> TargetCreate:
    return TargetCreate(
        name="AWS fixture",
        kind="sagemaker",
        credential_ref="aws-profile://default",
        configuration=configuration(),
    )


def test_target_api_opt_in_and_sql_immutability(versions: DatasetVersionService) -> None:
    artifacts = versions.datasets.artifacts
    targets = TargetService(artifacts, Settings(_env_file=None))
    with TestClient(create_app(Settings(_env_file=None), target_service=targets)) as client:
        api = "/api/v1/execution-targets"
        payload = body().model_dump(mode="json", by_alias=True)
        assert client.post(api, json=payload).status_code == 503
        targets.settings.sagemaker_execution_enabled = True
        response = client.post(api, json=payload)
        assert response.status_code == 201, response.text
        identifier = response.json()["id"]
        assert (
            client.get(api + "/" + identifier).json()["configuration"] == payload["configuration"]
        )
        assert client.get(api).json()[0]["kind"] == "sagemaker"
        with pytest.raises(SourceError, match="separate pinned"):
            targets.deployment(UUID(identifier))
        payload["kind"] = "ssh_docker"
        assert client.post(api, json=payload).status_code == 422
        payload = body().model_dump(mode="json", by_alias=True)
        payload["credential_ref"] = "inline-sensitive-key"
        invalid = client.post(api, json=payload)
        assert invalid.status_code == 422 and "inline-sensitive-key" not in invalid.text
    for assignment in (
        "kind='local_docker'",
        "configuration='{}'::jsonb",
        "credential_ref='aws-profile://other'",
        "name='changed'",
    ):
        with artifacts.factory() as session, pytest.raises(DBAPIError):
            session.execute(
                text(f"UPDATE execution_targets SET {assignment} WHERE id=:id"), {"id": identifier}
            )
            session.commit()
    with artifacts.factory() as session, pytest.raises(DBAPIError):
        session.execute(text("DELETE FROM execution_targets WHERE id=:id"), {"id": identifier})
        session.commit()
    with artifacts.factory() as session, pytest.raises(DBAPIError):
        session.execute(
            text(
                "INSERT INTO execution_targets (id,name,kind,configuration) "
                "VALUES (:id,'invalid','sagemaker','{}'::jsonb)"
            ),
            {"id": uuid4()},
        )
        session.commit()


def test_aws_job_registry_restart_clone_cancel_cleanup_and_no_local_fallback(
    versions: DatasetVersionService,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    local, backend = setup_service(versions, tmp_path, monkeypatch)
    session = MagicMock()
    sm, s3, ecr = MagicMock(), MagicMock(), MagicMock()
    session.client.side_effect = lambda name, **_: {"sagemaker": sm, "s3": s3, "ecr": ecr}[name]
    monkeypatch.setattr("app.executors.sagemaker.boto3.Session", lambda **_: session)
    s3.get_bucket_versioning.return_value = {}
    s3.get_bucket_location.return_value = {"LocationConstraint": "us-east-2"}
    s3.list_objects_v2.return_value = {}
    s3.list_multipart_uploads.return_value = {}
    ecr.describe_images.return_value = {
        "imageDetails": [
            {
                "imageDigest": configuration().training_image.rsplit("@", 1)[1],
                "imageManifestMediaType": "application/vnd.oci.image.manifest.v1+json",
            }
        ]
    }
    targets = TargetService(
        versions.datasets.artifacts,
        Settings(
            _env_file=None,
            sagemaker_execution_enabled=True,
            aws_approved_profiles="default",
            sagemaker_staging_workspace=str(tmp_path / "staging"),
        ),
    )
    target = targets.create(body())
    service = TrainingRunService(
        local.plans, None, artifacts=versions.datasets.artifacts, targets=targets
    )
    plan = local.plans.create(inputs(versions))
    run = service.create(RunCreate(plan_id=plan.id, execution_target_id=target.id))
    assert run.external_references["cloudwatch_stream_prefix"] == run.external_id + "/"
    JobProcessor(service).tick("worker")
    assert service.get(run.id).status == "SUBMITTED" and not backend.calls
    request = sm.create_training_job.call_args.kwargs
    info = {**request, "TrainingJobStatus": "InProgress"}
    sm.describe_training_job.return_value = info
    restarted = TrainingRunService(
        local.plans, None, artifacts=versions.datasets.artifacts, targets=targets
    )
    executor = restarted.executor_for(run.id)
    complete_output(TrainingRunService(local.plans, executor), backend, run)
    info["TrainingJobStatus"] = "Completed"
    JobProcessor(restarted).tick("reconciler")
    result = restarted.get(run.id)
    assert result.status == "SUCCEEDED" and result.model_version_id is not None
    assert result.plan_sha256 == plan.sha256 and result.execution_target_id == target.id
    assert sm.create_training_job.call_count == 1
    restarted.cleanup(run.id)
    clone = restarted.clone(run.id)
    assert clone.execution_target_id == target.id and clone.image_digest == run.image_digest
    assert restarted.cancel(clone.id).status == "CANCELED"
    targets.settings.sagemaker_execution_enabled = False
    with pytest.raises(SourceError, match="disabled"):
        restarted.executor_for(run.id)
