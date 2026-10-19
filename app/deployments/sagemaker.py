"""Network-isolated BYOC endpoints; immutable identity and conservative cleanup."""

import hashlib
import json
import os
import shutil
import tarfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from boto3.s3.transfer import TransferConfig
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from app.deployments.base import DeploymentStatus
from app.deployments.local import LocalDockerDeploymentExecutor, container_name
from app.deployments.materialize import DeploymentMaterializer
from app.deployments.schemas import ChatRequest, ChatResponse, DeploymentSpec
from app.executors.base import ExecutorError
from app.executors.sagemaker import SageMakerTrainingExecutor
from app.training.runner import read_bounded


def matches(expected: Any, actual: Any) -> bool:
    """AWS descriptions add defaults; require every requested immutable field."""
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(
            matches(v, actual.get(k)) for k, v in expected.items()
        )
    if isinstance(expected, list):
        return (
            isinstance(actual, list)
            and len(actual) == len(expected)
            and all(matches(a, b) for a, b in zip(expected, actual, strict=True))
        )
    return bool(expected == actual)


def missing_resource(exc: ClientError) -> bool:
    error = exc.response["Error"]
    message = str(error.get("Message", "")).lower()
    return error["Code"] == "ValidationException" and (
        "could not find" in message or "does not exist" in message
    )


