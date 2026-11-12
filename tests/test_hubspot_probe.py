"""Tests for monitor/hubspot/probe.py: probes P1-P5 against the fake portal."""

from __future__ import annotations

import json
from datetime import date

import pytest
from hubspot_fake import INDUSTRY_OPTIONS, FakeHubSpot

from monitor.hubspot.client import HubSpotClient
from monitor.hubspot.guard import GuardError
from monitor.hubspot.probe import render_verification, run_probes

PORTAL = "4242"
TODAY = date(2026, 11, 13)


def client_for(fake: FakeHubSpot) -> HubSpotClient:
    return HubSpotClient("token", transport=fake.transport(), sleep=lambda s: None)


def run(fake: FakeHubSpot):
    return run_probes(client_for(fake), PORTAL, TODAY, sleep=lambda s: None)


def test_runs_p1_to_p5_in_order():
    results = run(FakeHubSpot())
    assert [r.probe_id for r in results] == ["P1", "P2", "P3", "P4", "P5"]


def test_expected_outcomes_on_a_portal_that_behaves_as_planned():
    results = {r.probe_id: r for r in run(FakeHubSpot())}
    assert all(r.as_planned for r in results.values()), [r.detail for r in results.values()]
    assert results["P1"].observed["closedate"] == "2026-10-14T00:00:00Z"
    assert results["P4"].observed["readOnlyValue"] is True
    assert results["P5"].observed["options"] == INDUSTRY_OPTIONS


def test_p1_sends_a_closed_won_deal_with_a_past_close_date():
    fake = FakeHubSpot()
    run(fake)
    bodies = [json.loads(r.content) for r in fake.sent("POST", "/crm/objects/2026-09/deals")]
    p1 = next(b for b in bodies if b.get("properties", {}).get("northwind_id") == "PROBE-01")
    assert p1["properties"]["dealstage"] == "closedwon"
    assert p1["properties"]["closedate"] == "2026-10-14T00:00:00Z"


def test_p2_detects_a_filled_lifecycle_stage():
    results = {r.probe_id: r for r in run(FakeHubSpot(auto_lifecycle_on_deal="opportunity"))}
    assert not results["P2"].as_planned
    assert "opportunity" in results["P2"].detail


def test_p3_detects_an_auto_created_company():
    results = {r.probe_id: r for r in run(FakeHubSpot(auto_company_for_contacts=True))}
    assert not results["P3"].as_planned


def test_p1_detects_a_rejected_deal():
    fake = FakeHubSpot()
    fake.script(
        "POST",
        "/crm/objects/2026-09/deals",
        400,
        body={"message": "closedate must be after createdate", "category": "VALIDATION_ERROR"},
    )
    results = {r.probe_id: r for r in run(fake)}
    assert not results["P1"].as_planned
    assert results["P1"].http_status == 400
    assert "closedate must be after createdate" in results["P1"].detail


def test_probe_records_are_archived_afterwards():
    fake = FakeHubSpot(auto_company_for_contacts=True)
    run(fake)
    for object_type in ("companies", "contacts", "deals"):
        assert not [
            p
            for p in fake.objects[object_type].values()
            if p.get("northwind_id", "").startswith("PROBE")
        ]
    assert not [
        p for p in fake.objects["companies"].values() if p.get("domain", "").startswith("probe-")
    ]


def test_guard_runs_first():
    fake = FakeHubSpot(account_type="STANDARD")
    with pytest.raises(GuardError):
        run(fake)
    assert fake.writes() == []


def test_render_verification_has_one_row_per_probe_and_no_portal_id():
    results = run(FakeHubSpot(portal_id=4242))
    text = render_verification(results, TODAY)
    assert text.startswith("# HubSpot behaviour checks")
    assert "2026-11-13" in text
    for probe_id in ("P1", "P2", "P3", "P4", "P5"):
        assert f"| {probe_id} |" in text
    assert "4242" not in text
    assert "token" not in text.lower()
