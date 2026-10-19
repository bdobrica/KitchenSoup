import copy
import io
import json
from functools import lru_cache
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock
from uuid import uuid4

import botocore.session
import pytest
from botocore.exceptions import ClientError, EndpointConnectionError
from botocore.validate import validate_parameters
from pydantic import ValidationError

from app.deployments.materialize import DeploymentMaterializer
from app.deployments.sagemaker import SageMakerDeploymentExecutor
from app.deployments.schemas import ChatRequest
from app.executors.base import ExecutorError
from app.executors.sagemaker import SageMakerTrainingExecutor
from tests.unit.test_kubernetes_deployments import specification
from tests.unit.test_local_executor import IMAGE
from tests.unit.test_sagemaker import configuration


@lru_cache
def service_model() -> Any:
    return botocore.session.get_session().get_service_model("sagemaker")


class Hosting:
    def __init__(self) -> None:
        self.objects: dict[str, dict[str, Any]] = {}
        self.creates: list[str] = []
        self.deletes: list[str] = []
        self.state = "Creating"
        self.unknown = False
        self.pending_delete = False
        self.ambiguous: str | None = None
        self.client = MagicMock()
        for kind in ("model", "endpoint_config", "endpoint"):
            getattr(self.client, "create_" + kind).side_effect = self.create(kind)
            getattr(self.client, "describe_" + kind).side_effect = self.describe(kind)
            getattr(self.client, "delete_" + kind).side_effect = self.delete(kind)
        self.client.list_tags.side_effect = lambda **kw: {
            "Tags": next(
                info["Tags"] for info in self.objects.values() if kw["ResourceArn"] in info.values()
            )
        }

    def create(self, kind: str) -> Any:
        def call(**request: Any) -> dict[str, str]:
            operation = "Create" + "".join(p.title() for p in kind.split("_"))
            shape = service_model().operation_model(operation).input_shape
            assert shape is not None
            validate_parameters(request, shape)
            self.creates.append(kind)
            data = copy.deepcopy(request)
            key = {"model": "Model", "endpoint_config": "EndpointConfig", "endpoint": "Endpoint"}[
                kind
            ]
            data[key + "Arn"] = (
                "arn:aws:sagemaker:us-east-2:123456789012:"
                + kind.replace("_", "-")
                + "/"
                + request[key + "Name"]
            )
            self.objects[kind] = data
            if self.ambiguous == kind:
                raise EndpointConnectionError(
                    endpoint_url="https://sagemaker.us-east-2.amazonaws.com"
                )
            return {key + "Arn": data[key + "Arn"]}

        return call

    def describe(self, kind: str) -> Any:
        def call(**_: Any) -> dict[str, Any]:
            if self.unknown:
                raise EndpointConnectionError(
                    endpoint_url="https://sagemaker.us-east-2.amazonaws.com"
                )
            if kind not in self.objects:
                raise ClientError(
                    {
                        "Error": {
                            "Code": "ValidationException",
                            "Message": "Could not find resource",
                        }
                    },
                    "Describe",
                )
            data = copy.deepcopy(self.objects[kind])
            if kind == "endpoint":
                data["EndpointStatus"] = self.state
            return data

        return call

    def delete(self, kind: str) -> Any:
        def call(**_: Any) -> dict[str, Any]:
            self.deletes.append(kind)
            if not self.pending_delete:
                self.objects.pop(kind)
            else:
                self.state = "Deleting"
            return {}

        return call


