# Milestone 17 validation

Date: 2026-10-19

Implemented [lazy model representations](../representations.md) and
[ADR 0018](../adr/0018-lazy-model-representations.md).

## Completed checks

- `make verify`: Ruff formatting/lint, strict mypy (128 application/test source
  files), **300 unit tests passed** (10.30 seconds). Includes bounded calibration,
  generated schema drift, Docker isolation, ownership refusal, tampered output
  rejection and idempotent cleanup.
- `sg docker -c 'make test-integration'`: **70 tests passed** (47.90 seconds), using
  disposable PostgreSQL, RustFS and Valkey. Includes fresh migrations, downgrade/
  upgrade/repeated upgrade and ORM drift, concurrent identical submission reuse,
  advisory claims, immutable snapshots, logical model identity, artifact derivations,
  merged-base plan selection, explicit AWQ/default LoRA serving selection, recovery,
  cleanup retries without rewriting committed output, and corrupt-output failure.
- `sg docker -c 'make test-transform-gpu'`: passed real offline FP16 merge,
  AWQ INT4 export, bounded native archive verification, AWQ vLLM readiness/chat
  through the private-network application proxy, and cleanup. Synthetic random
  Qwen2 has hidden size 128, intermediate size 256, one layer and a small native
  tokenizer. A PEFT LoRA adapter has nonzero B weights. Calibration uses explicit
  synthetic text; no external model or calibration dataset was downloaded.
- GPU queried with `nvidia-smi`: **NVIDIA GeForce RTX 2070 SUPER**, compute
  capability **7.5**, **8192 MiB**. FP16 validation satisfies the local hardware
  requirement; BF16 GPU validation was not performed.
- Final changed browser scripts parsed successfully with the V8 JavaScript parser.
  No browser rendering or end-to-end clicking was performed.
- Final diff whitespace check passed.

The real GPU smoke uses a test-only hash-checking artifact source instead of a
retained application database/object store. It calls the production materializer,
transformation executor/runner, output verification and serving executor. Integration
tests separately exercise real database/storage registration with a fake executor.
No claim of numerical model quality, general model/GPU compatibility, production
migration, or complete browser acceptance follows from the synthetic fixture.

## Runtime identity

Digest-pinned CUDA base:

`vllm/vllm-openai:v0.10.2@sha256:df2607b26bdda2875de4832f4d08da0055b4b6e3570347f3a849bcc652771dd6`

Resolved local transformation image used by the passing GPU smoke:

`sha256:539ada73aa4f2c860692f8f3580f8f68f25ae0cddbc3900bdfc67730c0541f3f`

Resolved local serving image:

`sha256:9370f4e32cc288c0f363da49c9106cbd719a1864633e2c133fba914e28305a26`

The pinned transformation image uses Torch 2.8.0+cu128, Transformers 4.51.3,
PEFT 0.17.1 and AutoAWQ 0.2.9. Added/replaced transitive dependencies are generated
in `images/model-transform/requirements.txt`. Its ML dependency closure check passed.
The unused vLLM package is removed from this separate image so serving's newer
Transformers requirement does not constrain AutoAWQ. An inherited unrelated
PyGObject/pycairo OS metadata inconsistency prevents a clean global `pip check`;
no claim of that global check passing is made. The application has no new Python
runtime dependencies. Rebuilding changes content identities as source/attestations
change; submitted jobs retain the resolved image identity.

Synthetic output sizes were **342678 bytes merged FP16** and **101703 bytes AWQ**.
These ZIP sizes are fixture-specific, not a projected compression ratio for real
models. Output checks verified AWQ configuration and packed `qweight` tensors.
The initial AWQ export probe exposed a string-path API requirement, corrected
before the passing smoke. All synthetic containers/workspaces and the serving
private network were removed by the test cleanup paths.

## Operational boundary

No retained application database was migrated, host process restarted, user model
transformed, or user `.env` changed. Setup requires `make migrate`,
`KITCHENSOUP_LOCAL_TRANSFORMATIONS_ENABLED=true` and restarting the opt-in host
processes. Default Compose retains no Docker authority. Avoid overlapping GPU
workloads and provide private workspace capacity/quotas. Calibration is retained
in the immutable local job, not returned in progress views or sent to a provider.
AutoAWQ is archived upstream; runtime upgrades require repeat execution validation.
