# Milestone 18 validation: Docker-over-SSH

Date: 2026-10-19

## Implemented boundary

Versioned immutable SSH targets, operator-approved credential references and host
trust, connection/Docker/CUDA probes, SFTP staging, remote Soup and vLLM adapters,
retained target routing, training output verification/registration and compute
selectors are implemented. Existing AppSpec, runner contracts and local default
behavior are preserved. See [SSH setup](../ssh-execution.md) and
[ADR 0019](../adr/0019-docker-over-ssh.md).

AWS credential parsing is explicitly deferred to Milestone 21. Direct presigned
object transfer is a later optional mode; SFTP needs no remote RustFS access.

## Repository gates

- `make verify`: Ruff formatting/lint, strict mypy (137 source files),
  **319 unit tests passed**, including SSH
  credential approval, unknown-host rejection, no implicit credentials, bounded
  SFTP reads, missing-file versus transport errors, safe paths, manifest/hash and
  image binding, restart, cleanup and private-endpoint proxy behavior.
- `make test-integration`: **72 passed** using disposable PostgreSQL/RustFS/Valkey.
  Ran inside the authorized SSH session because that session has Docker group
  access. No `sg`, socket permission change or group modification was used.
- Integration covers target API validation/no-store responses, uniqueness and SQL
  immutability; identical reviewed plan hashes across local and selected-target
  runs; durable target routing after service restart; duplicate dispatch;
  verified-output registration; clone target retention; disabled-target refusal;
  serving readiness/chat routing and lease cleanup retry. It includes migration
  upgrade/downgrade/repeated upgrade and ORM drift checks.
- `make openapi` regenerated the additive API contract and SSH target schema.
  Existing AppSpec, resolved-plan, runner and deployment snapshot schemas did not
  change. Only optional target IDs were added to creation/view API models.

## Actual SSH/GPU execution

Used the user-provided loopback SSH server on `127.0.0.1:2222`, user `bogdan`,
with its already approved host key and file-based Ed25519 credential reference.
No private key bytes, credentials, prompts or runtime payloads are retained here.

Remote identity: UID/GID 1000/1000. Docker Server: 29.9.1.
GPU: **NVIDIA GeForce RTX 2070 SUPER**, compute capability **7.5**, 8192 MiB.
The host's WSL `nvidia-smi` is outside the SSH shell's default PATH; the connection
action queries CUDA capabilities inside the trainer image instead.

The real connection-test implementation passed with synthetic target metadata:
SSH identity, Docker availability, both remote image IDs and CUDA/native-BF16
capability. A private tmpfs is necessary for PyTorch imports under a read-only
root and is included in the probe. Native BF16 was false, as expected.

The successful opt-in command was `make test-ssh-gpu`, with explicit username,
credential reference, approved secret/known-hosts paths and a dedicated temporary
remote workspace. The smoke generated a tiny random one-layer Qwen2 model with
hidden size 128 and two synthetic conversation examples inside the existing
trainer image. It transferred that bundle to local staging, then submitted it
through the production SSH/SFTP training adapter, with no launch overrides.

Verified:

1. New private remote UUID workspace, hash-inventoried input upload and pinned-image
   GPU launch with offline/read-only restrictions and remote non-root identity.
2. **Actual FP16 Soup training**, successful output manifest, matching run/input/
   plan/image identities and per-adapter file hashes.
3. Remote status/log reads, bounded output download and inert adapter ZIP:
   **21,868 bytes**. Cleanup removed the training container and remote run directory;
   locally retained logs remained readable after container removal.
4. A second real training launch was canceled and cleaned up without substituting
   a sleeping/CPU container or changing the production launch.
5. Production serving materialization and SFTP upload of the exact trained adapter
   with its synthetic full base. Separate pinned vLLM image reached readiness on
   an internal Docker bridge. Chat completed through an SSH direct-tcpip channel.
   Docker inspection confirmed **no published serving ports**.
6. Stop removed the serving container, internal network and local/remote deployment
   workspace. A later status returned LOST for the absent container.

Resolved trainer image:
`sha256:a1a512ef8bdaccdf1f1cde441fe2dfa26d8c5204204a604af3047ed51fdc251a`

Resolved serving image:
`sha256:9370f4e32cc288c0f363da49c9106cbd719a1864633e2c133fba914e28305a26`

These are the already installed runtime images exercised by the SSH adapters;
the milestone changes host control-plane code, not runtime image dependencies.

## Limits

FP16 is sufficient for this acceptance; **BF16 GPU execution was not run**.
Synthetic weights demonstrate execution/integrity, not model quality, large-model
VRAM fit or broad architecture compatibility. The live GPU smoke uses synthetic
archive stand-ins for object storage; real database/store registration and lease
reconciliation are covered separately by integration tests. No live test combined
a retained application database and GPU execution. No model weights were downloaded.

Browser JavaScript received syntax checks; no interactive browser walkthrough was
performed. SSH target setup was not persisted to the user's application database,
`.env` was not changed, and host web/worker/reconciler processes were not restarted.
The normal application still needs dependency update, migration, operator SSH
settings and target creation before use. Test UUID workspaces/containers/networks
were removed; dedicated empty remote parent directories remain, per retention policy.
Paired evaluation and merge/AWQ executors remain local.
