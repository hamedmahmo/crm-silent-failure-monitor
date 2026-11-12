"""Tests for data/generate.py.

The detection checks in this file are written independently of the planting code in
generate.py. Each check is the reference rule for one defect in data/DEFECTS.md: it must find
nothing in clean data and exactly the manifest's records when defects are planted.
"""

from __future__ import annotations

import csv
import json
import re
import subprocess
import sys
from collections import Counter, defaultdict
from collections.abc import Callable
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

import generate

ROOT = Path(__file__).resolve().parents[1]
GENERATE = ROOT / "data" / "generate.py"
DEFECTS_MD = ROOT / "data" / "DEFECTS.md"

# Business rules, restated here so the tests act as the spec.
AS_OF = date(2026, 6, 30)
SOURCE_REQUIRED_FROM = date(2026, 1, 1)
STALE_STAGE_DAYS = 60
MAX_DEAL_AMOUNT = 150_000
EVENT_WINDOW_START = AS_OF - timedelta(days=59)

TABLES = ["owners", "companies", "contacts", "deals", "usage_events", "support_tickets"]
PRIMARY_KEYS = {
    "owners": "owner_id",
    "companies": "company_id",
    "contacts": "contact_id",
    "deals": "deal_id",
    "usage_events": "event_id",
    "support_tickets": "ticket_id",
}
OPEN_STAGES = {
    "appointmentscheduled",
    "qualifiedtobuy",
    "presentationscheduled",
    "decisionmakerboughtin",
    "contractsent",
}
CLOSED_STAGES = {"closedwon", "closedlost"}
LIFECYCLE_STAGES = {
    "subscriber",
    "lead",
    "marketingqualifiedlead",
    "salesqualifiedlead",
    "opportunity",
    "customer",
}
SOURCES = {
    "ORGANIC_SEARCH",
    "PAID_SEARCH",
    "EMAIL_MARKETING",
    "SOCIAL_MEDIA",
    "REFERRALS",
    "DIRECT_TRAFFIC",
    "OFFLINE",
}
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
ALL_IDS = [f"D{i:02d}" for i in range(1, 24)]

Tables = dict[str, list[dict[str, str]]]


# ---------------------------------------------------------------- helpers


def run(out: Path, seed: int = 42, scale: float = 1.0, defects: bool = False) -> Path:
    args = ["--seed", str(seed), "--scale", str(scale), "--out", str(out)]
    if defects:
        args.append("--defects")
    assert generate.main(args) == 0
    return out


def load(out: Path) -> Tables:
    tables = {}
    for name in TABLES:
        with (out / f"{name}.csv").open(newline="", encoding="utf-8") as f:
            tables[name] = list(csv.DictReader(f))
    return tables


def load_manifest(out: Path) -> dict:
    return json.loads((out / "defects_manifest.json").read_text(encoding="utf-8"))


def day(value: str) -> date:
    return date.fromisoformat(value[:10])


