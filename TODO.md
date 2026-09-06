# KitchenSoup — Implementation TODO

This checklist is ordered so it can be implemented consecutively. Each milestone should leave the repository in a working state.

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

- [ ] Define OpenAI-compatible `LLMProvider` interface.
- [ ] Add provider configuration model.
- [ ] Add:
  - [ ] `base_url`
  - [ ] `api_key_ref`
  - [ ] configured model names
- [ ] Implement `env://` credential reference.
- [ ] Implement `file://` credential reference.
- [ ] Add OpenAI-compatible client implementation.
- [ ] Add provider health/test action.
- [ ] Add settings UI.
- [ ] Add explicit warning when source content will be sent externally.
- [ ] Add support for OpenAI defaults.
- [ ] Verify compatibility with LiteLLM-style base URLs.
- [ ] Add structured-output helper.
- [ ] Add unit tests with mocked provider.

**Acceptance criteria**

- [ ] User can configure OpenAI using a secret reference.
- [ ] User can configure an internal LiteLLM-compatible gateway.
- [ ] KitchenSoup never persists the resolved API key in normal configuration rows.

---

## Milestone 10 — Intents, recipes, and AppSpec

- [ ] Define `kitchensoup.appspec/v1`.
- [ ] Define user intents:
  - [ ] conversation imitation
  - [ ] task from examples
  - [ ] learn from documents
  - [ ] advanced
- [ ] Define versioned recipe format.
- [ ] Add default conversation SFT recipe.
- [ ] Add task-from-examples recipe.
- [ ] Add initial document adaptation recipe.
- [ ] Add recipe validation.
- [ ] Add recipe resolver.
- [ ] Resolve user intent into concrete training defaults.
- [ ] Keep advanced ML fields hidden by default.
- [ ] Add Advanced UI for resolved parameters.
- [ ] Add "review training plan" screen.
- [ ] Store both human intent and resolved config.
- [ ] Add AppSpec serialization tests.
- [ ] Add schema migration/versioning strategy.

**Acceptance criteria**

- [ ] Non-technical user can configure training without entering ML parameters.
- [ ] Advanced user can inspect the resolved settings.
- [ ] The AppSpec remains independent of Soup configuration.

---

## Milestone 11 — Soup trainer image and translation

- [ ] Create `images/soup-trainer`.
- [ ] Pin Soup version.
- [ ] Pin container dependencies.
- [ ] Implement runner entrypoint.
- [ ] Accept immutable AppSpec/resolved run input.
- [ ] Implement AppSpec → Soup config translator.
- [ ] Write generated `soup.yaml`.
- [ ] Invoke Soup through CLI only.
- [ ] Capture stdout/stderr.
- [ ] Capture exit status.
- [ ] Produce output manifest.
- [ ] Record image digest.
- [ ] Add tiny smoke-test dataset fixture.
- [ ] Implement `make build-soup`.
- [ ] Add translator unit tests.
- [ ] Add optional GPU smoke test.

**Acceptance criteria**

- [ ] A pinned container can train from a generated Soup config.
- [ ] KitchenSoup does not import Soup Python modules.
- [ ] Run output is deterministic enough to register and reproduce.

---

## Milestone 12 — Local Docker training executor

- [ ] Define `TrainingExecutor` protocol.
- [ ] Implement local Docker executor.
- [ ] Mount or materialize dataset input.
- [ ] Pass immutable run spec.
- [ ] Launch with `--gpus`.
- [ ] Use deterministic container naming.
- [ ] Implement status.
- [ ] Implement logs.
- [ ] Implement cancel.
- [ ] Implement cleanup.
- [ ] Persist external container ID/name.
- [ ] Add `make gpu-check`.
- [ ] Add `make test-gpu`.
- [ ] Add a tiny end-to-end local training smoke test.

**Acceptance criteria**

- [ ] KitchenSoup can launch a Soup fine-tune on the local NVIDIA GPU.
- [ ] Status and logs appear in the application.
- [ ] User can cancel the run.

---

## Milestone 13 — Queue, worker, reconciler, and job state

