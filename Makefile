.DEFAULT_GOAL := help
PYTHON ?= python3.13
VENV := .venv
VENV_PYTHON := $(VENV)/bin/python
COMPOSE ?= docker compose
PRECISION ?= bf16
DOCKER_BUILD_NETWORK ?= default
export MESSAGE

.PHONY: help setup fmt lint test test-unit verify dev
.PHONY: local-env up down restart logs ps clean shell db-shell compose-check check-dependencies
.PHONY: migrate migration migration-check test-integration
.PHONY: openapi storage-init catalog-sync

help: ## Show available developer commands
	@awk 'BEGIN {FS = ":.*## "} /^[a-zA-Z_-]+:.*## / {printf "  %-14s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

setup: ## Create a Python 3.13 virtualenv and install application + dev tools
	$(PYTHON) -c 'import sys; assert sys.version_info[:2] == (3, 13), "Python 3.13 is required"'
	$(PYTHON) -m venv $(VENV)
	$(VENV_PYTHON) -m pip install -e '.[dev]'

fmt: ## Format Python code and organize imports
	$(VENV_PYTHON) -m ruff check --select I --fix .
	$(VENV_PYTHON) -m ruff format .

lint: ## Check formatting, lint rules, and types
	$(VENV_PYTHON) -m ruff format --check .
	$(VENV_PYTHON) -m ruff check .
	$(VENV_PYTHON) -m mypy

test: ## Run all service-free tests
	$(VENV_PYTHON) -m pytest

test-unit: ## Run unit tests
	$(VENV_PYTHON) -m pytest tests/unit

verify: lint test ## Run the aggregate local and CI gate

dev: ## Serve the application at http://127.0.0.1:8000
	$(VENV_PYTHON) -m uvicorn app.main:create_app --factory --reload --host 127.0.0.1 --port 8000

local-env: ## Generate missing development credentials in ignored .env
	$(PYTHON) scripts/local_env.py

up: local-env build-soup-ingest ## Build and start the stack, waiting for healthy services
	$(COMPOSE) build web worker reconciler catalog-init storage-init
	SOUP_IMAGE_ID=$$(docker image inspect kitchensoup-soup-ingest:local --format '{{.Id}}') $(COMPOSE) up --no-build --detach --wait --wait-timeout 180

down: ## Stop the stack, retaining local data volumes
	$(COMPOSE) down --remove-orphans

restart: ## Recreate the stack and recheck startup dependencies, retaining data
	$(MAKE) down
	$(MAKE) up

logs: ## Follow stack logs (Ctrl+C to stop following)
	$(COMPOSE) logs --follow --tail=100

ps: ## Show stack status
	$(COMPOSE) ps

clean: ## Delete this stack and its data volumes (requires CONFIRM=1)
	@test "$(CONFIRM)" = "1" || { echo 'This deletes local database and object data. Use make clean CONFIRM=1.'; exit 1; }
	$(COMPOSE) down --volumes --remove-orphans

shell: ## Open a shell inside the web container
	$(COMPOSE) exec web sh

db-shell: ## Open psql inside PostgreSQL
	$(COMPOSE) exec postgres psql -U kitchensoup -d kitchensoup

compose-check: local-env ## Validate the Compose configuration without printing credentials
	$(COMPOSE) config --quiet

check-dependencies: ## Probe PostgreSQL, Valkey, and RustFS from the web container
	$(COMPOSE) exec -T web python -c 'from app.config import Settings; from app.dependencies import check_dependencies; check_dependencies(Settings()); print("All dependency checks passed")'

migrate: local-env ## Build the application and upgrade the local database to head
	$(COMPOSE) up --detach --wait postgres
	$(COMPOSE) build web
	$(COMPOSE) run --rm --no-deps web python -m alembic upgrade head

