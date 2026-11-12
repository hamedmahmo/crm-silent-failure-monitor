"""Build a realistic "day 2" snapshot from day-1 Northwind output (data/generate.py --defects).

The story: one day after AS_OF, RevOps cleans up some issues, a web-form integration and a bad
import introduce new ones, and normal sales work changes a few clean records. The output folder
looks like generator output (CSVs, metadata.json, defects_manifest.json) with as_of moved
forward one day, plus changes.json, which records exactly what changed as ground truth.

Only the standard library is used. Randomness comes from random.Random(f"{seed}:day2").

Usage:
    uv run python data/simulate_day2.py --day1 data/output/day1 --out data/output/day2 [--seed 42]
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import shutil
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path

import generate
from generate import (
    ACTIVE_OWNER_IDS,
    AS_OF,
    CLOSED_STAGES,
    COLUMNS,
    COMPANY_ADJECTIVES,
    COMPANY_NOUNS,
    COMPANY_SUFFIXES,
    COUNTRIES,
    EMPLOYEE_BANDS,
    FIRST_NAMES,
    INDUSTRIES,
    JOB_TITLES,
    LAST_NAMES,
    LIFECYCLE_STAGES,
    OPEN_STAGES,
    PLAN_TIERS,
    SOURCES,
    Row,
    fmt_ts,
    random_amount,
    random_time_on,
)

DAY2 = AS_OF + timedelta(days=1)
EDITED_TABLES = ("companies", "contacts", "deals")
COPIED_FILES = ("owners.csv", "usage_events.csv", "support_tickets.csv")
KEYS = {"companies": "company_id", "contacts": "contact_id", "deals": "deal_id"}

# How many records each part of the story touches.
FIXES = {"D03": 4, "D04": 3, "D06": 3, "D09": 4, "D17": 4, "D20": 4}
DELETES = {"D18": 2, "D01": 3}
NEW = {"D22": 3, "D16": 2, "D07": 2, "D23": 2, "D04": 1}
BENIGN_NEW_CONTACTS = 5
BENIGN_STAGE_MOVES = 3
BENIGN_CLOSED_WON = 1


def read_table(folder: Path, table: str) -> list[Row]:
    with (folder / f"{table}.csv").open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def write_table(folder: Path, table: str, rows: list[Row]) -> None:
    with (folder / f"{table}.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS[table], lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


class Day2:
    """Applies the day-2 story to the day-1 tables and records every change."""

    def __init__(self, tables: dict[str, list[Row]], manifest: dict, rng: random.Random) -> None:
        self.tables = tables
        self.manifest = manifest
        self.rng = rng
        self.by_defect = {e["id"]: list(e["record_ids"]) for e in manifest["defects"]}
        planted = {rid for ids in self.by_defect.values() for rid in ids}
        self.used = set(planted)  # records the new defects and benign changes must not touch
        # ID makers are created before any deletion, so deleted IDs are never reused.
        self.make_id = {t: generate.next_id(tables[t], t) for t in EDITED_TABLES}
        self.fixed: list[dict[str, str]] = []
        self.deleted: list[dict[str, str]] = []
        self.new: list[dict[str, str]] = []
        self.added: dict[str, list[str]] = {t: [] for t in EDITED_TABLES}
        self.removed: dict[str, list[str]] = {t: [] for t in EDITED_TABLES}
        self.changed: dict[str, set[str]] = {t: set() for t in EDITED_TABLES}
        self.benign_deals: list[str] = []

    # ------------------------------------------------------------ helpers

    def index(self, table: str) -> dict[str, Row]:
        return {row[KEYS[table]]: row for row in self.tables[table]}

    def sample(self, ids: list[str], k: int) -> list[str]:
        return sorted(self.rng.sample(sorted(ids), k))

    def fix(self, defect_id: str, table: str, record_ids: list[str], apply: Callable) -> None:
        rows = self.index(table)
        for record_id in record_ids:
            apply(rows[record_id])
            self.fixed.append({"defect": defect_id, "record_id": record_id})
            self.changed[table].add(record_id)

    def delete(self, defect_id: str, table: str, record_ids: list[str]) -> None:
        doomed = set(record_ids)
        key = KEYS[table]
        self.tables[table] = [row for row in self.tables[table] if row[key] not in doomed]
        for record_id in record_ids:
            self.deleted.append({"defect": defect_id, "record_id": record_id})
            self.removed[table].append(record_id)

    def moment(self) -> str:
        return fmt_ts(random_time_on(self.rng, DAY2))

    def pick_untouched(self, table: str, k: int, where: Callable[[Row], bool]) -> list[Row]:
        key = KEYS[table]
        pool = [row for row in self.tables[table] if row[key] not in self.used and where(row)]
        chosen = self.rng.sample(pool, k)
        self.used.update(row[key] for row in chosen)
        return chosen

    def pick_company(self) -> Row:
        """An active company that no defect touches, for new contacts and deals."""
        return self.rng.choice(
            [
                c
                for c in self.tables["companies"]
                if not c["churned_at"] and c["company_id"] not in self.used
            ]
        )

    # ------------------------------------------------------------ fixes and deletions

    def apply_fixes(self) -> None:
        d03 = [
            rid
            for rid in self.by_defect["D03"]
            if "@@" in self.index("contacts")[rid]["email"]
            or self.index("contacts")[rid]["email"] != self.index("contacts")[rid]["email"].strip()
        ]
        assert len(d03) == FIXES["D03"], d03  # the missing-@ emails can't be repaired

        def fix_email(row: Row) -> None:
            row["email"] = row["email"].replace("@@", "@").strip()

        self.fix("D03", "contacts", sorted(d03), fix_email)

        def fill_firmographics(row: Row) -> None:
            row["industry"] = row["industry"] or self.rng.choice(INDUSTRIES)
            row["country"] = row["country"] or self.rng.choice(COUNTRIES)

        self.fix(
            "D04", "companies", self.sample(self.by_defect["D04"], FIXES["D04"]), fill_firmographics
        )

        def set_amount(row: Row) -> None:
            row["amount"] = random_amount(self.rng)

        self.fix("D06", "deals", self.sample(self.by_defect["D06"], FIXES["D06"]), set_amount)

        def normalize_stage(row: Row) -> None:
            row["stage"] = "closedwon"

        self.fix("D09", "deals", self.sample(self.by_defect["D09"], FIXES["D09"]), normalize_stage)

        owners = {c["company_id"]: c["owner_id"] for c in self.tables["companies"]}

        def assign_owner(row: Row) -> None:
            row["owner_id"] = owners[row["company_id"]]

        d17_deals = [rid for rid in self.by_defect["D17"] if rid.startswith("DEAL-")]
        self.fix("D17", "deals", self.sample(d17_deals, FIXES["D17"]), assign_owner)

        def push_close_date(row: Row) -> None:
            row["close_date"] = (DAY2 + timedelta(days=self.rng.randint(14, 60))).isoformat()

        self.fix("D20", "deals", self.sample(self.by_defect["D20"], FIXES["D20"]), push_close_date)

    def apply_deletions(self) -> None:
        # Merging duplicates deletes the newer record; nothing else references it.
        self.delete("D18", "companies", self.sample(self.by_defect["D18"], DELETES["D18"]))
        self.delete("D01", "contacts", self.sample(self.by_defect["D01"], DELETES["D01"]))

    # ------------------------------------------------------------ new records and defects

    def add_contact(self, defect_id: str | None, source: str | None, lifecycle: str | None) -> str:
        company = self.pick_company()
        emails = {c["email"].strip().lower() for c in self.tables["contacts"]}
        first, last = self.rng.choice(FIRST_NAMES), self.rng.choice(LAST_NAMES)
        local, n = f"{first}.{last}".lower(), 1
        while f"{local}@{company['domain']}" in emails:
            n += 1
            local = f"{first}.{last}{n}".lower()
        contact_id = self.make_id["contacts"]()
        self.tables["contacts"].append(
            {
                "contact_id": contact_id,
                "company_id": company["company_id"],
                "first_name": first,
                "last_name": last,
                "email": f"{local}@{company['domain']}",
                "job_title": self.rng.choice(JOB_TITLES),
                "lifecycle_stage": (
                    self.rng.choice(LIFECYCLE_STAGES) if lifecycle is None else lifecycle
                ),
                "owner_id": company["owner_id"],
                "source": self.rng.choice(SOURCES) if source is None else source,
                "created_at": self.moment(),
            }
        )
        self.used.add(contact_id)
        self.added["contacts"].append(contact_id)
        if defect_id:
            self.new.append({"defect": defect_id, "record_id": contact_id})
        return contact_id

    def add_contractsent_deal_without_amount(self) -> None:
        company = self.pick_company()
        contacts = [
            c
            for c in self.tables["contacts"]
            if c["company_id"] == company["company_id"] and c["contact_id"] not in self.used
        ]
        created = self.moment()
        deal_id = self.make_id["deals"]()
        self.tables["deals"].append(
            {
                "deal_id": deal_id,
                "company_id": company["company_id"],
                "primary_contact_id": self.rng.choice(contacts)["contact_id"],
                "owner_id": company["owner_id"],
                "stage": "contractsent",
                "amount": "",
                "currency": "USD",
                "created_at": created,
                "stage_entered_at": created,
                "close_date": (DAY2 + timedelta(days=self.rng.randint(14, 90))).isoformat(),
                "closed_at": "",
            }
        )
        self.used.add(deal_id)
        self.added["deals"].append(deal_id)
        self.new.append({"defect": "D23", "record_id": deal_id})

    def add_company_without_industry(self) -> None:
        domains = {c["domain"].lower() for c in self.tables["companies"]}
        pairs = [
            (a, b)
            for a in COMPANY_ADJECTIVES
            for b in COMPANY_NOUNS
            if f"{a}{b}.example".lower() not in domains
        ]
        adjective, noun = self.rng.choice(pairs)
        company_id = self.make_id["companies"]()
        self.tables["companies"].append(
            {
                "company_id": company_id,
                "name": f"{adjective} {noun} {self.rng.choice(COMPANY_SUFFIXES)}",
                "domain": f"{adjective}{noun}.example".lower(),
                "industry": "",
                "country": self.rng.choice(COUNTRIES),
                "employee_band": self.rng.choice(EMPLOYEE_BANDS),
                "plan_tier": self.rng.choice(PLAN_TIERS),
                "owner_id": self.rng.choice(ACTIVE_OWNER_IDS),
                "created_at": self.moment(),
                "churned_at": "",
            }
        )
        self.used.add(company_id)
        self.added["companies"].append(company_id)
        self.new.append({"defect": "D04", "record_id": company_id})

    def apply_new_defects(self) -> None:
        # A web-form integration drops the source field, then the lifecycle field.
        for _ in range(NEW["D22"]):
            self.add_contact("D22", source="", lifecycle=None)
        for _ in range(NEW["D16"]):
            self.add_contact("D16", source=None, lifecycle="")
        # A bulk import writes cents into the dollar amount of existing deals.
        valid = set(OPEN_STAGES) | set(CLOSED_STAGES)
        for deal in self.pick_untouched(
            "deals", NEW["D07"], lambda d: bool(d["amount"]) and d["stage"] in valid
        ):
            deal["amount"] = str(int(deal["amount"]) * 100)
            self.changed["deals"].add(deal["deal_id"])
            self.new.append({"defect": "D07", "record_id": deal["deal_id"]})
        for _ in range(NEW["D23"]):
            self.add_contractsent_deal_without_amount()
        for _ in range(NEW["D04"]):
            self.add_company_without_industry()

    # ------------------------------------------------------------ benign changes

    def apply_benign_changes(self) -> None:
        for _ in range(BENIGN_NEW_CONTACTS):
            self.add_contact(None, source=None, lifecycle=None)

        for deal in self.pick_untouched(
            "deals", BENIGN_STAGE_MOVES, lambda d: d["stage"] in OPEN_STAGES[:-1]
        ):
            deal["stage"] = OPEN_STAGES[OPEN_STAGES.index(deal["stage"]) + 1]
            deal["stage_entered_at"] = self.moment()
            if deal["stage"] in ("decisionmakerboughtin", "contractsent") and not deal["amount"]:
                deal["amount"] = random_amount(self.rng)
            self.benign_deals.append(deal["deal_id"])

        for deal in self.pick_untouched(
            "deals", BENIGN_CLOSED_WON, lambda d: d["stage"] in OPEN_STAGES
        ):
            closed = self.moment()
            deal["stage"] = "closedwon"
            deal["amount"] = deal["amount"] or random_amount(self.rng)
            deal["stage_entered_at"] = closed
            deal["closed_at"] = closed
            deal["close_date"] = DAY2.isoformat()
            self.benign_deals.append(deal["deal_id"])

        self.changed["deals"].update(self.benign_deals)

    # ------------------------------------------------------------ outputs

    def day2_manifest(self) -> dict:
        resolved: dict[str, set[str]] = {}
        for change in self.fixed + self.deleted:
            resolved.setdefault(change["defect"], set()).add(change["record_id"])
        added: dict[str, list[str]] = {}
        for change in self.new:
            added.setdefault(change["defect"], []).append(change["record_id"])
        entries = []
        for entry in self.manifest["defects"]:
            gone = resolved.get(entry["id"], set())
            record_ids = [r for r in entry["record_ids"] if r not in gone]
            record_ids += added.get(entry["id"], [])
            updated = dict(entry) | {"count": len(record_ids), "record_ids": record_ids}
            if "pairs" in entry:
                updated["pairs"] = [pair for pair in entry["pairs"] if pair[1] not in gone]
            entries.append(updated)
        return {"seed": self.manifest["seed"], "as_of": DAY2.isoformat(), "defects": entries}

    def changes(self, seed: int) -> dict:
        def by_defect(changes: list[dict[str, str]]) -> list[dict[str, str]]:
            return sorted(changes, key=lambda c: (c["defect"], c["record_id"]))

        return {
            "seed": seed,
            "as_of": DAY2.isoformat(),
            "fixed": by_defect(self.fixed),
            "deleted": by_defect(self.deleted),
            "new": by_defect(self.new),
            "added": {t: sorted(ids) for t, ids in self.added.items()},
            "removed": {t: sorted(ids) for t, ids in self.removed.items()},
            "changed": {t: sorted(ids) for t, ids in self.changed.items()},
            "benign": {"deals": sorted(self.benign_deals)},
        }


def simulate(day1: Path, out: Path, seed: int = 42) -> dict:
    """Write the day-2 folder and return the changes it records."""
    day1, out = Path(day1), Path(out)
    manifest = json.loads((day1 / "defects_manifest.json").read_text(encoding="utf-8"))
    metadata = json.loads((day1 / "metadata.json").read_text(encoding="utf-8"))
    tables = {table: read_table(day1, table) for table in EDITED_TABLES}

    day2 = Day2(tables, manifest, random.Random(f"{seed}:day2"))
    day2.apply_fixes()
    day2.apply_deletions()
    day2.apply_new_defects()
    day2.apply_benign_changes()

    out.mkdir(parents=True, exist_ok=True)
    for table in EDITED_TABLES:
        write_table(out, table, day2.tables[table])
    for name in COPIED_FILES:
        shutil.copyfile(day1 / name, out / name)
    row_counts = dict(metadata["row_counts"])
    row_counts.update({table: len(day2.tables[table]) for table in EDITED_TABLES})
    generate.write_json(
        out / "metadata.json",
        metadata | {"as_of": DAY2.isoformat(), "day": 2, "row_counts": row_counts},
    )
    generate.write_json(out / "defects_manifest.json", day2.day2_manifest())
    changes = day2.changes(seed)
    generate.write_json(out / "changes.json", changes)
    return changes


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--day1", type=Path, required=True, help="day-1 generator output folder")
    parser.add_argument("--out", type=Path, required=True, help="folder for the day-2 snapshot")
    parser.add_argument("--seed", type=int, default=42, help="random seed (default 42)")
    args = parser.parse_args(argv)
    changes = simulate(args.day1, args.out, args.seed)
    print(f"Wrote day-2 snapshot (as of {changes['as_of']}) to {args.out}")
    print(
        f"  fixed {len(changes['fixed'])}, deleted {len(changes['deleted'])}, "
        f"new defects {len(changes['new'])}, benign deal changes {len(changes['benign']['deals'])}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
