"""Tests for data/simulate_day2.py: a realistic second day built from day-1 output."""

from __future__ import annotations

import csv
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

import simulate_day2

ROOT = Path(__file__).resolve().parents[1]
SIMULATE = ROOT / "data" / "simulate_day2.py"
KEYS = {"companies": "company_id", "contacts": "contact_id", "deals": "deal_id"}
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def load(folder: Path, table: str) -> dict[str, dict[str, str]]:
    with (folder / f"{table}.csv").open(newline="", encoding="utf-8") as f:
        return {row[KEYS[table]]: row for row in csv.DictReader(f)}


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def ids_for(changes: dict, kind: str, defect_id: str) -> set[str]:
    return {c["record_id"] for c in changes[kind] if c["defect"] == defect_id}


@pytest.fixture(scope="module")
def day2_dir(tmp_path_factory: pytest.TempPathFactory, northwind_defect_dir: Path) -> Path:
    out = tmp_path_factory.mktemp("day2")
    simulate_day2.simulate(northwind_defect_dir, out, seed=42)
    return out


@pytest.fixture(scope="module")
def changes(day2_dir: Path) -> dict:
    return load_json(day2_dir / "changes.json")


def test_same_input_and_seed_give_identical_files(
    day2_dir: Path, northwind_defect_dir: Path, tmp_path: Path
) -> None:
    simulate_day2.simulate(northwind_defect_dir, tmp_path, seed=42)
    names = sorted(p.name for p in day2_dir.iterdir())
    assert names == sorted(p.name for p in tmp_path.iterdir())
    for name in names:
        assert (day2_dir / name).read_bytes() == (tmp_path / name).read_bytes(), name


def test_different_seed_gives_different_changes(northwind_defect_dir: Path, tmp_path: Path) -> None:
    a = simulate_day2.simulate(northwind_defect_dir, tmp_path / "a", seed=42)
    b = simulate_day2.simulate(northwind_defect_dir, tmp_path / "b", seed=7)
    assert a["fixed"] != b["fixed"]


def test_as_of_advances_one_day(day2_dir: Path) -> None:
    assert load_json(day2_dir / "metadata.json")["as_of"] == "2026-07-01"
    assert load_json(day2_dir / "defects_manifest.json")["as_of"] == "2026-07-01"


def test_scenario_counts(changes: dict) -> None:
    expected_fixed = {"D03": 4, "D04": 3, "D06": 3, "D09": 4, "D17": 4, "D20": 4}
    expected_deleted = {"D18": 2, "D01": 3}
    expected_new = {"D22": 3, "D16": 2, "D07": 2, "D23": 2, "D04": 1}
    for kind, expected in [
        ("fixed", expected_fixed),
        ("deleted", expected_deleted),
        ("new", expected_new),
    ]:
        counts: dict[str, int] = {}
        for change in changes[kind]:
            counts[change["defect"]] = counts.get(change["defect"], 0) + 1
        assert counts == expected, kind


def test_day2_manifest_is_day1_minus_resolved_plus_new(
    northwind_defect_dir: Path, day2_dir: Path, changes: dict
) -> None:
    day1 = {
        e["id"]: e for e in load_json(northwind_defect_dir / "defects_manifest.json")["defects"]
    }
    day2 = {e["id"]: e for e in load_json(day2_dir / "defects_manifest.json")["defects"]}
    assert list(day1) == list(day2)
    for defect_id, entry in day2.items():
        expected = (
            set(day1[defect_id]["record_ids"])
            - ids_for(changes, "fixed", defect_id)
            - ids_for(changes, "deleted", defect_id)
        ) | ids_for(changes, "new", defect_id)
        assert set(entry["record_ids"]) == expected, defect_id
        assert entry["count"] == len(entry["record_ids"]), defect_id


def test_d18_pairs_drop_deleted_duplicates(day2_dir: Path, changes: dict) -> None:
    d18 = next(
        e for e in load_json(day2_dir / "defects_manifest.json")["defects"] if e["id"] == "D18"
    )
    assert [pair[1] for pair in d18["pairs"]] == d18["record_ids"]
    assert not ids_for(changes, "deleted", "D18") & set(d18["record_ids"])


