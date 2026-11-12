"""Tests for monitor/snapshot.py: reading a snapshot and storing it in DuckDB."""

from __future__ import annotations

import csv
import shutil
from datetime import date, datetime
from pathlib import Path

import pytest

import generate
from monitor.params import load_params
from monitor.snapshot import (
    SCOPE_TABLES,
    SnapshotError,
    connect,
    read_csv_snapshot,
    store_snapshot,
    use_run,
)

LOADED_AT = datetime(2026, 7, 1, 6, 0, 0)


def csv_rows(folder: Path, table: str) -> list[dict[str, str]]:
    with (folder / f"{table}.csv").open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


@pytest.fixture()
def con(tmp_path: Path):
    connection = connect(tmp_path / "monitor.duckdb")
    yield connection
    connection.close()


@pytest.fixture()
def dirty_run(con, northwind_defect_dir: Path) -> int:
    snapshot = read_csv_snapshot(northwind_defect_dir)
    return store_snapshot(con, snapshot, label="day1", loaded_at=LOADED_AT)


def test_scope_tables() -> None:
    assert SCOPE_TABLES == ("owners", "companies", "contacts", "deals")


def test_read_csv_snapshot(northwind_defect_dir: Path) -> None:
    snapshot = read_csv_snapshot(northwind_defect_dir)
    assert snapshot.source == "csv"
    assert snapshot.source_ref == str(northwind_defect_dir)
    assert snapshot.as_of == date(2026, 6, 30)
    assert set(snapshot.tables) == set(SCOPE_TABLES)
    for table in SCOPE_TABLES:
        assert snapshot.tables[table] == csv_rows(northwind_defect_dir, table)


def test_row_counts_match_csv(con, dirty_run: int, northwind_defect_dir: Path) -> None:
    use_run(con, dirty_run, load_params())
    for table in SCOPE_TABLES:
        [(count,)] = con.execute(f"SELECT count(*) FROM {table}").fetchall()
        assert count == len(csv_rows(northwind_defect_dir, table)), table


def test_out_of_scope_tables_not_loaded(con, dirty_run: int) -> None:
    rows = con.execute("SELECT table_name FROM information_schema.tables").fetchall()
    names = {row[0] for row in rows}
    assert not any("usage_events" in n or "support_tickets" in n for n in names)


def test_blank_becomes_null_and_whitespace_is_kept(con, dirty_run: int) -> None:
    use_run(con, dirty_run, load_params())
    stages = {row[0] for row in con.execute("SELECT stage FROM deals").fetchall()}
    assert "closedwon " in stages  # D09 variant with a trailing space
    emails = [row[0] for row in con.execute("SELECT email FROM contacts").fetchall()]
    assert any(e.endswith(" ") for e in emails)  # D03 variant with a trailing space
    [(blank_amounts,)] = con.execute("SELECT count(*) FROM deals WHERE amount = ''").fetchall()
    assert blank_amounts == 0
    [(null_amounts,)] = con.execute("SELECT count(*) FROM deals WHERE amount IS NULL").fetchall()
    assert null_amounts > 0
    [(zero,)] = con.execute("SELECT count(*) FROM deals WHERE amount = '0'").fetchall()
    assert zero > 0  # D06 zero amounts stay as the text '0'


def test_columns_are_text(con, dirty_run: int) -> None:
    types = {
        row[0]
        for row in con.execute(
            "SELECT data_type FROM information_schema.columns "
            "WHERE table_name = 'snap_deals' AND column_name <> 'run_id'"
        ).fetchall()
    }
    assert types == {"VARCHAR"}


def test_runs_table_records_metadata(con, dirty_run: int, northwind_defect_dir: Path) -> None:
    row = con.execute(
        "SELECT run_id, label, source, source_ref, as_of, loaded_at FROM runs"
    ).fetchone()
    assert row == (
        dirty_run,
        "day1",
        "csv",
        str(northwind_defect_dir),
        date(2026, 6, 30),
        LOADED_AT,
    )


