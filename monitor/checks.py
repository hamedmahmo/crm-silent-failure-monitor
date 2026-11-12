"""Load checks from checks/*.yaml and run them against one snapshot run.

Each YAML file holds a list of checks. A check's SQL runs against the views created by
monitor.snapshot.use_run() and must return one column, record_id. Adding a check means adding
one entry to a YAML file.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import duckdb
import yaml

from monitor import CHECKS_DIR
from monitor.params import Params
from monitor.snapshot import COLUMNS, SCOPE_TABLES, use_run

SEVERITIES = ("critical", "high", "medium", "low")
FIELDS = (
    "id",
    "title",
    "severity",
    "owner",
    "record_type",
    "defects",
    "why_it_matters",
    "sample_columns",
    "sql",
)
NOT_CHECK_FILES = {"params.yaml"}
ID_RE = re.compile(r"^[a-z][a-z0-9_]*$")
DEFECT_RE = re.compile(r"^D\d{2}$")
MAX_WHY_LENGTH = 160


class CheckError(Exception):
    """A check definition is invalid."""


@dataclass(frozen=True)
class Check:
    id: str
    title: str
    severity: str
    owner: str
    record_type: str
    defects: tuple[str, ...]
    why_it_matters: str
    sample_columns: tuple[str, ...]
    sql: str
    source: str = ""


@dataclass(frozen=True)
class CheckResult:
    check: Check
    status: str  # "ok" or "error"
    error: str | None
    record_ids: list[str]


def load_checks(folder: Path = CHECKS_DIR) -> list[Check]:
    """Load and validate every check in folder/*.yaml (except params.yaml)."""
    checks: list[Check] = []
    seen: dict[str, str] = {}
    for path in sorted(Path(folder).glob("*.yaml")):
        if path.name in NOT_CHECK_FILES:
            continue
        entries = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(entries, list):
            raise CheckError(f"{path}: expected a list of checks")
        for index, entry in enumerate(entries):
            where = f"{path.name} entry {index + 1}"
            check = parse_check(entry, where, str(path))
            if check.id in seen:
                raise CheckError(f"{where}: duplicate id {check.id!r} (also in {seen[check.id]})")
            seen[check.id] = where
            checks.append(check)
    return checks


def parse_check(entry: object, where: str, source: str) -> Check:
    if not isinstance(entry, dict):
        raise CheckError(f"{where}: expected a mapping")
    missing = [f for f in FIELDS if f not in entry]
    if missing:
        raise CheckError(f"{where}: missing {', '.join(missing)}")
    unknown = sorted(set(entry) - set(FIELDS))
    if unknown:
        raise CheckError(f"{where}: unknown field(s) {', '.join(unknown)}")

    check_id = entry["id"]
    if not isinstance(check_id, str) or not ID_RE.match(check_id):
        raise CheckError(f"{where}: id must be lower_snake_case, got {check_id!r}")
    where = f"{where} ({check_id})"
    for field in ("title", "owner", "sql"):
        if not isinstance(entry[field], str) or not entry[field].strip():
            raise CheckError(f"{where}: {field} must be a non-empty string")
    if entry["severity"] not in SEVERITIES:
        raise CheckError(f"{where}: severity must be one of {', '.join(SEVERITIES)}")
    record_type = entry["record_type"]
    if record_type not in SCOPE_TABLES:
        raise CheckError(f"{where}: record_type must be one of {', '.join(SCOPE_TABLES)}")

    defects = entry["defects"]
    if (
        not isinstance(defects, list)
        or not defects
        or not all(isinstance(d, str) and DEFECT_RE.match(d) for d in defects)
    ):
        raise CheckError(f"{where}: defects must be a non-empty list of IDs like D06")

    why = entry["why_it_matters"]
    if not isinstance(why, str) or not why.strip() or "\n" in why.strip():
        raise CheckError(f"{where}: why_it_matters must be one line of text")
    if len(why.strip()) > MAX_WHY_LENGTH:
        raise CheckError(f"{where}: why_it_matters is longer than {MAX_WHY_LENGTH} characters")

    columns = entry["sample_columns"]
    if not isinstance(columns, list) or not columns:
        raise CheckError(f"{where}: sample_columns must be a non-empty list")
    unknown_columns = [c for c in columns if c not in COLUMNS[record_type]]
    if unknown_columns:
        raise CheckError(
            f"{where}: sample_columns not in {record_type}: {', '.join(map(str, unknown_columns))}"
        )

    return Check(
        id=check_id,
        title=entry["title"].strip(),
        severity=entry["severity"],
        owner=entry["owner"].strip(),
        record_type=record_type,
        defects=tuple(defects),
        why_it_matters=why.strip(),
        sample_columns=tuple(columns),
        sql=entry["sql"].strip().rstrip(";"),
        source=source,
    )


def run_one(con: duckdb.DuckDBPyConnection, check: Check) -> CheckResult:
    try:
        con.execute(check.sql)
        columns = [d[0] for d in con.description or []]
        if columns != ["record_id"]:
            return CheckResult(
                check, "error", f"SQL must return one column, record_id; got {columns}", []
            )
        rows = con.fetchall()
    except duckdb.Error as exc:
        return CheckResult(check, "error", f"{type(exc).__name__}: {exc}", [])
    record_ids = sorted({str(row[0]) for row in rows if row[0] is not None})
    return CheckResult(check, "ok", None, record_ids)


def run_checks(
    con: duckdb.DuckDBPyConnection, run_id: int, checks: list[Check], params: Params
) -> list[CheckResult]:
    """Run every check against one run and store findings and per-check status.

    A failing check is recorded with status "error"; the remaining checks still run.
    Running the checks again for the same run replaces the earlier results.
    """
    use_run(con, run_id, params)
    results = [run_one(con, check) for check in checks]
    con.begin()
    try:
        con.execute("DELETE FROM findings WHERE run_id = ?", [run_id])
        con.execute("DELETE FROM check_runs WHERE run_id = ?", [run_id])
        for result in results:
            check = result.check
            con.execute(
                "INSERT INTO check_runs VALUES (?, ?, ?, ?, ?)",
                [run_id, check.id, result.status, result.error, len(result.record_ids)],
            )
            if result.record_ids:
                con.executemany(
                    "INSERT INTO findings VALUES (?, ?, ?, ?)",
                    [[run_id, check.id, check.record_type, rid] for rid in result.record_ids],
                )
        con.commit()
    except Exception:
        con.rollback()
        raise
    return results


def findings_for_run(con: duckdb.DuckDBPyConnection, run_id: int) -> dict[str, set[str]]:
    """Stored findings for one run, as {check_id: {record_id, ...}}."""
    found: dict[str, set[str]] = {}
    for check_id, record_id in con.execute(
        "SELECT check_id, record_id FROM findings WHERE run_id = ?", [run_id]
    ).fetchall():
        found.setdefault(check_id, set()).add(record_id)
    return found
