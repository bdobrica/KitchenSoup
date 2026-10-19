# ADR 0020: Kubernetes Jobs with private API-mediated staging

Status: Accepted

Date: 2026-10-19

## Context

PLAN section 23 and Milestone 19 require ordinary GPU Kubernetes Jobs. ADRs
0010–0015 establish offline runner bundles, retained image/input identity,
PostgreSQL lifecycle authority and verified output registration. ADR 0019
establishes immutable execution-target routing outside AppSpec.

## Decision

Extend those executor choices with immutable `kitchensoup.kubernetes-target/v1`
targets. Preserve existing AppSpec, runner contracts and the internal retained-run
envelope. Kubernetes infrastructure is target configuration: namespace, pinned
repository/manifest digest, GPU count, runtime class, service account, node selector,
tolerations and storage class/capacity. Retries retain the target and image digest;
disabled or unknown targets never fall back to Docker. Namespace and required GPU,
storage, networking and admission infrastructure are provisioned by the operator.

Use the official Kubernetes Python client in the trusted control plane. This third
executor concretely justifies the dependency. Resolve kubeconfig contents through
operator-approved file/environment references; only embedded CA/client TLS data or
static bearer tokens over verified HTTPS are accepted. Reject exec/auth-provider
plugins, external file references, proxy configuration and insecure TLS. Neither
kubeconfig nor object-store credentials enter the runner, queue or database snapshot.

Create one deny-all ingress/egress NetworkPolicy, private RWO PVC and non-GPU staging
Pod per run. The prepared cluster must enforce NetworkPolicies. Transfer the verified
local bundle via bounded Kubernetes exec websocket streams into the staging volume;
the staging Pod runs only fixed transfer code already provided by the control plane,
not a separately installed remote agent. Use fixed Python path/regular-file checks,
no shell, archive extraction, externally exposed service or presigned object URLs.

Then create an ordinary batch/v1 Job using the existing Soup image/entrypoint,
read-only input/root filesystem, private writable output, non-root UID, dropped
capabilities, no new privileges, explicit CPU/memory/GPU and volume bounds,
RuntimeDefault seccomp and no mounted service-account token. Pod affinity places
the Job with its staging Pod for RWO/local-path storage; the operator's selector
must select GPU-capable nodes. Disable automatic retries (`backoffLimit: 0`);
explicit retries are new run clones. Active deadlines bound stuck GPU Jobs.
A fixed Python launcher sets the same 1 GiB per-file limit as Docker before
executing the pinned runner entrypoint. The operator configures kubelet Pod PID
limits and storage quotas where the runtime/storage driver cannot enforce them
through these manifests.

Retain a private launch nonce, API resource UIDs and run/input/image bindings before
and during launch. Validate resource identity, Job ownership and image before
observation, transfer or deletion; delete with UID preconditions. API failures are
unknown state, never absence. A pending Kubernetes Job can progress autonomously,
so maps to the executor's active RUNNING observation even before GPU scheduling;
database states remain authoritative. An abandoned preparation without a Job fails
and can be cloned; preparation is never replayed. Job/Pod exit zero alone is not
success: collect bounded manifest-listed files, verify hashes/identities and use the
existing inert ZIP registration boundary and lineage transaction.

Cancellation deletes the verified Job with foreground propagation and waits for
its disappearance before confirming cancellation. Outputs and logs remain on the
staging volume until explicit terminal cleanup. Cleanup retains verified successful
outputs and bounded logs locally, deletes the Job and staging Pod, waits for their
removal, then removes PVC and NetworkPolicy. No TTL may race registration. Private
local staging remains subject to operator retention. Crashes during cleanup retry
against retained resource identities. The storage-class reclaim policy governs
underlying PV disposal. No cluster agent, workflow engine or Kubernetes operator
is installed by KitchenSoup.

## Consequences

Web, worker and reconciler need the same private local staging and credential
references plus namespace-scoped API access. Kubernetes exec is privileged operator
authority, confined to a dedicated training namespace by RBAC; this remains the
trusted single-user boundary, not a multi-tenant arbitrary-workload service.
RBAC and namespace Pod Security examples accompany the implementation. A capacity
probe additionally needs optional read-only node access; a real GPU run remains
the capability proof. Static credentials/certificates need operator rotation.

NVIDIA GPU Operator is the standard infrastructure prerequisite, or an equivalent
preinstalled driver/runtime/device-plugin stack. WSL/k3s may use the Windows driver
with NVIDIA Container Toolkit and device plugin instead of Operator driver install.
FP16 validation on compute capability 7.5 is sufficient (ADR 0013). Kubernetes vLLM
serving remains Milestone 20; paired evaluation and merge/AWQ remain local.
