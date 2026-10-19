import copy
import gzip
import io
import tarfile
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import botocore.session
import pytest
from botocore.exceptions import ClientError
from botocore.validate import validate_parameters
from pydantic import ValidationError

from app.executors.base import ExecutorError
from app.executors.sagemaker import SageMakerTrainingExecutor
from app.targets.sagemaker_schemas import SageMakerConfig
from app.targets.schemas import TargetCreate
from app.training import runner
from app.training.runner import execute
from app.training.soup import RunnerInput
from tests.unit.test_local_executor import IMAGE, BundleMaterializer, bundle

__all__ = ["bundle"]


def configuration() -> SageMakerConfig:
    return SageMakerConfig(
        region="us-east-2",
        role_arn="arn:aws:iam::123456789012:role/training",
        training_image="123456789012.dkr.ecr.us-east-2.amazonaws.com/trainer@" + IMAGE,
        staging_bucket="test-staging",
    )


@pytest.fixture
def executor(
    bundle: tuple[Path, RunnerInput], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> SageMakerTrainingExecutor:
    session = MagicMock()
    clients = {"sagemaker": MagicMock(), "s3": MagicMock(), "ecr": MagicMock()}
    session.client.side_effect = lambda name, **_: clients[name]
    clients["s3"].get_bucket_versioning.return_value = {}
    clients["s3"].get_bucket_location.return_value = {"LocationConstraint": "us-east-2"}
    clients["s3"].list_multipart_uploads.return_value = {}
    clients["ecr"].describe_images.return_value = {
        "imageDetails": [
            {
                "imageDigest": IMAGE,
                "imageManifestMediaType": "application/vnd.oci.image.manifest.v1+json",
            }
        ]
    }
    monkeypatch.setattr("app.executors.sagemaker.boto3.Session", lambda **_: session)
    return SageMakerTrainingExecutor(
        tmp_path / "jobs",
        BundleMaterializer(bundle[0]),
        configuration(),
        "aws-profile://default",
        {"default"},
    )


def submitted(executor: SageMakerTrainingExecutor, run: RunnerInput) -> tuple[str, dict[str, Any]]:
    name = executor.submit(run.run_id, run.plan, IMAGE)
    request = executor.sm.create_training_job.call_args.kwargs
    model = botocore.session.get_session().get_service_model("sagemaker")
    shape = model.operation_model("CreateTrainingJob").input_shape
    assert shape is not None
    validate_parameters(request, shape)
    info = {**copy.deepcopy(request), "TrainingJobStatus": "InProgress"}
    executor.sm.describe_training_job.return_value = info
    return name, info


@pytest.mark.parametrize(
    "field,value",
    [
        ("training_image", "trainer:latest"),
        ("region", "eu-west-1"),
        ("staging_prefix", "../other"),
        ("max_runtime_seconds", 3601),
        ("instance_type", "ml.p5.48xlarge"),
        ("volume_gib", 1000),
    ],
)
def test_reject_unbounded_or_unpinned_targets(field: str, value: Any) -> None:
    body = configuration().model_dump(by_alias=True)
    body[field] = value
    with pytest.raises(ValidationError):
        SageMakerConfig.model_validate(body)


def test_profile_and_target_authority(executor: SageMakerTrainingExecutor, tmp_path: Path) -> None:
    with pytest.raises(ExecutorError, match="approved"):
        SageMakerTrainingExecutor(
            tmp_path / "other",
            executor.materializer,
            configuration(),
            "aws-profile://unapproved",
            {"default"},
        )
    for kind, reference in (("ssh_docker", "aws-profile://default"), ("sagemaker", "env://KEY")):
        with pytest.raises(ValidationError):
            TargetCreate(
                name="invalid", kind=kind, credential_ref=reference, configuration=configuration()
            )


def test_submit_is_bounded_offline_and_never_replayed(
    executor: SageMakerTrainingExecutor, bundle: tuple[Path, RunnerInput]
) -> None:
    name, info = submitted(executor, bundle[1])
    assert info["EnableNetworkIsolation"] is True
    assert info["ResourceConfig"]["InstanceCount"] == 1
    assert info["StoppingCondition"] == {"MaxRuntimeInSeconds": 600}
    assert "RetryStrategy" not in info and "VpcConfig" not in info
    assert info["EnableManagedSpotTraining"] is False
    assert set(info["Environment"]) == {"KITCHENSOUP_LAUNCH_NONCE"}
    assert executor.status(name, IMAGE).state == "RUNNING"
    with pytest.raises(ExecutorError, match="already exists"):
        executor.submit(bundle[1].run_id, bundle[1].plan, IMAGE)
    assert executor.sm.create_training_job.call_count == 1
    for call in executor.s3.upload_fileobj.call_args_list:
        assert call.kwargs["Key"].startswith(executor.prefix(name) + "input/")


def test_stop_waits_for_terminal_and_cleanup_is_scoped(
    executor: SageMakerTrainingExecutor, bundle: tuple[Path, RunnerInput]
) -> None:
    name, info = submitted(executor, bundle[1])
    assert executor.cancel(name, IMAGE).state == "RUNNING"
    executor.sm.stop_training_job.assert_called_once_with(TrainingJobName=name)
    info["TrainingJobStatus"] = "Stopping"
    with pytest.raises(ExecutorError, match="Wait"):
        executor.cleanup(name, IMAGE)
    assert executor.cancel(name, IMAGE).state == "RUNNING"
    assert executor.sm.stop_training_job.call_count == 1
    info["TrainingJobStatus"] = "Stopped"
    assert executor.status(name, IMAGE).state == "CANCELED"
    prefix = executor.prefix(name)
    executor.s3.get_bucket_versioning.return_value = {}
    executor.s3.list_objects_v2.side_effect = [
        {"Contents": [{"Key": prefix + "input/run.json"}]},
        {},
        {},
    ]
    executor.s3.list_multipart_uploads.side_effect = [
        {"Uploads": [{"Key": prefix + "input/model", "UploadId": "synthetic-upload"}]},
        {},
        {},
    ]
    executor.cleanup(name, IMAGE)
    executor.cleanup(name, IMAGE)
    executor.s3.delete_object.assert_called_once_with(
        Bucket="test-staging", Key=prefix + "input/run.json"
    )
    executor.s3.abort_multipart_upload.assert_called_once_with(
        Bucket="test-staging", Key=prefix + "input/model", UploadId="synthetic-upload"
    )
    executor.s3.get_bucket_versioning.return_value = {"Status": "Enabled"}
    with pytest.raises(ExecutorError, match="versioning"):
        executor.cleanup(name, IMAGE)


def test_foreign_job_and_aws_outage_never_authorize_stop(
    executor: SageMakerTrainingExecutor, bundle: tuple[Path, RunnerInput]
) -> None:
    name, info = submitted(executor, bundle[1])
    info["Environment"]["KITCHENSOUP_LAUNCH_NONCE"] = "foreign"
    with pytest.raises(ExecutorError, match="identity"):
        executor.cancel(name, IMAGE)
    executor.sm.stop_training_job.assert_not_called()
    executor.sm.describe_training_job.side_effect = ClientError(
        {"Error": {"Code": "AccessDeniedException", "Message": "sensitive provider payload"}},
        "DescribeTrainingJob",
    )
    with pytest.raises(ExecutorError, match="unavailable") as failure:
        executor.status(name, IMAGE)
    assert "sensitive" not in str(failure.value)


def output_archive(
    executor: SageMakerTrainingExecutor, name: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> bytes:
    def cli(path: Path, command: list[str], timeout: int) -> tuple[int, bool]:
        (path / "adapter").mkdir()
        (path / "adapter/adapter_config.json").write_text("{}")
        (path / "adapter/adapter_model.safetensors").write_bytes(b"synthetic")
        (path / "stdout.log").write_text("training completed")
        return 0, False

    monkeypatch.setattr(runner, "invoke_cli", cli)
    output = tmp_path / "produced"
    assert execute(executor.workspace(name) / "input", output, IMAGE, lambda *_: None) == 0
    data = io.BytesIO()
    with tarfile.open(fileobj=data, mode="w:gz") as archive:
        for path in [
            output / "output-manifest.json",
            output / "stdout.log",
            *(output / "adapter").iterdir(),
        ]:
            archive.add(path, arcname=path.relative_to(output).as_posix())
    return data.getvalue()


def test_completed_requires_manifest_hashes_and_exports_existing_registry_zip(
    executor: SageMakerTrainingExecutor,
    bundle: tuple[Path, RunnerInput],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    name, info = submitted(executor, bundle[1])
    payload = output_archive(executor, name, monkeypatch, tmp_path)
    info["TrainingJobStatus"] = "Completed"
    info["ModelArtifacts"] = {
        "S3ModelArtifacts": f"s3://test-staging/{executor.prefix(name)}output/{name}/output/model.tar.gz"
    }
    executor.s3.get_object.return_value = {"Body": io.BytesIO(payload)}
    assert executor.status(name, IMAGE).state == "SUCCEEDED"
    with executor.output(name, IMAGE) as stream:
        assert stream.read(2) == b"PK"
    assert executor.logs(name, "stdout", 0).text == "training completed"
    (executor.workspace(name) / "output/adapter/adapter_model.safetensors").write_bytes(b"tampered")
    assert executor.status(name, IMAGE).state == "FAILED"
    with pytest.raises(ExecutorError):
        with executor.output(name, IMAGE):
            pytest.fail("unverified output escaped")


@pytest.mark.parametrize(
    "name,kind",
    [
        ("../escape", tarfile.REGTYPE),
        ("adapter/link", tarfile.SYMTYPE),
        ("/absolute", tarfile.REGTYPE),
    ],
)
def test_remote_tar_cannot_escape(
    executor: SageMakerTrainingExecutor, bundle: tuple[Path, RunnerInput], name: str, kind: bytes
) -> None:
    external, _ = submitted(executor, bundle[1])
    data = io.BytesIO()
    with tarfile.open(fileobj=data, mode="w:gz") as archive:
        entry = tarfile.TarInfo(name)
        entry.type = kind
        entry.linkname = "/etc/passwd"
        archive.addfile(entry)
    executor.s3.get_object.return_value = {"Body": io.BytesIO(data.getvalue())}
    uri = f"s3://test-staging/{executor.prefix(external)}output/{external}/output/model.tar.gz"
    with pytest.raises(ExecutorError, match="Unsafe"):
        executor.collect(external, {"ModelArtifacts": {"S3ModelArtifacts": uri}})
    assert not (executor.workspace(external) / "output/output-manifest.json").exists()


@pytest.mark.parametrize(
    "code,message,missing",
    [
        ("ResourceNotFound", "absent", True),
        ("ValidationException", "Requested resource not found.", True),
        ("ValidationException", "other provider failure", False),
        ("AccessDeniedException", "Requested resource not found.", False),
    ],
)
def test_missing_job_is_distinct_from_access_or_validation_failure(
    executor: SageMakerTrainingExecutor,
    bundle: tuple[Path, RunnerInput],
    code: str,
    message: str,
    missing: bool,
) -> None:
    name, _ = submitted(executor, bundle[1])
    executor.sm.describe_training_job.side_effect = ClientError(
        {"Error": {"Code": code, "Message": message}}, "DescribeTrainingJob"
    )
    if missing:
        assert executor.status(name, IMAGE).state == "MISSING"
    else:
        with pytest.raises(ExecutorError, match="unavailable"):
            executor.status(name, IMAGE)


def test_oversized_tar_metadata_is_rejected_before_allocation(
    executor: SageMakerTrainingExecutor,
    bundle: tuple[Path, RunnerInput],
) -> None:
    name, info = submitted(executor, bundle[1])
    entry = tarfile.TarInfo("pax-header")
    entry.type, entry.size = tarfile.XHDTYPE, 1024**3
    payload = gzip.compress(entry.tobuf())
    executor.s3.get_object.return_value = {"Body": io.BytesIO(payload)}
    info["TrainingJobStatus"] = "Completed"
    info["ModelArtifacts"] = {
        "S3ModelArtifacts": f"s3://test-staging/{executor.prefix(name)}output/{name}/output/model.tar.gz"
    }
    assert executor.status(name, IMAGE).state == "FAILED"


def test_preflight_rejects_versioned_or_wrong_region_staging_and_manifest_index(
    executor: SageMakerTrainingExecutor,
) -> None:
    executor.s3.get_bucket_versioning.return_value = {"Status": "Suspended"}
    with pytest.raises(ExecutorError, match="versioning"):
        executor.preflight()
    executor.s3.get_bucket_versioning.return_value = {}
    executor.s3.get_bucket_location.return_value = {"LocationConstraint": "eu-west-1"}
    with pytest.raises(ExecutorError, match="same region"):
        executor.preflight()
    executor.s3.get_bucket_location.return_value = {"LocationConstraint": "us-east-2"}
    executor.ecr.describe_images.return_value = {
        "imageDetails": [
            {
                "imageDigest": IMAGE,
                "imageManifestMediaType": "application/vnd.oci.image.index.v1+json",
            }
        ]
    }
    with pytest.raises(ExecutorError, match="manifest index"):
        executor.preflight()
    executor.sm.create_training_job.assert_not_called()


def test_cleanup_refuses_other_prefix_and_lost_identity(
    executor: SageMakerTrainingExecutor,
    bundle: tuple[Path, RunnerInput],
) -> None:
    name, info = submitted(executor, bundle[1])
    info["TrainingJobStatus"] = "Failed"
    executor.s3.list_objects_v2.return_value = {"Contents": [{"Key": "other-run/input/model"}]}
    with pytest.raises(ExecutorError, match="cleanup key"):
        executor.cleanup(name, IMAGE)
    executor.s3.delete_object.assert_not_called()
    (executor.workspace(name) / "request.json").unlink()
    with pytest.raises(ExecutorError, match="identity"):
        executor.cancel(name, IMAGE)
    executor.sm.stop_training_job.assert_not_called()
