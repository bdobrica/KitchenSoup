# ADR 0017: Temporary local vLLM playgrounds

Status: Accepted

Date: 2026-10-19

## Context

Milestone 16 introduces serving separately from training and paired evaluation.
Registered fine-tunes currently have a LoRA representation; lazy merge remains
Milestone 17. The trusted single-user host workflow already owns explicit Docker
execution and PostgreSQL-authoritative lifecycle reconciliation.

## Decision

Add a synchronous DeploymentExecutor port and an explicitly enabled host-local
Docker implementation. Keep serving in a separate digest-pinned vLLM image.
Use vLLM 0.10.2's V0 engine and XFormers backend to retain FP16 Turing support;
precision is explicit and defaults to FP16, with BF16 available for native hardware.
No Soup internals, ML imports in the control plane or new Python dependencies are
needed. Default Compose processes retain no Docker authority.

A launch selects a registered model and compatible artifact, and commits QUEUED
before preparation. For a registered training output, select its LoRA archive
and retain the exact reviewed base source from its successful local training run.
vLLM serves that explicit base plus named adapter, with no eager merge, adapter
composition or use of adapter-supplied base paths. Full imported model archives
can also be served. Hub-only base registrations without an artifact require a
full archive import; do not invent an artifact or change the existing deployment
artifact foreign-key contract to represent an unmaterialized remote repository.

Persist an immutable versioned deployment snapshot with exact image ID, selected
artifact ID/hash, base source/revision/hash and bounded serving settings, alongside
the model/target/container identity. Web requests create/read state; the worker
materializes verified registered bytes and starts the server. The reconciler
observes readiness, disappearance and failure, and enforces expiry. Scans are
PostgreSQL-authoritative; no new queue or scheduler is introduced. Per-deployment
PostgreSQL advisory claims serialize external operations and release on process
death. Separate row transactions permit lease and stop requests during preparation
or chat. A concurrent stop request wins later observation/start state writes.

States are QUEUED, PREPARING, STARTING, RUNNING, STOP_REQUESTED and terminal
STOPPED, FAILED or LOST. Database guards enforce transitions and immutable input
bindings. Existing legacy deployment rows are not retroactively assigned v1
meaning. A dead PREPARING worker never replays materialization/start: adopt only
a verified healthy server; otherwise stop its verified resources and fail. Daemon
unavailability or identity mismatch leaves state pending rather than claiming a
GPU is released. Remove failed/lost resources before terminalizing. Startup has a
15-minute deadline measured from creation. Explicit relaunch creates a new UUID.

The default lease is 30 minutes, starts at creation and includes preparation.
Extension is explicit, bounded to one day from now, and re-enables expiry after
keep-running. Keep-running explicitly disables automatic expiry until stop or a
new extension. Expired leases cannot be revived by an action. Stop is durable,
idempotent and available before readiness. Both worker and reconciler can enforce
expiry/stop. One bounded in-flight chat/preparation can delay cleanup while holding
the advisory claim; there is no independent wall-clock kill service or GPU scheduler.
Operators keep the reconciler running and avoid overlapping GPU workloads.

Materialize models in a private serving workspace, never copy training data, and
verify artifact hashes before safe extraction. Resolve public base downloads
anonymously at exact commits using the existing bounded allowlist. Use only native
safetensors and causal LoRA; disable remote code and online model loading.
Containers have non-root host UID, read-only root/models, dropped capabilities,
no-new-privileges, bounded RAM/CPU/PIDs/shared memory/tmpfs/logs, and no credentials
or Docker socket. The private tmpfs permits executable mappings for vLLM/Triton
JIT-compiled native kernels; model/root mounts remain read-only and remote code
remains disabled. Each deployment gets an identity-labelled internal bridge network
without external routing, and no published host ports. The host proxy reaches its
private bridge address.
Verify image/ownership labels before container operations and verify internal network
ownership before deletion. Remove verified containers/networks and model workspace
on stop; no training or registered artifact is removed.

The browser sends bounded non-streaming chat to a deployment-specific application
proxy. Only messages cross that boundary; callers cannot provide an endpoint,
model, generation settings, tools, URL or provider credentials. Resolve and validate
the live private Docker network binding for each request, never trust an arbitrary
saved endpoint. Disable environment proxies and redirects, bound response size/time,
force the selected base/adapter name and 128-token deterministic generation, and
hide raw backend errors. Do not persist or log user prompts/responses. Chat history
is tab-local and rendered as text. Serving readiness and endpoint identity are
persisted but raw endpoints are not returned to browser clients. Host access
assumes the same trusted local-user security boundary as the existing application.

## Consequences

A fine-tuned model can be tested without merging. Engine/model compatibility and
GPU capacity still need execution validation; structural inspection is insufficient.
The pinned engine favors supported Turing execution over a floating latest version.
The UI exposes remaining lease, extend, keep and stop. Host web, worker and
reconciler share the workspace, database, object store and local daemon. They must
run under the same non-root operator. Remote/Kubernetes/SageMaker serving, capacity
scheduling, persistent chat history, automatic activity renewal and first-class
multi-user isolation remain later work.
