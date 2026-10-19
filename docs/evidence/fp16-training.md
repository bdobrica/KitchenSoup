# Explicit FP16 training validation

Date: 2026-10-19

Added explicit BF16/FP16 plan selection, precision-aware runner preflight and GPU
smoke commands. Recipe defaults remain BF16. ADR 0012 supersedes the previous
BF16-only execution policy; translator v2 records the new preflight semantics.

## Verification

- `make openapi`: regenerated the six affected API/runner schema documents.
- Focused training-plan/runner tests: 56 passed, including explicit precision
  resolution, invalid precision rejection, hardware selection mismatch, unchanged
  legacy BF16 snapshot hashes, and FP16 snapshot tamper detection.
- `make verify`: formatting, Ruff, strict mypy (93 files), and all 175 unit tests
  passed outside the sandbox. The initial sandboxed suite stalled in Starlette
  TestClient cross-thread startup and was interrupted; the unsandboxed rerun passed.
- Compiled the changed trainer entrypoint and smoke/probe scripts; checked the
  GPU probe's `--precision` CLI help.
- Reviewed the diff for unrelated edits, generated drift and sensitive data.

## Initial validation limits

Docker image inspection failed with Docker socket permission denied, including
an escalated retry. Container/CLI dry runs, disposable database integration tests,
and actual BF16/FP16 GPU training were not run. No model weights were downloaded.
The successful GPU follow-up below resolves that pending validation.

## Local executor FP16 GPU validation — Milestone 12 complete

Date: 2026-10-19

The milestone acceptance criterion was narrowed to an actual FP16 fine-tune at the
user's request; see [ADR 0013](../adr/0013-fp16-milestone-validation.md).
BF16 hardware validation is optional and was not performed.

- GPU: NVIDIA GeForce RTX 2070 SUPER, compute capability 7.5, 8192 MiB VRAM.
- NVIDIA driver: 595.71 (host-reported); container PyTorch 2.8.0, Soup 0.74.0.
- Trainer image resolved by the successful executor run:
  `sha256:ef49ad32e96f560769b3577aa4f7f9b58ae490d3bf95df4bef12f4f6a2fa18ab`.
- Docker access used the account's existing `docker` group membership through
  `sg docker`; the current shell had not inherited that supplementary group.
  No socket permissions or system group configuration were changed.
- `make test-gpu PRECISION=fp16` passed. The target rebuilt the pinned trainer,
  passed the Docker CUDA/FP16 probe, generated a tiny random Qwen2 and synthetic
  conversation bundle, and performed actual FP16 LoRA training through
  `LocalDockerTrainingExecutor` using unchanged production GPU launch arguments.
- The harness required the runner manifest to report `succeeded` and the executor
  to report `SUCCEEDED`. Executor success verifies exit codes, run/input/plan/image
  identity, required adapter configuration and safetensors files, and adapter hashes.
- The same smoke verified stdout availability, stopped-container cleanup, log
  access after cleanup, retained workspace files, and cancellation/cleanup of a
  separate synthetic running process. Test containers were removed automatically.
- The initial two-step fixture failed Soup's finite-metric check with a final
  `grad_norm=nan` during FP16 GradScaler warm-up. The fixture now uses eight epochs
  over two examples (16 steps), allowing scaler recovery. The successful rerun
  passed Soup's final finite-metric and trainable-weight checks without disabling
  numerical checks or changing production recipes, parameters, or Soup code.
- `make verify` passed: formatting, Ruff, strict mypy (93 files), 175 unit tests.

The synthetic test downloaded no model weights, used no providers, and did not
create application database records. This validates execution and artifact
integrity, not model quality or the VRAM fit of larger models. Disposable database
integration and browser checks were not repeated; earlier lifecycle evidence is in
[milestone-12.md](milestone-12.md). Milestone 12 is complete under the requested
FP16 acceptance criterion; BF16 execution remains untested on this hardware.
