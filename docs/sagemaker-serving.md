# SageMaker vLLM playgrounds

SageMaker targets with a separate `deployment_image` can launch registered model
archives, exact-base LoRA adapters, merged models and supported AWQ representations
from `/deployments`. FP16 supports `ml.g4dn.xlarge` (T4). BF16 needs native hardware
(`ml.g5.xlarge`/`ml.g6.xlarge`) and an explicitly selected BF16 deployment. Hub-only
registrations still need an archive; no external model download happens in AWS.

## Operator setup

Reuse [SageMaker training setup](sagemaker-training.md): opt in with
`KITCHENSOUP_SAGEMAKER_EXECUTION_ENABLED=true`, approve profiles with
`KITCHENSOUP_AWS_APPROVED_PROFILES=default`, and share the same durable private
`KITCHENSOUP_SAGEMAKER_STAGING_WORKSPACE` across web, worker and reconciler.
`aws-profile://default` uses the standard SDK environment/instance-role chain;
other profiles must also be operator-approved. Never put AWS keys in target JSON.
Default application processes have no serving authority until explicitly enabled.

Build the separate adapter over the pinned vLLM 0.10.2 engine:

```sh
make build-sagemaker-vllm
# Authenticate to your private regional ECR registry using password-stdin.
# Tag and push kitchensoup-sagemaker-vllm:local, then resolve its manifest digest.
```

Use one linux/amd64 **Docker v2** image manifest. Hosting rejects OCI manifests as
well as image indexes, even though managed training accepts single OCI manifests.
The build disables provenance and selects Docker media types explicitly; confirm
`imageManifestMediaType` in ECR before pinning. Images are large; upload before allocating
an endpoint. ECR storage and transfer may be billed separately. Training and
serving images remain separate; neither image uses Docker-in-Docker.

Create a new immutable SageMaker target through the target page or API; see the
[complete target example](../deploy/aws/serving-target.example.json). Existing
training-only targets cannot be edited. Retain the existing training fields and add:

```json
{
  "deployment_image": "123456789012.dkr.ecr.us-east-2.amazonaws.com/kitchensoup-vllm@sha256:<64-hex-manifest-digest>",
  "max_deployment_lease_minutes": 30
}
```

The image must be separate from the trainer and use the target region. The maximum
lease is 1–60 minutes, defaults to 30, and starts at creation, including model
preparation. Serving uses the target's one GPU instance type. Check **endpoint
usage** quota separately from **training job usage**; training quota does not
authorize a hosting instance. Configure the regional private, unversioned staging
bucket and execution role before launch. Serving preflight verifies the serving
pin; the training image is not loaded during inference.

The control-plane identity needs regional hosting create/describe/delete for
Models, EndpointConfigs and Endpoints, AddTags/ListTags, `sagemaker:InvokeEndpoint`,
restricted `iam:PassRole`, and existing staging S3/ECR discovery/upload/delete
permissions. Shared infrastructure is operator-owned. Use the
[execution-role policy example](../deploy/aws/serving-role-policy.example.json)
with the existing [trust example](../deploy/aws/training-role-trust.example.json),
substituting the account, region, bucket, prefix and repository. This execution
role reads only serving model staging, pulls the image and writes endpoint logs;
it is distinct from the control-plane identity. Scope controller hosting
permissions to `kitchensoup-playground-*` resources where AWS supports that scope.
If a target also trains, its execution role needs the training permissions as well.

## Lifecycle, security and billing

The worker verifies registered bytes and builds a serving-only archive with a
pinned hash manifest. SageMaker downloads/extracts it into `/opt/ml/model` outside
the network-isolated runtime. No training datasets, local RustFS credentials, AWS
credentials, remote code or user-supplied executable enter the server. The image
verifies every staged file, initializes a private writable cache, drops privileges,
and starts offline vLLM on loopback. It does not assume managed `/tmp` is writable
by the serving UID.
Its port-8080 adapter exposes only `/ping` and bounded `/invocations`.

The browser calls KitchenSoup. The server verifies live ownership/configuration
and invokes the deterministic endpoint name with AWS SigV4 authentication. Saved
endpoint metadata never chooses an arbitrary address. Requests retain the existing
32-message/16000-character/128-output-token bounds; upstream response size is
bounded and inference uses a 50-second upstream socket timeout and 70-second SDK socket
timeout. JSON chat works unchanged. Streaming requests use the existing one-delta
SSE fallback after the bounded AWS result; native AWS token streaming is not
implemented. Prompts/responses are not persisted or request-logged.

**Stop now**, failed startup and expiry delete the endpoint, then its dedicated
configuration/model and exact staged archive. The deployment remains pending
until AWS confirms deletion; an API outage or ownership mismatch cannot report
released GPU capacity. Lease extension cannot pass the absolute target ceiling;
indefinite keep-running is rejected. Keep the reconciler running, even when idle.
There is no provider TTL or independent automatic billing cutoff. Closing the
browser or stopping the application does not delete a SageMaker endpoint.
AWS can reject deletion while an endpoint is still `Creating`. Stop/expiry remains
durable and retries when AWS permits deletion, which can extend billing beyond
the requested lease. There is no supported force-delete bypass in this executor.

Provisioning intent, nonce, request, snapshot and returned ARNs remain in private
`ownership.json` beside the deployment workspace after local model files are
removed. Preserve this journal across restart. Never retry an uncertain creation
or remove its journal: reconcile matching owned resources first. A missing resource
after an unacknowledged create stays pending for operator investigation, with no
automatic reallocation. Do not manually replace/edit owned AWS resources.
If the journal is lost, the executor requires confirmed provider absence or
restoration of ownership evidence; a missing local file never proves cleanup.

One endpoint uses one instance with no autoscaling, VPC/NAT or data capture. Idle
endpoints are billable, including deployment/deletion overhead where applicable.
Lease limits are not account-wide dollar caps; storage/logs and other AWS charges
are additional. Keep AWS budgets/quotas independent. Shared ECR repositories,
buckets, roles and CloudWatch log groups are not deleted by the executor.

## Validation

`make verify` covers mocked provisioning, AWS wire shapes, ownership tampering,
ambiguous creation, invocation bounds and cleanup failures. `make test-integration`
uses disposable local services for registry selection, durable leases, worker
restart, expiry, stop precedence and cleanup retry. It makes no AWS calls.

The optional paid smoke uses the existing synthetic Qwen2 fixture generator
(`kitchensoup-runpod-trainer:local` must already be built), local PostgreSQL/RustFS
and preconfigured disposable AWS infrastructure. Supply a complete versioned
SageMaker target configuration as private JSON, with its separately pinned image:

```sh
make test-sagemaker-vllm ARGS="--confirm-paid --target-config /private/target.json --minutes 15"
```

This explicitly allocates one T4 endpoint, checks JSON and SSE through the real
application playground route, then requests stop and confirms cleanup. It targets
only its own deployment UUID, leaving unrelated workloads alone. It imports a
small synthetic base archive; no model-quality claim is made. The harness/operator
must also remove any bucket, ECR repository/image, IAM role/policy and owned log
group created for the test. Do not remove preexisting/shared resources.

See [ADR 0026](adr/0026-sagemaker-serving.md) and
[Milestone 22 evidence](evidence/milestone-22.md).
