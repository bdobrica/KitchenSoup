# Artifact storage

Run `make migrate`, then `make up`, and open `/artifacts`. Choose a file, upload
it directly to RustFS, and wait for the displayed SHA-256 before downloading.
The page uses packaged JavaScript and needs no CDN or Node build for this flow.
Raw uploads do not yet create datasets or import conversations.

## Configuration

Compose enables storage for web and provisions the `kitchensoup` bucket with
CORS for the localhost web origins. `make storage-init` safely repeats bucket
creation/CORS configuration; it replaces the bucket's existing CORS rules.
It does not make the bucket public or apply migrations.

Settings below use the `KITCHENSOUP_` prefix. Customize Compose environment
mappings when overriding settings not already mapped from `.env`.

| Setting | Default / purpose |
| --- | --- |
| `STORAGE_ENABLED` | false outside Compose; disabled APIs return 503 |
| `S3_ENDPOINT` | `http://rustfs:9000`, reachable by the server |
| `S3_PUBLIC_ENDPOINT` | `http://127.0.0.1:9000`, reachable by the browser |
| `S3_REGION` / `S3_BUCKET` | `us-east-1` / `kitchensoup` |
| `S3_ACCESS_KEY` / `S3_SECRET_KEY` | Required when enabled; Compose uses ignored `.env` RustFS credentials |
| `S3_ALLOWED_ORIGINS` | Comma-separated web origins used by provisioning |
| `STORAGE_URL_TTL` | 300 seconds; configurable from 1 to 3600 |
| `UPLOAD_MAX_BYTES` | 1 GiB; configurable from 1 byte to 5 GiB |

If changing `RUSTFS_API_PORT`, also update `KITCHENSOUP_S3_PUBLIC_ENDPOINT`
in `.env`. Browser grants must be signed for the endpoint actually used; do not
rewrite their hostname after signing. Keep the bucket and endpoint stable during
pending uploads. Artifact downloads reject metadata for a different bucket.
Credentials and signed URLs are secrets: do not record them in application logs
or source control. The existing unauthenticated, localhost-only deployment
assumptions still apply; CORS is not authorization.

## Version 1 HTTP contract

The [generated OpenAPI contract](contracts/artifacts-v1.openapi.json) describes
request/response schemas. Regenerate it with `make openapi` after changing API
models or routes. Unit tests check generated-schema drift. Preserve existing
fields and status meanings for compatible v1 changes; incompatible changes need
a new version and corresponding documentation/tests.

| Method and path (under `/api/v1`) | Result |
| --- | --- |
| POST `/artifact-uploads` | 201: upload UUID, signed PUT URL, required headers, URL expiry and completion deadline |
| POST `/artifact-uploads/{upload_id}/complete` | 200: verified artifact metadata; safely repeatable |
| GET `/artifacts/{artifact_id}` | 200: UUID, SHA-256, byte size, format and creation time |
| POST `/artifacts/{artifact_id}/download` | 200: signed GET URL and expiry in seconds |

Initiation accepts `filename`, integer `size_bytes`, and optional `content_type`
(default `application/octet-stream`). Unknown properties are rejected. PUT the
exact file body to the grant URL with the returned headers. The browser supplies
Content-Length automatically; non-browser clients must send the declared length.
Completion is permitted for one hour after reservation. Already completed uploads
remain repeatable after that deadline. For raw uploads, `format` is the declared
media type; no content-type detection is claimed.

Errors use `{"detail": "message"}`, except FastAPI's structured 422 validation
errors. Status meanings: 404 missing upload/artifact/object; 409 size mismatch or
different configured bucket; 410 completion expired; 413 upload limit exceeded;
422 invalid input; 503 storage/database unavailable or storage disabled.
Retry completion after transient failures. Grants and artifact metadata responses
use `Cache-Control: no-store`. Downloads force attachment/octet-stream treatment.

## Storage and operational limits

[ADR 0002](adr/0002-artifact-storage.md) records staging/final key isolation and
transaction behavior. Keys use a UUID and a sanitized filename, never user paths.
SHA-256 covers the exact finalized bytes, including empty files. S3 object
versioning is not required. ArtifactStore exposes put/get/stat/delete/presigning;
there is intentionally no public artifact-delete API yet.

Server writes use managed multipart transfers starting at 8 MiB. Browser uploads
use a single PUT with no multipart resume UI. Verification spills beyond 8 MiB
to temporary disk, so allow disk capacity for concurrent uploads. No automatic
retention job removes abandoned uploads or orphaned objects yet.

`make test-integration` starts isolated PostgreSQL and RustFS containers with
ephemeral data and generated credentials, exercises migrations, transfers,
hashing, CORS, replay protection and completion races, then removes the containers.
See [Milestone 3 evidence](evidence/milestone-3.md) for browser validation.
