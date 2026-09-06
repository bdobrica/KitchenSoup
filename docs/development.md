# Development reference

## Runtime and dependencies

Python 3.13 is the supported minor version, configured in `.python-version`,
project metadata, Ruff, mypy, and CI. `make setup` creates `.venv` and installs
an editable package with development tools using pip. Re-running it is safe.
Dependency ranges are declared in `pyproject.toml`; installations are not locked
to an identical transitive dependency set.

FastAPI provides HTTP routing and OpenAPI; Uvicorn serves ASGI; Jinja2 renders
HTML; Pydantic Settings loads validated operator configuration. Development
uses pytest and HTTPX for in-process HTTP tests, Ruff for linting/formatting, and
mypy for strict type checking. Hatchling builds the Python package, including
its templates and static assets. Psycopg supplies the authenticated PostgreSQL
startup query; redis-py supplies the Valkey PING probe. These small clients will
also support database and future queue operations. SQLAlchemy owns ORM mappings
and transactions; Alembic manages PostgreSQL schema changes. RustFS readiness uses
Python’s standard HTTP client. Boto3 provides S3 signing and managed multipart
transfers behind the ArtifactStore port; boto3-stubs supplies development types.

Run `make dev` from the repository root. It binds to `127.0.0.1:8000` with reload.
The ASGI entry point is `app.main:create_app` with Uvicorn's `--factory` option.
The factory accepts an explicit `Settings` instance for isolated tests.
Template and static paths resolve relative to the installed package.

## Configuration

`KITCHENSOUP_APP_NAME` is a display name, defaulting to `KitchenSoup`, with a
length of 1–100 characters. Values passed to `Settings` take precedence over
environment variables, which override `.env` in the working directory, which
in turn overrides defaults. `.env` uses UTF-8; unrelated dotenv entries are
ignored. Restart the application after changing configuration. Invalid settings
fail application creation. Templates escape the display name.

Only the display name is passed to templates. Local `.env` files are ignored by
Git and excluded from Docker build context; `.env.example` contains no credentials.
The PostgreSQL password is represented by `SecretStr` in application settings.

## HTTP contract

| Method and path | Success response | Meaning |
| --- | --- | --- |
| `GET /` | `200 text/html` | Server-rendered home page; markup may evolve. |
| `GET /healthz` | `200 application/json`, `{"status":"ok"}` | Process liveness only; no database, queue, storage, or GPU readiness checks. |
| `GET /static/styles.css` | `200 text/css` | Packaged home-page stylesheet. |

FastAPI exposes the generated schema at `/openapi.json`, interactive API docs at
`/docs`, and ReDoc at `/redoc`. The health response has a typed OpenAPI schema;
future additions should preserve its status code and `status` field. This is a
local operational endpoint, not a new cross-component resource API.

