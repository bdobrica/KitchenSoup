"""Dataset source admission and removal with explicit metadata transactions."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import (
    Artifact,
    ConversationImport,
    Dataset,
    DatasetSource,
    DatasetVersion,
    Document,
    DocumentIngestion,
)
from app.db.session import unit_of_work
from app.ingestion.schemas import (
    DatasetCreate,
    DatasetSourceView,
    DatasetView,
    DocumentView,
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
