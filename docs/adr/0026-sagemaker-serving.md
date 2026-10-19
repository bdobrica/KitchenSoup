# ADR 0026: Leased network-isolated SageMaker BYOC playgrounds

Status: Accepted

Date: 2026-10-20

## Context

Milestone 22 extends the deployment lifecycle to managed AWS inference. ADR 0023
deliberately restricts the existing SageMaker adapter to training. Endpoint idle
time is billable and AWS provisioning/deletion can outlive a worker request.

## Decision

Supersede only ADR 0023's training-only restriction and ADR 0017's Docker-specific
transport for SageMaker serving. Add optional, separately digest-pinned regional
ECR `deployment_image` and a maximum serving lease to the immutable v1 target.
Hosting requires a single Docker v2 manifest; reject OCI manifests/indexes before
staging or provisioning. Training's existing OCI acceptance is unchanged.
Existing training-only targets remain valid. Use the same approved AWS profile
resolver and existing boto3 dependency; unknown targets never fall back locally.

Retain the registered artifact, exact base and deployment snapshot contracts.
Materialize and verify native safetensors/LoRA/merged/AWQ bytes on the trusted host,
then create a bounded inert model archive with a hash manifest. Stage only that
archive in the private unversioned regional bucket. Create one network-isolated
SageMaker Model, EndpointConfig and single-GPU Endpoint. No VPC, NAT, autoscaling,
data capture, serverless, multi-model, spot, warm pool or credentials inside the
container. The serving image implements `/ping` and `/invocations` on port 8080
and forwards a bounded fixed chat subset to offline vLLM on loopback. Verify the
host-pinned manifest and file hashes before loading; initialize a private writable
cache, then drop supplementary groups and switch to UID/GID 10001 before model
execution. Explicit FP16 supports T4; native BF16 is separately checked.
Do not assert Docker filesystem/capability isolation for this managed runtime.

Reuse PostgreSQL deployment snapshots, advisory claims, stop precedence, readiness
deadline and worker/reconciler lifecycle. Before every external create, durably
record its intent in the shared private host workspace with a random nonce, exact
request and deployment UUID. Persist returned ARNs. Names alone are not ownership.
Verify regional/account ARN, nonce/UUID tags and requested configuration before
observation, invocation and deletion. Never replay an uncertain create. A matching
resource can be adopted for observation/cleanup; uncertain creation followed by
absence stays pending for operator investigation, rather than falsely confirming
cleanup or allocating a second endpoint. Retain this workspace across restarts.

Invoke only the freshly verified, deterministic owned endpoint name through AWS
SigV4-authenticated `InvokeEndpoint`; saved URLs/ARNs are metadata, never request
authority. The browser uses the existing application proxy, receives neither AWS
credentials nor a raw endpoint, and cannot choose generation parameters. Disable
request logging, persist no prompts/responses, bound payloads and timeouts, and
close SDK response streams. Reuse non-streaming JSON and the existing one-delta
SSE fallback; native AWS response-stream framing is outside this milestone.

SageMaker leases have an absolute target ceiling measured from creation, initially
30 minutes and configurable up to 60. Reject indefinite keep-running; extensions
cannot exceed that ceiling. This narrowly supersedes ADR 0017's indefinite keep
semantics for this backend. Stop, failure and expiry delete the owned endpoint,
confirm absence, then delete its configuration/model and exact staging object.
Unknown API state, identity mismatches and incomplete deletion remain nonterminal
for restart-safe retries. Keep the ownership journal; remove local model staging.
If the journal is lost, require confirmed provider absence or restored ownership
evidence; a missing file cannot prove that billable resources have disappeared.
The reconciler must remain running: SageMaker supplies no endpoint TTL here, and
stopping a process does not release endpoint billing. Limits are not dollar caps.

Shared ECR/bucket/IAM and CloudWatch resources remain operator-owned. A live
validation harness removes infrastructure it creates, independently, and requires
explicit paid opt-in, one small synthetic fixture and confirmed final cleanup.

## Consequences

Web, worker and reconciler need the same durable private staging and approved
profile access. IAM needs hosting CRUD, resource tags, PassRole and invocation,
plus ECR/S3 permissions. Hosting GPU quota is independent of training quota.
Endpoint provisioning/download/deletion and retained storage can incur charges.
No live acceptance is claimed from mocks or local GPU execution alone.

References: [hosting container contract](https://docs.aws.amazon.com/sagemaker/latest/dg/your-algorithms-inference-code.html),
[network isolation](https://docs.aws.amazon.com/sagemaker/latest/dg/mkt-algo-model-internet-free.html),
[InvokeEndpoint](https://docs.aws.amazon.com/sagemaker/latest/APIReference/API_runtime_InvokeEndpoint.html).
