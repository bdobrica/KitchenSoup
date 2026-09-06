# Milestone 10 validation

Validated on 2026-09-06 with Python 3.13, PostgreSQL 18.6 and RustFS 1.0.0-beta.12.

- `make fmt`, `make openapi` and `make verify` passed formatting, lint, strict
  typing and 132 unit tests. New tests cover all guided defaults, Advanced recipe
  selection and overrides, serialization, schema drift, invalid numeric bounds,
  non-finite values, unsupported versions/fields and recipe mode consistency.
- `make test-integration` passed 43 tests against disposable PostgreSQL/RustFS.
  New coverage includes preview without writes, saved human/resolved payloads,
  snapshot serialization, retained registered model revision, archive hashes,
  changed-review rejection, direct SQL immutability, restrictive dataset deletion,
  absence of submitted runs, document recipes and mixed-data rejection, and APIs.
- `make migration MESSAGE="add immutable training plans"` generated the relational
  revision; a separate reviewed trigger revision guards all plan updates. Migration
  round trips, repeated upgrades and ORM drift checks passed. `make migrate`
  applied both revisions to the development database. No dependency was added.
- Generated contracts were compared with the preceding commit: all previously
  published OpenAPI paths and component schemas remain unchanged. AppSpec, recipes,
  resolved plans and their API operations are additive.
- `make up` started seven healthy long-running services. Headless Chromium created
  a synthetic eight-target conversation dataset version, followed its plan link,
  reviewed guided defaults without entering ML fields, inspected the resolved
  payload, saved it and verified identical contents after reload. It also selected
  an Advanced task recipe, overrode epochs, verified edits invalidate an earlier
  preview, and checked document guidance and incompatible-data rejection. No
  browser JavaScript errors occurred.
- Browser tooling remained in /tmp. No training, weight download, provider call,
  execution target or queued TrainingRun was created. The initial recipes still
  require Soup translation, tokenization, hardware and execution validation in the
  following milestones; no GPU fit or training-quality claim is made.

Existing Starlette/AnyIO deprecations and the intentional duplicate-ZIP fixture
warning remain unrelated. No credentials or signed object URLs were printed.

`make compose-check` and the final `make migration-check` passed. The synthetic
browser dataset, plan, newly registered test model and associated objects were
removed by exact identifiers; existing data was retained. `make down` stopped the
stack and preserved local volumes and ignored development credentials.
