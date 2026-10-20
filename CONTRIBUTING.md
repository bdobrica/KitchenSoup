# Contributing

Read [AGENTS.md](AGENTS.md), relevant accepted [ADRs](docs/adr/), affected
[contracts](docs/contracts/) and security references before changing behavior.
Keep changes focused. Introduce a superseding ADR for architectural changes;
do not rewrite accepted decisions. Preserve existing published schemas unless an
accepted decision explicitly permits a breaking change.

Use Python 3.13 and `make setup`. Run focused tests, then `make fmt` and
`make verify`. Database/storage/lifecycle changes also need `make test-integration`
with Docker; schema changes need reviewed Alembic revisions, migration round trips
and drift checks. Regenerate contracts with `make openapi` rather than editing JSON.
Include compatibility tests and documentation for public API changes. See the
[command reference](docs/commands.md).

Do not add live cloud tests to the default gate. Paid tests require explicit
operator opt-in, bounded resources, and independent confirmed cleanup. Use mocked
providers and synthetic fixtures for normal tests; never commit keys, kubeconfigs,
private journals, runtime payloads, personal data or actual account identifiers.
New dependencies need a concrete repository-local justification. Generated image
locks are updated through the scripts named in their headers.

Describe the user-visible problem, resulting behavior, validation and material
limitations in pull requests. Update sequencing in PLAN/TODO when needed; store
validation evidence in `docs/evidence/`. Keep README concise. Use `docs`, `feat`,
`build`, `fix` or `ci` commit prefixes. Contributions to this repository are under
its [Apache-2.0 license](LICENSE); retain relevant upstream notices. Release steps
are in [the release guide](docs/releases.md).
