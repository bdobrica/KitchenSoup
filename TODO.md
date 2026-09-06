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

- [ ] Add SQLAlchemy.
- [ ] Add Alembic.
- [ ] Add initial database session/unit-of-work layer.
- [ ] Create migration for application metadata.
- [ ] Create tables for:
  - [ ] `model_catalog_entries`
  - [ ] `models`
  - [ ] `model_versions`
  - [ ] `model_version_parents`
  - [ ] `model_sources`
  - [ ] `artifacts`
  - [ ] `artifact_derivations`
  - [ ] `datasets`
  - [ ] `dataset_versions`
  - [ ] `dataset_sources`
  - [ ] `documents`
  - [ ] `conversation_imports`
  - [ ] `conversations`
  - [ ] `training_runs`
  - [ ] `job_events`
  - [ ] `execution_targets`
  - [ ] `llm_providers`
  - [ ] `evaluation_suites`
  - [ ] `evaluation_prompts`
  - [ ] `evaluation_results`
  - [ ] `deployments`
- [ ] Add timestamps consistently.
- [ ] Add immutable identifiers using UUIDs.
- [ ] Add content-hash fields where relevant.
- [ ] Add migration Makefile commands:
  - [ ] `make migrate`
  - [ ] `make migration`
- [ ] Add repository/service tests for basic CRUD.

**Acceptance criteria**

- [ ] A fresh database can be created using only Make targets.
- [ ] All migrations are repeatable in CI.
- [ ] Core entities can be created and queried.

---

## Milestone 3 — ArtifactStore and RustFS

- [ ] Define `ArtifactStore` protocol.
- [ ] Implement S3-compatible `ArtifactStore`.
- [ ] Configure it for RustFS in Docker Compose.
- [ ] Add object key helper/versioning rules.
- [ ] Add content hashing.
- [ ] Implement `put`.
- [ ] Implement `get`.
- [ ] Implement `stat`.
- [ ] Implement `delete`.
- [ ] Implement presigned GET.
- [ ] Implement presigned PUT.
- [ ] Add multipart upload support if required by the selected S3 client workflow.
- [ ] Add upload-completion API.
- [ ] Ensure browser uploads can bypass FastAPI.
- [ ] Add integration tests against RustFS.
- [ ] Add a local/test storage implementation if useful.

**Acceptance criteria**

- [ ] Browser can upload an object using a presigned URL.
- [ ] KitchenSoup records the object and its SHA-256 in PostgreSQL.
- [ ] Browser can download the object through a presigned URL.

---

## Milestone 4 — Model catalog and model registry

- [ ] Define version-controlled curated model catalog schema.
- [ ] Add initial small Qwen models.
- [ ] Store:
  - [ ] repository
  - [ ] revision
  - [ ] parameter count
  - [ ] context length
  - [ ] license
  - [ ] gated flag
  - [ ] Soup compatibility
  - [ ] vLLM compatibility
  - [ ] quantization support
  - [ ] approximate VRAM guidance
- [ ] Add catalog loader/synchronizer.
- [ ] Build model list UI.
- [ ] Build model detail UI.
- [ ] Add ungated Hugging Face model registration.
- [ ] Record exact Hugging Face revision.
- [ ] Add uploaded model source.
- [ ] Support model archive upload through ArtifactStore.
- [ ] Safely inspect uploaded archive.
- [ ] Validate Hugging Face/Transformers-compatible structure.
- [ ] Reject obvious unsupported model artifacts with a friendly error.
- [ ] Display source license prominently.
- [ ] Add API endpoints for model browsing/import.

**Acceptance criteria**

- [ ] User can choose a curated Qwen model.
- [ ] User can register an ungated Hugging Face model.
- [ ] User can upload a compatible custom model artifact.
- [ ] Model source and license metadata are visible.

---

## Milestone 5 — Raw source ingestion

- [ ] Add dataset creation UI.
- [ ] Add source upload UI.
- [ ] Store original uploads unchanged.
- [ ] Record upload SHA-256.
- [ ] Add supported file type validation.
- [ ] Add safe archive extraction utilities.
- [ ] Prevent path traversal.
- [ ] Add archive size/file-count limits.
- [ ] Add source deletion flow.
- [ ] Add source metadata UI.
- [ ] Add initial document-source records.
- [ ] Do not implement a general-purpose document parser.

**Acceptance criteria**

- [ ] User can create a dataset and upload raw source material.
- [ ] Raw sources remain retrievable.
- [ ] Malicious archive paths are rejected.

---

## Milestone 6 — Canonical ChatGPT importer

- [ ] Define `kitchensoup.conversation/v1`.
- [ ] Define importer protocol.
- [ ] Implement ChatGPT export detector.
- [ ] Accept complete ChatGPT ZIP export.
- [ ] Detect supported conversation JSON file layouts.
- [ ] Parse conversations.
- [ ] Preserve source conversation IDs.
- [ ] Normalize message roles.
- [ ] Normalize text content.
- [ ] Mark unsupported multimodal/tool/attachment content.
- [ ] Store canonical conversation artifacts.
- [ ] Store conversation metadata in PostgreSQL.
- [ ] Build conversation-selection UI.
- [ ] Add search.
- [ ] Add select all / select none.
- [ ] Show title, date, and message count.
- [ ] Add fixture-based importer tests.
- [ ] Ensure importer failures do not destroy the raw upload.

**Acceptance criteria**

- [ ] User uploads a ChatGPT export ZIP directly.
- [ ] KitchenSoup lists the imported conversations.
- [ ] User selects a subset for dataset creation.
- [ ] Original export remains unchanged and stored.

---

## Milestone 7 — Soup-backed document ingestion

- [ ] Define document-ingestion service boundary.
- [ ] Add Soup ingestion runner mode.
- [ ] Support initial Soup-supported document types.
- [ ] Convert selected source documents into a versioned dataset artifact.
- [ ] Capture Soup ingestion logs.
- [ ] Record Soup version/image digest used.
- [ ] Record warnings and ignored inputs.
- [ ] Add ingestion integration fixtures.
- [ ] Keep ingestion output separate from raw source objects.

**Acceptance criteria**

- [ ] Supported documents can be converted into a dataset without KitchenSoup implementing parsing itself.
- [ ] The ingestion operation is reproducible from stored source artifacts.

---

## Milestone 8 — Dataset manifests and preview

- [ ] Define `kitchensoup.dataset-manifest/v1`.
- [ ] Add dataset-version creation.
- [ ] Make dataset versions immutable.
- [ ] Store selected source IDs/hashes in manifest.
- [ ] Convert selected canonical conversations into training examples.
- [ ] Generate preview statistics:
  - [ ] source count
  - [ ] conversation count
  - [ ] message count
  - [ ] example count
  - [ ] ignored item count
- [ ] Add training-example preview UI.
- [ ] Add paginated example browsing.
- [ ] Add validation warnings.
- [ ] Add dataset version detail page.
- [ ] Add "create new version" flow.

**Acceptance criteria**

- [ ] User can see exactly what training examples will be used.
- [ ] Changing source selection creates a new dataset version.
- [ ] Existing dataset versions never mutate.

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
