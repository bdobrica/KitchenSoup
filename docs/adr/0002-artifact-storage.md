# ADR 0002: Verified artifact registration through S3 staging

Status: Accepted

Date: 2026-09-06

## Context

Milestone 3 requires browser uploads that bypass FastAPI, S3-compatible storage,
and authoritative SHA-256 metadata in PostgreSQL. Presigned PUT URLs can be
reused until expiry and can overwrite their target object. Registering that
same key would allow its bytes to change after hashing.

## Decision

Use a synchronous ArtifactStore protocol and a boto3 S3 adapter. Synchronous
FastAPI handlers run blocking storage and database operations in worker threads,
consistent with ADR 0001. This replaces the illustrative asynchronous storage
signature in PLAN.md without adding a second database concurrency model.

Reserve an upload UUID in PostgreSQL before returning a short-lived presigned
PUT for `uploads/v1/{uuid}/{safe_filename}`. Bind the signature to the declared
size and media type. Completion locks the reservation, verifies the actual size
and SHA-256, and writes those exact bytes to `raw/v1/{uuid}/{safe_filename}`.
Only then commit the artifact metadata and completion link. Never issue browser
PUT grants for registered keys. Repeated completion returns the same artifact.

Hash the downloaded object bytes, not an ETag or client-provided checksum.
Spool downloads beyond 8 MiB to temporary disk and enforce configured upload
bounds while reading. The adapter uses SDK-managed multipart uploads for large
server writes; the browser uses a single PUT.

Keep internal storage traffic and browser signing endpoints configurable
separately. Keep credentials in operator settings; return only scoped expiring
URLs. Provision bucket CORS explicitly through a one-shot local Compose service.
The versioned HTTP schema and [storage reference](../storage.md) describe the API.

## Consequences

Completion transfers bytes through the server for verification and takes a
database row lock for that operation. It requires temporary disk capacity and
is intended for the existing trusted single-user application.

PostgreSQL and S3 do not share a transaction. Failed completion can leave an
unregistered final object; expired, abandoned, or replayed staging uploads can
also remain. Retrying completion is safe. Cleanup after a committed registration
is best effort; scheduled retention remains future work. Keep the configured
store stable while uploads are pending.

Real disposable RustFS integration tests exercise the adapter and signed HTTP
requests, so an additional local/test adapter is unnecessary at this milestone.
AWS S3 compatibility is an adapter design goal, not a live-provider test claim.

Reference: [S3 presigned URL behavior](https://docs.aws.amazon.com/AmazonS3/latest/userguide/using-presigned-url.html).
