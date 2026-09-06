# Milestone 4 validation

Validated on 2026-09-06 using Python 3.13, PostgreSQL 18.6 and
RustFS 1.0.0-beta.12.

- `make fmt`, `make openapi`, and `make verify`: generated schemas, formatting,
  linting, strict types, and 38 service-free tests passed. The initial sandboxed
  HTTP-test run stalled and was interrupted; the same gate passed outside the
  sandbox.
- `make test-integration`: 16 tests passed against disposable PostgreSQL and
  RustFS. Registry tests cover repeatable catalog synchronization/selection,
  retirement without deleting source history, preserved license snapshots,
  exact revision persistence, archive registration, and no partial model rows
  after failed provider or archive checks. Existing database/storage tests pass.
- `make migration-check` reported no new upgrade operations; this slice requires
  no schema migration. `make compose-check` passed.
- `make up` built the packaged catalog and UI, completed `catalog-init` and
  `storage-init`, and started all six long-running services healthy.
- Live anonymous Hugging Face resolution returned Qwen2.5 0.5B Instruct at commit
  `7ae557604adf67be50417f59c2c2f167def9a775`, with the `apache-2.0` declaration.
  The 1.5B catalog metadata was independently read from the public model API.
- Headless Chromium selected a curated model, registered a live Hugging Face
  model, and uploaded a synthetic structural ZIP through RustFS. Detail pages
  displayed the expected source, exact revision, archive SHA-256 and license.
  Browser tooling remained temporary outside the repository.
- An explicit comparison against the previous OpenAPI document confirmed every
  existing operation and schema unchanged; new model routes are additive.
- Browser-created models and the synthetic archive were removed. `make down`
  stopped the stack, retaining the catalog, volumes and ignored credentials.

The synthetic archive contains a minimal structural fixture, not a runnable
pretrained model. No GPU training, inference, full Qwen weight download,
maximum-size archive benchmark or conversion was performed. Compatibility
labels describe structural/upstream expectations and pending runner validation.
Test warnings are upstream Starlette/AnyIO deprecations and the intentionally
constructed duplicate ZIP member in a rejection test.
