# Milestone 20 validation: Kubernetes vLLM playgrounds

Date: 2026-10-19

## Implemented boundary

Ordinary one-replica Deployments, ClusterIP Services, GPU requests, API-mediated
init-container model download into emptyDir, readiness, private API port-forward
chat and verified foreground cleanup reuse the existing application-owned lease
lifecycle. Kubernetes target configuration gains an optional pinned serving image;
old training-only targets remain compatible. AppSpec, deployment snapshots, models,
artifacts and database schema are unchanged. No new dependency is introduced.
See [setup](../kubernetes-serving.md) and [ADR 0021](../adr/0021-kubernetes-serving.md).

## Repository gates

- `make verify`: Ruff formatting/lint, strict mypy (**145 source files**) and
  **347 unit tests passed**. Serving coverage includes isolation, readiness, retained
  identity after executor restart, bounded chat routing, UID/image/Service binding
  rejection, API uncertainty, missing local identity, container restart, partial
  launch cleanup, idempotent stop and training-only target compatibility.
- `make test-integration`: **76 passed**, through the authorized SSH login using
  disposable PostgreSQL/RustFS/Valkey. New tests exercise registered-model target
  routing with the production Kubernetes executor and an API fixture, durable
  restart/readiness, expiry, pending cleanup during outages, eventual STOPPED,
  disabled-target refusal and missing-serving-image refusal. Existing migration
  round trips, ORM drift and local/SSH behavior remain covered.
- `make openapi` regenerated the additive target/API schema changes; existing
  deployment/training wire schemas are unchanged.
- `kubectl apply --dry-run=client -f deploy/kubernetes/serving-role.yaml` passed.
- Browser interaction and Node.js syntax checks were not run: Node.js is absent.
  Existing HTTP/template regression tests passed.

## GPU execution

Used the same authorized SSH login, approved k3s kubeconfig reference and prepared
single-node cluster as Milestone 19: `redbeast`, k3s **v1.36.5+k3s1**, containerd
**2.3.4-k3s1.36**, `nvidia` RuntimeClass, enforced NetworkPolicies, GPU node selector
and one allocatable **RTX 2070 SUPER**, compute capability **7.5**, 8 GiB VRAM.
Used the dedicated `kitchensoup-m20-validation` namespace with restricted Pod Security.
No `sg`, group/socket permission change, credential forwarding or Helm operation
was needed. Kubeconfig contents are not retained in evidence or runtime state.

Imported the existing 34.2 GB pinned vLLM image from Docker into containerd and
added its exact offline digest alias. The one-time import took about 17 minutes.
The cached image is retained for subsequent development:

```text
docker.io/library/kitchensoup-vllm@sha256:9370f4e32cc288c0f363da49c9106cbd719a1864633e2c133fba914e28305a26
```

Ran `make test-kubernetes-vllm-gpu` with the isolated namespace, approved
`file:///etc/rancher/k3s/k3s.yaml` reference, `/etc/rancher/k3s` secret directory
and the image above. The existing trainer generated a tiny synthetic Qwen2 base
and untrained LoRA locally; the production serving executor uses only Kubernetes.
No Hub downloads or persistent application-database writes were needed.

Verified both **FP16 base and FP16 LoRA inference** through authenticated API port
forwarding after an executor-instance restart. Each attempt created a GPU
Deployment, ClusterIP Service and deny-all policy, received model files through
the init container into emptyDir, passed `/health` readiness, returned a bounded
chat response and completed foreground stop plus local/remote cleanup. No ingress,
host port or route from the control plane into the Pod network was required.
The namespace had no remaining serving resources after completion.

Initial validation caught a smoke-harness argument-variable collision before
resource creation, then a live ReplicaSet/Pod discovery race. Both were fixed;
the discovery-order regression is covered by unit tests and subsequent full GPU
runs passed. Materialization archives remain on the trusted host; only native
model/adapter files enter serving volumes.
The final live LoRA volume inspection contained exactly `adapter`, `model` and
the completion marker, with no original archive.

The GPU smoke covers actual executor/runtime behavior; registered-model selection,
PostgreSQL lifecycle, lease expiry and unavailable-API cleanup retries are covered
separately by integration tests.

## Limits

BF16 and multi-node scheduling are not required for this FP16 validation. Ingress,
external registries, eviction recovery, larger models and numerical quality are
outside the synthetic smoke. EmptyDir replacement Pods require explicit relaunch.
NetworkPolicy enforcement, ephemeral-storage quotas, log rotation, kubelet PID
limits and capacity are operator infrastructure responsibilities.

The running application services, installed database and persistent target
configuration were not changed. Enable the existing Kubernetes settings and
register a new target with a pinned serving image to use the feature normally.