migration: local-env ## Generate a reviewed migration (MESSAGE="describe change")
	@test -n "$$MESSAGE" || { echo 'Set MESSAGE to describe the migration.'; exit 1; }
	$(COMPOSE) up --detach --wait postgres
	$(COMPOSE) build web
	$(COMPOSE) run --rm --no-deps --user "$$(id -u):$$(id -g)" --volume "$(CURDIR):/workspace" --workdir /workspace web python -m alembic revision --autogenerate -m "$$MESSAGE"

migration-check: ## Detect schema drift between the migrated database and ORM metadata
	$(COMPOSE) run --rm --no-deps web python -m alembic check

test-integration: ## Run integration tests using disposable PostgreSQL, RustFS and Valkey
	$(VENV_PYTHON) scripts/test_database.py

storage-init: local-env ## Configure the local artifact bucket and browser CORS
	$(COMPOSE) up --detach --wait rustfs
	$(COMPOSE) build storage-init
	$(COMPOSE) run --rm --no-deps storage-init

openapi: ## Regenerate versioned API and catalog schemas
	$(VENV_PYTHON) scripts/export_openapi.py

.PHONY: storage-retention
storage-retention: ## Delete expired upload staging after a one-day grace (requires CONFIRM=1)
	@test "$(CONFIRM)" = "1" || { echo 'Use make storage-retention CONFIRM=1 to delete expired staging objects.'; exit 1; }
	$(COMPOSE) run --rm --no-deps web python -m app.storage.retention --confirm

catalog-sync: local-env ## Synchronize the packaged catalog into the migrated database
	$(COMPOSE) up --detach --wait postgres
	$(COMPOSE) build catalog-init
	$(COMPOSE) run --rm --no-deps catalog-init

.PHONY: build-soup-ingest test-soup-ingest
build-soup-ingest: ## Build the pinned Soup document CLI image
	$(COMPOSE) build soup-ingest

test-soup-ingest: build-soup-ingest ## Exercise real Soup document fixtures in its isolated image
	$(VENV_PYTHON) scripts/test_soup_ingest.py

.PHONY: build-soup test-soup test-soup-gpu
build-soup: ## Build the pinned Soup trainer image (large CUDA dependencies)
	docker build --platform linux/amd64 -f images/soup-trainer/Dockerfile -t kitchensoup-soup-trainer:local .

test-soup: build-soup ## Validate the offline trainer bundle and real Soup CLI without a GPU
	$(VENV_PYTHON) scripts/test_soup_trainer.py

.PHONY: build-sagemaker test-sagemaker test-sagemaker-gpu
build-sagemaker: ## Build the thin managed-training adapter from the cached pinned Soup image
	docker build --platform linux/amd64 --provenance=false --build-arg TRAINER_ID=$$(docker image inspect kitchensoup-soup-trainer:local --format '{{.Id}}') -f images/soup-trainer/Dockerfile.sagemaker -t kitchensoup-sagemaker-trainer:local .

test-sagemaker: build-sagemaker ## Offline CPU SageMaker filesystem/privilege-drop check (no AWS cost)
	$(VENV_PYTHON) scripts/test_sagemaker_runner.py

test-sagemaker-gpu: build-sagemaker ## Local FP16 GPU check of managed-training wrapper (no AWS cost)
	$(VENV_PYTHON) scripts/test_sagemaker_runner.py --gpu

test-soup-gpu: build-soup ## Optional GPU training smoke (PRECISION=bf16 or fp16) (synthetic tiny model)
	$(VENV_PYTHON) scripts/test_soup_trainer.py --gpu --precision $(PRECISION)

.PHONY: gpu-check test-gpu test-local-training training-infra dev-training
gpu-check: ## Check Docker GPU access and selected precision (build-soup first)
	$(VENV_PYTHON) scripts/gpu_check.py --precision $(PRECISION)

test-local-training: build-soup ## Exercise real Docker lifecycle with offline CPU validation
	$(VENV_PYTHON) scripts/test_local_training.py

