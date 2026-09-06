"""Versioned packaged catalog; PostgreSQL is a synchronized browsing projection."""

from pathlib import Path

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import ModelCatalogEntry
from app.db.session import unit_of_work
from app.registry.schemas import Catalog

CATALOG_PATH = Path(__file__).with_name("catalog-v1.json")


def load_catalog(path: Path = CATALOG_PATH) -> Catalog:
    return Catalog.model_validate_json(path.read_text())


def sync_catalog(factory: sessionmaker[Session], catalog: Catalog | None = None) -> None:
    catalog = catalog if catalog is not None else load_catalog()
    with unit_of_work(factory) as session:
        for entry in catalog.models:
            values = entry.model_dump(mode="json")
            details = {
                key: values.pop(key)
                for key in list(values)
                if key not in {"key", "name", "repository", "revision", "license", "gated"}
            }
            values["details"] = details
            statement = insert(ModelCatalogEntry).values(**values)
            session.execute(
                statement.on_conflict_do_update(index_elements=[ModelCatalogEntry.key], set_=values)
            )
        # Retain referenced rows and UUIDs, but hide entries removed from the source catalog.
        for row in session.scalars(select(ModelCatalogEntry)):
            if row.key not in {entry.key for entry in catalog.models}:
                row.details = {**row.details, "retired": True}
        session.commit()


if __name__ == "__main__":
    from app.config import Settings
    from app.db.session import create_database_engine

    engine = create_database_engine(Settings())
    try:
        sync_catalog(sessionmaker(engine, expire_on_commit=False))
        print("Model catalog synchronized")
    finally:
        engine.dispose()
