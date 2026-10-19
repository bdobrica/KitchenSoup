"""Managed offline Soup training; AWS credentials stay in the control plane."""

import json
import re
import tarfile
import tempfile
import time
from pathlib import Path
from typing import Any, Self
from uuid import UUID, uuid4

import boto3
from boto3.exceptions import Boto3Error
from boto3.s3.transfer import TransferConfig
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from app.executors.base import ExecutorError, InputMaterializer, JobStatus
from app.executors.local_docker import LocalDockerTrainingExecutor, container_name
from app.targets.sagemaker_schemas import SageMakerConfig
from app.training.runner import file_record, read_bounded
from app.training.schemas import PlanPreview
from app.training.soup import RunnerError, RunnerInput, RunnerManifest


class InvalidOutput(ExecutorError):
    """Definitive output-contract violation, distinct from AWS unavailability."""


class BoundedTarInfo(tarfile.TarInfo):
    """Bound extended metadata before tarfile processes it or allocates its body."""

    @classmethod
    def frombuf(cls, buf: bytes | bytearray, encoding: str, errors: str) -> Self:
        info = super().frombuf(buf, encoding, errors)
        if (
            info.type
            in (
                tarfile.XHDTYPE,
                tarfile.XGLTYPE,
                tarfile.GNUTYPE_LONGNAME,
                tarfile.GNUTYPE_LONGLINK,
            )
            and info.size > 64 * 1024
        ):
            raise InvalidOutput("AWS archive metadata exceeds limits")
        if info.type == tarfile.GNUTYPE_SPARSE:
            raise InvalidOutput("Sparse AWS output is unsupported")
        return info


