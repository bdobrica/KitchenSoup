# ADR 0023: Bounded SageMaker offline training

Status: Accepted

Date: 2026-10-19

## Context

PLAN section 24 and Milestone 21 require the same reviewed training plan to run
as a SageMaker Training Job. ADRs 0010–0015 define offline Soup bundles, verified
outputs and PostgreSQL-authoritative lifecycle; ADR 0019 defines immutable targets.
AWS training has different filesystem and resource isolation controls from Docker.

## Decision

Add immutable `kitchensoup.sagemaker-target/v1` training targets. Keep infrastructure
outside AppSpec: AWS region, execution-role ARN, regional private ECR image pinned
by manifest digest, dedicated unversioned regional S3 staging bucket/prefix, one
small GPU instance type, volume size and runtime limit. Existing targets and
contracts retain their meaning. Unknown/disabled targets never fall back locally.
Serving, Studio, endpoints, automatic provisioning and quota requests are outside
this adapter. boto3 already exists for storage; reuse it rather than add the
SageMaker training framework SDK.

Resolve `aws-profile://profile` only from an explicit operator profile allowlist.
`aws-profile://default` uses the normal AWS environment/instance-role credential
chain; named profiles use the operator's AWS SDK configuration. Profiles and any
configured credential process are trusted operator configuration, never agent or
runner state. No AWS key, token, URL, SDK configuration or profile content enters
AppSpec, bundles, queues or persisted run snapshots. Other credential consumers
continue to reject AWS profile references.

Use the common registered-input materializer and one native File-mode `bundle`
input channel. Stage verified files beneath the deterministic run UUID prefix.
SageMaker performs S3 and ECR transfers outside the network-isolated container;
enable network isolation and expose no AWS credentials to the runtime. Retain the
request, private launch nonce, input/plan hashes and pinned image locally before
submission. Verify external identity and configuration on every observation and
destructive operation. AWS errors are unknown state except documented specific
missing-resource responses. Never resubmit an uncertain attempt; explicit retries
clone the run. Job names/ARNs and CloudWatch references derive from the retained
target/run, not caller-supplied endpoints.

Build a separate thin managed-training image from the pinned Soup trainer. Fixed
aliases preserve the runner's `/input` and `/output` paths. The root bootstrap
initializes SageMaker's writable directories and native input ownership/modes,
drops supplementary groups and
switches to UID/GID 10001 before parsing inputs or invoking the existing Soup CLI.
SageMaker provides managed volumes and container isolation; Docker-specific
capability/read-only-root/PID flags are not asserted for this backend. File size,
bundle validation, no custom model code, offline behavior and runner timeout
remain enforced. The reviewed precision must match Soup's hardware selection;
T4/g4dn uses FP16, whereas native BF16 GPUs require a BF16 plan.

Request exactly one instance, no managed spot recovery, no automatic retries, no
warm pool, no profiler/debugger rules, and no VPC/NAT infrastructure. Default maximum
training runtime is 600 seconds, configurable only between 60 and 3600 seconds;
instance types and storage sizes are bounded. These are cost controls, not a dollar
spending cap: AWS provisioning, downloads, artifact upload, ECR/S3/CloudWatch storage
and failures can also incur costs. Operator quotas/budgets remain independent.

Map InProgress/Stopping to active RUNNING, Stopped to CANCELED, and Failed to FAILED.
Completed is successful only after bounded archive collection and the common
manifest/run/input/image/adapter hash verification. Never execute or blindly extract
the AWS `model.tar.gz`. Import verified adapters through the existing inert ZIP,
registry/lineage transaction and POSTPROCESSING retry boundary. Cancellation sends
StopTrainingJob but confirms cancellation only after a terminal observation.

Explicit terminal cleanup retains verified successful output locally and removes
only objects and stranded multipart-upload parts beneath that attempt's staging prefix. Refuse active jobs and
versioned staging buckets. Retain the non-running SageMaker job metadata and
shared ECR/bucket/IAM infrastructure; the operator owns their lifecycle. Logs may
contain source data. Surface console/CloudWatch references without broad log-group
deletion. A validation harness must remove resources it provisions, independently
of this shared-infrastructure policy. No periodic cloud cleanup controller is added.

## Consequences

Web/worker/reconciler need the same private staging and approved profile access.
Run state, claims, clone semantics and registry ownership stay unchanged. AWS account
access, an approved GPU training quota, regional staging bucket, execution role,
ECR image and PassRole permissions are prerequisites. IAM allows cannot override
Organizations policy denies. No live GPU acceptance is claimed without an actual
completed job and cleanup evidence; service-free tests and disposable local database
tests validate lifecycle independently of account quotas.

References: [CreateTrainingJob](https://docs.aws.amazon.com/sagemaker/latest/APIReference/API_CreateTrainingJob.html),
[network isolation](https://docs.aws.amazon.com/sagemaker/latest/dg/mkt-algo-model-internet-free.html),
[training filesystem](https://docs.aws.amazon.com/sagemaker/latest/dg/amazon-sagemaker-toolkits.html).
