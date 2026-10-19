# Milestone 26 validation

Date: 2026-10-19

## Implementation

Runpod serving uses the existing deployment protocol and registered artifact
resolver/materializer. It shares Runpod REST v2, SSH trust, durable provisioning,
capacity reservations and confirmed termination with training, without sharing
Pods or images. No database migration is needed: the existing owned-resource
schema already supports deployment ownership.

The additive target configuration retains old training-only targets. Serving has
independent GPU/VRAM/disk/image settings and an absolute maximum lease from creation.
The existing non-streaming JSON API remains compatible; `stream: true` adds bounded
OpenAI-style SSE. Browser clients receive no backend endpoint or credentials.
The serving image protects all HTTP routes, including auxiliary non-`/v1` routes
which the pinned vLLM native API-key middleware does not protect. The engine key
comes from a private SFTP file/environment, not arguments or a database snapshot.

Restart/failure checks cover uncertain allocation without duplicate creation,
shared reserved capacity, interrupted launch, transfer failure, cancellation during
preparation, actual hardware rejection, public authentication rejection, endpoint
failure, lease expiry, stop, pending termination and terminal-resource cleanup.
Private bounded credential-redacted startup logs survive successful Pod deletion.

See [ADR 0025](../adr/0025-runpod-serving.md) and
[operator reference](../runpod-serving.md).

## Local GPU validation

```sh
make build-runpod-vllm
.venv/bin/python scripts/test_runpod_image.py --serving --gpu
```

Passed on the local NVIDIA RTX 2070 SUPER with FP16, using a synthetic native Qwen2
model. Verified non-root SSH identity, authenticated SFTP, input hash verification,
direct vLLM execution, anonymous API/auxiliary-route rejection, authenticated JSON
chat and streaming completion. The temporary local container and keys were removed.
No model quality is implied and no external model weights were downloaded.

The first local attempt exposed a fixture context mismatch (256 vs the default
2048 tokens). Serving now retains a resolved context capped at the model's declared
maximum. The final local run also verified the all-route authentication middleware.

## Repository verification

- `make verify`: 441 unit tests passed; Ruff formatting/lint passed (294 files),
  strict mypy passed (163 source files).
- `make test-integration`: all 115 tests passed using disposable PostgreSQL,
  RustFS and Valkey, including migration round trips and schema drift checks.
- `make openapi`: regenerated additive API/target contracts; contract drift passed.
- Final readiness regressions cover proxy 404/429/5xx during loading, anonymous
  success rejection, selected adapter readiness and delayed healthy startup.
- Final diff/credential scan is recorded with the commit review.

Browser automation was unavailable because Node is not installed; no browser
automation or JavaScript runtime check is claimed. API/template checks, code review
and real application-proxy inference cover the implemented workflow.

## Published image

Public repository: `quay.io/bdobrica/kitchensoup:runpod-vllm-m26`.
The final all-route-authenticated image is pinned as:

```text
quay.io/bdobrica/kitchensoup@sha256:c1c5cd437ce4b89268a4d7e65791564fba3cbfda158f0ff778a4b25bf26c4f71
```

Anonymous manifest access and exact digest were verified. Compressed layers total
11,468,146,258 bytes (approximately 34 GB unpacked). This is separate from the
Milestone 25 Soup trainer image; no credentials entered either build context.
The published image is intentionally retained for operator use.

## Live provider validation

The user authorized publication to the repository and up to $3 total for this
milestone. Initial read-only inventory showed zero Pods. No AWS resources were
created or queried.

The final opt-in smoke passed through the real application lifecycle and FastAPI
playground proxy with an uploaded synthetic native Qwen2 archive and real
PostgreSQL/ArtifactStore services. No provider, SSH, model execution or inference
response was mocked in the successful run.

- Deployment: `9a9a3fa1-2ad5-433f-b1c1-69d28eb9c62b`.
- Pod: `sk0igdt38w132k`.
- Registered model version: `db739ada-7413-4343-af3f-eb7201d14447`; selected
  artifact: `1c9b9424-12df-4b4a-a72d-fc3ec7f5e635`.
- GPU: Secure Cloud `NVIDIA RTX PRO 6000 Blackwell Server Edition MIG 1g.24gb`,
  quoted at $0.69/hour; observed placement `EUR-IS-2`.
