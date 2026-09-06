# Milestone 1 validation

Validated locally on 2026-09-06 using Python 3.13.5, Docker Engine 29.0.1,
and Docker Compose 2.40.3.

- `make setup` installed the application and development dependencies.
- `make verify` passed Ruff formatting/lint, strict mypy, and all 15 unit tests.
  Tests cover successful/failed startup probes, suppressed connection-error
  details, required/redacted database passwords, and local credential generation,
  in addition to the Milestone 0 contracts. Two existing upstream TestClient
  deprecation warnings remain.
- `make compose-check` validated the configuration without exposing credentials.
- `make up` built the image and reported all six services healthy.
- `make check-dependencies` passed PostgreSQL SELECT 1, Valkey PING, and RustFS
  readiness probes from inside the web container.
- Live HTTP requests to the containerized home page and `/healthz` returned 200;
  the health body remained `{"status":"ok"}`.
- Temporary PostgreSQL row, Valkey key, and authenticated S3 bucket/object writes
  succeeded. After `make restart` recreated all containers, the original row,
  key, and object contents were readable. All six services became healthy again.
  S3 requests used curl SigV4 with credentials resolved inside the RustFS container.
- Temporary probe data was removed, then `make down` stopped and removed the
  containers/network successfully. Named volumes and ignored `.env` were retained.
- `make clean` without its confirmation flag refused to invoke Docker. The
  destructive `CONFIRM=1` path was not executed against the retained volumes.
- `git diff --check` passed.

No GPU, training, queue processing, database migrations, or ArtifactStore
implementation was exercised. The worker/reconciler are idle placeholders.
The S3 persistence check is infrastructure validation, not an application
artifact workflow. Hosted CI and interactive browser JavaScript were not run.
