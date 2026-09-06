# Dataset versions and preview

Open a dataset, import and save a conversation selection and/or process documents
with Soup. Under **Create a dataset version**, choose whether to include the saved
conversation selection and check the completed document extractions to include.
Choose **Create new version** to freeze the selection and open its preview.

The version detail page shows source/conversation/message/example counts, known
ignored items, byte size, declared licenses and validation warnings. Browse examples
with Previous/Next and 5, 20 or 100 rows per page. Conversation previews show the
context and the final assistant target; document previews show extracted text.
Download the exact example JSONL and its manifest from the same page.

Return to the dataset, change and save selections, and create a new version.
Previous version links remain available and preserve their original contents.
Creating a version never starts training. Unsaved conversation checkbox changes
are not included; version creation uses the saved draft. Document checkboxes are
local to the creation form and do not alter extraction history.

## Conversion and statistics

The converter `kitchensoup.examples/v1` emits one conversation example per
nonempty assistant response that has an earlier nonempty user message. Each
example ends at that assistant response and includes only earlier context.
Ordered text parts are joined by a newline, roles are preserved, and whitespace-only
messages are skipped. Assistant messages before the first user are not training
targets; they may remain in the context of later examples. Conversations without
usable targets produce a warning. A selection with no usable examples returns 422.

Document examples preserve Soup's extracted text without Q&A generation or further
parsing. Choose a complete successful extraction; selecting two extractions with
the same ingested source returns 422 to prevent accidental duplicated training
text. Raw document uploads cannot be selected directly until processed by Soup.
Mixing conversation and document examples is supported for preview. Model templates,
tokenization and compatible training recipes are resolved in later milestones.

| Statistic | Meaning |
| --- | --- |
| `source_count` | Distinct source UUIDs in selected imports/extractions, including explicitly ignored extraction inputs |
| `conversation_count` | Selected canonical conversations, including those without usable targets |
| `message_count` | Nonempty canonical messages counted once, without repeated prefix-context inflation; documents add zero |
| `example_count` | Emitted assistant-target and document-text rows |
| `ignored_item_count` | Known empty messages, unprompted assistant targets, conversations without targets, empty Soup rows and ignored extraction inputs |
| `size_bytes` | Exact UTF-8 example JSONL byte size |

Known ignored counts are conversion outcomes and can overlap (an empty assistant
message can also leave a conversation without a target). Canonical import warnings
may describe additional omitted attachments, branches or tools with unavailable
counts. These warnings retain null counts; the statistic does not claim a complete
count of content omitted from the raw export. Source/target warnings are visible
alongside the source and conversation UUIDs in the manifest.

## Version 1 contracts

The generated [manifest schema](contracts/dataset-manifest-v1.schema.json) defines
`kitchensoup.dataset-manifest/v1`. The [example schema](contracts/training-example-v1.schema.json)
defines each row of a `kitchensoup.training-example/v1` JSONL artifact. Regenerate
both and the [HTTP contract](contracts/artifacts-v1.openapi.json) with `make openapi`.

The manifest records dataset/version UUIDs and number, dataset license, converter
version, original source UUIDs/hashes/filenames/licenses, selected conversation
UUIDs and provider IDs, canonical artifact references, extraction IDs and their
manifest/output references, example artifact reference, statistics and warnings.
Each artifact reference records UUID, SHA-256, size and format. Original registered
hashes are snapshotted; only the canonical/derived bytes used for conversion are
read and rehashed during version creation. No source is silently re-imported.

Conversation example fields identify the source, conversation and target message;
`messages` contains ordered role/content pairs through the assistant target.
Document example fields identify source, extraction and zero-based output row;
`text` contains extracted text. Optional inapplicable fields are omitted from stored
JSONL; API previews may return null/default values for those fields. Text bytes
and message order are unchanged between stored examples and previews.

Examples and manifests live under `datasets/v1/{dataset_version_id}/`. Their
ArtifactDerivation edges link the inputs, and the manifest also references its
example artifact through an edge. DatasetVersion stores the manifest JSON and
SHA-256 of its exact artifact bytes; PostgreSQL rejects updates to existing
versions. No public version-update or deletion endpoint exists.

| Method and path under `/api/v1/datasets/{dataset_id}` | Result |
| --- | --- |
| POST `/versions` | 201: new immutable version; accepts optional `conversation_ids` and `document_ingestion_ids` |
| GET `/versions` | 200: version details, newest first |
| GET `/versions/{version_id}` | 200: immutable manifest and metadata |
| GET `/versions/{version_id}/examples?offset=0&limit=20` | 200: `{offset, limit, total, items}` |

Omitting `conversation_ids` snapshots the saved draft; `[]` selects none.
`document_ingestion_ids` defaults to `[]`. Repeated IDs are deduplicated; repeated
POST requests create separate versions. Import UUID order, canonical file order
and extraction UUID/row order determine example order, independent of checkbox
order. Concurrent creation allocates distinct sequential version numbers.

Requests reject extra properties. Foreign conversation/extraction selections,
failed or overlapping extractions and empty/oversized datasets return 422.
Missing or wrong-dataset versions return 404. Unsupported legacy manifests,
invalid/mismatched canonical artifacts and altered preview output return 409.
Storage/database availability and size errors retain existing 503/413 conventions.
Metadata and preview responses use `Cache-Control: no-store`; artifact downloads
use the existing scoped, expiring download grants. Keep real training text and
manifests private; the UI renders source text without interpreting HTML.

## Limits and verification

Select at most 10,000 conversations and 16 document extractions. Canonical/import
artifacts are capped at 128 MiB, Soup output at 32 MiB and combined conversion
inputs at 256 MiB. A version is limited to 64 MiB and 100,000 examples. Preview
allows `offset >= 0` and `1 <= limit <= 100`; offsets past the end return an empty
page. Each preview hashes the bounded full artifact before returning a page,
which trades simplicity for repeated storage reads on large datasets.

The existing database schema and immutable-version trigger suffice: no migration
is added. `make verify` and `make test-integration` cover conversion, schema drift,
snapshot immutability, concurrent numbering, pagination, document provenance,
selection errors, artifact tampering and storage rollback. See
[ADR 0007](adr/0007-dataset-manifests-and-preview.md) and
[Milestone 8 evidence](evidence/milestone-8.md).
