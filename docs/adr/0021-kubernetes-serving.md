# ADR 0021: Temporary Kubernetes vLLM playgrounds

Status: Accepted

Date: 2026-10-19

## Context

Milestone 20 extends ADR 0017's serving lifecycle and ADR 0020's prepared-cluster
target boundary to ordinary Kubernetes Deployments and Services.

## Decision

Supersede only ADR 0017's Docker-specific infrastructure restriction. Route
Kubernetes serving through the retained immutable target, never fall back to
Docker. Add an optional digest-pinned `deployment_image` to the existing v1
Kubernetes configuration. Old training-only targets remain valid; changing images
requires a new target. Preserve deployment snapshots, lease semantics, database
claims, readiness deadline, stop precedence and unknown-state handling.

The trusted host materializes and verifies registered model/adapter bytes using
the existing serving materializer. Create a deny-all NetworkPolicy, ClusterIP
Service and one-replica Recreate Deployment. A non-GPU init container receives
those files through bounded Kubernetes exec streams into a size-bounded emptyDir;
the host marks completion only after all transfers succeed. This is the MVP model
download, mediated by the API rather than object-store credentials or presigned
URLs inside an untrusted runtime. No training datasets, kubeconfig, mounted API
token, external network route, archive extraction or remote code enters serving.
The server mounts models read-only. Retain the pinned Turing-compatible vLLM
runtime and explicit FP16/BF16 selection. Set non-root execution, read-only root,
dropped capabilities, RuntimeDefault seccomp, bounded CPU/RAM/GPU/tmp/shared memory.
Operators enforce network policies, ephemeral-storage quotas and kubelet PID limits
(512 or an appropriate cluster-wide bound) on the prepared cluster.

Retain the launch nonce, snapshot hash, resource UIDs and exact initialized Pod UID
in the shared private host workspace. Verify labels/annotations, image and
Deployment → ReplicaSet → Pod ownership before observation, transfer or deletion;
delete with UID preconditions. Never automatically populate a replacement Pod:
emptyDir is attempt-local, and disappearance/replacement fails the attempt for
explicit relaunch. Container restarts fail the attempt rather than masquerading as
continuous healthy service. Preparation is never replayed after worker death.

Readiness comes from the server's `/health` probe. Persist a derived namespace-local
Service URL internally, never use a saved endpoint as request authority. For each
chat request revalidate the live Service, Deployment and initialized Pod binding;
use an authenticated Kubernetes API port-forward to that Pod's fixed port 8000.
No DNS route, ingress, NodePort, load balancer, arbitrary URL or exposed host port
is required. Reuse the bounded non-streaming chat/generation contract and disable
vLLM request logging. Prompt bodies travel in the forwarded HTTP stream, not exec
arguments or Kubernetes API request bodies. Do not persist prompts or responses.

Explicit stop, startup failure and lease expiry use the existing PostgreSQL
workflow. Foreground-delete the verified Deployment and wait for dependents to
disappear before removing Service and NetworkPolicy, then remove local model
staging. An API outage or cleanup timeout leaves the action pending for retry,
never falsely records released GPU capacity. Keep the reconciler running; no
independent TTL controller is introduced.

## Consequences

The same Kubernetes opt-in and approved kubeconfig settings cover training and
serving. Controller RBAC additionally permits Deployments, read-only ReplicaSets,
Services and Pod port-forwarding. The server/init ServiceAccount has no privileges
or mounted token. Default application processes remain disabled. Ingress is
optional operator-managed exposure with authentication, TLS and explicit network
policy; the built-in playground needs none. This remains trusted single-user
operator authority, without scheduling, durable model cache or automatic recovery
of evicted serving Pods.
