# Milestone 22 validation evidence

## Implementation

The separately pinned SageMaker vLLM image implements managed `/ping` and
`/invocations` on port 8080, verifies the host-pinned file manifest, drops privileges
and loads native model bytes offline. `SageMakerDeploymentExecutor` stages verified
registered representations, creates one network-isolated model/config/endpoint,
normalizes status and invokes the endpoint through AWS SigV4. PostgreSQL retains
the existing lifecycle/snapshot; private ownership journals retain nonce, exact
requests, provisioning intent and AWS ARNs. No executor replay follows uncertain
creation. Observation and destructive operations verify ownership/configuration.

Target serving configuration is additive to v1, preserving training-only targets.
The UI selects eligible SageMaker targets and exposes bounded leases, extension,
stop and existing JSON/SSE playground chat. Keep-running is rejected; deletion
must be confirmed before terminal status. A running reconciler is required.
See [ADR 0026](../adr/0026-sagemaker-serving.md) and
[operator setup](../sagemaker-serving.md).

## Service-free and local service checks

- Final `make verify`: formatter/linter, strict mypy, **470 unit tests passed**,
  including hosting manifest-format, private-cache and lost-journal regressions.
- `make test-integration` through the authorized SSH environment: **118 tests
  passed**, with disposable PostgreSQL/RustFS/Valkey removed by the harness. This
  includes migrations/schema drift and the real target-to-executor route with
  mocked AWS. Both a direct Docker permission failure and its corrected SSH run
  occurred; the failed invocation created no disposable services.
- Final `PYTEST_ADDOPTS="-k sagemaker" make test-integration`: **5 passed** after
  the final recovery guard; disposable services removed.
- Focused serving tests validate AWS request shapes against the installed SDK,
  independent resource tags/configuration, FP16/BF16 rejection before allocation,
  quota rejection, uncertain create responses, asynchronous deletion, API outages,
  restart adoption, lease expiry/extension, stop, fixed invocation parameters,
  response bounds, S3 versioning/multipart cleanup and lost ownership journals.
- Managed-container route tests verify manifest identity, staged file tampering,
  exact inventory, readiness, bounded input, fixed selected-model invocation,
  private cache permissions and privilege-drop ordering.
- Versioned OpenAPI/target schema regenerated with `make openapi`; no database
  migration, dependency, AppSpec or deployment-snapshot change is required.

## Local GPU check

The actual `kitchensoup-sagemaker-vllm:local` container served a synthetic Qwen2
model on the local RTX 2070 SUPER in **FP16**. The isolated validation harness
verified successful `/ping`, a successful OpenAI-compatible `/invocations`
response and rejection of an unexposed `/v1/models` route, then removed its
container. No external weights or user training data were used; no model quality
claim is made.

An initial image candidate exposed missing runner/cache environment after dropping
UID. The image sets runner identity and initializes a private writable cache before
switching UID/GID. After the managed-runtime failure below, the actual GPU check
was repeated successfully with a root-owned, mode-0755 `/tmp` tmpfs, exercising
the permission fix rather than relying on Docker's world-writable default.
The local harness was also corrected to mirror the
production context clamp (256 for this fixture) and normalized read-only archive
permissions. Failed local candidates and the successful container were removed;
none allocated an AWS endpoint.

## AWS acceptance

Hosting quota preflight in us-east-2 confirmed one `ml.g4dn.xlarge`, one
`ml.g6.xlarge` and two `ml.g5.xlarge` endpoint instances. No endpoint existed before
validation. Tests selected a single T4 FP16 endpoint at a time; training quota is independent.

The initial provisioning attempt (`ca60e3ff-8740-4b42-9b92-012f10f83b60`) was rejected
by `CreateModel`: hosting does not accept an OCI image manifest, despite training
accepting that media type. No Model, EndpointConfig or Endpoint was allocated.
The application cleaned its staged archive and the harness deleted the temporary
bucket, ECR images/repository and IAM role/policy, and confirmed log-group absence; fresh independent
checks confirmed absence. CloudTrail identified the manifest rejection without
retaining inference payloads. No GPU runtime was incurred by this attempt.

