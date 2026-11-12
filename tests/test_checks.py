"""Tests for monitor/checks.py and the checks in checks/*.yaml."""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

import pytest
import yaml

import generate
from monitor.checks import Check, CheckError, findings_for_run, load_checks, run_checks
from monitor.params import load_params
from monitor.scoring import in_scope_defect_ids
from monitor.snapshot import (
    PRIMARY_KEYS,
    SCOPE_TABLES,
    connect,
    read_csv_snapshot,
    record_ids_by_type,
    store_snapshot,
)

LOADED_AT = datetime(2026, 7, 1, 6, 0, 0)
EXPECTED_IN_SCOPE = [
    "D01", "D02", "D03", "D04", "D06", "D07", "D08", "D09", "D10",
    "D16", "D17", "D18", "D19", "D20", "D21", "D22", "D23",
]  # fmt: skip
REAL_CHECKS = load_checks()


def registry_entries() -> list[dict]:
    return [{"id": d.id, "record_types": list(d.record_types)} for d in generate.DEFECTS]


@pytest.fixture(scope="module")
def db(tmp_path_factory: pytest.TempPathFactory, northwind_defect_dir, northwind_clean_dir):
    con = connect(tmp_path_factory.mktemp("checks") / "monitor.duckdb")
    dirty = store_snapshot(con, read_csv_snapshot(northwind_defect_dir), "dirty", LOADED_AT)
    clean = store_snapshot(con, read_csv_snapshot(northwind_clean_dir), "clean", LOADED_AT)
    params = load_params()
    dirty_results = {r.check.id: r for r in run_checks(con, dirty, REAL_CHECKS, params)}
    clean_results = {r.check.id: r for r in run_checks(con, clean, REAL_CHECKS, params)}
    yield {
        "con": con,
        "dirty": dirty,
        "clean": clean,
        "dirty_results": dirty_results,
        "clean_results": clean_results,
    }
    con.close()


@pytest.fixture(scope="module")
def manifest(northwind_defect_dir: Path) -> dict:
    return json.loads((northwind_defect_dir / "defects_manifest.json").read_text(encoding="utf-8"))


# ---------------------------------------------------------------- scope and coverage


def test_in_scope_defects_derived_from_record_types() -> None:
    assert in_scope_defect_ids(registry_entries()) == EXPECTED_IN_SCOPE


def test_every_in_scope_defect_has_a_check() -> None:
    covered = {d for check in REAL_CHECKS for d in check.defects}
    assert set(EXPECTED_IN_SCOPE) <= covered


def test_no_check_targets_an_out_of_scope_defect() -> None:
    for check in REAL_CHECKS:
        assert set(check.defects) <= set(EXPECTED_IN_SCOPE), check.id


def test_check_ids_unique_and_files_loaded() -> None:
    ids = [c.id for c in REAL_CHECKS]
    assert len(ids) == len(set(ids)) == 18
    assert {Path(c.source).name for c in REAL_CHECKS} == {
        "companies.yaml",
        "contacts.yaml",
        "deals.yaml",
    }


# ---------------------------------------------------------------- real checks on real data


@pytest.mark.parametrize("check", REAL_CHECKS, ids=lambda c: c.id)
def test_check_finds_exactly_the_planted_records(db, manifest: dict, check: Check) -> None:
    result = db["dirty_results"][check.id]
    assert result.status == "ok", result.error
    of_type = record_ids_by_type(db["con"], db["dirty"])[check.record_type]
    planted = {
        record_id
        for entry in manifest["defects"]
        if entry["id"] in check.defects
        for record_id in entry["record_ids"]
        if record_id in of_type
    }
    assert planted, "every check should have planted records to find"
    assert set(result.record_ids) == planted


@pytest.mark.parametrize("check", REAL_CHECKS, ids=lambda c: c.id)
def test_check_finds_nothing_in_clean_data(db, check: Check) -> None:
    result = db["clean_results"][check.id]
    assert result.status == "ok", result.error
    assert result.record_ids == []


