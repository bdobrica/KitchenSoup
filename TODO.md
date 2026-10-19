# KitchenSoup — Implementation TODO

Each milestone should leave the repository in a working state. Milestone numbers
are stable identifiers; the current implementation order is in PLAN section 47.

---

## Milestone 0 — Repository bootstrap

- [x] Create the repository.
- [x] Add Apache License 2.0.
- [x] Add `PLAN.md`, `TODO.md`, and `README.md`.
- [x] Add Python project metadata with `pyproject.toml`.
- [x] Choose and configure supported Python version.
- [x] Add Ruff formatting/linting.
- [x] Add mypy or pyright if desired.
- [x] Add pytest.
- [x] Add `.editorconfig`.
- [x] Add `.gitignore`.
- [x] Add `.env.example`.
- [x] Create initial `Makefile`.
- [x] Implement `make help`.
- [x] Implement `make setup`.
- [x] Implement `make fmt`.
- [x] Implement `make lint`.
- [x] Implement `make test`.
- [x] Add initial CI workflow for lint + unit tests.
- [x] Add a minimal FastAPI application with `/healthz`.
- [x] Add a minimal Jinja page.
- [x] Add HTMX and Alpine.js without requiring a Node build.
- [x] Add a base application configuration layer using Pydantic settings.

**Acceptance criteria**

- [x] `make setup && make test` succeeds on a development machine.
- [x] FastAPI starts and serves the home page.
- [x] No Node.js toolchain is required.

---

## Milestone 1 — Local infrastructure

- [x] Add PostgreSQL to Docker Compose.
- [x] Add Valkey to Docker Compose.
- [x] Add RustFS to Docker Compose.
- [x] Add persistent local volumes.
- [x] Add service health checks.
- [x] Add FastAPI application service.
- [x] Add worker service placeholder.
- [x] Add reconciler service placeholder.
- [x] Implement `make up`.
- [x] Implement `make down`.
- [x] Implement `make restart`.
- [x] Implement `make logs`.
- [x] Implement `make ps`.
- [x] Implement `make clean`.
- [x] Add dependency health checks to application startup.
- [x] Add `make db-shell`.
- [x] Add `make shell`.

**Acceptance criteria**

- [x] `make up` starts a healthy development stack.
- [x] Application can connect to PostgreSQL, Valkey, and RustFS.
- [x] `make down` shuts the stack down cleanly.

---

## Milestone 2 — Database foundation

- [x] Add SQLAlchemy.
- [x] Add Alembic.
- [x] Add initial database session/unit-of-work layer.
- [x] Create migration for application metadata.
- [x] Create tables for:
  - [x] `model_catalog_entries`
  - [x] `models`
  - [x] `model_versions`
  - [x] `model_version_parents`
  - [x] `model_sources`
  - [x] `artifacts`
  - [x] `artifact_derivations`
  - [x] `datasets`
  - [x] `dataset_versions`
  - [x] `dataset_sources`
  - [x] `documents`
  - [x] `conversation_imports`
  - [x] `conversations`
  - [x] `training_runs`
  - [x] `job_events`
  - [x] `execution_targets`
  - [x] `llm_providers`
  - [x] `evaluation_suites`
  - [x] `evaluation_prompts`
  - [x] `evaluation_results`
  - [x] `deployments`
- [x] Add timestamps consistently.
- [x] Add immutable identifiers using UUIDs.
- [x] Add content-hash fields where relevant.
- [x] Add migration Makefile commands:
  - [x] `make migrate`
  - [x] `make migration`
- [x] Add repository/service tests for basic CRUD.

**Acceptance criteria**

- [x] A fresh database can be created using only Make targets.
- [x] All migrations are repeatable in CI.
- [x] Core entities can be created and queried.

---

## Milestone 3 — ArtifactStore and RustFS

- [x] Define `ArtifactStore` protocol.
- [x] Implement S3-compatible `ArtifactStore`.
- [x] Configure it for RustFS in Docker Compose.
- [x] Add object key helper/versioning rules.
- [x] Add content hashing.
- [x] Implement `put`.
- [x] Implement `get`.
- [x] Implement `stat`.
- [x] Implement `delete`.
- [x] Implement presigned GET.
- [x] Implement presigned PUT.
- [x] Add multipart upload support if required by the selected S3 client workflow.
- [x] Add upload-completion API.
- [x] Ensure browser uploads can bypass FastAPI.
- [x] Add integration tests against RustFS.

