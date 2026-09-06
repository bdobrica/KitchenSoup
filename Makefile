.DEFAULT_GOAL := help
PYTHON ?= python3.13
VENV := .venv
VENV_PYTHON := $(VENV)/bin/python

.PHONY: help setup fmt lint test test-unit verify dev

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
