# Soup document ingestion

Run `make migrate`, then `make up`. Open a dataset, upload documents, select them
under **Process documents**, and choose **Process selected documents**. Each attempt
appears in the history with warnings and downloads for the extracted dataset,
ingestion record and Soup logs. Failed CLI attempts retain logs and originals
without publishing partial dataset output. Repeating an action creates a new
numbered extraction; existing records and artifact bytes remain unchanged.

## Engine boundary and supported inputs

The image invokes the public `soup data ingest source.ext --output output.jsonl`
command from Soup 0.74.0. KitchenSoup validates the resulting wire format; all
PDF, Word, Markdown and text extraction is Soup's responsibility.

| Input | Soup output | Limitations surfaced in ingestion records |
| --- | --- | --- |
| TXT | One text row | Empty/whitespace text is ignored and counted |
| Markdown | Heading sections | Markdown is retained as text; referenced resources are not fetched |
| PDF | Text per page, zero-based `page` | No OCR or image transcription; empty pages are counted |
| DOCX | Nonempty paragraphs, zero-based `para` | Tables and images are omitted |
| JSON, JSONL, CSV, ZIP | Ignored when explicitly selected in an API batch | `unsupported_document_type`; at least one supported document is required |

Upstream references: [Soup document ingestion documentation](https://github.com/MakazhanAlpamys/Soup/blob/v0.74.0/docs/data.md)
and [CLI implementation](https://github.com/MakazhanAlpamys/Soup/blob/v0.74.0/src/soup_cli/commands/data.py).

The `DocumentRunner` protocol is the application boundary. The default HTTP adapter
uses the operator-only `KITCHENSOUP_SOUP_INGESTION_URL`; an empty setting disables
new ingestion outside Compose. Users cannot supply runner addresses or commands.
Compose runs the parser on an internal network shared with web, with no public
port, external egress, storage credentials or Docker socket. Do not expose the
runner independently: its private transport assumes trusted application callers.
See [ADR 0006](adr/0006-soup-document-ingestion.md).

## Versioned artifacts and HTTP contract

Two routes extend the [existing v1 OpenAPI contract](contracts/artifacts-v1.openapi.json):

| Method and path under `/api/v1` | Result |
| --- | --- |
| POST `/datasets/{dataset_id}/document-ingestions` | 200: a new succeeded or failed attempt; body `{"source_ids": ["uuid", "..."]}` |
| GET `/datasets/{dataset_id}/document-ingestions` | 200: attempts, newest extraction first, with provenance and artifact UUIDs |

Duplicate source UUIDs are deduplicated and sources are processed in UUID order.
Foreign/missing source IDs return 404 before calling Soup. Unsupported-only
selections return 422; input limits return 413; hash/store mismatches return 409;
runner unavailability/invalid transport returns 503 and oversized transport 502.
Responses use `Cache-Control: no-store`. A CLI nonzero exit is a persisted failed
attempt, not an HTTP transport error. Input/transport failures do not create an
attempt. Inspect `status` before using `output_artifact_id`, which is null for a
failed attempt. Source removal returns 409 for datasets with ingestion history.

The [ingestion manifest schema](contracts/document-ingestion-v1.schema.json),
`kitchensoup.document-ingestion/v1`, records the selected source artifact IDs,
SHA-256 hashes and declared licenses, per-source dispositions/warnings, Soup version,
image ID, runner mode, command and output/log artifact IDs. The command template's
`{extension}` is replaced only by a supported lowercase extension. `example_count`
counts extracted text rows, not a resolved training recipe's final examples.

The output format `kitchensoup.document-examples/v1` is UTF-8 JSONL. Each line has
`source_id` (the dataset source UUID) and `data` (the original Soup JSON row with
required string `text`; Soup may include `source`, `page`, `para`, `section`, `level`).
Source basenames are fixed `source.ext`; use the UUID and manifest for original
names. Empty text rows are omitted. Raw source bytes, encodings and names remain
available through the original artifact. Outputs are under
`derived/documents/v1/{ingestion_id}/`, separate from `raw/v1/`.

The private transport accepts an exact-length binary POST to
`/v1/ingest/{extension}` and returns the generated
[Soup response schema](contracts/soup-ingest-response-v1.schema.json).
It never accepts caller-selected paths, CLI options, credentials or environment.
Logs are JSON containing per-source stdout, stderr, exit status and truncation.
They may contain source text or parser diagnostics: keep them private and do not
copy real logs into source control, evidence or ordinary application logs.

## Limits and reproduction

A request selects at most 16 sources, at most 16 MiB each and 64 MiB combined.
Each Soup process has 60 CPU seconds, a 65-second wall timeout, 1 GiB address space
and a 32 MiB file-size limit. The serial runner has a 1.5 GiB cgroup memory limit,
one CPU, 64 processes and 256 MiB tmpfs. Each stdout/stderr stream is retained up
to 256 KiB; truncation is flagged. Combined output is capped at 32 MiB and each
source at 100,000 rows. Input ZIP admission limits still apply to DOCX originals.
The HTTP adapter allows 90 seconds per source; a full batch can take several
minutes. Concurrent dataset requests can receive busy/timeout errors; retry after
the active batch. Background jobs/cancellation are not implemented yet.

All originals are rehashed before processing. To reproduce, keep the stored
originals, select the manifest's source UUIDs, and run the recorded image with the
recorded mode. New attempts get new UUIDs/version numbers; output bytes should
match for the same image, sources and ordering. The image ID is a Docker content
ID (`sha256:...`), not a registry repository digest. Keep/export that image when
long-term replay is needed; dependency locks alone do not guarantee byte-identical
image rebuilds. No image attestation or registry retention is claimed.

`make up` builds the ingestion image, inspects its actual image ID and injects it
into the runner. A runner without a valid image ID fails health checks. For manual
Compose use, export `SOUP_IMAGE_ID` from `docker image inspect` for the image being
started. Do not rebuild that image between inspection and launch.

Use `make build-soup-ingest` and `make test-soup-ingest` for real CLI fixture checks.
The latter runs network-disabled containers with synthetic fixtures and checks
TXT, Markdown, DOCX and PDF output, byte-identical repeats, and malformed-PDF
failure logs. It needs neither a GPU nor application/database credentials.
Edit `images/soup-ingest/requirements.in`, run
`.venv/bin/python scripts/lock_soup_ingest.py`, then rebuild and test when updating
Soup or parser dependencies. The generated lock pins transitive dependencies;
the Dockerfile pins the Python base digest. The web environment gains no parser
or Soup dependency. Regenerate wire schemas with `make openapi`.
