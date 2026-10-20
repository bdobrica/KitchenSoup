# KitchenSoup

KitchenSoup is a self-hosted control plane for guided language-model fine-tuning.
Import conversations/documents, review immutable datasets and Soup training plans,
register verified model artifacts with lineage, compare results, and serve
temporary vLLM playgrounds.

**Status:** first MVP, version 0.1.0. Training and serving support local Docker,
Docker-over-SSH, Kubernetes, SageMaker and Runpod through explicit opt-ins.
[Validation evidence](docs/evidence/) records the hardware and provider checks
actually performed. This is a trusted single-user application without internal
authentication. GPU execution is disabled in ordinary Compose.

## Quick start

Install Python **3.13** with venv, GNU Make, Docker Engine and Compose **v2.20+**.
Use Linux or WSL2. No Node.js or GPU is needed for data preparation.

```sh
make setup
make verify
make migrate
make up
```

Open <http://127.0.0.1:8000>. Missing local credentials are generated into ignored
`.env`; keep it private and retain it with the data volumes. `make down` retains
data; `make clean CONFIRM=1` deletes it. `make help` lists commands.
See [installation and first workflow](docs/installation.md) for configuration,
upgrades and startup details. For RTX 20/T4 GPUs select **FP16**.

## Concepts and guides

KitchenSoup owns orchestration and registry state; Soup and vLLM remain separate
engines. PostgreSQL owns durable metadata, Valkey carries transient wakeups, and
ArtifactStore encapsulates object storage. Model lineage and artifact derivations
are separate. Read [security assumptions](docs/security.md) before network exposure.

- [Architecture](docs/architecture.md), [accepted decisions](docs/adr/), [contracts](docs/contracts/)
- [Executor prerequisites and examples](docs/executors.md), [GPU host workflow](docs/local-training.md)
- [Datasets](docs/datasets.md), [conversations](docs/conversations.md), [documents](docs/documents.md), [dataset versions](docs/dataset-versions.md)
- [Models and lineage](docs/models.md), [license behavior](docs/model-licenses.md)
- [LLM providers](docs/providers.md), [reviewed plans](docs/training-plans.md), [job lifecycle](docs/jobs.md)
- [Evaluation](docs/evaluations.md), [merge/AWQ](docs/representations.md), [playgrounds](docs/deployments.md)
- [Storage](docs/storage.md), [safety and retention](docs/safety-and-retention.md), [OIDCGate perimeter](docs/oidcgate.md)
- [Development](docs/development.md), [all Make targets](docs/commands.md), [contributing](CONTRIBUTING.md)
- [Changelog](CHANGELOG.md), [release process](docs/releases.md), [design](PLAN.md), [milestones](TODO.md)

The browser loads pinned HTMX/Alpine scripts from jsDelivr; enhancements require
internet access. Dependencies are not fully transitively locked.

## License

[Apache-2.0](LICENSE). Models, datasets and runtime dependencies retain their own
terms; see [third-party notices](THIRD_PARTY_NOTICES.md).