def executor_for(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[SageMakerDeploymentExecutor, Hosting]:
    provider = Hosting()
    session = MagicMock()
    clients = {
        "sagemaker": provider.client,
        "s3": MagicMock(),
        "ecr": MagicMock(),
        "sagemaker-runtime": MagicMock(),
    }
    session.client.side_effect = lambda name, **kw: clients[name]
    clients["s3"].get_bucket_versioning.return_value = {}
    clients["s3"].list_multipart_uploads.return_value = {}
    clients["s3"].get_bucket_location.return_value = {"LocationConstraint": "us-east-2"}
    clients["ecr"].describe_images.return_value = {
        "imageDetails": [
            {
                "imageDigest": IMAGE,
                "imageManifestMediaType": "application/vnd.docker.distribution.manifest.v2+json",
            }
        ]
    }
    monkeypatch.setattr("app.executors.sagemaker.boto3.Session", lambda **kw: session)
    c = configuration().model_copy(
        update={"deployment_image": "123456789012.dkr.ecr.us-east-2.amazonaws.com/vllm@" + IMAGE}
    )
    aws = SageMakerTrainingExecutor(
        root / "training", MagicMock(), c, "aws-profile://default", {"default"}
    )
    materializer = MagicMock(spec=DeploymentMaterializer)

    def materialize(spec: Any, path: Path) -> None:
        (path / "model").mkdir()
        (path / "model/config.json").write_text('{"model_type":"qwen2"}')
        (path / "model/model.safetensors").write_bytes(b"fixture")

    materializer.materialize.side_effect = materialize
    return SageMakerDeploymentExecutor(root / "serving", materializer, aws), provider


@pytest.fixture
def hosted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[SageMakerDeploymentExecutor, Hosting]:
    return executor_for(tmp_path, monkeypatch)


def test_managed_lifecycle_identity_invocation_and_cleanup(
    hosted: tuple[SageMakerDeploymentExecutor, Hosting],
) -> None:
    executor, provider = hosted
    identifier, spec = uuid4(), specification()
    executor.deploy(identifier, spec)
    assert provider.creates == ["model", "endpoint_config", "endpoint"]
    assert provider.objects["model"]["EnableNetworkIsolation"] is True
    assert executor.status(identifier, spec).state == "STARTING"
    provider.state = "InService"
    assert executor.status(identifier, spec).state == "RUNNING"
    executor.runtime.invoke_endpoint.return_value = {
        "ContentType": "application/json",
        "Body": io.BytesIO(b'{"choices":[{"message":{"content":"hello"}}]}'),
    }
    assert (
        executor.chat(
            identifier, spec, ChatRequest(messages=[{"role": "user", "content": "hi"}])
        ).content
        == "hello"
    )
    request = executor.runtime.invoke_endpoint.call_args.kwargs
    assert "EndpointUrl" not in request
    assert json.loads(request["Body"])["model"] == "tuned"
    executor.stop(identifier, spec)
    executor.stop(identifier, spec)
    assert not provider.objects
    assert provider.deletes == ["endpoint", "endpoint_config", "model"]
    assert not (executor.workspace(identifier) / "model").exists()
    assert executor.journal(identifier, spec) is not None


@pytest.mark.parametrize(
    "state,expected",
    [
        ("Failed", "FAILED"),
        ("OutOfService", "FAILED"),
        ("Updating", "FAILED"),
        ("Creating", "STARTING"),
        ("InService", "RUNNING"),
    ],
)
def test_state_mapping(
    hosted: tuple[SageMakerDeploymentExecutor, Hosting], state: str, expected: str
) -> None:
    executor, provider = hosted
    identifier, spec = uuid4(), specification()
    executor.deploy(identifier, spec)
    provider.state = state
    assert executor.status(identifier, spec).state == expected


def test_unknown_state_and_async_delete_remain_pending(
    hosted: tuple[SageMakerDeploymentExecutor, Hosting],
) -> None:
    executor, provider = hosted
    identifier, spec = uuid4(), specification()
    executor.deploy(identifier, spec)
    provider.unknown = True
    with pytest.raises(ExecutorError, match="observation"):
        executor.stop(identifier, spec)
    assert not provider.deletes
    provider.unknown, provider.pending_delete = False, True
    with pytest.raises(ExecutorError, match="pending"):
        executor.stop(identifier, spec)
    assert set(provider.objects) == {"endpoint", "endpoint_config", "model"}
    provider.pending_delete = False
    provider.objects.pop("endpoint")
    executor.stop(identifier, spec)
    assert not provider.objects


@pytest.mark.parametrize("kind", ["model", "endpoint_config", "endpoint"])
def test_uncertain_create_is_adopted_for_cleanup_never_replayed(
    hosted: tuple[SageMakerDeploymentExecutor, Hosting], kind: str
) -> None:
    executor, provider = hosted
    identifier, spec = uuid4(), specification()
    provider.ambiguous = kind
    with pytest.raises(ExecutorError):
        executor.deploy(identifier, spec)
    with pytest.raises(ExecutorError, match="already attempted"):
        executor.deploy(identifier, spec)
    executor.stop(identifier, spec)
    assert not provider.objects
    assert provider.creates.count(kind) == 1


def test_ambiguous_absence_never_claims_cleanup(
    hosted: tuple[SageMakerDeploymentExecutor, Hosting],
) -> None:
    executor, provider = hosted
    identifier, spec = uuid4(), specification()
    provider.ambiguous = "endpoint"
    with pytest.raises(ExecutorError):
        executor.deploy(identifier, spec)
    provider.objects.pop("endpoint")
    with pytest.raises(ExecutorError, match="Uncertain"):
        executor.stop(identifier, spec)
    assert not provider.deletes


@pytest.mark.parametrize("tamper", ["tags", "image", "config", "snapshot"])
def test_foreign_or_mutated_resources_are_not_deleted(
    hosted: tuple[SageMakerDeploymentExecutor, Hosting], tamper: str
) -> None:
    executor, provider = hosted
    identifier, spec = uuid4(), specification()
    executor.deploy(identifier, spec)
    if tamper == "tags":
        provider.objects["endpoint"]["Tags"] = []
    elif tamper == "image":
        provider.objects["model"]["PrimaryContainer"]["Image"] = "foreign:latest"
    elif tamper == "config":
        provider.objects["endpoint"]["EndpointConfigName"] = "foreign"
    else:
        spec = spec.model_copy(update={"adapter": False})
    with pytest.raises(ExecutorError, match="mismatch"):
        executor.status(identifier, spec)
    assert not provider.deletes


def test_preparation_and_precision_fail_before_allocation(
    hosted: tuple[SageMakerDeploymentExecutor, Hosting],
) -> None:
    executor, provider = hosted
    spec = specification().model_copy(update={"precision": "bf16"})
    with pytest.raises(ExecutorError, match="FP16"):
        executor.deploy(uuid4(), spec)
    executor.materializer.materialize.side_effect = ExecutorError("hash mismatch")  # type: ignore[attr-defined]
    with pytest.raises(ExecutorError, match="hash"):
        executor.deploy(uuid4(), specification())
    assert not provider.creates


@pytest.mark.parametrize(
    "field,value",
    [
        ("deployment_image", "image:latest"),
        ("deployment_image", "123456789012.dkr.ecr.eu-west-1.amazonaws.com/vllm@" + IMAGE),
        ("max_deployment_lease_minutes", 61),
    ],
)
def test_serving_config_is_bounded_and_backward_compatible(field: str, value: Any) -> None:
    c = configuration()
    assert c.deployment_image is None
    with pytest.raises(ValidationError):
        type(c).model_validate({**c.model_dump(by_alias=True), field: value})


def test_missing_image_rejected(hosted: tuple[SageMakerDeploymentExecutor, Hosting]) -> None:
    executor, _ = hosted
    executor.aws.configuration = configuration()
    with pytest.raises(ExecutorError, match="separate"):
        SageMakerDeploymentExecutor(executor.root, executor.materializer, executor.aws)


def test_hosting_rejects_oci_before_remote_staging_or_provisioning(
    hosted: tuple[SageMakerDeploymentExecutor, Hosting],
) -> None:
    executor, provider = hosted
    executor.aws.ecr.describe_images.return_value["imageDetails"][0]["imageManifestMediaType"] = (
        "application/vnd.oci.image.manifest.v1+json"
    )
    with pytest.raises(ExecutorError, match="Docker v2"):
        executor.deploy(uuid4(), specification())
    assert not provider.creates
    executor.aws.s3.upload_file.assert_not_called()


def test_missing_journal_never_implies_provider_absence(
    hosted: tuple[SageMakerDeploymentExecutor, Hosting],
) -> None:
    executor, provider = hosted
    identifier, spec = uuid4(), specification()
    assert executor.status(identifier, spec).state == "LOST"
    executor.stop(identifier, spec)
    executor.deploy(identifier, spec)
    (executor.workspace(identifier) / "ownership.json").unlink()
    with pytest.raises(ExecutorError, match="restore ownership"):
        executor.status(identifier, spec)
    with pytest.raises(ExecutorError, match="restore ownership"):
        executor.stop(identifier, spec)
    assert not provider.deletes and len(provider.objects) == 3


def test_definitive_rejection_cleans_partial_infrastructure(
    hosted: tuple[SageMakerDeploymentExecutor, Hosting],
) -> None:
    executor, provider = hosted
    identifier, spec = uuid4(), specification()
    provider.client.create_endpoint.side_effect = ClientError(
        {"Error": {"Code": "ResourceLimitExceeded", "Message": "quota"}}, "CreateEndpoint"
    )
    with pytest.raises(ExecutorError, match="creation failed"):
        executor.deploy(identifier, spec)
    executor.stop(identifier, spec)
    assert not provider.objects
    assert provider.deletes == ["endpoint_config", "model"]


def test_stop_or_expiry_during_staging_never_allocates(
    hosted: tuple[SageMakerDeploymentExecutor, Hosting],
) -> None:
    executor, provider = hosted
    identifier, spec = uuid4(), specification()
    executor.stop_requested = lambda _: True
    with pytest.raises(ExecutorError, match="stopped or expired"):
        executor.deploy(identifier, spec)
    executor.stop(identifier, spec)
    assert not provider.creates


def test_exact_multipart_cleanup_and_versioning_guard(
    hosted: tuple[SageMakerDeploymentExecutor, Hosting],
) -> None:
    executor, provider = hosted
    identifier, spec = uuid4(), specification()
    executor.deploy(identifier, spec)
    executor.aws.s3.get_bucket_versioning.return_value = {"Status": "Enabled"}
    with pytest.raises(ExecutorError, match="unversioned"):
        executor.stop(identifier, spec)
    assert not provider.objects
    executor.aws.s3.delete_object.assert_not_called()
    executor.aws.s3.get_bucket_versioning.return_value = {}
    executor.aws.s3.list_multipart_uploads.side_effect = [
        {"Uploads": [{"Key": executor.key(identifier), "UploadId": "part"}]},
        {},
    ]
    executor.stop(identifier, spec)
    executor.aws.s3.abort_multipart_upload.assert_called_once_with(
        Bucket=executor.configuration.staging_bucket, Key=executor.key(identifier), UploadId="part"
    )
