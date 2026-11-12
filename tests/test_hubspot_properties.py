"""Tests for monitor/hubspot/properties.py: custom properties are created once."""

from __future__ import annotations

import json

import pytest
from hubspot_fake import FakeHubSpot

import generate
from monitor.hubspot.client import HubSpotClient, HubSpotError
from monitor.hubspot.properties import GROUP_NAME, ID_PROPERTY, PROPERTIES, ensure_properties


def client_for(fake: FakeHubSpot) -> HubSpotClient:
    return HubSpotClient("token", transport=fake.transport(), sleep=lambda s: None)


def test_every_custom_property_is_in_the_group_with_the_prefix():
    assert GROUP_NAME == "northwind"
    for object_type, defs in PROPERTIES.items():
        assert object_type in ("companies", "contacts", "deals")
        names = [d.name for d in defs]
        assert ID_PROPERTY in names
        assert "northwind_created_at" in names
        assert all(n.startswith("northwind_") for n in names)


def test_expected_properties_per_object():
    names = {t: {d.name for d in defs} for t, defs in PROPERTIES.items()}
    assert {"northwind_employee_band", "northwind_plan_tier", "northwind_churned_at"} <= names[
        "companies"
    ]
    assert "northwind_source" in names["contacts"]
    assert {"northwind_stage_entered_at", "northwind_closed_at"} <= names["deals"]


def test_source_options_match_the_generator():
    [source] = [d for d in PROPERTIES["contacts"] if d.name == "northwind_source"]
    assert source.type == "enumeration"
    assert [o["value"] for o in source.options] == generate.SOURCES


def test_first_run_creates_group_and_all_properties():
    fake = FakeHubSpot()
    created = ensure_properties(client_for(fake))
    for object_type, defs in PROPERTIES.items():
        assert GROUP_NAME in fake.groups[object_type]
        assert created[object_type] == [d.name for d in defs]
        for d in defs:
            stored = fake.properties[object_type][d.name]
            assert stored["groupName"] == GROUP_NAME
            assert stored["hasUniqueValue"] is (d.name == ID_PROPERTY)


def test_id_property_payload():
    fake = FakeHubSpot()
    ensure_properties(client_for(fake))
    [request] = fake.sent("POST", "/crm/properties/2026-09/deals/batch/create")
    [id_input] = [i for i in json.loads(request.content)["inputs"] if i["name"] == ID_PROPERTY]
    assert id_input["type"] == "string"
    assert id_input["fieldType"] == "text"
    assert id_input["hasUniqueValue"] is True


def test_second_run_creates_nothing():
    fake = FakeHubSpot()
    ensure_properties(client_for(fake))
    before = len(fake.writes())
    created = ensure_properties(client_for(fake))
    assert created == {t: [] for t in PROPERTIES}
    assert len(fake.writes()) == before


def test_only_missing_properties_are_created():
    fake = FakeHubSpot()
    ensure_properties(client_for(fake))
    del fake.properties["contacts"]["northwind_source"]
    created = ensure_properties(client_for(fake))
    assert created["contacts"] == ["northwind_source"]
    assert created["companies"] == [] and created["deals"] == []


def test_existing_id_property_without_unique_values_is_an_error():
    fake = FakeHubSpot()
    fake.properties["deals"][ID_PROPERTY] = {"name": ID_PROPERTY, "hasUniqueValue": False}
    with pytest.raises(HubSpotError, match="unique"):
        ensure_properties(client_for(fake))
