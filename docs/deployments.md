# Temporary local playgrounds

Launch a registered fine-tuned model from its model page, or open `/deployments`.
Choose FP16 for an RTX 2070 SUPER. BF16 requires a GPU with native BF16 support;
there is no automatic precision fallback. vLLM loads the exact reviewed base plus
the registered LoRA adapter, without a merge. Full uploaded model archives are also
supported. Hub-only base registrations need a full archive import first.

## Host setup

```sh
make migrate
make build-vllm
make training-infra
```

Set `KITCHENSOUP_LOCAL_DEPLOYMENT_ENABLED=true` in your local `.env`, then start or
restart the host processes in separate terminals:

```sh
make dev-training
make worker-training
make reconcile-training
```

This explicitly opts the host processes into local Docker serving. Default Compose
web/worker/reconciler containers remain disabled and receive no Docker socket.
`KITCHENSOUP_DEPLOYMENT_IMAGE` defaults to `kitchensoup-vllm:local`; each launch
resolves and retains its immutable local image ID. Build before launching.
`KITCHENSOUP_DEPLOYMENT_WORKSPACE` defaults to the ignored `.local-deployments`.
Use the same stable private workspace, daemon and non-root user for all processes.
The host needs private disk capacity for model preparation (up to 8 GiB for a base,
plus the adapter), Docker GPU support and enough free VRAM. Do not overlap training,
evaluation and serving on a single GPU. The image download is large.

## Lease and lifecycle

A launch returns QUEUED promptly. The worker prepares model bytes and starts vLLM;
the reconciler marks STARTING/RUNNING after readiness. The 30-minute default lease
starts at creation, including preparation. Supported lease durations are 1–1440
minutes. Startup failure or a 15-minute readiness timeout stops the resources and
records FAILED. Confirmed disappearance records LOST; a new launch is an explicit
new attempt. Restart recovery never blindly reruns an uncertain launch.

- **Extend 30 minutes** adds time, capped at one day from now. After keep-running,
  it restores automatic expiry.
- **Keep running until stopped** disables automatic expiry. Stop it explicitly
  when finished to release GPU memory.
- **Stop now** records a durable stop request. Reconciliation removes the matching
  container, private network and serving workspace before recording STOPPED.

Keep a host reconciler running: there is no external TTL controller. Docker
outages retain pending stop requests for retry. In-flight preparation or a bounded
chat holds a deployment claim and may delay stopping; expiry blocks new chat
requests immediately. Closing the tab does not stop a deployment. Lease actions
cannot revive an expired or terminal playground. Artifacts and training workspaces
are retained independently of serving cleanup.

## Chat and API

Chat is non-streaming, greedy and capped at 128 new tokens with a 2048-token total
model context. Shorten or clear an overlong conversation. Each request has at most
32 messages, 8000 characters per message and 16000 total; backend token limits can
be smaller. Chat history lives only in the browser tab, is rendered as text, and
is not saved by KitchenSoup. Private model bytes mount read-only. The serving
container uses a private internal Docker network with no published host ports;
no provider credentials, network downloads or Docker socket enter it. Its bounded
private tmpfs permits native JIT kernel mappings required by vLLM/Triton. The control
plane proxies only the selected deployment's OpenAI chat route and disables backend
request logging. This is the existing trusted-user/network deployment model, not
a multi-user public inference service.

| Method | v1 path | Purpose |
| --- | --- | --- |
| POST | `/api/v1/deployments` | `{model_version_id, artifact_id?, precision?, lease_minutes?}`; 202 |
| GET | `/api/v1/deployments` | List playgrounds |
| GET | `/api/v1/deployments/{id}` | PostgreSQL state and lease |
| POST | `/api/v1/deployments/{id}/extend` | `{minutes: 30}` |
| POST | `/api/v1/deployments/{id}/keep` | Disable automatic expiry |
| POST | `/api/v1/deployments/{id}/stop` | Durable stop; 202 |
| POST | `/api/v1/deployments/{id}/chat/completions` | `{messages: [{role, content}]}` → `{content}` |

The application proxy deliberately exposes a bounded chat subset, not an arbitrary
OpenAI URL/model proxy. Backend vLLM speaks OpenAI-compatible chat. Endpoints and
container IDs persist internally; clients receive no arbitrary target URL. Generated
[OpenAPI](contracts/artifacts-v1.openapi.json) and the
[deployment snapshot schema](contracts/deployment-v1.schema.json) define the v1
contracts. [ADR 0017](adr/0017-temporary-local-serving.md) records lifecycle/security
decisions. Existing AppSpec/training contracts are unchanged.

## Validation

`make verify` covers service-free checks. `make test-integration` uses disposable
PostgreSQL/RustFS/Valkey for lifecycle, leases and persistence. `make test-vllm-gpu`
builds both fixture/serving runtimes and uses a synthetic local Qwen2 model and
untrained LoRA in the real separate serving image; it downloads no model weights
and makes no quality claim. FP16 is the GPU smoke precision. BF16 validation is
optional. See [Milestone 16 evidence](evidence/milestone-16.md).

Upstream compatibility references:
[vLLM 0.10.2 GPU requirements](https://docs.vllm.ai/en/v0.10.2/getting_started/installation/gpu.html),
[vLLM 0.10.2 CUDA platform](https://docs.vllm.ai/en/v0.10.2/api/vllm/platforms/cuda.html).