**Acceptance criteria**

- [x] Browser can upload an object using a presigned URL.
- [x] KitchenSoup records the object and its SHA-256 in PostgreSQL.
- [x] Browser can download the object through a presigned URL.

---

## Milestone 4 — Model catalog and model registry

- [x] Define version-controlled curated model catalog schema.
- [x] Add initial small Qwen models.
- [x] Store:
  - [x] repository
  - [x] revision
  - [x] parameter count
  - [x] context length
  - [x] license
  - [x] gated flag
  - [x] Soup compatibility
  - [x] vLLM compatibility
  - [x] quantization support
  - [x] approximate VRAM guidance
- [x] Add catalog loader/synchronizer.
- [x] Build model list UI.
- [x] Build model detail UI.
- [x] Add ungated Hugging Face model registration.
- [x] Record exact Hugging Face revision.
- [x] Add uploaded model source.
- [x] Support model archive upload through ArtifactStore.
- [x] Safely inspect uploaded archive.
- [x] Validate Hugging Face/Transformers-compatible structure.
- [x] Reject obvious unsupported model artifacts with a friendly error.
- [x] Display source license prominently.
- [x] Add API endpoints for model browsing/import.

**Acceptance criteria**

- [x] User can choose a curated Qwen model.
- [x] User can register an ungated Hugging Face model.
- [x] User can upload a compatible custom model artifact.
- [x] Model source and license metadata are visible.

---

## Milestone 5 — Raw source ingestion

- [x] Add dataset creation UI.
- [x] Add source upload UI.
- [x] Store original uploads unchanged.
- [x] Record upload SHA-256.
- [x] Add supported file type validation.
- [x] Add safe archive extraction utilities.
- [x] Prevent path traversal.
- [x] Add archive size/file-count limits.
- [x] Add source deletion flow.
- [x] Add source metadata UI.
- [x] Add initial document-source records.
- [x] Do not implement a general-purpose document parser.

**Acceptance criteria**

- [x] User can create a dataset and upload raw source material.
- [x] Raw sources remain retrievable.
- [x] Malicious archive paths are rejected.

---

## Milestone 6 — Canonical ChatGPT importer

- [x] Define `kitchensoup.conversation/v1`.
- [x] Define importer protocol.
- [x] Implement ChatGPT export detector.
- [x] Accept complete ChatGPT ZIP export.
- [x] Detect supported conversation JSON file layouts.
- [x] Parse conversations.
- [x] Preserve source conversation IDs.
- [x] Normalize message roles.
- [x] Normalize text content.
- [x] Mark unsupported multimodal/tool/attachment content.
- [x] Store canonical conversation artifacts.
- [x] Store conversation metadata in PostgreSQL.
- [x] Build conversation-selection UI.
- [x] Add search.
- [x] Add select all / select none.
- [x] Show title, date, and message count.
- [x] Add fixture-based importer tests.
- [x] Ensure importer failures do not destroy the raw upload.

**Acceptance criteria**

- [x] User uploads a ChatGPT export ZIP directly.
- [x] KitchenSoup lists the imported conversations.
- [x] User selects a subset for dataset creation.
- [x] Original export remains unchanged and stored.

---

## Milestone 7 — Soup-backed document ingestion

- [x] Define document-ingestion service boundary.
- [x] Add Soup ingestion runner mode.
- [x] Support initial Soup-supported document types.
- [x] Convert selected source documents into a versioned dataset artifact.
- [x] Capture Soup ingestion logs.
- [x] Record Soup version/image digest used.
- [x] Record warnings and ignored inputs.
- [x] Add ingestion integration fixtures.
- [x] Keep ingestion output separate from raw source objects.

**Acceptance criteria**

- [x] Supported documents can be converted into a dataset without KitchenSoup implementing parsing itself.
- [x] The ingestion operation is reproducible from stored source artifacts.

---

## Milestone 8 — Dataset manifests and preview

- [x] Define `kitchensoup.dataset-manifest/v1`.
- [x] Add dataset-version creation.
- [x] Make dataset versions immutable.
- [x] Store selected source IDs/hashes in manifest.
- [x] Convert selected canonical conversations into training examples.
- [x] Generate preview statistics:
  - [x] source count
  - [x] conversation count
  - [x] message count
  - [x] example count
  - [x] ignored item count
