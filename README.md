# KitchenSoup

KitchenSoup is a lightweight, self-hosted application for guided
language-model fine-tuning. The planned workflow covers preparing datasets,
training through Soup, comparing results, and serving temporary vLLM playgrounds.

**Status:** model catalog and registry. Choose a curated Qwen model, register an
ungated Hugging Face revision, or upload a compatible model ZIP. Sources and
licenses are retained alongside verified artifacts in PostgreSQL and RustFS.
Worker/reconciler processes remain idle placeholders. Ingestion and training
workflows remain in [TODO.md](TODO.md).

## Quick start

Install Python **3.13** (including `venv`/`ensurepip`) and GNU Make. On Windows,
use WSL. Install Docker Engine and Docker Compose v2.20+ for the local stack.
No Node.js toolchain or GPU is required.

```sh
make setup
make test
make migrate
make up
```

Open <http://127.0.0.1:8000> and choose **Models** to browse or import a model.
Stop the stack with `make down`; data volumes are retained. `make restart` recreates the stack with its existing data.
If your Python 3.13 executable has a different name, use
`make setup PYTHON=python3` and `make up PYTHON=python3`.

`make up` generates missing local credentials in ignored `.env`. Existing
values are preserved. Edit that file to change the display name or host ports;
environment variables take precedence. RustFS exposes its console at
<http://127.0.0.1:9001>, using the generated RustFS credentials in `.env`.

For the service-free web shell, use `make dev` and stop it with Ctrl+C. Run it
separately from the Compose web service to avoid a port conflict.

## Development

- `make help` lists available commands.
- `make ps` shows service health; `make logs` follows logs.
- `make shell` opens the web container; `make db-shell` opens psql.
- `make check-dependencies` checks live service connections.
- `make migrate` applies migrations; `make migration MESSAGE="..."` generates one.
- `make migration-check` detects schema drift.
- `make test-integration` tests persistence and storage using disposable PostgreSQL/RustFS containers.
- `make clean CONFIRM=1` deletes the stack and its data volumes.
- `make fmt` formats Python and organizes imports.
- `make lint` checks formatting, lint rules, and strict types.
- `make test` runs service-free tests; `make test-unit` runs unit tests.
- `make verify` runs the same lint and test gate used by CI.

The home page uses Jinja templates, with version-pinned HTMX and Alpine.js
scripts loaded from jsDelivr. Browser enhancements require internet access;
the server-rendered page and health link work without JavaScript.

## Key concepts and documentation

KitchenSoup owns orchestration and registry state. Soup and vLLM remain separate
engines behind container/CLI boundaries. Durable metadata belongs in
PostgreSQL; Valkey holds transient state, and object storage sits behind an
S3-compatible adapter. Model versions and artifact representations have separate
lineages.

- [Development, local infrastructure, and HTTP contract](docs/development.md)
- [Model catalog, registration, and archive requirements](docs/models.md)
- [Artifact storage and upload API](docs/storage.md)
- [Database schema and migration workflow](docs/database.md)
- [Project design and implementation intent](PLAN.md)
- [Remaining milestones](TODO.md)
- [Repository contribution instructions](AGENTS.md)

## License

[Apache License 2.0](LICENSE). Third-party models, datasets, libraries, and
containers retain their own licenses.
