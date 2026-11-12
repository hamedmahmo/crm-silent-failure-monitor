# CLAUDE.md

Instructions for Claude Code in this repository.

## Purpose

CRM Silent-Failure Monitor: a scheduled job that snapshots CRM data (owners, companies, contacts, deals) into DuckDB, runs SQL checks defined in `checks/*.yaml` for data problems that break reporting without raising errors, and reports what is new, resolved or unchanged since the last run. RevOps and sales-ops teams would use the report to fix records before they distort pipeline and revenue numbers. It runs offline on the Northwind generator output (`make demo`) and, in later milestones, against a HubSpot developer test account.

Scope: the defects in `data/DEFECTS.md` on owners, companies, contacts and deals. Usage events and support tickets (D05, D11-D15) are out of scope. The plan is in `docs/plan.md`.

This repository started from `portfolio-template`, a template for small Python data and automation projects. Each project is a short case study: a realistic problem, synthetic data, working code, tests, and a README that explains the result.

## Stack

- Python 3.12, pinned in `.python-version` and `pyproject.toml` (`requires-python = ">=3.12,<3.13"`). The machine default may be newer; always go through `uv`.
- uv for environments, dependencies and running commands. Never call `pip` or a bare `python`.
- pytest for tests, ruff for linting and formatting.
- pre-commit with ruff and gitleaks.
- GitHub Actions on Ubuntu runs the same Make targets as local development.
- Development happens on Windows 11 with PowerShell and GNU make. CI runs on Linux.

## Commands

| Command | What it does |
|---|---|
| `make setup` | `uv sync` and install the pre-commit hook |
| `make test` | `uv run pytest` |
| `make lint` | `ruff check` and `ruff format --check` |
| `make format` | apply ruff formatting and safe fixes |
| `make demo` | generate the Northwind dataset with defects into `data/output/` |
| `make hooks` | run all pre-commit hooks, including gitleaks, on every file |

Run a single test with `uv run pytest tests/test_generate.py -k d05`.

Makefile rule: every recipe is a plain `uv ...` or `uv run ...` command. No `rm -rf`, `export`, `source`, pipes or shell quoting, because the same Makefile runs under `cmd.exe` or Git's `sh` on Windows and under `sh` on Linux. If a target needs logic, write a Python script in `scripts/` and call it with `uv run python`.

## Data rules

- Synthetic or public data only. The default source is `data/generate.py` (Northwind Analytics, a fictional B2B SaaS company).
- No healthcare examples or industries.
- No real people, email addresses or employer data. Generated emails and domains use the reserved `.example` top-level domain.
- Secrets live only in `.env` (gitignored). Never print, read or commit them. List new variable names, with blank values, in `.env.example`.
- Generated files go in `data/output/`, which is gitignored. Don't commit generated data.
- Known data-quality defects are documented in `data/DEFECTS.md`. If you add or change a defect, update the registry in `data/generate.py`, `DEFECTS.md` and the tests together; `test_defects_md_matches_registry` fails if they drift apart.

## Conventions

- Write the test first, watch it fail, then write the code.
- Keep the generator stdlib-only unless `docs/decisions.md` records a reason not to.
- Seeded randomness only (`random.Random(seed)`); never use the global `random` state or today's date in generated data.
- Conventional commit messages: `feat:`, `fix:`, `docs:`, `test:`, `chore:`, `refactor:`.
- Plain, specific prose in docs and READMEs. Follow Wikipedia's "Signs of AI writing" guidance: no puffery, no filler summaries, no invented results.
- Record decisions that someone would later ask "why?" about in `docs/decisions.md`.
- Plans go in `docs/plan.md` and are reviewed before code is written.

## Definition of done

A task is done when all of these hold:

- [ ] Tests covering the change were written first and pass: `make test`.
- [ ] `make lint` passes.
- [ ] `make hooks` passes (ruff and gitleaks).
- [ ] `docs/runbook.md` is updated if commands, setup or outputs changed.
- [ ] `docs/decisions.md` has an entry for any non-obvious choice.
- [ ] README results come from a real run, not estimates.
- [ ] `make demo` catches every in-scope planted record with 0 false positives on day 1 and day 2, and clean data gives zero findings.
- [ ] No HubSpot, Claude or Slack call runs in `make test`; network clients are tested with mocked transports.
