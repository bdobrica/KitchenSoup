# ADR 0005: Canonical conversation imports and draft selection

Status: Accepted

Date: 2026-09-06

## Context

PLAN sections 12–13 require importing complete ChatGPT exports into a versioned,
engine-independent representation, with source IDs and explicit ignored-content
warnings. Dataset manifests and training-example preview remain in Milestone 8.

## Decision

Define `kitchensoup.conversation/v1` in Pydantic and generate its JSON Schema.
Store canonical conversations as UTF-8 JSONL, one conversation per line, with
source identifiers, normalized text messages and typed import metadata. The
source field is provider-neutral; the first importer writes `chatgpt`.

A synchronous importer port detects supported JSON files in a safely extracted
export and yields canonical conversations. The ChatGPT implementation recognizes
mapping-based conversation lists and a conversations-list wrapper, in a single
file or numbered shards under one directory. It follows parent links from
`current_node`, preserving the active branch in chronological path order. If
current_node is absent, accept only a unique leaf. Reject cycles, missing parents,
ambiguous layouts, duplicate source IDs and malformed supported fields.

Preserve source conversation/message identifiers. Normalize CRLF/CR to LF and
retain text parts without concatenating unrelated messages. Normalize developer
to system; retain system/user/assistant text. Omit tool-directed/tool messages,
hidden messages and unsupported content, recording warning codes. Retain text
parts of multimodal messages and explicitly flag ignored non-text parts and
attachments. Never execute exported content or read attachment URLs.

Lock the dataset/source during import and reuse an existing import for the same
source, importer and importer version. Verify the original artifact hash, parse
the export, write a separate canonical artifact, then commit import metadata,
conversation rows and an artifact-derivation edge in one transaction. Failed
imports roll back metadata and never mutate raw objects. As in ADR 0002, a
successful object write followed by a database failure can leave an orphan object;
retention cleanup remains separate.

Persist conversation selection as mutable draft metadata, defaulting to false.
Replace selection atomically within one dataset and reject IDs from other
datasets. Selection never rewrites canonical artifacts or existing dataset
versions. Future manifests will snapshot selected IDs and source hashes.

## Consequences

The importer has explicit JSON, conversation, node, output and time limits in
addition to existing ZIP limits. Its supported layouts are fixture-defined;
OpenAI's export help documents ZIP delivery, not a stable JSON wire schema.
Unsupported layouts fail visibly while raw sources remain retrievable.

Warnings and message counts are available in the UI without loading message text
into browser lists. Conversation metadata preserves exact source IDs and dates;
missing dates remain null. Selection is last-save-wins draft state, not a dataset
manifest or execution grant. The generated migration adds selection and warnings
to existing conversation rows without changing their identifiers.
