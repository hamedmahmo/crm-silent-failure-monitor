"""Refuse to write unless the token belongs to the configured developer test account."""

from __future__ import annotations

from dataclasses import dataclass

from monitor.hubspot.client import API_VERSION, PORTAL_ENV, HubSpotClient, HubSpotError

DEVELOPER_TEST = "DEVELOPER_TEST"


class GuardError(HubSpotError):
    """The portal is not the developer test account named in the environment."""


@dataclass(frozen=True)
class Portal:
    portal_id: str
    account_type: str


def check_portal(client: HubSpotClient, expected_portal_id: str) -> Portal:
    """Read the account details and stop unless it is the expected developer test account."""
    expected = (expected_portal_id or "").strip()
    if not expected:
        raise GuardError(f"{PORTAL_ENV} is not set; refusing to write")
    details = client.get(f"/account-info/{API_VERSION}/details") or {}
    account_type = details.get("accountType")
    if account_type != DEVELOPER_TEST:
        raise GuardError(
            f"the token's portal is a {account_type or 'unknown'} account, "
            "not a developer test account; refusing to write"
        )
    portal_id = str(details.get("portalId", ""))
    if portal_id != expected:
        raise GuardError(
            f"the token belongs to a different portal than {PORTAL_ENV}; refusing to write"
        )
    return Portal(portal_id, account_type)
