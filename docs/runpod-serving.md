# Runpod playgrounds

Runpod serving reuses the opt-in [training setup](runpod-training.md), API key,
SSH key references and host trust. No Runpod key or raw inference endpoint reaches
the browser. Keep the trusted application behind your existing authenticated
perimeter; this feature does not add multi-user authorization.

## Images and targets

Build the pinned local serving base with `make build-vllm`, then
`make build-runpod-vllm`. Publish the SSH-capable serving variant to an accessible
registry and configure its digest, separately from the Soup trainer image.
The pinned engine image is large; allow at least 50 GB container disk for the
validated image and tiny fixture. Staged model and adapter bytes together are
bounded to 9 GiB by the SSH transfer abstraction.
Local validation without cloud cost:

```sh
.venv/bin/python scripts/test_runpod_image.py --serving --gpu
```

Create a new immutable `runpod_pod` target using the settings page or target API.
Existing training-only targets remain valid. Add these optional v1 settings:

| Setting | Meaning |
| --- | --- |
| `deployment_image` | Separate SSH-capable vLLM `repository@sha256:digest`; required for serving |
| `deployment_gpu_id` | Optional override, also included in `allowed_gpu_ids` |
| `deployment_min_vram_gb` | Optional serving VRAM override; otherwise training minimum |
| `deployment_disk_gb` | Optional serving disk override; otherwise training disk |
| `max_deployment_lease_minutes` | Absolute limit from creation, including preparation; 1–60, default 30 |

GPU price ceiling, cloud/placement constraints and concurrent-Pod limits are shared.
Training and serving allocate independent dedicated Pods. Uncertain allocations
and pending cleanup consume shared capacity. Connection tests remain read-only.
The target form accepts the serving overrides; the model's playground page lists
serving-capable targets and estimates GPU compute at the configured price ceiling.

## Supported representations

The pinned vLLM 0.10.2 variant supports the repository's native Qwen2, Llama and
Mistral causal safetensors archives, merged full models, causal LoRA paired with its
exact reviewed base source/revision, and the existing generated 4-bit GEMM AWQ
representation. AWQ requires FP16 and a compatible GPU. Hub-only registrations
need a registered full archive. Remote/custom model code, arbitrary quantization
formats and adapter-provided base paths are unsupported.

The host verifies registered archives and structure before allocating. It uses a
conservative weights-plus-overhead VRAM check, then probes actual CUDA, VRAM and
native BF16 after allocation. This is not a guarantee that every otherwise valid
model fits: KV cache and engine overhead can still cause startup failure. Runtime
context length is capped by the model's declared maximum and the 2048-token
playground limit; resolved settings and artifact identities remain in private
staging under a retained private `records/<deployment-id>.json` observation record.
The immutable PostgreSQL snapshot remains authoritative; these derived records
never authorize provider or inference access. BF16 is explicit and requires native support; FP16 remains supported.

SFTP stages verified model bytes. The Pod needs no route or credential for local
RustFS. Full public-IP TCP SSH is required; basic proxied SSH is insufficient.
Community Cloud does not always provide that mapping. The image runs root only
for SSH bootstrap, then runs model serving as the non-root runner. Its egress and
filesystem isolation differ from local Docker; use reviewed native model formats.

## Endpoint and chat protection

The fixed port 8000 HTTP proxy endpoint is derived only from a freshly verified
owned Pod ID. A saved URL never authorizes a request. Each deployment gets a random
private inference key, transferred over SSH and supplied through the server's
environment. The image adds all-route authentication to vLLM (whose native check covers only
`/v1`); even auxiliary routes reject anonymous access. readiness verifies both rejection
and authenticated selected-model discovery. Inference and provider credentials are
absent from snapshots, browser JSON and command arguments. Private local staging
must be shared by web, worker and reconciler and protected like other executor
staging. Losing that staging prevents chat and requires stopping/relaunching.

Browsers call KitchenSoup's existing chat route. The default request and JSON
response are unchanged. With `stream: true`, the response is `text/event-stream`
with OpenAI-style `choices[0].delta.content` events and a `[DONE]` marker. Errors
following response headers use a sanitized `event: error` without a DONE marker.
Messages retain their existing bounds; generation remains greedy and capped at
128 tokens. No model, endpoint, credential, tool or generation override is accepted.

Connections disable redirects and environment proxies. Cloud inference is bounded
to 90 seconds (stream idle timeout 15 seconds), 1 MiB transport and 65536 content
characters. These limits account for the provider's documented 100-second proxy
limit. Disconnect closes upstream streaming and releases the deployment claim.
Prompts and responses are neither logged nor persisted. The UI renders text only.
`GET /api/v1/deployments/{id}/logs` returns bounded serving startup output with the
inference credential redacted; no provider environment dump is returned.

## Leases and cleanup

SSH/image bootstrap waits up to the existing 15-minute startup allowance, bounded
by the absolute allocation deadline. Cold image pulls can consume this window.
The requested lease starts at deployment creation and includes image loading and
transfer. Extension cannot exceed creation plus the target's maximum lease.
Runpod rejects keep-running; other executors retain their existing semantics.
The UI displays remaining time and cleanup warnings. Idle Pods remain billable.

Explicit stop, expiry, unrecoverable startup or endpoint failure terminate the Pod.
The direct process supervisor has a runtime bound, but exiting the process does
not release provider billing. Keep the reconciler running: it confirms provider
absence before releasing capacity and retries failed cleanup after restarts,
including terminal deployments. Provider outages leave cleanup pending. No process
or lease setting is an account-wide billing guarantee; storage and other charges
can exceed GPU-only estimates. The published registry image is retained separately.

## Paid validation

`make test-runpod-vllm` is explicitly opt-in. Supply `RUNPOD_VLLM_TEST_ARGS` with
`--confirm-paid`, separate digest-pinned `--image` and `--training-image`, selected
`--gpu-id`, `--cloud`, reviewed `--precision`, `--max-hourly-usd`, `--minutes` and
`--budget-usd` (at most 3). It registers a synthetic tiny model, deploys through the
real application lifecycle, verifies authenticated JSON and streaming chat through
the playground proxy, and confirms termination in `finally`. No model quality is
implied. Failed cleanup is a hard test failure requiring operator attention.

See [ADR 0025](adr/0025-runpod-serving.md) and
[Milestone 26 evidence](evidence/milestone-26.md).
