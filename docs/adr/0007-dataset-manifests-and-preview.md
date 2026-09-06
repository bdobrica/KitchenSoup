# ADR 0007: Immutable dataset manifests and exact example preview

Status: Accepted

Date: 2026-09-06

## Context

PLAN sections 11–16 distinguish originals, canonical imports and versioned training
datasets. ADR 0001 already provides immutable dataset-version rows. ADRs 0005 and
0006 provide canonical conversations, mutable draft conversation selection and
immutable Soup extraction artifacts. Milestone 8 connects these inputs to preview;
recipes, model-specific formatting and training execution remain later work.

## Decision

Define `kitchensoup.dataset-manifest/v1` and `kitchensoup.training-example/v1` in
Pydantic and generate their JSON schemas. Create a fresh numbered DatasetVersion
under a dataset row lock, snapshotting either explicit conversation UUIDs or the
saved conversation draft, plus explicitly chosen successful document extractions.
Omitting conversation IDs uses the saved draft; an empty list selects none.
Document selection uses complete immutable extraction IDs, never an implicit
latest extraction or a Document's first-output pointer. Reject overlapping
extractions for the same ingested source to avoid duplicate document examples.

Verify the exact hashes of canonical conversations and document artifacts read
for conversion. Snapshot original source artifact UUIDs, registered hashes,
filenames and declared licenses. Preserve canonical/import IDs, document extraction
manifest/output hashes, converter version, selected conversation IDs and source
conversation IDs. Original bytes are not reprocessed during version creation;
canonical/ingestion provenance links them to the retained uploads.

For conversations, emit one example per nonempty assistant response with an earlier
nonempty user message. Include the cumulative retained context through that
response, never subsequent messages. Join ordered text parts with a newline,
retain roles and existing text, and skip whitespace-only messages. An assistant
before any user is not a target but remains available as historical context for
later targets. Do not invent a system prompt, merge roles, chunk text, call an
LLM, fetch attachments or apply model tokenization. Record the target message ID.
Generate prefixes lazily and bound total output to prevent quadratic accumulation
in memory. Future recipes must describe any additional transformation explicitly.

For documents, preserve every extracted text row from each selected successful
Soup output as a text example, referencing extraction ID, source UUID and zero-based
row. Do not generate synthetic Q&A pairs. A dataset may contain both conversation
and document examples; this is a data snapshot, not a promise that every future
training recipe can consume both modes.

Persist the exact example JSONL and manifest as separate artifacts with derivation
edges. Store the same manifest JSON and exact manifest-byte SHA-256 in the existing
immutable DatasetVersion row. Every creation is a new version, even for unchanged
selection; output bytes are reproducible for identical IDs/inputs/converter.
Draft changes never modify snapshots. Existing conservative source-removal guards
remain in force. No schema migration or new database authority is introduced.

Preview reads and rehashes the stored examples, checks their manifest reference
and count, and returns a bounded page. PostgreSQL owns the version manifest;
preview never regenerates examples from mutable draft state. Render text as text,
not HTML or Markdown, so untrusted source content cannot execute in the browser.

## Consequences

Statistics count unique selected sources, selected conversations, nonempty canonical
messages (once per conversation), emitted examples, exact output byte size and
known ignored items. Known ignored items comprise empty messages, unprompted
assistant targets, conversations with no target, empty Soup rows and explicitly
ignored extraction inputs. These categories may overlap; they are conversion
outcomes rather than an estimate of the original export's omitted nodes. Upstream
warning codes without exact counts remain warnings with null counts; no omission
count is invented for attachments, tools, branches or Word/PDF limitations.

At most 10,000 conversations and 16 extraction IDs are selected. Canonical inputs
are bounded per artifact and to 256 MiB combined; output is capped at 64 MiB and
100,000 examples. Preview accepts offsets and 1–100 examples per page, reading the
bounded artifact for hash verification on each request. An index/range reader can
replace this implementation later without changing the immutable contract.

All-empty selections fail without a version. Storage/validation failures roll
back metadata and never alter originals or earlier versions; as in ADR 0002,
failed commits can leave unregistered objects for future retention cleanup.
No training is started by creating or browsing a dataset version.