- [x] Add training-example preview UI.
- [x] Add paginated example browsing.
- [x] Add validation warnings.
- [x] Add dataset version detail page.
- [x] Add "create new version" flow.

**Acceptance criteria**

- [x] User can see exactly what training examples will be used.
- [x] Changing source selection creates a new dataset version.
- [x] Existing dataset versions never mutate.

---

## Milestone 9 — LLM provider abstraction

- [x] Define OpenAI-compatible `LLMProvider` interface.
- [x] Add provider configuration model.
- [x] Add:
  - [x] `base_url`
  - [x] `api_key_ref`
  - [x] configured model names
- [x] Implement `env://` credential reference.
- [x] Implement `file://` credential reference.
- [x] Add OpenAI-compatible client implementation.
- [x] Add provider health/test action.
- [x] Add settings UI.
- [x] Add explicit warning when source content will be sent externally.
- [x] Add support for OpenAI defaults.
- [x] Verify compatibility with LiteLLM-style base URLs.
- [x] Add structured-output helper.
- [x] Add unit tests with mocked provider.

**Acceptance criteria**

- [x] User can configure OpenAI using a secret reference.
- [x] User can configure an internal LiteLLM-compatible gateway.
- [x] KitchenSoup never persists the resolved API key in normal configuration rows.

---

## Milestone 10 — Intents, recipes, and AppSpec

- [x] Define `kitchensoup.appspec/v1`.
- [x] Define user intents:
  - [x] conversation imitation
  - [x] task from examples
  - [x] learn from documents
  - [x] advanced
- [x] Define versioned recipe format.
- [x] Add default conversation SFT recipe.
- [x] Add task-from-examples recipe.
- [x] Add initial document adaptation recipe.
- [x] Add recipe validation.
- [x] Add recipe resolver.
- [x] Resolve user intent into concrete training defaults.
- [x] Keep advanced ML fields hidden by default.
- [x] Add Advanced UI for resolved parameters.
- [x] Add "review training plan" screen.
- [x] Store both human intent and resolved config.
- [x] Add AppSpec serialization tests.
- [x] Add schema migration/versioning strategy.

**Acceptance criteria**

- [x] Non-technical user can configure training without entering ML parameters.
- [x] Advanced user can inspect the resolved settings.
- [x] The AppSpec remains independent of Soup configuration.

---

## Milestone 11 — Soup trainer image and translation

- [x] Create `images/soup-trainer`.
- [x] Pin Soup version.
- [x] Pin container dependencies.
- [x] Implement runner entrypoint.
- [x] Accept immutable AppSpec/resolved run input.
- [x] Implement AppSpec → Soup config translator.
- [x] Write generated `soup.yaml`.
- [x] Invoke Soup through CLI only.
- [x] Capture stdout/stderr.
- [x] Capture exit status.
- [x] Produce output manifest.
- [x] Record image digest.
- [x] Add tiny smoke-test dataset fixture.
- [x] Implement `make build-soup`.
- [x] Add translator unit tests.
- [x] Add optional GPU smoke test.

**Acceptance criteria**

- [x] A pinned container can train from a generated Soup config (validated by the separate synthetic CPU CLI probe).
- [x] KitchenSoup does not import Soup Python modules.
- [x] Run output is deterministic enough to register and reproduce.

---

## Milestone 12 — Local Docker training executor

- [x] Define `TrainingExecutor` protocol.
- [x] Implement local Docker executor.
- [x] Mount or materialize dataset input.
- [x] Pass immutable run spec.
- [x] Launch with `--gpus`.
- [x] Use deterministic container naming.
- [x] Implement status.
- [x] Implement logs.
- [x] Implement cancel.
- [x] Implement cleanup.
- [x] Persist external container ID/name.
- [x] Add `make gpu-check`.
- [x] Add `make test-gpu`.
- [x] Add a tiny end-to-end local training smoke test.

**Acceptance criteria**

- [x] Validate an FP16 Soup fine-tune through the local executor on a compatible NVIDIA GPU.
  See [FP16 GPU evidence](docs/evidence/fp16-training.md); BF16 validation is optional.
- [x] Status and logs appear in the application.
- [x] User can cancel the run.

---

## Milestone 13 — Queue, worker, reconciler, and job state

