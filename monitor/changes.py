"""Compare one run with the previous run: findings and records.

Findings are compared on (check_id, record_id): new, persisting or resolved. A resolved finding
is "deleted" when its record is gone from the current snapshot, otherwise "fixed". Checks that
errored in the current run are left out, so a broken check never makes problems look solved.
"""

from __future__ import annotations

from dataclasses import dataclass

import duckdb

from monitor.snapshot import COLUMNS, PRIMARY_KEYS, SCOPE_TABLES, record_ids_by_type


@dataclass(frozen=True)
class FindingChange:
    check_id: str
    record_type: str
    record_id: str
    status: str  # "new", "persisting" or "resolved"
    resolution: str | None = None  # "fixed" or "deleted" when resolved


@dataclass(frozen=True)
class RecordDiff:
    table: str
    added: set[str]
    removed: set[str]
    changed: set[str]


@dataclass(frozen=True)
class RunDiff:
    run_id: int
    previous_run_id: int | None
    findings: list[FindingChange]
    records: dict[str, RecordDiff] | None  # None on the first run
    errored_checks: list[str]


def previous_run_id(con: duckdb.DuckDBPyConnection, run_id: int) -> int | None:
    [(previous,)] = con.execute(
        "SELECT max(run_id) FROM runs WHERE run_id < ?", [run_id]
    ).fetchall()
    return previous


def findings_with_type(con: duckdb.DuckDBPyConnection, run_id: int) -> dict[tuple[str, str], str]:
    rows = con.execute(
        "SELECT check_id, record_id, record_type FROM findings WHERE run_id = ?", [run_id]
    ).fetchall()
    return {(check_id, record_id): record_type for check_id, record_id, record_type in rows}


def errored_checks(con: duckdb.DuckDBPyConnection, run_id: int) -> list[str]:
    rows = con.execute(
        "SELECT check_id FROM check_runs WHERE run_id = ? AND status = 'error' ORDER BY check_id",
        [run_id],
    ).fetchall()
    return [row[0] for row in rows]


def record_diff(
    con: duckdb.DuckDBPyConnection, run_id: int, previous: int, table: str
) -> RecordDiff:
    key = PRIMARY_KEYS[table]
    columns = ", ".join(COLUMNS[table])
    current_ids = {r[0] for r in con.execute(
        f"SELECT {key} FROM snap_{table} WHERE run_id = ?", [run_id]).fetchall()}  # fmt: skip
    previous_ids = {r[0] for r in con.execute(
        f"SELECT {key} FROM snap_{table} WHERE run_id = ?", [previous]).fetchall()}  # fmt: skip
    # EXCEPT compares whole rows and treats NULLs as equal.
    differing = {
        r[0]
        for r in con.execute(
            f"SELECT {key} FROM (SELECT {columns} FROM snap_{table} WHERE run_id = ? "
            f"EXCEPT SELECT {columns} FROM snap_{table} WHERE run_id = ?)",
            [run_id, previous],
        ).fetchall()
    }
    return RecordDiff(
        table=table,
        added=current_ids - previous_ids,
        removed=previous_ids - current_ids,
        changed=(differing & previous_ids),
    )


def diff_runs(con: duckdb.DuckDBPyConnection, run_id: int) -> RunDiff:
    previous = previous_run_id(con, run_id)
    errored = errored_checks(con, run_id)
    skip = set(errored)
    current = {k: v for k, v in findings_with_type(con, run_id).items() if k[0] not in skip}
    before = (
        {k: v for k, v in findings_with_type(con, previous).items() if k[0] not in skip}
        if previous is not None
        else {}
    )
    present = record_ids_by_type(con, run_id)

    changes = []
    for (check_id, record_id), record_type in sorted(current.items()):
        status = "persisting" if (check_id, record_id) in before else "new"
        changes.append(FindingChange(check_id, record_type, record_id, status))
    for (check_id, record_id), record_type in sorted(before.items()):
        if (check_id, record_id) not in current:
            resolution = "fixed" if record_id in present[record_type] else "deleted"
            changes.append(FindingChange(check_id, record_type, record_id, "resolved", resolution))

    records = (
        None
        if previous is None
        else {table: record_diff(con, run_id, previous, table) for table in SCOPE_TABLES}
    )
    return RunDiff(run_id, previous, changes, records, errored)
