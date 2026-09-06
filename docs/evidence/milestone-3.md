# Milestone 3 validation

Validated on 2026-09-06 with Python 3.13, PostgreSQL 18.6 and
RustFS 1.0.0-beta.12.

- `make setup` installed boto3 and its development type stubs.
- `make migration MESSAGE='artifact upload reservations'` generated revision
  `876f49074a6f`; `0004_upload_identity_guard` adds the metadata guard trigger.
- `make verify` passed formatting, linting, strict types, and 21 unit tests,
  including generated OpenAPI drift checks.
- `make test-integration` passed 12 tests against disposable PostgreSQL/RustFS:
  migration round trips, metadata constraints, managed multipart transfer,
  bounded reads, signed size/type enforcement, browser CORS preflight, actual
  byte hashing, replay isolation, missing/expired/mismatched uploads, empty
  uploads, and concurrent/repeated completion.
- `make migrate` applied the revisions to the retained local database.
  `make migration-check` reported no new upgrade operations.
- `make up` started all six long-running services healthy and completed bucket
  provisioning. `make compose-check` passed.
- Headless Chromium opened `/artifacts`, uploaded a synthetic text file directly
  to the RustFS port through the page, displayed the expected SHA-256, and
  downloaded identical bytes through the page's download button. Playwright was
  installed temporarily outside the repository for this manual acceptance check;
  browser automation is not part of the committed test gate.
- Synthetic browser objects and database rows were removed. `make down` stopped
  the stack and retained volumes and ignored credentials.

The test client emitted upstream Starlette/AnyIO deprecation warnings; tests
passed. No AWS live-provider, GPU, large-scale concurrency, or maximum-size
upload validation was performed. Browser uploads currently use single PUT;
server writes exercise managed multipart at 9 MiB in integration tests.
Abandoned-upload and orphan-object retention remains future work.
