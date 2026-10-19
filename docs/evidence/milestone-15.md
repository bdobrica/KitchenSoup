# Milestone 15 validation

Date: 2026-10-19

## Delivered scope

- Named 3–10-prompt suites created before training; optional immutable run binding
  and preserved binding on clone. Existing plan/run clients remain compatible.
- Explicit durable evaluation attempts dispatched by the host worker, separately
  from training success. Sequential offline base and registered LoRA generation,
  with verified materialization and FP16/BF16 selection from the reviewed plan.
- Persisted response artifacts and unique run/prompt/model results; duplicate
  requests, per-run advisory claims, partial failure, explicit missing-response
  resume, and interrupted-worker cleanup without automatic GPU replay.
- Side-by-side response text, three human preferences, current counts, downloadable
  content-addressed report snapshots, model-page summary and separate artifact
  derivation edges. New accepted design: [ADR 0016](../adr/0016-local-paired-evaluation.md).
- Generated relational migration and additive API/inference/report schemas; separate
  hand-authored PostgreSQL guards. No new runtime dependency or changes to existing
  AppSpec, resolved-plan, recipe or training-runner wire schemas.

## Verification performed

| Check | Result |
| --- | --- |
| `make fmt`, `make openapi` | Completed; contracts regenerated from source |
| `make verify` | Ruff format/lint passed; strict mypy passed for 111 source files; 285 unit tests passed |
| `sg docker -c 'make test-integration'` | 64 passed against disposable PostgreSQL, RustFS and Valkey |
| Migration round trips/idempotence/drift | Passed within integration gate, including baseline upgrade checks |
| Four modified/new browser scripts parsed with V8 `new Function` | Passed; syntax only |
| `sg docker -c 'make test-evaluation-gpu PRECISION=fp16'` | Passed on NVIDIA GeForce RTX 2070 SUPER, compute capability 7.5, 8192 MiB |
| `git diff --check` | Passed |

GPU target rebuilt the cached pinned trainer runtime and exercised the real local
inference launcher twice, including production network isolation, GPU arguments,
non-root UID, read-only mounts, resource limits and verified container cleanup.
A tiny locally generated random Qwen2 and synthetic **untrained** causal LoRA were
used. Both base and adapter paths returned validated responses bound to the exact
inference-input hashes. No model weights were downloaded and no provider was called.
The final runtime image identity was:

```text
sha256:ac96fdca384667438354f2c928b265edd03bdacc689e6a80b462edc627e04440
```

Integration exercises suite/prompt bounds and read APIs, run association, early
execution rejection, duplicate create reuse, competing advisory claims, response
pairs, three preferences/counts, report provenance and retained old bytes,
content-addressed report reuse, unsupported preference/missing prompt errors,
completed-response reuse, partial failure and explicit resume, worker restart
cleanup, disabled local inference and direct-SQL suite/response/run immutability
and prompt membership guards. These tests inject synthetic generation while using
real persistence/storage; the separate GPU target covers actual inference.
Unit checks include sanitized materialization failures, prompt limits, adapter
tampering and symlinks, unlisted adapter
rejection and generated inference/report schema drift. Existing training, registry,
queue, provider, ingestion and storage tests also passed.

## Validation boundaries

No BF16 GPU validation was required or performed. No new Soup fine-tune, quality
benchmark, real browser rendering/E2E test, vLLM deployment, merge, external provider
call or retained application database migration was performed. Synthetic adapter
inference proves the local loading/generation path; it does not demonstrate model
improvement. Existing FP16 training evidence is retained separately.

Operator setup: apply `make migrate`, keep the updated `make build-soup` image,
restart host processes, create a suite at `/evaluations` and select it on a saved
plan before training. Existing run/suite/image snapshots cannot be changed
retroactively. See [evaluation workflow](../evaluations.md).
