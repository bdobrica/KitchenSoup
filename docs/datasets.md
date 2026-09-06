# Datasets and raw sources

Run `make migrate`, then `make up`, and open `/datasets`. Create a dataset, upload
a source, and use **Download original** to retrieve its unchanged bytes. Source
cards show the filename, kind, byte size, SHA-256, attachment date, declared license,
and initial document metadata. Dataset and source licenses are independent;
an omitted source license is not inferred from the dataset license.

The browser uploads through the [artifact API](storage.md), completes server
hash verification, then attaches that artifact to the dataset. Before attachment,
the source service rehashes stored bytes and performs file-type admission checks.
Successful attachment creates metadata in one PostgreSQL transaction. Repeating
the same dataset/artifact/filename/license request returns the same source;
conflicting metadata returns 409. Failed admission leaves the raw artifact
unchanged and creates no source or document records.

## Supported source types

| Extension | Admission check | Initial records |
| --- | --- | --- |
| `.txt`, `.md` | UTF-8 (optional BOM), no binary NULs | Document source and unprocessed Document |
| `.pdf` | Starts with a PDF header | Document source and unprocessed Document |
| `.docx` | Safe ZIP with `[Content_Types].xml` and `word/document.xml` | Document source and unprocessed Document |
| `.json`, `.jsonl`, `.csv` | UTF-8 (optional BOM), no binary NULs | Structured raw source |
| `.zip` | Safe archive extraction and CRC checks | Archive raw source |

Extensions are case-insensitive. The declared browser MIME type is not trusted
as proof of format. JSON/JSONL/CSV syntax, conversation layouts, PDF internals,
and Word XML are not parsed at this milestone. No canonical artifacts, extracted
text, training examples or dataset versions are produced. Original line endings,
encoding markers, filenames inside archives and all source bytes are retained.

## ZIP extraction utility

`app/storage/archives.py` provides `validated_zip` for bounded member access and
`extract_zip` for temporary extraction. The latter yields a private directory
and removes it after use, including after exceptions. Consumers must finish using
extracted files inside that context. They cannot select an existing destination.
Source admission exercises this path and discards extracted files afterwards;
future importers can use it without changing the retained original archive.

The shared validator rejects traversal and absolute paths, backslashes/drive
prefixes, control characters, duplicate names (case-insensitive), file/directory
conflicts, links, special files, encryption and unsupported compression. It
checks the central-directory bounds before constructing ZipFile. Files are
created exclusively without restoring executable permissions. Nested archives
are copied as inert members and never recursively extracted.

Default raw-source limits are 4,096 entries, a 4 MiB central directory, 1 GiB total
expanded bytes, 100:1 per-member compression, 512-character paths, and a 60-second
cooperative extraction budget. ZIP stored/deflate is supported; ZIP64 directory
metadata and other archive formats are unsupported. Actual bytes and CRCs are
checked while copying. Configured artifact upload limits still apply to compressed
input. Allow temporary disk for both the original artifact spool and extracted
members; concurrent uploads multiply that requirement. Model inspection uses the
same path validator while retaining its separate 8 GiB expanded-size limit.

Upstream references: [ZIP handling](https://docs.python.org/3.13/library/zipfile.html)
and [temporary directories](https://docs.python.org/3.13/library/tempfile.html).

## Source removal

**Remove source** asks for confirmation and removes an unprocessed source from
that dataset, along with its placeholder Document. It retains the Artifact,
SHA-256 and raw object, including when another dataset or model references them.
The response includes the retained artifact UUID; the UI offers a download button
immediately after removal. The existing artifact metadata/download APIs remain
available by UUID after reload. Permanent raw-object deletion is deferred.

Removal returns 409 if conversation imports, canonical document artifacts, or
any dataset version exist. This preserves provenance until version-specific
source membership and retention rules are implemented. The API also checks that
the source belongs to the requested dataset. See [ADR 0004](adr/0004-raw-source-ingestion.md).

## Version 1 HTTP contract

The routes below extend the existing [OpenAPI document](contracts/artifacts-v1.openapi.json).
The historical filename is retained. Regenerate it with `make openapi`; existing
artifact/model operations and schemas remain unchanged.

| Method and path under `/api/v1` | Result |
| --- | --- |
| POST `/datasets` | 201: create a dataset from name, optional description/license |
| GET `/datasets` | 200: list datasets and attached source metadata |
| GET `/datasets/{dataset_id}` | 200: dataset details and sources |
| POST `/datasets/{dataset_id}/sources` | 200: attach artifact UUID, filename, optional source license |
| POST `/datasets/{dataset_id}/sources/{source_id}/download` | 200: short-lived signed GET grant |
| DELETE `/datasets/{dataset_id}/sources/{source_id}` | 200: retained artifact UUID and `raw_artifact_retained: true` |

Dataset creation creates a new dataset on every call. Attachment is repeatable;
removing an already absent source returns 404. Unknown request properties and
unsupported files return 422; missing resources return 404; mismatched hashes,
conflicting attachments or protected removals return 409; artifact-size bounds
return 413; unavailable services/workspace return 503. Successful data responses
use `Cache-Control: no-store`. The existing trusted-user, localhost deployment
assumptions apply. The service-free shell renders `/datasets` but its API returns 503.