The application is single-user and unauthenticated under the existing
[security assumptions](../PLAN.md#45-security).

## Browser assets

The base template loads HTMX 2.0.4 and Alpine.js 3.14.9 from explicit jsDelivr
URLs without npm or a build step. HTMX updates the health result, while Alpine
controls the planned-workflow disclosure. The ordinary health link still works
without JavaScript. CDN availability is needed for the enhancements.

See the upstream [FastAPI template documentation](https://fastapi.tiangolo.com/advanced/templates/),
[HTMX installation guidance](https://htmx.org/docs/#installing), and
[Alpine installation guidance](https://alpinejs.dev/essentials/installation).

## Verification

Run `make setup && make test`, then `make verify`. Tests cover health responses,
templates/static files from a different working directory, HTML escaping,
configuration defaults, dotenv/environment precedence, and invalid configuration.
The unit-test gate requires no services or GPU. CI also runs the disposable
PostgreSQL/RustFS integration suite through `make test-integration`.
Browser JavaScript execution is not covered by the unit suite.

## Local infrastructure

Run `make setup`, then `make up`. Docker Compose v2.20+ is required for `--wait`.
`make up` generates missing credentials, builds the application image, and waits
up to 180 seconds after build/pull for all six services to become healthy.
The image installs the package, including templates/static assets, runs as a
non-root user, and is shared by web, worker, and reconciler. Code changes are
applied with `make up` or `make restart`; there is no source bind mount.

| Service | Image | Persistent volume / container path | Host access |
| --- | --- | --- | --- |
| PostgreSQL | `postgres:18.6-bookworm` | `postgres-data` at `/var/lib/postgresql` | `make db-shell` |
| Valkey | `valkey/valkey:9.1.1-alpine3.24` | `valkey-data` at `/data`, AOF enabled | Compose network only |
| RustFS | `rustfs/rustfs:1.0.0-beta.12` | `rustfs-data` at `/data`, `rustfs-logs` at `/logs` | localhost ports 9000 and 9001 |
| Web | `kitchensoup:local`, Python 3.13 slim Bookworm | none | localhost port 8000 |
| Worker / reconciler | same application image | none | no listener |

RustFS is a prerelease pinned to the tested version. Upgrades should be tested
against retained data before changing image tags. PostgreSQL 18 uses its
versioned data directory beneath `/var/lib/postgresql`. Image tags and Python
dependency ranges are not digest/transitive lockfiles.

PostgreSQL is authoritative for durable application metadata. Run `make migrate`
to create or upgrade its schema; see [the database reference](database.md).
Valkey persistence does not make queue/cache contents authoritative. The one-shot
`storage-init` service provisions the artifact bucket and CORS before web starts.
See [artifact storage](storage.md) for configuration and upload contracts. Queue
dispatch, reconciliation, Docker socket mounts, and training executors remain future work.

`make local-env` creates `.env` from the example when absent and fills missing
or blank `KITCHENSOUP_POSTGRES_PASSWORD`, `RUSTFS_ACCESS_KEY`, and
`RUSTFS_SECRET_KEY` with random local values. It preserves existing values and
never prints them. Keep `.env` with the volumes: changing the PostgreSQL password
in `.env` does not change a database already initialized with another password.
New `.env` files are created with mode 0600 where the filesystem supports it.
RustFS credentials are passed to RustFS, web, and bucket provisioning. Worker
and reconciler do not receive S3 credentials. Provider/executor secret references
remain later work.

PostgreSQL and Valkey have no published ports. All published web/RustFS ports
bind to `127.0.0.1`. To resolve conflicts, change `KITCHENSOUP_PORT`,
`RUSTFS_API_PORT`, or `RUSTFS_CONSOLE_PORT` in `.env`. This is a trusted local
stack, with no application authentication and no Valkey password.

### Startup and health

Compose waits for `pg_isready`, Valkey PING, and RustFS `/health/ready` before
starting application processes. Each application process then performs its own
PostgreSQL authenticated `SELECT 1`, Valkey PING, and RustFS readiness HTTP GET.
A failed check prevents startup with a service-specific error that omits raw
connection details. Network/query timeouts are three seconds per operation;
Valkey retries are disabled. Connections close after probing.

`KITCHENSOUP_CHECK_DEPENDENCIES` defaults to false for the service-free shell and
unit tests and is explicitly true in Compose. Other application probe settings
are `KITCHENSOUP_POSTGRES_HOST` (postgres), `KITCHENSOUP_POSTGRES_PORT` (5432),
`KITCHENSOUP_POSTGRES_DB` and `KITCHENSOUP_POSTGRES_USER` (kitchensoup),
`KITCHENSOUP_VALKEY_URL` (redis://valkey:6379/0), and
`KITCHENSOUP_RUSTFS_HEALTH_URL` (http://rustfs:9000/health/ready). These are
application settings; customize the Compose environment mapping when needed.

`/healthz` retains its process-liveness contract. Startup success does not imply
continuous dependency readiness. `make check-dependencies` re-runs the probes
inside web. RustFS readiness verifies HTTP service availability, not S3
credential authorization or object read/write capability.

Worker and reconciler are explicitly idle placeholders. After successful
startup checks they update an ephemeral heartbeat every five seconds; their
container probes reject a heartbeat older than twenty seconds. SIGTERM/SIGINT
stops them cleanly. They neither consume jobs nor mutate application state.

### Operations and verification

- `make compose-check` validates Compose without printing expanded credentials.
- `make ps` shows health and ports; `make logs` follows the last 100 log lines.
- `make shell` opens a web shell; `make db-shell` opens psql in PostgreSQL.
- `make down` removes containers/network and retains named volumes and `.env`.
- `make restart` performs down/up to re-run dependency ordering and startup checks.
- `make clean CONFIRM=1` deletes this Compose project's containers and volumes,
  including database and object data. It keeps `.env` and images. Without the
  confirmation flag it fails before running Docker; it never prunes other projects.

Normal `make verify` remains service-free. CI additionally runs `make compose-check`
and `make test-integration` against isolated PostgreSQL and RustFS containers.
For live validation, run `make up`, `make check-dependencies`, visit `/` and
`/healthz`, run `make restart`, then `make down`. The full stack is not part
of the unit-test gate.

Upstream references: [Compose startup ordering](https://docs.docker.com/compose/how-tos/startup-order/),
[PostgreSQL image](https://hub.docker.com/_/postgres), and
[RustFS release](https://github.com/rustfs/rustfs/releases/tag/1.0.0-beta.12).
