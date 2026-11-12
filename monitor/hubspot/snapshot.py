"""Read the HubSpot developer test account into a Snapshot (milestone 5). Read-only.

Every unarchived company, contact and deal is read, not only seeded ones:
- Record IDs are `northwind_id` when it is set, otherwise `HS-<HubSpot ID>`.
- Seeded records (with `northwind_id`) take the generator values kept in `northwind_*`
  properties. Other records take HubSpot's own `createdate` and `hs_analytics_source`. There is
  no fallback between the two: HubSpot fills Original source on API-created contacts, which
  would hide every D22 contact.
- Owner names and emails are never read into the snapshot; the test account's owner is a real
  person and no check uses names.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime

from monitor.hubspot.client import API_VERSION, HubSpotClient
from monitor.hubspot.guard import check_portal
from monitor.hubspot.properties import ID_PROPERTY
from monitor.hubspot.seed import INDUSTRY_LABELS
from monitor.params import Params
from monitor.snapshot import COLUMNS, PRIMARY_KEYS, Rows, Snapshot, SnapshotError

SOURCE_REF = "HubSpot developer test account"
PIPELINE = "default"
TS_FORMAT = "%Y-%m-%dT%H:%M:%SZ"

OBJECT_PROPERTIES: dict[str, tuple[str, ...]] = {
    "companies": (
        ID_PROPERTY, "name", "domain", "industry", "country", "hubspot_owner_id", "createdate",
        "northwind_created_at", "northwind_employee_band", "northwind_plan_tier",
        "northwind_churned_at",
    ),
    "contacts": (
        ID_PROPERTY, "firstname", "lastname", "email", "jobtitle", "lifecyclestage",
        "hubspot_owner_id", "createdate", "hs_analytics_source", "northwind_created_at",
        "northwind_source",
    ),
    "deals": (
        ID_PROPERTY, "dealstage", "amount", "deal_currency_code", "closedate", "hubspot_owner_id",
        "createdate", "northwind_created_at", "northwind_stage_entered_at", "northwind_closed_at",
    ),
}  # fmt: skip
# (from, to) -> the HUBSPOT_DEFINED type ID HubSpot labels "Primary". Deals have no primary contact.
ASSOCIATIONS = (("contacts", "companies"), ("deals", "companies"), ("deals", "contacts"))
PRIMARY_TYPE_IDS = {("contacts", "companies"): 1, ("deals", "companies"): 5}


@dataclass(frozen=True)
class RawHubSpot:
    """What the API returned, before mapping."""

    owners: list[dict]
    archived_owners: list[dict]
    objects: dict[str, list[dict]]  # type -> [{id, properties}]
    associations: dict[tuple[str, str], dict[str, list[dict]]]  # (from, to) -> {from id: [to]}
    industry_options: list[dict]


# ---------------------------------------------------------------- value formats


def _parse(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def normalize_timestamp(value: str | None) -> str:
    """HubSpot's ISO 8601 (with or without milliseconds) as the generator's whole-second UTC."""
    if not value:
        return ""
    return _parse(value).strftime(TS_FORMAT)


def date_part(value: str | None) -> str:
    """The UTC date of a date or datetime value, as YYYY-MM-DD."""
    if not value:
        return ""
    if len(value) == 10:
        return date.fromisoformat(value).isoformat()
    return _parse(value).date().isoformat()


# ---------------------------------------------------------------- reading


def check_stages(pipelines: list[dict], params: Params) -> None:
    """The checks label open and closed deals by stage ID; stop if the portal's differ."""
    pipeline = next((p for p in pipelines if p.get("id") == PIPELINE), None)
    if pipeline is None:
        raise SnapshotError(f"the portal has no deal pipeline with id '{PIPELINE}'")
    stage_ids = {s.get("id") for s in pipeline.get("stages") or []}
    missing = [s for s in (*params.open_stages, *params.closed_stages) if s not in stage_ids]
    if missing:
        raise SnapshotError(
            f"deal pipeline '{PIPELINE}' has no stage {', '.join(missing)}; "
            "checks/params.yaml lists stages the portal doesn't have"
        )


def fetch(client: HubSpotClient, portal_id: str, params: Params) -> RawHubSpot:
    """Every request a snapshot needs, guard first. Only GETs and batch reads."""
    check_portal(client, portal_id)
    pipelines = client.get(f"/crm/pipelines/{API_VERSION}/deals") or {}
    check_stages(pipelines.get("results") or [], params)
    owners_path = f"/crm/owners/{API_VERSION}"
    owners = (client.get(owners_path, params={"archived": "false"}) or {}).get("results") or []
    archived = (client.get(owners_path, params={"archived": "true"}) or {}).get("results") or []
    industry = client.get(f"/crm/properties/{API_VERSION}/companies/industry") or {}
    objects = {t: client.list_objects(t, props) for t, props in OBJECT_PROPERTIES.items()}
    associations = {
        (a, b): client.read_associations(a, b, [r["id"] for r in objects[a]])
        for a, b in ASSOCIATIONS
    }
    return RawHubSpot(owners, archived, objects, associations, industry.get("options") or [])


