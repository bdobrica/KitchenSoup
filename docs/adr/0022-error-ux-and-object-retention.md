# ADR 0022: Error explanations and conservative object retention

Status: Accepted

Date: 2026-10-19

## Decision

Add payload-free, advisory error codes, explanations and suggested actions. Keep
the v1 string `detail` contract for existing clients. Classify bounded training
logs on an explicit diagnostics request without storing their contents or changing
run authority, state, retries or cleanup. Raw logs remain in the existing private
log endpoint under Advanced. Never expose arbitrary exception bodies, credentials
or validation inputs through error responses. Existing sanitized domain errors
remain available; unexpected errors receive a generic response.

Registered objects, provenance, snapshots and logs are retained indefinitely by
default. Add an explicit operator retention sweep for expired upload staging and
unregistered deterministic completion objects after a one-day grace period. Lock
upload reservations, preserve registered objects, and keep reservations for audit
and repeatable sweeps. Do not enumerate or age-delete arbitrary bucket objects.
This completes ADR 0002's deferred cleanup policy without altering registration.

Add an explicitly confirmed raw-object deletion action for an attached,
unprocessed, exclusively referenced source. Retain the source and artifact metadata
and record a durable deletion request before external deletion. Reject model,
canonical, shared, derived, versioned or ingestion-history sources. Preserve ADR
0004's existing source-removal contract and conservative protection of provenance;
processed-source erasure is outside this action. Do not infer deletion permission
from an unreferenced object, age, or a cache. Keep a deletion tombstone in PostgreSQL;
block reads, attachment and completion replay while pending or deleted. Retry the
same action after storage failure, with idempotent object deletion. Delete both the
registered raw bytes and their upload staging copy. No automatic background
deletion of registered objects or source metadata is introduced.

Deletion is not guaranteed physical erasure: operator backups, object versions,
replicas, browser downloads and still-valid signed URLs require operator retention
controls. Never apply a bucket-wide expiry rule to registered artifacts.

Existing upload bounds and shared archive extraction limits remain authoritative;
test them rather than raising limits or adding new archive formats. Disclose
external LLM sharing at provider configuration (current calls use synthetic prompts)
and endpoint exposure at playground launch. Future source-bearing provider actions
must disclose their destination and selected content at the action per ADR 0008.
