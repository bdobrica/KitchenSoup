# ADR 0025: Authenticated Runpod playgrounds with bounded leases

Status: Accepted

Date: 2026-10-19

## Context

Milestone 26 extends ADR 0017 serving and ADR 0024 resource ownership to public
Runpod HTTP proxy endpoints. An obscure Pod URL is not authentication. Paid idle
resources need bounded leases and restart-safe termination.

## Decision

Supersede ADR 0024's training-only restriction for separately pinned SSH-capable
vLLM images. Reuse registered model materialization, immutable deployment snapshots,
SSH trust, PostgreSQL claims, capacity reservations and verified provider ownership.
Keep model preparation before allocation where possible. Support the existing native
Qwen2/Llama/Mistral safetensors, exact-base LoRA, merged and validated AWQ formats;
Hub-only registrations still require an archive. Retain the existing vLLM version
and explicit precision, with actual CUDA/VRAM validation. Training and serving use
dedicated Pods and different images under the shared capacity limit.

For this adapter, supersede ADR 0017's private-Docker-network restriction with the
public HTTPS proxy on fixed port 8000. Derive the URL only from a freshly validated
owned Pod identifier; saved endpoints are observation metadata. Disable environment
proxies and redirects. Generate a random per-deployment inference credential in
private control-plane staging, transfer it by authenticated SFTP and pass it to
vLLM via its environment rather than command arguments. No provider API key or
operator private SSH key enters the Pod. Credentials never enter snapshots, queue
payloads, browser responses, logs or evidence. An image-owned ASGI middleware protects every HTTP route, including non-`/v1`
auxiliary routes omitted by the pinned engine's native authentication.
Startup checks require anonymous
inference rejection and authenticated selected-model discovery before readiness.
The SSH bootstrap remains privileged; serving runs as runner without remote code,
with offline model loading and verified private files. As with ADR 0024, this is
not the host-enforced filesystem/network isolation of the local executor.

Preserve existing non-streaming chat. Add an optional `stream: true` to the v1
request, returning OpenAI-style SSE content deltas and a DONE marker. Only bounded
messages cross the application boundary; force model, greedy generation and the
existing 128-token limit. Bound bytes, content, idle and total request duration
below the provider's documented 100-second proxy limit. Close upstream generators
and release claims on disconnect. Do not log or persist prompts/responses. Other
executors can return their bounded result as one stream delta without changing
non-streaming behavior. Application authentication remains the existing trusted
operator/perimeter boundary; this does not introduce multi-user authorization.

Narrowly supersede ADR 0017's indefinite keep-running behavior only for Runpod.
The target's maximum lease is an absolute limit measured from deployment creation,
including preparation. Extension cannot pass that limit; keep-running is rejected.
The provider resource retains that absolute deadline, while the application may
choose a shorter lease. The direct serving supervisor also bounds process runtime,
but only provider termination releases billing. Stop, expiry, failed startup and
unrecoverable endpoint failures request termination. Unknown API state and cleanup
failure remain pending, consume capacity and are retried after restart; never
report termination before provider absence is confirmed. Never replay provisioning
or launch after uncertain worker death. Raw endpoints and credentials remain hidden.

## Consequences

Operators must keep the reconciler running and protect the trusted application
boundary. Idle Pods remain billable; estimates exclude storage and other charges
and are not account-wide spending guarantees. Live tests require explicit opt-in,
a small compatible fixture, cost/runtime ceilings and confirmed resource cleanup.

References: [Runpod proxy security and timeouts](https://docs.runpod.io/pods/configuration/expose-ports),
[vLLM 0.10.2 server](https://docs.vllm.ai/en/v0.10.2/serving/openai_compatible_server.html).
