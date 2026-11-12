"""Tests for monitor/hubspot/reset.py: only records tagged with northwind_id are removed."""

from __future__ import annotations

import json

import pytest
from hubspot_fake import FakeHubSpot

from monitor.hubspot.client import HubSpotClient
from monitor.hubspot.guard import GuardError
from monitor.hubspot.reset import find_tagged, reset

PORTAL = "4242"


def client_for(fake: FakeHubSpot) -> HubSpotClient:
    return HubSpotClient("token", transport=fake.transport(), sleep=lambda s: None)


def populate(fake: FakeHubSpot, tagged: int, untagged: int) -> None:
    for object_type in ("companies", "contacts", "deals"):
        for i in range(tagged):
            hs = fake.new_id()
            fake.objects[object_type][hs] = {"hs_object_id": hs, "northwind_id": f"NW-{i}"}
        for _ in range(untagged):
            hs = fake.new_id()
            fake.objects[object_type][hs] = {"hs_object_id": hs, "name": "someone else's record"}


def test_find_tagged_filters_on_northwind_id_and_pages():
    fake = FakeHubSpot()
    populate(fake, tagged=450, untagged=3)
    ids = find_tagged(client_for(fake), "contacts")
    assert len(ids) == 450
    bodies = [json.loads(r.content) for r in fake.sent("POST", "/contacts/search")]
    assert len(bodies) == 3
    for body in bodies:
        assert body["filterGroups"] == [
            {"filters": [{"propertyName": "northwind_id", "operator": "HAS_PROPERTY"}]}
        ]


def test_dry_run_counts_and_archives_nothing():
    fake = FakeHubSpot()
    populate(fake, tagged=5, untagged=2)
    counts = reset(client_for(fake), PORTAL, yes=False)
    assert counts == {"companies": 5, "contacts": 5, "deals": 5}
    assert fake.sent("POST", "/batch/archive") == []
    assert fake.archived == []


def test_yes_archives_exactly_the_tagged_records_in_batches():
    fake = FakeHubSpot()
    populate(fake, tagged=150, untagged=2)
    tagged = {t: set(fake.by_northwind_id(t)) for t in fake.objects}
    tagged_hs = {
        t: {p["hs_object_id"] for p in fake.by_northwind_id(t).values()} for t in fake.objects
    }
    counts = reset(client_for(fake), PORTAL, yes=True)
    assert counts == {t: 150 for t in ("companies", "contacts", "deals")}
    for object_type in ("companies", "contacts", "deals"):
        archived = {hs for t, hs in fake.archived if t == object_type}
        assert archived == tagged_hs[object_type]
        assert len(fake.objects[object_type]) == 2
        assert tagged[object_type]
        sizes = [
            len(json.loads(r.content)["inputs"])
            for r in fake.sent("POST", f"/{object_type}/batch/archive")
        ]
        assert sizes == [100, 50]


def test_reset_runs_the_guard_first():
    fake = FakeHubSpot(account_type="STANDARD")
    populate(fake, tagged=2, untagged=0)
    with pytest.raises(GuardError):
        reset(client_for(fake), PORTAL, yes=True)
    assert fake.writes() == []
    assert fake.sent("POST", "/search") == []