- [ ] Define normalized job-state enum.
- [ ] Enforce valid transitions.
- [ ] Append every transition to `job_events`.
- [ ] Add lightweight Valkey queue.
- [ ] Implement worker command dispatch.
- [ ] Implement reconciler loop.
- [ ] Add claim/idempotency locking.
- [ ] Poll non-terminal jobs.
- [ ] Normalize executor status.
- [ ] Detect lost jobs.
- [ ] Implement cancel-request lifecycle.
- [ ] Add retry-by-clone.
- [ ] Keep submitted run immutable.
- [ ] Add SSE endpoint.
- [ ] Publish transient status updates through Valkey.
- [ ] Add run progress UI.
- [ ] Add logs UI.
- [ ] Add state-machine unit tests.

**Acceptance criteria**

- [ ] Long-running training survives web-process restart.
- [ ] PostgreSQL always contains authoritative current state.
- [ ] Browser receives live-ish progress without WebSockets.

---

## Milestone 14 — Model registration and lineage

- [ ] Register successful training output as a model version.
- [ ] Add model-parent relationship.
- [ ] Retain base-model license provenance.
- [ ] Register LoRA adapter artifact.
- [ ] Add artifact hash/size/format.
- [ ] Implement artifact derivation relations.
- [ ] Add model lineage UI.
- [ ] Add artifact lineage UI.
- [ ] Add lineage graph visualization with Mermaid or equivalent generated data.
- [ ] Ensure a newly fine-tuned model appears in model selection.
- [ ] Add "use as base model" flow.
- [ ] Add lazy merged-model requirement detection.

**Acceptance criteria**

- [ ] A completed run produces a reusable registered model version.
- [ ] Model and artifact lineage are separately queryable.
- [ ] License provenance remains visible.

---

## Milestone 15 — Evaluation prompts

- [ ] Add evaluation suite creation before training.
- [ ] Support 3–10 simple test prompts initially.
- [ ] Associate evaluation suite with run.
- [ ] Add base-model inference runner.
- [ ] Add fine-tuned-model inference runner.
- [ ] Store generated responses.
- [ ] Show responses side by side.
- [ ] Allow:
  - [ ] base better
  - [ ] fine-tuned better
  - [ ] same
- [ ] Store user preferences.
- [ ] Produce simple evaluation report artifact.
- [ ] Show evaluation summary on model page.

**Acceptance criteria**

- [ ] User can answer "did the fine-tuned model improve my examples?" without external tooling.

---

## Milestone 16 — vLLM image and temporary playground

- [ ] Create separate pinned vLLM image.
- [ ] Implement `make build-vllm`.
- [ ] Define `DeploymentExecutor` protocol.
- [ ] Implement local Docker deployment executor.
- [ ] Materialize selected model artifact.
- [ ] Start vLLM with GPU access.
- [ ] Persist endpoint/container identity.
- [ ] Add deployment normalized state.
- [ ] Add lease/`expires_at`.
- [ ] Add reconciler handling for expired deployments.
- [ ] Add "extend lease" action.
- [ ] Add "keep running" action.
- [ ] Add "stop now" action.
- [ ] Add playground chat UI.
- [ ] Proxy or safely expose OpenAI-compatible request path.
- [ ] Add inference smoke test.

**Acceptance criteria**

- [ ] User can launch a temporary playground from a registered model.
- [ ] Playground shuts down automatically when its lease expires.
- [ ] User can interact with the fine-tuned model in the browser.

---

## Milestone 17 — Merge and quantization

- [ ] Define artifact-transformation job model.
- [ ] Implement lazy LoRA + base merge.
- [ ] Register merged BF16/FP16 artifact.
- [ ] Implement AWQ INT4 quantization.
- [ ] Register AWQ artifact.
- [ ] Add artifact derivation edges.
- [ ] Display size and format.
- [ ] Add post-processing progress state.
- [ ] Add UI for "Create representation".
- [ ] Prevent duplicate identical transformations.
- [ ] Reuse existing artifact if transform already exists.
- [ ] Add smoke test using a small model.

**Acceptance criteria**

- [ ] One logical model can have LoRA, merged, and AWQ representations.
- [ ] Derivation lineage is preserved.
- [ ] Quantization does not create a separate logical model version.

---

## Milestone 18 — Docker-over-SSH executor

