# ADR 0015: Registered training outputs and independent lineage graphs

Status: Accepted

Date: 2026-10-19

## Context

Milestone 14 connects the verified offline runner output to the application-owned
registry. ADRs 0001 and 0003 separate logical models, source-license provenance
and artifact derivations. ADR 0014 deliberately deferred registry import.

## Decision

Supersede ADR 0014's executor-verification-only POSTPROCESSING completion. Commit
POSTPROCESSING before importing bytes, then register the trained model, adapter,
source provenance, model-parent and artifact-derivation edges and SUCCEEDED in
one PostgreSQL transaction. Keep the existing per-run database claim across this
operation. Storage/executor failures leave POSTPROCESSING retryable; dispatch is
never replayed. Run and event reads do not acquire write locks during import.

Each successful attempt creates a new logical model with version 1 and a parent
edge to its exact base version. An optional unique training-run foreign key binds
that version to its immutable run. Deterministic run-derived model/version/artifact
UUIDs and object keys make retry after an ambiguous storage write safe. A committed
registration is reused without copying bytes again. Existing imported versions
retain a null run relationship. Existing terminal runs are not retroactively changed.

Extend the executor port with a bounded verified-output stream. The local executor
locks the workspace, revalidates successful container/runner identity, then copies
only inventoried adapter files and the runner manifest into an inert ZIP. Check
confined regular files and their hashes again while copying; do not execute or
extract outputs. Inventory is capped at 4096 unique files and 8 GiB adapter bytes.
The registered format is `kitchensoup.lora-adapter+zip/v1`; SHA-256 and size cover
the exact ZIP, whose embedded manifest retains individual adapter hashes. No new
runner wire schema, engine launch privilege or trainer-image change is introduced.

Copy the reviewed run's base source identity, exact revision or uploaded artifact
and declared license, retaining the registered source's reference URL and notes.
Submitted sources are ungated. This remains source
provenance, not a claim that the adapter itself was downloaded from that source.
The model-parent graph records logical descent; the artifact graph records byte
inputs: base representations when registered locally, dataset manifest and examples.
Public Hub revisions remain model source provenance, without fictitious base-weight
artifacts. Artifact traversal also retains existing upstream dataset derivations.

Lineage edges are append-only and acyclic, enforced in PostgreSQL for both graphs.
Serialize graph insert checks with a transaction advisory lock and reject self or
transitive cycles. Trained version identity/run association cannot be rewritten.
Expose separately queryable ancestor graphs and typed nodes/edges, with UUID-only
Mermaid text. Render labels and provenance as text in packaged browser code and
use a local SVG visualization without new dependencies or remote renderers.

A trained adapter is selectable as a logical model but is not a complete
Transformers model. Base-requirement detection reports the missing merged
representation; plan creation fails with 409 rather than silently training against
its ancestor. The UI's use-as-base link selects that exact version and explains the
requirement. Merge execution and subsequent deployment remain Milestones 17 and 16;
no eager merge, adapter composition or unsupported source type is introduced here.

## Consequences

A newly completed run is SUCCEEDED only after its reusable representation and
lineage are durably registered. GET responses gain optional model identifiers;
registered versions gain additive run, artifact and parent identifiers. Existing
fields and status meanings remain compatible. Generated OpenAPI documents these
additions and the new lineage/base-requirement GET routes.

PostgreSQL and object storage still do not share a transaction. An interrupted
import can leave an unregistered deterministic object; retry safely overwrites it.
Automatic retention remains future work. Registration holds a database write
transaction during storage transfer and requires private temporary disk capacity.
The local operator must keep the workspace, image, daemon and store available until
registration completes. Byte verification does not establish numerical quality.
No GPU execution is needed to validate this registry boundary with synthetic outputs.
