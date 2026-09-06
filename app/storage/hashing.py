import hashlib
from typing import BinaryIO

from app.storage.base import ObjectTooLarge


def hash_content(source: BinaryIO, *, max_bytes: int) -> tuple[str, int]:
    """Hash from the current position without loading the whole object into memory."""
    digest = hashlib.sha256()
    size = 0
    while chunk := source.read(1024 * 1024):
        size += len(chunk)
        if size > max_bytes:
            raise ObjectTooLarge("Object exceeds the upload limit")
        digest.update(chunk)
    return digest.hexdigest(), size