test-gpu: build-soup gpu-check ## Tiny end-to-end local Docker training smoke (PRECISION=bf16 or fp16)
	$(VENV_PYTHON) scripts/test_local_training.py --gpu --precision $(PRECISION)

training-infra: local-env ## Start loopback infrastructure for the opt-in host training processes
	$(COMPOSE) -f docker-compose.yml -f docker-compose.training.yml up --detach --wait postgres valkey rustfs

dev-training: ## Serve the host application with local Docker training enabled
	$(VENV_PYTHON) scripts/dev_training.py

.PHONY: worker-training reconcile-training
worker-training: ## Run the host training command worker (separate terminal)
	$(VENV_PYTHON) scripts/dev_training.py --role worker

reconcile-training: ## Run the host training reconciler (separate terminal)
	$(VENV_PYTHON) scripts/dev_training.py --role reconciler

.PHONY: test-evaluation-gpu
test-evaluation-gpu: build-soup ## Tiny offline base/LoRA GPU inference smoke (PRECISION=fp16 on RTX 20)
	$(VENV_PYTHON) scripts/test_evaluation_gpu.py --precision $(PRECISION)

.PHONY: build-vllm test-vllm-gpu
build-vllm: ## Build the separate digest-pinned vLLM playground image
	docker build --platform linux/amd64 -f images/vllm/Dockerfile -t kitchensoup-vllm:local .

test-vllm-gpu: build-vllm build-soup ## Optional offline FP16 base/LoRA serving and stop smoke
	$(VENV_PYTHON) scripts/test_vllm_gpu.py

.PHONY: build-transform test-transform-gpu
build-transform: ## Build the pinned offline merge/AWQ image
	docker build --platform linux/amd64 -f images/model-transform/Dockerfile -t kitchensoup-model-transform:local .

test-transform-gpu: build-transform build-soup build-vllm ## Offline FP16 merge and AWQ GPU smoke
	$(VENV_PYTHON) scripts/test_transform_gpu.py

.PHONY: test-ssh-gpu
.PHONY: build-runpod test-runpod-gpu
test-runpod-image: ## Local SSH/SFTP/direct runner CPU check (build-runpod first; no cloud cost)
	$(VENV_PYTHON) scripts/test_runpod_image.py

build-runpod: ## Build the SSH-capable direct trainer (publish and pin before cloud use)
	docker build --network $(DOCKER_BUILD_NETWORK) --platform linux/amd64 --provenance=false --build-arg TRAINER_ID=$$(docker image inspect kitchensoup-soup-trainer:local --format '{{.Id}}') -f images/soup-trainer/Dockerfile.runpod -t kitchensoup-runpod-trainer:local .

test-runpod-gpu: ## Opt-in paid FP16 Runpod test (set RUNPOD_TEST_ARGS; see docs/runpod-training.md)
	$(VENV_PYTHON) scripts/test_runpod_gpu.py $(RUNPOD_TEST_ARGS)

test-ssh-gpu: ## Optional remote FP16 training/serving smoke (set SSH_TEST_ARGS; see docs/ssh-execution.md)
	$(VENV_PYTHON) scripts/test_ssh_gpu.py $(SSH_TEST_ARGS)

.PHONY: test-kubernetes-gpu
test-kubernetes-gpu: ## Optional FP16 Kubernetes Job smoke (set KUBERNETES_TEST_ARGS; see docs/kubernetes-training.md)
	$(VENV_PYTHON) scripts/test_kubernetes_gpu.py $(KUBERNETES_TEST_ARGS)

.PHONY: test-kubernetes-vllm-gpu
test-kubernetes-vllm-gpu: ## Optional Kubernetes FP16 serving smoke (set KUBERNETES_SERVING_TEST_ARGS)
	$(VENV_PYTHON) scripts/test_vllm_gpu.py $(KUBERNETES_SERVING_TEST_ARGS)
