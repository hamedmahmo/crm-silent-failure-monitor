.DEFAULT_GOAL := help
.PHONY: help setup test lint format demo hooks

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

demo: ## Generate the Northwind dataset with defects into data/output
	uv run python data/generate.py --defects --out data/output

hooks: ## Run every pre-commit hook (including gitleaks) on all files
	uv run pre-commit run --all-files
