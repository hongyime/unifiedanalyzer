# UnifiedAnalyzer — developer shortcuts
# One muscle-memory interface for common tasks.
#
# Usage:
#   make test        Fast local test run (no DB required; DB tests skip)
#   make test-db     Full integration run via docker-compose (live Postgres)
#   make lint        Ruff lint + optional type check (installs ruff if absent)
#   make schema      Apply the analyzer schema to the local/dev DB
#   make serve       Start the API server (port 8002, scheduler separate)
#   make help        Show this message

.DEFAULT_GOAL := help

# ── Config ──────────────────────────────────────────────────────────────────

PYTHON     ?= python
PYTEST     ?= $(PYTHON) -m pytest
COV_FLAGS  ?= --cov=src --cov-report=term-missing
TEST_DIR   ?= tests
SRC_DIR    ?= src

COMPOSE        ?= docker compose
COMPOSE_TEST   ?= $(COMPOSE) -f docker/docker-compose.test.yml
COMPOSE_PROD   ?= $(COMPOSE) -f docker/docker-compose.yml --env-file .env

# ── Targets ──────────────────────────────────────────────────────────────────

.PHONY: help test test-fast test-db test-db-down lint schema serve \
        build up down logs shell

help: ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*##' $(MAKEFILE_LIST) | \
	  awk 'BEGIN {FS = ":.*##"}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

# ── Testing ──────────────────────────────────────────────────────────────────

test: ## Fast local run — DB tests skip when ANALYZER_DATABASE_URL is unset
	$(PYTEST) $(TEST_DIR)/ -q $(COV_FLAGS)

test-fast: ## Same as test but skip coverage (faster feedback loop)
	$(PYTEST) $(TEST_DIR)/ -q

test-db: ## Full integration run: spins up a clean Postgres, applies schema, runs all tests
	$(COMPOSE_TEST) up --build --exit-code-from test-runner

test-db-down: ## Tear down the test docker-compose stack and remove volumes
	$(COMPOSE_TEST) down -v

# ── Lint ─────────────────────────────────────────────────────────────────────

lint: ## Ruff lint + isort check (installs ruff/isort if absent)
	@$(PYTHON) -m ruff --version >/dev/null 2>&1 || \
	  { echo "Installing ruff..."; $(PYTHON) -m pip install -q ruff; }
	$(PYTHON) -m ruff check $(SRC_DIR)/ $(TEST_DIR)/
	@echo "Lint passed."

lint-fix: ## Auto-fix ruff issues where safe
	@$(PYTHON) -m ruff --version >/dev/null 2>&1 || \
	  { echo "Installing ruff..."; $(PYTHON) -m pip install -q ruff; }
	$(PYTHON) -m ruff check --fix $(SRC_DIR)/ $(TEST_DIR)/

# ── Schema ───────────────────────────────────────────────────────────────────

schema: ## Apply the analyzer DB schema (idempotent)
	$(PYTHON) -m src.main schema

# ── Docker (production stack) ────────────────────────────────────────────────

build: ## Build all Docker images
	$(COMPOSE_PROD) build

up: ## Start all services in detached mode
	$(COMPOSE_PROD) up -d

down: ## Stop all services
	$(COMPOSE_PROD) down

logs: ## Tail logs from all services
	$(COMPOSE_PROD) logs -f

shell: ## Open a shell in the analyzer container
	$(COMPOSE_PROD) exec analyzer /bin/bash

# ── Dev server ───────────────────────────────────────────────────────────────

serve: ## Run the API server locally (no scheduler)
	RUN_SCHEDULER=0 $(PYTHON) -m src.main serve
