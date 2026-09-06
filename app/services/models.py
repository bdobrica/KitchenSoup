"""Model versions and source provenance registered in caller-owned transactions."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import Model, ModelCatalogEntry, ModelSource, ModelVersion
from app.db.session import unit_of_work
from app.registry.huggingface import ModelResolver, ResolvedModel
from app.registry.inspection import RegistryError, inspect_archive
from app.registry.schemas import (
    ArchiveImport,
    CatalogEntry,
    HuggingFaceImport,
    ModelView,
    SourceView,
    VersionView,
)
from app.services.artifacts import ArtifactService
from app.storage.hashing import hash_content


class ModelService:
    def __init__(
        self, factory: sessionmaker[Session], artifacts: ArtifactService, resolver: ModelResolver
    ) -> None:
        self.factory = factory
        self.artifacts = artifacts
        self.resolver = resolver

    def catalog(self) -> list[CatalogEntry]:
        with unit_of_work(self.factory) as session:
            return [
                self._catalog_view(row)
                for row in session.scalars(
                    select(ModelCatalogEntry).order_by(ModelCatalogEntry.key)
                )
                if not row.details.get("retired")
            ]

    @staticmethod
    def _catalog_view(row: ModelCatalogEntry) -> CatalogEntry:
        return CatalogEntry.model_validate(
            {
                **row.details,
                "key": row.key,
                "name": row.name,
                "repository": row.repository,
                "revision": row.revision,
                "license": row.license,
                "gated": row.gated,
            }
        )

    def list(self) -> list[ModelView]:
        with unit_of_work(self.factory) as session:
            return [
                self._view(session, row)
                for row in session.scalars(select(Model).order_by(Model.created_at, Model.id))
            ]

    def get(self, identifier: UUID) -> ModelView:
        with unit_of_work(self.factory) as session:
            row = session.get(Model, identifier)
            if row is None:
                raise RegistryError(404, "Model not found")
            return self._view(session, row)

    @staticmethod
    def _view(session: Session, row: Model) -> ModelView:
        versions = []
        for version in session.scalars(
            select(ModelVersion)
            .where(ModelVersion.model_id == row.id)
            .order_by(ModelVersion.version)
        ):
            sources = [
                SourceView.model_validate(source, from_attributes=True)
                for source in session.scalars(
                    select(ModelSource)
                    .where(ModelSource.model_version_id == version.id)
                    .order_by(ModelSource.created_at, ModelSource.id)
                )
            ]
            versions.append(VersionView(id=version.id, version=version.version, sources=sources))
        return ModelView(id=row.id, name=row.name, description=row.description, versions=versions)

    def choose(self, key: str) -> ModelView:
        with unit_of_work(self.factory) as session:
            row = session.scalar(
                select(ModelCatalogEntry).where(ModelCatalogEntry.key == key).with_for_update()
            )
            if row is None or row.details.get("retired"):
                raise RegistryError(404, "Catalog model not found")
            entry = self._catalog_view(row)
            # Repeated selection of the same catalog revision reuses its registered version.
            existing = session.scalar(
                select(Model)
                .join(ModelVersion)
                .join(ModelSource)
                .where(ModelSource.catalog_entry_id == row.id, ModelSource.revision == row.revision)
            )
            if existing is not None:
                return self._view(session, existing)
            return self._register(
                session,
                entry.name,
                ModelSource(
                    kind="catalog",
                    location=entry.repository,
                    revision=entry.revision,
                    license=entry.license,
                    license_url=str(entry.license_url),
                    gated=False,
                    catalog_entry_id=row.id,
                    notes="Curated source; engine execution validation pending",
                ),
            )

    def import_huggingface(self, request: HuggingFaceImport) -> ModelView:
        resolved: ResolvedModel = self.resolver.resolve(request.repository, request.revision)
        with unit_of_work(self.factory) as session:
            return self._register(
                session,
                request.name,
                ModelSource(
                    kind="huggingface",
                    location=resolved.repository,
                    revision=resolved.revision,
                    license=resolved.license,
                    license_url=resolved.license_url,
                    gated=False,
                    notes=request.notes,
                ),
            )

    def import_archive(self, request: ArchiveImport) -> ModelView:
        artifact = self.artifacts.get(request.artifact_id)
        if artifact.bucket != self.artifacts.bucket:
            raise RegistryError(409, "Artifact belongs to a different configured store")
        with self.artifacts.store.get(
            artifact.object_key, max_bytes=self.artifacts.max_bytes
        ) as body:
            digest, size = hash_content(body, max_bytes=self.artifacts.max_bytes)
            if digest != artifact.sha256 or size != artifact.size_bytes:
                raise RegistryError(409, "Stored archive no longer matches its registered hash")
            body.seek(0)
            inspect_archive(body)
        with unit_of_work(self.factory) as session:
            return self._register(
                session,
                request.name,
                ModelSource(
                    kind="upload",
                    location=f"artifact:{artifact.id}",
                    revision=None,
                    license=request.license,
                    license_url=str(request.license_url) if request.license_url else None,
                    gated=False,
                    artifact_id=artifact.id,
                    notes=request.notes,
                ),
            )

    def _register(self, session: Session, name: str, source: ModelSource) -> ModelView:
        model = Model(name=name)
        session.add(model)
        session.flush()
        version = ModelVersion(model_id=model.id, version=1)
        session.add(version)
        session.flush()
        source.model_version_id = version.id
        session.add(source)
        session.flush()
        result = self._view(session, model)
        session.commit()
        return result
