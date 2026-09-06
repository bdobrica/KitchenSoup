import os
from collections.abc import Iterator

import pytest
from alembic.config import Config
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session, sessionmaker

from alembic import command
from app.config import Settings
from app.db.models import Base
from app.db.session import create_database_engine


@pytest.fixture(scope="session")
def engine() -> Iterator[Engine]:
    settings = Settings(_env_file=None)
    if os.environ.get("KITCHENSOUP_DB_TEST") != "1" or settings.postgres_db != "kitchensoup_test":
        pytest.fail("Run make test-integration to provision an isolated database")
    engine = create_database_engine(settings)
    with engine.begin() as connection:
        config = Config("alembic.ini")
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
    yield engine
    engine.dispose()


@pytest.fixture(autouse=True)
def empty_database(engine: Engine) -> None:
    # This database exists only inside the disposable test container.
    tables = ", ".join(f'"{name}"' for name in Base.metadata.tables)
    with engine.begin() as connection:
        connection.execute(text(f"TRUNCATE {tables} CASCADE"))


@pytest.fixture
def factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(engine, expire_on_commit=False)