- [x] Define normalized job-state enum.
- [x] Enforce valid transitions.
- [x] Append every transition to `job_events`.
- [x] Add lightweight Valkey queue.
- [x] Implement worker command dispatch.
- [x] Implement reconciler loop.
- [x] Add claim/idempotency locking.
- [x] Poll non-terminal jobs.
- [x] Normalize executor status.
- [x] Detect lost jobs.
- [x] Implement cancel-request lifecycle.
- [x] Add retry-by-clone.
- [x] Keep submitted run immutable.
- [x] Add SSE endpoint.
- [x] Publish transient status updates through Valkey.
- [x] Add run progress UI.
- [x] Add logs UI.
- [x] Add state-machine unit tests.

**Acceptance criteria**

- [x] Long-running training survives web-process restart.
- [x] PostgreSQL always contains authoritative current state.
- [x] Browser receives live-ish progress without WebSockets.

See [Milestone 13 evidence](docs/evidence/milestone-13.md).

---

## Milestone 14 — Model registration and lineage

- [x] Register successful training output as a model version.
- [x] Add model-parent relationship.
- [x] Retain base-model license provenance.
- [x] Register LoRA adapter artifact.
- [x] Add artifact hash/size/format.
- [x] Implement artifact derivation relations.
- [x] Add model lineage UI.
- [x] Add artifact lineage UI.
- [x] Add lineage graph visualization with Mermaid or equivalent generated data.
- [x] Ensure a newly fine-tuned model appears in model selection.
- [x] Add "use as base model" flow.
- [x] Add lazy merged-model requirement detection.

**Acceptance criteria**

- [x] A completed run produces a reusable registered model version.
- [x] Model and artifact lineage are separately queryable.
- [x] License provenance remains visible.

See [Milestone 14 evidence](docs/evidence/milestone-14.md).

---

## Milestone 15 — Evaluation prompts

- [x] Add evaluation suite creation before training.
- [x] Support 3–10 simple test prompts initially.
- [x] Associate evaluation suite with run.
- [x] Add base-model inference runner.
- [x] Add fine-tuned-model inference runner.
- [x] Store generated responses.
- [x] Show responses side by side.
- [x] Allow:
  - [x] base better
  - [x] fine-tuned better
  - [x] same
- [x] Store user preferences.
- [x] Produce simple evaluation report artifact.
- [x] Show evaluation summary on model page.

**Acceptance criteria**

- [x] User can answer "did the fine-tuned model improve my examples?" without external tooling.

See [Milestone 15 evidence](docs/evidence/milestone-15.md).

---

## Milestone 16 — vLLM image and temporary playground

- [x] Create separate pinned vLLM image.
- [x] Implement `make build-vllm`.
- [x] Define `DeploymentExecutor` protocol.
- [x] Implement local Docker deployment executor.
- [x] Materialize selected model artifact.
- [x] Start vLLM with GPU access.
- [x] Persist endpoint/container identity.
- [x] Add deployment normalized state.
- [x] Add lease/`expires_at`.
- [x] Add reconciler handling for expired deployments.
- [x] Add "extend lease" action.
- [x] Add "keep running" action.
- [x] Add "stop now" action.
- [x] Add playground chat UI.
- [x] Proxy or safely expose OpenAI-compatible request path.
- [x] Add inference smoke test.

**Acceptance criteria**

- [x] User can launch a temporary playground from a registered model.
- [x] Playground shuts down automatically when its lease expires.
- [x] User can interact with the fine-tuned model in the browser.

See [Milestone 16 evidence](docs/evidence/milestone-16.md).

---

## Milestone 17 — Merge and quantization

- [x] Define artifact-transformation job model.
- [x] Implement lazy LoRA + base merge.
- [x] Register merged BF16/FP16 artifact.
- [x] Implement AWQ INT4 quantization.
- [x] Register AWQ artifact.
- [x] Add artifact derivation edges.
- [x] Display size and format.
- [x] Add post-processing progress state.
- [x] Add UI for "Create representation".
- [x] Prevent duplicate identical transformations.
- [x] Reuse existing artifact if transform already exists.
- [x] Add smoke test using a small model.

**Acceptance criteria**

- [x] One logical model can have LoRA, merged, and AWQ representations.
- [x] Derivation lineage is preserved.
- [x] Quantization does not create a separate logical model version.

See [Milestone 17 evidence](docs/evidence/milestone-17.md).

---

