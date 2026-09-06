.DEFAULT_GOAL := help
PYTHON ?= python3.13
VENV := .venv
VENV_PYTHON := $(VENV)/bin/python
COMPOSE ?= docker compose

.PHONY: help setup fmt lint test test-unit verify dev
.PHONY: local-env up down restart logs ps clean shell db-shell compose-check check-dependencies

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

up: local-env ## Build and start the stack, waiting for healthy services
	$(COMPOSE) up --build --detach --wait --wait-timeout 180

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
