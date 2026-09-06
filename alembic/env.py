"""Alembic uses the same configuration and driver as application sessions."""

from logging.config import fileConfig

from alembic import context
from app.config import Settings
from app.db.models import Base
from app.db.session import create_database_engine, database_url

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)


def run_migrations() -> None:
    if context.is_offline_mode():
        context.configure(
            url=database_url(Settings()),
            target_metadata=Base.metadata,
            literal_binds=True,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()
        return
    connection = config.attributes.get("connection")
    if connection is not None:
        context.configure(connection=connection, target_metadata=Base.metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()
        return
    engine = create_database_engine(Settings())
    try:
        with engine.connect() as connection:
            context.configure(
                connection=connection, target_metadata=Base.metadata, compare_type=True
            )
            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()


run_migrations()
