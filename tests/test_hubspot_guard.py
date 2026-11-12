"""Tests for monitor/hubspot/guard.py: writes only go to the configured developer test account."""

from __future__ import annotations

import pytest
from hubspot_fake import FakeHubSpot

from monitor.hubspot.client import HubSpotClient
from monitor.hubspot.guard import GuardError, check_portal


def client_for(fake: FakeHubSpot) -> HubSpotClient:
    return HubSpotClient("token", transport=fake.transport(), sleep=lambda s: None)


def test_accepts_developer_test_account_with_matching_portal():
    fake = FakeHubSpot(portal_id=4242)
    portal = check_portal(client_for(fake), "4242")
    assert portal.portal_id == "4242"
    assert portal.account_type == "DEVELOPER_TEST"
    assert fake.sent("GET", "/account-info/2026-09/details")


def test_portal_id_whitespace_is_ignored():
    check_portal(client_for(FakeHubSpot(portal_id=4242)), " 4242 ")


@pytest.mark.parametrize("account_type", ["STANDARD", "SANDBOX", "APP_DEVELOPER", None])
def test_refuses_other_account_types(account_type):
    with pytest.raises(GuardError, match="not a developer test account"):
        check_portal(client_for(FakeHubSpot(account_type=account_type)), "4242")


def test_refuses_a_different_portal():
    with pytest.raises(GuardError, match="HUBSPOT_PORTAL_ID"):
        check_portal(client_for(FakeHubSpot(portal_id=4242)), "5555")


def test_refuses_a_missing_portal_id():
    with pytest.raises(GuardError, match="HUBSPOT_PORTAL_ID"):
        check_portal(client_for(FakeHubSpot()), "")


def test_guard_sends_no_writes():
    fake = FakeHubSpot(account_type="STANDARD")
    with pytest.raises(GuardError):
        check_portal(client_for(fake), "4242")
    assert fake.writes() == []
