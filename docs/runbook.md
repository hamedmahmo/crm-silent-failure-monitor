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
| `make demo` | offline demo: generate day 1, simulate day 2, run the monitor on both, open `reports/latest.html` |
| `make hooks` | run every pre-commit hook on all files |

## Offline demo

```
make demo
```

This runs `uv run python -m monitor demo --out data/output --reports reports`, which:

1. generates Northwind with defects (seed 42) into `data/output/day1/`,
2. writes a simulated next day into `data/output/day2/` (`data/simulate_day2.py`),
3. deletes and recreates `data/output/monitor.duckdb`, then runs the monitor on day 1 and day 2,
4. writes `reports/day1.html`, `reports/day1.md`, `reports/day2.html`, `reports/day2.md`, and `reports/latest.html` and `reports/latest.md` (copies of day 2),
5. prints one summary and one score line per day, and opens `reports/latest.html` in the default browser. It doesn't open a browser when `--no-open` is passed or the `CI` environment variable is set.

Output of `make demo` (the same on every run, because the data is seeded):

```
day1 (as of 2026-06-30): 105 open issues in 18 checks: 105 new, 0 persisting, 0 resolved (no previous run)
  caught 105 of 105 planted records, 0 false positives
day2 (as of 2026-07-01): 88 open issues in 17 checks: 10 new, 78 persisting, 27 resolved
  caught 88 of 88 planted records, 0 false positives
```

Day 2 follows a fixed story: 22 issues fixed in place, 5 duplicate records merged (deleted), 10 new issues from a broken web form, a bad import and new records, and some normal edits (5 new contacts, 3 stage moves, 1 deal closed won) that must not be flagged. `data/output/day2/changes.json` lists every change; the tests compare the monitor's new and resolved findings against it.

## Running the monitor on one snapshot

```
uv run python -m monitor run --path data/output/day1 --label day1
uv run python -m monitor run --help
```

| Option | Default | Notes |
|---|---|---|
| `--source` | `csv` | only `csv` until the HubSpot source exists (milestone 5) |
| `--path` | required | folder with `owners.csv`, `companies.csv`, `contacts.csv`, `deals.csv`, `metadata.json` |
| `--db` | `data/output/monitor.duckdb` | runs accumulate here; each run is compared with the one before |
| `--label` | folder name | used in the report and its file name |
| `--reports` | `reports` | writes `<label>.html`, `<label>.md`, `latest.html`, `latest.md` |
| `--checks` | `checks` | folder of check YAML files |
| `--fresh` | off | delete the database first |

If the folder has a `defects_manifest.json`, the run is scored against it; otherwise the report says "not scored".

Exit codes: 0 when every check ran (findings are not failures), 1 when at least one check errored, 2 when the check YAML or the snapshot can't be read.

The HTML report is a single file with no external requests, so it can be opened from disk, attached to a ticket or published on GitHub Pages. The run timestamp is the only part that changes between two runs on the same data.

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

`.github/workflows/ci.yml` runs on pushes to `main` and on pull requests, on Ubuntu: `uv sync --locked`, `make lint`, `make test`, `make demo`, `make hooks`. `make demo` runs the whole offline pipeline there; GitHub sets `CI=true`, so it doesn't try to open a browser.

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
| `monitor run` exits with 1 | a check errored; the report lists it under "Checks that failed to run" with the DuckDB message |
| Report compares with an unexpected earlier run | runs accumulate in the database; pass `--fresh` or a different `--db` |
| `--scale must be between 0.5 and 4.0` | smaller runs can't fit every defect's fixed count; larger runs run out of unique company names |
