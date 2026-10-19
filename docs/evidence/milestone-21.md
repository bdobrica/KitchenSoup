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

## Live AWS follow-up

Date: 2026-10-20 (Europe/Bucharest)

The initial validation was blocked by an Organizations SCP and zero training GPU
quotas; no AWS resources were created at that stage. After the operator's access
and quota updates, a read-only check in `us-east-2` reported quota **1** for each
of `ml.g4dn.xlarge`, `ml.g5.xlarge` and `ml.g6.xlarge`. No in-progress training job,
ECR repository or SageMaker-named execution role was found during preflight.

The operator authorized inexpensive live validation and removal of its AWS
resources. A dedicated, restricted execution role/inline policy, private encrypted
unversioned staging bucket and private ECR repository were provisioned. The managed
trainer image was published as a single-platform manifest pinned to
`sha256:172cdff4687bb9956e98dc528c29fd572768c904102486c0b1f762daf4a36272`.
The role trust was restricted to the account's regional SageMaker service; its
data/image policy covered only the disposable bucket/prefix and repository.
Credentials remained in the trusted control plane.

One `ml.g4dn.xlarge` job used an actual **Tesla T4**, explicit **FP16**, 30 GiB,
a **600-second** maximum runtime, no spot/retry/warm pool, and network isolation.
A tiny randomly initialized Qwen2 model and repository conversation fixture were
registered through the real application services, with no external model download
or private user data. The reviewed plan used eight epochs, sequence length 256,
batch size/gradient accumulation 1 and LoRA rank 2. FP16 preflight enforced the
reviewed precision against Soup's hardware selection.

The production `TrainingRunService` and `JobProcessor.process` worker/reconciler
paths dispatched only this run; AWS clients, PostgreSQL, Soup and ArtifactStore
were real. The target connection test verified regional unversioned staging and
the exact ECR manifest before submission.

| Evidence | Result |
| --- | --- |
| Run | `21765bd0-77a0-4b55-aabd-2fd6153570de` |
| Reviewed plan SHA-256 | `da84270cdfa36ac69cbf375ecde1588f02576e8225fe61ebdb75d06826aeb4e0` |
| AWS result | `Completed`; training and billable duration **139 seconds** |
| Runner result | `succeeded`, stage `complete`, runner and Soup exit codes **0** |
| Registered model version | `e662339f-9344-5585-b1d7-90861af77dbd` |
| Registered adapter artifact | `4c007c69-3385-53e9-80b3-6cd22961118a` |
| Registered adapter ZIP SHA-256 | `ad26c5008b987b98ee233e243c4b7098365a69607dc51532f1011fae7fac165d` (21,867 bytes) |
| Adapter safetensors SHA-256 | `c483fadf2de4eacff7ec18ac26a1dccc1e99e7ff8dacb50a7e1148aee53a4ec6` |

The exact durable event sequence was
`QUEUED → PREPARING → SUBMITTED → RUNNING → POSTPROCESSING → SUCCEEDED`.
Output manifest identity and artifact hashes were verified before normal registry
import. The registered adapter ZIP was independently re-read from ArtifactStore
and its SHA-256/size checked against PostgreSQL. Its model-parent edge and all
three artifact-parent edges (base, dataset manifest and examples) were confirmed.
The application's cursor-based stdout log reader also returned training logs.
This establishes execution/lifecycle compatibility, not model quality.

## Follow-up verification and cleanup

- `make verify`: **441 unit tests passed**, Ruff formatting/lint (**294 files**)
  and strict mypy (**163 source files**) passed. The **21 focused SageMaker unit
  tests** also passed separately.
- `make test-integration`: **115 passed**, including both SageMaker cases,
  migration round trips/schema drift, registry lineage, restart and cancellation.
  Disposable local PostgreSQL/RustFS/Valkey containers were removed by the harness.
  An initial direct pytest invocation was rejected by its isolated-database guard;
  the documented harness then passed.
- No executor, runner, contract or dependency changes were needed for live AWS
  acceptance. Existing local CPU/FP16 evidence above remains applicable.
- AWS confirmed terminal completion before cleanup. The application's cleanup
  removed the run's staged objects and checked for stranded multipart uploads.
  The disposable job metadata, bucket, ECR image/repository and dedicated IAM
  role/inline policy were then deleted.
- AWS initially denied log deletion. After the operator granted access, the owned
  stream was removed. The training log group was confirmed absent immediately
  before this test and empty after stream deletion, so the validation-created
  group was also removed. No pre-existing or shared group was deleted.
- **Independent AWS absence checks passed** for the job, staging bucket, ECR
  repository/image, IAM role, owned log stream and newly created log group.
  The dedicated inline policy was deleted before its role. No temporary AWS
  resource or cleanup request remains pending, and no compute remains running.

No endpoint, Studio resource, notebook, VPC or NAT gateway was created. Local
synthetic registry artifacts, private execution evidence and cached development
images are retained. The disposable target is historical: cloning it requires
reprovisioning its deleted infrastructure/image. Operator IAM/quota changes were
not reverted. No credential, private runtime payload or exact AWS billing total
is retained or claimed; training duration excludes separate storage/log charges.
Milestone 21 live training acceptance is complete. Milestone 22 serving remains
unimplemented.
