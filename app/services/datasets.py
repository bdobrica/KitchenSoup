"""Dataset source admission and removal with explicit metadata transactions."""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import (
    Artifact,
    ArtifactUpload,
    Base,
    ConversationImport,
    Dataset,
    DatasetSource,
    DatasetVersion,
    Document,
    DocumentIngestion,
    RawSourceDeletion,
)
from app.db.session import unit_of_work
from app.ingestion.schemas import (
    DatasetCreate,
    DatasetSourceView,
    DatasetView,
    DocumentView,
    RawDeletionView,
    SourceAttach,
    SourceRemoved,
)
from app.ingestion.sources import SourceError, inspect_source, source_type
from app.services.artifacts import ArtifactService
from app.storage.hashing import hash_content


class DatasetService:
    def __init__(self, factory: sessionmaker[Session], artifacts: ArtifactService) -> None:
        self.factory = factory
        self.artifacts = artifacts

    def create(self, request: DatasetCreate) -> DatasetView:
        with unit_of_work(self.factory) as session:
            dataset = Dataset(**request.model_dump())
            session.add(dataset)
            session.flush()
            result = self._view(session, dataset)
            session.commit()
            return result

    def list(self) -> list[DatasetView]:
        with unit_of_work(self.factory) as session:
            return [
                self._view(session, row)
                for row in session.scalars(select(Dataset).order_by(Dataset.created_at, Dataset.id))
            ]

    def get(self, identifier: UUID) -> DatasetView:
        with unit_of_work(self.factory) as session:
            return self._view(session, self._dataset(session, identifier))

    @staticmethod
    def _dataset(session: Session, identifier: UUID, *, lock: bool = False) -> Dataset:
        query = select(Dataset).where(Dataset.id == identifier)
        dataset = session.scalar(query.with_for_update() if lock else query)
        if dataset is None:
            raise SourceError(404, "Dataset not found")
        return dataset

    @staticmethod
    def _source_view(session: Session, source: DatasetSource) -> DatasetSourceView:
        artifact = session.get(Artifact, source.artifact_id)
        assert artifact is not None
        documents = [
            DocumentView.model_validate(row)
            for row in session.scalars(
                select(Document).where(Document.source_id == source.id).order_by(Document.id)
            )
        ]
        return DatasetSourceView(
            id=source.id,
            artifact_id=artifact.id,
            filename=source.filename,
            kind=source.kind,
            license=source.license,
            sha256=artifact.sha256,
            size_bytes=artifact.size_bytes,
            created_at=source.created_at,
            documents=documents,
            raw_available=session.get(RawSourceDeletion, artifact.id) is None,
        )

    def _view(self, session: Session, dataset: Dataset) -> DatasetView:
        sources = [
            self._source_view(session, source)
            for source in session.scalars(
                select(DatasetSource)
                .where(DatasetSource.dataset_id == dataset.id)
                .order_by(DatasetSource.created_at, DatasetSource.id)
            )
        ]
        return DatasetView(
            id=dataset.id,
            name=dataset.name,
            description=dataset.description,
            license=dataset.license,
            created_at=dataset.created_at,
            sources=sources,
        )

    def attach(self, identifier: UUID, request: SourceAttach) -> DatasetSourceView:
        source_type(request.filename)
        self.get(identifier)
        artifact = self.artifacts.get(request.artifact_id)
        with self.factory() as session:
            self.artifacts.require_available(session, artifact.id)
        if artifact.bucket != self.artifacts.bucket:
            raise SourceError(409, "Artifact belongs to a different configured store")
        with self.artifacts.store.get(
            artifact.object_key, max_bytes=self.artifacts.max_bytes
        ) as body:
            digest, size = hash_content(body, max_bytes=self.artifacts.max_bytes)
            if digest != artifact.sha256 or size != artifact.size_bytes:
                raise SourceError(409, "Stored source no longer matches its registered hash")
            body.seek(0)
            admitted = inspect_source(body, request.filename)
        with unit_of_work(self.factory) as session:
            self._dataset(session, identifier, lock=True)
            self.artifacts.require_available(session, artifact.id)
            source = session.scalar(
                select(DatasetSource).where(
                    DatasetSource.dataset_id == identifier, DatasetSource.artifact_id == artifact.id
                )
            )
            if source is not None:
                if source.filename != request.filename or source.license != request.license:
                    raise SourceError(
                        409, "This artifact is already attached with different source metadata"
                    )
                return self._source_view(session, source)
            source = DatasetSource(
                dataset_id=identifier,
                artifact_id=artifact.id,
                kind=admitted.kind,
                filename=request.filename,
                license=request.license,
            )
            session.add(source)
            session.flush()
            if admitted.kind == "document":
                session.add(
                    Document(
                        source_id=source.id, title=request.filename, media_type=admitted.media_type
                    )
                )
                session.flush()
            result = self._source_view(session, source)
            session.commit()
            return result

    def download(self, identifier: UUID, source_id: UUID) -> str:
        with unit_of_work(self.factory) as session:
            self._dataset(session, identifier)
            source = self._source(session, identifier, source_id)
            artifact_id = source.artifact_id
        return self.artifacts.download(artifact_id)

    @staticmethod
    def _source(session: Session, identifier: UUID, source_id: UUID) -> DatasetSource:
        source = session.scalar(
            select(DatasetSource)
            .where(DatasetSource.id == source_id, DatasetSource.dataset_id == identifier)
            .with_for_update()
        )
        if source is None:
            raise SourceError(404, "Source not found in this dataset")
        return source

    def remove(self, identifier: UUID, source_id: UUID) -> SourceRemoved:
        with unit_of_work(self.factory) as session:
            self._dataset(session, identifier, lock=True)
            source = self._source(session, identifier, source_id)
            deletion = session.get(RawSourceDeletion, source.artifact_id)
            if deletion is not None:
                raise SourceError(
                    409, "Keep this source metadata as the record of its raw-object deletion"
                )
            if session.scalar(
                select(DocumentIngestion.id)
                .where(DocumentIngestion.dataset_id == identifier)
                .limit(1)
            ):
                raise SourceError(409, "Sources with document ingestion history cannot be removed")
            documents = list(
                session.scalars(
                    select(Document).where(Document.source_id == source.id).with_for_update()
                )
            )
            # Version manifests do not yet have relational source membership. Preserve
            # all sources once snapshots exist until that relationship is introduced.
            if (
                session.scalar(
                    select(DatasetVersion.id)
                    .where(DatasetVersion.dataset_id == identifier)
                    .limit(1)
                )
                or session.scalar(
                    select(ConversationImport.id)
                    .where(ConversationImport.source_id == source.id)
                    .limit(1)
                )
                or any(document.canonical_artifact_id is not None for document in documents)
            ):
                raise SourceError(
                    409, "This source has derived data or dataset versions and cannot be removed"
                )
            result = SourceRemoved(artifact_id=source.artifact_id)
            for document in documents:
                session.delete(document)
            session.flush()
            session.delete(source)
            session.commit()
            return result

    def delete_raw(self, identifier: UUID, source_id: UUID, *, confirm: bool) -> RawDeletionView:
        if not confirm:
            raise SourceError(422, "Confirm permanent deletion of the original bytes")
        with unit_of_work(self.factory) as session:
            self._dataset(session, identifier, lock=True)
            source = self._source(session, identifier, source_id)
            artifact = session.scalar(
                select(Artifact).where(Artifact.id == source.artifact_id).with_for_update()
            )
            assert artifact is not None
            if artifact.bucket != self.artifacts.bucket or not artifact.object_key.startswith(
                f"raw/v1/{artifact.id}/"
            ):
                raise SourceError(
                    409, "Only original uploads in the configured store can be deleted"
                )
            deletion = session.get(RawSourceDeletion, artifact.id)
            if deletion is None:
                if (
                    artifact.model_version_id is not None
                    or session.scalar(
                        select(DatasetVersion.id)
                        .where(DatasetVersion.dataset_id == identifier)
                        .limit(1)
                    )
                    or session.scalar(
                        select(DocumentIngestion.id)
                        .where(DocumentIngestion.dataset_id == identifier)
                        .limit(1)
                    )
                    or session.scalar(
                        select(ConversationImport.id)
                        .where(ConversationImport.source_id == source_id)
                        .limit(1)
                    )
                ):
                    raise SourceError(
                        409, "Processed or versioned originals must be retained for provenance"
                    )
                # Fail closed for every relational artifact use except its upload receipt
                # and this single raw attachment. This includes shared sources and models.
                for table in Base.metadata.tables.values():
                    if table.name in {"artifact_uploads", "raw_source_deletions"}:
                        continue
                    for column in table.columns:
                        if any(fk.target_fullname == "artifacts.id" for fk in column.foreign_keys):
                            query = select(column).where(column == artifact.id)
                            if table.name == "dataset_sources":
                                query = query.where(table.c.id != source_id)
                            if session.scalar(query.limit(1)) is not None:
                                raise SourceError(
                                    409,
                                    "This original is shared or referenced and must be retained",
                                )
                session.add(RawSourceDeletion(artifact_id=artifact.id))
                session.commit()  # Durable intent precedes external deletion; retries are safe.
            artifact_id, key = artifact.id, artifact.object_key
        self.artifacts.store.delete(key)
        # Include an abandoned/replayed staging copy, without deriving keys from browser input.
        with unit_of_work(self.factory) as session:
            upload = session.scalar(
                select(ArtifactUpload).where(ArtifactUpload.artifact_id == artifact_id)
            )
            if upload is not None:
                self.artifacts.store.delete(upload.object_key)
            deletion = session.scalar(
                select(RawSourceDeletion)
                .where(RawSourceDeletion.artifact_id == artifact_id)
                .with_for_update()
            )
            assert deletion is not None
            if deletion.deleted_at is None:
                deletion.deleted_at = datetime.now(UTC)
            session.commit()
        return RawDeletionView(artifact_id=artifact_id, deleted=True)
