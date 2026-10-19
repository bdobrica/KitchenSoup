# Model representations

A fine-tuned logical version retains its LoRA adapter. On its model detail page,
**Create representation** merges that adapter with the exact reviewed base into
FP16 (default) or BF16 safetensors. A merged representation can then be quantized
to AWQ INT4. All outputs stay on the same version, with separate artifact hashes,
byte sizes and derivation history. Browsing never launches a merge.

## Local setup

Run `make migrate` and `make build-transform`. Set
`KITCHENSOUP_LOCAL_TRANSFORMATIONS_ENABLED=true` in your ignored `.env`, then restart
the host web, worker and reconciler processes described in [local training](local-training.md).
The standard Compose processes have no Docker authority. The host processes share
one private `KITCHENSOUP_TRANSFORMATION_WORKSPACE` (default `.local-transformations`),
operator UID, database, artifact store and Docker daemon. The optional image setting
is `KITCHENSOUP_TRANSFORMATION_IMAGE` (default `kitchensoup-model-transform:local`);
each submitted job resolves and retains its exact image ID.

Use FP16 on RTX 2070 SUPER. BF16 requires native support. Avoid overlapping serving,
training, evaluation and transformation GPU workloads. Allow private disk for inputs,
expanded output and its ZIP; the model limit is 8 GiB expanded. Docker bounds RAM to
16 GiB and CPU to four cores, but bind mounts require operator disk quotas. No
credentials or network enter the GPU container. Public exact-commit base download
occurs anonymously in the host materializer before launch.

## Calibration and reuse

AWQ requires explicit calibration text, one sample per line: up to 128 nonempty
samples, 8000 characters each and 65536 characters total. It is stored with the
immutable job and omitted from progress API responses. Use representative text you
are comfortable retaining locally. AutoAWQ tokenizes and packs blocks of 128 tokens;
samples over 128 tokens are excluded by its pinned calibration loader. Supply enough
eligible text for at least one complete block. Unsuitable text fails the job rather
than downloading a fallback dataset. AWQ uses 4-bit GEMM weights, group size 128,
zero points and FP16 activations. Quantization is lossy; review quality separately.

Identical input, base, image, precision and calibration reuse the same job/output,
including after web restart or concurrent requests. Changed settings create a new
job. Failed/lost identical jobs remain visible; automatic retries are not implemented.
Worker/reconciler must keep running. Progress is QUEUED → PREPARING → RUNNING →
POSTPROCESSING → SUCCEEDED, with FAILED/LOST outcomes. A crashed preparation is
observed, never blindly replayed. Storage or Docker outages leave pending work for
reconciliation. A verified successful artifact may be registered during POSTPROCESSING
while container cleanup is pending. Resource cleanup precedes SUCCEEDED.

Further training uses a registered merged representation of the selected logical
version, never its unmodified ancestor. If several merges exist, the oldest is
selected deterministically; the review plan contains the exact chosen hash.
Playgrounds offer an explicit representation selector; their default remains LoRA.
AWQ serving uses FP16. Full pre-quantized archive imports remain unsupported.

## Version 1 API

All routes are under `/api/v1`; see [generated OpenAPI](contracts/artifacts-v1.openapi.json).

| Method/path | Result |
| --- | --- |
| POST `/artifacts/{id}/transformations` | 202: create/reuse job; body `operation` (`merge` or `awq`), `precision` (default `fp16`), `calibration` (AWQ only) |
| GET `/transformations/{id}` | Job status, model/input/output UUIDs and creation time |
| GET `/model-versions/{id}/transformations` | Job history for the logical version |

Snapshots use [kitchensoup.transformation/v1](contracts/transformation-v1.schema.json).
Output formats are `kitchensoup.merged-fp16+zip/v1`, `kitchensoup.merged-bf16+zip/v1`
and `kitchensoup.awq-int4+zip/v1`. ZIPs contain native Transformers safetensors and
tokenizer files. Derivations are queryable through the existing artifact lineage API.
No source-license metadata is replaced and no logical model version is created.

`make test-transform-gpu` runs an opt-in synthetic offline FP16 merge/AWQ smoke.
The isolated image removes the unused vLLM package and pins [AutoAWQ 0.2.9](https://github.com/casper-hansen/AutoAWQ)
and PEFT on the existing digest-pinned CUDA/vLLM base. AutoAWQ is archived upstream;
image upgrades require repeat validation. Its [calibration API](https://casper-hansen.github.io/AutoAWQ/reference/)
is image-local; the control plane has no new Python dependencies. Regenerate added
package pins with `sg docker -c ".venv/bin/python scripts/lock_transform.py"`.
The build checks the ML dependency closure. An unrelated inherited OS PyGObject
metadata warning prevents a clean global `pip check`; it does not enter that closure.