def test_fixes_change_the_targeted_field(
    northwind_defect_dir: Path, day2_dir: Path, changes: dict
) -> None:
    contacts1, contacts2 = load(northwind_defect_dir, "contacts"), load(day2_dir, "contacts")
    companies1, companies2 = load(northwind_defect_dir, "companies"), load(day2_dir, "companies")
    deals1, deals2 = load(northwind_defect_dir, "deals"), load(day2_dir, "deals")
    for cid in ids_for(changes, "fixed", "D03"):
        assert not EMAIL_RE.match(contacts1[cid]["email"])
        assert EMAIL_RE.match(contacts2[cid]["email"])
    for cid in ids_for(changes, "fixed", "D04"):
        assert not companies1[cid]["industry"] or not companies1[cid]["country"]
        assert companies2[cid]["industry"] and companies2[cid]["country"]
    for did in ids_for(changes, "fixed", "D06"):
        assert int(deals2[did]["amount"]) > 0
    for did in ids_for(changes, "fixed", "D09"):
        assert deals1[did]["stage"] != "closedwon" and deals2[did]["stage"] == "closedwon"
    for did in ids_for(changes, "fixed", "D17"):
        assert not deals1[did]["owner_id"] and deals2[did]["owner_id"]
    for did in ids_for(changes, "fixed", "D20"):
        assert deals2[did]["close_date"] > "2026-07-01"


def test_deleted_records_are_gone(day2_dir: Path, changes: dict) -> None:
    companies, contacts = load(day2_dir, "companies"), load(day2_dir, "contacts")
    for cid in ids_for(changes, "deleted", "D18"):
        assert cid not in companies
    for cid in ids_for(changes, "deleted", "D01"):
        assert cid not in contacts


def test_new_ids_are_never_reused(northwind_defect_dir: Path, changes: dict) -> None:
    day1_ids = set()
    for table in KEYS:
        day1_ids |= set(load(northwind_defect_dir, table))
    added = {rid for ids in changes["added"].values() for rid in ids}
    assert not added & day1_ids


def test_new_records_are_created_on_day2(day2_dir: Path, changes: dict) -> None:
    for table, ids in changes["added"].items():
        rows = load(day2_dir, table)
        for record_id in ids:
            assert rows[record_id]["created_at"].startswith("2026-07-01"), (table, record_id)
    assert len(changes["added"]["contacts"]) == 3 + 2 + 5  # D22, D16, benign
    assert len(changes["added"]["deals"]) == 2  # D23
    assert len(changes["added"]["companies"]) == 1  # D04


def test_benign_changes_touch_no_defect_records(northwind_defect_dir: Path, changes: dict) -> None:
    planted = {
        rid
        for e in load_json(northwind_defect_dir / "defects_manifest.json")["defects"]
        for rid in e["record_ids"]
    }
    assert len(changes["benign"]["deals"]) == 4  # 3 stage moves + 1 closed won
    assert not set(changes["benign"]["deals"]) & planted


def test_changed_ids_cover_fixes_new_defects_and_benign(changes: dict) -> None:
    changed = {rid for ids in changes["changed"].values() for rid in ids}
    fixed = {c["record_id"] for c in changes["fixed"]}
    new_on_existing = {c["record_id"] for c in changes["new"] if c["defect"] == "D07"}
    benign = {rid for ids in changes["benign"].values() for rid in ids}
    assert changed == fixed | new_on_existing | benign


def test_other_tables_copied_unchanged(northwind_defect_dir: Path, day2_dir: Path) -> None:
    for name in ["owners.csv", "usage_events.csv", "support_tickets.csv"]:
        assert (northwind_defect_dir / name).read_bytes() == (day2_dir / name).read_bytes()


def test_cli(northwind_defect_dir: Path, tmp_path: Path) -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(SIMULATE),
            "--day1",
            str(northwind_defect_dir),
            "--out",
            str(tmp_path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "changes.json").exists()
    assert "2026-07-01" in result.stdout
