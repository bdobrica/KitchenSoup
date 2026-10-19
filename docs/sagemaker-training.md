# SageMaker training

Milestone 21 adds a training-only AWS adapter. See
[ADR 0023](adr/0023-sagemaker-training.md),
[target schema](contracts/sagemaker-target-v1.schema.json),
[API contract](contracts/artifacts-v1.openapi.json) and
[validation evidence](evidence/milestone-21.md).
SageMaker serving remains Milestone 22.

## Prerequisites and costs

Use a trusted single-user control plane with private shared disk for web, worker
and reconciler. Keep the existing registered artifact store (including RustFS);
the separate AWS staging bucket does not replace it. boto3 is already an application
dependency. The runtime image needs no SageMaker SDK, credentials or Internet access.

Provision these resources explicitly as the operator:

- A regional **GPU training quota of at least 1** for the selected instance type.
  EC2 quotas and SageMaker endpoint quotas do not satisfy training quotas.
- A dedicated, private, **never-versioned S3 bucket in the job region**, with public
  access blocked and SSE-S3 encryption. Enabled or suspended versioning is rejected, since delete markers
  would leave billable object versions. Do not configure a lifecycle rule that can
  expire active inputs or outputs before registry import.
- A private regional ECR repository containing the managed-training image below.
- A same-account SageMaker execution role. Start from the
  [trust example](../deploy/aws/training-role-trust.example.json) and
  [role policy example](../deploy/aws/training-role-policy.example.json), replacing
  every example account, region, repository and bucket. These are configuration
  examples, not automatically applied policies. The role needs read access to input
  objects/image layers and write access to output objects and its CloudWatch streams.
- An operator AWS identity with Create/Describe/StopTrainingJob, tagging and restricted
  `iam:PassRole` for that execution role (`iam:PassedToService=sagemaker.amazonaws.com`).
  It also needs Get/Put/DeleteObject, ListBucket, GetBucketLocation/GetBucketVersioning
  plus ListBucketMultipartUploads/AbortMultipartUpload for staging and ECR
  DescribeImages for the pinned image. Setup/image publishing
  requires separate IAM/ECR permissions. The adapter does not create roles, repositories,
  buckets, VPCs, NAT gateways, notebooks, Studio domains, endpoints or quota requests.

Organizations SCPs, permission boundaries and regional restrictions can override
IAM allows. Diagnose those before adding broad policies. Check account/region and
training quota read-only before uploading a large image or submitting a job:

```sh
aws sts get-caller-identity
aws service-quotas list-service-quotas --service-code sagemaker --region us-east-2 \
  --query 'Quotas[?contains(QuotaName, `ml.g4dn.xlarge for training job usage`)].{Name:QuotaName,Value:Value}'
```

The default target uses one T4 `ml.g4dn.xlarge`, 30 GiB and 600 training seconds.
Only g4dn/g5/g6 xlarge instances are accepted, with 20–100 GiB and 60–3600 seconds.
There is no automatic retry, spot restart, warm pool or profiler/debugger rule.
Concurrent explicit runs each allocate an instance; this is not a scheduler.
**These limits are not a dollar spending cap.** Provisioning, downloads, uploads,
stop grace periods and S3/ECR/CloudWatch storage can add charges. Free credits do
not guarantee every service/charge is covered. Use AWS budgets/quotas separately.

## Build and publish the managed image

```sh
make build-soup
make build-sagemaker
make test-sagemaker
# Optional local RTX 20 FP16 smoke; no AWS resources:
make test-sagemaker-gpu
```

The thin image preserves the pinned Soup CLI and dependencies. It maps native
SageMaker paths to `/input` and `/output`, fixes input/writable-volume ownership,
then drops to UID/GID 10001 before parsing model/data bytes. Input files are read-only
for the runner. Unlike Docker executors, SageMaker manages container isolation;
KitchenSoup does not claim control over managed-host capability/PID/rootfs flags.

Build with provenance disabled for a single-platform image manifest. Publish to your
already-provisioned ECR repository and use its **manifest digest**, not a Docker
config ID, mutable tag or attestation index. Example after substituting your registry:

```sh
aws ecr get-login-password --region us-east-2 | \
  docker login --username AWS --password-stdin 123456789012.dkr.ecr.us-east-2.amazonaws.com
docker tag kitchensoup-sagemaker-trainer:local \
  123456789012.dkr.ecr.us-east-2.amazonaws.com/kitchensoup-trainer:m21
docker push 123456789012.dkr.ecr.us-east-2.amazonaws.com/kitchensoup-trainer:m21
aws ecr describe-images --region us-east-2 --repository-name kitchensoup-trainer \
  --image-ids imageTag=m21 --query 'imageDetails[0].imageDigest' --output text
```

