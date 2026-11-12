"""Read a CRM snapshot and store it in DuckDB.

A Snapshot is source-agnostic: the CSV reader here builds one from generator output, and the
HubSpot reader (milestone 5) will build the same object from the API.

Values are stored as text exactly as read. Only an empty string becomes NULL, so whitespace
defects such as "closedwon " survive. Checks cast to numbers and dates where they need to.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

import duckdb

from monitor.params import Params

SCOPE_TABLES = ("owners", "companies", "contacts", "deals")
COLUMNS: dict[str, list[str]] = {
    "owners": ["owner_id", "name", "active"],
    "companies": [
        "company_id", "name", "domain", "industry", "country", "employee_band", "plan_tier",
        "owner_id", "created_at", "churned_at",
    ],
    "contacts": [
        "contact_id", "company_id", "first_name", "last_name", "email", "job_title",
        "lifecycle_stage", "owner_id", "source", "created_at",
    ],
    "deals": [
        "deal_id", "company_id", "primary_contact_id", "owner_id", "stage", "amount", "currency",
        "created_at", "stage_entered_at", "close_date", "closed_at",
    ],
}  # fmt: skip
PRIMARY_KEYS = {
    "owners": "owner_id",
    "companies": "company_id",
    "contacts": "contact_id",
    "deals": "deal_id",
}

Rows = list[dict[str, str]]


class SnapshotError(Exception):
    """A snapshot can't be read or a run can't be selected."""


@dataclass(frozen=True)
class Snapshot:
    source: str  # "csv" or "hubspot"
    source_ref: str  # folder path, or portal description
    as_of: date
    tables: dict[str, Rows]


def read_csv_snapshot(folder: Path) -> Snapshot:
    """Read the in-scope tables and metadata.json from a generator output folder."""
    folder = Path(folder)
    metadata_path = folder / "metadata.json"
    if not metadata_path.is_file():
        raise SnapshotError(f"missing {metadata_path}")
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    tables: dict[str, Rows] = {}
    for table in SCOPE_TABLES:
        path = folder / f"{table}.csv"
        if not path.is_file():
            raise SnapshotError(f"missing {path}")
        with path.open(newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            if reader.fieldnames != COLUMNS[table]:
                raise SnapshotError(
                    f"{path}: expected columns {COLUMNS[table]}, got {reader.fieldnames}"
                )
            tables[table] = list(reader)
    return Snapshot("csv", str(folder), date.fromisoformat(metadata["as_of"]), tables)


def connect(db_path: Path | str) -> duckdb.DuckDBPyConnection:
    """Open (or create) the monitor database and make sure the tables exist."""
    if str(db_path) != ":memory:":
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(db_path))
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS runs (
            run_id INTEGER PRIMARY KEY,
            label VARCHAR NOT NULL,
            source VARCHAR NOT NULL,
            source_ref VARCHAR NOT NULL,
            as_of DATE NOT NULL,
            loaded_at TIMESTAMP NOT NULL
        )
        """
    )
    for table in SCOPE_TABLES:
        columns = ", ".join(f"{name} VARCHAR" for name in COLUMNS[table])
        con.execute(f"CREATE TABLE IF NOT EXISTS snap_{table} (run_id INTEGER NOT NULL, {columns})")
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS findings (
            run_id INTEGER NOT NULL,
            check_id VARCHAR NOT NULL,
            record_type VARCHAR NOT NULL,
            record_id VARCHAR NOT NULL,
            PRIMARY KEY (run_id, check_id, record_id)
        )
        """
    )
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS check_runs (
            run_id INTEGER NOT NULL,
            check_id VARCHAR NOT NULL,
            status VARCHAR NOT NULL,
            error VARCHAR,
            finding_count INTEGER NOT NULL,
            PRIMARY KEY (run_id, check_id)
        )
        """
    )
    return con


INSERT_CHUNK = 250


def insert_rows(
    con: duckdb.DuckDBPyConnection, table: str, width: int, rows: list[list[object]]
) -> None:
    """Insert rows with parameterized multi-row VALUES; much faster than executemany."""
    row_sql = "(" + ", ".join("?" * width) + ")"
    for start in range(0, len(rows), INSERT_CHUNK):
        chunk = rows[start : start + INSERT_CHUNK]
        values = ", ".join([row_sql] * len(chunk))
        con.execute(f"INSERT INTO {table} VALUES {values}", [v for row in chunk for v in row])


def store_snapshot(
    con: duckdb.DuckDBPyConnection,
    snapshot: Snapshot,
    label: str,
    loaded_at: datetime | None = None,
) -> int:
    """Store a snapshot as a new run and return its run_id."""
    if loaded_at is None:
        loaded_at = datetime.now(UTC).replace(tzinfo=None)
    [(run_id,)] = con.execute("SELECT coalesce(max(run_id), 0) + 1 FROM runs").fetchall()
    con.begin()
    try:
        con.execute(
            "INSERT INTO runs VALUES (?, ?, ?, ?, ?, ?)",
            [run_id, label, snapshot.source, snapshot.source_ref, snapshot.as_of, loaded_at],
        )
        for table in SCOPE_TABLES:
            columns = COLUMNS[table]
            rows = [
                [run_id, *(row[c] if row[c] != "" else None for c in columns)]
                for row in snapshot.tables[table]
            ]
            insert_rows(con, f"snap_{table}", len(columns) + 1, rows)
        con.commit()
    except Exception:
        con.rollback()
        raise
    return run_id


def record_ids_by_type(con: duckdb.DuckDBPyConnection, run_id: int) -> dict[str, set[str]]:
    """Primary keys of every in-scope table in one run."""
    return {
        table: {
            row[0]
            for row in con.execute(
                f"SELECT {PRIMARY_KEYS[table]} FROM snap_{table} WHERE run_id = ?", [run_id]
            ).fetchall()
        }
        for table in SCOPE_TABLES
    }


def use_run(con: duckdb.DuckDBPyConnection, run_id: int, params: Params) -> None:
    """Point the views that check SQL uses at one run.

    Creates temp views owners, companies, contacts, deals (that run's rows) plus run_context
    (one row of thresholds and as_of) and deal_stages (the stage picklist).
    """
    row = con.execute("SELECT as_of FROM runs WHERE run_id = ?", [run_id]).fetchone()
    if row is None:
        raise SnapshotError(f"run {run_id} not found")
    run_id = int(run_id)
    for table in SCOPE_TABLES:
        con.execute(
            f"CREATE OR REPLACE TEMP VIEW {table} AS "
            f"SELECT * EXCLUDE (run_id) FROM snap_{table} WHERE run_id = {run_id}"
        )
    con.execute(
        "CREATE OR REPLACE TEMP TABLE run_context (as_of DATE, source_required_from DATE, "
        "stale_stage_days INTEGER, max_deal_amount BIGINT)"
    )
    con.execute(
        "INSERT INTO run_context VALUES (?, ?, ?, ?)",
        [row[0], params.source_required_from, params.stale_stage_days, params.max_deal_amount],
    )
    con.execute(
        "CREATE OR REPLACE TEMP TABLE deal_stages "
        "(stage VARCHAR, is_open BOOLEAN, is_closed BOOLEAN)"
    )
    con.executemany(
        "INSERT INTO deal_stages VALUES (?, ?, ?)",
        [[s, True, False] for s in params.open_stages]
        + [[s, False, True] for s in params.closed_stages],
    )
