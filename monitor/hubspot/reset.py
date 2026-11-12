"""Remove seeded records: only records with northwind_id set are touched."""

from __future__ import annotations

from monitor.hubspot.client import HubSpotClient
from monitor.hubspot.guard import check_portal
from monitor.hubspot.properties import ID_PROPERTY
from monitor.hubspot.seed import SEED_OBJECTS

TAGGED = [{"propertyName": ID_PROPERTY, "operator": "HAS_PROPERTY"}]


def find_tagged(client: HubSpotClient, object_type: str) -> list[str]:
    """HubSpot IDs of every record of this type with northwind_id set (seeded or probe)."""
    return [str(r["id"]) for r in client.search(object_type, TAGGED, properties=[ID_PROPERTY])]


def reset(client: HubSpotClient, portal_id: str, yes: bool = False) -> dict[str, int]:
    """Count tagged records per type; archive them only when `yes` is set."""
    check_portal(client, portal_id)
    found = {object_type: find_tagged(client, object_type) for object_type in SEED_OBJECTS}
    if yes:
        for object_type in reversed(SEED_OBJECTS):
            client.batch_archive(object_type, found[object_type])
    return {object_type: len(ids) for object_type, ids in found.items()}
