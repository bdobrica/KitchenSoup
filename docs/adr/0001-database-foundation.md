# ADR 0001: PostgreSQL metadata and explicit transactions

Status: Accepted

Date: 2026-09-06

## Context

[PLAN.md](../../PLAN.md#36-proposed-postgresql-entities) assigns durable metadata
to PostgreSQL, separates model and artifact lineage, and requires immutable
dataset versions and submitted run inputs. Milestone 2 establishes persistence
before the ingestion, training, and serving services are implemented.

## Decision

Use SQLAlchemy 2 with the existing synchronous Psycopg driver. The database
module owns ORM mappings and session construction. A caller-owned unit of work
requires an explicit commit and otherwise rolls back and closes the session.
Repositories do not commit. Blocking database work belongs in synchronous
handlers or worker threads when called from asynchronous code.

Alembic revisions are the schema authority for deployed databases. Application
startup does not create tables or apply migrations. Generate relational changes
from ORM metadata, review the revision, and maintain PostgreSQL trigger SQL in
separate, hand-authored revisions. Existing migrations remain independent of
future ORM definitions.

All 21 initial tables use UUID primary keys and timezone-aware creation/update
timestamps. PostgreSQL guards identifiers and creation timestamps against
updates and maintains update timestamps. Foreign keys restrict deletion of
referenced rows. Model lineage and artifact derivations use separate edge tables.
Content hashes are lowercase SHA-256 strings on artifacts and immutable
dataset/run snapshots; sources refer to the corresponding artifact hash.

Dataset-version rows and job events reject updates. Job events also reject
deletes. Submitted training input columns reject updates while status and the
external execution identifier remain mutable. State-transition enforcement and
event emission are deferred to Milestone 13; there is no submission service yet.

Provider and target records have credential-reference fields, not raw secret
fields. Flexible JSONB fields contain non-secret configuration or immutable
snapshot payloads. They do not establish new public wire schemas or expand
execution authority. Service-specific payload validation comes with the
corresponding milestones.

## Consequences

Integration tests require actual PostgreSQL to check UUIDs, JSONB, constraints,
and triggers. `make test-integration` provisions an isolated ephemeral database,
runs migration round trips and persistence tests, and removes the container.
CI runs this gate in addition to service-free checks.

The database is intentionally an initial foundation. Later migrations add
workflow-specific fields and constraints. Transitive lineage-cycle detection,
retention policy, provider configuration validation, and published JSON schemas
remain with their scheduled milestones. No cross-repository persistence access
or external framework registry is introduced.
