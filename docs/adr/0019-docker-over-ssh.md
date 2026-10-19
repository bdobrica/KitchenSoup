# ADR 0019: Docker-over-SSH targets and SFTP staging

Status: Accepted

Date: 2026-10-19

## Context

PLAN sections 22, 27 and 32 require remote training and serving without installing
an agent or requiring remote access to local object storage. ADRs 0011, 0014 and
0017 establish explicit operator authority, immutable execution inputs and private
Docker execution. Milestone 18 adds a second executor, preserving those contracts.

## Decision

Extend the existing execution-target entity with a versioned
`kitchensoup.ssh-target/v1` configuration. Persist host, port, non-root username,
dedicated absolute workspace and operator-installed image names, plus a credential
reference. Targets are immutable, including deletion, at the application and SQL
boundary; create a new target to change settings. Runs/deployments retain their
existing immutable target foreign key. This binds remote authority without copying
credentials or infrastructure into AppSpec. Existing v1 training input, plan and
deployment snapshots retain their meaning; the legacy `kitchensoup.local-run/v1`
application envelope now supports both Docker adapters. Old local rows still route
locally. Add an optional execution-target ID to creation APIs; omission remains local.

Supersede only the host-local executor restrictions of ADRs 0011/0014/0017.
Reuse training manifest verification, runner isolation, normalized transitions,
PostgreSQL claims/events, registration, serving leases and deterministic identities.
Every external operation resolves its retained target; unsupported or disabled
SSH targets never fall back to local Docker. Lost SSH connectivity is unknown state,
not proof that a container disappeared or a GPU was released. Preparation is never
replayed after uncertainty; adopt a verified container or fail the attempt, then
retry by clone. Clones retain the same target and image identity.

Use Paramiko in the trusted control plane for SSH commands, bounded SFTP reads
and streaming transfers, and SSH direct-tcpip channels. This concrete second
executor justifies the dependency; Soup/vLLM containers gain no SSH library, key,
socket, credentials or agent. Resolve `env://` only from an operator allowlist, and
`file://` only beneath an operator-approved secret directory. File keys must be
private regular files owned by the process user. Support unencrypted Ed25519,
RSA and ECDSA private keys; no password, interactive prompt, SSH agent, implicit
identity search, caller-supplied SSH options or automatic host-key enrollment.
Use only an operator-provisioned known-hosts file and reject unknown/mismatched
keys. References and key material are never forwarded to the GPU runtime.
AWS profiles remain the SageMaker milestone's responsibility.

Materialize and verify model/dataset bytes on the control-plane host using the
existing registered-input materializer. Upload them via SFTP into a new private
remote UUID workspace. Reject symlink ancestors, reused attempt directories and
non-regular inputs. Docker CLI commands use a fixed Unix daemon socket over an
SSH session, with individually shell-quoted argument vectors and no caller command
fragments. Resolve the image ID on that daemon before committing the queued job;
never pull during launch. Run as the remote user's UID/GID with the same GPU,
network, mount and resource restrictions as local execution.

Fetch bounded logs and the bounded runner manifest through SFTP. Download only
normalized manifest-listed adapter files, with confined regular-file, inventory,
size and transfer bounds; apply the common run/input/image/hash verifier and
rehash the inert registration ZIP. Never recursively extract remote output or
trust Docker exit zero alone. Default mode sends no presigned URLs or object-store
credentials; the remote host needs no RustFS route. Direct object transfers remain
explicitly deferred.

Training retains remote inputs/outputs until the explicit stopped-container
cleanup action. Cleanup first retains verified successful adapter files and bounded
logs locally, removes the verified container, then deletes its remote workspace.
Local staging remains for logs, provenance and operator retention. Serving removes
its container, identity-verified internal network and local/remote model workspaces
on explicit stop or expiry. A cleanup failure keeps serving state pending for retry.
No new retention engine or remote filesystem quota is introduced.

Remote serving keeps vLLM on an internal Docker bridge with no published ports.
Inspect and verify container/image/network identity and obtain its private address
for each readiness/chat request. Reach that address through an SSH direct-tcpip
channel; do not accept browser-supplied endpoints or use saved URLs as authority.
Reuse bounded chat messages, forced model/generation settings, response limits and
no persisted chat history. The SSH server must permit forwarding to the bridge.

## Consequences

Web, worker and reconciler share stable private local staging, operator credential
settings and host trust. Default Compose services keep SSH disabled and receive
no keys. SSH target registration/probes are explicit trusted-operator actions,
consistent with the existing single-user deployment boundary; this is not a
multi-tenant arbitrary-host service.

Remote hosts need Linux Docker, NVIDIA Container Toolkit, SSH/SFTP, non-root Docker
access and preloaded pinned runtime images. No KitchenSoup agent or Python package
is installed remotely; runner code remains inside its image. Connection tests
exercise SSH identity, Docker, runtime image resolution and CUDA capability in the
trainer image, avoiding assumptions about host `nvidia-smi` locations.

Paired evaluation and merge/AWQ transformations remain local executors. Registered
SSH training outputs support the existing lineage and local transformations, and
can be deployed to local or SSH playgrounds. Selecting a pre-training evaluation
suite retains its association but remote paired evaluation is explicitly rejected;
use a playground. No GPU scheduler, automatic key provisioning, container image
transfer, remote agent, password authentication or AWS credential parser is added.
