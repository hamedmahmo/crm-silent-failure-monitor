"""Seed Northwind day 1 into the HubSpot developer test account.

build_seed() is pure: it decides which records go in, moves the dates that checks compare with
the as-of date, and builds the request payloads. apply_seed() sends them, verify_seed() checks
the portal afterwards, and write_seed_output() saves the answer key that milestone 5 scores
against.

Date rule (docs/decisions.md, 2026-11-12): only open deals' close_date and stage_entered_at move
forward by N days, N = seed day - 2026-06-30. Everything else keeps its generated value.
"""

from __future__ import annotations

import copy
import csv
import json
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

from monitor.hubspot.client import OWNER_ENV, HubSpotClient, HubSpotError
from monitor.hubspot.guard import check_portal
from monitor.hubspot.properties import ID_PROPERTY, ensure_properties
from monitor.scoring import in_scope_defect_ids
from monitor.snapshot import COLUMNS, PRIMARY_KEYS, SCOPE_TABLES

BASE_DAY = date(2026, 6, 30)  # the generator's AS_OF
SEED_OBJECTS = ("companies", "contacts", "deals")
TS_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
VERIFY_ATTEMPTS = 6
VERIFY_WAIT = 5.0

# Generator industry -> label of HubSpot's default `industry` option. The value sent is looked
# up from the live option list, so a label HubSpot doesn't have stops the seed before any write.
INDUSTRY_LABELS = {
    "Software": "Computer Software",
    "Logistics": "Logistics and Supply Chain",
    "Retail": "Retail",
    "Manufacturing": "Machinery",
    "Financial Services": "Financial Services",
    "Education": "Education Management",
    "Media": "Media Production",
    "Energy": "Oil & Energy",
    "Construction": "Construction",
    "Hospitality": "Hospitality",
    "Agriculture": "Farming",
    "Telecommunications": "Telecommunications",
}

Rows = list[dict[str, str]]


class SeedError(HubSpotError):
    """The seed can't be built or the portal isn't as expected after seeding."""


@dataclass(frozen=True)
class SeedPlan:
    seed: int
    seed_day: date
    shift_days: int
    owner_id: str
    tables: dict[str, Rows]  # generator columns, after skipping and date moves (incl. owners)
    manifest: dict  # plantable in-scope defects only, as_of = seed day
    skipped: dict[str, list[str]]
    inputs: dict[str, list[dict]]  # batch upsert inputs per object type
    associations: list[tuple[str, str, str, str]]  # (from type, to type, from id, to id)


@dataclass(frozen=True)
class SeedResult:
    created: dict[str, int]
    updated: dict[str, int]
    associations: int
    hubspot_ids: dict[str, dict[str, str]]  # object type -> northwind_id -> HubSpot ID


# ---------------------------------------------------------------- choices made before seeding


def skipped_defect_ids(manifest: Mapping) -> list[str]:
    """In-scope defects HubSpot rejects, so their records are not seeded."""
    entries = {e["id"]: e for e in manifest["defects"]}
    return [
        d for d in in_scope_defect_ids(manifest["defects"]) if not entries[d]["hubspot_plantable"]
    ]


def industry_map(options: list[Mapping]) -> dict[str, str]:
    """Generator industry -> HubSpot option value, matched on the option label."""
    by_label = {str(o["label"]).casefold(): str(o["value"]) for o in options}
    missing = [g for g, label in INDUSTRY_LABELS.items() if label.casefold() not in by_label]
    if missing:
        raise SeedError(
            "HubSpot's industry options have no match for: "
            + ", ".join(f"{g} ({INDUSTRY_LABELS[g]})" for g in missing)
        )
    return {g: by_label[label.casefold()] for g, label in INDUSTRY_LABELS.items()}


def resolve_owner(owners: list[Mapping], configured: str | None = None) -> str:
    """The HubSpot owner every Northwind owner maps to (decision 2026-11-11)."""
    active = [str(o["id"]) for o in owners if not o.get("archived")]
    if configured:
        if configured not in active:
            raise SeedError(f"{OWNER_ENV} is not an active owner in this portal")
        return configured
    if not active:
        raise SeedError("the portal has no active owner to assign records to")
    if len(active) > 1:
        raise SeedError(f"the portal has {len(active)} active owners; set {OWNER_ENV} to one")
    return active[0]


# ---------------------------------------------------------------- build (pure)


def _shift_day(value: str, days: int) -> str:
    return (date.fromisoformat(value) + timedelta(days=days)).isoformat()


def _shift_ts(value: str, days: int) -> str:
    return (datetime.strptime(value, TS_FORMAT) + timedelta(days=days)).strftime(TS_FORMAT)


def _props(pairs: list[tuple[str, str]]) -> dict[str, str]:
    """Leave blank values out, so a blank field stays empty in HubSpot."""
    return {name: value for name, value in pairs if value}


