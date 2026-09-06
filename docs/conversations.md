# ChatGPT conversations

Create a dataset at `/datasets`, upload a complete ChatGPT export ZIP, and click
**Import ChatGPT conversations** on its source card. No manual extraction is needed.
Use title search, **Select all shown**, **Select none**, and individual checkboxes,
then **Save selection**. Selection survives page reloads and is reserved for future
training-dataset creation. Dataset manifests and example generation follow in
Milestone 8; importing and selecting do not start training.

The list shows title, source creation date, retained text-message count and ignored
content warnings. Search affects only visible rows; Select all shown adds those
rows to the selection, while Select none clears the entire draft. New imports
start unselected. Save replaces selection for the dataset; concurrent saves use
the last committed request. Unsaved browser changes are not durable.

**Download original** still retrieves the exact ZIP. **Download canonical
conversations** appears after import; repeating import safely shows it again.
The canonical artifact UUID is also returned by the import API. Raw sources with
imports cannot be removed through the unprocessed-source removal flow.

## Supported export layouts

The detector accepts `conversations.json`, or numbered `conversations-N.json`
shards, at the ZIP root or under one directory. All detected conversation files
must share that directory. A single-file layout mixed with shards is ambiguous
and rejected. Unrelated account files and attachments are ignored by the importer
and retained in the raw ZIP. Nested archives are not expanded.

Each JSON file contains either a list of conversations or exactly a
`{"conversations": [...]}` wrapper. Each conversation has a source `id` or
`conversation_id`, a nonempty `mapping`, and normally `current_node`. Parent
links determine the selected branch; inactive branches are flagged as ignored.
Without current_node, only a unique leaf is accepted. Mapping cycles, missing
parents, duplicate conversation/message IDs and malformed text fields fail the
whole import. Missing titles become “Untitled conversation”; missing source
creation times remain null. Numeric Unix timestamps become UTC dates.

These are explicit supported layouts, not a promise of universal export
compatibility. [OpenAI's export guidance](https://help.openai.com/en/articles/7260999-how-do-i-export-my-chatgpt-history-and-data)
describes ZIP delivery but does not publish a stable conversation JSON schema.
Repository fixtures are synthetic and contain no personal exports.

## Canonical contract

[conversation-v1.schema.json](contracts/conversation-v1.schema.json) defines
`kitchensoup.conversation/v1`; regenerate it with `make openapi`. The canonical
artifact is UTF-8 JSONL, with one schema-valid object per line and a trailing
newline. Each object includes schema version, source conversation ID, title,
provider identifier, text messages with source message IDs, and metadata holding
source creation time, importer version and warning codes.

Roles normalize to system/user/assistant; developer becomes system with a warning.
Text parts retain their order and whitespace, with CRLF/CR normalized to LF.
Non-text parts, attachments, hidden messages, tool calls/output and unsupported
content are omitted and flagged. The importer does not fetch attachment references,
execute tool requests, copy arbitrary account metadata, or infer missing text.
Conversations with no supported text remain visible with zero messages.

Canonical files use `canonical/conversations/v1/{import_uuid}/conversations.jsonl`.
Their Artifact metadata records SHA-256 and format `kitchensoup.conversation/v1`.
An ArtifactDerivation links canonical output to the unchanged raw artifact.
PostgreSQL stores title, source ID, date, message count, warnings and selection;
message text lives in the canonical artifact. See [ADR 0005](adr/0005-canonical-chatgpt-import.md).

## API and limits

The additive routes are in the existing [v1 OpenAPI document](contracts/artifacts-v1.openapi.json):

| Method and path under `/api/v1/datasets/{dataset_id}` | Result |
| --- | --- |
| POST `/sources/{source_id}/imports/chatgpt` | 200: import ID, source ID, canonical artifact ID, importer/schema versions |
| GET `/conversations` | 200: conversation metadata and draft selection |
| PUT `/conversation-selection` | 200: replace selection using `{"conversation_ids": ["uuid", ...]}` |

Repeated imports of the same source/importer version return the existing import.
Different source uploads remain distinct, even if their content matches. A
selection request containing any foreign/unknown UUID returns 422 without changing
selection. Empty selection clears all. Selection is limited to 10,000 IDs per
request. The existing no-store responses and trusted local-user assumptions apply.

Imports require a ZIP source attached to that dataset. Limits are 64 MiB combined
conversation JSON, 10,000 conversations, 20,000 nodes per conversation, 100,000 nodes
per export, 128 MiB canonical output, and a cooperative 60-second parse budget,
plus the [existing ZIP extraction bounds](datasets.md#zip-extraction-utility).
JSON is decoded per file; it is not an unbounded streaming JSON parser. Canonical
output spills from memory to temporary disk above 8 MiB. Allow disk for the raw
spool, extracted ZIP, and canonical output.

Errors use the existing source-error convention: 404 for missing/wrong-dataset
sources, 409 for changed raw hashes or store mismatch, 422 for unsupported or
malformed exports/selections, and 503 for unavailable storage/workspace/database.
Failures keep the raw source and roll back all import metadata. A final object
write followed by failed database commit may leave an unregistered canonical
object; automatic orphan cleanup remains future work.