def test_two_snapshots_stay_separate(
    con, northwind_defect_dir: Path, northwind_clean_dir: Path
) -> None:
    first = store_snapshot(con, read_csv_snapshot(northwind_defect_dir), "dirty", LOADED_AT)
    second = store_snapshot(con, read_csv_snapshot(northwind_clean_dir), "clean", LOADED_AT)
    assert second == first + 1
    params = load_params()

    use_run(con, first, params)
    [(dirty_contacts,)] = con.execute("SELECT count(*) FROM contacts").fetchall()
    use_run(con, second, params)
    [(clean_contacts,)] = con.execute("SELECT count(*) FROM contacts").fetchall()

    assert dirty_contacts == len(csv_rows(northwind_defect_dir, "contacts"))
    assert clean_contacts == len(csv_rows(northwind_clean_dir, "contacts"))
    assert dirty_contacts != clean_contacts
    columns = [row[0] for row in con.execute("DESCRIBE contacts").fetchall()]
    assert "run_id" not in columns
    assert columns == generate.COLUMNS["contacts"]


def test_run_ids_survive_reconnect(tmp_path: Path, northwind_clean_dir: Path) -> None:
    db = tmp_path / "monitor.duckdb"
    with connect(db) as first:
        run_1 = store_snapshot(first, read_csv_snapshot(northwind_clean_dir), "a", LOADED_AT)
    with connect(db) as second:
        run_2 = store_snapshot(second, read_csv_snapshot(northwind_clean_dir), "b", LOADED_AT)
    assert (run_1, run_2) == (1, 2)


def test_run_context_and_deal_stages(con, dirty_run: int) -> None:
    use_run(con, dirty_run, load_params())
    row = con.execute(
        "SELECT as_of, source_required_from, stale_stage_days, max_deal_amount FROM run_context"
    ).fetchone()
    assert row == (date(2026, 6, 30), date(2026, 1, 1), 60, 150_000)
    stages = con.execute("SELECT stage, is_open, is_closed FROM deal_stages").fetchall()
    assert {s for s, is_open, _ in stages if is_open} == set(generate.OPEN_STAGES)
    assert {s for s, _, is_closed in stages if is_closed} == set(generate.CLOSED_STAGES)


def test_use_run_rejects_unknown_run(con, dirty_run: int) -> None:
    with pytest.raises(SnapshotError, match="run 99"):
        use_run(con, 99, load_params())


def test_missing_csv_raises(tmp_path: Path, northwind_clean_dir: Path) -> None:
    broken = tmp_path / "broken"
    shutil.copytree(northwind_clean_dir, broken)
    (broken / "deals.csv").unlink()
    with pytest.raises(SnapshotError, match="deals.csv"):
        read_csv_snapshot(broken)


def test_missing_metadata_raises(tmp_path: Path, northwind_clean_dir: Path) -> None:
    broken = tmp_path / "broken"
    shutil.copytree(northwind_clean_dir, broken)
    (broken / "metadata.json").unlink()
    with pytest.raises(SnapshotError, match="metadata.json"):
        read_csv_snapshot(broken)


def test_wrong_columns_raise(tmp_path: Path, northwind_clean_dir: Path) -> None:
    broken = tmp_path / "broken"
    shutil.copytree(northwind_clean_dir, broken)
    path = broken / "owners.csv"
    path.write_text("owner_id,name\nOWN-01,Someone\n", encoding="utf-8")
    with pytest.raises(SnapshotError, match="owners.csv"):
        read_csv_snapshot(broken)


def test_params_match_generator_constants() -> None:
    params = load_params()
    assert params.source_required_from == generate.SOURCE_REQUIRED_FROM
    assert params.stale_stage_days == generate.STALE_STAGE_DAYS
    assert params.max_deal_amount == generate.MAX_DEAL_AMOUNT
    assert list(params.open_stages) == generate.OPEN_STAGES
    assert list(params.closed_stages) == generate.CLOSED_STAGES
