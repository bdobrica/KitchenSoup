# Bootstrap development reference

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
its templates and static assets. None of these dependencies runs training or
connects to other ThinkPixel components.

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

Only the display name is passed to templates. Provider credentials and service
configuration will be introduced with their respective milestones. Local `.env`
files are ignored by Git; `.env.example` contains no credentials.

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
CI uses the same commands on Python 3.13 and requires no external services or GPU.
Browser JavaScript execution is not covered by the unit suite.
