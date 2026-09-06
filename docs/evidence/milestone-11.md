# Milestone 11 validation

Validated on 2026-09-06 using the existing Python 3.13 development environment and
the separate pinned Python 3.12 Linux/amd64 trainer image.

- `make fmt`, `make openapi`, and `make verify` passed formatting, lint, strict
  types and 152 unit tests. New coverage includes exact parameter translation,
  plan/recipe consistency and serialization, file paths and hashes, template and
  remote-code handling, final-assistant labels, overlength rejection, bounded
  stdout/stderr, exit codes/timeouts, sanitized child environment, failure/success
  manifests, attempt immutability and generated schema drift.
- `make test-integration` passed all 43 existing PostgreSQL/RustFS tests. No database
  migration or control-plane dependency was added. The previously published
  OpenAPI document is byte-equivalent as parsed JSON; the two runner schemas are
  separate additive contracts.
- `scripts/lock_soup_trainer.py` resolved the image's full dependency set in its
  pinned base. `make build-soup` built successfully and `pip check` reported no
  broken requirements. Soup 0.74.0, torch 2.8.0, Transformers 5.16.1, PEFT 0.20.0,
  TRL 0.29.0 and transitive dependencies are pinned in the generated lock.
- `make test-soup` passed with networking disabled, read-only root/input mounts,
  dropped capabilities, a non-root UID and container resource limits. The fixture
  generates a tiny random Qwen2 model and tokenizer locally and uses two synthetic
  conversation examples. No model weights or source material were downloaded.
- The production entrypoint verified the bundle, prepared final-target labels,
  generated `soup.yaml`, invoked the real `soup train --dry-run` CLI and emitted a
  `validated` output manifest. Tests checked image identity, retained labels,
  log/exit metadata and refusal to overwrite the same attempt workspace.
- A separate CPU CLI-only probe trained that tiny fixture using the generated
  configuration and produced `adapter_config.json` and nonempty
  `adapter_model.safetensors`. Soup uses FP32 on CPU; this verifies the actual
  pinned Soup/TRL training API and adapter output, not BF16 runner execution.
  Production runner precision checks were not bypassed or weakened.
- The CPU-tested content-addressed image identity was
  `sha256:5f04555f1fe183be6b05e58187a562293e2a48a52344f54e8d991642fbece951`.
  The launcher uses the inspected image identity for both launch and recording;
  this is local Docker provenance, not remote attestation. Rebuilding may change
  the OCI index/attestation digest even when cached root filesystem layers match.
- `make test-soup-gpu` was attempted but could not launch: Docker reported
  `could not select device driver "" with capabilities: [[gpu]]` (exit 125).
  The host reports a GTX 1660 Ti with 6 GiB; it also lacks native BF16 support.
  Actual BF16 GPU training remains unverified. A configured NVIDIA runtime and
  compatible GPU are required to complete that optional smoke test.
- Smoke containers and temporary synthetic workspaces were removed. Existing
  application data/volumes were untouched, and no application TrainingRun,
  provider call, deployment or external job was submitted.

Existing Starlette/AnyIO deprecations and the deliberate duplicate-ZIP warning
remain unchanged. Docker's secret-name heuristic flags the constant
`HF_HUB_DISABLE_IMPLICIT_TOKEN=1` privacy setting; no secret value is present.
No Soup Python modules are imported by KitchenSoup or the harness. Source inspection
and CLI checks informed the pinned adapter, as linked in [the runner reference](../soup-training.md).
