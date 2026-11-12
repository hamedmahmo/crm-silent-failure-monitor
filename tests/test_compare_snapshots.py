"""Tests for monitor/compare.py and scripts/compare_snapshots.py."""

from __future__ import annotations

import copy
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from monitor import ROOT
from monitor.compare import compare_snapshots, main
from monitor.snapshot import read_csv_snapshot, write_csv_snapshot


@pytest.fixture
def expected(northwind_defect_dir: Path):
    return read_csv_snapshot(northwind_defect_dir)


def write(snapshot, folder: Path) -> Path:
    write_csv_snapshot(snapshot, folder)
    return folder


def changed(snapshot, edit) -> object:
    tables = copy.deepcopy(snapshot.tables)
    edit(tables)
    return replace(snapshot, tables=tables)


def test_write_csv_snapshot_reads_back_unchanged(expected, tmp_path):
    back = read_csv_snapshot(write(expected, tmp_path / "copy"))
    assert back.as_of == expected.as_of
    assert back.tables == expected.tables


def test_identical_snapshots_have_no_differences(expected, tmp_path):
    assert compare_snapshots(expected, expected) == []
    folder = write(expected, tmp_path / "a")
    assert main([str(folder), str(folder)]) == 0


def test_a_changed_value_is_reported(expected, capsys, tmp_path):
    def edit(tables):
        tables["companies"][0]["industry"] = "Changed"

    actual = changed(expected, edit)
    problems = compare_snapshots(expected, actual)
    company = expected.tables["companies"][0]["company_id"]
    assert len(problems) == 1
    assert "companies.industry" in problems[0]
    assert company in problems[0]
    a, b = write(expected, tmp_path / "a"), write(actual, tmp_path / "b")
    assert main([str(a), str(b)]) == 1
    assert "companies.industry" in capsys.readouterr().out


def test_missing_and_extra_records_are_reported(expected):
    def edit(tables):
        removed = tables["contacts"].pop(0)
        extra = dict(tables["deals"][0], deal_id="HS-123")
        tables["deals"].append(extra)
        assert removed

    problems = "\n".join(compare_snapshots(expected, changed(expected, edit)))
    assert "contacts: 1 missing" in problems
    assert "deals: 1 extra" in problems
    assert "HS-123" in problems


def test_owner_ids_are_compared_as_blank_or_set(expected):
    def other_owner(tables):
        for table in ("companies", "contacts", "deals"):
            for row in tables[table]:
                if row["owner_id"]:
                    row["owner_id"] = "77"
        tables["owners"] = [{"owner_id": "77", "name": "", "active": "true"}]

    assert compare_snapshots(expected, changed(expected, other_owner)) == []

    def drop_owner(tables):
        row = next(r for r in tables["deals"] if r["owner_id"])
        row["owner_id"] = ""

    problems = compare_snapshots(expected, changed(expected, drop_owner))
    assert len(problems) == 1
    assert "deals.owner_id" in problems[0]


def test_examples_are_capped_at_5(expected):
    def edit(tables):
        for row in tables["contacts"][:8]:
            row["job_title"] = "Changed"

    [problem] = compare_snapshots(expected, changed(expected, edit))
    assert problem.startswith("contacts.job_title: 8 records differ")
    listed = problem.split(" (e.g.")[0]
    assert listed.count("CONT-") == 5
    assert listed.endswith("and 3 more")


def test_script_runs(expected, tmp_path):
    folder = write(expected, tmp_path / "a")
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "compare_snapshots.py"), str(folder), str(folder)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "no differences" in result.stdout
