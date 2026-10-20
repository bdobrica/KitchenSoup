# Installation and first workflow

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
