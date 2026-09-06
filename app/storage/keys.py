"""Object paths are versioned locators; database UUIDs remain authoritative."""

import re
from uuid import UUID


def safe_filename(filename: str) -> str:
    name = re.sub(r"[^a-zA-Z0-9._-]", "_", filename)[:128].strip(".")
    return name or "file"


def upload_key(identifier: UUID, filename: str) -> str:
    return f"uploads/v1/{identifier}/{safe_filename(filename)}"


def artifact_key(identifier: UUID, filename: str) -> str:
    return f"raw/v1/{identifier}/{safe_filename(filename)}"
