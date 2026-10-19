"""Verified artifact registration with isolated staging and explicit transactions."""

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import Artifact, ArtifactUpload, RawSourceDeletion
from app.db.session import unit_of_work
from app.storage.base import ArtifactStore, StorageError
from app.storage.hashing import hash_content
from app.storage.keys import artifact_key, safe_filename, upload_key


class UploadError(Exception):
    def __init__(self, status: int, message: str) -> None:
        self.status = status
        super().__init__(message)


@dataclass(frozen=True)
class UploadGrant:
    upload_id: UUID
    url: str
    headers: dict[str, str]
    expires_at: datetime
    complete_by: datetime


class ArtifactService:
    def __init__(
        self,
        store: ArtifactStore,
        factory: sessionmaker[Session],
        *,
        bucket: str,
        max_bytes: int = 1024**3,
        url_ttl: int = 300,
    ) -> None:
        self.store = store
        self.factory = factory
        self.bucket = bucket
        self.max_bytes = max_bytes
        self.url_ttl = url_ttl

    def initiate(self, filename: str, content_type: str, size_bytes: int) -> UploadGrant:
        if size_bytes > self.max_bytes:
            raise UploadError(413, "File exceeds the configured upload limit")
        now = datetime.now(UTC)
        identifier = uuid4()
        name = safe_filename(filename)
        key = upload_key(identifier, name)
        url = self.store.presign_put(
            key, self.url_ttl, content_type=content_type, size_bytes=size_bytes
        )
        complete_by = now + timedelta(hours=1)
        with unit_of_work(self.factory) as session:
            session.add(
                ArtifactUpload(
                    id=identifier,
                    object_key=key,
                    filename=name,
                    content_type=content_type,
                    size_bytes=size_bytes,
                    expires_at=complete_by,
                )
            )
            session.commit()
        return UploadGrant(
            identifier,
            url,
            {"Content-Type": content_type},
            now + timedelta(seconds=self.url_ttl),
            complete_by,
        )

    def complete(self, identifier: UUID) -> Artifact:
        with unit_of_work(self.factory) as session:
            upload = session.scalar(
                select(ArtifactUpload).where(ArtifactUpload.id == identifier).with_for_update()
            )
            if upload is None:
                raise UploadError(404, "Upload not found")
            if upload.artifact_id is not None:
                self.require_available(session, upload.artifact_id)
                artifact = session.get(Artifact, upload.artifact_id)
                assert artifact is not None
            else:
                if upload.expires_at <= datetime.now(UTC):
                    raise UploadError(410, "Upload completion deadline has passed")
                if self.store.stat(upload.object_key).size_bytes != upload.size_bytes:
                    raise UploadError(409, "Uploaded size does not match the declared size")
                with self.store.get(
                    upload.object_key, max_bytes=min(upload.size_bytes, self.max_bytes)
                ) as source:
                    digest, size = hash_content(source, max_bytes=self.max_bytes)
                    if size != upload.size_bytes:
                        raise UploadError(409, "Uploaded size does not match the declared size")
                    source.seek(0)
                    key = artifact_key(upload.id, upload.filename)
                    self.store.put(key, source)
                artifact = Artifact(
                    id=upload.id,
                    bucket=self.bucket,
                    object_key=key,
                    sha256=digest,
                    size_bytes=size,
                    format=upload.content_type,
                )
                session.add(artifact)
                session.flush()
                upload.artifact_id = artifact.id
                session.commit()
            staging_key = upload.object_key
            session.expunge(artifact)
        # A cleanup failure must not turn an already committed registration into a failed request.
        try:
            self.store.delete(staging_key)
        except StorageError:
            logging.warning("Staging cleanup deferred after artifact registration")
        return artifact

    def get(self, identifier: UUID) -> Artifact:
        with unit_of_work(self.factory) as session:
            artifact = session.get(Artifact, identifier)
            if artifact is None:
                raise UploadError(404, "Artifact not found")
            session.expunge(artifact)
            return artifact

    def download(self, identifier: UUID) -> str:
        with self.factory() as session:
            self.require_available(session, identifier)
            artifact = session.get(Artifact, identifier)
            if artifact is None:
                raise UploadError(404, "Artifact not found")
            if artifact.bucket != self.bucket:
                raise UploadError(409, "Artifact belongs to a different configured store")
            self.store.stat(artifact.object_key)
            return self.store.presign_get(artifact.object_key, self.url_ttl)

    @staticmethod
    def require_available(session: Session, identifier: UUID) -> None:
        # Serialize attachment/read preparation with an explicit deletion request.
        session.scalar(select(Artifact).where(Artifact.id == identifier).with_for_update())
        if session.get(RawSourceDeletion, identifier) is not None:
            raise UploadError(410, "This original was explicitly deleted or is pending deletion")

    def sweep_uploads(self, *, limit: int = 100) -> int:
        """Only expired reservation-owned keys; registered artifacts never expire."""
        if not 1 <= limit <= 1000:
            raise ValueError("Retention batch must contain 1 to 1000 reservations")
        cutoff = datetime.now(UTC) - timedelta(days=1)
        count = 0
        with self.factory() as session:
            identifiers = list(
                session.scalars(
                    select(ArtifactUpload.id)
                    .where(
                        ArtifactUpload.expires_at < cutoff,
                        ArtifactUpload.staging_cleaned_at.is_(None),
                    )
                    .order_by(ArtifactUpload.expires_at)
                    .limit(limit)
                )
            )
        for identifier in identifiers:
            with unit_of_work(self.factory) as session:
                upload = session.scalar(
                    select(ArtifactUpload).where(ArtifactUpload.id == identifier).with_for_update()
                )
                if (
                    upload is None
                    or upload.expires_at >= cutoff
                    or upload.staging_cleaned_at is not None
                ):
                    continue
                self.store.delete(upload.object_key)
                key = artifact_key(upload.id, upload.filename)
                registered = session.scalar(
                    select(Artifact.id).where(
                        Artifact.bucket == self.bucket, Artifact.object_key == key
                    )
                )
                if upload.artifact_id is None and registered is None:
                    self.store.delete(key)
                upload.staging_cleaned_at = datetime.now(UTC)
                count += 1
                session.commit()
        return count
