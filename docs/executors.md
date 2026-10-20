# Executor setup and examples

Start with [installation](installation.md). Configure identical approved secrets,
private persistent staging and backend opt-ins in web, worker and reconciler.
Apply database migrations before starting those processes. Never upload keys,
kubeconfigs or AWS configuration as model/dataset artifacts.

| Backend | Prerequisites and configuration | Example submitted to `/api/v1/execution-targets` |
| --- | --- | --- |
| Local | [Linux/WSL Docker GPU workflow](local-training.md) | Omit target ID; enable host local execution explicitly |
| SSH | [SSH/SFTP, trusted host keys, Docker GPU](ssh-execution.md) | [SSH target](../deploy/ssh/target.example.json) |
| Kubernetes | [Device plugin, runtime, enforcing NetworkPolicy, RWO storage, RBAC](kubernetes-training.md) | [Training target](../deploy/kubernetes/training-target.example.json), [serving target](../deploy/kubernetes/serving-target.example.json) |
| SageMaker | [Account, regional GPU quota, S3/ECR, IAM and PassRole](sagemaker-training.md) | [Training](../deploy/aws/training-target.example.json), [serving](../deploy/aws/serving-target.example.json) |
| Runpod | [API key, registered SSH key, reachable full SSH, pinned public images](runpod-training.md) | [Runpod target](../deploy/runpod/target.example.json) |

Examples are inert templates, not credentials or ready-to-launch resources. Replace
all placeholder digests, GPU selections, paths, account/bucket/role identifiers and
limits before submission. Local image names are resolved to content IDs; cloud and
Kubernetes images require repository manifest digests. Training and inference images
are separate. Targets are immutable: create a new target to change configuration.

For an RTX 2070 SUPER (compute capability 7.5), choose **FP16** in the reviewed plan
and serving settings. Native BF16 requires compatible hardware. Install the host
NVIDIA driver and NVIDIA Container Toolkit, configure Docker's NVIDIA runtime,
then use `make build-soup` and `make gpu-check PRECISION=fp16`. Docker group access
is host administrative authority. On WSL use the Windows NVIDIA driver rather than
installing a Linux display driver. Passing `nvidia-smi` alone is insufficient;
KitchenSoup's probe verifies CUDA and precision inside its pinned trainer.

SSH additionally needs noninteractive key authentication, SFTP and direct TCP
forwarding to its Docker bridge. Preload both runtime images and verify host
fingerprints out of band. A proxied terminal without SFTP is insufficient for
Runpod. Use its full public-IP SSH port and SSH-capable images.

Kubernetes runs on the existing single-node k3s development host as well as a
prepared cluster. Verify `nvidia.com/gpu` allocatable capacity and the chosen
RuntimeClass. For this repository's development host, invoke kubectl/helm through
SSH to `127.0.0.1:2222` to pick up groups; helm explicitly uses
`--kubeconfig /etc/rancher/k3s/k3s.yaml`. Application kubeconfigs must satisfy the
[stricter credential format](kubernetes-training.md#image-and-credentials); do not
assume a general-purpose kubeconfig with exec plugins is accepted.

AWS training and hosting GPU quotas are separate. Organization SCP denies override
IAM allows. Supply operator-owned infrastructure and narrowly scoped permissions,
not account-wide administration; example execution-role policies live in
[deploy/aws](../deploy/aws/). Hosting needs a Docker v2 ECR manifest. SageMaker
cannot always delete an endpoint while it is Creating; keep reconciliation active
until confirmed absence. See [serving](sagemaker-serving.md).

Cloud leases, prices and runtime limits are safeguards, not dollar caps. Storage,
provisioning, downloads and idle GPU time can be billable. Provider errors leave
cleanup pending; inspect owned resources independently if control-plane access is
lost. No paid test is part of the ordinary verification gate.
