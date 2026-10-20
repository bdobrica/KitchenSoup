# Installation and first workflow

## Published images (recommended)

Download [the standalone Compose file](../deploy/compose.yaml) as `compose.yaml`
in an empty directory and follow the [README quick start](../README.md#quick-start).
Only Docker Engine and Compose v2.20+ are required on Linux/WSL2. The initial
images support **linux/amd64**. Pulls require no registry login for this public
repository. The default application has no authentication and binds to loopback.

KitchenSoup `quay.io/bdobrica/kitchensoup:0.1.0` is the non-root Python control
plane shared by web, worker, reconciler and initialization services. It includes
packaged UI/catalog resources and Alembic migrations, without training frameworks
or build tools. Soup ingestion uses the separately published
`quay.io/bdobrica/kitchensoup:soup-ingest-0.1.0` image, with a digest pin and its
matching image configuration identity in Compose. Its isolated network, read-only
filesystem and resource limits match the development stack.

Generate `.env` **once**, before first startup. Do not rerun the generation command
for retained volumes: it replaces their credentials. Keep `.env` private, outside
source control. Custom ports use `KITCHENSOUP_PORT`, `RUSTFS_API_PORT` and
`RUSTFS_CONSOLE_PORT`; if changing the object API port, also set
`KITCHENSOUP_S3_PUBLIC_ENDPOINT` to its browser-reachable URL.

`docker compose up -d --wait` starts PostgreSQL, applies migrations through an
explicit one-shot service, provisions storage/CORS and syncs the bundled model
catalog before starting web. Worker and reconciler also wait for migrations.
Inspect failures with `docker compose ps -a` and `docker compose logs migrate`.
No source bind mounts, local builds, Docker socket, host keys or GPU access are
needed. GPU executors require deliberate additional configuration; see
[executor setup](executors.md). The basic installation prepares data and plans.

Stop with `docker compose down`; restart with `docker compose up -d --wait`.
Named volumes retain the database and object data. `docker compose down --volumes`
**deletes that data**. Do not stop reconciliation until remote resources are cleaned
up. Back up volumes and private configuration before upgrading. Download and review
the next release's Compose file, run `docker compose pull`, then
`docker compose up -d --wait`; migrations run as an explicit service again.
No automatic rollback of schema changes is provided. Use image digests from the
Compose file for immutable installation; version tags are convenient references.
Do not move an existing release tag to distribute a fix.

[Container validation evidence](evidence/container-distribution.md) records the
published image identities and fresh-install/restart checks.

## Development from a checkout


Use a trusted Linux host or WSL2, Python 3.13 with venv/ensurepip, GNU Make,
Docker Engine and Compose v2.20+. Allow disk space and internet access for Python
packages and pinned container dependencies. Initial builds can be large. No Node.js
or GPU is needed to import data, review plans or run service-free tests.

From a checkout of the release tag:

```sh
git checkout v0.1.0
make setup
make verify
make migrate
make up
make check-dependencies
```

If needed pass `PYTHON=python3` to commands after confirming it is Python 3.13.
Docker access must work for the current user; log in again after group changes.
Open <http://127.0.0.1:8000>. Credentials are generated into ignored `.env` without
printing them. Keep that file private and retain it with database/object volumes.
Do not replace a retained database password by generating a new `.env`.
See [configuration and startup](development.md#local-infrastructure).

Create a dataset, upload ChatGPT exports or supported documents, select content
and publish an immutable dataset version. Register an inspected native model or
public ungated exact Hub revision. Choose an intent, inspect examples and save a
reviewed plan. License provenance is visible but is not a permission grant.
See [models](models.md), [datasets](datasets.md), [plans](training-plans.md).

For training, follow [executor setup](executors.md); ordinary Compose deliberately
has no GPU/Docker authority. The host workflow uses three terminals for web,
worker and reconciler with identical settings. Choose FP16 for RTX 20/T4 devices.
Successful runs register verified outputs. Optionally compare local evaluations,
create merged/AWQ representations and launch a bounded temporary playground.

`make down` stops Compose while retaining data; `make restart` recreates it.
`make clean CONFIRM=1` irreversibly removes its named data volumes. Neither command
stops separately running host processes or releases remote cloud resources.
Stop playgrounds/cancel active runs and confirm provider cleanup before shutting
down reconcilers. Back up PostgreSQL, object data, private staging/journals and
operator secret configuration consistently before upgrading; `.env` is not a backup.

This release is for trusted single-user operation. To expose it beyond loopback,
read [security](security.md) and the [OIDCGate example](oidcgate.md) first. RustFS
browser transfers need a browser-reachable signing endpoint and matching CORS;
an authenticated web proxy alone does not configure object access. The default
loopback stack is the verified installation baseline.
