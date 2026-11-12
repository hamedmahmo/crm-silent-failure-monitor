"""One monitor run: snapshot, checks, comparison, score and report."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import duckdb

from monitor.checks import Check, CheckResult, run_checks
from monitor.params import Params
from monitor.report import Report, build_report, render_html, render_markdown
from monitor.snapshot import read_csv_snapshot, store_snapshot

MANIFEST_NAME = "defects_manifest.json"


@dataclass(frozen=True)
class RunOutcome:
    run_id: int
    label: str
    report: Report
    results: list[CheckResult]

    @property
    def errors(self) -> list[CheckResult]:
        return [r for r in self.results if r.status == "error"]


def run_once(
    con: duckdb.DuckDBPyConnection,
    folder: Path,
    label: str,
    checks: list[Check],
    params: Params,
    loaded_at: datetime | None = None,
) -> RunOutcome:
    """Load a CSV snapshot folder as a new run, run the checks and build the report."""
    folder = Path(folder)
    run_id = store_snapshot(con, read_csv_snapshot(folder), label, loaded_at)
    results = run_checks(con, run_id, checks, params)
    manifest_path = folder / MANIFEST_NAME
    manifest = (
        json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.is_file() else None
    )
    return RunOutcome(run_id, label, build_report(con, run_id, checks, manifest), results)


def write_reports(report: Report, reports_dir: Path, label: str) -> list[Path]:
    """Write <label>.html/.md and latest.html/.md; return the paths written."""
    reports_dir = Path(reports_dir)
    reports_dir.mkdir(parents=True, exist_ok=True)
    html, markdown = render_html(report), render_markdown(report)
    written = []
    for name in (label, "latest"):
        for suffix, text in ((".html", html), (".md", markdown)):
            path = reports_dir / f"{name}{suffix}"
            path.write_text(text, encoding="utf-8", newline="\n")
            written.append(path)
    return written


def summary_lines(outcome: RunOutcome) -> list[str]:
    r = outcome.report
    open_checks = sum(1 for c in r.cards if c.status != "resolved")
    persisting = r.open_issues - r.new_issues
    line = (
        f"{outcome.label} (as of {r.as_of}): {r.open_issues} open issues in {open_checks} checks: "
        f"{r.new_issues} new, {persisting} persisting, {r.resolved_issues} resolved"
    )
    if r.previous_label is None:
        line += " (no previous run)"
    lines = [line, f"  {r.score.line}" if r.score else "  not scored (no defects_manifest.json)"]
    for result in outcome.errors:
        lines.append(f"  check error: {result.check.id}: {result.error}")
    return lines
