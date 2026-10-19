# Kubernetes training

KitchenSoup submits ordinary `batch/v1` Jobs to an operator-prepared NVIDIA GPU
cluster. It installs no operator, workflow engine or remote agent. See
[ADR 0020](adr/0020-kubernetes-training.md) and the
[target schema](contracts/kubernetes-target-v1.schema.json).

## Infrastructure

Provision NVIDIA GPU Operator before using this executor, **or an equivalent
driver + Container Toolkit + NVIDIA device-plugin stack**. KitchenSoup never
installs or upgrades GPU infrastructure. On WSL2/k3s use the Windows NVIDIA driver,
the NVIDIA containerd runtime and device plugin; do not install a Linux display
driver over the WSL driver. The plugin must advertise `nvidia.com/gpu` on the node.
Ensure its node-affinity labels match your GPU node. This project's development
node uses `nvidia.com/gpu.present=true`, the default target selector.

The cluster needs a NetworkPolicy-enforcing CNI (including k3s's network-policy
controller), `RuntimeDefault` seccomp, the chosen RuntimeClass (`nvidia` by default),
and a dynamic `ReadWriteOnce` StorageClass. Local-path storage works on a single
node. All selected staging nodes must have enough GPU capacity for the trainer;
on heterogeneous clusters choose a GPU-only node selector. A required Pod affinity
places training on the staging node. Disk requests do not guarantee quota enforcement
by every provisioner; configure storage/namespace quotas and kubelet Pod PID limits
(256 is the Docker adapter's bound). A fixed launcher retains the Docker 1 GiB
per-file limit before executing the Soup runner entrypoint.
Use a dedicated namespace restricted to the trusted KitchenSoup controller identity.
NetworkPolicies are additive: other allow policies must not select these training
or staging Pods and reopen network access.

Start with [namespace, trainer account and controller Role](../deploy/kubernetes/training-namespace.yaml):

```sh
kubectl apply -f deploy/kubernetes/training-namespace.yaml
kubectl get nodes -o custom-columns=NAME:.metadata.name,GPU:.status.allocatable.nvidia\\.com/gpu
```

Create a RoleBinding from `kitchensoup-training-controller` to your separate
control-plane user/service account using your cluster's identity provisioning.
The trainer account must have no application privileges. The controller needs
create/get/delete Jobs, Pods, PVCs and NetworkPolicies, Pod list and exec access.
The optional target capacity test also needs cluster-level `get/list nodes`;
that additional permission is unnecessary for execution itself. It reports
advertised capacity, not successful CUDA initialization or free GPU availability.

## Image and credentials

Build the existing trainer with `make build-soup`. Push it to a cluster-accessible
registry and supply the **repository manifest digest**, for example
`registry.example/trainer@sha256:…`. For offline development an operator can import
the image into each selected node's containerd store and use its matching digest
reference. Docker image config IDs and registry manifest digests are not generally
interchangeable. The target uses `IfNotPresent`; admission/image provenance and
registry access are infrastructure responsibilities. No registry credential is
forwarded by KitchenSoup.

Enable on every web/worker/reconciler process:

```sh
export KITCHENSOUP_KUBERNETES_EXECUTION_ENABLED=true
export KITCHENSOUP_KUBERNETES_SECRET_DIRECTORY=/run/secrets
export KITCHENSOUP_KUBERNETES_STAGING_WORKSPACE=/private/kitchensoup/kubernetes
```

Mount a private, read-only kubeconfig under that approved secret directory and
reference it with `file:///run/secrets/training-kubeconfig`. Files may be private
to the user or an operator group (0600/0640), and cannot be world-accessible.
Alternatively put the **kubeconfig contents**, not a path or token alone, in an
environment variable explicitly listed in `KITCHENSOUP_KUBERNETES_ENV_NAMES` and
use `env://NAME`. The kubeconfig must embed its CA and client certificate/key or
static token, use HTTPS, and select the desired context. Exec/auth-provider plugins,
external file references, insecure TLS and proxy overrides are rejected.
Do not paste inline credentials into target settings or commit kubeconfigs.
Credential rotation updates the referenced secret, never the immutable target.

Apply migration head with the documented `make migrate` workflow before running
the updated application. Default Compose keeps Kubernetes disabled; credential
mounts and common private staging are an explicit operator opt-in. For a host
control plane keep existing loopback database/store/Valkey settings; run the host
commands documented in [jobs](jobs.md), with the same settings and staging directory.
A control plane configured entirely through environment variables can use
`python -m app.background worker` and `python -m app.background reconciler`.

## Target and run

Create the target at **Settings → Execution targets**, or POST the adjusted
[example](../deploy/kubernetes/training-target.example.json) to
`/api/v1/execution-targets`. Replace the placeholder digest and configure namespace,
service account, GPU count, optional selector/tolerations and storage class/capacity.
An omitted runtime class uses `nvidia`; explicitly set it to null for clusters
whose default runtime already supports NVIDIA. A null storage class uses the
cluster default. Target configuration and credential references are immutable;
create a new target to change infrastructure. Existing SSH API bodies remain valid.

Select that target when submitting a saved training plan, or pass its UUID as
`execution_target_id` to `POST /api/v1/training-runs`. Omission retains the existing
local-Docker default. AppSpec and recipe semantics do not change; select FP16 in
the reviewed plan for compute capability 7.5 GPUs. Native BF16 is optional validation.
Kubernetes [serving](kubernetes-serving.md) uses the same targets with an optional
pinned serving image; paired evaluation and transformations remain local.

The controller materializes registered inputs locally, verifies their inventories,
creates a deny-all NetworkPolicy, PVC and CPU staging Pod, then streams inputs via
the Kubernetes API. It starts the GPU Job only after staging succeeds. The trainer
mounts input read-only and output privately; neither Pod receives API tokens,
long-lived credentials, provider state or object URLs. No RustFS route is required
from the cluster. A standard Job uses `nvidia.com/gpu`, no automatic retries and a
wall deadline. Scheduling/image-pull waits remain active; deadline failures become
failed runs. API unavailability leaves durable state unchanged; actual absence
becomes LOST. Preparation is never replayed after a crash; clone an abandoned run.

Status, bounded stdout/stderr logs, cancel, clone, output registration and lineage
reuse the existing application lifecycle. Job exit zero is insufficient: success
requires matching run/input/plan/image identities and every adapter hash. Files are
copied individually with path, file-type, size and inventory bounds, then registered
through the existing inert adapter ZIP boundary. No output archive is executed.

Cancellation foreground-deletes only the verified Job and waits for its Pods.
It retains the staging Pod/PVC for logs. Explicit terminal cleanup removes Job,
staging Pod, PVC and NetworkPolicy, retaining bounded logs and verified successful
outputs locally. No TTL races registration. The storage class decides whether an
underlying PV is deleted or retained. Run cleanup after failed/abandoned preparation
too; a crash may leave partial resources. Interrupted cleanup is retryable. No
automatic retention policy deletes your local staging.

## Development validation

```sh
make test-kubernetes-gpu KUBERNETES_TEST_ARGS='--kubeconfig-ref file:///etc/rancher/k3s/k3s.yaml --secret-directory /etc/rancher/k3s --namespace kitchensoup-m19-validation --image REGISTRY/TRAINER@sha256:DIGEST'
```

Create the isolated namespace beforehand. This opt-in smoke generates synthetic
fixtures using the existing local Docker trainer image, submits actual FP16 Soup
training via the production Kubernetes adapter, checks verified output and restart
handling, cancels a second real Job and cleans its resources. It does not modify
the application database or require BF16. Run it in an SSH login with updated
Docker/Kubernetes groups when the agent's inherited groups are stale. For Helm
on this k3s host pass `--kubeconfig /etc/rancher/k3s/k3s.yaml` explicitly.

See [Milestone 19 evidence](evidence/milestone-19.md) for executed checks.
