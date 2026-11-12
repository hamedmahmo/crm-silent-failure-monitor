"""Probes P1-P5: check the HubSpot behaviour the seed depends on, before seeding.

Each probe creates records tagged northwind_id = PROBE-xx, reads them back and archives them.
The results go in docs/hubspot-verification.md.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, timedelta

from monitor.hubspot.client import API_VERSION, HubSpotClient, HubSpotError
from monitor.hubspot.guard import check_portal
from monitor.hubspot.properties import ensure_properties
from monitor.hubspot.seed import SeedError, industry_map

PROBE_WAIT = 10.0  # seconds for portal automation to run before reading back
LIFECYCLE_DOMAIN = "probe-lifecycle.example.com"
AUTOCREATE_DOMAIN = "probe-autocreate.example.com"


@dataclass(frozen=True)
class ProbeResult:
    probe_id: str
    question: str
    affects: str
    as_planned: bool
    detail: str
    http_status: int | None = None
    observed: dict = field(default_factory=dict)


class _Probes:
    def __init__(self, client: HubSpotClient, today: date, sleep: Callable[[float], None]):
        self.client = client
        self.today = today
        self.sleep = sleep
        self.created: list[tuple[str, str]] = []

    def create(self, object_type: str, properties: dict[str, str]) -> str:
        record = self.client.post(
            f"/crm/objects/{API_VERSION}/{object_type}", {"properties": properties}
        )
        self.created.append((object_type, str(record["id"])))
        return str(record["id"])

    def read(self, object_type: str, hs_id: str, properties: str) -> dict:
        path = f"/crm/objects/{API_VERSION}/{object_type}/{hs_id}"
        return (self.client.get(path, params={"properties": properties}) or {}).get(
            "properties", {}
        )

    def p1(self) -> ProbeResult:
        question = "Is a closed-won deal with a close date 30 days ago accepted and kept?"
        affects = "every closed deal; D08"
        closedate = f"{(self.today - timedelta(days=30)).isoformat()}T00:00:00Z"
        try:
            hs_id = self.create(
                "deals",
                {
                    "northwind_id": "PROBE-01",
                    "dealname": "Probe P1 closed-won deal",
                    "pipeline": "default",
                    "dealstage": "closedwon",
                    "amount": "1000",
                    "closedate": closedate,
                },
            )
            status = self.client.last_status
            back = self.read("deals", hs_id, "closedate,createdate,dealstage")
        except HubSpotError as exc:
            return ProbeResult("P1", question, affects, False, f"rejected: {exc}", exc.status)
        kept = (back.get("closedate") or "")[:10] == closedate[:10]
        detail = f"accepted; close date sent {closedate[:10]}, read back {back.get('closedate')}"
        observed = {"closedate": back.get("closedate"), "createdate": back.get("createdate")}
        return ProbeResult("P1", question, affects, kept, detail, status, observed)

    def p2(self) -> ProbeResult:
        question = (
            "Does a contact created without a lifecycle stage stay blank "
            "after a deal is associated?"
        )
        affects = "D16; lifecycle stage settings"
        try:
            contact = self.create(
                "contacts",
                {
                    "northwind_id": "PROBE-02",
                    "email": f"probe02@{LIFECYCLE_DOMAIN}",
                    "firstname": "Probe",
                    "lastname": "Lifecycle",
                },
            )
            status = self.client.last_status
            deal = self.create(
                "deals",
                {
                    "northwind_id": "PROBE-03",
                    "dealname": "Probe P2 open deal",
                    "pipeline": "default",
                    "dealstage": "appointmentscheduled",
                    "amount": "1000",
                },
            )
            self.client.associate("deals", "contacts", [(deal, contact)])
            self.sleep(PROBE_WAIT)
            stage = self.read("contacts", contact, "lifecyclestage").get("lifecyclestage") or ""
        except HubSpotError as exc:
            return ProbeResult("P2", question, affects, False, f"failed: {exc}", exc.status)
        detail = "still blank" if not stage else f"set to {stage!r} by the portal"
        return ProbeResult(
            "P2", question, affects, not stage, detail, status, {"lifecyclestage": stage}
        )

    def p3(self) -> ProbeResult:
        question = "Does a new contact create a company for its email domain?"
        affects = "D04; company auto-creation setting"
        domain_filter = [{"propertyName": "domain", "operator": "EQ", "value": AUTOCREATE_DOMAIN}]
        try:
            self.create(
                "contacts",
                {
                    "northwind_id": "PROBE-04",
                    "email": f"probe04@{AUTOCREATE_DOMAIN}",
                    "firstname": "Probe",
                    "lastname": "Autocreate",
                },
            )
            status = self.client.last_status
            self.sleep(PROBE_WAIT)
            companies = self.client.count("companies", domain_filter)
        except HubSpotError as exc:
            return ProbeResult("P3", question, affects, False, f"failed: {exc}", exc.status)
        detail = "no company created" if companies == 0 else f"{companies} company created"
        return ProbeResult(
            "P3", question, affects, companies == 0, detail, status, {"companies": companies}
        )

    def p4(self) -> ProbeResult:
        question = "Is the contact property hs_analytics_source (Original source) read-only?"
        affects = "northwind_source custom property"
        try:
            prop = (
                self.client.get(f"/crm/properties/{API_VERSION}/contacts/hs_analytics_source") or {}
            )
        except HubSpotError as exc:
            return ProbeResult(
                "P4", question, affects, True, f"couldn't read it: {exc}", exc.status
            )
        read_only = (prop.get("modificationMetadata") or {}).get("readOnlyValue")
        detail = f"readOnlyValue = {read_only}; the seed uses northwind_source either way"
        return ProbeResult(
            "P4",
            question,
            affects,
            True,
            detail,
            self.client.last_status,
            {"readOnlyValue": read_only},
        )

    def p5(self) -> ProbeResult:
        question = (
            "Do all 12 generator industries match an option of the company industry property?"
        )
        affects = "industry map in monitor/hubspot/seed.py"
        try:
            prop = self.client.get(f"/crm/properties/{API_VERSION}/companies/industry") or {}
        except HubSpotError as exc:
            return ProbeResult(
                "P5", question, affects, False, f"couldn't read it: {exc}", exc.status
            )
        options = prop.get("options") or []
        status = self.client.last_status
        try:
            industry_map(options)
        except SeedError as exc:
            return ProbeResult(
                "P5", question, affects, False, str(exc), status, {"options": options}
            )
        detail = f"{len(options)} options; all 12 industries match"
        return ProbeResult("P5", question, affects, True, detail, status, {"options": options})

    def cleanup(self) -> None:
        by_type: dict[str, list[str]] = {}
        for object_type, hs_id in self.created:
            by_type.setdefault(object_type, []).append(hs_id)
        for domain in (LIFECYCLE_DOMAIN, AUTOCREATE_DOMAIN):
            filters = [{"propertyName": "domain", "operator": "EQ", "value": domain}]
            for record in self.client.search("companies", filters):
                by_type.setdefault("companies", []).append(str(record["id"]))
        for object_type, ids in by_type.items():
            self.client.batch_archive(object_type, ids)


def run_probes(
    client: HubSpotClient,
    portal_id: str,
    today: date,
    sleep: Callable[[float], None] | None = None,
) -> list[ProbeResult]:
    check_portal(client, portal_id)
    ensure_properties(client)
    probes = _Probes(client, today, sleep or time.sleep)
    try:
        results = [probes.p1(), probes.p2(), probes.p3(), probes.p4(), probes.p5()]
    finally:
        probes.cleanup()
    return [_without(r, portal_id) for r in results]


def _without(result: ProbeResult, portal_id: str) -> ProbeResult:
    """Keep the portal ID out of anything written to the repository."""
    if portal_id and portal_id in result.detail:
        return ProbeResult(
            result.probe_id,
            result.question,
            result.affects,
            result.as_planned,
            result.detail.replace(portal_id, "<portal>"),
            result.http_status,
            result.observed,
        )
    return result


def render_verification(results: list[ProbeResult], today: date) -> str:
    def cell(text: object) -> str:
        return str(text).replace("|", "\\|").replace("\n", " ")

    lines = [
        "# HubSpot behaviour checks",
        "",
        f"Run with `uv run python -m monitor hubspot probe` on {today.isoformat()} against the "
        f"developer test account, API version {API_VERSION}. Each probe created records tagged "
        "`northwind_id = PROBE-xx`, read them back and archived them.",
        "",
        "| Probe | Question | Affects | HTTP | Result | As planned |",
        "|---|---|---|---|---|---|",
    ]
    for r in results:
        lines.append(
            f"| {r.probe_id} | {cell(r.question)} | {cell(r.affects)} | {r.http_status or ''} "
            f"| {cell(r.detail)} | {'yes' if r.as_planned else 'no'} |"
        )
    return "\n".join(lines) + "\n"