The build now explicitly exports Docker v2 media types and serving preflight
rejects OCI before remote staging/provisioning. Training's existing OCI behavior
is preserved.

The second attempt (`f36cf8a2-0327-4994-8246-4c00d4a43d9c`) exposed the managed
temporary-directory permissions: vLLM could not create its cache after dropping
privileges. Stop was requested promptly after confirming the startup failure.
AWS rejected `DeleteEndpoint` while it was `Creating`; the durable stop request
remained pending and deletion was retried until allowed. Endpoint, configuration,
model and owned logs were independently confirmed absent before the next attempt.
Its observed test/cleanup interval was **984 seconds**, not a billing measurement.
The dedicated image setup was retained only during active diagnosis, avoiding
another full base-image upload, and was removed after successful validation.

### Successful live FP16 acceptance — 2026-10-20 (Europe/Bucharest)

The third attempt used the cache-fixed Docker v2 image and a registered synthetic
Qwen2 archive (hidden size 128, effective context 256), with one `ml.g4dn.xlarge`,
network isolation and a 15-minute requested lease. No external weights or private
training data were downloaded. The real application materializer, PostgreSQL,
RustFS, AWS clients and vLLM were used; no provider/executor mocks were involved.

| Identity | Value |
| --- | --- |
| Deployment | `3b3a006b-91a4-47ab-a02d-0c9f0bb494c9` |
| Registered model version | `de414349-d3b1-4af0-8b8b-077dfc9e892a` |
| Selected artifact | `fbfb736a-ec7d-4168-b586-79ec7d9b8b29` |
| Artifact SHA256 | `eaec26954fc12e2842461fe2d51f1baba5cc4025207cc92615af59cba22a160e` |
| Serving image manifest | `sha256:fc27bc4e4d5fc03f3c74f7eb57b404a54d741981b6c87e79fb4418f2275038ec` |
| Staged file-manifest SHA256 | `82d767007c0e23463bb8e39d94cbcc92ca7a887d041ef6685581a6ae465089f6` |

Observed lifecycle: **QUEUED → STARTING → RUNNING → STOP_REQUESTED → STOPPED**.
PREPARING is committed by the worker; the observation list samples public views.
The requested/effective serving dtype was FP16. The pinned image requires one
CUDA GPU and passes `half` explicitly to vLLM; BF16 live validation is not claimed.

The actual `/api/v1/deployments/{id}/chat/completions` route returned HTTP 200 and
string content through IAM-authenticated `InvokeEndpoint`. The `stream: true`
route returned SSE with the DONE marker and no error event, using the documented
one-delta fallback. The deployment view exposed neither AWS endpoint ARN nor
inference credentials. The tests used FastAPI TestClient against the real service;
browser JavaScript was not executed. Prompts/responses were not saved as evidence.
The observed create-to-confirmed-stop interval was **504 seconds**; this is not
AWS billable seconds or a model-quality result.

## Final cleanup

The application confirmed endpoint absence before deleting the endpoint
configuration, model and exact staged archive, and removed local model bytes.
Ownership journals, hashes and normal registered fixture assets remain locally.
The validation harness then removed all dedicated resources it created:

- Both disposable S3 buckets and any staged objects/multipart parts.
- Both ECR repositories, including initial and corrected image manifests/layers.
- Both dedicated IAM roles and their inline policies.
- Each validation-created endpoint log group/stream; the initial never-created
  group was confirmed absent. No preexisting/shared log group was removed.

An independent final API audit rechecked all three attempt names for Endpoint,
EndpointConfig and Model absence, all three log-group names, and both sets of
bucket/repository/role names. **All were absent.** No cleanup remains pending.
No Studio, notebook, cluster, VPC, NAT, warm pool, autoscaling or quota change was
created. User-managed permissions and existing infrastructure were left intact.
At most one T4 endpoint was allocated at a time. Final AWS billing was not queried;
charges may include storage/logs and can appear later. Lease limits cannot force
AWS to accept deletion during provisioning and are not dollar spending caps.
