"""Session ownership and explicit transaction boundaries."""

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import URL, Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings


def database_url(settings: Settings) -> URL:
    return URL.create(
        "postgresql+psycopg",
        username=settings.postgres_user,
        password=settings.postgres_password.get_secret_value(),
        host=settings.postgres_host,
        port=settings.postgres_port,
        database=settings.postgres_db,
    )


def create_database_engine(settings: Settings) -> Engine:
    return create_engine(database_url(settings), pool_pre_ping=True, hide_parameters=True)


@contextmanager
def unit_of_work(factory: sessionmaker[Session]) -> Iterator[Session]:
    """Commit explicitly via session.commit(); otherwise roll back on exit."""
    with factory() as session:
        try:
            yield session
        finally:
            session.rollback()
