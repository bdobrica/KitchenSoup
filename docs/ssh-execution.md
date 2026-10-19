# Docker-over-SSH execution

Train a saved plan or launch a registered model on an explicitly configured SSH
GPU host. The default transfer mode is SFTP: KitchenSoup verifies and materializes
inputs locally, uploads private files, runs Docker remotely and downloads verified
outputs. The remote host needs no route to local RustFS and no KitchenSoup agent.

## Prepare the hosts

The remote host needs Linux Docker, NVIDIA Container Toolkit, SSH with SFTP and
TCP forwarding, and a non-root user with direct Docker access. Install the pinned
Soup trainer and vLLM images on that daemon first; the target uses the image names
`kitchensoup-soup-trainer:local` and `kitchensoup-vllm:local` by default. Build them
from this checkout with `make build-soup build-vllm`, or load an operator-transferred
image. Each submission resolves and retains the remote content-addressed image ID;
launch uses `--pull never`. No automatic build/pull or host package install occurs.

On the control-plane host, update dependencies with `make setup` and apply
`make migrate`. Configure the **same** settings for host web, worker and reconciler:

```dotenv
KITCHENSOUP_SSH_EXECUTION_ENABLED=true
KITCHENSOUP_SSH_KNOWN_HOSTS=/home/operator/.ssh/known_hosts
KITCHENSOUP_SSH_SECRET_DIRECTORY=/home/operator/.ssh
KITCHENSOUP_SSH_ENV_NAMES=
KITCHENSOUP_SSH_STAGING_WORKSPACE=.ssh-staging
```

Use absolute paths appropriate to your operator account; `~` is not expanded in
configuration. For the supplied loopback setup, substitute `/home/bogdan/.ssh`.
Verify the host's fingerprint through a trusted channel and provision its entry
in the known-hosts file, including `[127.0.0.1]:2222` for the supplied nonstandard
port. Unknown or changed host keys fail closed; KitchenSoup never enrolls keys.

The private key must be an unencrypted Ed25519, RSA or ECDSA key, stored as a regular
file under the approved directory, owned by the host process user and inaccessible
to group/others (for example mode 0600). Alternatively set an approved environment
variable to the multiline private key and list its name in `SSH_ENV_NAMES`; enter
only `env://NAME` in the target. No passwords, SSH agent or implicit key discovery
are used. Keys are resolved at connection time and never stored in normal database
records or copied to containers. Do not paste private keys into the UI.

Keep storage enabled and run the host processes documented in
[local training](local-training.md) and [job lifecycle](jobs.md). The existing
`dev-training`, `worker-training` and `reconcile-training` commands forward SSH
settings from `.env`; restart them after changing settings. SSH-only processes may
use `Settings` with local execution disabled and SSH enabled. Default Compose
processes remain disabled and have no credential mounts.

## Register and select a target

Open **Execution targets** (`/settings/execution-targets`) and create a target:

| Field | Supplied loopback example |
| --- | --- |
| Name | `Loopback GPU` |
| Host | `127.0.0.1` |
| Port | `2222` |
| Username | `bogdan` |
| Credential reference | `file:///home/bogdan/.ssh/id_ed25519` |
| Remote workspace | `/home/bogdan/.kitchensoup-ssh` |

Use a dedicated absolute remote directory without spaces, commas, shell characters
or symlink ancestors. KitchenSoup creates it privately if absent; an existing
workspace must be owned by the SSH user and inaccessible to group/others. Host
names support DNS names and IPv4. Target configuration, names and credential
references are immutable; create a new target for changes. Rotating the key value
at the same approved reference does not change target identity.

Choose **Test SSH, Docker and GPU**. It checks remote UID/GID, Docker access, both
runtime images and CUDA device/capability/native-BF16 support inside the trainer
image. It starts only a short synthetic GPU probe, not training. It does not
benchmark available VRAM, verify SFTP write capacity or promise that a model fits.

Save a training plan as usual and choose the SSH target in **Compute target** before
**Start training**. AppSpec/dataset bytes are unchanged. Status, events, logs,
cancel, retry-by-clone and registered output lineage use the existing run pages.
Omitting the target in API calls preserves local execution. Registered SSH outputs
can be selected for a local or SSH playground, including their later merged/AWQ
representations. Choose the target on the playground launch form. Leases, keep,
extend and stop have the same behavior on both executors.

