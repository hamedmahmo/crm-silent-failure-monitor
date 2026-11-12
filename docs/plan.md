# Plan: portfolio-template repo + Northwind Analytics synthetic data

## Context

`C:\Users\hmahm\Desktop\Portfolio\portfolio-template` is an empty git repo (branch `master`, no commits, remote `origin` = github.com/hamedmahmo/portfolio-template). The goal is a GitHub template repo for small Python data/automation portfolio projects, plus a seeded synthetic dataset for a fictional B2B SaaS company ("Northwind Analytics") with 23 documented, plantable data-quality defects. A later project, the CRM Silent-Failure Monitor, will be tested against those defects, partly by planting them in a HubSpot test portal, so each defect records whether HubSpot would accept it on create.

Machine facts (checked): uv 0.13.7, GNU Make 4.4.1, Python 3.12.16 already installed under uv, default python 3.14.7, `pre-commit` installed globally, no gitleaks binary, no Go, no Docker.

User decisions: gitleaks via the official golang pre-commit hook (pre-commit bootstraps Go). When done: rename `master` -> `main`, commit, push, and mark the repo as a GitHub template.

This plan was reviewed and approved before any code was written.

## File layout

```
.python-version            3.12
pyproject.toml             requires-python ">=3.12,<3.13"; dev deps pytest, ruff, pre-commit; ruff + pytest config
uv.lock
Makefile
scripts/make_help.py       prints Makefile targets (avoids shell quoting in `make help`)
.pre-commit-config.yaml
.gitignore / .gitattributes / .env.example
.github/workflows/ci.yml
CLAUDE.md                  skeleton: purpose, stack, commands, data rules, conventions, definition of done
README.md                  case-study template
docs/plan.md, docs/runbook.md, docs/decisions.md
.claude/commands/milestone.md, case-study.md, runbook.md
data/generate.py           stdlib-only generator
data/DEFECTS.md
data/output/               gitignored
tests/test_generate.py
```

## Tooling details

