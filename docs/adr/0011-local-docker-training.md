# ADR 0011: Explicit local Docker training submission

Status: Accepted

Date: 2026-09-06

## Context

Milestone 12 connects reviewed plans and the offline runner from ADRs 0009–0010
without introducing the queue, worker dispatch or reconciliation of Milestone 13.
The current application is a trusted, single-user local control plane.

## Decision

Use a synchronous `TrainingExecutor` port consistent with the existing synchronous
services and FastAPI threadpool handlers. The local adapter uses fixed Docker CLI
arguments against the local Unix socket. Training infrastructure settings are
operator configuration, never AppSpec fields. Submission accepts only a saved plan
UUID. The host application explicitly opts into Docker access; default Compose web,
provider and ingestion containers receive no socket or new privileges. Later worker
dispatch can reuse this port.

Create an immutable TrainingRun snapshot containing the saved plan UUID, complete
reviewed plan and resolved content-addressed image identity. Retain the plan hash
in the existing SHA-256 column and the deterministic container name in external_id.
Commit these before materialization or Docker creation. Existing relational fields
retain model, dataset and target relationships; no migration is required.

A trusted materializer downloads the exact public Hugging Face commit anonymously,
or verifies and safely extracts the registered model ZIP. Only runner-supported
file types enter the inventory. It obtains the exact registered manifest and
example artifact bytes, verifies hashes and the complete bundle, and retains
run.json before launch. No Hub tokens, storage credentials, URLs or provider state
enter the training container. Anonymous redirects are restricted to HTTPS Hugging
Face/CDN domains and all downloads/extractions are bounded.

Launch the resolved image identity with GPU access, no network, read-only root and
input, private output, non-root host UID, no capabilities/new privileges, and explicit
memory, CPU, PID, tmpfs, shared-memory and per-file limits. Persist launch arguments
and input identity privately. The operator supplies sufficient private workspace
capacity and filesystem quotas for aggregate disk isolation; Docker bind mounts
alone do not enforce a total output quota.

Observe runs on request. Distinguish Docker unavailability from a missing container.
Verify run/input/image labels before manipulating a container. Success requires
exit zero and the matching successful runner manifest with verified required adapter
hashes. CPU validation is not training success. Missing containers never erase
retained terminal state. Cancellation stops only the identified container; cleanup
removes stopped containers and retains inputs, outputs and logs for later artifact
registration/retention. Logs are private, bounded, cursor-based reads rendered as
text. Per-run host locks serialize submission/cancel/cleanup; database row locks
serialize status updates.

## Consequences

Submission materializes inputs synchronously and may take minutes. Cancellation
while preparation holds the host lock asks the caller to retry. Each POST creates
a new attempt; there is no automatic retry or idempotency-key contract yet. Lost
responses or process crashes require inspecting retained runs/containers before
resubmitting. A crash before launch may leave PREPARING with a missing container.
Automatic recovery, durable event transitions, distributed claims, GPU scheduling,
output registration and retention remain subsequent milestones.

Use one application and a stable private workspace against this local Docker daemon.
The opt-in host mode is a development bridge to worker execution, not a remote or
multi-tenant deployment interface. Native-BF16 hardware remains required; no CPU
or FP16 fallback is added to the production executor.