## Milestone 18 — Docker-over-SSH executor

- [x] Implement SSH credential reference support.
- [x] Defer `aws-profile://` credential parsing to Milestone 21 (SageMaker).
- [x] Add SSH execution target model/config.
- [x] Implement connection test.
- [x] Implement remote Docker availability test.
- [x] Implement remote NVIDIA capability test.
- [x] Implement default SSH/SFTP artifact transfer mode.
- [x] Create remote run workspace.
- [x] Upload run inputs.
- [x] Start Soup container remotely.
- [x] Implement remote status via Docker inspect.
- [x] Implement remote logs.
- [x] Implement remote cancellation.
- [x] Download output artifacts.
- [x] Clean remote workspace according to policy.
- [x] Defer optional direct-presigned-object transfer; SFTP is the default mode.
- [x] Implement SSH vLLM deployment executor.
- [x] Add execution target UI.

**Acceptance criteria**

- [x] Same AppSpec/dataset can run locally or on a remote SSH GPU machine.
- [x] Remote host does not need direct network access to local RustFS in default mode.
- [x] No KitchenSoup agent is installed remotely.

See [Milestone 18 evidence](docs/evidence/milestone-18.md).

---

## Milestone 19 — Kubernetes training

- [x] Add Kubernetes client dependency.
- [x] Add Kubernetes execution-target configuration.
- [x] Support kubeconfig/credential reference.
- [x] Add namespace configuration.
- [x] Implement `KubernetesTrainingExecutor`.
- [x] Generate normal Kubernetes Job.
- [x] Configure `nvidia.com/gpu`.
- [x] Support optional node selector.
- [x] Support optional tolerations.
- [x] Add input artifact materialization.
- [x] Add output artifact collection.
- [x] Implement status mapping.
- [x] Implement logs.
- [x] Implement cancellation by deleting/stopping Job.
- [x] Add cleanup policy.
- [x] Document NVIDIA GPU Operator or equivalent prepared NVIDIA stack as infrastructure prerequisite.
- [x] Add Kubernetes manifests/examples.

**Acceptance criteria**

- [x] KitchenSoup can submit and track a Soup training Job on a prepared GPU Kubernetes cluster.

See [Milestone 19 evidence](docs/evidence/milestone-19.md).

---

## Milestone 20 — Kubernetes vLLM deployment

- [x] Implement Kubernetes deployment executor.
- [x] Generate `Deployment`.
- [x] Generate `Service`.
- [x] Request GPU resources.
- [x] Add init-container model download.
- [x] Use `emptyDir` in MVP.
- [x] Add readiness handling.
- [x] Resolve endpoint/service URL.
- [x] Implement stop.
- [x] Implement lease expiry.
- [x] Add example ingress/network guidance without making ingress mandatory.

**Acceptance criteria**

- [x] Registered model can be temporarily served as vLLM on Kubernetes.

See [Milestone 20 evidence](docs/evidence/milestone-20.md).

---

## Milestone 21 — SageMaker training

- [x] Add AWS SDK dependency (reuse the existing boto3 dependency).
- [x] Implement `aws-profile://` credential reference.
- [x] Add SageMaker execution-target configuration.
- [x] Add ECR image configuration.
- [x] Add AWS S3 staging support.
- [x] Implement `SageMakerTrainingExecutor`.
- [x] Create training job.
- [x] Map input channels.
- [x] Map output location.
- [x] Normalize `DescribeTrainingJob` states.
- [x] Implement cancellation.
- [x] Surface CloudWatch/log references where practical.
- [x] Import completed model artifact into KitchenSoup registry.
- [x] Record SageMaker external identifiers.

**Acceptance criteria**

- [ ] Same logical KitchenSoup run can execute as a SageMaker Training Job.

Implementation and local lifecycle/FP16 wrapper checks are complete; live AWS GPU
acceptance awaits an approved regional training-instance quota. See
[setup and cost controls](docs/sagemaker-training.md) and
[validation evidence](docs/evidence/milestone-21.md).

---

## Milestone 22 — SageMaker vLLM BYOC deployment

- [ ] Build/pin SageMaker-compatible vLLM image.
- [ ] Push/configure ECR image.
- [ ] Implement model creation.
- [ ] Implement endpoint configuration.
- [ ] Implement endpoint lifecycle.
- [ ] Normalize endpoint state.
- [ ] Add stop/delete.
- [ ] Add playground lease semantics.
- [ ] Add endpoint invocation adapter.
- [ ] Record AWS resource IDs.

