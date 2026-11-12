# Plan: CRM Silent-Failure Monitor

## Context

The repo was created from `portfolio-template` and has one commit. It contains the Northwind generator (`data/generate.py`), the defect registry and `data/DEFECTS.md` (23 defects), the template tests, docs and Make targets. No monitor code exists yet. `docs/plan.md` still holds the template's own plan, and `.env.example` is missing even though `.gitignore` expects it.

Goal: a scheduled job that snapshots CRM data (owners, companies, contacts, deals) into DuckDB, runs YAML-defined SQL checks for problems that break reporting without raising errors, compares each run with the previous one (new, resolved, unchanged), scores the findings against `defects_manifest.json`, and writes a markdown and HTML report. Milestones 1-3 run fully offline with `make demo`. Milestones 4-8 (HubSpot, digest, schedule, docs) are planned here and built later.

This plan was reviewed and approved on 2026-11-11 before any monitor code was written. It replaces the template's plan, which is still in git history.

**Scope.** In scope: defects whose `record_types` only include owners, companies, contacts or deals. That gives 17 defects: D01-D04, D06-D10, D16-D23. Out of scope: D05 (needs usage_events), D11-D13 (usage_events), D14-D15 (support_tickets). By registry counts the 17 defects plant 105 records. The scope rule lives in code (`SCOPE_TABLES`) and is derived from the manifest, not a hard-coded ID list.

**Git.** Per milestone: tests first, implement, run `/milestone`, then commit with a conventional message and the Co-Authored-By line. The repo is on `main`, so I will create a branch `feat/offline-monitor` and commit M1-M3 there. No push.

## Layout

```
monitor/                  package at repo root, run with `uv run python -m monitor`
  __main__.py, cli.py     argparse: `run`, `demo` subcommands
  snapshot.py             Snapshot dataclass, CSV reader, store into DuckDB, current-run views
  checks.py               YAML loader/validator, check engine, findings + check_runs tables
  scoring.py              compare findings with the manifest
  changes.py              findings diff and record diff against the previous run
  report.py               report model (dataclasses) + Jinja2 rendering, severity palette tokens
  templates/report.html.j2, report.md.j2
checks/
  params.yaml             business rules: stage picklist (open/closed), source cutoff, stale days, max amount
  companies.yaml, contacts.yaml, deals.yaml
data/simulate_day2.py     stdlib only; builds the day-2 snapshot from day-1 output
tests/conftest.py         session fixtures: clean and defect data from generate.build()
tests/test_snapshot.py, test_checks.py, test_scoring.py, test_simulate.py, test_changes.py, test_report.py, test_demo.py
```

`pyproject.toml`: name `crm-silent-failure-monitor`, new description, runtime deps `duckdb` and `pyyaml` via `uv add` (M1), `jinja2` (M3) (httpx and python-dotenv come in M4-M5 when code needs them). pytest `pythonpath = ["data", "."]`; ruff `known-first-party = ["generate", "simulate_day2", "monitor"]`. The generator and simulator stay stdlib-only.

## Milestone 1: setup and offline snapshot loader

Setup changes: CLAUDE.md purpose and project-specific done check, README title and one-line summary, pyproject name, `.env.example` with blank `HUBSPOT_ACCESS_TOKEN=`, `ANTHROPIC_API_KEY=`, `SLACK_WEBHOOK_URL=`.

Design:
- `Snapshot(source, source_ref, as_of, tables: dict[str, list[dict[str, str]]])`. The CSV reader builds it from a generator output folder (`as_of` from `metadata.json`). The HubSpot reader in M5 will build the same object, so storage and checks don't care where data came from.
- DuckDB file (default `data/output/monitor.duckdb`). Tables:
  - `runs(run_id, label, source, source_ref, as_of, loaded_at)`
  - `snap_owners`, `snap_companies`, `snap_contacts`, `snap_deals`: `run_id` plus every source column as VARCHAR.
