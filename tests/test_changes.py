"""Tests for monitor/changes.py: what is new, persisting or resolved since the previous run."""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

import pytest

import simulate_day2
from monitor.changes import diff_runs, previous_run_id
from monitor.checks import load_checks, run_checks
from monitor.params import load_params
from monitor.snapshot import COLUMNS, Snapshot, connect, read_csv_snapshot, store_snapshot

LOADED_AT = datetime(2026, 7, 1, 6, 0, 0)


def deal(deal_id: str, **values: str) -> dict[str, str]:
    row = dict.fromkeys(COLUMNS["deals"], "x")
    row["deal_id"] = deal_id
    row.update(values)
    return row


def snapshot(deals: list[dict[str, str]], as_of: date) -> Snapshot:
    return Snapshot(
        "csv", "test", as_of, {"owners": [], "companies": [], "contacts": [], "deals": deals}
    )


def add_findings(con, run_id: int, check_id: str, record_ids: list[str], status="ok") -> None:
    con.execute(
        "INSERT INTO check_runs VALUES (?, ?, ?, ?, ?)",
        [run_id, check_id, status, None if status == "ok" else "boom", len(record_ids)],
    )
    for record_id in record_ids:
        con.execute("INSERT INTO findings VALUES (?, ?, 'deals', ?)", [run_id, check_id, record_id])


@pytest.fixture()
def two_runs(tmp_path: Path):
    con = connect(tmp_path / "m.duckdb")
    run1 = store_snapshot(
        con,
        snapshot(
            [deal("DEAL-1"), deal("DEAL-2"), deal("DEAL-3"), deal("DEAL-4"), deal("DEAL-5")],
            date(2026, 6, 30),
        ),
        "day1",
        LOADED_AT,
    )
    run2 = store_snapshot(
        con,
        snapshot(
            # DEAL-2 changed, DEAL-3 deleted, DEAL-6 added; DEAL-1, 4 and 5 unchanged.
            [deal("DEAL-1"), deal("DEAL-2", stage="closedwon"), deal("DEAL-4"), deal("DEAL-5"),
             deal("DEAL-6")],
            date(2026, 7, 1),
        ),
        "day2",
        LOADED_AT,
    )  # fmt: skip
    add_findings(con, run1, "check_a", ["DEAL-1", "DEAL-2", "DEAL-3"])
    add_findings(con, run2, "check_a", ["DEAL-1", "DEAL-6"])
    add_findings(con, run1, "check_b", ["DEAL-4"])
    add_findings(con, run2, "check_b", [], status="error")
    yield con, run1, run2
    con.close()


def statuses(diff, check_id: str) -> dict[str, tuple[str, str | None]]:
    return {f.record_id: (f.status, f.resolution) for f in diff.findings if f.check_id == check_id}


def test_previous_run_id(two_runs) -> None:
    con, run1, run2 = two_runs
    assert previous_run_id(con, run1) is None
    assert previous_run_id(con, run2) == run1


def test_new_persisting_and_resolved(two_runs) -> None:
    con, run1, run2 = two_runs
    diff = diff_runs(con, run2)
    assert diff.previous_run_id == run1
    assert statuses(diff, "check_a") == {
        "DEAL-1": ("persisting", None),
        "DEAL-6": ("new", None),
        "DEAL-2": ("resolved", "fixed"),
        "DEAL-3": ("resolved", "deleted"),
    }


def test_errored_check_does_not_resolve_its_findings(two_runs) -> None:
    con, _, run2 = two_runs
    diff = diff_runs(con, run2)
    assert statuses(diff, "check_b") == {}
    assert diff.errored_checks == ["check_b"]


def test_record_diff(two_runs) -> None:
    con, _, run2 = two_runs
    deals = diff_runs(con, run2).records["deals"]
    assert (deals.added, deals.removed, deals.changed) == ({"DEAL-6"}, {"DEAL-3"}, {"DEAL-2"})
    assert diff_runs(con, run2).records["owners"].added == set()


def test_first_run_is_all_new(two_runs) -> None:
    con, run1, _ = two_runs
    diff = diff_runs(con, run1)
    assert diff.previous_run_id is None
    assert {f.status for f in diff.findings} == {"new"}
    assert len(diff.findings) == 4
    assert diff.records is None


# ---------------------------------------------------------------- day 1 to day 2 end to end


@pytest.fixture(scope="module")
def simulated(tmp_path_factory: pytest.TempPathFactory, northwind_defect_dir: Path):
    tmp = tmp_path_factory.mktemp("e2e")
    simulate_day2.simulate(northwind_defect_dir, tmp / "day2", seed=42)
    con = connect(tmp / "monitor.duckdb")
    checks, params = load_checks(), load_params()
    run1 = store_snapshot(con, read_csv_snapshot(northwind_defect_dir), "day1", LOADED_AT)
    run_checks(con, run1, checks, params)
    run2 = store_snapshot(con, read_csv_snapshot(tmp / "day2"), "day2", LOADED_AT)
    run_checks(con, run2, checks, params)
    changes = json.loads((tmp / "day2" / "changes.json").read_text(encoding="utf-8"))
    yield diff_runs(con, run2), changes
    con.close()


def test_monitor_new_matches_simulated_new(simulated) -> None:
    diff, changes = simulated
    found = {f.record_id for f in diff.findings if f.status == "new"}
    assert found == {c["record_id"] for c in changes["new"]}


def test_monitor_resolved_matches_fixed_and_deleted(simulated) -> None:
    diff, changes = simulated
    resolved = {f.record_id: f.resolution for f in diff.findings if f.status == "resolved"}
    expected = {c["record_id"]: "fixed" for c in changes["fixed"]}
    expected |= {c["record_id"]: "deleted" for c in changes["deleted"]}
    assert resolved == expected


def test_monitor_record_diff_matches_simulation(simulated) -> None:
    diff, changes = simulated
    for table in ["companies", "contacts", "deals"]:
        records = diff.records[table]
        assert records.added == set(changes["added"][table]), table
        assert records.removed == set(changes["removed"][table]), table
        assert records.changed == set(changes["changed"][table]), table
    assert diff.records["owners"].added == diff.records["owners"].changed == set()
