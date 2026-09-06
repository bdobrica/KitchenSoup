# Training intent, recipes and AppSpec

Run `make migrate` and `make up`, then open `/training-plans`. Register a base model
and create a dataset version first. Choose an intent, model and dataset version;
click **Review training plan**, inspect the examples and warnings, then **Save
reviewed plan**. Saved plans survive reload and appear in the plan list. Dataset
version pages also link directly to configuration with that version selected.

Saving a plan does not submit training. Engine translation, executor selection,
hardware preflight and training execution remain later milestones.

## Intents and initial recipes

| Intent | Recipe v1 | Input | Epochs | Learning rate |
| --- | --- | --- | --- | --- |
| `conversation_imitation` | `conversation-sft-default` | Conversation examples | 3 | 0.0002 |
| `task_from_examples` | `task-sft-default` | User input / desired assistant output examples | 3 | 0.0001 |
| `learn_from_documents` | `document-adaptation-default` | Extracted document text | 1 | 0.00005 |
| `advanced` | Explicit recipe ID and version required | Determined by recipe | Recipe or override | Recipe or override |

All recipes use LoRA rank 16, alpha 32, dropout 0.05, maximum sequence length 2048,
batch size 1 per device, 16 gradient accumulation steps and seed 42. Training is
specified as AdamW, BF16, linear schedule, zero warmup/weight decay, no quantization,
and all linear LoRA targets; output is an adapter. These are initial defaults,
not measured quality or GPU memory guarantees. A seed alone does not guarantee
bit-identical GPU results. Advanced controls can override the bounded numeric
parameters; engine commands, arbitrary configuration and secrets are not accepted.

Conversation recipes apply the pinned model's chat template in future preparation;
only the final assistant target contributes loss, with prior turns providing
context. The model/template must support this masking. Documents use ordinary
causal text loss, with no generated Q&A, added conversation roles or cross-example
packing. The trainer must reject examples exceeding the sequence limit instead of
silently losing targets through truncation. Tokenization and these engine-specific
checks are deferred to the translator and preflight, not performed by plan review.
Mixed conversation/document selections require separate dataset versions. A task
recipe currently uses the same user/assistant example format as conversations.

Fine-tuning is unreliable for adding or updating factual knowledge. Consider
retrieval over documents for that use case. The document intent displays this
warning before review, and retains it in the saved plan. Model/source licenses and
existing dataset warnings remain visible through the review and preview links.

## Contracts and API

The [AppSpec schema](contracts/appspec-v1.schema.json),
[recipe schema](contracts/recipe-v1.schema.json), and
[resolved plan schema](contracts/resolved-plan-v1.schema.json) are generated from
`app/training/schemas.py` using `make openapi`. Routes are additive in the existing
[v1 OpenAPI document](contracts/artifacts-v1.openapi.json).

A minimal guided request is:

```json
{
  "schema": "kitchensoup.appspec/v1",
  "intent": "conversation_imitation",
  "base_model_version_id": "11111111-1111-4111-8111-111111111111",
  "dataset_version_id": "22222222-2222-4222-8222-222222222222"
}
```

Replace the synthetic UUIDs with registered versions. Omitted recipe uses the
intent's explicitly pinned default; the resolved result always retains the whole
recipe and version. To pin explicitly, add
`"recipe": {"id": "conversation-sft-default", "version": 1}`. Overrides look like
`"overrides": {"epochs": 2, "learning_rate": 0.0001}`; omitted/null numeric fields
use recipe defaults. The UI pins the selected recipe in its request.

| Method and path under `/api/v1` | Result |
| --- | --- |
| GET `/recipes` | Validated packaged recipes, no database required |
| POST `/training-plans/preview` | Resolve AppSpec; no writes |
| POST `/training-plans` | 201: save a new immutable plan from AppSpec |
| GET `/training-plans` | Saved snapshots, newest first |
| GET `/training-plans/{id}` | Saved AppSpec, resolved config, digest and timestamps |

Preview returns `appspec`, `resolved` and `sha256`. Save accepts the optional
`X-Plan-SHA256` header containing the preview digest. The browser always supplies
it; a different resolution returns 409 instead of saving a stale review. API
clients omitting the header explicitly request a new resolution and snapshot.
Every successful POST saves a new plan, even for identical inputs. Creation is
not idempotent; check the list before retrying after an uncertain response.
There is no update/delete API or submission action.

The plan hash is SHA-256 over the UTF-8 JSON object containing `appspec` and
`resolved`, serialized with schema aliases, all model defaults/null fields,
sorted keys, compact separators, unescaped Unicode and finite numbers. It excludes
row ID/timestamps and the hash itself. Recipe hashing uses the same serialization
on the full normalized recipe. Saved reads validate payload versions and their
hash; they do not resolve current defaults. Hashes identify retained configuration,
not execution authorization or artifact availability.

Resolution snapshots registered provenance and dataset metadata; it does not read
model weights or rehash dataset artifacts. Actual bytes and compatibility must be
verified during future execution preparation. Errors: 404 missing references,
422 invalid intent/recipe/parameters or incompatible input, 409 stale review or
unsupported/corrupt stored snapshot, 503 unavailable database. Unknown request
fields and schema versions fail validation. Normal metadata responses are no-store.

## Maintenance and version migration

Edit recipes under `app/training/recipes/`. Validate their strict schemas and
intent/format/objective consistency with the tests; publish changed recipe meaning
under a new version, retaining old versions. Guided default mappings are explicit
in `app/training/recipes.py`; no implicit "latest" lookup occurs. Recipes cannot be
uploaded or fetched from arbitrary URLs.

The AppSpec records training intent independently of Soup YAML. It currently has
no executor, engine image or generated configuration fields. Future submission
must preserve the reviewed inputs while resolving those execution details.
The illustrative submission shape in PLAN is not the v1 review contract.

Alembic upgrades relational storage separately from versioned JSON payloads.
Migration `f6263d0b8ecb` adds `training_plans`; `0009_training_plan_guards` prevents
all updates, including direct SQL changes. Unknown payload versions are rejected,
not guessed or coerced to v1. Future versions need an explicit supported reader
and tested conversion, leaving prior immutable snapshots intact; semantic changes
produce a new plan and new hash. No payload conversion is needed for this first
version. See [ADR 0009](adr/0009-training-intent-and-review-plans.md).
