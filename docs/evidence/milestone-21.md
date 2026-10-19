# Milestone 21 validation: SageMaker training

Date: 2026-10-19

## Implemented boundary

The opt-in SageMakerTrainingExecutor reuses the reviewed plan, registered input
materializer, offline Soup CLI, PostgreSQL claims/events and verified adapter ZIP
registration/lineage path. Versioned immutable targets select approved AWS profiles,
regional pinned private ECR images, execution role, unversioned S3 staging and
bounded single-instance resources. Native File-mode input and model.tar.gz output
are adapted without forwarding credentials or executing output content.
See [setup and cost controls](../sagemaker-training.md) and
[ADR 0023](../adr/0023-sagemaker-training.md).

## Repository and local checks

- `make verify`: Ruff formatting/lint, strict mypy (**153 source files**) and
  **382 unit tests passed**. New tests validate AWS request shape against botocore,
  bounded resources, no submission replay, profile approval, immutable image/nonce
  binding, cancellation/Stopping, AWS outage versus specific missing-job responses,
  regional/unversioned staging, manifest-index rejection, verified adapter ZIP output,
  malicious tar paths/links/metadata, tampering and exact-prefix cleanup.
- `make test-integration`: **86 passed**, through the authorized local SSH login,
  using disposable PostgreSQL/RustFS/Valkey containers. Tests cover migration
  head/base/head round trips, ORM drift, SQL target immutability, API opt-in and
  payload sanitization, target routing without Docker fallback, process restart,
  successful model/lineage registration, clone/cancel and terminal cleanup.
  AWS APIs in these tests are fixtures; database and registered storage are real.
- After the final cost review, **21 focused unit tests**, the aggregate gate and
  **2 focused integration tests** passed again. These include multipart staging
  configuration and aborting stranded billable upload parts under only the owned
  run prefix. The local smoke-container listing was empty afterward.
- `make openapi` regenerated additive target/run contracts and the new target schema.
  Existing AppSpec, plan, runner-input/output and registered adapter formats are unchanged.
  All three AWS JSON examples parsed successfully; the target example also passed
  the production TargetCreate validator. IAM permissions were not applied/live-tested.
- `make test-sagemaker test-sagemaker-gpu` passed against the final thin managed image,
  entirely locally. Both tests disable networking and use tmpfs volumes and a generated
  tiny random Qwen2 model with two synthetic examples; no model download is needed.
  CPU preparation exercises the actual pinned Soup CLI dry run; FP16 execution uses
  the local RTX 2070 SUPER (compute 7.5, 8 GiB). The GPU test completes actual Soup
  training and publishes adapter files. Checks verify native managed paths, private
  root-owned input handling, UID/GID 10001 with empty supplementary groups, manifest
  identity, bounded diagnostic publication, and exclusion of training data/configuration
  from the model artifact directory. This is a lifecycle/compatibility smoke, not a
  numerical-quality or fine-tuning effectiveness claim.
- Local smoke containers and tmpfs data are removed by the harness. The thin local
  development image remains cached. Kubernetes settings/resources were not changed.
- Browser automation/Node.js syntax checks were not run; Node.js is unavailable.
  HTTP/template regression tests passed. No live endpoint, provider or AWS GPU test
  is represented by the local smoke.

## AWS preflight and cleanup accounting

Only read-only AWS CLI calls were made: STS identity, regional SageMaker job
discovery, IAM execution-role discovery, ECR/S3 access discovery, quota discovery,
and DescribeTrainingJob for a deliberately absent UUID. No credential values,
profile files, signed URLs, account-specific runtime payloads or real dataset/model
bytes are retained in source or evidence.

Initial discovery was denied by an Organizations SCP. After the operator removed
that restriction and granted quota-read permission, SageMaker API access worked.
No existing SageMaker-named execution role was found. In `us-east-2`, both g4dn.xlarge
and g5.xlarge training quotas were zero; discovery found no nonzero training-instance
quota. The operator requested quota 1 for g4dn.xlarge. The subsequent quota recheck
still reported **0**, so no paid training launch or image upload was attempted.

**AWS resources created by this implementation/validation: none.** No training job,
bucket, object, multipart upload, repository/image, IAM role/policy, log group/stream,
endpoint, VPC or notebook was provisioned. No billable AWS compute/storage was
allocated and there is no implementation-created AWS resource to delete. The
operator's account-policy/quota changes are outside the validation harness.

## Remaining acceptance

Milestone 21 implementation and local lifecycle checks are complete. Live acceptance
remains unchecked in TODO.md until an approved regional training GPU quota permits
a tiny SageMaker job, verified output import and recorded cleanup of every disposable
resource created for that check. IAM/ECR/S3 role and image prerequisites must also
be provisioned before that run. No live SageMaker success or exact AWS bill total
is claimed. Milestone 22 serving remains unimplemented.