Image layers are large and ECR storage costs persist until deletion. Do not overwrite
or remove an image used by retained runs that you still intend to clone. Example
commands publish only when explicitly run; ordinary tests never call AWS.

## Configure and run

Run `make migrate` before starting updated processes. Enable these settings on all
three control-plane processes; mount credentials only into those trusted processes:

```text
KITCHENSOUP_SAGEMAKER_EXECUTION_ENABLED=true
KITCHENSOUP_AWS_APPROVED_PROFILES=default
KITCHENSOUP_SAGEMAKER_STAGING_WORKSPACE=/private/shared/sagemaker-staging
```

`aws-profile://default` uses the standard AWS environment/instance-role chain.
Named references use an explicitly approved SDK profile; configure its files and any
credential process as trusted operator input. No wildcard profile approval is supported.
References do not expose credentials through target API reads. Provider/SSH/Kubernetes
credential consumers continue to reject AWS profiles.

Create a target in Settings → Execution targets, or POST the substituted
[target example](../deploy/aws/training-target.example.json) to
`/api/v1/execution-targets`. Targets are immutable, including SQL update/delete guards.
Create a new target to change region, role, runtime, image or storage. Its read-only
test checks regional unversioned staging and pinned ECR image availability; it does
not allocate a GPU, test PassRole or prove training quota/job permissions.

Select the target when submitting a saved plan, using `execution_target_id` as for
SSH/Kubernetes. Keep AppSpec and reviewed data/model/recipe semantics unchanged.
For T4/g4dn choose **FP16**; Soup selects native BF16 on capable GPUs, so g5/g6 need a
BF16 reviewed plan. A precision mismatch fails rather than silently changing it.
Existing model/tokenization/sequence limits still apply.

The control plane materializes and hashes registered input bytes, uploads native
files beneath `<prefix>/kitchensoup-training-<run-uuid>/input/`, and maps them to
the `bundle` File-mode channel. SageMaker performs transfers with the execution
role outside the network-isolated runtime. Input/plan hashes, launch nonce and request
are retained privately. No AWS key or presigned URL enters the bundle or run snapshot.

PostgreSQL retains the deterministic job name and target/image binding. API run
reads include additive `external_references` containing the requested job ARN,
console link and CloudWatch group/stream prefix; these references do not prove a
remote job exists. The console link appears on the run page. InProgress/Stopping
remain active; cancellation confirms only after Stopped. API failures preserve
unknown state. Crashed submissions are observed, never automatically resubmitted.

Completed is not sufficient: the executor verifies the bounded native `model.tar.gz`,
manifest identity and adapter hashes before exporting the existing inert adapter ZIP.
The normal POSTPROCESSING registry transaction creates the model/version and both
lineages. Repeated import is idempotent. Invalid output never becomes a registered
success. Terminal cleanup refuses unverified completed output for investigation.
The archive reader rejects links, traversal, sparse files, duplicate/uninventoried
entries and oversized metadata/files/expansion. No output code is executed.

During training, use the AWS console/CloudWatch references; local cursor logs become
available after successful artifact collection. The wrapper writes bounded diagnostic
tails (at most 64 KiB per stream) to CloudWatch, including on ordinary CLI failure.
AWS termination can prevent a manifest/log upload. Raw logs can contain source data;
treat AWS logs and staged data as private. Paired evaluations/merge/AWQ remain local;
SageMaker endpoints are not available from this target.

## Stop and cleanup

Cancel from the run page (or POST `/api/v1/training-runs/{id}/cancel`) and wait for a
terminal state. StopTrainingJob is asynchronous; a stop request is not proof that
compute has been released. A completion that wins the stop race follows normal
verified registration. Use the console if the control plane becomes unavailable.

After registration or failed/canceled preparation, confirm **Clean up stopped execution
resources** (legacy DELETE `/api/v1/training-runs/{id}/container`). This removes only
that attempt's S3 staging prefix, including stranded multipart-upload parts,
preserving verified local output and registered
artifacts. API failures leave cleanup retryable. Shared bucket/ECR/IAM resources,
SageMaker job metadata and CloudWatch logs are retained by the adapter. Set an
appropriate CloudWatch retention policy separately; never delete a shared log group
as a per-run cleanup action. Local staging follows operator retention.

For disposable validation, additionally remove every resource created specifically
for the check: job metadata (DeleteTrainingJob after terminal state if permitted),
that job's log streams, test ECR repository/images, emptied staging bucket, dedicated
inline role policy and role. Remove only recorded test-owned names. Do not delete
pre-existing infrastructure or the shared `/aws/sagemaker/TrainingJobs` group. Verify
absence and terminal state, and retain cleanup evidence without credentials or
runtime payloads. There is no automatic provisioning/cleanup script that can erase
shared account resources.
