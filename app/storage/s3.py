"""S3 adapter; browser signing uses an independently configured public endpoint."""

from collections.abc import Iterator
from contextlib import contextmanager
from tempfile import SpooledTemporaryFile
from typing import TYPE_CHECKING, BinaryIO, cast

import boto3
from boto3.s3.transfer import TransferConfig
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from app.config import Settings
from app.storage.base import ArtifactStat, ObjectNotFound, ObjectTooLarge, StorageError

if TYPE_CHECKING:
    from mypy_boto3_s3 import S3Client


@contextmanager
def storage_errors() -> Iterator[None]:
    try:
        yield
    except ClientError as error:
        if error.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
            raise ObjectNotFound("Object not found") from None
        raise StorageError("Object storage operation failed") from None
    except (BotoCoreError, OSError):
        raise StorageError("Object storage operation failed") from None


class S3ArtifactStore:
    def __init__(self, settings: Settings) -> None:
        self.bucket = settings.s3_bucket
        self.region = settings.s3_region
        config = Config(
            signature_version="s3v4",
            s3={"addressing_style": "path"},
            connect_timeout=5,
            read_timeout=30,
            retries={"max_attempts": 2},
            request_checksum_calculation="when_required",
            response_checksum_validation="when_required",
        )

        def client(endpoint: str) -> "S3Client":
            return boto3.client(
                "s3",
                endpoint_url=endpoint,
                region_name=self.region,
                aws_access_key_id=settings.s3_access_key.get_secret_value(),
                aws_secret_access_key=settings.s3_secret_key.get_secret_value(),
                config=config,
            )

        self.client = client(settings.s3_endpoint)
        self.signer = client(settings.s3_public_endpoint)

    def close(self) -> None:
        self.client.close()
        self.signer.close()

    def put(self, key: str, source: BinaryIO) -> ArtifactStat:
        with storage_errors():
            self.client.upload_fileobj(
                source,
                self.bucket,
                key,
                Config=TransferConfig(
                    multipart_threshold=8 * 1024**2,
                    multipart_chunksize=8 * 1024**2,
                ),
            )
            return self.stat(key)

    def get(self, key: str, *, max_bytes: int | None = None) -> BinaryIO:
        output = SpooledTemporaryFile(max_size=8 * 1024**2, mode="w+b")
        try:
            with storage_errors():
                response = self.client.get_object(Bucket=self.bucket, Key=key)
                body = response["Body"]
                try:
                    if max_bytes is not None and response["ContentLength"] > max_bytes:
                        raise ObjectTooLarge("Object exceeds the upload limit")
                    size = 0
                    while chunk := body.read(1024 * 1024):
                        size += len(chunk)
                        if max_bytes is not None and size > max_bytes:
                            raise ObjectTooLarge("Object exceeds the upload limit")
                        output.write(chunk)
                finally:
                    body.close()
            output.seek(0)
            return cast(BinaryIO, output)
        except BaseException:
            output.close()
            raise

    def stat(self, key: str) -> ArtifactStat:
        with storage_errors():
            result = self.client.head_object(Bucket=self.bucket, Key=key)
            return ArtifactStat(key=key, size_bytes=result["ContentLength"])

    def delete(self, key: str) -> None:
        with storage_errors():
            self.client.delete_object(Bucket=self.bucket, Key=key)

    def presign_get(self, key: str, expires_in: int) -> str:
        self._validate_expiry(expires_in)
        with storage_errors():
            return self.signer.generate_presigned_url(
                "get_object",
                Params={
                    "Bucket": self.bucket,
                    "Key": key,
                    "ResponseContentDisposition": "attachment",
                    "ResponseContentType": "application/octet-stream",
                },
                ExpiresIn=expires_in,
            )

    def presign_put(self, key: str, expires_in: int, *, content_type: str, size_bytes: int) -> str:
        self._validate_expiry(expires_in)
        with storage_errors():
            return self.signer.generate_presigned_url(
                "put_object",
                Params={
                    "Bucket": self.bucket,
                    "Key": key,
                    "ContentType": content_type,
                    "ContentLength": size_bytes,
                },
                ExpiresIn=expires_in,
            )

    @staticmethod
    def _validate_expiry(expires_in: int) -> None:
        if not 1 <= expires_in <= 3600:
            raise ValueError("URL lifetime must be between 1 and 3600 seconds")

    def provision(self, origins: list[str]) -> None:
        """Explicit operator action: create the development bucket and set its CORS rules."""
        with storage_errors():
            try:
                self.client.head_bucket(Bucket=self.bucket)
            except ClientError as error:
                if error.response.get("Error", {}).get("Code") not in (
                    "404",
                    "NoSuchBucket",
                    "NotFound",
                ):
                    raise
                if self.region == "us-east-1":
                    self.client.create_bucket(Bucket=self.bucket)
                else:
                    self.client.create_bucket(
                        Bucket=self.bucket,
                        CreateBucketConfiguration={
                            "LocationConstraint": self.region,  # type: ignore[typeddict-item]
                        },
                    )
            self.client.put_bucket_cors(
                Bucket=self.bucket,
                CORSConfiguration={
                    "CORSRules": [
                        {
                            "AllowedOrigins": origins,
                            "AllowedMethods": ["GET", "PUT", "HEAD"],
                            "AllowedHeaders": ["content-type"],
                            "ExposeHeaders": ["ETag"],
                            "MaxAgeSeconds": 300,
                        }
                    ]
                },
            )
