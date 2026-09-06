# Milestone 2 validation

Validated locally on 2026-09-06 with Python 3.13.5 and PostgreSQL 18.6.

- `make setup` installed SQLAlchemy 2.0.52 and Alembic 1.19.2 alongside the
  existing Psycopg driver.
- `make migration MESSAGE='initial application metadata'` generated revision
  `49a011c37db2` from ORM metadata. Its upgrade/downgrade creates/removes all
  21 application tables. Revision `0002_metadata_guards` adds hand-authored SQL
  for immutable identities/snapshots/events and database-maintained timestamps.
- `make verify` passed formatting, Ruff, strict mypy, and all 16 unit tests.
  The two previously recorded upstream TestClient deprecation warnings remain.
- `make test-integration` passed all seven PostgreSQL tests in an isolated
  container. Coverage includes migration downgrade/upgrade/repeated upgrade,
  schema comparison, records in all 21 tables, model repository CRUD, explicit
  commit/rollback, UUIDs, timestamps, foreign keys, unique versions, hash/reference
  constraints, and immutable run/dataset/event data. The disposable containers
  and their tmpfs test databases were removed by the runner.
- `make migrate` built the image and applied both revisions to the development
  database. `make migration-check` reported no new upgrade operations.
- `make up` started all six services healthy with the updated image. From web,
  SQLAlchemy observed 21 application tables plus `alembic_version`; the home
  page and unchanged `/healthz` response passed live HTTP checks.
- `make down` stopped and removed the development containers/network, retaining
  the migrated database volume and local configuration.
- `git diff --check` passed.

CI now invokes the same disposable-database test target. Hosted GitHub Actions
was not run during this validation. No ingestion, training, state-transition
service, GPU, or new public resource API is implemented by this milestone.
