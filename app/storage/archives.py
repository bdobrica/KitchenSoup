"""Bounded ZIP access and extraction into disposable, private directories."""

import stat
import struct
import time
import zipfile
import zlib
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from tempfile import TemporaryDirectory
from typing import BinaryIO


class ArchiveError(Exception):
    """Invalid or unsupported archive; never includes source payloads."""


@dataclass(frozen=True)
class ArchiveLimits:
    max_entries: int = 4096
    max_directory_bytes: int = 4 * 1024**2
    max_expanded_bytes: int = 1024**3
    max_ratio: int = 100
    seconds: int = 60


DEFAULT_LIMITS = ArchiveLimits()


@contextmanager
def validated_zip(
    source: BinaryIO, limits: ArchiveLimits = DEFAULT_LIMITS
) -> Iterator[tuple[zipfile.ZipFile, dict[str, zipfile.ZipInfo]]]:
    """Validate the complete directory before exposing members; callers bound reads."""
    try:
        source.seek(0, 2)
        length = source.tell()
        source.seek(max(0, length - 65557))
        tail = source.read(65557)
        marker = tail.rfind(b"PK\x05\x06")
        if marker < 0 or len(tail) - marker < 22:
            raise ArchiveError("Upload a valid ZIP archive")
        end_offset = length - len(tail) + marker
        if end_offset >= 20:
            source.seek(end_offset - 20)
            if source.read(4) == b"PK\x06\x07":
                # ZipFile uses ZIP64 directory values even without sentinel fields
                # in the ordinary end record. Reject before it can allocate metadata.
                raise ArchiveError("ZIP64 archive directories are unsupported")
        _, disk, directory_disk, disk_count, count, directory_size, offset, comment = struct.unpack(
            "<4s4H2LH", tail[marker : marker + 22]
        )
        if (
            disk
            or directory_disk
            or disk_count != count
            or count > limits.max_entries
            or directory_size > limits.max_directory_bytes
            or offset == 0xFFFFFFFF
            or offset + directory_size > length
            or marker + 22 + comment != len(tail)
        ):
            raise ArchiveError(
                "Archive directory exceeds limits or uses unsupported ZIP64/multi-volume metadata"
            )
        source.seek(0)
        with zipfile.ZipFile(source) as archive:
            entries = archive.infolist()
            if len(entries) != count:
                raise ArchiveError("Inconsistent ZIP directory")
            files = {}
            seen = set()
            total = 0
            for entry in entries:
                name = entry.filename
                path = PurePosixPath(name)
                parts = name.rstrip("/").split("/")
                mode = entry.external_attr >> 16
                if (
                    not name
                    or name != entry.orig_filename
                    or "\\" in name
                    or ":" in name
                    or path.is_absolute()
                    or any(p in {"", ".", ".."} or len(p) > 255 for p in parts)
                    or any(ord(c) < 32 for c in name)
                    or len(name) > 512
                    or name.casefold().rstrip("/") in seen
                ):
                    raise ArchiveError("Archive contains unsafe or duplicate paths")
                seen.add(name.casefold().rstrip("/"))
                if (
                    stat.S_IFMT(mode) not in (0, stat.S_IFREG, stat.S_IFDIR)
                    or stat.S_ISDIR(mode)
                    and not entry.is_dir()
                    or entry.flag_bits & 1
                ):
                    raise ArchiveError(
                        "Archive links, special files, and encryption are unsupported"
                    )
                if entry.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
                    raise ArchiveError("Use ZIP stored or deflate compression")
                if entry.is_dir():
                    if entry.file_size:
                        raise ArchiveError("Archive directories must not contain file data")
                    continue
                total += entry.file_size
                if (
                    total > limits.max_expanded_bytes
                    or entry.file_size > max(1, entry.compress_size) * limits.max_ratio
                ):
                    raise ArchiveError("Archive exceeds expanded-size or compression-ratio limits")
                files[name] = entry
            regular_names = {name.casefold() for name in files}
            for name in seen:
                if any(str(parent) in regular_names for parent in PurePosixPath(name).parents):
                    raise ArchiveError("Archive contains conflicting file and directory paths")
            yield archive, files
    except (zipfile.BadZipFile, EOFError, RuntimeError, ValueError, struct.error, zlib.error):
        raise ArchiveError(
            "Upload a valid, unencrypted ZIP using stored or deflate compression"
        ) from None


@contextmanager
def extract_zip(source: BinaryIO, limits: ArchiveLimits = DEFAULT_LIMITS) -> Iterator[Path]:
    """Extract regular files exclusively into a new directory; remove it on every exit."""
    started = time.monotonic()
    with (
        validated_zip(source, limits) as (archive, files),
        TemporaryDirectory(prefix="kitchensoup-source-") as temporary,
    ):
        root = Path(temporary)
        total = 0
        for name, entry in files.items():
            destination = root.joinpath(*PurePosixPath(name).parts)
            # Paths and ancestors were checked before any writes. The private directory
            # contains no pre-existing files, and archive symlinks are never created.
            destination.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(entry) as reader, destination.open("xb") as writer:
                size = 0
                while chunk := reader.read(1024**2):
                    size += len(chunk)
                    total += len(chunk)
                    if (
                        size > entry.file_size
                        or total > limits.max_expanded_bytes
                        or time.monotonic() - started > limits.seconds
                    ):
                        raise ArchiveError("Archive extraction exceeded its resource limits")
                    writer.write(chunk)
                if size != entry.file_size or time.monotonic() - started > limits.seconds:
                    raise ArchiveError("Archive size is inconsistent or extraction timed out")
        yield root
