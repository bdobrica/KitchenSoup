# Milestone 16 validation

Date: 2026-10-19

## Delivered scope

- Separate digest-pinned vLLM 0.10.2 image and `make build-vllm`; explicitly enabled
  host Docker DeploymentExecutor, independent of trainer/evaluation authority.
- Registered full model archive or trained causal LoRA plus exact reviewed base;
  bounded verified materialization without training data, credentials or merge.
- Durable queued launch, normalized states, immutable v1 snapshot, endpoint/container
  identity, worker dispatch, readiness/restart/lost recovery and lease reconciliation.
- Explicit extend, keep-running and stop actions; expiry blocks new chat and removes
  verified resources before terminalization. Daemon/cleanup failure remains retryable.
- Playground UI and deployment-specific bounded chat proxy, model-page launch link,
  private internal Docker network, no published host port, disabled request logging,
  text rendering and tab-local conversation history.
- Generated relational migration and additive API/snapshot schemas, separate SQL
  guards, setup/reference documentation and [ADR 0017](../adr/0017-temporary-local-serving.md).
  No new application dependencies or changes to existing training wire contracts.

## Verification performed

| Check | Result |
| --- | --- |
| `make fmt`, `make openapi` | Completed; contracts generated from source |
| `make verify` | Ruff format/lint and strict mypy (120 source files) passed; 291 unit tests passed |
| `sg docker -c 'make test-integration'` | 67 passed using disposable PostgreSQL/RustFS/Valkey |
| Migration round trips, repeated upgrade and schema drift | Passed within integration gate |
| New/modified playground and model scripts parsed with V8 `new Function` | Passed; syntax only |
| `sg docker -c 'make test-vllm-gpu'` | FP16 base and named LoRA inference passed on NVIDIA GeForce RTX 2070 SUPER, compute capability 7.5 |
| `git diff --check` | Passed |

The GPU smoke generated a tiny random native Qwen2 and **untrained** causal LoRA
locally in the existing trainer fixture runtime. It exercised production serving
materialization/safe extraction, native safetensors loading, isolated GPU Docker
launch, actual vLLM readiness and OpenAI-compatible requests, bounded application
proxy generation and verified stop/network/workspace cleanup, sequentially for
base and tuned paths. No model weights were downloaded and no provider was called.
The generic synthetic tokenizer uses its stable native Transformers class to be
readable by both pinned runtimes. This is execution evidence, not model-quality
validation or a new Soup fine-tune.

Upstream linux/amd64 image manifest:

```text
sha256:df2607b26bdda2875de4832f4d08da0055b4b6e3570347f3a849bcc652771dd6
```

Tested local serving image identity:

```text
sha256:0df62a128f64bd9979a576855ef2e2d3fd1af0cd0f29fc3dae4f8c77e2e7bff5
```

GPU validation exposed and resolved internal-network endpoint discovery, non-root
runtime cache setup, the synthetic fixture's separate chat-template packaging and
executable mappings for native Triton LoRA kernels. The final launcher uses a
private internal bridge with no published port and a bounded executable tmpfs;
root/models remain read-only. It does not enable remote model code.

Integration checks cover queued API creation without GPU work, claims/duplicate
worker scans, service restart, readiness and persisted endpoint state, base/adapter
artifact selection, invalid/foreign artifact rejection, full uploaded models,
Hub-only base rejection, chat gating/forwarding, bounded requests, lease extension,
keep-running, restoration of expiry, expired lease rejection, stop before start,
automatic expiry, daemon stop retry, failed launch, uncertain-worker recovery,
confirmed loss, disabled executor, and direct-SQL snapshot/terminal-state guards.
Unit tests check Docker authority/arguments, internal endpoint confinement,
container identity mismatch, daemon absence versus outage, cleanup idempotence,
forced selected model/generation limits, request bounds and generated schema drift.
Existing registry, training, evaluation, provider, storage and ingestion gates pass.

## Validation boundaries

BF16 GPU validation remains optional and was not performed. Automatic expiry is
validated through the real persistence/service lifecycle with an injected executor;
real GPU container stop/cleanup is exercised separately by the GPU smoke. No browser
rendering/end-to-end interaction test, retained application database migration,
host-process restart, real user model deployment, external provider check or
production deployment was performed. The host must retain its worker/reconciler
and database/Docker connectivity for lease enforcement; active bounded operations
can delay cleanup. No GPU capacity scheduler or independent TTL daemon is added.

To enable the delivered workflow, migrate the application database, set
`KITCHENSOUP_LOCAL_DEPLOYMENT_ENABLED=true`, restart the shared host processes and
open `/deployments`. See [setup and lease behavior](../deployments.md).
