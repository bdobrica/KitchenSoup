# Milestone 6 validation

Validated on 2026-09-06 with Python 3.13, PostgreSQL 18.6 and
RustFS 1.0.0-beta.12.

- `make migration MESSAGE='conversation selection and import warnings'` generated
  revision `953d3fcf27d6`. The reviewed migration adds selected=false and warnings=[]
  defaults to existing conversation rows. `make migrate` applied it locally.
- `make fmt`, `make openapi`, and `make verify` passed formatting, linting, strict
  types and 72 unit tests. Canonical JSON Schema and OpenAPI are generated from
  source definitions; unit tests check generated-schema drift.
- `make test-integration` passed 26 tests with disposable PostgreSQL and RustFS,
  including migration round trips, simultaneous/repeated import, canonical SHA-256
  and artifact lineage, persisted dataset-scoped selection, protected source
  removal, and metadata rollback after late parsing or storage failures.
- Synthetic export fixtures cover active branches, CRLF normalization, retained
  source IDs, nested numbered shards, the conversations wrapper, multimodal/tool/
  attachment warnings, hidden/developer messages, malformed graphs, duplicate IDs,
  invalid timestamps and JSON limits. No real personal export is committed.
- `make migration-check` found no schema drift. `make compose-check` passed.
- `make up` completed initialization and started all six long-running services
  healthy. Headless Chromium uploaded a synthetic complete ZIP, imported it,
  displayed warnings, searched titles, exercised select all/none, saved a subset,
  reloaded to verify persistence, and downloaded the byte-identical original ZIP.
  Browser tooling remained temporary outside the repository.
- All previously published API operations and schemas were compared with HEAD and
  remain unchanged. Conversation routes and the canonical schema are additive.
- Synthetic browser records, canonical/raw objects and lineage edges were removed.
  `make down` stopped the stack, retaining migrated local volumes and credentials.

Warnings are existing upstream Starlette/AnyIO deprecations and the intentional
ZIP duplicate-name fixture. Validation used synthetic exports; it does not claim
universal support for changing ChatGPT export layouts. Maximum-size performance,
real personal exports, training-example generation, immutable dataset manifests,
and GPU training were not tested and are outside this milestone.
