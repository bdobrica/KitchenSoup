# Milestone 19 validation: Kubernetes training

Date: 2026-10-19

## Implemented boundary

Versioned immutable Kubernetes targets, approved kubeconfig references, namespace,
runtime/service-account/GPU configuration, selectors/tolerations, ordinary Jobs,
private API-mediated staging, status/logs, foreground cancellation and explicit
cleanup are implemented. Successful outputs reuse manifest/hash verification,
inert adapter ZIP registration and application-owned model/artifact lineage.
See [setup](../kubernetes-training.md) and [ADR 0020](../adr/0020-kubernetes-training.md).

The official Kubernetes client and PyYAML parse/API dependencies stay in the
control plane. No Soup, AppSpec, recipe, runner-wire or deployment-wire contract
changed. The target API and generated schema are additive. Existing SSH bodies
and local-default behavior remain compatible. Kubernetes serving remains
Milestone 20; paired evaluation and transformations remain local.

## Repository gates

- `make verify`: Ruff formatting/lint, strict mypy (**142 source files**) and
  **338 unit tests passed**. Kubernetes coverage includes approved secret resolution,
  plugin/external-file/insecure-TLS rejection, digest pinning, kind/config matching,
  resource isolation, nonblocking websocket upload without stdout, pending Job
  observation, output verification, API uncertainty, restart, foreign UID refusal,
  cancellation and cleanup with retained logs.
- `make test-integration`: **74 passed**, using disposable PostgreSQL/RustFS/Valkey
  through the authorized SSH login with updated Docker groups. Covers Kubernetes
  target API opt-in/validation/no-store responses, SQL immutability and invalid
  inserts, actual executor routing without Docker fallback, registry registration,
  restart, clone target/image retention and disabled-target refusal. Includes
  migration downgrade-to-base, upgrade-to-head, repeated upgrade and ORM drift check.
- `make openapi` regenerated the API contract and new Kubernetes target schema.
  Existing training and deployment snapshot schemas are unchanged.
- `kubectl apply --dry-run=client -f deploy/kubernetes/training-namespace.yaml`
  accepted the Namespace, trainer ServiceAccount and controller Role examples.
- Browser interaction and JavaScript syntax tooling were not run: Node.js is
  unavailable in this environment. The existing HTTP/template regression tests pass.

## Actual GPU execution

Used SSH to `127.0.0.1:2222` as the supplied non-root operator. No `sg`, group
change, credential printing or socket permission change was needed. The application
client used the operator-approved file reference to the k3s kubeconfig; no kubeconfig
contents are retained here. Helm was not needed for this milestone.

Infrastructure: single Ready node `redbeast`, k3s **v1.36.5+k3s1**, containerd
**2.3.4-k3s1.36**, `nvidia` RuntimeClass, NVIDIA device plugin, node GPU label and
one allocatable `nvidia.com/gpu`. Storage: default **local-path**, RWO,
WaitForFirstConsumer. GPU: **NVIDIA GeForce RTX 2070 SUPER**, compute capability
**7.5**, 8 GiB. Used an isolated namespace with restricted Pod Security admission.

Imported the existing 11.9 GB trainer image from Docker into k3s containerd. Import
retained the tag but required an explicit digest alias for offline CRI resolution.
Image reference used for actual Job and staging Pod:

```text
docker.io/library/kitchensoup-soup-trainer@sha256:a1a512ef8bdaccdf1f1cde441fe2dfa26d8c5204204a604af3047ed51fdc251a
```

Ran `make test-kubernetes-gpu` with the approved kubeconfig reference, explicit
secret directory, isolated namespace and the digest reference above. The smoke
generated a tiny random one-layer Qwen2 model and two synthetic conversations using
the existing Docker trainer, then exercised the production Kubernetes adapter.
No Hub downloads or application-database writes were required by this opt-in smoke.

Verified:

1. Deny-all NetworkPolicy, private per-run PVC and non-GPU staging Pod were created.
   Inputs streamed through authenticated Kubernetes exec; the cluster needed no
   route or credentials to RustFS.
2. An ordinary GPU Job ran **actual FP16 Soup training**, with no CPU/BF16 fallback,
   no automatic Job retries, read-only input/root, non-root identity, no API token,
   dropped capabilities, seccomp and resource limits. The final launcher retains
   Docker's 1 GiB per-file limit before invoking the unchanged runner entrypoint.
3. Successful runner manifest, run/input/plan/image binding and adapter file hashes
   passed. Bounded collection produced a **13,403-byte verified adapter ZIP**.
4. A fresh executor instance recovered the retained launch identity and successful
   status. Stdout remained readable after explicit terminal cleanup.
5. A second actual GPU Pod reached Running before foreground Job cancellation.
   Cancellation waited for removal; explicit cleanup removed its staging Pod, PVC
   and policy. Test resources were removed; the imported trainer remains cached.

An initial synthetic staging run exposed a blocking websocket read before upload
completion. It was interrupted and cleaned up, the read loop was corrected and a
regression added. Subsequent full training/cancel/cleanup runs passed.

## Limits

FP16 on the available GPU is sufficient validation. BF16, multiple nodes, external
registries, other CSI drivers and GPU Operator installation were not exercised.
The infrastructure uses the already prepared WSL driver/runtime/device-plugin stack;
KitchenSoup installed no GPU operator or cluster agent. Numerical quality and the
memory requirements of larger models are outside this synthetic smoke.

The live smoke validates the real executor; PostgreSQL/storage registration and
durable service routing are separately covered by integration tests. The installed
application database, running application services and persistent target configuration
were not changed. Apply migration head and opt-in settings when enabling this feature
in the normal application.