The SSH user needs access from the remote host to its Docker bridge addresses;
sshd must allow direct-tcpip forwarding. Readiness and chat use that channel.
No vLLM port is published, no public endpoint is returned to the browser, and
prompts/responses are not persisted. An SSH outage leaves cleanup pending; keep
the worker/reconciler and host connectivity available to enforce expiry.

## API and compatibility

The generated [OpenAPI](contracts/artifacts-v1.openapi.json) and
[target schema](contracts/ssh-target-v1.schema.json) define the additive contracts.

| Method/path | Purpose |
| --- | --- |
| `GET /api/v1/execution-targets` | List registered SSH targets |
| `POST /api/v1/execution-targets` | Create immutable `{name, kind: "ssh_docker", credential_ref, configuration}` |
| `GET /api/v1/execution-targets/{id}` | Read target configuration/reference |
| `POST /api/v1/execution-targets/{id}/test` | Run connection/Docker/GPU/image probe |
| `POST /api/v1/training-runs` | Existing body plus optional `execution_target_id` |
| `POST /api/v1/deployments` | Existing body plus optional `execution_target_id` |

Target responses use `Cache-Control: no-store`. Creation requests contain only a
registered target ID; they cannot override SSH commands, credentials, endpoints or
infrastructure. Existing local v1 snapshots and omitted-target requests remain
valid. The historical `kitchensoup.local-run/v1` persisted envelope is shared by
both Docker adapters; runner wire schemas and AppSpec are unchanged.

## Workspace and failure policy

Stable local staging is private and shared by web/worker/reconciler. Remote training
uses `<workspace>/training/<run UUID>`, serving uses
`<workspace>/deployments/<deployment UUID>`. Upload never reuses a run directory.
Unknown preparation after a crash is inspected and adopted only if its identity
matches; otherwise retry with a new cloned run. Connectivity loss is not absence.
Disabling SSH never sends a retained SSH job to local Docker.

Inputs retain existing model/dataset bounds. SFTP upload additionally caps 8192 files
and 9 GiB per attempt; commands cap combined output at 4 MiB/60 seconds. Transfers
have a 60-second I/O timeout and a 30-minute upload-loop/per-output-file deadline.
Successful outputs require a matching manifest (8 MiB), at most 4096 adapter files,
8 GiB total adapter bytes and verified per-file hashes. Logs expose bounded 64 KiB
cursor reads, retaining at most 8 MiB per stream. Remote output is never recursively
trusted or extracted. Provide disk quotas/capacity on both hosts; bind mounts are
not aggregate disk quotas. There is no automatic capacity scheduler.

Training retains remote inputs, outputs and container until **Remove stopped
container**. That action retains bounded logs and successful adapter bytes locally,
removes the verified container and deletes its remote UUID directory. Local staging
and lock files remain under operator retention. Serving stop/expiry removes its
verified container/network and local/remote UUID directory. Partial cleanup retries
without declaring a GPU released early. Shared workspace parent directories remain.

Paired evaluation and merge/AWQ still execute locally. SSH training may retain a
preselected evaluation suite, but paired evaluation rejects that remote run; use
its registered model in a playground. `aws-profile://` belongs to the SageMaker
milestone; direct presigned-object transfer remains a later optional mode.

## Validation

`make verify` covers service-free tests; `make test-integration` uses disposable
PostgreSQL/RustFS/Valkey, including target immutability, routing, registration,
restart, cloning and serving lease cleanup. No GPU is needed for those gates.

The optional smoke uses tiny random Qwen2 weights and synthetic conversations:

```sh
make test-ssh-gpu SSH_TEST_ARGS='--username bogdan --port 2222 --key-ref file:///home/bogdan/.ssh/id_ed25519 --secret-directory /home/bogdan/.ssh --known-hosts /home/bogdan/.ssh/known_hosts --remote-workspace /tmp/kitchensoup-ssh-smoke'
```

Both images must already exist remotely. The smoke runs real FP16 Soup training,
status/log reads, manifest/hash verification, output download, cancellation,
vLLM serving of that trained adapter through SSH, and cleanup. It installs no
agent and downloads no model weights. The object-store stand-in contains only
synthetic archives; separate integration tests cover PostgreSQL/store registration.
See [Milestone 18 evidence](evidence/milestone-18.md) and
[ADR 0019](adr/0019-docker-over-ssh.md). BF16 GPU validation remains optional.
