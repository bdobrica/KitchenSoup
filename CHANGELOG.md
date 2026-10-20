# Changelog

## Unreleased

Tag-triggered image publication reuses CI, verifies release versions and publishes
digest-pinned Compose installation assets through GitHub Releases.

Published non-root control-plane and isolated Soup-ingestion images, with a
standalone digest-pinned Compose installation and automatic one-shot database
migrations. The quick start no longer requires a checkout, Python or Make.

## 0.1.0 — 2026-10-20

First self-hosted MVP: immutable dataset versions and reviewed Soup plans, durable
training lifecycle and verified model/adapter registration, separate model/artifact
lineage, local paired evaluation, merge/AWQ representations, and leased vLLM chat.
Training and serving support local Docker, Docker-over-SSH, Kubernetes, SageMaker
and Runpod with backend-specific opt-ins and cleanup. External LLM providers use
OpenAI-compatible requests and operator-managed credential references.

This is a trusted single-user release. It has no internal authentication or
multi-tenant permissions; default Compose exposes only loopback services and has
GPU execution disabled. Cloud GPU resources remain billable during provisioning,
idle time or failed cleanup. Leases depend on the reconciler and provider access.
Native token streaming varies by backend; SageMaker uses one-delta SSE fallback.
Dependencies are not fully transitively locked; browser enhancements use a CDN.
See [installation](docs/installation.md), [security](docs/security.md),
[release procedure](docs/releases.md) and [validation evidence](docs/evidence/milestone-24.md).