def build_seed(
    tables: Mapping[str, Rows],
    manifest: Mapping,
    seed_day: date,
    owner_id: str,
    industries: Mapping[str, str],
    open_stages: tuple[str, ...] | None = None,
) -> SeedPlan:
    if open_stages is None:
        from monitor.params import load_params

        open_stages = load_params().open_stages
    shift = (seed_day - BASE_DAY).days
    skip_defects = set(skipped_defect_ids(manifest))
    skipped_ids = {
        rid for e in manifest["defects"] if e["id"] in skip_defects for rid in e["record_ids"]
    }

    seeded: dict[str, Rows] = {}
    skipped: dict[str, list[str]] = {}
    for table in SCOPE_TABLES:
        key = PRIMARY_KEYS[table]
        rows = copy.deepcopy(list(tables[table]))
        skipped[table] = [r[key] for r in rows if r[key] in skipped_ids]
        seeded[table] = [r for r in rows if r[key] not in skipped_ids]
    skipped.pop("owners")

    for deal in seeded["deals"]:
        if deal["stage"] in open_stages:
            if deal["close_date"]:
                deal["close_date"] = _shift_day(deal["close_date"], shift)
            if deal["stage_entered_at"]:
                deal["stage_entered_at"] = _shift_ts(deal["stage_entered_at"], shift)

    in_scope = set(in_scope_defect_ids(manifest["defects"]))
    live_manifest = {
        "seed": manifest.get("seed"),
        "as_of": seed_day.isoformat(),
        "source": "hubspot seed",
        "defects": [
            copy.deepcopy(e)
            for e in manifest["defects"]
            if e["id"] in in_scope and e["hubspot_plantable"]
        ],
    }

    def owner(row: Mapping[str, str]) -> str:
        return owner_id if row["owner_id"] else ""

    company_names = {c["company_id"]: c["name"] for c in tables["companies"]}
    inputs: dict[str, list[dict]] = {
        "companies": [
            _input(
                c["company_id"],
                [
                    ("name", c["name"]),
                    ("domain", c["domain"]),
                    ("industry", industries[c["industry"]] if c["industry"] else ""),
                    ("country", c["country"]),
                    ("northwind_employee_band", c["employee_band"]),
                    ("northwind_plan_tier", c["plan_tier"]),
                    ("hubspot_owner_id", owner(c)),
                    ("northwind_created_at", c["created_at"]),
                    ("northwind_churned_at", c["churned_at"]),
                ],
            )
            for c in seeded["companies"]
        ],
        "contacts": [
            _input(
                c["contact_id"],
                [
                    ("firstname", c["first_name"]),
                    ("lastname", c["last_name"]),
                    ("email", c["email"]),
                    ("jobtitle", c["job_title"]),
                    ("lifecyclestage", c["lifecycle_stage"]),
                    ("hubspot_owner_id", owner(c)),
                    ("northwind_source", c["source"]),
                    ("northwind_created_at", c["created_at"]),
                ],
            )
            for c in seeded["contacts"]
        ],
        "deals": [
            _input(
                d["deal_id"],
                [
                    ("dealname", _deal_name(d, company_names)),
                    ("pipeline", "default"),
                    ("dealstage", d["stage"]),
                    ("amount", d["amount"]),
                    ("deal_currency_code", d["currency"]),
                    ("closedate", f"{d['close_date']}T00:00:00Z" if d["close_date"] else ""),
                    ("hubspot_owner_id", owner(d)),
                    ("northwind_created_at", d["created_at"]),
                    ("northwind_stage_entered_at", d["stage_entered_at"]),
                    ("northwind_closed_at", d["closed_at"]),
                ],
            )
            for d in seeded["deals"]
        ],
    }

    company_ids = {c["company_id"] for c in seeded["companies"]}
    contact_ids = {c["contact_id"] for c in seeded["contacts"]}
    associations = [
        ("contacts", "companies", c["contact_id"], c["company_id"])
        for c in seeded["contacts"]
        if c["company_id"] in company_ids
    ]
    associations += [
        ("deals", "companies", d["deal_id"], d["company_id"])
        for d in seeded["deals"]
        if d["company_id"] in company_ids
    ]
    associations += [
        ("deals", "contacts", d["deal_id"], d["primary_contact_id"])
        for d in seeded["deals"]
        if d["primary_contact_id"] in contact_ids
    ]

    return SeedPlan(
        seed=int(manifest.get("seed") or 0),
        seed_day=seed_day,
        shift_days=shift,
        owner_id=owner_id,
        tables=seeded,
        manifest=live_manifest,
        skipped=skipped,
        inputs=inputs,
        associations=associations,
    )


def _input(northwind_id: str, pairs: list[tuple[str, str]]) -> dict:
    return {"idProperty": ID_PROPERTY, "id": northwind_id, "properties": _props(pairs)}


def _deal_name(deal: Mapping[str, str], company_names: Mapping[str, str]) -> str:
    company = company_names.get(deal["company_id"])
    return f"{company} - {deal['deal_id']}" if company else f"Northwind {deal['deal_id']}"


# ---------------------------------------------------------------- apply (HTTP)