def test_clean_data_false_positive_traps_are_not_flagged(db) -> None:
    con, run_id = db["con"], db["clean"]
    pre_cutoff_blank_source = {
        row[0]
        for row in con.execute(
            "SELECT contact_id FROM snap_contacts WHERE run_id = ? AND source IS NULL "
            "AND CAST(left(created_at, 10) AS DATE) < DATE '2026-01-01'",
            [run_id],
        ).fetchall()
    }
    early_stage_blank_amount = {
        row[0]
        for row in con.execute(
            "SELECT deal_id FROM snap_deals WHERE run_id = ? AND amount IS NULL "
            "AND stage IN ('appointmentscheduled', 'qualifiedtobuy', 'presentationscheduled')",
            [run_id],
        ).fetchall()
    }
    assert pre_cutoff_blank_source
    assert early_stage_blank_amount
    flagged = findings_for_run(con, run_id)
    all_flagged = {record_id for ids in flagged.values() for record_id in ids}
    assert not all_flagged & (pre_cutoff_blank_source | early_stage_blank_amount)


def test_findings_table_matches_results(db) -> None:
    stored = findings_for_run(db["con"], db["dirty"])
    for check_id, result in db["dirty_results"].items():
        assert stored.get(check_id, set()) == set(result.record_ids)


def test_record_ids_by_type(db, northwind_defect_dir: Path) -> None:
    ids = record_ids_by_type(db["con"], db["dirty"])
    assert set(ids) == set(SCOPE_TABLES)
    snapshot = read_csv_snapshot(northwind_defect_dir)
    for table in SCOPE_TABLES:
        assert ids[table] == {row[PRIMARY_KEYS[table]] for row in snapshot.tables[table]}


# ---------------------------------------------------------------- loader validation


def valid_entry(**overrides: object) -> dict:
    entry = {
        "id": "deal_no_company_test",
        "title": "Deal with no company",
        "severity": "high",
        "owner": "revops",
        "record_type": "deals",
        "defects": ["D19"],
        "why_it_matters": "Deals without a company drop out of account-level revenue reports.",
        "sample_columns": ["deal_id", "stage", "amount"],
        "sql": "SELECT deal_id AS record_id FROM deals WHERE company_id IS NULL",
    }
    entry.update(overrides)
    return {k: v for k, v in entry.items() if v is not None}


def write_checks(folder: Path, entries: list[dict], name: str = "test.yaml") -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_text(yaml.safe_dump(entries, sort_keys=False), encoding="utf-8")
    return folder


def test_loader_accepts_valid_entry(tmp_path: Path) -> None:
    [check] = load_checks(write_checks(tmp_path, [valid_entry()]))
    assert check.id == "deal_no_company_test"
    assert check.defects == ("D19",)
    assert check.sample_columns == ("deal_id", "stage", "amount")
    assert check.source.endswith("test.yaml")


def test_loader_ignores_params_yaml(tmp_path: Path) -> None:
    write_checks(tmp_path, [valid_entry()])
    (tmp_path / "params.yaml").write_text("stale_stage_days: 60\n", encoding="utf-8")
    assert len(load_checks(tmp_path)) == 1


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"title": None}, "missing"),
        ({"sql": None}, "missing"),
        ({"why_it_matters": None}, "missing"),
        ({"severity": "urgent"}, "severity"),
        ({"record_type": "usage_events"}, "record_type"),
        ({"defects": ["19"]}, "defect"),
        ({"defects": []}, "defect"),
        ({"why_it_matters": "Line one.\nLine two."}, "why_it_matters"),
        ({"why_it_matters": "x" * 161}, "why_it_matters"),
        ({"sample_columns": ["deal_id", "not_a_column"]}, "not_a_column"),
        ({"sample_columns": []}, "sample_columns"),
        ({"owner": ""}, "owner"),
        ({"id": "Bad Id"}, "id"),
        ({"surprise": "typo"}, "surprise"),
    ],
)
def test_loader_rejects_invalid_entry(tmp_path: Path, overrides: dict, message: str) -> None:
    with pytest.raises(CheckError, match=message):
        load_checks(write_checks(tmp_path, [valid_entry(**overrides)]))


