# Milestone 13 — Durable training job lifecycle

Date: 2026-10-19

## Implemented

- Normalized QUEUED/PREPARING/SUBMITTED/RUNNING/POSTPROCESSING/success/failure/
  cancel/lost states, enforced in Python and PostgreSQL.
- Atomic initial/transition events in immutable `job_events`; baseline retained for
  preexisting runs. No submitted input or runner payload migration.
- Bounded Valkey UUID command hints, independent worker/reconciler loops,
  PostgreSQL advisory claims, row-locked state updates and database scan recovery.
- Async cancel requests during preparation/training, terminal retry-by-clone,
  deterministic attempt identities and conservative crash recovery without replay.
- PostgreSQL-backed SSE replay/live updates, transient Valkey publications,
  progress history, cursor-based log display and retry controls in the run UI.
- Host worker/reconciler Make targets, loopback Valkey overlay, role-specific
  background health heartbeats, generated OpenAPI and operating documentation.

See [ADR 0014](../adr/0014-durable-training-lifecycle.md) and
[job lifecycle reference](../jobs.md).

## Verification performed

- `make fmt` and `make openapi` completed. Generated schema equality is checked by
  the unit suite; unrelated runner/AppSpec/recipe schemas did not change.
- `make verify` passed: Ruff formatting/lint, strict mypy over 100 source files,
  and **276 unit tests**. The independent transition contract matrix checks every
  normalized state pair and rejects executor-only MISSING as a durable state.
- `sg docker -c 'make test-integration'` passed: **57 integration tests** using
  disposable PostgreSQL 18.6, RustFS beta.12 and Valkey 9.1.1. The existing Docker
  group membership was activated for the command; no daemon/group permissions
  were changed.
- Migration round trips, idempotent upgrades and Alembic metadata drift checks
  passed inside the disposable database. Direct SQL checks compare all state
  transitions with application rules; rollback removes both status and event.
  Existing snapshot/event immutability checks still pass. An upgrade from the
  preceding revision preserves an existing run snapshot/history and adds only
  its current-state baseline before subsequent transitions continue in sequence.
- Lifecycle tests verify that web submission does not launch Docker, duplicate
  dispatch/competing claims launch once, missing wakeups are recovered from
  PostgreSQL, and deleting queue hints or losing Valkey cannot lose cancel/retry.
- Separate Python worker/reconciler interpreters reconstruct the retained attempt
  after the submitting web application's lifecycle closes. A fresh web application
  reads RUNNING from PostgreSQL. The synthetic external executor launches once;
  no in-memory web task owns the execution.
- Recovery checks cover abandoned preparation, a container launched before the
  SUBMITTED commit, an unstarted container stopped before failure, daemon outage
  distinguished from confirmed absence, LOST retention and immutable retry inputs.
- Cancellation tests cover queued work, a request committed while submission is
  blocked, repeated requests and successful completion winning the stop race.
- Streaming-client tests verify active SSE delivery through terminal cancellation,
  all six ordered event IDs, reconnect replay with Last-Event-ID, invalid cursors,
  missing runs and no-store headers. Real Valkey publication/subscription is checked.
- Default `make compose-check` and the host overlay `docker compose -f
  docker-compose.yml -f docker-compose.training.yml config --quiet` passed without
  displaying expanded credentials.
- Worker and reconciler command entrypoints passed isolated startup, heartbeat,
  SIGTERM and heartbeat-cleanup smokes with local training disabled.
- Final diff reviewed for unrelated changes, generated drift and sensitive payloads.

## Limits

The lifecycle suite uses synthetic external executors or a fake Docker CLI while
exercising real database/queue services. No additional GPU training, provider call,
model download or full browser end-to-end automation was performed in this slice.
Prior actual RTX 2070 SUPER FP16 execution remains recorded in
[FP16 evidence](fp16-training.md); GPU execution arguments and runner behavior are
unchanged. SSE was exercised through the application's streaming HTTP test client.

Only disposable test services were created; the retained application database was
not migrated or populated by this validation. Test containers are removed by the
harness. Existing Starlette test-client deprecation and intentional duplicate-ZIP
fixture warnings are unrelated to this milestone.

Model/output registry import remains Milestone 14. GPU capacity scheduling,
automatic retries, retention and multi-user authority are outside this milestone.
