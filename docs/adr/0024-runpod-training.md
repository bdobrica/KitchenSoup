# ADR 0024: Owned Runpod Pods for direct Soup training

Status: Accepted

Date: 2026-10-19

## Context

Milestone 25 adds an optional GPU provider without SageMaker quota prerequisites.
ADRs 0012, 0014, 0015 and 0019 define reviewed precision, durable dispatch,
verified registration and SSH credential/trust boundaries. Automatic billable
allocation needs durable ownership beyond a deterministic container name.

## Decision

Add the immutable `runpod_pod` target and `kitchensoup.runpod-target/v1` contract.
Keep infrastructure outside AppSpec and retain the existing training protocol,
runner input/output schemas, PostgreSQL job claims and registered artifact lineage.
Runpod serving remains Milestone 26; selecting this target for serving is rejected.
Supersede only ADR 0019's remote Docker requirement for this new adapter: execute
the pinned Soup runner directly in a dedicated SSH-capable container image.

Resolve the API bearer key from an operator-allowlisted environment variable or
approved secret directory. Resolve a separate SSH private-key reference through
ADR 0019's private-file/allowlist rules. Keys never enter AppSpec, wakeups, logs,
browser responses or workload inputs. Use full public-IP SSH/SFTP with strict
operator-provisioned known-hosts trust by default; do not enroll keys discovered
over the network. An explicitly selected `provisioned-ephemeral` mode provisions
trust before allocation: generate a per-resource host key in private control-plane
staging, persist only its public half with ownership, and send the private half
only to the pinned image's privileged bootstrap through the authenticated provider
API. Pin that public key for all SSH connections; never learn it from a connection
or scan. The ephemeral key remains provider metadata until termination, is not a
long-lived operator credential, is unreadable by the workload, and is removed from
local private staging after confirmed deletion. This narrowly supersedes ADR 0019's
prohibition on automatic key provisioning for this explicit mode; its ban on
automatic host-key enrollment and forwarding operator private keys remains.
The image bootstrap installs account public keys and runs sshd as root;
only the existing non-root runner user can log in. No Docker daemon, root SSH,
password login, agent forwarding or nested container is involved. This provider
does not reproduce local Docker network isolation: use only reviewed inert model
formats and the existing offline runner, with no control-plane secrets in the Pod.
For this adapter only, supersede ADR 0010's host-enforced read-only input mounts,
network-disabled container and local Docker resource flags. Runpod owns the outer
container; inputs are verified private SFTP files rather than read-only mounts,
egress remains available, and the non-root runner retains restricted environment,
file-size bounds and process timeout. Provider allocation limits and API termination
bound resource lifetime. This does not claim the local executor's isolation level.

Persist a resource UUID, application owner, immutable create request with random
ownership nonce, target, deadline and cleanup state before the sole create request.
Serialize capacity reservation with a PostgreSQL advisory transaction lock across
all targets. Both the operator-wide and target limits count uncertain allocations
and unconfirmed terminations. Reserve first; never infer uniqueness from Pod names.
Use bounded REST v2 requests; retry safe reads only. After an ambiguous create,
discover Pods through bounded complete pagination and verify name, nonce, resource
UUID and pinned image before deleting; also validate allocation settings before
adopting for execution. An incompatible allocation with verified ownership is
terminated rather than stranded by the compatibility failure. Retain all
verified matching Pod IDs for cleanup. An empty inventory cannot prove a delayed
create will never allocate: keep its reservation and warning, never create again.
Unknown state is not absence. Operator investigation is necessary if ambiguity
cannot be resolved; this deliberately favors cost safety over automatic retries.

Probe actual CUDA/VRAM and ADR 0012 precision before staging. Verify immutable local
inputs and transferred hashes, launch once under a private UUID workspace, collect
bounded logs and manifest-listed outputs, and reuse the common identity/hash
verifier. A running Pod is not training success. Preserve verified output locally
before termination so registry import can resume without GPU capacity. Never send
RustFS credentials or require a Pod-to-RustFS route.

Record application outcome separately from cleanup. Success, failure, cancellation
and allocation timeout request termination. Confirm deletion through provider
inspection before releasing capacity. Reconcile pending cleanup independently of
terminal run state and after restart, using the same per-run claim as dispatch.
The allocation deadline includes startup and transfer; the image runner receives
only the remaining training time. A worker interrupted during preparation follows
ADR 0014's conservative recovery and does not replay launch.
Persist a local launch-intent marker before the sole SSH launch command. Before
that marker, bootstrap cannot have started Soup: preparation failure requests
API termination immediately even when SSH is unavailable. Uncertain allocation
cleanup still holds capacity and retries discovery independently of run outcome.
Read the PostgreSQL cancellation state during bounded image/SSH readiness waiting
so an explicit stop can terminate the Pod without waiting for SSH to work.

## Consequences

Web, workers and reconciler share private staging, database, credential settings
and SSH trust. Disabled Runpod execution never falls back to local execution.
Targets need an accessible digest-pinned SSH image, registered public SSH key,
public networking and explicitly provisioned host trust before paid submission.
Connection tests read the catalog/inventory and never allocate. Estimates cover GPU
compute only; missing/excessive pricing fails closed. Runtime bounds and API price
checks are safeguards, not account-wide billing guarantees. Provider outages or
stopped control-plane processes can delay termination: monitor pending cleanup.

Provider contract: [Runpod REST v2](https://docs.runpod.io/api-reference-v2/overview).
Serving authentication, streaming and bounded deployment leases will be decided
with Milestone 26 rather than changing existing serving behavior here.
