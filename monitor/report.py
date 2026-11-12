"""Build the report model for one run and render it as HTML and markdown (Jinja2).

The model holds everything the templates need, so templates never query the database.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import duckdb
from jinja2 import Environment, FileSystemLoader, StrictUndefined

from monitor.changes import FindingChange, diff_runs
from monitor.checks import SEVERITIES, Check, findings_for_run
from monitor.scoring import score
from monitor.snapshot import PRIMARY_KEYS, SCOPE_TABLES, record_ids_by_type

PROJECT = "CRM Silent-Failure Monitor"
SOURCE_LABELS = {"csv": "Offline demo (CSV)", "hubspot": "HubSpot"}
STATUSES = ("new", "persisting", "resolved")
MAX_SAMPLES = 10
TEMPLATES = Path(__file__).parent / "templates"

# Colour tokens, written into the page as CSS custom properties. Severity colours are the
# fixed status palette from the dataviz reference (critical, serious, warning) plus a neutral
# grey for low. Every text/background pair is checked for WCAG AA in tests/test_report.py.
_SEVERITY_TOKENS = {
    "sev-critical-bg": "#d03b3b",
    "sev-critical-fg": "#ffffff",
    "sev-high-bg": "#ec835a",
    "sev-high-fg": "#0b0b0b",
    "sev-medium-bg": "#fab219",
    "sev-medium-fg": "#0b0b0b",
    "sev-low-bg": "#52514e",
    "sev-low-fg": "#ffffff",
}
PALETTE: dict[str, dict[str, str]] = {
    "light": {
        "page": "#f9f9f7",
        "surface": "#fcfcfb",
        "surface-2": "#f0efec",
        "border": "#e1e0d9",
        "text": "#0b0b0b",
        "text-secondary": "#52514e",
        "accent": "#256abf",
        "accent-text": "#256abf",
        **_SEVERITY_TOKENS,
        "pill-new-bg": "#256abf",
        "pill-new-fg": "#ffffff",
        "pill-persisting-bg": "#f0efec",
        "pill-persisting-fg": "#3d3c39",
        "pill-resolved-bg": "#e3f1e3",
        "pill-resolved-fg": "#006300",
    },
    "dark": {
        "page": "#0d0d0d",
        "surface": "#1a1a19",
        "surface-2": "#2c2c2a",
        "border": "#383835",
        "text": "#f0efec",
        "text-secondary": "#c3c2b7",
        "accent": "#3987e5",
        "accent-text": "#5598e7",
        **_SEVERITY_TOKENS,
        "pill-new-bg": "#3987e5",
        "pill-new-fg": "#0b0b0b",
        "pill-persisting-bg": "#2c2c2a",
        "pill-persisting-fg": "#e1e0d9",
        "pill-resolved-bg": "#14301a",
        "pill-resolved-fg": "#4cc24c",
    },
}


# ---------------------------------------------------------------- model


@dataclass(frozen=True)
class SampleRow:
    record_id: str
    status: str  # "new", "persisting" or "resolved"
    values: tuple[str | None, ...]


@dataclass(frozen=True)
class Card:
    check_id: str
    title: str
    severity: str
    owner: str
    why_it_matters: str
    record_type: str
    status: str  # "new", "persisting" or "resolved"
    open_count: int
    new_count: int
    persisting_count: int
    resolved_count: int
    sample_columns: tuple[str, ...]
    samples: tuple[SampleRow, ...]


@dataclass(frozen=True)
class ErrorCard:
    check_id: str
    title: str
    severity: str
    owner: str
    error: str


@dataclass(frozen=True)
class TableDiff:
    table: str
    added: int
    removed: int
    changed: int


@dataclass(frozen=True)
class ScoreRow:
    check_id: str
    title: str
    defects: str
    planted: int
    caught: int
    missed: int
    false_positives: int


@dataclass(frozen=True)
class ScoreSummary:
    caught: int
    planted: int
    false_positives: int
    rows: tuple[ScoreRow, ...]
    out_of_scope: tuple[str, ...]

    @property
    def line(self) -> str:
        return (
            f"caught {self.caught} of {self.planted} planted records, "
            f"{self.false_positives} false positives"
        )


@dataclass(frozen=True)
class Report:
    project: str
    run_label: str
    run_at: datetime
    as_of: date
    source_label: str
    previous_label: str | None
    previous_as_of: date | None
    open_issues: int
    new_issues: int
    resolved_issues: int
    records_affected: int
    score: ScoreSummary | None
    cards: tuple[Card, ...]
    errors: tuple[ErrorCard, ...]
    passing: tuple[str, ...]
    record_diff: tuple[TableDiff, ...] | None

    @property
    def severities(self) -> list[str]:
        present = {c.severity for c in self.cards}
        return [s for s in SEVERITIES if s in present]

    @property
    def owners(self) -> list[str]:
        return sorted({c.owner for c in self.cards})

    @property
    def statuses(self) -> list[str]:
        present = {c.status for c in self.cards}
        return [s for s in STATUSES if s in present]


def card_status(new: int, persisting: int, resolved: int) -> str | None:
    if new:
        return "new"
    if persisting:
        return "persisting"
    if resolved:
        return "resolved"
    return None


def sort_cards(cards: list[Card]) -> list[Card]:
    return sorted(
        cards,
        key=lambda c: (
            SEVERITIES.index(c.severity),
            STATUSES.index(c.status),
            -c.open_count,
            -c.resolved_count,
            c.check_id,
        ),
    )


def display_value(value: str | None) -> tuple[str, bool]:
    """Text to show for a raw value, and whether it needs a visual marker."""
    if value is None:
        return "blank", True
    stripped = value.strip()
    if stripped == value:
        return value, False
    lead = len(value) - len(value.lstrip())
    trail = len(value) - len(value.rstrip())
    return "␠" * lead + stripped + "␠" * trail, True


# ---------------------------------------------------------------- building from the DB


def fetch_values(
    con: duckdb.DuckDBPyConnection, run_id: int, check: Check, record_ids: list[str]
) -> dict[str, tuple[str | None, ...]]:
    if not record_ids:
        return {}
    key = PRIMARY_KEYS[check.record_type]
    columns = ", ".join(check.sample_columns)
    rows = con.execute(
        f"SELECT {key}, {columns} FROM snap_{check.record_type} "
        f"WHERE run_id = ? AND list_contains(?, {key})",
        [run_id, record_ids],
    ).fetchall()
    return {row[0]: tuple(row[1:]) for row in rows}


def build_samples(
    con: duckdb.DuckDBPyConnection,
    run_id: int,
    previous_run_id: int | None,
    check: Check,
    changes: list[FindingChange],
    status: str,
) -> tuple[SampleRow, ...]:
    if status == "resolved":
        chosen = sorted((c for c in changes if c.status == "resolved"), key=lambda c: c.record_id)
        source_run = previous_run_id if previous_run_id is not None else run_id
    else:
        chosen = sorted(
            (c for c in changes if c.status != "resolved"),
            key=lambda c: (STATUSES.index(c.status), c.record_id),
        )
        source_run = run_id
    chosen = chosen[:MAX_SAMPLES]
    values = fetch_values(con, source_run, check, [c.record_id for c in chosen])
    blank = (None,) * len(check.sample_columns)
    return tuple(SampleRow(c.record_id, c.status, values.get(c.record_id, blank)) for c in chosen)


def build_report(
    con: duckdb.DuckDBPyConnection,
    run_id: int,
    checks: list[Check],
    manifest: Mapping | None,
    run_at: datetime | None = None,
) -> Report:
    label, source, as_of, loaded_at = con.execute(
        "SELECT label, source, as_of, loaded_at FROM runs WHERE run_id = ?", [run_id]
    ).fetchone()
    diff = diff_runs(con, run_id)
    previous = (
        con.execute(
            "SELECT label, as_of FROM runs WHERE run_id = ?", [diff.previous_run_id]
        ).fetchone()
        if diff.previous_run_id is not None
        else (None, None)
    )
    run_status = {
        check_id: (status, error)
        for check_id, status, error in con.execute(
            "SELECT check_id, status, error FROM check_runs WHERE run_id = ?", [run_id]
        ).fetchall()
    }
    by_check: dict[str, list[FindingChange]] = {}
    for change in diff.findings:
        by_check.setdefault(change.check_id, []).append(change)

    cards, errors, passing = [], [], []
    for check in checks:
        status, error = run_status.get(check.id, ("ok", None))
        if status == "error":
            errors.append(
                ErrorCard(check.id, check.title, check.severity, check.owner, error or "")
            )
            continue
        changes = by_check.get(check.id, [])
        counts = {s: sum(1 for c in changes if c.status == s) for s in STATUSES}
        card_state = card_status(counts["new"], counts["persisting"], counts["resolved"])
        if card_state is None:
            passing.append(check.title)
            continue
        cards.append(
            Card(
                check_id=check.id,
                title=check.title,
                severity=check.severity,
                owner=check.owner,
                why_it_matters=check.why_it_matters,
                record_type=check.record_type,
                status=card_state,
                open_count=counts["new"] + counts["persisting"],
                new_count=counts["new"],
                persisting_count=counts["persisting"],
                resolved_count=counts["resolved"],
                sample_columns=check.sample_columns,
                samples=build_samples(
                    con, run_id, diff.previous_run_id, check, changes, card_state
                ),
            )
        )

    open_findings = [c for c in diff.findings if c.status != "resolved"]
    summary = None
    if manifest is not None:
        result = score(
            findings_for_run(con, run_id), checks, manifest, record_ids_by_type(con, run_id)
        )
        titles = {c.id: c.title for c in checks}
        summary = ScoreSummary(
            caught=result.caught_total,
            planted=result.planted_total,
            false_positives=result.false_positive_total,
            rows=tuple(
                ScoreRow(
                    check_id=s.check_id,
                    title=titles[s.check_id],
                    defects=", ".join(s.defects),
                    planted=len(s.planted),
                    caught=len(s.caught),
                    missed=len(s.missed),
                    false_positives=len(s.false_positives),
                )
                for s in result.checks
            ),
            out_of_scope=tuple(result.out_of_scope),
        )

    return Report(
        project=PROJECT,
        run_label=label,
        run_at=run_at or loaded_at,
        as_of=as_of,
        source_label=SOURCE_LABELS.get(source, source),
        previous_label=previous[0],
        previous_as_of=previous[1],
        open_issues=len(open_findings),
        new_issues=sum(1 for c in diff.findings if c.status == "new"),
        resolved_issues=sum(1 for c in diff.findings if c.status == "resolved"),
        records_affected=len({c.record_id for c in open_findings}),
        score=summary,
        cards=tuple(sort_cards(cards)),
        errors=tuple(errors),
        passing=tuple(passing),
        record_diff=(
            None
            if diff.records is None
            else tuple(
                TableDiff(t, len(d.added), len(d.removed), len(d.changed))
                for t, d in ((t, diff.records[t]) for t in SCOPE_TABLES)
            )
        ),
    )


# ---------------------------------------------------------------- rendering


def _escape_markdown_cell(value: object) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def _environment() -> Environment:
    env = Environment(
        loader=FileSystemLoader(TEMPLATES),
        autoescape=lambda name: name is not None and name.endswith(".html.j2"),
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
    )
    env.filters["md"] = _escape_markdown_cell
    env.filters["ts"] = lambda value: value.strftime("%Y-%m-%d %H:%M UTC")
    env.filters["num"] = lambda value: f"{value:,}"
    env.globals.update(palette=PALETTE, display_value=display_value, severities=SEVERITIES)
    return env


def render_html(report: Report) -> str:
    return _environment().get_template("report.html.j2").render(r=report)


def render_markdown(report: Report) -> str:
    return _environment().get_template("report.md.j2").render(r=report)
