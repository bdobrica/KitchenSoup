# Errors, safety and object retention

Milestone 23 follows [ADR 0022](adr/0022-error-ux-and-object-retention.md).
Run `make migrate` before using the new raw-deletion and retention actions.

## Actionable errors

Common errors have a short explanation and a suggested next step. HTTP error
responses retain `detail`; translated errors add an `error` object containing
`code`, `message` and `action`. Clients must tolerate additional fields and unknown
codes. The generated [error envelope schema](contracts/error-response-v1.schema.json)
documents this additive contract; validation errors retain their existing detail
array shape with input values and context omitted. Existing HTTP status meanings
remain unchanged. Normalized, credential-free
executor and model-resolution details are available as `advanced_detail` and under
**Advanced error details** in the corresponding UI. Unknown exception bodies,
provider payloads and submitted validation values are never returned.

| Code | Next step |
| --- | --- |
| `gpu_memory` | Stop competing workloads; reduce model/recipe memory or use a larger GPU |
| `target_memory` | Increase the target RAM limit, stop competing workloads or choose a smaller model |
| `model_download` | Check public, ungated repository/revision and download connectivity |
| `storage_unavailable` | Check object storage, bucket, operator credentials and disk space |
| `object_missing` | Check explicit deletion or restore the object from backup |
| `upload_too_large` | Choose a smaller file or review the configured upload bound |
| `executor_unavailable` | Check target connectivity and credentials; inspect the retained attempt before retrying |
| `operation_failed` | Inspect Advanced details and logs, then retry explicitly |

`GET /api/v1/training-runs/{id}/diagnostics` returns a payload-free explanation for
FAILED/LOST runs and `null` otherwise. The classifier scans at most 8 MiB of retained
stderr, keeping a 256 KiB tail. It is advisory: log text can be incomplete, misleading,
or contain source content. It never changes state, grants execution authority or
automatically retries. Existing bounded raw log endpoints remain available under
**Advanced details and raw logs**. Treat those private logs as potentially sensitive;
do not publish them as diagnostics evidence. The diagnostics operation does not
persist log text or send it to an LLM.

## Retention policy

| Data | Default retention and deletion |
| --- | --- |
| Registered originals and artifacts | Indefinite; no age-based deletion |
| Canonical datasets, immutable plans/runs, model lineage and reports | Indefinite; referenced provenance is protected |
| Executor workspaces and logs | Existing explicit cleanup controls; stopped-container removal retains host files |
| Chat messages | Existing browser-tab history; no server-side history |
| Upload staging and unregistered deterministic completion copies | Eligible one day after the upload completion deadline |
| Backups, object versions, replicas and downloaded copies | Operator-managed; not erased by application deletion |

After `make up`, the sweep is an explicit operator action using the Compose web
image's configured database and store:

```sh
make storage-retention CONFIRM=1
# Bound a Compose batch explicitly (1–1000; default 100):
docker compose run --rm --no-deps web python -m app.storage.retention --confirm --limit 1000
```

Each batch locks expired upload receipts, deletes their staging keys and, only when
unregistered, their deterministic completion keys. Successful receipts are marked
so repeated batches progress. Failures leave the receipt retryable; already deleted
objects can safely be deleted again. The grace period exceeds both the one-hour
completion window and maximum signed PUT lifetime. Registered final objects and
unrelated bucket keys are never swept. Unregistered canonical/model output objects
from failed transactions require operator investigation; this sweep does not infer
their ownership from a bucket listing. Keep the configured store stable while
upload receipts are retained, as required by ADR 0002.

No recurring scheduler or bucket lifecycle rule is installed. An operator can
schedule repeated batches, monitor storage capacity and manage backup retention.
Do not apply a bucket-wide expiration rule to registered artifact prefixes.
For a host deployment, run `.venv/bin/python -m app.storage.retention --confirm`
with the same environment, database and object-store endpoints as the host app.

## Explicit raw-source deletion

The source card offers **Delete original bytes**, with confirmation. This deletes
the raw object and its staging copy while retaining the artifact UUID, hash, size,
source metadata and a durable deletion record. It only accepts an unprocessed
source that has no other artifact references, model use, ingestion/import history,
or dataset versions. Shared and derived originals receive 409. This conservative
action preserves existing provenance; erasing processed datasets and trained models
is a separate, currently unsupported workflow.

`POST /api/v1/datasets/{dataset_id}/sources/{source_id}/delete-raw` requires JSON
`{"confirm": true}` with a strict boolean. It returns
`{"artifact_id": "<uuid>", "deleted": true}` after both deletes succeed.
Pending/deleted sources expose `raw_available: false` in source views. Artifact
metadata and lineage remain readable; downloading, attaching, importing or replaying
completion for deleted bytes is rejected. Source metadata cannot be removed after
a deletion request. The existing **Remove source** action still retains originals
and asks for confirmation.

A storage error after requesting deletion leaves a durable tombstone and blocks
new reads. **Retry original deletion** repeats the same idempotent action; it does
not create a new artifact or restore bytes. A crash after object deletion but
before the completion record is also recoverable this way. The operator must
finish pending requests; there is no automatic registered-object deletion worker.

This is deletion from the configured object-store namespace, not a guarantee of
physical erasure. Already downloaded copies remain, and signed URLs cannot be
revoked by the application. A still-valid signed PUT could recreate a staging copy
until expiry; the later retention sweep removes it. Versioned stores may retain old
versions behind delete markers. Operators manage those versions, backups and
replicas separately.

## Existing admission and sharing safeguards

Upload grants enforce the operator's `KITCHENSOUP_UPLOAD_MAX_BYTES` (default 1 GiB,
maximum 5 GiB), signed content length and actual bounded completion reads. Oversize
files fail before registration; a declared size is never trusted as proof of size.
[Raw source archive limits](datasets.md#zip-extraction-utility) bound directory
metadata, entries, expansion, compression ratio and extraction time, reject links
and traversal, validate CRCs and clean temporary directories on failure. Nested
archives are inert; model inspection has its own bounded stream checks.

Provider settings disclose destination and external sharing. Current test actions
send only fixed synthetic prompts and can incur a charge. A gateway may forward
data outside the local network. Future source-bearing actions must identify the
destination and selected content at the action. Playground launch warns against
publishing raw endpoints; use authentication, TLS and network restrictions for any
operator-provided external route. Default Docker/SSH/Kubernetes serving remains
private. Source and stopped-container deletion actions ask for confirmation; the
destructive Compose reset and retention commands require `CONFIRM=1`.