- **uv / Python**: `.python-version` = `3.12`; `requires-python = ">=3.12,<3.13"` so uv never picks 3.14. Dev deps added with `uv add --dev pytest ruff pre-commit`, so `uv run pre-commit` uses the project's pinned version. No runtime deps (generator is stdlib only).
- **ruff** (in pyproject): `target-version = "py312"`, `line-length = 100`, `select = ["E","F","I","B","UP","SIM"]`, plus `ruff format`.
- **pytest** (in pyproject): `testpaths = ["tests"]`, `pythonpath = ["data"]` so tests can `import generate`.
- **Makefile**: every recipe is a plain `uv ...` / `uv run ...` command with no quotes, pipes, `rm`, `export` or `source`, so it runs the same under `sh` (Linux, or Git's sh on Windows) and `cmd.exe`. Targets:
  - `help` (default) — `uv run python scripts/make_help.py`
  - `setup` — `uv sync` then `uv run pre-commit install`
  - `test` — `uv run pytest`
  - `lint` — `uv run ruff check .` and `uv run ruff format --check .`
  - `format` — `uv run ruff format .` and `uv run ruff check --fix .`
  - `demo` — `uv run python data/generate.py --defects --out data/output` (prints row counts and per-defect counts)
  - `hooks` — `uv run pre-commit run --all-files`
- **.pre-commit-config.yaml**: `pre-commit-hooks` (trailing-whitespace, end-of-file-fixer, check-yaml, check-toml, check-added-large-files), `ruff-pre-commit` (ruff, ruff-format), `gitleaks/gitleaks` hook id `gitleaks` (golang). Revs pinned with `uv run pre-commit autoupdate`; ruff rev kept in line with the locked ruff version.
- **.gitattributes**: `* text=auto eol=lf` so Windows checkouts don't fight the hooks.
- **.gitignore**: `.venv/`, `__pycache__/`, `.pytest_cache/`, `.ruff_cache/`, `data/output/`, `.env`. `.env.example` holds placeholder names only (e.g. `HUBSPOT_ACCESS_TOKEN=`).
- **CI** (`.github/workflows/ci.yml`, ubuntu-latest, on push + PR): checkout (fetch-depth 0 for gitleaks), `astral-sh/setup-uv` with caching, `uv sync --locked`, `make lint`, `make test`, `make demo`, then `uv run pre-commit run --all-files` (Go is preinstalled on Ubuntu runners).

## Docs and Claude commands

- **CLAUDE.md**: the six requested sections, filled in for this template, with `TODO:` markers in Purpose and Definition of done for each new project. Data rules: synthetic or public data only, no healthcare, no real names/emails/employers, `.example` domains for emails, secrets only in `.env`. Definition of done: tests written first, `make lint` and `make test` pass, runbook/decisions updated.
- **README.md** (case-study template, short instructions under each heading):
  1. Title + one-line summary
  2. **Demo** — `![Demo](docs/demo.gif)` placeholder with a note on what the GIF should show
  3. Problem
  4. Data (points to `data/DEFECTS.md` and `make demo`)
  5. **Architecture** — Mermaid `flowchart LR` placeholder (source -> checks -> alerts) to replace per project
  6. Approach
  7. Results (no invented numbers; fill from real runs)
  8. How to run
  9. **How I built this** — two lists: "What I decided" (scope, rules, trade-offs) and "What Claude Code did" (scaffolding, tests, refactors), plus a line on how its output was reviewed
  10. Decisions (link to `docs/decisions.md`), Limitations, Using this template
- **docs/runbook.md**: setup, commands, regenerate data, troubleshooting (first gitleaks run builds Go; Windows shell notes).
- **docs/decisions.md**: ADR-lite format (date, decision, context, alternatives, consequences), seeded with real entries: uv + Python 3.12 pin, stdlib generator, golang gitleaks hook, `.example` email domains, HubSpot plantability flag.
- **.claude/commands/** (markdown prompts using `$ARGUMENTS`):
  - `milestone.md` — run `make lint` and `make test`, summarise what changed, add a `docs/decisions.md` entry if a decision was made, propose a conventional commit message.
  - `case-study.md` — fill or refresh README sections (including Demo, Architecture, How I built this) from the code, decisions log and `make demo` output; no invented results.
  - `runbook.md` — regenerate `docs/runbook.md` from the Makefile, CLI `--help` and config.
- All prose follows Wikipedia's "Signs of AI writing" guidance: plain, specific, no puffery.

## data/generate.py

CLI: `--seed` (default 42), `--out` (default `data/output`), `--defects`, `--scale` (default 1.0, minimum enforced so every defect's fixed count fits). Constants: `AS_OF = 2026-06-30` anchors all dates; `SOURCE_REQUIRED_FROM = 2026-01-01` (attribution cutoff); `STALE_STAGE_DAYS = 60`. Writes CSVs with `lineterminator="\n"` (byte-identical on Windows and Linux) plus `metadata.json` (seed, as_of, constants, row counts, defects on/off).

**Tables and fields** (approx rows at scale 1). Picklists use HubSpot internal values so rows map onto HubSpot properties.
- `owners` (12): `owner_id, name, active`
- `companies` (200): `company_id, name, domain, industry, country, employee_band, plan_tier, owner_id, created_at, churned_at`
- `contacts` (~1,000): `contact_id, company_id, first_name, last_name, email, job_title, lifecycle_stage, owner_id, source, created_at`
  - `lifecycle_stage`: subscriber, lead, marketingqualifiedlead, salesqualifiedlead, opportunity, customer
  - `source`: HubSpot original-source values (ORGANIC_SEARCH, PAID_SEARCH, EMAIL_MARKETING, SOCIAL_MEDIA, REFERRALS, DIRECT_TRAFFIC, OFFLINE). Contacts created before the cutoff may legitimately have a blank source (a realistic false-positive trap for the monitor).
- `deals` (~600): `deal_id, company_id (blank allowed), primary_contact_id, owner_id, stage, amount, currency, created_at, stage_entered_at, close_date, closed_at`
  - `stage`: appointmentscheduled, qualifiedtobuy, presentationscheduled, decisionmakerboughtin, contractsent, closedwon, closedlost
  - Clean rules: closedwon has amount; open deals have `close_date >= AS_OF` and `stage_entered_at` within 60 days; contractsent has amount; early-stage open deals may have blank amount (another false-positive trap).
- `usage_events` (~20,000): `event_id, company_id, contact_id, event_type, feature, quantity, event_ts`
- `support_tickets` (~1,500): `ticket_id, company_id, contact_id, priority, status, category, opened_at, first_response_at, closed_at`

Names come from small built-in word pools; emails and company domains use the reserved `.example` TLD; industries exclude healthcare.

**Defect registry**: `DEFECTS` is a list of `Defect(id, title, record_types, count, unit, hubspot_plantable, plant_fn)`. `generate_clean(rng, scale)` builds the tables, then `plant_defects(tables, seed)` runs each defect with its own RNG (`random.Random(f"{seed}:{defect_id}")`), choosing from an eligible pool that excludes records touched by earlier defects. Each defect affects exactly `count` records (or entities, per `unit`) regardless of seed and scale. Planting must not trip any other defect's detection rule (e.g. D08 only targets closed deals so it never triggers D20; D21 keeps `close_date >= AS_OF`); tests enforce this. With `--defects`, `defects_manifest.json` lists per defect: id, title, record_types, hubspot_plantable, count, and the affected record IDs (plus window dates for D13).

## data/DEFECTS.md — 23 defects

Table columns: ID, what's wrong, record types, count, hubspot_plantable, reason (when "no"). Below the table, one detection rule per defect and how it appears in the manifest.

| ID | What's wrong | Record types | Count | HubSpot plantable |
|---|---|---|---|---|
| D01 | Duplicate contact: same email differing only by case/whitespace | contacts | 8 contacts | no — duplicate email |
| D02 | Orphan contact: `company_id` not in companies | contacts | 5 contacts | no — unknown company ID |
| D03 | Malformed email (missing `@`, `@@`, trailing space) | contacts | 6 contacts | no — malformed email |
| D04 | Required company fields blank (`industry`, `country`) | companies | 6 companies | yes |
| D05 | Churned company still emits usage events after `churned_at` | companies, usage_events | 3 companies (4 events each) | no — usage events are not HubSpot records |
| D06 | `closedwon` deal with null or zero amount | deals | 5 deals | yes |
| D07 | Deal amount 100x too large (cents in a dollars field) | deals | 4 deals | yes |
| D08 | Closed deal with `close_date` before `created_at` | deals | 5 deals | yes (close date set before the real create time) |
| D09 | Stage outside the picklist (`Closed Won`, `won`, trailing space) | deals | 6 deals | no — invalid stage |
| D10 | Deal owner is inactive or not in `owners` | deals | 5 deals | no — HubSpot validates owner IDs (my judgment; to confirm in sandbox) |
| D11 | Duplicate usage event (same `event_id` replayed) | usage_events | 10 events | no — not a HubSpot record |
| D12 | Usage event timestamp after AS_OF | usage_events | 8 events | no — not a HubSpot record |
| D13 | Ingestion gap: all events dropped for 3 consecutive days | usage_events | 2 companies | no — not a HubSpot record |
| D14 | Ticket `closed_at` before `opened_at` | support_tickets | 5 tickets | yes (closed date set before the real create time) |
| D15 | Ticket contact belongs to a different company than the ticket | support_tickets, contacts | 6 tickets | yes |
| D16 | Contact missing `lifecycle_stage` | contacts | 10 contacts | yes |
| D17 | Contact or deal with no owner | contacts, deals | 4 contacts + 4 deals | yes |
| D18 | Two companies share the same domain | companies | 3 pairs | yes |
| D19 | Deal with no associated company (`company_id` blank) | deals | 5 deals | yes |
| D20 | Open deal with `close_date` before AS_OF | deals | 8 deals | yes |
| D21 | Open deal in the same stage for more than 60 days (`stage_entered_at`) | deals | 6 deals | yes (see note) |
| D22 | Contact created on/after `SOURCE_REQUIRED_FROM` with blank `source` | contacts | 10 contacts | yes |
| D23 | Open deal at `contractsent` with no amount | deals | 5 deals | yes |

Note for D21 (written into DEFECTS.md): HubSpot's own `hs_date_entered_<stage>` properties are read-only, so in a live portal this defect needs a custom `stage_entered_at` property, or a deal left in a stage long enough to age past 60 days. Plantability flags for D08, D10 and D14 go beyond the five rejection rules you listed; DEFECTS.md marks them as "to confirm in a HubSpot sandbox".

## Tests first (tests/test_generate.py)

Written and run (failing) before `generate.py` exists:
1. Same seed gives byte-identical files; different seed gives different files.
2. Clean output: unique primary keys; foreign keys resolve; emails valid, unique (case-insensitive) and on `.example`; dates <= AS_OF; stages and lifecycle stages in picklists; and all 23 detection checks find zero rows.
3. `--defects` output: manifest has exactly D01–D23; each defect's affected count equals its registry `count`; an independent checker per defect (written in the test file, not reusing planting code) finds exactly the manifest's record IDs, so defects can't overlap or leak into each other.
4. `data/DEFECTS.md` and the registry agree on IDs, counts and `hubspot_plantable` (parsed from the table).
5. Counts are the same for two different seeds and two scales.
6. CLI smoke test via `subprocess` into `tmp_path` (with and without `--defects`).

## Verification on this machine

1. `make setup` — creates `.venv` with 3.12 (`uv run python --version` shows 3.12.x) and installs the git hook.
2. `make lint`, `make test`, `make demo` all pass/run under PowerShell.
3. `make hooks` — all hooks pass, including gitleaks (first run bootstraps Go; may take a few minutes).
4. Negative gitleaks check: clone the repo into the scratchpad, add a file with a fake key, run `uv run pre-commit run gitleaks --all-files` there and confirm it fails. Nothing touches the real repo.
5. Report exact command output; if any step fails, say so instead of claiming done.

## Git and GitHub (after verification passes)

1. `git branch -m master main`
2. One commit: `feat: add portfolio template with Northwind synthetic data generator` (ends with the Co-Authored-By line); the pre-commit hooks run for real on this commit.
3. `git push -u origin main`
4. `gh repo edit --template` (check `gh auth status` first; if `gh` isn't logged in, I'll stop and ask you to run `! gh auth login`).
5. Push CI runs; I'll check it with `gh run watch` and report the result.
