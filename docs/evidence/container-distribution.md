# Standalone container distribution validation

Date: 2026-10-20. Platform: local Linux/WSL2 Docker, linux/amd64.
Application code/package version: 0.1.0, based on commit 8745b49; packaging and
installation changes are included in the accompanying commit. The manual image
build uses the default `unknown` revision label; registry digests identify the
published bytes. No existing Git release tag was moved.

## Published images

Repository: `quay.io/bdobrica/kitchensoup` (public).

| Role | Tag | Registry manifest/index digest |
| --- | --- | --- |
| Control plane | `0.1.0` | `sha256:8a3e8b6fe37f4da2066871b0bf2df13cfd645a99d99deac375c620de92b79739` |
| CPU Soup ingestion | `soup-ingest-0.1.0` | `sha256:261d638f333fb523365ed88b3dd87e80714119f0c2391d5d3807b68b6285c0dd` |

The ingestion image configuration ID is
`sha256:57cad380cb88e6ba40b3068683486d4d8443323d08c37f4f930f8981dc8c44c4`.
Compose supplies that exact identity to the unchanged runner provenance contract.
This configuration ID differs from the registry manifest/index digest.
The application runs as UID/GID 10001 and contains 106,438,024 bytes of compressed
linux/amd64 layers (about 106 MB); Docker reports 503,744,294 bytes locally.
Soup/vLLM training/inference frameworks are absent from the control plane.
Python application dependencies are range-resolved, not fully transitively locked.
The base Python image and published application/ingestion images are digest-pinned.

## Checks performed

- Built the multi-stage application image and existing isolated Soup-ingestion
  image with the local Docker daemon. The application contains its installed wheel,
  UI assets/catalog and migration tree, without the repository or build workspace.
- Pushed both versioned images and checked public registry manifests using an
  empty Docker configuration directory (no registry credentials).
- Successfully pulled every image in the final standalone Compose file using
  that empty Docker configuration directory, including the exact app/ingestion pins.
- Started all ten services from a temporary directory containing only Compose and
  private generated credentials; no checkout mounts or local builds. Used a
  distinct Compose project, fresh disposable volumes and alternate loopback ports.
- `docker compose up -d --wait --wait-timeout 180` passed. Migration, catalog and
  storage initialization exited successfully; all seven long-lived services were
  healthy, including worker/reconciler heartbeat checks.
- Loaded real health/UI/static routes. Created a synthetic dataset, uploaded TXT
  through a real presigned RustFS URL, verified its SHA-256, attached the source,
  and successfully ingested it through the real Soup CLI sidecar.
- Stopped/recreated the stack with retained volumes, then recreated it using the
  final digest-pinned file. Verified the dataset, source and successful ingestion
  remained available. `python -m pip check` passed in the published app image.
- Removed the disposable stack, networks and all four volumes. Container/volume
  queries for its project label returned no resources. Existing application stacks
  and their data were not targeted. Published images intentionally remain.
- `make verify`: format/lint, mypy and all 470 unit tests passed.
- `make compose-check`: existing development Compose configuration passed.
- `make test-integration`: all 118 integration tests passed, including migrations
  and real disposable PostgreSQL/object-store workflows.
- The exact documented container-based credential command generated three random
  values into a mode-0600 file; neither credentials nor presigned URLs were recorded.

The initial sandbox verification attempt could not write Ruff's cache through the
workspace path alias; the gate was rerun successfully with filesystem permission.
No cloud/GPU resources, paid provider operations, new schema/API changes, or live
training/inference checks were needed. ARM64 and a complete UI-driven GPU workflow
were not tested. Tag-triggered image publication remains follow-up work.
