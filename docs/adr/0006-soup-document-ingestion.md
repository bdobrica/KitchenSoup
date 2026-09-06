# ADR 0006: Isolated Soup CLI document ingestion

Status: Accepted

Date: 2026-09-06

## Context

PLAN sections 14 and 19 assign document parsing to Soup through its public CLI.
Milestone 7 requires selected-source ingestion, reproducible artifacts and logs;
training executors, durable background jobs and dataset manifests come later.
Soup's light CLI supports Python 3.12 while the application uses Python 3.13.

## Decision

Run `soup data ingest` in a separate, pinned Python 3.12 image. KitchenSoup never
imports Soup modules. A synchronous DocumentRunner port exchanges bounded input
bytes and a versioned response with a private runner. The runner invokes a fixed
argument vector, generated filenames and a fresh temporary working directory.
Source names and contents cannot choose commands, options, paths or destinations.
PDF and DOCX parsing dependencies exist only in that image.

Compose gives the runner an internal network shared only with web, no published
port, storage/database credentials, Docker socket, persistent mounts or outbound
network. Run non-root with a read-only filesystem, bounded tmpfs, dropped
capabilities, no new privileges and memory/CPU/process limits. Bound subprocess
file sizes, address space, CPU and wall time. Capture stdout/stderr separately;
do not write source data or CLI diagnostics to application/container logs.

Serialize ingestion on the dataset row. Verify and materialize selected raw
artifacts before invoking Soup. Snapshot source UUIDs, hashes, filenames and
licenses in a new numbered ingestion record. Record the installed Soup version,
content-addressed Docker image ID supplied by the operator launcher, runner mode,
command, warnings, ignored inputs and counts. Every successful request creates a
new immutable attempt; retry never overwrites earlier outputs. The image ID is a
local content identity, not a claim of registry publication or remote attestation.

Register separate JSONL, log and manifest artifacts with derivation edges. Keep
Soup row fields inside a `data` object alongside the source UUID; discard empty
text rows with explicit counts. Persist CLI failures with logs and no partial
dataset output. Admission, connectivity and malformed transport failures return
an error without registering an attempt. Original source artifacts never change.
The first successful output can fill the existing Document artifact pointer;
all subsequent versions remain available through ingestion history.

Document-ingestion records reject updates in PostgreSQL. Protect all sources in
a dataset with ingestion history against removal until explicit source-membership
retention rules exist, extending ADR 0004's conservative protection to attempts.
This avoids dangling provenance for failed or ignored inputs as well.

## Consequences

Initial parsing support is PDF, DOCX, Markdown and TXT. Structured files and ZIPs
remain raw sources (or use their existing conversation importer); the Soup document
CLI does not handle them. PDF extraction has no OCR; DOCX extracts paragraphs,
omitting tables and images. These limitations are recorded as warnings.

Extraction JSONL and its versioned ingestion manifest are intermediate artifacts,
not Milestone 8's `kitchensoup.dataset-manifest/v1` or a training recipe. No GPU,
model loading, external LLM call, general parser or training executor is added.

Requests are synchronous and bounded, intended for this trusted local deployment.
Process crashes or transport failures can interrupt an attempt; durable queue,
reconciliation and cancellation remain Milestone 13. PostgreSQL/S3 registration
still has the orphan-object caveat from ADR 0002. Logs can contain document text;
they are private artifacts downloadable explicitly, not ordinary application logs.
Reproduction requires retained original objects and the recorded image; operators
must retain/export images they need after local Docker pruning.