- Raw values are stored as text, exactly as read. Only an empty string becomes NULL. Whitespace is kept, because `closedwon ` (D09) and a trailing-space email (D03) are defects. Checks cast where they need numbers or dates (`TRY_CAST(amount AS DOUBLE)`, `CAST(left(created_at, 10) AS DATE)`), which also works for HubSpot's ISO timestamps with milliseconds. Decision entry.
- Loading uses Python's `csv` module and `executemany` into explicitly typed tables, so DuckDB's CSV sniffer can't trim or retype values.
- `use_run(con, run_id)` creates temp views `owners`, `companies`, `contacts`, `deals` (that run's rows, without `run_id`), `run_context` (one row: `as_of`, `source_required_from`, `stale_stage_days`, `max_deal_amount`) and `deal_stages(stage, is_open, is_closed)`. Check SQL only refers to these names. Thresholds and the stage picklist come from `checks/params.yaml`; `as_of` comes from the snapshot.

Tests first (`tests/test_snapshot.py`):
- row counts per table equal the CSV row counts; usage_events and support_tickets are not loaded
- blank becomes NULL; `closedwon ` and trailing-space emails survive unchanged
- run ids increase; `runs` has label, source and `as_of` from `metadata.json`
- two snapshots in one DB stay separate; `use_run` shows only the selected run
- missing CSV or `metadata.json` raises a clear error
- `checks/params.yaml` matches `generate.SOURCE_REQUIRED_FROM`, `STALE_STAGE_DAYS`, `MAX_DEAL_AMOUNT`, `OPEN_STAGES`, `CLOSED_STAGES`

## Milestone 2: check engine, YAML checks, scoring

YAML entry (each file holds a list; adding a check means adding one entry):

```yaml
- id: deal_closedwon_no_amount
  title: Closed-won deal with blank or zero amount
  severity: critical            # critical | high | medium | low
  owner: revops                 # team, never a person
  record_type: deals            # owners | companies | contacts | deals
  defects: [D06]
  why_it_matters: Closed-won deals with no amount are missing from bookings and revenue reports.
  sample_columns: [deal_id, stage, amount, owner_id, close_date]
  sql: |
    SELECT deal_id AS record_id
    FROM deals
    WHERE stage = 'closedwon'
      AND (amount IS NULL OR TRY_CAST(amount AS DOUBLE) = 0)
```

Validation: required fields present, unique ids, known severity and record type, defect IDs match `D\d\d`, `why_it_matters` is one line of at most 160 characters describing the business impact (for example "Deals without a company drop out of account-level revenue reports"), `sample_columns` exist in the record type's table, SQL returns exactly one column named `record_id`.

18 checks, one per in-scope defect, except D17, which is split into `contact_no_owner` and `deal_no_owner` so each check returns one record type:
- companies: missing industry/country (D04), duplicate domain (D18)
- contacts: duplicate email by `lower(trim(email))` (D01), orphan company (D02), malformed email (D03), missing lifecycle stage (D16), no owner (D17), blank source on or after the cutoff (D22)
- deals: closedwon without amount (D06), amount above max (D07), closed with close_date before created_at (D08), stage not in picklist (D09), inactive or unknown owner (D10), no owner (D17), no company (D19), open with past close_date (D20), stage older than 60 days (D21), contractsent without amount (D23)

The SQL follows the detection rules in `data/DEFECTS.md`. Severity and owner team are my first proposal; you can change them in the YAML.

Engine: each check runs in its own try block. Results go to `findings(run_id, check_id, record_type, record_id)` and `check_runs(run_id, check_id, status, error, finding_count)`. A failing check is recorded as an error and shown at the top of the report. The other checks still run, and `monitor run` exits 1 at the end. A monitor for silent failures shouldn't fail silently itself.

Scoring (`scoring.py`):
- Planted set per check = manifest `record_ids` of the check's defects, limited to IDs that exist in the check's `record_type` table. That limit is what lets the two D17 checks be scored separately.
- Per check: caught = found ∩ planted, missed = planted − found, false positives = found − planted.
- Totals over in-scope defects. An in-scope defect with no check counts as missed and is listed as "no check".
- Summary line: `caught X of Y planted records, Z false positives`.
- No manifest (clean data, or a live portal later): scoring is skipped and the report says so.

Tests first:
- `test_checks.py`: loader accepts the real `checks/*.yaml` and rejects missing fields, duplicate ids, bad severity, unknown record type, malformed defect ids, a missing or multi-line `why_it_matters`, unknown `sample_columns` and SQL with the wrong column; every in-scope defect has at least one check and no check targets an out-of-scope defect; parametrized per check on defect data: findings equal the planted set; on clean data every check returns nothing; explicit trap test that the clean data contains pre-cutoff blank-source contacts and early-stage blank-amount deals and none are flagged; a broken SQL check is recorded as an error while the rest run; a new entry in a temporary checks folder is picked up and run.
- `test_scoring.py`: small hand-made inputs for caught, missed and false positives, the record-type limit for D17, out-of-scope defects excluded, the summary string format.

## Milestone 3: change detection, day-2 simulation, reports, `make demo`

Day 2 (`data/simulate_day2.py --day1 DIR --out DIR --seed 42`). It reads the day-1 CSVs and manifest, applies seeded changes (`random.Random(f"{seed}:day2")`), and writes day-2 CSVs, `metadata.json` with `as_of` 2026-07-01, a day-2 `defects_manifest.json` in the same format, and `changes.json` (fixed, deleted, new and modified IDs) as ground truth. The story: one day of RevOps clean-up plus a broken integration.
- Fixed in place: 4 D03 emails (the `@@` and trailing-space ones; missing-`@` ones stay), 3 D04 companies get industry and country, 3 D06 deals get an amount, 4 D09 stages normalized to `closedwon`, the 4 D17 deals get an active owner, 4 D20 deals get a later close date.
- Deleted (merged): 2 of 3 D18 duplicate companies, 3 of 8 D01 duplicate contacts. The report marks these "resolved (deleted)" rather than "resolved (fixed)".
- New: a web-form integration creates 3 contacts with blank source (D22) and 2 with blank lifecycle stage (D16); a bad import multiplies 2 deal amounts by 100 (D07); 2 new deals at contractsent without amount (D23); 1 new company with blank industry (D04).
- Benign changes that must not be flagged: 5 new clean contacts, 3 open deals moved to a later stage with valid amounts, 1 deal closed won with an amount.
- Advancing `as_of` by one day creates no aging findings: clean open deals close on or after 2026-07-01 and entered their stage on or after 2026-05-02. The day-2 zero-false-positive test checks this.
- Everything else persists.

Change detection (`changes.py`): the previous run is the latest earlier `run_id`. Findings diff on `(check_id, record_id)`: new, unchanged, resolved; a resolved record is `deleted` when its ID is missing from the current snapshot, otherwise `fixed`. Record diff per table: added, removed and changed counts (changed = same ID, different row hash). On the first run everything is new and the report says there was no previous run.

Report (`report.py` + Jinja2 templates). One report model built from the DB (dataclasses, no SQL in templates), rendered by `report.html.j2` and `report.md.j2`. The Jinja environment uses autoescaping for HTML. The HTML should read like a small internal product page, not a table dump. I'll load the `dataviz` skill before writing the summary tiles and severity colors.

Check status, per check, comparing this run with the previous one:
- NEW: at least one finding that wasn't there last run (the card also shows how many persist).
- PERSISTING: open findings, none of them new.
- RESOLVED: no findings now, some last run (samples come from the previous snapshot).
- Checks with no findings in either run are not cards; they appear in a collapsed "Passing checks" list. Checks that errored get a separate card at the top.

HTML page:
- One self-contained file: inline CSS, a small inline vanilla JS block, system font stack, no CDN, no external fonts, no network requests, so it works offline and on GitHub Pages.
- Header: project name, run timestamp (UTC, when the run was loaded), `as_of` date, data source ("Offline demo (CSV)" or "HubSpot"), previous run compared against.
- Summary tiles: open issues, new since last run, resolved since last run, records affected (distinct record IDs with open findings), and score ("caught X of Y, Z false positives", or "not scored" without a manifest).
- Filters above the cards: severity, owner team and status, as `<select>` elements that toggle cards by `data-` attributes, with a count of visible cards. Without JS every card stays visible.
- One card per check, sorted by severity (critical, high, medium, low), then NEW before PERSISTING before RESOLVED, then by count. Each card: severity badge, status pill, owner team, the `why_it_matters` line, open count (with new and resolved counts), and a `<details>` table of up to 10 sample records using the check's `sample_columns`, new records first, each row marked new or persisting.
- Below the cards: record diff per table (added, removed, changed) and the per-check scoring table when a manifest exists.
- Style: generous spacing, rounded cards with subtle borders, one accent color, four severity colors. Colors are CSS custom properties on `:root`, redefined under `prefers-color-scheme: dark`. Badge text and background pairs meet WCAG AA (4.5:1) in both schemes; the palette lives in `report.py` so a test can compute the ratios.
- Phone: viewport meta, single column below about 640px, sample tables scroll sideways inside their card instead of widening the page.
- Print: filters hidden, light colors, cards don't split across pages, all `<details>` opened on `beforeprint`.

Markdown report: same model and ordering in plain sections (summary, cards as headings with status and counts, sample IDs, record diff, scoring), for terminals and PR comments.

Run timestamp and repeatability: the timestamp is real, so reports differ between runs only on that line. Rendering takes the timestamp as an argument, which lets tests pass a fixed value.

CLI and Make:
- `uv run python -m monitor run --source csv --path DIR --db FILE --label NAME --reports DIR [--fresh]` loads the snapshot, runs the checks, diffs against the previous run, scores against the manifest if there is one, writes `<reports>/<label>.md` and `.html` plus `latest.md` and `latest.html`, and prints the summary.
- `uv run python -m monitor demo --out data/output --reports reports [--no-open]` deletes and recreates the demo DB, generates day 1 (`generate.build(seed=42, defects=True)` into `data/output/day1`), simulates day 2, runs both, and prints e.g. `day1: caught X of Y planted records, Z false positives` and `day2: ... new A, resolved B, unchanged C`. `reports/latest.html` is the day-2 report, so it shows NEW, PERSISTING and RESOLVED cards. It then opens `reports/latest.html` with Python's `webbrowser`, unless `--no-open` is given or the `CI` environment variable is set. Numbers in the README will come from the real output.
- `make demo` becomes `uv run python -m monitor demo --out data/output --reports reports`, a single plain `uv run` line as the Makefile rule requires. CI already runs `make demo`; `CI=true` on GitHub runners keeps it from trying to open a browser.
- `reports/` is added to `.gitignore`. Generated reports aren't committed; M7 publishes them.

Tests first:
- `test_simulate.py`: same input and seed give identical bytes; `as_of` is 2026-07-01; day-2 manifest = day-1 − fixed − deleted + new for every defect; each fix changed the field it targets; new records exist with 2026-07-01 creation dates.
- `test_changes.py`: hand-made two-run DB gives the expected new, unchanged, resolved (fixed vs deleted) and record diff; a first run is all new; end to end, the monitor's new set equals `changes.json` new and its resolved set equals fixed + deleted.
- `test_report.py` (on a small hand-made report model and on the real day-2 model):
  - card order: severity, then NEW, PERSISTING, RESOLVED; status rules for each pill, including a check that resolved completely
  - each card has its severity, status and owner as `data-` attributes, the `why_it_matters` text, and at most 10 sample rows with new rows first
  - header has the timestamp passed in, the source label and all five tiles with the right numbers; score tile says "not scored" without a manifest
  - filter `<select>` options list exactly the severities, owners and statuses present
  - autoescaping: a check title containing `<script>` comes out escaped
  - self-contained: no `http://` or `https://`, no `<link>`, no `@import`, no `src=` pointing at a file
  - contrast: every severity badge and pill pair, and body text on the card background, is at least 4.5:1 in light and dark palettes (WCAG relative-luminance formula in the test)
  - markdown has the summary, scoring line and cards in the same order
- `test_demo.py`: `monitor demo --no-open` into `tmp_path` exits 0, prints two scoring lines with 0 false positives and nothing missed, writes `latest.html` and `latest.md`; two runs give reports that are identical apart from the timestamp line; the browser is not opened with `--no-open` or when `CI` is set (`webbrowser.open` monkeypatched). Clean generator output through `monitor run` gives zero findings.

Docs in M3: runbook (new demo, outputs, CLI), decisions entries (text storage with NULL for blanks, YAML checks with SQL, D17 split and record-type scoring, day-2 scenario, error handling, Jinja2 for reports, self-contained HTML), CLAUDE.md commands table and done check: "`make demo` catches every in-scope planted record with 0 false positives on day 1 and day 2, and clean data gives zero findings".

After M3: stop and summarize. No HubSpot calls in M1-M3.

## Milestones 4-8 (planned, not built now)

**M4 HubSpot seed and reset (live).**
- `monitor/hubspot/client.py`: httpx client, `Authorization: Bearer` from `HUBSPOT_ACCESS_TOKEN` (service key) loaded with python-dotenv into the environment. The token is never printed or logged, and request logging redacts headers. On 429 and 5xx: retry honoring `Retry-After`, otherwise exponential backoff with jitter, capped attempts. Batch endpoints, 100 records per call.
- Safety guard before any write: `GET /account-info/v3/details` must report a developer test account, and its portal ID must equal `HUBSPOT_PORTAL_ID` from the environment. Otherwise the command stops.
- Properties: a `northwind` property group; `northwind_id` (string, unique values) on companies, contacts and deals; `northwind_created_at` (datetime); `stage_entered_at` (datetime) on deals for D21. Each is created only if missing.
- Seed (`make seed-hubspot`): generate with the same seed, plant only `hubspot_plantable` defects, skip records belonging to non-plantable defects (D01-D03, D09, D10), and upsert with `batch/upsert` on `northwind_id`, so running it twice changes nothing. Associations (contact to company, deal to company and contact) use the v4 batch associations API. Owners: every `OWN-xx` maps to the single user in the test account; no users are created (decided 2026-11-11).
- Dates: business dates are shifted by (seed day − 2026-06-30) so relative rules (D20, D21) hold on the seeding day. HubSpot sets `createdate` itself, so the original creation time goes in `northwind_created_at`, and the live snapshot uses it as `created_at` when present. Without this, every clean closed deal would look like D08 and every pre-cutoff blank-source contact like D22. Approved 2026-11-11 (see `docs/decisions.md`).
- Reset (`make reset-hubspot`): search each object type for records with `northwind_id` set and archive only those, in batches. Dry run by default; `--yes` to delete.
- Verify D08 and D14 (ticket closed before created; D10 stays offline-only, decided 2026-11-11) with probe records tagged `northwind_id`, record the HTTP results in `docs/hubspot-verification.md`, then update the registry, `DEFECTS.md` and the tests together.
- Tests use `httpx.MockTransport`: auth header, retries and backoff, batching, idempotent upsert payloads, reset only touching tagged records, guard refusing a non-test portal. No network in `make test`.

**M5 HubSpot snapshot.** Read companies, contacts and deals with paged list endpoints and the properties the checks need; associations through v4 batch read; owners from `/crm/v3/owners` (archived ones become `active = false`); stage picklist from `/crm/v3/pipelines/deals`. Map to the same `Snapshot` (record ID = `northwind_id` if set, else `HS-<id>`). `monitor run --source hubspot`, `make snapshot-hubspot`. Live scoring against the manifest is meaningful on the seeding day; after that, records age into new findings, which the report shows as findings outside the manifest. Mocked-transport tests only.

**M6 Digest.** `digest.py` builds a short summary from the report model (counts, new issues by severity and owner, check titles; no record contents). If `ANTHROPIC_API_KEY` is set, ask the Claude API for the summary (model and SDK chosen from the claude-api reference at build time); on error or no key, use the plain template summary. If `SLACK_WEBHOOK_URL` is set, post the digest; otherwise skip it. The webhook URL is never logged. Tests cover both fallbacks and mocked calls.

**M7 Scheduled workflow.** `.github/workflows/monitor.yml`: daily cron plus `workflow_dispatch`, `concurrency` so runs don't overlap, `permissions: contents: read`, secrets `HUBSPOT_ACCESS_TOKEN`, `HUBSPOT_PORTAL_ID`, optional `ANTHROPIC_API_KEY`, `SLACK_WEBHOOK_URL`. The previous DuckDB file is restored with `actions/cache` (key per run id, restore-key prefix) so change detection has a previous run; if the cache is gone, the run reports everything as new. Reports are uploaded as an artifact. The job fails on check errors, not on findings. Note: GitHub disables scheduled workflows after 60 days without repo activity.
- GitHub Pages: a second job publishes `reports/latest.html` as `index.html` with `actions/upload-pages-artifact` and `actions/deploy-pages` (`pages: write`, `id-token: write` only on that job, `github-pages` environment). Pages is public for a public repo, so it only ever publishes reports from the developer test account, which holds synthetic Northwind records; the report contains no tokens or portal URLs. The README links to the Pages URL after the first deploy. Before the live job exists, the same job can publish the offline demo report so the link works.

**M8 Docs.** `scripts/check_catalog.py` writes `docs/checks.md` from the YAML (`make catalog`), with a test that fails if the catalog is out of date. README case study via `/case-study` with numbers from a real run, a link to the published report on GitHub Pages, a screenshot of `reports/latest.html`, runbook via `/runbook`, demo GIF.

## Verification (each of M1-M3)

1. Write the milestone's tests, run `make test`, confirm they fail for the right reason.
2. Implement until `make test` passes, then `make lint` and `make hooks` (ruff and gitleaks).
3. M3: run `make demo` and read the printed scoring lines and both reports; expect all in-scope planted records caught with 0 false positives on day 1 and day 2, and the new and resolved sets matching `changes.json`. Run `monitor run` on clean generator output and expect zero findings. Open `reports/latest.html` (`make demo` opens it) and check, with screenshots: card order and pills, each filter alone and combined, expanding a sample table, light and dark scheme, a 375px-wide window with no sideways page scroll, and print preview.
4. `/milestone`, then commit on `feat/offline-monitor` (`feat: ...` with the Co-Authored-By line).
5. Report actual output. If something fails, say so rather than calling the milestone done.
