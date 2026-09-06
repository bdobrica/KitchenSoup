# ADR 0004: Raw dataset sources and temporary archive extraction

Status: Accepted

Date: 2026-09-06

## Context

PLAN sections 11 and 14 separate raw sources from canonical representations and
training datasets. Originals are retained by default. Milestone 5 establishes
source admission before the ChatGPT importer and Soup document processing.

## Decision

Create datasets and attach completed ArtifactStore artifacts as raw sources.
Recheck the stored size and SHA-256 before admission; keep original bytes
unchanged. Record filename, kind and optional declared source license separately
from the dataset's license. Document inputs create initial Document records with
no canonical artifact. Admission does not create dataset versions or derived data.

Use the existing direct-upload and completion API from ADR 0002. Attachments are
serialized on the dataset row, and repeated attachment of the same artifact and
metadata returns the existing source. A conflicting filename/license is rejected.
No database schema change is needed for these records.

Share ZIP directory/path validation between model inspection and raw-source
extraction. Extract source ZIP/DOCX members only into a newly created private
temporary directory. Never extract into a caller-supplied existing directory,
restore archive permissions, follow archive links, execute members, or recursively
unpack nested archives. Check paths, collisions and declared resource bounds
before writing, then check actual expanded bytes, CRCs and elapsed time while
copying. Remove the directory on success or failure. Model inspection remains
stream-based and does not extract model files, as required by ADR 0003.

Remove unprocessed source records and their placeholder Documents on an explicit
source-removal request; retain the underlying Artifact and object. Reject removal
when canonical documents, conversation imports, or dataset versions exist. Until
version manifests have explicit source membership, the version check conservatively
protects every source in that dataset. Lock the dataset, source and document rows
while making this decision. Existing foreign keys preserve referenced records.

## Consequences

Initial types are TXT, Markdown, PDF, DOCX, JSON, JSONL, CSV and ZIP. Text admission
checks UTF-8 and rejects binary NULs; PDF admission checks the header; DOCX checks
its ZIP envelope and expected members. These are admission checks, not complete
format parsing. Conversation schema detection and document processing stay in
Milestones 6 and 7. ZIP members can include attachments and are retained as inert
bytes without executing or recursively processing them.

Raw source extraction requires temporary disk up to the expanded-size limit,
in addition to the ArtifactStore spool. Limits and HTTP behavior are documented
in [the dataset reference](../datasets.md). Removal is not permanent erasure;
explicit raw-object deletion and retention policy remain in Milestone 23.
