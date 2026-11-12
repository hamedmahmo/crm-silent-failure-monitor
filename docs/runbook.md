# Runbook

How to set up, run and troubleshoot this project. Keep it current: `/runbook` in Claude Code regenerates it from the Makefile and CLI help.

## Requirements

- [uv](https://docs.astral.sh/uv/) (tested with 0.13.7). It installs Python 3.12 for the project; the system Python version doesn't matter.
- GNU make (tested with 4.4.1). On Windows, one option is `winget install ezwinports.make`.
- git.

## First-time setup

```
make setup
```

This runs `uv sync` (creates `.venv` with Python 3.12, installs the runtime dependencies duckdb and pyyaml, and the dev tools pytest, ruff and pre-commit) and `uv run pre-commit install` (adds the git hook).

Check the interpreter: `uv run python --version` should print `Python 3.12.x`.

The first commit, or the first `make hooks`, builds the gitleaks hook from source. pre-commit downloads a Go toolchain for this, so it takes a few minutes once and is cached afterwards in `~/.cache/pre-commit` (Windows: `%USERPROFILE%\.cache\pre-commit`).

## Everyday commands

| Command | What it does |
|---|---|
| `make help` | list targets |
| `make test` | run pytest |
| `make lint` | ruff check and format check, no changes |
| `make format` | apply ruff formatting and safe fixes |
| `make demo` | generate the dataset with defects into `data/output/` |
| `make hooks` | run every pre-commit hook on all files |

## Generating data

```
uv run python data/generate.py --help
uv run python data/generate.py                       # clean data, seed 42, scale 1.0
uv run python data/generate.py --defects             # plant the 23 defects in data/DEFECTS.md
uv run python data/generate.py --seed 7 --scale 2.0 --out data/output/big
```

| Option | Default | Notes |
|---|---|---|
| `--seed` | 42 | same seed gives byte-identical files on Windows and Linux |
| `--scale` | 1.0 | 0.5 to 4.0; 1.0 means 200 companies |
| `--out` | `data/output` | created if missing |
| `--defects` | off | writes `defects_manifest.json` |

Output files: `owners.csv`, `companies.csv`, `contacts.csv`, `deals.csv`, `usage_events.csv`, `support_tickets.csv`, `metadata.json`, and `defects_manifest.json` when `--defects` is set. A clean run deletes any old manifest in the output folder.

All dates are anchored to `AS_OF` = 2026-06-30, not today's date, so output doesn't change over time.

## Checks

Checks live in `checks/companies.yaml`, `checks/contacts.yaml` and `checks/deals.yaml`. Business rules shared by the checks (attribution cutoff, stale-stage days, top list price, deal stage picklist) are in `checks/params.yaml`.

To add a check, add one entry to the right YAML file:

| Field | Rule |
|---|---|
| `id` | lower_snake_case, unique across all files |
| `title` | short name shown in the report |
| `severity` | `critical`, `high`, `medium` or `low` |
| `owner` | the team that fixes it, e.g. `revops` (never a person) |
| `record_type` | `owners`, `companies`, `contacts` or `deals` |
| `defects` | defect IDs from `data/DEFECTS.md` that the check targets, e.g. `[D19]` |
| `why_it_matters` | one line, at most 160 characters, on the business impact |
| `sample_columns` | columns of `record_type` shown in the report's sample table |
| `sql` | a query returning one column, `record_id` |

The SQL can use the views `owners`, `companies`, `contacts`, `deals` (the current run, every column as text, blanks as NULL), `run_context` (`as_of`, `source_required_from`, `stale_stage_days`, `max_deal_amount`) and `deal_stages` (`stage`, `is_open`, `is_closed`). Cast where needed, e.g. `TRY_CAST(amount AS DOUBLE)` or `TRY_CAST(left(created_at, 10) AS DATE)`.

Then run `make test`. `tests/test_checks.py` loads every entry, fails on invalid fields, and runs each check against generated data with and without defects.

## CI

`.github/workflows/ci.yml` runs on pushes to `main` and on pull requests, on Ubuntu: `uv sync --locked`, `make lint`, `make test`, `make demo`, `make hooks`.

`uv sync --locked` fails if `uv.lock` is out of date. Run `uv lock` locally and commit the lock file.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `make: command not found` in PowerShell | install GNU make (see Requirements) and open a new terminal |
| Python 3.14 (or other) used instead of 3.12 | run commands through `uv run`; check `.python-version` says `3.12` |
| gitleaks hook is slow the first time | expected: it builds gitleaks with Go once, then uses the cache |
| gitleaks blocks a commit | remove the secret from the file, keep it in `.env` instead, and re-stage. If it is a false positive, add the fingerprint to a `.gitleaksignore` file |
| `end-of-file-fixer` or `trailing-whitespace` changed files and the commit stopped | the hook fixed them; `git add` the files and commit again |
| `uv sync --locked` fails in CI | run `uv lock` locally and commit `uv.lock` |
| `--scale must be between 0.5 and 4.0` | smaller runs can't fit every defect's fixed count; larger runs run out of unique company names |
