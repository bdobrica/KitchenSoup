# Local Docker training

The local executor submits a saved [training plan](training-plans.md), materializes
its registered inputs and runs the pinned [Soup image](soup-training.md). It supports
status, private stdout/stderr, cancellation and stopped-container cleanup. Current
recipes require a CUDA GPU with native BF16; the development GTX 1660 Ti is not
compatible. SageMaker execution is a later adapter.

## Start the host application

The default Compose application keeps Docker access disabled. For local training,
use a Linux/WSL host with Docker CLI and `/var/run/docker.sock`, Python 3.13 and a
non-root user able to access Docker. Keep this single-user application on loopback.

```sh
make setup                  # if the virtualenv does not exist
make migrate
make storage-init
make catalog-sync
make build-soup
make gpu-check
make training-infra         # adds loopback PostgreSQL port 5432; retains volumes
# If the Compose web service is running, free its port first:
docker compose stop web
make dev-training           # http://127.0.0.1:8000
```

`dev-training` reads existing `.env` credentials without displaying them and uses
local PostgreSQL/RustFS ports 5432/9000. The optional
`docker-compose.training.yml` only adds loopback database access. Adjust these
operator settings if local ports differ. The host mode does not expose the private
Soup document-ingestion sidecar; prepare document versions through the regular
Compose application first. Conversation upload/versioning works in host mode.

Register an uploaded model or public ungated model with an exact commit, prepare
a dataset version, save a reviewed plan, then choose **Start local training**.
Follow the resulting run page for status and logs. **Cancel run** stops its
container. **Remove stopped container** retains the local inputs, outputs and logs.
Runs are also accessible at `/training-runs` after a failed or interrupted request.

Environment settings (prefix `KITCHENSOUP_`):

| Setting | Default | Purpose |
| --- | --- | --- |
| `LOCAL_TRAINING_ENABLED` | false | Explicitly enable local Docker submission |
| `TRAINING_WORKSPACE` | `.local-training` | Stable private directory on the Docker host |
| `TRAINING_IMAGE` | `kitchensoup-soup-trainer:local` | Operator-built pinned runner; resolved to image ID |
| `TRAINING_MEMORY_GIB` | 16 | Container RAM and combined RAM/swap limit |
| `TRAINING_CPUS` | 4 | CPU quota |
| `TRAINING_TIMEOUT` | 86400 | Soup CLI wall timeout in seconds |

Use a filesystem private to the operator, with sufficient free space and an
aggregate quota where needed. Model materialization is capped at 8 GiB, Hub metadata
at 4 MiB, 4096 files and 30 minutes; archive extraction also has existing ratio/time
limits. Dataset limits remain 8 MiB manifest and 64 MiB examples. Each output file
has a 1 GiB hard limit; logs retain 8 MiB per stream. A bind mount does not impose
a total disk quota. Inputs and outputs persist until the operator's retention
workflow removes them. Do not place workspaces in source control or a shared folder.

The container receives `--gpus all`, no network or secrets, read-only input/root,
non-root host UID, dropped capabilities, 256 PIDs, bounded tmpfs/shared memory and
no new privileges. `launch.json` binds image/input/plan hashes; `command.json`
retains launch arguments. Model uploads are hash-verified, inspected and safely
extracted; unsupported auxiliary files are omitted. Public Hub downloads use only
the saved exact revision, fixed endpoints and allowlisted HTTPS redirects, with no
implicit local credentials or proxies. The runner rechecks the complete bundle.

## API and persistence

The additive routes are in the generated [v1 OpenAPI](contracts/artifacts-v1.openapi.json).
Existing AppSpec, plan and runner schemas are unchanged.

| Method/path | Behavior |
| --- | --- |
| `POST /api/v1/training-runs` | `{ "plan_id": "UUID" }`; creates a new attempt, returns 201 |
| `GET /api/v1/training-runs` | Retained run summaries, newest first |
| `GET /api/v1/training-runs/{id}` | Observe Docker and update retained state |
| `GET /api/v1/training-runs/{id}/logs?stream=stdout&cursor=0` | Up to 64 KiB and next byte cursor; stderr also supported |
| `POST /api/v1/training-runs/{id}/cancel` | Stop the identified container; repeatable |
| `DELETE /api/v1/training-runs/{id}/container` | Remove stopped container; 204; retain workspace |

Responses use `Cache-Control: no-store`; disabled/unavailable executor returns 503.
A confirmed preparation/launch failure after creating the row returns its FAILED
run with a safe initial observation. Ambiguous launch errors are inspected first;
Docker unavailability leaves PREPARING for later observation. Refresh may only show generic failure/absence diagnostics.
No raw Docker/Hub errors are returned. Logs can contain training diagnostics and
are available only through the trusted local application's run API.

`TrainingRun.resolved_config` uses the internal `kitchensoup.local-run/v1` envelope:
`plan_id`, `image_digest`, and complete `plan` (PlanPreview shape). The run retains
the plan SHA-256, immutable AppSpec and relational model/dataset/target IDs. The
external ID is `kitchensoup-training-{run UUID}`, committed before Docker effects.
Launch metadata remains in the stable private workspace. No table/migration changes.

States currently observed are PREPARING, RUNNING, SUCCEEDED, FAILED, CANCELED and
MISSING. MISSING is an observation, not a replacement for retained state. A stopped
exit-zero container is FAILED unless the matching successful manifest and required
adapter hashes verify. Validation-only output is never SUCCEEDED. Output
registration and durable state-machine/event semantics are later milestones.

Submission is synchronous and holds a per-run lock during materialization; retry
cancel after preparation finishes. Repeated submission creates another run. If a
request is lost, inspect the run list before retrying. There is no automatic queue,
reconciler or GPU allocation yet. A process crash can leave a PREPARING run without
a container. Keep the workspace/image stable across restarts and retain output
before cleaning up host files. Do not run competing applications against this target.

## Verification

```sh
make verify
make test-integration
make test-local-training    # real Docker lifecycle + Soup CPU validation
make gpu-check              # CUDA runtime and native BF16; explicit failure if absent
make test-gpu               # actual tiny BF16 run through the production executor
```

The smoke builds a tiny random Qwen2 and synthetic conversations locally; it
never downloads model weights or contacts a paid provider. CPU mode changes GPU
arguments only inside the test harness and requests runner validation; production
submission has no CPU/validation bypass. It verifies launch, logs, cancellation,
cleanup and retained files. GPU mode uses production launch arguments unchanged
and verifies the success manifest and adapter bytes. Neither is a quality benchmark.

See [ADR 0011](adr/0011-local-docker-training.md),
[Docker launch options](https://docs.docker.com/reference/cli/docker/container/run/)
and [Hub API](https://huggingface.co/docs/hub/api).
