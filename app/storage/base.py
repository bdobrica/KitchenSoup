"""Storage port used by synchronous application services."""

from dataclasses import dataclass
from typing import BinaryIO, Protocol


class StorageError(Exception):
    """Storage operation failed; messages contain no provider payloads."""


class ObjectNotFound(StorageError):
    pass


class ObjectTooLarge(StorageError):
    pass


@dataclass(frozen=True)
class ArtifactStat:
    key: str
    size_bytes: int


class ArtifactStore(Protocol):
    def put(self, key: str, source: BinaryIO) -> ArtifactStat: ...
    def get(self, key: str, *, max_bytes: int | None = None) -> BinaryIO: ...
    def stat(self, key: str) -> ArtifactStat: ...
    def delete(self, key: str) -> None: ...
    def presign_get(self, key: str, expires_in: int) -> str: ...
    def presign_put(
        self, key: str, expires_in: int, *, content_type: str, size_bytes: int
    ) -> str: ...
