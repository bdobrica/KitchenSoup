# Make command reference

Run from the repository root; `make help` displays the current target descriptions.
The virtualenv is `.venv`; `PYTHON=python3` overrides the setup interpreter, which
must still be Python 3.13. `COMPOSE` overrides the Compose command. Build/run GPU
images only when needed: CUDA dependencies are large.

| Command | Purpose |
| --- | --- |
| `make help` | Show available developer commands |
| `make setup` | Create a Python 3.13 virtualenv and install application + dev tools |
| `make fmt` | Format Python code and organize imports |
| `make lint` | Check formatting, lint rules, and types |
| `make test` | Run all service-free tests |
| `make test-unit` | Run unit tests |
| `make verify` | Run the aggregate local and CI gate |
| `make dev` | Serve the application at http://127.0.0.1:8000 |
| `make local-env` | Generate missing development credentials in ignored .env |
| `make up` | Build and start the stack, waiting for healthy services |
| `make down` | Stop the stack, retaining local data volumes |
| `make restart` | Recreate the stack and recheck startup dependencies, retaining data |
| `make logs` | Follow stack logs (Ctrl+C to stop following) |
| `make ps` | Show stack status |
| `make clean` | Delete this stack and its data volumes (requires CONFIRM=1) |
| `make shell` | Open a shell inside the web container |
| `make db-shell` | Open psql inside PostgreSQL |
| `make compose-check` | Validate the Compose configuration without printing credentials |
| `make check-dependencies` | Probe PostgreSQL, Valkey, and RustFS from the web container |
| `make migrate` | Build the application and upgrade the local database to head |
| `make migration` | Generate a reviewed migration (MESSAGE="describe change") |
| `make migration-check` | Detect schema drift between the migrated database and ORM metadata |
| `make test-integration` | Run integration tests using disposable PostgreSQL, RustFS and Valkey |
| `make storage-init` | Configure the local artifact bucket and browser CORS |
| `make openapi` | Regenerate versioned API and catalog schemas |
| `make storage-retention` | Delete expired upload staging after a one-day grace (requires CONFIRM=1) |
| `make catalog-sync` | Synchronize the packaged catalog into the migrated database |
| `make build-soup-ingest` | Build the pinned Soup document CLI image |
| `make test-soup-ingest` | Exercise real Soup document fixtures in its isolated image |
| `make build-soup` | Build the pinned Soup trainer image (large CUDA dependencies) |
| `make test-soup` | Validate the offline trainer bundle and real Soup CLI without a GPU |
| `make build-sagemaker` | Build the thin managed-training adapter from the cached pinned Soup image |
| `make test-sagemaker` | Offline CPU SageMaker filesystem/privilege-drop check (no AWS cost) |
| `make test-sagemaker-gpu` | Local FP16 GPU check of managed-training wrapper (no AWS cost) |
| `make build-sagemaker-vllm` | Build the separately pinned managed-inference image |
| `make test-sagemaker-vllm` | Paid opt-in inference smoke (ARGS="--confirm-paid --target-config /private/target.json") |
| `make test-soup-gpu` | Optional GPU training smoke (PRECISION=bf16 or fp16) (synthetic tiny model) |
| `make gpu-check` | Check Docker GPU access and selected precision (build-soup first) |
| `make test-local-training` | Exercise real Docker lifecycle with offline CPU validation |
| `make test-gpu` | Tiny end-to-end local Docker training smoke (PRECISION=bf16 or fp16) |
| `make training-infra` | Start loopback infrastructure for the opt-in host training processes |
| `make dev-training` | Serve the host application with local Docker training enabled |
| `make worker-training` | Run the host training command worker (separate terminal) |
| `make reconcile-training` | Run the host training reconciler (separate terminal) |
| `make test-evaluation-gpu` | Tiny offline base/LoRA GPU inference smoke (PRECISION=fp16 on RTX 20) |
| `make build-vllm` | Build the separate digest-pinned vLLM playground image |
| `make test-vllm-gpu` | Optional offline FP16 base/LoRA serving and stop smoke |
| `make build-transform` | Build the pinned offline merge/AWQ image |
| `make test-transform-gpu` | Offline FP16 merge and AWQ GPU smoke |
| `make test-runpod-image` | Local SSH/SFTP/direct runner CPU check (build-runpod first; no cloud cost) |
| `make build-runpod` | Build the SSH-capable direct trainer (publish and pin before cloud use) |
| `make test-runpod-gpu` | Opt-in paid FP16 Runpod test (set RUNPOD_TEST_ARGS; see docs/runpod-training.md) |
| `make test-ssh-gpu` | Optional remote FP16 training/serving smoke (set SSH_TEST_ARGS; see docs/ssh-execution.md) |
| `make test-kubernetes-gpu` | Optional FP16 Kubernetes Job smoke (set KUBERNETES_TEST_ARGS; see docs/kubernetes-training.md) |
| `make test-kubernetes-vllm-gpu` | Optional Kubernetes FP16 serving smoke (set KUBERNETES_SERVING_TEST_ARGS) |
| `make build-runpod-vllm` | Build the separately pinned SSH-capable vLLM image (no cloud cost) |
| `make test-runpod-vllm` | Opt-in paid serving smoke (set RUNPOD_VLLM_TEST_ARGS; see docs/runpod-serving.md) |

## Arguments and side effects

`PRECISION=fp16` selects older CUDA hardware; default is `bf16`. `MESSAGE="..."`
is required for a generated migration, which must be reviewed. `CONFIRM=1` is
required for destructive volume/staging cleanup. `DOCKER_BUILD_NETWORK` controls
Runpod image build networking, not runtime network policy.

`up`, `migrate`, `storage-init`, `catalog-sync` and `compose-check` can generate
missing local credentials in ignored `.env`. `up` builds Soup document ingestion;
`down` preserves named volumes; `clean` deletes them. Host workers and remote
resources are outside Compose's shutdown. `test-integration` creates and removes
disposable services rather than using retained development data.

Local GPU tests do not allocate cloud resources. `test-sagemaker` and
`test-sagemaker-gpu` test the managed-training wrapper locally, despite their
names. `test-sagemaker-vllm`, `test-runpod-gpu` and `test-runpod-vllm` are paid
opt-in helpers: review their `--help`, provider setup and cleanup obligations.
They are not dependencies of `verify`. Build images first where a target's help
requires cached prerequisites; `build-sagemaker` and `build-runpod` wrap the cached
Soup image, and `build-runpod-vllm` wraps the local vLLM image.

| Argument variable | Guide |
| --- | --- |
| `SSH_TEST_ARGS` | [SSH smoke](ssh-execution.md) |
| `KUBERNETES_TEST_ARGS` | [Kubernetes training smoke](kubernetes-training.md) |
| `KUBERNETES_SERVING_TEST_ARGS` | [Kubernetes serving smoke](kubernetes-serving.md) |
| `RUNPOD_TEST_ARGS` | [Runpod training smoke](runpod-training.md) |
| `RUNPOD_VLLM_TEST_ARGS` | [Runpod serving smoke](runpod-serving.md) |
| `ARGS` for `test-sagemaker-vllm` | [SageMaker serving smoke](sagemaker-serving.md) |

For initialization, migrations, health and credentials, see [development](development.md).
