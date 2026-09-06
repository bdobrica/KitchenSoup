# Model catalog and registry

Run `make migrate`, then `make up`, and open `/models`. The page supports choosing
a curated Qwen model, registering a public Hugging Face repository/revision, and
uploading a custom model ZIP. Details show the registered versions, source,
exact revision or archive hash, declared license, license link, and notes.
Registration does not download Hugging Face weights or start training.

## Catalog maintenance

Edit [the packaged catalog](../app/registry/catalog-v1.json). Its
[version 1 schema](contracts/model-catalog-v1.schema.json) is generated from
`app/registry/schemas.py` with `make openapi`. Every entry includes a stable key,
full commit, architecture, parameter/context counts, license/gating, Soup/vLLM
compatibility, recipe/quantization guidance and approximate VRAM guidance.

`make catalog-sync` builds the current package and synchronizes it into an already
migrated PostgreSQL database. Compose runs the same one-shot `catalog-init` before
web starts. Repeated sync retains UUIDs; removed keys become hidden while their
referenced rows remain. Reintroducing a key reactivates it. Registered source
snapshots retain their original revision and licensing after catalog updates.
Sync requires no network access. Invalid packaged catalogs fail before any writes.

Initial entries are Qwen2.5 0.5B and 1.5B Instruct. Revisions and metadata were
checked against the public Hugging Face model API on 2026-09-06. Upstream model
cards document [0.5B](https://huggingface.co/Qwen/Qwen2.5-0.5B-Instruct) and
[1.5B](https://huggingface.co/Qwen/Qwen2.5-1.5B-Instruct); pinned license URLs are
included in each entry. BF16 weight estimates use two bytes per parameter,
rounded to approximately 1/3 GiB. They exclude activations, caches, optimizer
state and runtime overhead. No GPU-based training minimum has been measured.

## Registration contract

The additive model routes are included in the existing
[v1 OpenAPI document](contracts/artifacts-v1.openapi.json), whose historical filename
is retained for compatibility. Existing artifact and health contracts are unchanged.
`make openapi` regenerates the API and catalog schemas; unit tests check drift.

| Method and path under `/api/v1` | Result |
| --- | --- |
| GET `/model-catalog` | Active curated entries |
| POST `/model-catalog/{key}/register` | 200: registered model; repeated selection of the same revision reuses it |
| GET `/models` | Registered models with versions and source snapshots |
| GET `/models/{model_id}` | One registered model, or 404 |
| POST `/model-imports/huggingface` | 201: a new model from name, repository, revision (default main), optional notes |
| POST `/model-imports/archive` | 201: a new model from name, artifact UUID, declared license, optional license URL/notes |

Import requests create new logical models with version 1. They are not idempotent;
check the model list before retrying after an uncertain response. Catalog selection
is serialized on its catalog row. Failed provider checks or archive inspection
create no partial model/version/source rows. Unknown fields and invalid inputs
return 422. Missing resources return 404; hash/store mismatches return 409;
resource bounds can return 413; unavailable services return 503. Error messages
are friendly and omit provider payloads. There is no public model-delete API yet.

Hugging Face imports accept `owner/repository` identifiers, not URLs or credentials.
Only `https://huggingface.co` metadata is fetched, without redirects or implicit
local authentication. Metadata responses are capped at 4 MiB with 15-second HTTP
timeouts. The returned full commit is retained, and configuration is read at that
commit. Gated/private/missing sources are rejected. A model card with no license
is displayed as `unknown`; this does not imply permission to use the model.

## Uploaded models

The UI uploads directly through the existing [artifact API](storage.md), completes
hash verification, then registers that artifact as a model source. Failed model
inspection leaves the raw artifact in storage. The compressed upload limit is
1 GiB by default; configure `KITCHENSOUP_UPLOAD_MAX_BYTES` in the Compose web
environment for larger models, up to the existing 5 GiB single-PUT limit.

Use a ZIP containing either files at its root or one model directory:

- `config.json` for Qwen2ForCausalLM, LlamaForCausalLM or MistralForCausalLM;
- `tokenizer_config.json` and `tokenizer.json`, `tokenizer.model`, or `vocab.json`
  with `merges.txt`;
- `model.safetensors`, or `model.safetensors.index.json` with all referenced shards.

Inspection never extracts files or invokes Transformers. Safetensors headers,
dimensions, offsets and indexed tensor references are checked. Weights are not
loaded and numerical correctness, tensor/config agreement, tokenizer behavior,
and trainability are not established. See [ADR 0003](adr/0003-model-registry.md).

Limits are 4,096 entries, a 4 MiB ZIP central directory, 8 GiB expanded bytes,
100:1 per-file compression, 16 MiB per JSON file, 1 MiB per safetensors header,
32 MiB combined model metadata and a 60-second cooperative inspection budget.
ZIP stored/deflate compression is supported. ZIP64 directory metadata and other
archive formats are rejected. Paths, duplicates, links, special files, encryption,
custom code and pickle checkpoints are rejected. Source bytes are rehashed before
inspection to detect storage changes since artifact completion. These structural
checks are admission checks, not a general malware scanner or execution sandbox.

Upstream references: [Safetensors format](https://github.com/safetensors/safetensors#format),
[vLLM model support](https://docs.vllm.ai/en/latest/models/supported_models/), and
[Hugging Face metadata API](https://huggingface.co/docs/hub/api).