def test_loader_rejects_duplicate_ids(tmp_path: Path) -> None:
    write_checks(tmp_path, [valid_entry()], "a.yaml")
    write_checks(tmp_path, [valid_entry()], "b.yaml")
    with pytest.raises(CheckError, match="duplicate"):
        load_checks(tmp_path)


def test_loader_rejects_file_that_is_not_a_list(tmp_path: Path) -> None:
    (tmp_path / "bad.yaml").write_text("id: x\n", encoding="utf-8")
    with pytest.raises(CheckError, match="list"):
        load_checks(tmp_path)


# ---------------------------------------------------------------- engine behaviour


@pytest.fixture()
def small_db(tmp_path: Path, northwind_defect_dir: Path):
    con = connect(tmp_path / "small.duckdb")
    run_id = store_snapshot(con, read_csv_snapshot(northwind_defect_dir), "day1", LOADED_AT)
    yield con, run_id
    con.close()


def test_adding_a_yaml_entry_adds_a_check(tmp_path: Path, small_db, manifest: dict) -> None:
    con, run_id = small_db
    checks = load_checks(write_checks(tmp_path / "checks", [valid_entry()]))
    [result] = run_checks(con, run_id, checks, load_params())
    d19 = next(e for e in manifest["defects"] if e["id"] == "D19")
    assert result.status == "ok"
    assert set(result.record_ids) == set(d19["record_ids"])


def test_broken_check_is_recorded_and_others_still_run(tmp_path: Path, small_db) -> None:
    con, run_id = small_db
    entries = [
        valid_entry(id="broken_sql", sql="SELECT nope FROM missing_table"),
        valid_entry(id="wrong_column", sql="SELECT deal_id FROM deals WHERE company_id IS NULL"),
        valid_entry(id="works"),
    ]
    checks = load_checks(write_checks(tmp_path / "checks", entries))
    results = {r.check.id: r for r in run_checks(con, run_id, checks, load_params())}
    assert results["broken_sql"].status == "error"
    assert "missing_table" in results["broken_sql"].error
    assert results["wrong_column"].status == "error"
    assert "record_id" in results["wrong_column"].error
    assert results["works"].status == "ok"
    assert len(results["works"].record_ids) == 5
    rows = con.execute(
        "SELECT check_id, status, finding_count FROM check_runs WHERE run_id = ? ORDER BY check_id",
        [run_id],
    ).fetchall()
    assert rows == [("broken_sql", "error", 0), ("works", "ok", 5), ("wrong_column", "error", 0)]


def test_rerunning_checks_replaces_findings(tmp_path: Path, small_db) -> None:
    con, run_id = small_db
    checks = load_checks(write_checks(tmp_path / "checks", [valid_entry()]))
    run_checks(con, run_id, checks, load_params())
    run_checks(con, run_id, checks, load_params())
    [(count,)] = con.execute("SELECT count(*) FROM findings WHERE run_id = ?", [run_id]).fetchall()
    [(runs,)] = con.execute("SELECT count(*) FROM check_runs WHERE run_id = ?", [run_id]).fetchall()
    assert (count, runs) == (5, 1)


def test_check_sql_uses_as_of_from_the_snapshot(tmp_path: Path, small_db) -> None:
    con, run_id = small_db
    entry = valid_entry(sql="SELECT as_of::VARCHAR AS record_id FROM run_context")
    checks = load_checks(write_checks(tmp_path / "checks", [entry]))
    [result] = run_checks(con, run_id, checks, load_params())
    assert result.record_ids == [date(2026, 6, 30).isoformat()]  # from metadata.json
