# Milestone 12 validation — local Docker training executor

Date: 2026-09-06

## Implemented scope

Saved-plan submission creates an immutable TrainingRun snapshot and persists its
deterministic external container name before engine effects. The local executor
materializes hash-verified registered model/dataset inputs, resolves the pinned
image identity, and supports inspect/status, live private logs, cancellation and
stopped-container cleanup. Additive API routes and browser pages expose those
operations. Default Compose remains without Docker access; the documented opt-in
host mode provides local execution. No new dependency or database migration.

## Checks performed

- `make verify`: formatting, Ruff, strict mypy (93 source files), **163 unit tests passed**.
- `make test-integration`: **46 tests passed** against disposable PostgreSQL/RustFS,
  including saved-plan submission, persisted identity across service restart,
  API status/logs/cancel/cleanup, uploaded-model materialization, failed preparation
  and rejection of changed artifact bytes.
- `make test-local-training`: passed with the real local Docker daemon and pinned
  Soup image. Generated a tiny random Qwen2 and synthetic conversations offline;
  submitted through the executor with a test-only GPU-argument override and runner
  CPU validation. Verified logs, cancellation of a separate running synthetic
  process, cleanup, and retained workspace files. Validation is correctly excluded
  from training success. Production submission has no CPU override.
- Tested trainer image identity:
  `sha256:73b8af7a13be7cef20e434c73de0b186e7e919dd5cf14a82efa95326e5767ac5`.
- Focused tests verify zero exit without a valid success manifest is failure,
  required adapter hashes, foreign-container rejection, repeat cancellation/cleanup,
  canceled unstarted containers, log link/FIFO rejection, live log visibility before
  CLI exit, exact Hub commit paths, redirect restrictions and download bounds.
- `make openapi` regenerated the additive v1 API contract; existing AppSpec/runner
  wire definitions remain unchanged.
- Optional Compose configuration validated with `config --quiet`.
- Started the documented host application using `make dev-training` and the retained
  local PostgreSQL/RustFS infrastructure. A read-only request to the run-list API
  returned 200 and a JSON list. No training row or dataset was added to that database.
- Chromium browser smoke against a temporary application server with synthetic API
  responses passed: saved-plan submission/navigation, status/log display, cancel,
  cleanup, and rendering HTML-like log content as text. No JavaScript page errors.
- Reviewed the final diff for unrelated edits, generated drift and sensitive payloads.

## Hardware checks and limits

`make gpu-check` failed explicitly because local Docker could not expose CUDA.
The previously identified development GTX 1660 Ti also lacks native BF16 required
by recipe v1. `make test-gpu` is implemented but actual BF16 execution through this
executor was **not run**. That acceptance check remains open in TODO.md for suitable
hardware. No SageMaker job, paid provider call, remote model-weight download or
GPU quality/VRAM benchmark was performed.

The CPU smoke validates the real Soup dry-run and Docker lifecycle; it is not a
BF16 training result. Prior CPU training evidence remains in Milestone 11. Automatic
queue/reconciliation, distributed claims, output registry/retention and aggregate
workspace quotas are not supplied by this milestone; see ADR 0011 and the local
training reference for operator requirements and crash/retry behavior.

Temporary test containers and application processes were removed/stopped. Local
Compose services were stopped afterward, retaining existing volumes and `.env`.
The built trainer image remains available. Synthetic API/browser fixtures contained
no user runtime payloads or credentials.
