"""File-type admission without document parsing or source normalization."""

import codecs
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import BinaryIO

from app.storage.archives import ArchiveError, extract_zip


class SourceError(Exception):
    def __init__(self, status: int, message: str) -> None:
        self.status = status
        super().__init__(message)


@dataclass(frozen=True)
class SourceType:
    kind: str
    media_type: str


SUPPORTED = {
    ".txt": SourceType("document", "text/plain"),
    ".md": SourceType("document", "text/markdown"),
    ".pdf": SourceType("document", "application/pdf"),
    ".docx": SourceType(
        "document", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    ),
    ".json": SourceType("structured", "application/json"),
    ".jsonl": SourceType("structured", "application/x-ndjson"),
    ".csv": SourceType("structured", "text/csv"),
    ".zip": SourceType("archive", "application/zip"),
}


def source_type(filename: str) -> SourceType:
    suffix = PurePosixPath(filename).suffix.lower()
    if suffix not in SUPPORTED:
        raise SourceError(422, "Choose a TXT, MD, PDF, DOCX, JSON, JSONL, CSV, or ZIP file")
    return SUPPORTED[suffix]


def inspect_source(source: BinaryIO, filename: str) -> SourceType:
    kind = source_type(filename)
    suffix = PurePosixPath(filename).suffix.lower()
    source.seek(0)
    if suffix in {".zip", ".docx"}:
        try:
            with extract_zip(source) as root:
                if suffix == ".docx" and not all(
                    (root / name).is_file() for name in ("[Content_Types].xml", "word/document.xml")
                ):
                    raise SourceError(
                        422, "DOCX must contain its content types and Word document members"
                    )
        except ArchiveError as error:
            raise SourceError(422, str(error)) from None
        except OSError:
            raise SourceError(503, "Temporary archive workspace unavailable") from None
    elif suffix == ".pdf":
        if not source.read(1024).startswith(b"%PDF-"):
            raise SourceError(422, "This file does not have a PDF header")
    else:
        decoder = codecs.getincrementaldecoder("utf-8-sig")()
        try:
            while chunk := source.read(1024**2):
                text = decoder.decode(chunk)
                if "\0" in text:
                    raise SourceError(422, "Text sources must not contain binary NUL bytes")
            decoder.decode(b"", final=True)
        except UnicodeError:
            raise SourceError(422, "Text sources must use UTF-8 encoding") from None
    return kind