**Acceptance criteria**

- [ ] Registered model can be launched as a temporary SageMaker endpoint and used from the KitchenSoup playground.

---

## Milestone 23 — Error UX and safety polish

- [x] Add user-friendly error taxonomy.
- [x] Translate common GPU OOM failures.
- [x] Translate gated/Hugging Face download failures.
- [x] Translate storage failures.
- [x] Translate executor connectivity failures.
- [x] Keep raw error/log detail available under Advanced.
- [x] Add confirmation for destructive deletes.
- [x] Add object-retention policy.
- [x] Add raw-source deletion option (unprocessed, exclusively referenced originals).
- [x] Add external LLM data-sharing warning.
- [x] Add deployment endpoint exposure warning.
- [x] Add upload size limits.
- [x] Add archive bomb safeguards.

**Acceptance criteria**

- [x] Non-technical users receive actionable errors instead of raw stack traces for common failures.

See [Milestone 23 evidence](docs/evidence/milestone-23.md) and
[errors and retention](docs/safety-and-retention.md). Milestones 21–22 remain
pending without AWS account access.

---

## Milestone 24 — Documentation and release polish

- [ ] Finalize README installation instructions.
- [ ] Document all Makefile targets.
- [ ] Add architecture documentation.
- [ ] Add executor configuration examples.
- [ ] Add OpenAI provider example.
- [ ] Add LiteLLM provider example.
- [ ] Add OIDCGate deployment example.
- [ ] Add local NVIDIA prerequisites.
- [ ] Add SSH GPU-host prerequisites.
- [ ] Add Kubernetes prerequisites.
- [ ] Add SageMaker prerequisites.
- [ ] Add model-license behavior documentation.
- [ ] Add security assumptions.
- [ ] Add contributor guide.
- [ ] Add changelog/release process.
- [ ] Validate Apache-2.0 headers/notices as appropriate.
- [ ] Tag first MVP release.

---

## Milestone 25 — Runpod ephemeral GPU training

