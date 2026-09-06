# KitchenSoup

KitchenSoup is a lightweight, self-hosted application for guided
language-model fine-tuning. The planned workflow covers preparing datasets,
training through Soup, comparing results, and serving temporary vLLM playgrounds.

**Status:** repository bootstrap. The application currently serves a home page
and a process health endpoint. Infrastructure and training workflows are future
milestones in [TODO.md](TODO.md).

## Quick start

Install Python **3.13** (including `venv`/`ensurepip`) and GNU Make. On Windows,
use WSL. No Node.js, Docker, database, or GPU is required for this milestone.

```sh
make setup
make test
make dev
```

Open <http://127.0.0.1:8000>. Stop the development server with Ctrl+C.
If your Python 3.13 executable has a different name, use
`make setup PYTHON=python3`.

Configuration is optional: copy `.env.example` to `.env` to customize the display
name. Environment variables take precedence over `.env` values.

## Development

- `make help` lists available commands.
- `make fmt` formats Python and organizes imports.
- `make lint` checks formatting, lint rules, and strict types.
- `make test` runs service-free tests; `make test-unit` runs unit tests.
- `make verify` runs the same lint and test gate used by CI.

The home page uses Jinja templates, with version-pinned HTMX and Alpine.js
scripts loaded from jsDelivr. Browser enhancements require internet access;
the server-rendered page and health link work without JavaScript.

## Key concepts and documentation

KitchenSoup owns orchestration and registry state. Soup and vLLM remain separate
engines behind container/CLI boundaries. Planned durable state belongs in
PostgreSQL; Valkey holds transient state, and object storage sits behind an
S3-compatible adapter. Model versions and artifact representations have separate
lineages.

- [Development and bootstrap HTTP contract](docs/development.md)
- [Project design and implementation intent](PLAN.md)
- [Remaining milestones](TODO.md)
- [Repository contribution instructions](AGENTS.md)

## License

[Apache License 2.0](LICENSE). Third-party models, datasets, libraries, and
containers retain their own licenses.
