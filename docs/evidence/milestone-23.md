# Milestone 23 validation: error UX and safety polish

Date: 2026-10-19

## Implemented boundary

[ADR 0022](../adr/0022-error-ux-and-object-retention.md) and
[the reference](../safety-and-retention.md) define payload-free error explanations,
bounded advisory training diagnostics, Advanced normalized details/private raw logs,
confirmed raw-source deletion and conservative object retention. Existing uploads,
archive safeguards and provider sharing notices remain in force. Playground launch
discloses endpoint exposure. No dependency or executor authority is added.

The new migration records durable raw-deletion intent/completion and completed
staging sweeps. Raw deletion preserves artifact/source metadata, rejects shared,
canonical, imported and versioned originals, blocks new use while pending, and
retries safely after either raw-copy or staging-copy deletion failure. PostgreSQL
guards prevent removing tombstones or reverting completed deletion. Processed-source
erasure and arbitrary object-store garbage collection remain unsupported.

## Repository gates

- `make verify`: Ruff formatting/lint, strict mypy (**149 source files**) and
  **361 unit tests passed**. Error coverage includes GPU/host OOM, gated/public model
  downloads, storage and connectivity, sanitized unknown exceptions/validation,
  safe Advanced details and bounded diagnostics without lifecycle actions.
- `make test-integration`, over the existing local SSH connection on port 2222:
  **84 tests passed** against disposable PostgreSQL 18.6, RustFS beta.12 and Valkey.
  Includes full migration downgrade/upgrade, repeated upgrade and ORM drift checks,
  real raw/staging deletion, confirmation, tombstone guards, replay refusal,
  protected provenance, storage-outage recovery and retention batch progression.
- Existing upload-completion integration tests verify declared/actual size mismatch,
  configured limits, object hashing and replay. Existing archive tests verify
  compression bombs, entry/directory/expansion limits, traversal, links, CRC errors
  and temporary-workspace cleanup; no limits were increased.
- `make openapi` regenerated the additive routes/source field and the standalone
  `error-response-v1.schema.json` from application definitions.
- Final focused checks reran lint/types and 22 unit/HTTP-contract tests. A separate
  disposable integration check directly verified that both the registered raw key
  and a replayed staging key are absent after confirmed deletion.
- `make storage-retention` without confirmation refused before invoking Compose;
  the target is listed by `make help`. Actual sweep behavior was exercised only
  against disposable test data.
- HTTP template checks passed for Advanced logs and sharing/exposure notices.
  Node.js/browser automation is unavailable; interactive JavaScript was reviewed
  but no browser execution or JavaScript syntax-tool result is claimed.

## Environment and limits

No AWS account was used; Milestones 21–22 remain pending. Kubernetes/Helm settings,
the single-node cluster and GPU workloads were not changed. No new live GPU test
was needed for this control-plane slice; OOM classification uses synthetic error
fixtures. Live-provider sharing and externally exposed endpoints were not tested.

No installed application migration, real source deletion, production retention
sweep, bucket lifecycle rule or recurring cleanup job was executed. Operators must
run `make migrate` and deploy the updated application before using these features.
Registered objects otherwise retain indefinite retention. Backups, object versions,
replicas, downloads and still-valid signed URLs need separate operator controls.
Validation evidence contains no credentials, source payloads or presigned URLs.
