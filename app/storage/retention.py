"""Explicit operator sweep; never applies bucket-wide or registered-object expiry."""

import argparse

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import sessionmaker

from app.config import Settings
from app.db.session import create_database_engine
from app.services.artifacts import ArtifactService
from app.storage.base import StorageError
from app.storage.s3 import S3ArtifactStore


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--confirm", action="store_true", required=True)
    parser.add_argument("--limit", type=int, default=100)
    options = parser.parse_args()
    if not 1 <= options.limit <= 1000:
        parser.error("limit must be between 1 and 1000")
    settings = Settings()
    engine = create_database_engine(settings)
    store = S3ArtifactStore(settings)
    try:
        service = ArtifactService(store, sessionmaker(engine), bucket=settings.s3_bucket)
        print(f"Cleaned {service.sweep_uploads(limit=options.limit)} expired upload reservations")
    except StorageError:
        parser.exit(1, "Storage unavailable; completed deletions are safe to repeat.\n")
    except SQLAlchemyError:
        parser.exit(1, "Database unavailable; check connectivity and migrations before retrying.\n")
    finally:
        store.close()
        engine.dispose()


if __name__ == "__main__":
    main()
