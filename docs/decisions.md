# Decisions

A short log of choices someone might later ask "why?" about. Newest first. Copy the template at the bottom for new entries.

## 2026-11-11: A failing check is recorded as an error, not skipped

**Decision:** Each check runs on its own. A check whose SQL fails, or that returns anything other than one `record_id` column, is stored in `check_runs` with status `error` and the message; the other checks still run.

**Context:** The point of the project is catching failures that make no noise. If a broken check just returned nothing, the report would show the problem as fixed.

**Alternatives:** Stop the whole run on the first error; log and skip.

**Consequences:** The report has to show errored checks first (milestone 3), and `monitor run` will exit with status 1 when any check errors so the scheduled job fails visibly.

## 2026-11-11: Score each check only against planted records of its own record type

**Decision:** A check declares one `record_type`. Its planted set is the manifest `record_ids` of its defects that exist in that table. D17 (no owner, on contacts and deals) is covered by two checks, `contact_no_owner` and `deal_no_owner`. Overall totals count a planted record as caught if any check targeting its defect found it.

**Context:** The manifest lists D17's 4 contact IDs and 4 deal IDs together. Scored against the whole list, each half of a split check would show 4 false misses. One check returning two record types would make the report's sample tables and `sample_columns` ambiguous.

**Alternatives:** One D17 check with a `UNION` of contacts and deals; a record-type field on each manifest ID (would mean changing the template generator).

**Consequences:** 18 checks cover 17 in-scope defects. Scope is derived from each defect's `record_types`, so D05 (companies and usage events) and D15 (tickets and contacts) are out of scope along with the usage-event and ticket defects.

## 2026-11-11: Checks are SQL queries in YAML files

**Decision:** Each check is one YAML entry in `checks/*.yaml` with `id`, `title`, `severity`, `owner`, `record_type`, `defects`, `why_it_matters`, `sample_columns` and `sql`. The loader rejects missing or unknown fields, so a typo fails loudly instead of being ignored.

**Context:** RevOps analysts who know SQL should be able to add a check without touching Python. The same SQL can be pasted into a DuckDB session to debug it.

**Alternatives:** Python functions per check (the approach in `tests/test_generate.py`); a rules DSL such as Great Expectations or Soda.

**Consequences:** The SQL is tested against the generator: every check must find exactly its planted records and nothing in clean data. The severities and owning teams (`finance`, `revops`, `sales-ops`, `marketing-ops`) are a first proposal and can be changed in the YAML without code changes.

## 2026-11-11: Store snapshot values as text, with only empty strings as NULL

**Decision:** Every snapshot column is stored as VARCHAR exactly as read. An empty string becomes NULL; nothing else is trimmed or converted. Checks cast to numbers and dates themselves (`TRY_CAST(amount AS DOUBLE)`, `CAST(left(created_at, 10) AS DATE)`).

**Context:** Several defects are formatting problems: `closedwon ` with a trailing space (D09), an email with a trailing space (D03), and an amount of `0` versus a blank (D06). A typed load would trim, convert or reject exactly the values the monitor needs to see. HubSpot's API also returns property values as strings, so the same rule fits the live source in milestone 5.

**Alternatives:** DuckDB's `read_csv` with type detection; typed tables with a cleaning step before the checks.

**Consequences:** Check SQL is a little longer because of the casts. Rows are inserted from Python with parameterized multi-row `VALUES` statements (250 rows each) rather than `read_csv`, so the CSV sniffer can't change values. Switching from `executemany` to chunked inserts cut `tests/test_snapshot.py` from 13.7 s to 7.9 s.

## 2026-11-11: DuckDB file for snapshots and findings

**Decision:** Each run's snapshot, findings and check results go into one DuckDB file (`data/output/monitor.duckdb` by default). Every snapshot table has a `run_id`, and `use_run()` creates temp views (`owners`, `companies`, `contacts`, `deals`, `run_context`, `deal_stages`) so check SQL never mentions run IDs.

**Context:** Checks are SQL in YAML, change detection compares runs, and the scheduled job (milestone 7) needs to carry the previous run forward. A single embedded file covers all three without a server, and a check's SQL can be pasted into the DuckDB CLI to debug it.

**Alternatives:** SQLite (weaker SQL: no `EXCLUDE`, `TRY_CAST` or regex functions by default); pandas in memory (checks would be Python, not SQL, and nothing persists between runs).

**Consequences:** The file grows by one full snapshot per run, a few thousand rows for Northwind. A pruning option may be needed once the job runs daily against a real portal.

## 2026-11-11: Flag each defect as plantable in HubSpot or not

**Decision:** Every defect in `data/DEFECTS.md` and the `DEFECTS` registry has a `hubspot_plantable` yes/no flag and a fixed record count.

**Context:** The CRM Silent-Failure Monitor will be tested against CSV files and against a HubSpot test portal. HubSpot rejects malformed emails, duplicate emails, invalid stages, unknown company IDs and backdated create dates, so some defects can't exist in a real portal. Those that can are the ones that break reports without an error.

**Alternatives:** One list for CSV only; a separate HubSpot list.

**Consequences:** Tests check that the doc and the registry agree. The flags for D08, D10 and D14 are a judgement and still need checking in a sandbox portal. D21 needs a custom `stage_entered_at` property in HubSpot.

## 2026-11-11: Synthetic emails and domains use `.example`

**Decision:** Every generated email address and company domain ends in `.example`.

**Context:** The data must not contain real people or employers. `.example` is reserved (RFC 2606), so no generated address can reach a real inbox or match a real company domain.

**Alternatives:** Faker with real-looking domains.

**Consequences:** Emails look slightly artificial. Tests check the suffix.

## 2026-11-11: gitleaks via the official golang pre-commit hook

**Decision:** Use the `gitleaks` hook from `github.com/gitleaks/gitleaks`, which pre-commit builds with Go.

**Context:** Neither Go nor a gitleaks binary was installed on the development machine. pre-commit downloads Go itself when it isn't present, and Ubuntu CI runners already have Go.

**Alternatives:** `winget install Gitleaks.Gitleaks` with the `gitleaks-system` hook (faster first run, but every machine and CI job must install the binary); the Docker hook (Docker isn't installed).

**Consequences:** The first hook run takes a few minutes. After that it uses the pre-commit cache. The same config works on Windows and Linux.

## 2026-11-11: Stdlib-only data generator

**Decision:** `data/generate.py` uses only the Python standard library.

**Context:** The generator only needs seeded random choices, dates and CSV output. Without dependencies it runs anywhere uv can install Python, and its output can't change because a library was upgraded.

**Alternatives:** Faker, pandas.

**Consequences:** Name pools are small and hand-written. Output is byte-identical for a given seed on Windows and Linux (CSV uses `\n` line endings).

## 2026-11-11: uv with Python pinned to 3.12

**Decision:** Manage everything with uv. Pin Python with `.python-version` = `3.12` and `requires-python = ">=3.12,<3.13"`.

**Context:** The development machine's default Python is 3.14; CI runs on Ubuntu. The pin keeps both on the same minor version, and uv installs it when missing.

**Alternatives:** pip and venv; a looser `>=3.12`.

**Consequences:** Commands go through `uv run`. Moving to a newer Python means changing both pins and `target-version` in the ruff config.

---

## YYYY-MM-DD: Title

**Decision:** What was decided, in one or two sentences.

**Context:** The problem and the facts that mattered.

**Alternatives:** What else was considered.

**Consequences:** What this makes easier or harder, and anything to revisit.
