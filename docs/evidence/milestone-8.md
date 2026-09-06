# Milestone 8 validation

Validated on 2026-09-06 with Python 3.13, PostgreSQL 18.6 and RustFS 1.0.0-beta.12.

- `make fmt`, `make openapi` and `make verify` passed formatting, lint, strict
  type checking and all 80 unit tests. Unit checks cover assistant-target context,
  ordered text parts, empty/unprompted targets, and generated manifest/example
  schema drift. Conversation contexts contain no future turns.
- `make test-integration` passed all 35 tests using disposable PostgreSQL and
  RustFS. New checks cover saved and explicit selection, manifest hashes,
  pagination, immutable version history, concurrent version numbering, identical
  conversion hashes, document extraction provenance, overlapping extraction
  rejection, foreign selections, altered canonical/preview artifacts and storage
  failures without version registration. Existing migration round trips passed.
- The existing DatasetVersion table and immutable-record trigger were reused;
  no migration or dependency was added. `make migration-check` found no drift.
  `make compose-check` passed.
- Every previously published OpenAPI path and component schema was compared
  against the preceding commit and remains unchanged. Four version/preview
  operations and the manifest/example schemas are additive.
- `make up` started all seven long-running services healthy. Headless Chromium
  uploaded a synthetic ChatGPT export with eight assistant targets and a TXT file,
  imported conversations, saved selection and processed text with the real Soup
  runner. Version creation produced eight conversation examples and one document
  example. Pagination in both directions and page-size changes worked.
- The browser downloaded the exact example artifact and manifest and verified the
  manifest's output SHA-256. HTML-like source text remained literal text with no
  injected image or event execution. Changing saved selection and creating a
  document-only version produced version 2; reopening version 1 retained the same
  preview and exact downloadable bytes.
- Synthetic browser records, source/derived/version artifacts and lineage edges
  were removed. `make down` stopped the stack, retaining existing local volumes
  and ignored credentials. Browser tools and synthetic payloads stayed in `/tmp`.

Existing Starlette/AnyIO deprecations and the intentional duplicate-ZIP-name
fixture warning remain unrelated. No GPU training, tokenization, external LLM,
maximum-size performance or long-history load test was performed. The unchanged
Soup image's full four-format fixture suite was not rerun for this milestone;
the browser exercised its TXT path. Preview currently reads and rehashes the
bounded complete example artifact on each page request. Known ignored counts do
not invent counts for upstream warning categories that lack exact quantities.
