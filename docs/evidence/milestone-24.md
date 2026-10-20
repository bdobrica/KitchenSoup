# Milestone 24 — Documentation and first MVP release

Date: 2026-10-20 (operator local date). Release: `v0.1.0`.

## Scope

Finalized concise README and installation/first-workflow instructions. Added
architecture, all-Make-target reference, executor prerequisite/example index,
model-license behavior, security assumptions, contributor guide, changelog and
release procedure. Added inert SSH/Runpod target and OpenAI/LiteLLM provider JSON
examples and a fail-closed host OIDCGate configuration. Corrected obsolete
pre-worker/pre-secret-reference statements in development documentation.

No runtime behavior, public contract, dependency, database migration or engine
image source changed. Version 0.1.0 already matched package and FastAPI metadata.
Reviewed Apache-2.0 repository license, wheel metadata/license inclusion, original
source header conventions and upstream dependency/image notice boundaries.
THIRD_PARTY_NOTICES is a reference inventory, not a complete transitive SBOM or
legal compatibility determination. No third-party source was copied for this work.

## Verification

- `make verify`: Ruff format (314 files), lint and strict mypy (167 source files)
  passed; 470 unit tests passed. Existing Starlette httpx deprecation and intentional
  duplicate-ZIP-member fixture warnings remain.
- `make compose-check`: passed through the authorized SSH environment; expanded
  credential values were not printed.
- `make test-integration`: 118 passed through SSH, including migration round trips
  and drift checks; the harness removed its disposable PostgreSQL/RustFS/Valkey.
- `make openapi`: regenerated contracts; `git diff --exit-code -- docs/contracts`
  confirmed no drift.
- Validated all 11 deployment JSON examples with the existing Pydantic models
  where applicable, and parsed the OIDCGate YAML with fail-closed setting checks.
- Confirmed exact documentation coverage of all 57 Make help targets and checked
  245 local Markdown file links across root guides and durable reference pages.
- Inspected all 147 wheel entries: version 0.1.0, Apache-2.0 metadata and LICENSE,
  templates/static/catalog inclusion, and absence of credential/runtime files.
- Real CPU Soup document smoke passed: text, Markdown, DOCX and PDF rows with
  byte-identical replay, plus captured invalid-PDF failure. The normal
  `make test-soup-ingest` build encountered unavailable container egress and was
  stopped. A temporary Compose host-network build override was rejected by Bake
  for missing entitlement. Retried successfully with direct
  `docker build --network host -f images/soup-ingest/Dockerfile` and then the same
  `.venv/bin/python scripts/test_soup_ingest.py` recipe. Repository Compose and
  runtime networking remain unchanged.
- Reviewed final documentation/example diff for unrelated changes, generated
  drift and credential patterns; `git diff --check` passed.

The package was built locally using `uv build --wheel --out-dir` in `/tmp`, with
an isolated build backend. It was not published. The default `.venv` has no pip
module in this existing checkout; the isolated builder avoids changing it.
Sandbox path casing made Ruff cache and generator writes read-only; rerunning
those commands with authorized permissions succeeded.

OIDCGate field names and launch command were checked against public upstream
revision `1b30b5bfc5b08c87adff7b4ed23c5f394c389237` (configuration example,
`internal/config/config.go`, `cmd/oidcgate/main.go`). Its separate server was not
built or deployed. No live identity-provider login, TLS ingress, remote-browser
object-store access or browser JavaScript execution was tested. The guide marks
those as operator acceptance checks rather than verified installation paths.

No live GPU/provider validation was rerun for a documentation-only release.
Existing backend evidence remains in this directory, including FP16 local/SSH/
Kubernetes/SageMaker and Runpod acceptance. It does not imply every catalog model
or artifact format was live-tested. No AWS/Runpod resources or costs were created.

## Release boundary

The release uses a local annotated `v0.1.0` tag on the coherent documentation
commit after successful checks. No remote push, GitHub release, package upload,
image publication or deployment is included. Dependencies are not fully locked,
browser enhancements rely on a CDN, and the trusted single-user security model
remains explicit. Leases require a running reconciler and provider access;
SageMaker can reject deletion during Creating. Those limitations are retained in
the changelog and operator guides.
