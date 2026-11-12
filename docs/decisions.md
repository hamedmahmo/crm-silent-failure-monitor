# Decisions

A short log of choices someone might later ask "why?" about. Newest first. Copy the template at the bottom for new entries.

## 2026-11-12: Seed HubSpot with `.example.com` emails and domains

**Decision:** When seeding HubSpot, every company domain and contact email that ends in `.example` gets `.com` appended: `quartzstone.example` becomes `quartzstone.example.com`. The rewrite happens in `build_seed` (`hubspot_domain` and `hubspot_email` in `monitor/hubspot/seed.py`), so the seed output folder holds the same values as the portal. The generator, the CSV files and `make demo` keep `.example`. The probe contacts use `probe-lifecycle.example.com` and `probe-autocreate.example.com`.

**Context:** The first probe run (2026-11-12) showed that HubSpot rejects contact emails on the `.example` top-level domain with `400 INVALID_EMAIL`, so P2 and P3 could not run and all 1,031 seeded contacts would have failed. `example.com` is also reserved (RFC 2606, held by IANA), so its subdomains can't reach a real inbox or match a real company. Appending `.com` keeps each company's domain distinct, keeps contact emails on their company's domain, and keeps the 3 D18 pairs sharing a domain.

**Alternatives:** Leave email off HubSpot contacts (less realistic, and says nothing about company domains). Put every contact on `@example.com` with a unique local part (contacts no longer share their company's domain).

**Consequences:** The data rule in CLAUDE.md has one exception: data sent to HubSpot uses `.example.com`. Milestone 5 compares HubSpot records with the answer key by `northwind_id`, not by email or domain, so the rewrite doesn't affect scoring. Whether HubSpot accepts `.example.com` company domains is checked by the seed itself; the probes only create contacts.

## 2026-11-12: Use HubSpot's date-based API version 2026-09

**Decision:** Every HubSpot request uses the `2026-09` paths (`/crm/objects/2026-09/...`, `/crm/properties/2026-09/...`, `/crm/associations/2026-09/...`, `/account-info/2026-09/details`, `/crm/owners/2026-09`). The version is one constant, `API_VERSION` in `monitor/hubspot/client.py`.

**Context:** Since 2026-03-30 HubSpot versions its API by date and calls `v3` and `v4` "legacy", with no deprecation date announced yet. Each dated version is supported for 18 months. From `2026-09`, validation rules an admin sets up in the portal also apply to API writes; the developer test account has none.

**Alternatives:** The `v3`/`v4` paths that most examples still use.

**Consequences:** The code follows the current docs and has a known support window. Moving to a newer version means changing one constant and re-running the probes.

## 2026-11-12: Store the contact source in a custom property and map industries by label

**Decision:** The generator's `source` goes in a custom enumeration property, `northwind_source`, instead of HubSpot's `hs_analytics_source` (Original source). The 12 generator industries map to HubSpot's `industry` options through a table of HubSpot labels (`INDUSTRY_LABELS`); the values sent are looked up from the portal's live option list. All custom properties use a `northwind_` prefix, so the D21 property planned as `stage_entered_at` is `northwind_stage_entered_at`.

**Context:** HubSpot sets Original source from its own analytics, and its docs disagree on whether the API can write it. `industry` is an enumeration of about 150 HubSpot values, and none of them is spelled like the generator's labels (for example "Software" against "Computer Software").

**Alternatives:** Write `hs_analytics_source` and see if it sticks; a custom `northwind_industry` text property; hard-code HubSpot's internal industry values.

**Consequences:** D22 is checked on a property the seed controls, and probe P4 records whether the standard property is read-only. Companies keep HubSpot's standard industry field, which is what a RevOps team would report on. If HubSpot renames an industry option, the seed stops before writing anything and names the label to fix. Milestone 5 needs the reverse map to turn HubSpot values back into generator labels.

## 2026-11-12: Switch off portal automation before seeding, and check after

**Decision:** Before seeding, "Create and associate companies with contacts" and the lifecycle stage automation (default stage, first deal created, deal won, lead associated) are switched off by hand in the test account. Probes P2 and P3 test them, and the seed's post-seed checks fail if any company lacks `northwind_id` or a D16 contact has gained a lifecycle stage.

**Context:** These portal settings change records after they are created: auto-created companies would show up as D04 findings that aren't in the manifest, and automatic lifecycle stages would fill the blanks that D16 plants. HubSpot doesn't expose them through the API, so the code can't switch them off or read them directly.

**Alternatives:** Leave the settings on and filter their effects out when scoring; skip D16.

**Consequences:** Seeding depends on a manual setup step, documented in the runbook. A portal with the settings on is caught twice: once by the probes before seeding and once by the post-seed checks.

## 2026-11-12: When seeding HubSpot, shift only the dates checked against the as-of date

**Decision:** This corrects the 2026-11-11 entry "Shift dates when seeding HubSpot and keep the original create date". Only open deals' `close_date` and `stage_entered_at` move forward by N days (N = seed day − 2026-06-30). Create times, closed deals' `close_date` and `closed_at` keep their generated values. Create times still go in `northwind_created_at`, and the live run's as-of date is the run date.

**Context:** The original rule broke two checks. With a seeding day of 2026-11-13 (N = 136), shifting closed deals' close dates but not create dates makes all 5 D08 deals close after they were created, so they are missed. Shifting create dates as well moves 57 clean contacts with a blank source from late 2025 to after the 2026-01-01 attribution cutoff, so they become D22 false positives. Only D20 (open deal past its close date) and D21 (stale stage) compare a date with the as-of date.

**Alternatives:** Shift everything and move the attribution cutoff by N for live runs, which makes a business rule depend on the seeding day; don't shift at all and run live checks as of 2026-06-30, which means the live data never ages.

**Consequences:** A test runs all 18 checks on the seed tables for several seeding days and expects exactly the plantable records with no false positives. Seeded records keep historical create and close dates, so the portal looks like a CRM migrated from another system.

## 2026-11-12: Publish the offline demo report on GitHub Pages before milestone 7

**Decision:** A separate workflow, `.github/workflows/pages.yml`, runs `make demo` on every push to `main` and publishes the day-2 report as the Pages site. The README links to it.

**Context:** The plan puts Pages publishing in milestone 7, with the scheduled HubSpot run. Until then the README would have no working link to a report, and a screenshot doesn't show the filters or the sample tables.

**Alternatives:** Wait for milestone 7; commit a copy of the report to the repository (generated files stay out of git); attach the report to a release by hand.

**Consequences:** The published report is synthetic Northwind data from the offline demo, so it can be public. Its run timestamp changes on every push. Milestone 7 changes what the workflow publishes, not where.

## 2026-11-11: Map every sample owner to one HubSpot user; keep D10 offline-only

**Decision:** When seeding HubSpot (milestone 4), every Northwind owner `OWN-01` to `OWN-12` maps to the single user in the developer test account. No HubSpot users are created. D10 (deal owned by an inactive or unknown user) keeps `hubspot_plantable = no` and is tested only against the CSV files.

**Context:** HubSpot owners are portal users; they can't be created through the CRM API, and adding users for test data would mean managing seats and invitations. With one user there is no inactive owner to assign a deal to, and assigning an unknown owner ID is expected to be rejected.

**Alternatives:** Invite extra users and deactivate some to plant D10; probe the API to confirm whether unknown or archived owner IDs are rejected.

**Consequences:** In the live portal every record has the same owner, so owner-based report filters show nothing interesting there. D17 (no owner) is still plantable by leaving the owner empty. D10 is not part of the live verification in milestone 4; only D08 and D14 are probed.

## 2026-11-11: Shift dates when seeding HubSpot and keep the original create date

**Decision:** When seeding HubSpot (milestone 4), every business date (close date, stage entered date and similar) is shifted forward by the number of days between 2026-06-30 and the seeding day. The original create time is stored in a custom datetime property, `northwind_created_at`, and the live snapshot (milestone 5) uses it as `created_at` when it is set, instead of HubSpot's own `createdate`.

**Context:** HubSpot sets `createdate` to the moment a record is created through the API and doesn't allow backdating. Without a fix, every seeded record would look created on the seeding day: every clean closed deal would have a close date before its create date (D08), and every contact created before the attribution cutoff with a blank source would look like D22. The date-relative checks (D20, D21) also need dates that are recent relative to the run's as-of date.

**Alternatives:** Seed with the original dates and accept the false positives; drop the date-based checks from the live run; use HubSpot's import tool, which has its own create-date rules.

**Consequences:** The live run's results match the manifest on the seeding day. On later days records age into new findings (for example open deals passing their close date), which is realistic but means live scoring is only exact on the day of seeding. Data migrated into a real portal often has the same problem, so reading an "original created" property is a pattern a real team could reuse.

## 2026-11-11: One self-contained HTML report rendered with Jinja2

**Decision:** The report is one HTML file with inline CSS and a small inline script (filters, opening sample tables before printing), rendered from `monitor/templates/report.html.j2` with autoescaping on. A markdown version is rendered from the same report model. Colours are CSS custom properties; severity colours reuse a fixed status palette (critical red, serious orange, warning amber, neutral grey), and every text and background pair is tested for 4.5:1 contrast in light and dark mode.

**Context:** The report has to open from disk, as a CI artifact and on GitHub Pages with no network access, and it is meant to be read by RevOps and sales-ops staff, not just engineers. Building the page by string concatenation would make escaping easy to get wrong; record values come from the CRM and can contain anything.

**Alternatives:** A static site generator; a JavaScript framework loaded from a CDN; plain `html.escape` string building (the first plan).

**Consequences:** Jinja2 is a runtime dependency. Filtering works on the check card's status, so "Resolved" shows only checks with no open findings left; partly resolved checks show their resolved count on the card. The run timestamp is the only part of the report that differs between two runs on the same data, which the demo test relies on.

## 2026-11-11: A scripted day 2 instead of random changes

**Decision:** `data/simulate_day2.py` applies a fixed story to day-1 output: specific counts of fixes, merges (deletions), new defects and harmless edits, chosen with a seeded RNG, with `as_of` moved forward one day. It writes the day-2 manifest and a `changes.json` with every change.

**Context:** Change detection needs a second snapshot whose differences are known exactly, so tests can check that "new" and "resolved" match. Random edits would make some changes trip checks by accident, and the expected answer would have to be recomputed by the same kind of logic being tested.

**Alternatives:** Generating day 2 with a different seed (every record would change); hand-editing CSVs (not repeatable).

**Consequences:** New IDs are allocated before deletions so a deleted ID is never reused, which would otherwise turn a "deleted" resolution into "fixed". Moving `as_of` by one day creates no aging findings with this data (clean open deals close on or after 2026-07-01 and entered their stage on or after 2026-05-02); the zero-false-positive day-2 test would catch it if that changed.

## 2026-11-11: Findings of a check that errored are not resolved

**Decision:** When comparing runs, findings of a check that errored in the current run are left out entirely: they are neither persisting nor resolved.

**Context:** If a check breaks, it returns nothing. Comparing that with the previous run would mark every earlier finding as resolved, which is exactly the kind of silent failure the monitor exists to catch.

**Alternatives:** Treat them as persisting (would claim knowledge the run doesn't have).

**Consequences:** The report lists errored checks first, and the resolved count only includes checks that actually ran.

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
