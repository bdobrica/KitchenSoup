import stat
from io import BytesIO
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

import pytest

from app.ingestion.sources import SourceError, inspect_source
from app.storage.archives import ArchiveError, ArchiveLimits, extract_zip


def zip_bytes(files: dict[str, bytes]) -> bytes:
    body = BytesIO()
    with ZipFile(body, "w") as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    return body.getvalue()


def test_extraction_preserves_bytes_and_cleans_workspace() -> None:
    payload = zip_bytes({"export/conversations.json": b"[]", "export/notes.txt": b"notes\r\n"})
    source = BytesIO(payload)
    with extract_zip(source) as root:
        assert (root / "export/notes.txt").read_bytes() == b"notes\r\n"
        assert root.stat().st_mode & 0o077 == 0
    assert not root.exists()
    assert source.getvalue() == payload
    with pytest.raises(LookupError):
        with extract_zip(BytesIO(payload)) as failed_root:
            raise LookupError("consumer failure")
    assert not failed_root.exists()


@pytest.mark.parametrize(
    "path", ["../outside", "/absolute", "a/../../outside", "C:/file", "a\\b", "a/./b", "a//b"]
)
def test_rejects_paths_before_creating_workspace(
    path: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    def must_not_create(*args: object, **kwargs: object) -> None:
        pytest.fail("Invalid paths must be rejected before extraction")

    monkeypatch.setattr("app.storage.archives.TemporaryDirectory", must_not_create)
    with pytest.raises(ArchiveError):
        with extract_zip(BytesIO(zip_bytes({path: b"x"}))):
            pytest.fail("Unsafe archive admitted")


def test_rejects_links_collisions_and_limits() -> None:
    body = BytesIO()
    with ZipFile(body, "w") as archive:
        link = ZipInfo("link")
        link.create_system = 3
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        archive.writestr(link, "target")
    with pytest.raises(ArchiveError):
        with extract_zip(body):
            pytest.fail("Symlink admitted")
    for files in [{"a": b"x", "a/b": b"y"}, {"A.txt": b"x", "a.txt": b"y"}]:
        with pytest.raises(ArchiveError):
            with extract_zip(BytesIO(zip_bytes(files))):
                pytest.fail("Conflicting names admitted")
    payload = zip_bytes({"a": b"one", "b": b"two"})
    for limits in [
        ArchiveLimits(max_entries=1),
        ArchiveLimits(max_expanded_bytes=5),
        ArchiveLimits(max_directory_bytes=1),
    ]:
        with pytest.raises(ArchiveError):
            with extract_zip(BytesIO(payload), limits):
                pytest.fail("Limit ignored")
    body = BytesIO()
    with ZipFile(body, "w", compression=ZIP_DEFLATED) as archive:
        archive.writestr("compressible", b"x" * 100000)
    with pytest.raises(ArchiveError):
        with extract_zip(body):
            pytest.fail("Compression ratio ignored")


def test_crc_failure_cleans_partial_extraction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import tempfile

    from app.storage import archives

    directories = []

    def workspace(*, prefix: str) -> tempfile.TemporaryDirectory[str]:
        directory = tempfile.TemporaryDirectory(prefix=prefix, dir=tmp_path)
        directories.append(Path(directory.name))
        return directory

    monkeypatch.setattr(archives, "TemporaryDirectory", workspace)
    payload = zip_bytes({"a.txt": b"first", "b.txt": b"unique-payload"})
    corrupted = payload.replace(b"unique-payload", b"broken-payload", 1)
    with pytest.raises(ArchiveError):
        with extract_zip(BytesIO(corrupted)):
            pytest.fail("Corrupt archive admitted")
    assert directories and not any(directory.exists() for directory in directories)


@pytest.mark.parametrize(
    "filename,data,kind",
    [
        ("notes.txt", b"\xef\xbb\xbfhello\r\n", "document"),
        ("notes.md", b"# Title\n", "document"),
        ("records.json", b"[]", "structured"),
        ("records.jsonl", b"{}\n", "structured"),
        ("records.csv", b"a,b\r\n1,2", "structured"),
        ("file.PDF", b"%PDF-1.7\n", "document"),
    ],
)
def test_supported_sources_unchanged(filename: str, data: bytes, kind: str) -> None:
    body = BytesIO(data)
    assert inspect_source(body, filename).kind == kind
    assert body.getvalue() == data


def test_docx_and_zip_admission() -> None:
    payload = zip_bytes({"[Content_Types].xml": b"<Types/>", "word/document.xml": b"<document/>"})
    assert inspect_source(BytesIO(payload), "document.docx").kind == "document"
    assert inspect_source(BytesIO(payload), "export.zip").kind == "archive"
    with pytest.raises(SourceError):
        inspect_source(BytesIO(zip_bytes({"other.txt": b"x"})), "bad.docx")


def test_zip64_directory_is_rejected_before_zipfile_reads_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = zip_bytes({"a.txt": b"x"})
    payload = payload[:-22] + b"PK\x06\x07" + b"\0" * 16 + payload[-22:]

    def must_not_open(*args: object, **kwargs: object) -> None:
        pytest.fail("ZIP64 directory must be rejected before ZipFile construction")

    monkeypatch.setattr("app.storage.archives.zipfile.ZipFile", must_not_open)
    with pytest.raises(ArchiveError, match="ZIP64"):
        with extract_zip(BytesIO(payload)):
            pytest.fail("ZIP64 archive admitted")


@pytest.mark.parametrize(
    "filename,payload",
    [
        ("code.exe", b"x"),
        ("fake.pdf", b"text"),
        ("binary.txt", b"\0"),
        ("latin.txt", b"\xff"),
        ("bad.zip", b"not a zip"),
    ],
)
def test_bad_source_types(filename: str, payload: bytes) -> None:
    with pytest.raises(SourceError):
        inspect_source(BytesIO(payload), filename)
