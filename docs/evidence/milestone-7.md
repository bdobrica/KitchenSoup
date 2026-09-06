# Milestone 7 validation

Validated on 2026-09-06 with Python 3.13 for the application, Python 3.12 and
Soup 0.74.0 for ingestion, PostgreSQL 18.6 and RustFS 1.0.0-beta.12.

- `make migration MESSAGE='document ingestion provenance'` generated revision
  `df16af97c9ad`. A separate hand-authored trigger revision protects ingestion
  snapshots from updates. `make migrate` applied both; `make migration-check`
  found no schema drift.
- `make fmt`, `make openapi` and `make verify` passed formatting, lint, strict types
  and 76 unit tests. All previously published OpenAPI paths and component schemas
  were compared with HEAD and remain unchanged.
- `make test-integration` passed all 30 tests on the final run, including migration
  round trips and schema drift checks. Ingestion checks use disposable PostgreSQL/RustFS with a
  fixture runner. They verify selection scoping, source hashes, ignored/empty
  inputs, separate output artifacts, derivation edges, identical replay hashes,
  immutable numbered history, failure logs, protected removal and HTTP behavior.
  The all-table database fixture was extended for the new table.
- `make test-soup-ingest` exercised the real pinned Soup CLI twice for TXT,
  Markdown, PDF and DOCX. Each returned the expected rows and byte-identical
  repeats. A malformed synthetic PDF returned nonzero status with captured logs.
  Containers had no network, GPU, database or object-store credentials. The final
  image ID was `sha256:f5cd160386d11a1bff42122ff3687202c49e5f2dc276583640cb12f30493485c`.
- The Python base digest is pinned and the generated dependency lock was resolved
  with `scripts/lock_soup_ingest.py`. No Soup Python internals are imported by
  application or runner code.
- `make compose-check` passed. `make up` started seven long-running services
  healthy, including the isolated Soup runner. Web has no Docker socket; the
  runner has no published port, credentials, persistent storage or external egress.
- Headless Chromium uploaded three synthetic documents, selected two, and
  obtained three text rows through the real HTTP runner. Extracted dataset,
  manifest and log downloads worked. A second attempt produced identical output
  bytes; history survived reload; all original downloads matched uploaded bytes.
  Browser tooling and payloads remained temporary outside the repository.
- Synthetic browser rows, objects and lineage were removed. `make down` stopped
  the stack while retaining migrated local volumes and ignored credentials.

An intermediate full integration run encountered a connection error in the
unchanged RustFS signed-PUT rejection test. The ingestion checks passed on that
run; the final full rerun passed without changing that test. Existing upstream Starlette/AnyIO deprecations and the intentional duplicate
ZIP-name warning remain unrelated to ingestion.

Checks use synthetic documents, not sensitive real documents. No claim is made
for OCR, Word table/image extraction, maximum-size performance, crash recovery,
GPU training, external providers or Milestone 8's immutable dataset manifests.
Retain the recorded image and originals for replay; rebuilding can change Docker
attestation/image IDs even with the same pinned dependencies and filesystem.
