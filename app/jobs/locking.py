"""Process-independent per-run claims, including terminal cleanup."""

from collections.abc import Iterator
from contextlib import contextmanager
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker


@contextmanager
def claim(factory: sessionmaker[Session], identifier: UUID) -> Iterator[bool]:
    # Separate transaction: retain the claim across committed state transitions and
    # external effects, without holding the run row lock during materialization.
    # A dead process/connection releases this lock automatically. No TTL can expire
    # under a still-running submit and permit a second concurrent launch.
    key = int.from_bytes(identifier.bytes[:8], "big", signed=True)
    with factory() as session, session.begin():
        yield bool(session.scalar(text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": key}))
