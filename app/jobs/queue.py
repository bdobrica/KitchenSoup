"""Valkey is a bounded wakeup queue and notification bus, never run authority."""

from typing import cast
from uuid import UUID

from redis import Redis
from redis.client import PubSub
from redis.exceptions import RedisError


class JobQueue:
    key = "kitchensoup:training:commands:v1"
    channel = "kitchensoup:training:updates:v1"

    def __init__(self, url: str) -> None:
        self.client: Redis = Redis.from_url(url, socket_connect_timeout=1, socket_timeout=1)

    def wake(self, identifier: UUID) -> None:
        try:
            with self.client.pipeline() as pipe:
                pipe.lpush(self.key, str(identifier))
                pipe.ltrim(self.key, 0, 9999)
                pipe.execute()
        except RedisError:
            pass  # The worker's PostgreSQL scan recovers missed wakeups.

    def pop(self) -> UUID | None:
        try:
            value = self.client.rpop(self.key)
            return UUID(value.decode()) if isinstance(value, bytes) else None
        except (RedisError, ValueError, UnicodeError):
            return None

    def publish(self, identifier: UUID) -> None:
        try:
            self.client.publish(self.channel, str(identifier))
        except RedisError:
            pass

    def subscribe(self) -> PubSub:
        subscriber = cast(PubSub, self.client.pubsub())  # type: ignore[no-untyped-call]
        try:
            subscriber.subscribe(self.channel)  # type: ignore[no-untyped-call]
        except RedisError:
            subscriber.close()
            raise
        return subscriber

    def close(self) -> None:
        self.client.close()
