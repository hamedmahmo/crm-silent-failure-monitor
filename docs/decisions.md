# Decisions

A short log of choices someone might later ask "why?" about. Newest first. Copy the template at the bottom for new entries.

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
