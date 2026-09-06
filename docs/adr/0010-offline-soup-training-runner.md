# ADR 0010: Offline Soup CLI training bundles

Status: Accepted

Date: 2026-09-06

## Context

PLAN section 19 requires a pinned Soup CLI boundary and reproducible inputs and
outputs. ADR 0009 fixes recipe semantics, including final-assistant loss, no silent
truncation and BF16. Milestone 11 supplies the image and translation; materializing
registered inputs, launching jobs and registering outputs belong to later executors.

## Decision

Pin Soup 0.74.0 and its full training dependency set in a separate Python 3.12
image. KitchenSoup does not import Soup modules, patch its implementation or use
its internal Python types. Inspecting release source informs the adapter; actual
execution uses `soup train --config /output/soup.yaml --yes` only.

Publish immutable `kitchensoup.training-input/v1` and
`kitchensoup.training-output/v1` contracts. The input embeds the reviewed AppSpec,
resolved plan and hash, a run UUID, registered source provenance, and a complete
hash/size inventory of the materialized model files. A trusted launcher must
materialize the exact registered revision or inspected upload and bind the file
inventory to that provenance. The runner cannot independently attest a file's
Hugging Face origin from a claimed commit. Input metadata is not execution authority.

The runner operates on an immutable local `/input` bundle and an empty `/output`
workspace. It verifies plan/recipe consistency, model files, the exact dataset
manifest and example bytes before preparation. No Hub downloads, remote code,
providers, storage credentials or Docker socket are needed. Model admission is
restricted to native Qwen2/Llama/Mistral safetensors configurations. Materialized
files must be regular, inventoried and confined; symlinks and executable/pickle
representations are rejected.

Use the pinned Transformers tokenizer through an explicit preparation port and
Soup's documented `pre_tokenized` dataset input. For conversations, tokenize the
prior context with the model's generation prompt and the complete example; require
an exact token prefix, then mask all tokens before the final assistant target.
Reject templates that do not expose that stable boundary. For documents retain
ordinary causal text targets. Reject overlong or targetless examples before Soup;
do not truncate, pack across examples, generate Q&A or let an engine formatter
reinterpret the loss masks. Write deterministic token JSONL and Arrow shards.
The pinned TRL collator retains explicit labels.

Translate fixed KitchenSoup semantics into release-specific Soup fields. `soup.yaml`
uses JSON syntax, a YAML subset. Model/data/output paths and command arguments are
runner-owned. Soup automatically chooses mixed precision; to preserve ADR 0009,
actual training requires a CUDA device with native BF16 support. Unsupported GPUs
fail preflight rather than silently switching to FP16. Validation-only mode checks
preparation and the real Soup CLI dry run without claiming training success.

Capture stdout/stderr separately as bounded private files. A wall timeout kills
the CLI process group; retain its exit code and a final output manifest. Successful
training requires adapter configuration and safetensors output. Failure never
advertises partial adapters as successful outputs. Refuse a nonempty workspace;
retries need a new attempt directory. Record the launcher's content-addressed image
identity, input/plan hashes, translator/Soup versions, command and output hashes.
The image identity is launcher-supplied provenance, not an in-container attestation.

## Consequences

The image is substantially larger than the application and ingestion images.
`make build-soup` builds it separately; the normal web stack does not gain training
dependencies. Locks are generated in the pinned base from `requirements.in`.
No new database migration, run submission endpoint or executor is introduced.

Model materialization, retention/registration, cancellation/reconciliation and
resource allocation remain executor responsibilities. Launchers must provide a
read-only input mount, private output, no network, restricted environment and
container resource limits. A killed container or exhausted disk may prevent a final
manifest; absence of a success manifest is never success. Adapter bytes may differ
across GPU kernels despite fixed seeds; retained configuration and byte inventories
support diagnosis and reproduction without promising bit-identical GPU training.