Shared infrastructure in this milestone also supports Milestone 26. Implement
25–26 before Milestone 24 release polish; existing milestone numbers are retained.
See [Runpod execution plan](PLAN.md#49-runpod-gpu-execution). Implementation and
local and live provider validation are complete.
See [validation evidence](docs/evidence/milestone-25.md).

- [x] Record the Runpod ownership, direct-execution, SSH trust and cleanup ADR.
- [x] Add a bounded Runpod REST API v2 client and read-only connection test.
- [x] Add immutable versioned target configuration, contracts and compatibility tests.
- [x] Resolve approved `env://` and `file://` API-key and separate SSH-key references.
- [x] Configure allowed GPU types, one GPU, minimum VRAM, cloud and optional data centers.
- [x] Discover availability/pricing and validate actual CUDA, VRAM and reviewed FP16/BF16 compatibility.
- [x] Build/configure a digest-pinned SSH-capable Soup image and bounded ephemeral disk.
- [x] Implement shared Pod creation, inspection and termination with bounded timeouts/retries.
- [x] Persist provisioning intent, ownership, external Pod identity and separate cleanup state.
- [x] Reconcile uncertain creation responses without relying on unique Pod names or blind allocation retries.
- [x] Enforce shared concurrent-Pod reservations, hourly GPU price and maximum run duration.
- [x] Implement `RunpodTrainingExecutor` through the existing training protocol.
- [x] Require full public-IP SSH/SFTP networking and explicit host-key verification.
- [x] Initialize private workspaces, stage immutable inputs and verify transferred hashes.
- [x] Execute Soup directly without Docker-in-Docker; collect bounded status and logs.
- [x] Verify output manifests/artifacts and import through existing ArtifactStore and registry lineage.
- [x] Integrate worker/reconciler recovery, cancellation and timeout handling.
- [x] Automatically terminate on completion/failure and retry pending cleanup after restart.
- [x] Add target selection, GPU compute estimates and pending-cleanup warnings to the UI.
- [x] Add mocked lifecycle, concurrent-reservation and failure-injection tests.
- [x] Add opt-in `make test-runpod-gpu` with a reviewed synthetic FP16/BF16 fixture and verified cleanup.
- [x] Document credentials, SSH trust/networking, image publication, limits and billing.

**Acceptance criteria**

- [x] A reviewed plan provisions a compatible Pod and trains with immutable inputs and a pinned image.
- [x] Verified artifacts enter the registry; status, logs and cancellation are available in the UI.
- [x] Success, failure, cancellation and timeout terminate the Pod; cleanup failures remain visible and retryable.
- [x] Worker restart, concurrent workers and uncertain API responses do not create duplicate billable Pods.
- [x] No remote access to local RustFS or runtime access to long-lived control-plane credentials is required.
- [x] Opt-in live GPU validation with reviewed precision confirms termination of all temporary provider resources.

---

## Milestone 26 — Runpod vLLM deployment

Depends on Milestone 25 shared provider, credential, ownership and cleanup support.
Implementation, repository checks, local FP16 and live BF16 inference validation
are complete, including confirmed temporary Pod cleanup. See
[validation evidence](docs/evidence/milestone-26.md).

- [x] Record public-endpoint protection and bounded cloud-lease decisions in an ADR.
- [x] Implement `RunpodDeploymentExecutor` through the existing deployment protocol.
- [x] Reuse shared Pod lifecycle, credential references, capacity reservations and cleanup reconciliation.
- [x] Configure independent serving GPU/VRAM/disk settings and a separate pinned SSH-capable vLLM image.
- [x] Resolve registered artifacts and validate supported base, merged, LoRA/base-revision and quantized combinations.
- [x] Provision a dedicated inference Pod and retain deployment/model/image/Pod ownership bindings.
- [x] Stage and verify model artifacts, launch vLLM directly and retain resolved serving parameters.
- [x] Implement model-loading/readiness checks, normalized status and bounded logs/errors.
- [x] Derive the HTTPS proxy endpoint only from validated owned Pod identity and fixed port.
- [x] Protect vLLM with deployment-specific authentication and proxy inference through KitchenSoup.
- [x] Keep raw endpoints and provider/inference credentials out of browser responses.
- [x] Add bounded streaming chat as a compatible API/UI extension; preserve non-streaming behavior.
- [x] Handle proxy timeouts, connection errors and client disconnects without logging prompts.
- [x] Integrate stop, bounded lease extension, expiry and unrecoverable-failure termination.
- [x] Disable indefinite keep-running for Runpod while preserving other targets' lease semantics.
- [x] Resume orphan discovery and pending termination after restart; confirm resource release.
- [x] Add model-page target selection, estimated GPU compute cost and remaining lease time.
- [x] Add mocked compatibility, readiness, authentication, streaming and cleanup-failure tests.
- [x] Add opt-in `make test-runpod-vllm` through the playground proxy with verified cleanup.
- [x] Document supported models/images, endpoint security, cost limits and automatic shutdown.

**Acceptance criteria**

- [x] A registered compatible model automatically provisions a Pod and becomes usable in the playground.
- [x] Authenticated OpenAI-compatible inference works through KitchenSoup, including bounded streaming.
- [x] Status, logs, stop and bounded lease extension work; expiry and explicit stop terminate the Pod.
- [x] Cleanup failures persist and are retried after application restart.
- [x] Training and inference use separate pinned images and independent Pod lifecycles under the shared limit.
- [x] Browser clients receive no provider/inference credentials or unprotected backend access.
- [x] Opt-in live inference validation confirms termination of all temporary provider resources.

---

# MVP release gate

The first MVP release is ready when the following end-to-end scenario works:

- [ ] Start local stack with `make up`.
- [ ] Open the KitchenSoup UI.
- [ ] Upload a ChatGPT export.
- [ ] Select conversations.
- [ ] Preview generated training examples.
- [ ] Choose a curated small Qwen model.
- [ ] Add several evaluation prompts.
- [ ] Submit a local GPU fine-tuning run.
- [ ] Follow progress in the UI.
- [ ] Receive a registered model version.
- [ ] Inspect model lineage and artifacts.
- [ ] Compare base and fine-tuned outputs.
- [ ] Create a merged or AWQ artifact.
- [ ] Launch a temporary local vLLM playground.
- [ ] Chat with the fine-tuned model.
- [ ] Allow the playground lease to stop the container automatically.
- [ ] Repeat training on an SSH-accessible remote GPU machine.

Kubernetes and SageMaker may follow immediately after this release if the local/SSH executor abstractions are proven.