- Hardware: CUDA available, one GPU, 23.625 GiB VRAM, native BF16, compute 12.0.
- Requested/effective serving dtype: BF16 / `bfloat16`; resolved context 256 tokens.
- Maximum lease: 30 minutes from creation; serving bootstrap at most the existing
  15-minute startup allowance. Actual deployment lifetime was about ten minutes.
- Final application state: `STOPPED`; resource `RELEASED`, cleanup `CONFIRMED`.

```sh
make test-runpod-vllm RUNPOD_VLLM_TEST_ARGS="--confirm-paid --image quay.io/bdobrica/kitchensoup@sha256:c1c5cd437ce4b89268a4d7e65791564fba3cbfda158f0ff778a4b25bf26c4f71 --training-image quay.io/bdobrica/kitchensoup@sha256:112204d70ed2fc7e88792ceee2d3179d2901c075c79c0389e76d638720ffc561 --gpu-id NVIDIA\ RTX\ PRO\ 6000\ Blackwell\ Server\ Edition\ MIG\ 1g.24gb --cloud SECURE --precision bf16 --min-vram-gb 8 --max-hourly-usd 0.69 --minutes 30 --budget-usd 3"
```

Successful output:

```text
Authenticated non-streaming and streaming playground inference passed
Provider resource termination confirmed
```

The smoke verified anonymous API rejection, authenticated selected-model readiness,
JSON chat, streaming completion and absence of backend endpoint/credentials in the
browser-facing deployment view. Inference traveled through KitchenSoup; the Pod
needed no direct RustFS route. Model bytes and input manifest hashes were verified
through SSH/SFTP. Resolved serving settings and redacted startup logs remain in
private local observation records after deletion; immutable database bindings
remain authoritative.

### Failed attempts and recovery

| Deployment | Pod | Result |
| --- | --- | --- |
| `a1e87cd0-ee2a-429f-9477-609d519910f9` | `pkegzokp6aic3e` | B300 MIG provider container-network failure (`network must exist`); stopped through durable application action; deleted |
| `773caa87-eb0f-439f-bbde-7f46ffc61238` | none | A40 allocation rejected after preflight; reservation released without a Pod |
| `86b218d5-8f02-4331-84c5-2f7d8b7b2783` | `t16dvc90w9omeh` | BF16 preflight, transfer and vLLM launch succeeded; premature proxy warm-up/authentication classification failed readiness; deleted |
| `3aab2757-2c32-4e02-bf33-d20c5c4b83b2` | `uh9j413itb7df1` | Reproduced premature readiness failure after successful BF16 preparation; deleted |
| `9a9a3fa1-2ad5-433f-b1c1-69d28eb9c62b` | `sk0igdt38w132k` | Corrected readiness; authenticated JSON and streaming inference passed; deleted |

RTX 3090 and L4 availability checks also failed before allocation. Resource names
and UUIDs were never reused to allocate again after uncertain responses. A temporary
diagnostic wrapper on one failed attempt reported only safe HTTP error categories;
the final successful run used the checked-in Make target directly.

Retained evidence corrected the initial suspicion that the first serving attempt
had merely exhausted SSH readiness: both Blackwell attempts reached native BF16
preflight and vLLM startup. The actual application bug treated any anonymous proxy
response other than 401 as proof of missing authentication, including transient
404/5xx before model loading. The fix treats anonymous success as unsafe while
allowing proxy warm-up to remain STARTING. Ready deployments still terminate on
unrecoverable endpoint failure. Serving SSH bootstrap was also aligned with the
existing 15-minute startup allowance for the large cold image.

### Cleanup and billing

All four allocated Pods were confirmed absent through provider inspection after
application cleanup. An independent final inventory/billing audit follows below.
The conservative sum of configured GPU compute ceilings across all four allocations
is $0.9875 (excluding storage/other charges), below the approved $3 total budget.
These estimates are not billing guarantees; provider billing can lag. Normal local
development infrastructure, synthetic registry metadata and the published image are
retained; temporary test infrastructure and provider Pods are removed.

Final independent audit found zero account Pods, and individual GET inspections
confirmed all four allocated IDs absent. Every deployment resource is `RELEASED`
with cleanup `CONFIRMED`, including the rejected A40 reservation without a Pod.
The provider billing API reported $0.00 for these Pods at audit time; billing can
lag, so this is not a claim that execution was free. No temporary cloud resource
remains pending cleanup.

Final changed-file scan found no environment credential values or private key
material in source, contracts, tests or documentation; `git diff --check` passed.