def read_hubspot_snapshot(
    client: HubSpotClient, portal_id: str, as_of: date, params: Params
) -> Snapshot:
    return Snapshot(
        "hubspot", SOURCE_REF, as_of, rows_from_hubspot(fetch(client, portal_id, params))
    )


# ---------------------------------------------------------------- mapping


def industry_labels(options: list[Mapping]) -> dict[str, str]:
    """HubSpot industry value -> generator label, or the HubSpot label outside the map."""
    to_generator = {label: name for name, label in INDUSTRY_LABELS.items()}
    return {
        str(o["value"]): to_generator.get(str(o["label"]), str(o["label"]))
        for o in options
        if o.get("value")
    }


def _record_id(record: Mapping) -> str:
    props = record.get("properties") or {}
    return props.get(ID_PROPERTY) or f"HS-{record['id']}"


def _pick(targets: list[dict], ids: Mapping[str, str], primary_type: int | None) -> str:
    """The primary target if one is labelled, otherwise the lowest record ID."""
    known = [t for t in targets if str(t.get("toObjectId")) in ids]
    primary = [
        t
        for t in known
        if any(
            a.get("category") == "HUBSPOT_DEFINED"
            and (a.get("typeId") == primary_type or a.get("label") == "Primary")
            for a in t.get("associationTypes") or []
        )
    ]
    candidates = sorted(ids[str(t["toObjectId"])] for t in (primary or known))
    return candidates[0] if candidates else ""


def rows_from_hubspot(raw: RawHubSpot) -> dict[str, Rows]:
    ids = {t: {str(r["id"]): _record_id(r) for r in raw.objects[t]} for t in raw.objects}
    industries = industry_labels(raw.industry_options)

    def linked(from_type: str, to_type: str, hs_id: str) -> str:
        targets = raw.associations.get((from_type, to_type), {}).get(str(hs_id), [])
        return _pick(targets, ids[to_type], PRIMARY_TYPE_IDS.get((from_type, to_type)))

    def text(props: Mapping, name: str) -> str:
        value = props.get(name)
        return "" if value is None else str(value)

    def created(props: Mapping) -> str:
        seeded = bool(props.get(ID_PROPERTY))
        return normalize_timestamp(props.get("northwind_created_at" if seeded else "createdate"))

    tables: dict[str, Rows] = {
        "owners": [
            {"owner_id": str(o["id"]), "name": "", "active": active}
            for active, group in (("true", raw.owners), ("false", raw.archived_owners))
            for o in group
        ],
        "companies": [],
        "contacts": [],
        "deals": [],
    }
    for r in raw.objects["companies"]:
        p = r.get("properties") or {}
        value = text(p, "industry")
        tables["companies"].append(
            {
                "company_id": _record_id(r),
                "name": text(p, "name"),
                "domain": text(p, "domain"),
                "industry": industries.get(value, value),
                "country": text(p, "country"),
                "employee_band": text(p, "northwind_employee_band"),
                "plan_tier": text(p, "northwind_plan_tier"),
                "owner_id": text(p, "hubspot_owner_id"),
                "created_at": created(p),
                "churned_at": date_part(p.get("northwind_churned_at")),  # a date in the generator
            }
        )
    for r in raw.objects["contacts"]:
        p = r.get("properties") or {}
        seeded = bool(p.get(ID_PROPERTY))
        tables["contacts"].append(
            {
                "contact_id": _record_id(r),
                "company_id": linked("contacts", "companies", r["id"]),
                "first_name": text(p, "firstname"),
                "last_name": text(p, "lastname"),
                "email": text(p, "email"),
                "job_title": text(p, "jobtitle"),
                "lifecycle_stage": text(p, "lifecyclestage"),
                "owner_id": text(p, "hubspot_owner_id"),
                "source": text(p, "northwind_source" if seeded else "hs_analytics_source"),
                "created_at": created(p),
            }
        )
    for r in raw.objects["deals"]:
        p = r.get("properties") or {}
        tables["deals"].append(
            {
                "deal_id": _record_id(r),
                "company_id": linked("deals", "companies", r["id"]),
                "primary_contact_id": linked("deals", "contacts", r["id"]),
                "owner_id": text(p, "hubspot_owner_id"),
                "stage": text(p, "dealstage"),
                "amount": text(p, "amount"),
                "currency": text(p, "deal_currency_code"),
                "created_at": created(p),
                "stage_entered_at": normalize_timestamp(p.get("northwind_stage_entered_at")),
                "close_date": date_part(p.get("closedate")),
                "closed_at": normalize_timestamp(p.get("northwind_closed_at")),
            }
        )
    for table, rows in tables.items():
        assert all(list(row) == COLUMNS[table] for row in rows), table
        rows.sort(key=lambda row: row[PRIMARY_KEYS[table]])
    return tables
