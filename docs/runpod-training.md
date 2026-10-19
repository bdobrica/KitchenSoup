# Runpod ephemeral Soup training

[ADR 0024](adr/0024-runpod-training.md) defines ownership, trust and cleanup.
Runpod is opt-in and provisions one directly managed GPU Pod per training run.
No Docker daemon or access to your local RustFS service is needed on the Pod.
Serving is a separate Milestone 26 executor and is currently rejected for this target.

## Prerequisites and image

Use the existing host web/worker/reconciler setup in [jobs](jobs.md), with migrated
PostgreSQL, artifact storage, shared private staging and outbound HTTPS/SSH.
Apply migration `0023_runpod_resources` before enabling Runpod. Build the pinned
Soup trainer first, then its thin SSH-capable variant:

```sh
make build-soup
make build-runpod
make test-runpod-image
# Optional local FP16 GPU check, without any cloud charges:
.venv/bin/python scripts/test_runpod_image.py --gpu
docker tag kitchensoup-runpod-trainer:local quay.io/YOUR_NAMESPACE/kitchensoup:runpod-trainer-m25
docker push quay.io/YOUR_NAMESPACE/kitchensoup:runpod-trainer-m25
```

If Docker's build bridge cannot reach Debian, use
`make build-runpod DOCKER_BUILD_NETWORK=host`. Publish only image layers, never a
checkout, private key or `.env`. Select an accessible **public** registry image
and pin its single linux/amd64 manifest digest (`repository@sha256:…`) in the target.
Private registry authentication is not part of this milestone. Trainer and future
serving images remain separate. The variant installs OpenSSH; root bootstrap is
limited to SSH setup, and training/SFTP run as UID/GID 10001.

## Credentials and trust

Register your public SSH key with Runpod. Keep its unencrypted private key in an
operator-approved secret directory, owned by the process user with private modes.
An API key needs catalog read and Pod read/write access. Set these consistently
for web, worker and reconciler:

```dotenv
KITCHENSOUP_RUNPOD_EXECUTION_ENABLED=true
KITCHENSOUP_RUNPOD_ENV_NAMES=RUNPOD_API_KEY
KITCHENSOUP_RUNPOD_SECRET_DIRECTORY=/run/secrets
KITCHENSOUP_RUNPOD_STAGING_WORKSPACE=.runpod-staging
KITCHENSOUP_RUNPOD_MAX_CONCURRENT_PODS=1
KITCHENSOUP_SSH_SECRET_DIRECTORY=/run/secrets
KITCHENSOUP_SSH_KNOWN_HOSTS=/run/secrets/ssh_known_hosts
```

Export `RUNPOD_API_KEY` to the processes without printing it, or use an API
`file://` reference beneath the Runpod secret directory. An arbitrary entry in
`.env` does not export a process environment variable. The live smoke explicitly
loads the ignored `.env`. SSH `env://` references require the separate
`KITCHENSOUP_SSH_ENV_NAMES` allowlist; private-file checks remain stricter than
bearer-token file resolution. Never paste key values into target JSON.

Choose one explicit host-trust mode:

- `known-hosts` (default): provision the expected public host key through a trusted
  operator channel in the configured file, including `[public-IP]:mapped-port`.
  An unverified `ssh-keyscan` result is not authority. Unknown/mismatched keys fail.
- `provisioned-ephemeral`: the control plane generates a per-allocation host key
  **before** allocation and retains its public half in PostgreSQL. The private
  half goes only to privileged bootstrap over authenticated Runpod HTTPS, never
  to Soup or browser responses. SSH pins the retained public key. No network
  enrollment occurs. Private local staging is removed after confirmed termination;
  provider metadata retains this short-lived bootstrap value until Pod deletion.

Basic Runpod proxied SSH does not support SFTP. The image must start sshd and the
Pod must expose `22/tcp` with a reachable public-IP direct SSH mapping. KitchenSoup
always uses the non-root `runner` login rather than provider-reported root commands.
It rejects private/loopback/link-local provider addresses and does not follow API
redirects or use environment HTTP proxies.

## Configure and submit

Create a Runpod target under **Execution targets**, or POST to
`/api/v1/execution-targets`:

```json
{
  "name": "Bounded Runpod training",
  "kind": "runpod_pod",
  "credential_ref": "env://RUNPOD_API_KEY",
  "configuration": {
    "schema": "kitchensoup.runpod-target/v1",
    "ssh_credential_ref": "file:///run/secrets/id_ed25519",
    "ssh_host_trust": "provisioned-ephemeral",
    "gpu_id": "Tesla V100-PCIE-16GB",
    "allowed_gpu_ids": ["Tesla V100-PCIE-16GB"],
    "gpu_count": 1,
    "min_vram_gb": 8,
    "cloud": "COMMUNITY",
    "data_center_ids": [],
    "disk_gb": 30,
    "training_image": "YOUR_REPOSITORY@sha256:YOUR_64_HEX_DIGEST",
    "max_hourly_price_usd": 0.30,
    "max_runtime_minutes": 15,
    "max_concurrent_pods": 1
  }
}
```