class SageMakerTrainingExecutor(LocalDockerTrainingExecutor):
    def __init__(
        self,
        root: Path,
        materializer: InputMaterializer,
        configuration: SageMakerConfig,
        credential_ref: str,
        approved_profiles: set[str],
    ) -> None:
        super().__init__(root, materializer)
        self.configuration = configuration
        if not re.fullmatch(r"aws-profile://[A-Za-z0-9_-][A-Za-z0-9_.-]{0,127}", credential_ref):
            raise ExecutorError("Use an operator-approved aws-profile://profile reference")
        profile = credential_ref.removeprefix("aws-profile://")
        if profile not in approved_profiles:
            raise ExecutorError("AWS profile is not approved by the operator")
        try:
            # 'default' deliberately uses the standard environment/instance-role chain.
            session = boto3.Session(
                profile_name=None if profile == "default" else profile,
                region_name=configuration.region,
            )
            options = Config(connect_timeout=10, read_timeout=60, retries={"total_max_attempts": 1})
            self.session = session
            self.sm: Any = session.client("sagemaker", config=options)
            self.s3: Any = session.client("s3", config=options)
            self.ecr: Any = session.client("ecr", config=options)
        except (BotoCoreError, ValueError):
            raise ExecutorError(
                "AWS credentials are unavailable for the approved profile"
            ) from None

    @staticmethod
    def call(client: Any, method: str, **kwargs: Any) -> Any:
        try:
            return getattr(client, method)(**kwargs)
        except (BotoCoreError, ClientError, Boto3Error):
            raise ExecutorError(
                "AWS request failed; verify permissions, region and connectivity"
            ) from None

    def image_identity(self) -> str:
        return self.configuration.training_image.rsplit("@", 1)[1]

    def prefix(self, name: str) -> str:
        self.workspace(name)  # Strict run UUID validation before remote effects.
        return self.configuration.staging_prefix + "/" + name + "/"

    def preflight(self, image: str | None = None) -> str:
        c = self.configuration
        selected_image = image or c.training_image
        digest = selected_image.rsplit("@", 1)[1]
        versioning = self.call(self.s3, "get_bucket_versioning", Bucket=c.staging_bucket)
        if versioning.get("Status"):
            raise ExecutorError("SageMaker staging bucket must have versioning disabled")
        location = self.call(self.s3, "get_bucket_location", Bucket=c.staging_bucket)
        if (location.get("LocationConstraint") or "us-east-1") != c.region:
            raise ExecutorError("SageMaker staging bucket and job must use the same region")
        registry, repository = selected_image.split("/", 1)
        images = self.call(
            self.ecr,
            "describe_images",
            registryId=registry.split(".")[0],
            repositoryName=repository.split("@", 1)[0],
            imageIds=[{"imageDigest": digest}],
        ).get("imageDetails", [])
        if len(images) != 1 or images[0].get("imageDigest") != digest:
            raise ExecutorError("Pinned training image is unavailable in ECR")
        if images[0].get("imageManifestMediaType") not in (
            "application/vnd.docker.distribution.manifest.v2+json",
            "application/vnd.oci.image.manifest.v1+json",
        ):
            raise ExecutorError("Pin a single linux/amd64 image manifest, not a manifest index")
        return str(images[0]["imageManifestMediaType"])

    def submit(self, run_id: UUID, plan: PlanPreview, image: str) -> str:
        if image != self.image_identity():
            raise ExecutorError("Retained image does not match the immutable AWS target")
        name, c = container_name(run_id), self.configuration
        with self.lock(name) as workspace:
            if workspace.exists():
                raise ExecutorError("Run workspace already exists; clone the run")
            workspace.mkdir(mode=0o700)
            inputs, output = workspace / "input", workspace / "output"
            inputs.mkdir(mode=0o700)
            output.mkdir(mode=0o700)
            try:
                self.materializer.materialize(run_id, plan, inputs)
                run = RunnerInput.model_validate_json(
                    read_bounded(inputs / "run.json", 8 * 1024**2)
                )
            except ExecutorError:
                raise
            except Exception:
                raise ExecutorError(
                    "AWS input preparation failed; verify registered inputs and disk"
                ) from None
            if run.run_id != run_id or run.plan != plan:
                raise ExecutorError("Materialized bundle does not match the retained run")
            self.preflight()
            launch = {
                "image": image,
                "input_sha256": file_record(inputs, inputs / "run.json").sha256,
                "plan_sha256": plan.sha256,
                "nonce": uuid4().hex,
            }
            (workspace / "launch.json").write_text(json.dumps(launch))
            prefix = self.prefix(name)
            # One native File-mode channel. No archive or URL is exposed to the runner.
            for path in sorted(inputs.rglob("*")):
                if path.is_file():
                    file_record(inputs, path)
                    with path.open("rb") as source:
                        self.call(
                            self.s3,
                            "upload_fileobj",
                            Fileobj=source,
                            Bucket=c.staging_bucket,
                            Key=prefix + "input/" + path.relative_to(inputs).as_posix(),
                            ExtraArgs={"ServerSideEncryption": "AES256"},
                            Config=TransferConfig(
                                multipart_threshold=8 * 1024**2,
                                multipart_chunksize=64 * 1024**2,
                                use_threads=False,
                            ),
                        )
            request = dict(
                TrainingJobName=name,
                RoleArn=c.role_arn,
                AlgorithmSpecification={
                    "TrainingImage": c.training_image,
                    "TrainingInputMode": "File",
                    "ContainerEntrypoint": ["python", "/runner/sagemaker_entrypoint.py"],
                    "ContainerArguments": [
                        "--image-digest",
                        image,
                        "--timeout",
                        str(c.max_runtime_seconds),
                    ],
                },
                InputDataConfig=[
                    {
                        "ChannelName": "bundle",
                        "DataSource": {
                            "S3DataSource": {
                                "S3DataType": "S3Prefix",
                                "S3Uri": f"s3://{c.staging_bucket}/{prefix}input/",
                                "S3DataDistributionType": "FullyReplicated",
                            }
                        },
                    }
                ],
                OutputDataConfig={"S3OutputPath": f"s3://{c.staging_bucket}/{prefix}output/"},
                ResourceConfig={
                    "InstanceType": c.instance_type,
                    "InstanceCount": 1,
                    "VolumeSizeInGB": c.volume_gib,
                },
                StoppingCondition={"MaxRuntimeInSeconds": c.max_runtime_seconds},
                EnableNetworkIsolation=True,
                EnableManagedSpotTraining=False,
                Environment={"KITCHENSOUP_LAUNCH_NONCE": launch["nonce"]},
                Tags=[{"Key": "kitchensoup-run", "Value": str(run_id)}],
            )
            (workspace / "request.json").write_text(json.dumps(request))
            (workspace / "create-intent").touch()
            # Uncertain requests are recovered through DescribeTrainingJob only;
            # this deterministic job name is never submitted again.
            self.call(self.sm, "create_training_job", **request)
        return name

    def describe(self, name: str, image: str) -> dict[str, Any] | None:
        workspace = self.workspace(name)
        if not (workspace / "launch.json").exists():
            return None
        if not (workspace / "create-intent").exists():
            return None
        try:
            launch = json.loads(read_bounded(workspace / "launch.json", 4096))
            expected = json.loads(read_bounded(workspace / "request.json", 64 * 1024))
        except (OSError, ValueError, RunnerError):
            raise ExecutorError("Retained AWS launch identity is unavailable") from None
        c, prefix = self.configuration, self.prefix(name)
        try:
            info = self.sm.describe_training_job(TrainingJobName=name)
        except ClientError as exc:
            error = exc.response["Error"]
            if error["Code"] == "ResourceNotFound" or (
                error["Code"] == "ValidationException"
                and error.get("Message") == "Requested resource not found."
            ):
                return None
            # SageMaker also uses ValidationException for absent names; never infer
            # absence from that ambiguous code or arbitrary provider error text.
            raise ExecutorError("AWS training status is unavailable") from None
        except BotoCoreError:
            raise ExecutorError("AWS training status is unavailable") from None
        if (
            launch["image"] != image
            or image != self.image_identity()
            or info.get("TrainingJobName") != name
            or info.get("AlgorithmSpecification", {}).get("TrainingImage") != c.training_image
            or info.get("Environment", {}).get("KITCHENSOUP_LAUNCH_NONCE") != launch["nonce"]
            or info.get("RoleArn") != c.role_arn
            or info.get("EnableNetworkIsolation") is not True
            or info.get("OutputDataConfig", {}).get("S3OutputPath")
            != f"s3://{c.staging_bucket}/{prefix}output/"
        ):
            raise ExecutorError("AWS job identity does not match this retained run")
        for section in ("AlgorithmSpecification", "ResourceConfig", "StoppingCondition"):
            if any(info.get(section, {}).get(k) != v for k, v in expected[section].items()):
                raise ExecutorError("AWS job configuration does not match the retained launch")
        channels = info.get("InputDataConfig", [])
        channel = expected["InputDataConfig"][0]
        if (
            len(channels) != 1
            or channels[0].get("ChannelName") != "bundle"
            or channels[0].get("DataSource", {}).get("S3DataSource")
            != channel["DataSource"]["S3DataSource"]
        ):
            raise ExecutorError("AWS input channels do not match the retained launch")
        return dict(info)

    def collect(self, name: str, info: dict[str, Any]) -> None:
        root = self.workspace(name) / "output"
        if (root / "output-manifest.json").exists():
            return
        key = self.prefix(name) + "output/" + name + "/output/model.tar.gz"
        uri = f"s3://{self.configuration.staging_bucket}/{key}"
        if info.get("ModelArtifacts", {}).get("S3ModelArtifacts") != uri:
            raise ExecutorError("Unexpected SageMaker output location")
        response = self.call(
            self.s3, "get_object", Bucket=self.configuration.staging_bucket, Key=key
        )
        body = response["Body"]
        deadline = time.monotonic() + 300
        try:
            with tempfile.TemporaryFile() as compressed:
                count = 0
                while chunk := body.read(1024**2):
                    count += len(chunk)
                    if count > 9 * 1024**3:
                        raise InvalidOutput("AWS output archive exceeds limits")
                    if time.monotonic() > deadline:
                        raise ExecutorError("AWS output transfer timed out")
                    compressed.write(chunk)
                compressed.seek(0)
                # Stream tar entries: no extractall, links, devices or arbitrary paths.
                # Manifest is written last, so partial transfers remain retryable.
                manifest_bytes: bytes | None = None
                seen: set[str] = set()
                total = 0
                with tarfile.open(
                    fileobj=compressed, mode="r|gz", tarinfo=BoundedTarInfo
                ) as archive:
                    for item in archive:
                        path = item.name.removeprefix("./")
                        if time.monotonic() > deadline:
                            raise ExecutorError("AWS output transfer exceeded its time limit")
                        if item.isdir() and path in (".", "adapter") and item.size == 0:
                            continue
                        if (
                            not item.isfile()
                            or item.sparse is not None
                            or path in seen
                            or len(seen) >= 4100
                            or Path(path).is_absolute()
                            or ".." in Path(path).parts
                            or str(Path(path)) != path
                            or len(path) > 512
                            or "\\" in path
                            or not (
                                path in ("output-manifest.json", "stdout.log", "stderr.log")
                                or path.startswith("adapter/")
                            )
                        ):
                            raise InvalidOutput("Unsafe AWS output archive")
                        seen.add(path)
                        total += item.size
                        limit = 1024**3 if path.startswith("adapter/") else 8 * 1024**2
                        if item.size > limit or total > 8 * 1024**3 + 24 * 1024**2:
                            raise InvalidOutput("AWS output expansion exceeds limits")
                        reader = archive.extractfile(item)
                        if reader is None:
                            raise InvalidOutput("AWS output file is unavailable")
                        with reader:
                            if path == "output-manifest.json":
                                manifest_bytes = reader.read(8 * 1024**2 + 1)
                                if len(manifest_bytes) > 8 * 1024**2:
                                    raise InvalidOutput("AWS manifest exceeds limits")
                            else:
                                destination = root / path
                                destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                                with destination.open("wb") as target:
                                    while chunk := reader.read(1024**2):
                                        if time.monotonic() > deadline:
                                            raise ExecutorError("AWS output transfer timed out")
                                        target.write(chunk)
                if manifest_bytes is None:
                    raise InvalidOutput("AWS output has no runner manifest")
                manifest = RunnerManifest.model_validate_json(manifest_bytes)
                paths = {item.path for item in manifest.adapters}
                if (
                    len(paths) != len(manifest.adapters)
                    or len(paths) > 4096
                    or sum(item.size_bytes for item in manifest.adapters) > 8 * 1024**3
                    or seen - {"output-manifest.json", "stdout.log", "stderr.log"} != paths
                    or any(not path.startswith("adapter/") for path in paths)
                ):
                    raise InvalidOutput("AWS adapter inventory does not match the archive")
                (root / "output-manifest.json").write_bytes(manifest_bytes)
        except (ValueError, tarfile.TarError, RecursionError):
            raise InvalidOutput("AWS output violates the runner contract") from None
        except (OSError, BotoCoreError):
            raise ExecutorError("AWS output could not be read safely") from None
        finally:
            body.close()

    def inspect(self, external_id: str, image: str) -> dict[str, Any] | None:
        info = self.describe(external_id, image)
        if info is None:
            return None
        state = info["TrainingJobStatus"]
        if state in ("InProgress", "Stopping"):
            return {"Running": True}
        if state == "Completed":
            try:
                self.collect(external_id, info)
            except InvalidOutput:
                return {"ExitCode": 1}
            return {"ExitCode": 0}
        if state == "Stopped":
            (self.workspace(external_id) / "canceled").touch()
        if state not in ("Stopped", "Failed"):
            raise ExecutorError("Unknown SageMaker training state")
        return {"ExitCode": 1}

    def cancel(self, external_id: str, image: str) -> JobStatus:
        with self.lock(external_id):
            info = self.describe(external_id, image)
            if info and info["TrainingJobStatus"] == "InProgress":
                self.call(self.sm, "stop_training_job", TrainingJobName=external_id)
            return self.status(external_id, image)

    def cleanup(self, external_id: str, image: str) -> None:
        with self.lock(external_id) as workspace:
            if not (workspace / "launch.json").exists():
                return  # Preparation failed before any S3 or SageMaker effects.
            launch = json.loads(read_bounded(workspace / "launch.json", 4096))
            if launch["image"] != image or image != self.image_identity():
                raise ExecutorError("Retained AWS cleanup identity does not match")
            info = self.describe(external_id, image)
            if info and info["TrainingJobStatus"] in ("InProgress", "Stopping"):
                raise ExecutorError("Wait for SageMaker to stop before cleanup")
            if info and info["TrainingJobStatus"] == "Completed":
                if self.status(external_id, image).state != "SUCCEEDED":
                    raise ExecutorError("Preserve unverified output for operator investigation")
            c, prefix = self.configuration, self.prefix(external_id)
            # Exact run prefix only; fail closed on versioned buckets (delete markers
            # would leave billable versions). The operator provisions an unversioned bucket.
            versioning = self.call(self.s3, "get_bucket_versioning", Bucket=c.staging_bucket)
            if versioning.get("Status"):
                raise ExecutorError("SageMaker staging bucket must have versioning disabled")
            # A process crash can strand multipart-upload parts before an object
            # becomes listable. They are billable and owned by this exact prefix.
            for _ in range(20):
                uploads = self.call(
                    self.s3,
                    "list_multipart_uploads",
                    Bucket=c.staging_bucket,
                    Prefix=prefix,
                    MaxUploads=1000,
                ).get("Uploads", [])
                if not uploads:
                    break
                for upload in uploads:
                    if not upload["Key"].startswith(prefix):
                        raise ExecutorError("Unexpected AWS multipart cleanup key")
                    self.call(
                        self.s3,
                        "abort_multipart_upload",
                        Bucket=c.staging_bucket,
                        Key=upload["Key"],
                        UploadId=upload["UploadId"],
                    )
            else:
                raise ExecutorError("AWS multipart cleanup exceeded limits; retry cleanup")
            for _ in range(20):
                objects = self.call(
                    self.s3, "list_objects_v2", Bucket=c.staging_bucket, Prefix=prefix, MaxKeys=1000
                )
                keys = [obj["Key"] for obj in objects.get("Contents", [])]
                if not keys:
                    break
                for key in keys:
                    if not key.startswith(prefix):
                        raise ExecutorError("Unexpected AWS cleanup key")
                    self.call(self.s3, "delete_object", Bucket=c.staging_bucket, Key=key)
            else:
                raise ExecutorError("AWS staging cleanup exceeded its batch limit; retry cleanup")
            (workspace / "remote-cleaned").touch()
