.DEFAULT_GOAL := help
.PHONY: help setup test lint format demo hooks hubspot-check seed-hubspot reset-hubspot

help: ## List the available targets
	uv run python scripts/make_help.py

setup: ## Install Python 3.12, dev dependencies and the git pre-commit hook
	uv sync
	uv run pre-commit install

test: ## Run the test suite
	uv run pytest

lint: ## Check code style with ruff (no changes made)
	uv run ruff check .
	uv run ruff format --check .

format: ## Fix formatting and safe lint issues with ruff
	uv run ruff format .
	uv run ruff check --fix .

demo: ## Offline demo: generate day 1, simulate day 2, run the monitor on both, open reports/latest.html
	uv run python -m monitor demo --out data/output --reports reports

hooks: ## Run every pre-commit hook (including gitleaks) on all files
	uv run pre-commit run --all-files

hubspot-check: ## Read-only: confirm the HubSpot token belongs to the developer test account in .env
	uv run python -m monitor hubspot check

seed-hubspot: ## Load Northwind day 1 into the HubSpot developer test account (idempotent)
	uv run python -m monitor hubspot seed

reset-hubspot: ## Dry run: count the records tagged northwind_id that a reset would archive
	uv run python -m monitor hubspot reset
