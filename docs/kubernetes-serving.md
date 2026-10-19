# Kubernetes playgrounds

Use the prepared GPU cluster and approved kubeconfig configuration from
[Kubernetes training](kubernetes-training.md). Keep
`KITCHENSOUP_KUBERNETES_EXECUTION_ENABLED=true` and the same private staging directory
on web, worker and reconciler. No Helm installation or additional operator is
required. The controller also needs the permissions in
[serving-role.yaml](../deploy/kubernetes/serving-role.yaml), bound to its own
identity, never the tokenless runtime ServiceAccount.

Build `make build-vllm` and preload that image on eligible nodes, or use an
operator-controlled registry. Register a **new** Kubernetes execution target with
the same namespace, kubeconfig reference, runtime class, GPU selector, tolerations
and service account used for training, plus `deployment_image` set to the complete
`repository@sha256:manifest-digest`. The image manifest must exist under that exact
digest reference in containerd; importing only a tag may need an explicit digest
alias as described in the training guide. Do not substitute Docker's image config
ID for a registry manifest digest. Old targets without this field remain usable
for training. Targets are immutable; do not edit stored JSON. Start with the
[serving target example](../deploy/kubernetes/serving-target.example.json).

Select that target in the playground form for a registered model. FP16 works on
the RTX 2070 SUPER; BF16 is explicit and requires native hardware. The host verifies
registered archives and resolves any reviewed public base at its exact revision,
then uploads only model/adapter files into a tokenless init container's `emptyDir`.
After upload completes the init container exits and vLLM starts with read-only
models. No object-store credential or kubeconfig is sent to either container.

Serving requests one GPU (or the target's explicit count), 4 CPUs and 16 GiB RAM;
the init container needs only 1 CPU/256 MiB. Model storage uses `workspace_gib`
(default 32 GiB), with separate 2 GiB temporary and 1 GiB shared-memory volumes.
Prepare capacity and ephemeral-storage quotas, container log rotation and a
kubelet Pod PID limit; do not overlap GPU workloads on this single-GPU node.
NetworkPolicy enforcement is required, and other additive allow policies must
not select these Pods. Serving is fully offline after host materialization.

Readiness uses `/health`. The internal endpoint is a ClusterIP Service URL; it is
not returned to browser clients. Each chat validates live resource identity and
uses Kubernetes API port forwarding to the initialized Pod. Thus the control
plane needs API reachability only, even when running outside the cluster.

The existing [lease, stop and chat behavior](deployments.md) applies, including
the 30-minute default lease and 15-minute startup deadline. Keep the worker and
reconciler running. Stop or expiry foreground-deletes the Deployment, waits for
Pods to terminate, then removes Service, NetworkPolicy and private host model
staging. API outages leave cleanup pending. Artifacts and registered models remain.
The emptyDir belongs to the initialized Pod: eviction/replacement or server
restart fails the attempt and requires an explicit new deployment. A replacement
never receives model bytes automatically.

## Optional ingress

The built-in playground requires no ingress, NodePort, load balancer or host port.
For a separately managed external client, an operator can route an authenticated
TLS ingress to the generated Service's `http` port 8000. Restrict a dedicated
NetworkPolicy allow rule to that ingress controller's namespace **and** Pod labels
and TCP/8000. Keep egress denied. Apply authentication, request/body/time limits
and private-network access before exposing vLLM; the raw backend does not supply
the application's bounded chat or lease checks. Remove any exposure rule when
the temporary deployment stops. The executor never creates ingress or changes
operator policy to expose an endpoint.

## GPU validation

Use a dedicated restricted namespace and synthetic offline fixtures:

```sh
make test-kubernetes-vllm-gpu KUBERNETES_SERVING_TEST_ARGS="--kubernetes-namespace kitchensoup-validation --kubeconfig-ref file:///etc/rancher/k3s/k3s.yaml --secret-directory /etc/rancher/k3s --image YOUR_REPOSITORY@sha256:YOUR_MANIFEST_DIGEST"
```

The trainer image generates fixtures locally with Docker; the production serving
executor uses only Kubernetes. It validates FP16 base and LoRA inference, recreates
the executor to check retained identity, and removes all per-attempt resources.
See [Milestone 20 evidence](evidence/milestone-20.md) and
[ADR 0021](adr/0021-kubernetes-serving.md).
