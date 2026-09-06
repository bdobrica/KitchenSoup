"""Bounded startup probes; no durable state or artifact operations."""

from collections.abc import Callable
from urllib.request import urlopen

import psycopg
from redis import Redis
from redis.backoff import NoBackoff
from redis.retry import Retry

from app.config import Settings


def check_postgres(settings: Settings) -> None:
    with psycopg.connect(
        host=settings.postgres_host,
        port=settings.postgres_port,
        dbname=settings.postgres_db,
        user=settings.postgres_user,
        password=settings.postgres_password.get_secret_value(),
        connect_timeout=3,
        options="-c statement_timeout=3000",
    ) as connection:
        if connection.execute("SELECT 1").fetchone() != (1,):
            raise RuntimeError("Unexpected PostgreSQL response")


def check_valkey(settings: Settings) -> None:
    with Redis.from_url(
        settings.valkey_url,
        socket_connect_timeout=3,
        socket_timeout=3,
        retry=Retry(NoBackoff(), 0),
    ) as client:
        if not client.ping():
            raise RuntimeError("Unexpected Valkey response")


def check_rustfs(settings: Settings) -> None:
    with urlopen(settings.rustfs_health_url, timeout=3) as response:
        if response.status != 200:
            raise RuntimeError("Unexpected RustFS response")


def check_dependencies(settings: Settings) -> None:
    if not settings.check_dependencies:
        return
    probes: tuple[tuple[str, Callable[[Settings], None]], ...] = (
        ("PostgreSQL", check_postgres),
        ("Valkey", check_valkey),
        ("RustFS", check_rustfs),
    )
    for name, probe in probes:
        try:
            probe(settings)
        except Exception:
            # Driver errors may include connection details; never propagate them into logs.
            raise RuntimeError(
                f"{name} startup check failed; verify service configuration"
            ) from None