The [generated schema](contracts/runpod-target-v1.schema.json) is authoritative;
the example image digest is a placeholder. Targets are immutable; create a new
target to change settings. Cloud is `COMMUNITY` or `SECURE`. GPU availability,
cloud price, VRAM and optional data-center availability are checked before create;
actual allocation price and actual CUDA/VRAM are checked too. Catalog availability
is advisory and can change. `POST /{id}/test` only reads API/catalog/inventory and
does not allocate or claim a GPU capability/image-pull check.

Choose the target on a saved reviewed plan. The UI shows a fresh GPU compute
estimate before submission. Precision is explicit: Soup 0.74 selects native BF16
where supported and FP16 otherwise. An FP16 plan requires compatible hardware
without native BF16 under this pinned CLI policy; no fallback or silent change
occurs. The local hardware record retains requested/effective precision.

The worker verifies/materializes registered inputs, reserves capacity durably,
creates once, waits up to ten minutes for SSH within the allocation deadline,
honors durable cancellation during that wait, probes the GPU, stages files via
bounded SFTP, verifies transferred input hashes and executes the pinned runner.
Existing job events, bounded logs, cancellation and verified adapter registration
are reused. A successful Pod status alone cannot register a model.

## Cost, recovery and shutdown

The deadline includes startup/staging. Training receives only its remaining time.
Runtime and price ceilings are safeguards, **not a guaranteed billing cap**.
Idle GPUs remain billable; storage and other provider charges add to GPU estimates.
Keep the reconciler running independently of the browser and monitor your provider
balance. A stopped control plane or API outage can delay termination.

All Runpod targets share the operator concurrency ceiling; a target also has its
own ceiling. Uncertain allocations and pending termination consume capacity.
Run outcomes and resource cleanup are separate. The run view exposes
`resource_cleanup` and `resource_warning`, including after terminal success/failure.
Success retains verified local outputs before terminating, allowing registry import
to retry without a GPU. Failed/canceled/expired runs also terminate automatically.

The reconciler verifies ownership and retries deletion until provider inspection
confirms absence. Pod names alone never authorize adoption/deletion. After an
uncertain create it matches the persisted resource UUID, random nonce, image and
settings through complete bounded inventory. It never blindly repeats create.
If no matching Pod is found, capacity stays reserved and an operational warning
remains: investigate in the Runpod console, restore provider access and let the
reconciler resume. Do not delete ownership rows or retry the original attempt.
Clone only after the attempt reaches a terminal state, with cleanup checked first.

## Validation

`make verify` runs service-free API/catalog/security tests and contract drift checks.
`make test-integration` uses disposable local PostgreSQL/RustFS/Valkey to check
reservation concurrency, restart recovery, registration and failure/cleanup paths.
`make test-runpod-image` checks real local SSH/SFTP and CPU validation; it makes no
GPU training claim. `--gpu` checks real local FP16 direct execution.

The paid synthetic smoke requires local loopback infrastructure (`make training-infra`)
and current migrations, a public pinned image, registered SSH public key and the
API key in your ignored `.env`. It uses normal persisted application runs and
ArtifactStore registration; synthetic models/datasets/evidence remain local.

```sh
make test-runpod-gpu RUNPOD_TEST_ARGS='--confirm-paid --image YOUR_REPOSITORY@sha256:DIGEST --budget-usd 3'
```

Defaults allow one Community Cloud V100, at most $0.30/GPU-hour and 15 allocation
minutes. The harness reserves half the declared budget for non-GPU charges and
refuses budgets over $3. It uses `finally` cleanup, confirms provider termination
and leaves durable pending cleanup if access fails. It does not claim a hard
provider billing cap. FP16 is sufficient for acceptance; BF16 live validation is
optional. Never publish keys, user datasets or runtime payloads as test evidence.
For an explicitly reviewed BF16 smoke, select a native-BF16 GPU with
`--precision bf16 --gpu-id 'GPU CATALOG ID'`. `--min-vram-gb` controls the actual
minimum probed memory; allow for driver reservations on small advertised GPUs.
The executor never changes a reviewed plan's precision automatically.

The validated cloud smoke used Secure Cloud and the following settings (availability
and prices can change):

```sh
make test-runpod-gpu RUNPOD_TEST_ARGS='--confirm-paid --precision bf16 --cloud SECURE --gpu-id "NVIDIA B300 SXM6 AC MIG 1g.34gb" --max-hourly-usd 0.50 --min-vram-gb 4 --image quay.io/bdobrica/kitchensoup@sha256:112204d70ed2fc7e88792ceee2d3179d2901c075c79c0389e76d638720ffc561 --budget-usd 3'
```

The 4 GiB minimum is for the tiny synthetic fixture, not a general training sizing
recommendation. Community Cloud attempts exposed no public TCP mapping despite
requesting SSH; those Pods were terminated. A running sshd or basic proxied SSH
does not establish SFTP compatibility. See [validation evidence](evidence/milestone-25.md).

Provider references: [API v2](https://docs.runpod.io/api-reference-v2/overview),
[create Pod](https://docs.runpod.io/api-reference-v2/pods/create-pod),
[full SSH](https://docs.runpod.io/pods/configuration/use-ssh).