class SageMakerDeploymentExecutor(LocalDockerDeploymentExecutor):
    def __init__(
        self,
        root: Path,
        materializer: DeploymentMaterializer,
        aws: SageMakerTrainingExecutor,
        runtime: Any = None,
        stop_requested: Callable[[UUID], bool] | None = None,
    ) -> None:
        if not aws.configuration.deployment_image:
            raise ExecutorError("Configure a separate pinned SageMaker serving image")
        super().__init__(root, materializer, image=aws.configuration.deployment_image)
        self.aws = aws
        self.runtime: Any = (
            runtime
            if runtime is not None
            else aws.session.client(
                "sagemaker-runtime",
                config=Config(
                    connect_timeout=10, read_timeout=70, retries={"total_max_attempts": 1}
                ),
            )
        )
        self.configuration = aws.configuration
        self.stop_requested = stop_requested or (lambda _: False)

    def image_identity(self) -> str:
        return self.image.rsplit("@", 1)[1]

    def validate(self, spec: DeploymentSpec) -> None:
        if spec.image_digest != self.image_identity():
            raise ExecutorError("Serving image differs from immutable target")
        if spec.precision == "bf16" and self.configuration.instance_type == "ml.g4dn.xlarge":
            raise ExecutorError("T4 serving requires FP16")
        if spec.representation == "awq" and spec.precision != "fp16":
            raise ExecutorError("AWQ serving requires FP16")

    def key(self, identifier: UUID) -> str:
        return (
            self.configuration.staging_prefix + "/deployments/" + str(identifier) + "/model.tar.gz"
        )

    def journal(self, identifier: UUID, spec: DeploymentSpec) -> dict[str, Any] | None:
        self.validate(spec)
        path = self.workspace(identifier) / "ownership.json"
        if not path.exists():
            return None
        data: dict[str, Any] = json.loads(read_bounded(path, 1024**2))
        if data["spec"] != spec.model_dump(mode="json", by_alias=True):
            raise ExecutorError("Serving ownership snapshot mismatch")
        return data

    def save(self, identifier: UUID, data: dict[str, Any]) -> None:
        path = self.workspace(identifier) / "ownership.json"
        temporary = path.with_suffix(".tmp")
        with temporary.open("w") as stream:
            json.dump(data, stream)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
        descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def deploy(self, identifier: UUID, spec: DeploymentSpec) -> None:
        self.validate(spec)
        path = self.workspace(identifier)
        if path.exists():
            raise ExecutorError("Preparation already attempted; reconcile or relaunch")
        path.mkdir(mode=0o700)
        c, name = self.configuration, container_name(identifier)
        # Resolve/verify bytes before allocating any endpoint.
        self.materializer.materialize(spec, path)
        config = json.loads(read_bounded(path / "model/config.json", 1024**2))
        model_len = config.get("max_position_embeddings", spec.max_model_len)
        if type(model_len) is not int or model_len < 256:
            raise ExecutorError("Unsupported model context")
        weights = sum(p.stat().st_size for p in path.rglob("*.safetensors"))
        vram = 15 if c.instance_type == "ml.g4dn.xlarge" else 22
        if weights * 1.2 + 2 * 1024**3 > vram * 1024**3:
            raise ExecutorError("Model exceeds conservative serving VRAM budget")
        settings = {
            "adapter": spec.adapter,
            "precision": spec.precision,
            "max_model_len": min(model_len, spec.max_model_len),
        }
        (path / "serving.json").write_text(json.dumps(settings))
        files = [
            p
            for directory in (path / "model", path / "adapter")
            if directory.exists()
            for p in directory.rglob("*")
            if p.is_file()
        ]
        files.append(path / "serving.json")
        if sum(p.stat().st_size for p in files) > 9 * 1024**3:
            raise ExecutorError("Serving transfer exceeds 9 GiB")
        manifest = {
            p.relative_to(path).as_posix(): hashlib.file_digest(p.open("rb"), "sha256").hexdigest()
            for p in files
        }
        (path / "manifest.json").write_text(json.dumps(manifest))
        archive = path / "model.tar.gz"
        with tarfile.open(archive, "w:gz") as tar:
            for p in [*files, path / "manifest.json"]:
                info = tar.gettarinfo(str(p), arcname=p.relative_to(path).as_posix())
                info.uid = info.gid = 10001
                info.uname = info.gname = "runner"
                info.mode = 0o444
                with p.open("rb") as stream:
                    tar.addfile(info, stream)
        if self.aws.preflight(self.image) != "application/vnd.docker.distribution.manifest.v2+json":
            raise ExecutorError(
                "SageMaker hosting requires a Docker v2 manifest; OCI is unsupported"
            )
        nonce = uuid4().hex
        tags = [
            {"Key": "kitchensoup.deployment", "Value": str(identifier)},
            {"Key": "kitchensoup.nonce", "Value": nonce},
        ]
        model = {
            "ModelName": name,
            "ExecutionRoleArn": c.role_arn,
            "EnableNetworkIsolation": True,
            "PrimaryContainer": {
                "Image": self.image,
                "ModelDataUrl": f"s3://{c.staging_bucket}/{self.key(identifier)}",
                "Environment": {
                    "KITCHENSOUP_MANIFEST_SHA256": hashlib.sha256(
                        (path / "manifest.json").read_bytes()
                    ).hexdigest()
                },
            },
            "Tags": tags,
        }
        variant = {
            "VariantName": "AllTraffic",
            "ModelName": name,
            "InitialInstanceCount": 1,
            "InstanceType": c.instance_type,
            "InitialVariantWeight": 1.0,
            "ModelDataDownloadTimeoutInSeconds": 600,
            "ContainerStartupHealthCheckTimeoutInSeconds": 600,
        }
        requests: dict[str, dict[str, Any]] = {
            "model": model,
            "endpoint_config": {
                "EndpointConfigName": name,
                "ProductionVariants": [variant],
                "Tags": tags,
            },
            "endpoint": {"EndpointName": name, "EndpointConfigName": name, "Tags": tags},
        }
        data: dict[str, Any] = {
            "spec": spec.model_dump(mode="json", by_alias=True),
            "requests": requests,
            "intents": [],
            "acknowledged": [],
            "observed": [],
            "rejected": [],
            "deleted": [],
            "arns": {},
            "tags": tags,
        }
        self.save(identifier, data)
        if self.stop_requested(identifier):
            raise ExecutorError("Serving stopped or expired before staging")
        self.aws.call(
            self.aws.s3,
            "upload_file",
            Filename=str(archive),
            Bucket=c.staging_bucket,
            Key=self.key(identifier),
            ExtraArgs={"ServerSideEncryption": "AES256"},
            Config=TransferConfig(
                multipart_threshold=8 * 1024**2,
                multipart_chunksize=64 * 1024**2,
                max_concurrency=1,
                use_threads=False,
            ),
        )
        for kind, request in requests.items():
            if self.stop_requested(identifier):
                raise ExecutorError("Serving stopped or expired during preparation")
            if kind == "endpoint":
                # AWS recommends a visible EndpointConfig before CreateEndpoint.
                for attempt in range(10):
                    try:
                        if self.describe(identifier, data, "endpoint_config") is not None:
                            break
                    except ExecutorError:
                        if attempt == 9:
                            raise
                    time.sleep(1)
                else:
                    raise ExecutorError("AWS endpoint configuration is not yet visible")
            data["intents"].append(kind)
            self.save(identifier, data)
            try:
                result = getattr(self.aws.sm, "create_" + kind)(**request)
            except ClientError as exc:
                if exc.response["Error"]["Code"] in (
                    "ValidationException",
                    "ResourceLimitExceeded",
                    "AccessDeniedException",
                    "AccessDenied",
                ):
                    data["rejected"].append(kind)
                    self.save(identifier, data)
                raise ExecutorError(
                    "AWS serving creation failed; reconcile before relaunch"
                ) from None
            except BotoCoreError:
                raise ExecutorError(
                    "AWS serving creation uncertain; reconcile before relaunch"
                ) from None
            data["arns"][kind] = result
            data["acknowledged"].append(kind)
            self.save(identifier, data)

    def describe(self, identifier: UUID, data: dict[str, Any], kind: str) -> dict[str, Any] | None:
        field = {
            "model": "ModelName",
            "endpoint_config": "EndpointConfigName",
            "endpoint": "EndpointName",
        }[kind]
        try:
            info: dict[str, Any] = getattr(self.aws.sm, "describe_" + kind)(
                **{field: container_name(identifier)}
            )
        except ClientError as exc:
            # AWS uses ValidationException for documented missing hosting resources.
            if missing_resource(exc):
                if (
                    kind in data["intents"]
                    and kind not in data["observed"]
                    and kind not in data["rejected"]
                ):
                    raise ExecutorError(
                        "Uncertain AWS creation remains pending; do not replay"
                    ) from None
                return None
            raise ExecutorError("AWS serving observation unavailable") from None
        except BotoCoreError:
            raise ExecutorError("AWS serving observation unavailable") from None
        arn_field = field.removesuffix("Name") + "Arn"
        account = self.configuration.role_arn.split(":")[4]
        expected_arn = (
            f"arn:aws:sagemaker:{self.configuration.region}:{account}:"
            f"{kind.replace('_', '-')}/{container_name(identifier)}"
        )
        if info.get(arn_field) != expected_arn:
            raise ExecutorError("AWS serving resource ARN mismatch")
        tags = self.aws.call(self.aws.sm, "list_tags", ResourceArn=info[arn_field]).get("Tags", [])
        if any(tag not in tags for tag in data["tags"]):
            raise ExecutorError("AWS serving ownership mismatch")
        expected = data["requests"][kind]
        for key in expected.keys() - {"Tags"}:
            if not matches(expected[key], info.get(key)):
                raise ExecutorError("AWS serving resource configuration mismatch")
        if kind not in data["observed"]:
            data["observed"].append(kind)
            if kind not in data["acknowledged"]:
                data["acknowledged"].append(kind)
            self.save(identifier, data)
        return info

    def confirm_absent_without_journal(self, identifier: UUID) -> None:
        # A lost local record is not evidence that billable resources are absent.
        for kind, field in (
            ("endpoint", "EndpointName"),
            ("endpoint_config", "EndpointConfigName"),
            ("model", "ModelName"),
        ):
            try:
                getattr(self.aws.sm, "describe_" + kind)(**{field: container_name(identifier)})
            except ClientError as exc:
                if missing_resource(exc):
                    continue
                raise ExecutorError("Cannot verify AWS absence without ownership journal") from None
            except BotoCoreError:
                raise ExecutorError("Cannot verify AWS absence without ownership journal") from None
            raise ExecutorError("AWS resources remain; restore ownership journal before cleanup")

    def status(self, identifier: UUID, spec: DeploymentSpec) -> DeploymentStatus:
        data = self.journal(identifier, spec)
        if data is None:
            self.confirm_absent_without_journal(identifier)
            return DeploymentStatus("LOST")
        if "endpoint" not in data["intents"]:
            return DeploymentStatus("LOST")
        info = self.describe(identifier, data, "endpoint")
        if info is None:
            return DeploymentStatus("LOST")
        if (
            self.describe(identifier, data, "endpoint_config") is None
            or self.describe(identifier, data, "model") is None
        ):
            return DeploymentStatus("FAILED")
        state = info["EndpointStatus"]
        if state == "InService":
            return DeploymentStatus("RUNNING", info["EndpointArn"])
        if state in ("Creating", "SystemUpdating"):
            return DeploymentStatus("STARTING")
        return DeploymentStatus("FAILED")

    def stop(self, identifier: UUID, spec: DeploymentSpec) -> None:
        data = self.journal(identifier, spec)
        if data is None:
            self.confirm_absent_without_journal(identifier)
            path = self.workspace(identifier)
            if path.exists():
                shutil.rmtree(path)
            return
        for kind in ("endpoint", "endpoint_config", "model"):
            if kind not in data["intents"] or kind in data["deleted"]:
                continue
            info = self.describe(identifier, data, kind)
            if info is not None:
                if kind != "endpoint" or info["EndpointStatus"] != "Deleting":
                    field = {
                        "endpoint": "EndpointName",
                        "endpoint_config": "EndpointConfigName",
                        "model": "ModelName",
                    }[kind]
                    self.aws.call(
                        self.aws.sm, "delete_" + kind, **{field: container_name(identifier)}
                    )
                # Deletion is asynchronous; only confirmed absence releases ownership.
                if self.describe(identifier, data, kind) is not None:
                    raise ExecutorError("AWS serving deletion pending")
            data["deleted"].append(kind)
            self.save(identifier, data)
        if self.aws.call(
            self.aws.s3, "get_bucket_versioning", Bucket=self.configuration.staging_bucket
        ).get("Status"):
            raise ExecutorError("Serving cleanup requires an unversioned staging bucket")
        for _ in range(20):
            uploads = self.aws.call(
                self.aws.s3,
                "list_multipart_uploads",
                Bucket=self.configuration.staging_bucket,
                Prefix=self.key(identifier),
                MaxUploads=1000,
            ).get("Uploads", [])
            if not uploads:
                break
            for upload in uploads:
                if upload["Key"] != self.key(identifier):
                    raise ExecutorError("Unexpected serving multipart cleanup key")
                self.aws.call(
                    self.aws.s3,
                    "abort_multipart_upload",
                    Bucket=self.configuration.staging_bucket,
                    Key=upload["Key"],
                    UploadId=upload["UploadId"],
                )
        else:
            raise ExecutorError("Serving multipart cleanup pending")
        self.aws.call(
            self.aws.s3,
            "delete_object",
            Bucket=self.configuration.staging_bucket,
            Key=self.key(identifier),
        )
        # Keep the small ownership journal for safe repeated stops/restart audit.
        for folder in ("model", "adapter"):
            shutil.rmtree(self.workspace(identifier) / folder, ignore_errors=True)
        (self.workspace(identifier) / "model.tar.gz").unlink(missing_ok=True)

    def chat(self, identifier: UUID, spec: DeploymentSpec, body: ChatRequest) -> ChatResponse:
        if self.status(identifier, spec).state != "RUNNING":
            raise ExecutorError("SageMaker playground is not ready")
        payload = {
            "model": "tuned" if spec.adapter else "playground",
            "messages": [m.model_dump() for m in body.messages],
            "max_tokens": 128,
            "temperature": 0,
            "stream": False,
        }
        result = self.aws.call(
            self.runtime,
            "invoke_endpoint",
            EndpointName=container_name(identifier),
            ContentType="application/json",
            Accept="application/json",
            Body=json.dumps(payload, ensure_ascii=False).encode(),
        )
        stream = result["Body"]
        try:
            raw = stream.read(256 * 1024 + 1)
            if len(raw) > 256 * 1024 or result.get("ContentType") != "application/json":
                raise ValueError
            content = json.loads(raw)["choices"][0]["message"]["content"]
            if not isinstance(content, str):
                raise ValueError
            return ChatResponse(content=content)
        except (ValueError, KeyError, IndexError, TypeError, BotoCoreError):
            raise ExecutorError("SageMaker inference response unavailable") from None
        finally:
            stream.close()