def apply_seed(client: HubSpotClient, plan: SeedPlan, portal_id: str) -> SeedResult:
    """Guard, create missing properties, upsert companies, contacts, deals, then associate."""
    check_portal(client, portal_id)
    ensure_properties(client)

    created: dict[str, int] = {}
    updated: dict[str, int] = {}
    hubspot_ids: dict[str, dict[str, str]] = {}
    for object_type in SEED_OBJECTS:
        results = client.batch_upsert(object_type, plan.inputs[object_type])
        created[object_type] = sum(1 for r in results if r.get("new"))
        updated[object_type] = len(results) - created[object_type]
        hubspot_ids[object_type] = _id_map(client, object_type, plan, results)

    pairs: dict[tuple[str, str], list[tuple[str, str]]] = {}
    for from_type, to_type, from_id, to_id in plan.associations:
        pairs.setdefault((from_type, to_type), []).append(
            (hubspot_ids[from_type][from_id], hubspot_ids[to_type][to_id])
        )
    total = sum(client.associate(f, t, p) for (f, t), p in pairs.items())
    return SeedResult(created, updated, total, hubspot_ids)


def _id_map(client: HubSpotClient, object_type: str, plan: SeedPlan, results: list[dict]) -> dict:
    ids = {
        r["properties"][ID_PROPERTY]: str(r["id"])
        for r in results
        if (r.get("properties") or {}).get(ID_PROPERTY)
    }
    wanted = [i["id"] for i in plan.inputs[object_type]]
    missing = [i for i in wanted if i not in ids]
    if missing:
        for r in client.batch_read(object_type, missing, ID_PROPERTY, [ID_PROPERTY]):
            ids[r["properties"][ID_PROPERTY]] = str(r["id"])
    still_missing = [i for i in wanted if i not in ids]
    if still_missing:
        raise SeedError(
            f"{len(still_missing)} {object_type} have no HubSpot ID after upsert, "
            f"e.g. {still_missing[0]}"
        )
    return ids


# ---------------------------------------------------------------- verify (HTTP)


def verify_seed(
    client: HubSpotClient,
    plan: SeedPlan,
    sleep: Callable[[float], None] | None = None,
    attempts: int = VERIFY_ATTEMPTS,
    wait: float = VERIFY_WAIT,
) -> list[str]:
    """Problems found in the portal after seeding; an empty list means it looks as planned.

    Search results lag behind writes by a few seconds, so counts are retried before they are
    reported.
    """
    sleep = sleep or time.sleep
    tagged = [{"propertyName": ID_PROPERTY, "operator": "HAS_PROPERTY"}]
    untagged = [{"propertyName": ID_PROPERTY, "operator": "NOT_HAS_PROPERTY"}]
    expected = {t: len(plan.inputs[t]) for t in SEED_OBJECTS}

    def count_problems() -> list[str]:
        problems = []
        for object_type in SEED_OBJECTS:
            found = client.count(object_type, tagged)
            if found != expected[object_type]:
                problems.append(
                    f"{object_type}: {found} records with {ID_PROPERTY}, "
                    f"expected {expected[object_type]}"
                )
        extra = client.count("companies", untagged)
        if extra:
            problems.append(
                f"companies: {extra} without {ID_PROPERTY}; is 'Create and associate companies "
                "with contacts' switched off?"
            )
        return problems

    problems = count_problems()
    for _ in range(attempts - 1):
        if not problems:
            break
        sleep(wait)
        problems = count_problems()

    [d16] = [e for e in plan.manifest["defects"] if e["id"] == "D16"] or [None]
    if d16 is not None:
        for record in client.batch_read(
            "contacts", d16["record_ids"], ID_PROPERTY, ["lifecyclestage"]
        ):
            stage = (record.get("properties") or {}).get("lifecyclestage")
            if stage:
                nw_id = record["properties"].get(ID_PROPERTY, record.get("id"))
                problems.append(
                    f"D16 contact {nw_id} has lifecycle stage {stage!r}; "
                    "is the lifecycle stage automation switched off?"
                )
    return problems


# ---------------------------------------------------------------- output files


def write_seed_output(
    out: Path, plan: SeedPlan, result: SeedResult, ran_at: datetime | None = None
) -> None:
    """Save the seed tables (a CSV snapshot), the live manifest and seed.json in `out`."""
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    for table in SCOPE_TABLES:
        with (out / f"{table}.csv").open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=COLUMNS[table], lineterminator="\n")
            writer.writeheader()
            writer.writerows(plan.tables[table])
    _write_json(out / "metadata.json", {"as_of": plan.seed_day.isoformat(), "seed": plan.seed})
    _write_json(out / "defects_manifest.json", plan.manifest)
    ran_at = ran_at or datetime.now().replace(microsecond=0)
    _write_json(
        out / "seed.json",
        {
            "seed": plan.seed,
            "seed_day": plan.seed_day.isoformat(),
            "shift_days": plan.shift_days,
            "owner_id": plan.owner_id,
            "ran_at": ran_at.isoformat(),
            "skipped": {t: len(ids) for t, ids in plan.skipped.items()},
            "created": result.created,
            "updated": result.updated,
            "associations": result.associations,
            "planted": sum(len(e["record_ids"]) for e in plan.manifest["defects"]),
        },
    )


def _write_json(path: Path, data: object) -> None:
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