def ts(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")


def is_open(deal: dict[str, str]) -> bool:
    return deal["stage"] in OPEN_STAGES


# ---------------------------------------------------------------- detection checks


def check_d01(t: Tables) -> set[str]:
    groups = defaultdict(list)
    for c in t["contacts"]:
        groups[c["email"].strip().lower()].append(c["contact_id"])
    return {cid for ids in groups.values() if len(ids) > 1 for cid in sorted(ids)[1:]}


def check_d02(t: Tables) -> set[str]:
    company_ids = {c["company_id"] for c in t["companies"]}
    return {c["contact_id"] for c in t["contacts"] if c["company_id"] not in company_ids}


def check_d03(t: Tables) -> set[str]:
    return {c["contact_id"] for c in t["contacts"] if not EMAIL_RE.match(c["email"])}


def check_d04(t: Tables) -> set[str]:
    return {c["company_id"] for c in t["companies"] if not c["industry"] or not c["country"]}


def check_d05(t: Tables) -> set[str]:
    churned = {c["company_id"]: day(c["churned_at"]) for c in t["companies"] if c["churned_at"]}
    return {
        e["company_id"]
        for e in t["usage_events"]
        if e["company_id"] in churned and day(e["event_ts"]) > churned[e["company_id"]]
    }


def check_d06(t: Tables) -> set[str]:
    return {
        d["deal_id"] for d in t["deals"] if d["stage"] == "closedwon" and d["amount"] in ("", "0")
    }


def check_d07(t: Tables) -> set[str]:
    return {
        d["deal_id"] for d in t["deals"] if d["amount"] and float(d["amount"]) > MAX_DEAL_AMOUNT
    }


def check_d08(t: Tables) -> set[str]:
    return {
        d["deal_id"]
        for d in t["deals"]
        if d["stage"] in CLOSED_STAGES and day(d["close_date"]) < day(d["created_at"])
    }


def check_d09(t: Tables) -> set[str]:
    return {d["deal_id"] for d in t["deals"] if d["stage"] not in OPEN_STAGES | CLOSED_STAGES}


def check_d10(t: Tables) -> set[str]:
    active = {o["owner_id"] for o in t["owners"] if o["active"] == "true"}
    return {d["deal_id"] for d in t["deals"] if d["owner_id"] and d["owner_id"] not in active}


def check_d11(t: Tables) -> set[str]:
    counts = Counter(e["event_id"] for e in t["usage_events"])
    return {event_id for event_id, n in counts.items() if n > 1}


def check_d12(t: Tables) -> set[str]:
    return {e["event_id"] for e in t["usage_events"] if day(e["event_ts"]) > AS_OF}


def check_d13(t: Tables) -> set[str]:
    eligible = {
        c["company_id"]
        for c in t["companies"]
        if not c["churned_at"] and day(c["created_at"]) < EVENT_WINDOW_START
    }
    active_days = defaultdict(set)
    for e in t["usage_events"]:
        if e["company_id"] in eligible:
            active_days[e["company_id"]].add(day(e["event_ts"]))
    window = [
        EVENT_WINDOW_START + timedelta(days=i) for i in range((AS_OF - EVENT_WINDOW_START).days + 1)
    ]
    found = set()
    for company_id, days in active_days.items():
        run_length = 0
        for d in window:
            run_length = 0 if d in days else run_length + 1
            if run_length >= 3:
                found.add(company_id)
                break
    return found


def check_d14(t: Tables) -> set[str]:
    return {
        k["ticket_id"]
        for k in t["support_tickets"]
        if k["closed_at"] and ts(k["closed_at"]) < ts(k["opened_at"])
    }


def check_d15(t: Tables) -> set[str]:
    contact_company = {c["contact_id"]: c["company_id"] for c in t["contacts"]}
    return {
        k["ticket_id"]
        for k in t["support_tickets"]
        if contact_company.get(k["contact_id"]) != k["company_id"]
    }


def check_d16(t: Tables) -> set[str]:
    return {c["contact_id"] for c in t["contacts"] if not c["lifecycle_stage"]}


def check_d17(t: Tables) -> set[str]:
    contacts = {c["contact_id"] for c in t["contacts"] if not c["owner_id"]}
    deals = {d["deal_id"] for d in t["deals"] if not d["owner_id"]}
    return contacts | deals


def check_d18(t: Tables) -> set[str]:
    groups = defaultdict(list)
    for c in t["companies"]:
        groups[c["domain"].lower()].append(c["company_id"])
    return {cid for ids in groups.values() if len(ids) > 1 for cid in sorted(ids)[1:]}


def check_d19(t: Tables) -> set[str]:
    return {d["deal_id"] for d in t["deals"] if not d["company_id"]}


def check_d20(t: Tables) -> set[str]:
    return {d["deal_id"] for d in t["deals"] if is_open(d) and day(d["close_date"]) < AS_OF}


def check_d21(t: Tables) -> set[str]:
    return {
        d["deal_id"]
        for d in t["deals"]
        if is_open(d) and (AS_OF - day(d["stage_entered_at"])).days > STALE_STAGE_DAYS
    }


def check_d22(t: Tables) -> set[str]:
    return {
        c["contact_id"]
        for c in t["contacts"]
        if day(c["created_at"]) >= SOURCE_REQUIRED_FROM and not c["source"]
    }


def check_d23(t: Tables) -> set[str]:
    return {d["deal_id"] for d in t["deals"] if d["stage"] == "contractsent" and not d["amount"]}


CHECKS: dict[str, Callable[[Tables], set[str]]] = {
    defect_id: globals()[f"check_{defect_id.lower()}"] for defect_id in ALL_IDS
}


# ---------------------------------------------------------------- fixtures


@pytest.fixture(scope="session")
def clean_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return run(tmp_path_factory.mktemp("clean"))


@pytest.fixture(scope="session")
def defect_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return run(tmp_path_factory.mktemp("defects"), defects=True)


@pytest.fixture(scope="session")
def clean(clean_dir: Path) -> Tables:
    return load(clean_dir)


@pytest.fixture(scope="session")
def dirty(defect_dir: Path) -> Tables:
    return load(defect_dir)


@pytest.fixture(scope="session")
def manifest(defect_dir: Path) -> dict:
    return load_manifest(defect_dir)


# ---------------------------------------------------------------- determinism


def test_same_seed_gives_identical_files(defect_dir: Path, tmp_path: Path) -> None:
    again = run(tmp_path, defects=True)
    names = sorted(p.name for p in defect_dir.iterdir())
    assert names == sorted(p.name for p in again.iterdir())
    for name in names:
        assert (defect_dir / name).read_bytes() == (again / name).read_bytes(), name


def test_different_seed_gives_different_data(clean_dir: Path, tmp_path: Path) -> None:
    other = run(tmp_path, seed=7)
    assert (clean_dir / "contacts.csv").read_bytes() != (other / "contacts.csv").read_bytes()


def test_csv_uses_lf_line_endings(clean_dir: Path) -> None:
    assert b"\r\n" not in (clean_dir / "companies.csv").read_bytes()


# ---------------------------------------------------------------- clean data


def test_clean_row_counts(clean: Tables) -> None:
    assert len(clean["owners"]) == 12
    assert len(clean["companies"]) == 200
    assert 800 <= len(clean["contacts"]) <= 1200
    assert 450 <= len(clean["deals"]) <= 750
    assert 15_000 <= len(clean["usage_events"]) <= 30_000
    assert 1_000 <= len(clean["support_tickets"]) <= 2_000


def test_clean_run_writes_metadata_and_no_manifest(clean_dir: Path) -> None:
    meta = json.loads((clean_dir / "metadata.json").read_text(encoding="utf-8"))
    assert meta["seed"] == 42
    assert meta["as_of"] == AS_OF.isoformat()
    assert meta["defects"] is False
    assert not (clean_dir / "defects_manifest.json").exists()


@pytest.mark.parametrize("table", TABLES)
def test_clean_primary_keys_unique(clean: Tables, table: str) -> None:
    keys = [row[PRIMARY_KEYS[table]] for row in clean[table]]
    assert len(keys) == len(set(keys))


def test_clean_foreign_keys_resolve(clean: Tables) -> None:
    active_owners = {o["owner_id"] for o in clean["owners"] if o["active"] == "true"}
    companies = {c["company_id"] for c in clean["companies"]}
    contacts = {c["contact_id"] for c in clean["contacts"]}
    assert {c["owner_id"] for c in clean["companies"]} <= active_owners
    assert {c["owner_id"] for c in clean["contacts"]} <= active_owners
    assert {d["owner_id"] for d in clean["deals"]} <= active_owners
    assert {c["company_id"] for c in clean["contacts"]} <= companies
    assert {d["company_id"] for d in clean["deals"]} <= companies
    assert {d["primary_contact_id"] for d in clean["deals"]} <= contacts
    assert {e["company_id"] for e in clean["usage_events"]} <= companies
    assert {e["contact_id"] for e in clean["usage_events"]} <= contacts
    assert {k["company_id"] for k in clean["support_tickets"]} <= companies
    assert {k["contact_id"] for k in clean["support_tickets"]} <= contacts


def test_clean_emails_valid_unique_and_reserved_domain(clean: Tables) -> None:
    emails = [c["email"] for c in clean["contacts"]]
    assert all(EMAIL_RE.match(e) for e in emails)
    assert all(e.endswith(".example") for e in emails)
    assert len({e.lower() for e in emails}) == len(emails)
    assert all(c["domain"].endswith(".example") for c in clean["companies"])


def test_clean_timestamps_not_after_as_of(clean: Tables) -> None:
    fields = {
        "companies": ["created_at", "churned_at"],
        "contacts": ["created_at"],
        "deals": ["created_at", "stage_entered_at", "closed_at"],
        "usage_events": ["event_ts"],
        "support_tickets": ["opened_at", "first_response_at", "closed_at"],
    }
    for table, columns in fields.items():
        for row in clean[table]:
            for column in columns:
                if row[column]:
                    assert day(row[column]) <= AS_OF, (table, column, row)


def test_clean_picklists(clean: Tables) -> None:
    assert {d["stage"] for d in clean["deals"]} <= OPEN_STAGES | CLOSED_STAGES
    assert {c["lifecycle_stage"] for c in clean["contacts"]} <= LIFECYCLE_STAGES
    assert {c["source"] for c in clean["contacts"]} <= SOURCES | {""}
    assert {d["currency"] for d in clean["deals"]} == {"USD"}


def test_clean_has_no_healthcare_industry(clean: Tables) -> None:
    banned = {"health", "healthcare", "medical", "pharma", "pharmaceuticals", "hospital", "clinic"}
    for company in clean["companies"]:
        assert banned.isdisjoint(company["industry"].lower().split())


def test_clean_includes_false_positive_traps(clean: Tables) -> None:
    """Blank values that are allowed, so a monitor that flags every blank is wrong."""
    assert any(
        not c["source"] and day(c["created_at"]) < SOURCE_REQUIRED_FROM for c in clean["contacts"]
    )
    assert any(
        is_open(d) and d["stage"] != "contractsent" and not d["amount"] for d in clean["deals"]
    )


@pytest.mark.parametrize("defect_id", ALL_IDS)
def test_clean_data_has_no_defects(clean: Tables, defect_id: str) -> None:
    assert CHECKS[defect_id](clean) == set()


# ---------------------------------------------------------------- planted defects


def test_registry_has_all_defects_in_order() -> None:
    assert [d.id for d in generate.DEFECTS] == ALL_IDS


def test_manifest_lists_all_defects(manifest: dict) -> None:
    assert [d["id"] for d in manifest["defects"]] == ALL_IDS
    assert manifest["seed"] == 42
    assert manifest["as_of"] == AS_OF.isoformat()


@pytest.mark.parametrize("defect_id", ALL_IDS)
def test_defect_detected_exactly(dirty: Tables, manifest: dict, defect_id: str) -> None:
    entry = next(d for d in manifest["defects"] if d["id"] == defect_id)
    registry = next(d for d in generate.DEFECTS if d.id == defect_id)
    assert entry["count"] == registry.count
    assert entry["hubspot_plantable"] == registry.hubspot_plantable
    assert len(entry["record_ids"]) == len(set(entry["record_ids"])) == registry.count
    assert CHECKS[defect_id](dirty) == set(entry["record_ids"])


def test_d05_events_fall_after_churn(dirty: Tables, manifest: dict) -> None:
    entry = next(d for d in manifest["defects"] if d["id"] == "D05")
    churned = {c["company_id"]: day(c["churned_at"]) for c in dirty["companies"] if c["churned_at"]}
    events = {e["event_id"]: e for e in dirty["usage_events"]}
    assert len(entry["event_ids"]) == 4 * entry["count"]
    for event_id in entry["event_ids"]:
        event = events[event_id]
        assert event["company_id"] in entry["record_ids"]
        assert day(event["event_ts"]) > churned[event["company_id"]]


def test_d13_gap_is_three_days(dirty: Tables, manifest: dict) -> None:
    entry = next(d for d in manifest["defects"] if d["id"] == "D13")
    start = date.fromisoformat(entry["window_start"])
    end = date.fromisoformat(entry["window_end"])
    assert (end - start).days == 2
    for event in dirty["usage_events"]:
        if event["company_id"] in entry["record_ids"]:
            assert not start <= day(event["event_ts"]) <= end


def test_d18_pairs_share_a_domain(dirty: Tables, manifest: dict) -> None:
    entry = next(d for d in manifest["defects"] if d["id"] == "D18")
    domains = {c["company_id"]: c["domain"] for c in dirty["companies"]}
    assert len(entry["pairs"]) == entry["count"]
    for original, duplicate in entry["pairs"]:
        assert domains[original] == domains[duplicate]


@pytest.mark.parametrize(("seed", "scale"), [(1, 0.5), (7, 1.5)])
def test_counts_fixed_across_seeds_and_scales(tmp_path: Path, seed: int, scale: float) -> None:
    out = run(tmp_path, seed=seed, scale=scale, defects=True)
    tables, manifest = load(out), load_manifest(out)
    expected = {d.id: d.count for d in generate.DEFECTS}
    assert {d["id"]: d["count"] for d in manifest["defects"]} == expected
    for entry in manifest["defects"]:
        assert CHECKS[entry["id"]](tables) == set(entry["record_ids"]), entry["id"]


# ---------------------------------------------------------------- DEFECTS.md


def parse_defects_md() -> dict[str, dict[str, object]]:
    rows = {}
    for line in DEFECTS_MD.read_text(encoding="utf-8").splitlines():
        if not re.match(r"^\|\s*D\d{2}\s*\|", line):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        defect_id, _what, _types, count, plantable, notes = cells[:6]
        rows[defect_id] = {
            "count": int(re.match(r"\d+", count).group()),
            "hubspot_plantable": plantable.lower().startswith("yes"),
            "to_confirm": "to confirm" in notes.lower(),
        }
    return rows


def test_defects_md_matches_registry() -> None:
    documented = parse_defects_md()
    assert list(documented) == ALL_IDS
    for defect in generate.DEFECTS:
        assert documented[defect.id]["count"] == defect.count, defect.id
        assert documented[defect.id]["hubspot_plantable"] == defect.hubspot_plantable, defect.id
        assert documented[defect.id]["to_confirm"] == ("to confirm" in defect.hubspot_note), (
            defect.id
        )


def test_d08_is_confirmed_by_probe_p1_and_d10_d14_stay_unconfirmed() -> None:
    notes = {d.id: d.hubspot_note for d in generate.DEFECTS}
    assert "to confirm" not in notes["D08"]
    assert "P1" in notes["D08"]
    assert "to confirm" in notes["D10"]
    assert "to confirm" in notes["D14"]


# ---------------------------------------------------------------- CLI


def test_cli_smoke(tmp_path: Path) -> None:
    result = subprocess.run(
        [sys.executable, str(GENERATE), "--out", str(tmp_path), "--defects", "--scale", "0.5"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "defects_manifest.json").exists()
    assert "D23" in result.stdout


def test_cli_rejects_scale_out_of_range(tmp_path: Path) -> None:
    result = subprocess.run(
        [sys.executable, str(GENERATE), "--out", str(tmp_path), "--scale", "0.1"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2
    assert "scale" in result.stderr