- [ ] Implement SSH credential reference support.
- [ ] Add `aws-profile://` parsing only if useful here or defer to SageMaker milestone.
- [ ] Add SSH execution target model/config.
- [ ] Implement connection test.
- [ ] Implement remote Docker availability test.
- [ ] Implement remote NVIDIA capability test.
- [ ] Implement default SSH/SFTP artifact transfer mode.
- [ ] Create remote run workspace.
- [ ] Upload run inputs.
- [ ] Start Soup container remotely.
- [ ] Implement remote status via Docker inspect.
- [ ] Implement remote logs.
- [ ] Implement remote cancellation.
- [ ] Download output artifacts.
- [ ] Clean remote workspace according to policy.
- [ ] Add optional direct-presigned-object transfer mode later.
- [ ] Implement SSH vLLM deployment executor.
- [ ] Add execution target UI.

**Acceptance criteria**

- [ ] Same AppSpec/dataset can run locally or on a remote SSH GPU machine.
- [ ] Remote host does not need direct network access to local RustFS in default mode.
- [ ] No KitchenSoup agent is installed remotely.

---

## Milestone 19 — Kubernetes training

- [ ] Add Kubernetes client dependency.
- [ ] Add Kubernetes execution-target configuration.
- [ ] Support kubeconfig/credential reference.
- [ ] Add namespace configuration.
- [ ] Implement `KubernetesTrainingExecutor`.
- [ ] Generate normal Kubernetes Job.
- [ ] Configure `nvidia.com/gpu`.
- [ ] Support optional node selector.
- [ ] Support optional tolerations.
- [ ] Add input artifact materialization.
- [ ] Add output artifact collection.
- [ ] Implement status mapping.
- [ ] Implement logs.
- [ ] Implement cancellation by deleting/stopping Job.
- [ ] Add cleanup policy.
- [ ] Document NVIDIA GPU Operator as infrastructure prerequisite.
- [ ] Add Kubernetes manifests/examples.

**Acceptance criteria**

- [ ] KitchenSoup can submit and track a Soup training Job on a prepared GPU Kubernetes cluster.

---

## Milestone 20 — Kubernetes vLLM deployment

- [ ] Implement Kubernetes deployment executor.
- [ ] Generate `Deployment`.
- [ ] Generate `Service`.
- [ ] Request GPU resources.
- [ ] Add init-container model download.
- [ ] Use `emptyDir` in MVP.
- [ ] Add readiness handling.
- [ ] Resolve endpoint/service URL.
- [ ] Implement stop.
- [ ] Implement lease expiry.
- [ ] Add example ingress/network guidance without making ingress mandatory.

**Acceptance criteria**

- [ ] Registered model can be temporarily served as vLLM on Kubernetes.

---

## Milestone 21 — SageMaker training

- [ ] Add AWS SDK dependency.
- [ ] Implement `aws-profile://` credential reference.
- [ ] Add SageMaker execution-target configuration.
- [ ] Add ECR image configuration.
- [ ] Add AWS S3 staging support.
- [ ] Implement `SageMakerTrainingExecutor`.
- [ ] Create training job.
- [ ] Map input channels.
- [ ] Map output location.
- [ ] Normalize `DescribeTrainingJob` states.
- [ ] Implement cancellation.
- [ ] Surface CloudWatch/log references where practical.
- [ ] Import completed model artifact into KitchenSoup registry.
- [ ] Record SageMaker external identifiers.

**Acceptance criteria**

- [ ] Same logical KitchenSoup run can execute as a SageMaker Training Job.

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

- [ ] Add user-friendly error taxonomy.
- [ ] Translate common GPU OOM failures.
- [ ] Translate gated/Hugging Face download failures.
- [ ] Translate storage failures.
- [ ] Translate executor connectivity failures.
- [ ] Keep raw error/log detail available under Advanced.
- [ ] Add confirmation for destructive deletes.
- [ ] Add object-retention policy.
- [ ] Add raw-source deletion option.
- [ ] Add external LLM data-sharing warning.
- [ ] Add deployment endpoint exposure warning.
- [ ] Add upload size limits.
- [ ] Add archive bomb safeguards.

**Acceptance criteria**

- [ ] Non-technical users receive actionable errors instead of raw stack traces for common failures.

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
