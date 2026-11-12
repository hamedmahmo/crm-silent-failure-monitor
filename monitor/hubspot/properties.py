"""Custom properties the seed needs, in a `northwind` group. Created only if missing."""

from __future__ import annotations

from dataclasses import dataclass, field

from monitor.datagen import generator
from monitor.hubspot.client import API_VERSION, HubSpotClient, HubSpotError

GROUP_NAME = "northwind"
GROUP_LABEL = "Northwind (synthetic test data)"
ID_PROPERTY = "northwind_id"


@dataclass(frozen=True)
class PropertyDef:
    name: str
    label: str
    type: str  # string | datetime | enumeration
    field_type: str  # text | date | select
    options: tuple[dict[str, str], ...] = field(default=())

    def payload(self) -> dict:
        body: dict = {
            "name": self.name,
            "label": self.label,
            "type": self.type,
            "fieldType": self.field_type,
            "groupName": GROUP_NAME,
            "hasUniqueValue": self.name == ID_PROPERTY,
        }
        if self.options:
            body["options"] = [
                {**option, "displayOrder": i, "hidden": False}
                for i, option in enumerate(self.options)
            ]
        return body


def _text(name: str, label: str) -> PropertyDef:
    return PropertyDef(name, label, "string", "text")


def _datetime(name: str, label: str) -> PropertyDef:
    return PropertyDef(name, label, "datetime", "date")


def _source_options() -> tuple[dict[str, str], ...]:
    return tuple(
        {"label": value.replace("_", " ").capitalize(), "value": value}
        for value in generator().SOURCES
    )


def _common() -> list[PropertyDef]:
    return [
        _text(ID_PROPERTY, "Northwind ID"),
        _datetime("northwind_created_at", "Northwind created at"),
    ]


PROPERTIES: dict[str, list[PropertyDef]] = {
    "companies": [
        *_common(),
        _text("northwind_employee_band", "Northwind employee band"),
        _text("northwind_plan_tier", "Northwind plan tier"),
        _datetime("northwind_churned_at", "Northwind churned at"),
    ],
    "contacts": [
        *_common(),
        PropertyDef(
            "northwind_source", "Northwind source", "enumeration", "select", _source_options()
        ),
    ],
    "deals": [
        *_common(),
        _datetime("northwind_stage_entered_at", "Northwind stage entered at"),
        _datetime("northwind_closed_at", "Northwind closed at"),
    ],
}


def ensure_properties(client: HubSpotClient) -> dict[str, list[str]]:
    """Create the group and any missing properties; return the names created per object."""
    created: dict[str, list[str]] = {}
    for object_type, defs in PROPERTIES.items():
        base = f"/crm/properties/{API_VERSION}/{object_type}"
        groups = {g["name"] for g in (client.get(f"{base}/groups") or {}).get("results", [])}
        if GROUP_NAME not in groups:
            client.post(f"{base}/groups", {"name": GROUP_NAME, "label": GROUP_LABEL})
        existing = {p["name"]: p for p in (client.get(base) or {}).get("results", [])}
        id_prop = existing.get(ID_PROPERTY)
        if id_prop is not None and not id_prop.get("hasUniqueValue"):
            raise HubSpotError(
                f"{object_type}.{ID_PROPERTY} exists but doesn't require unique values; "
                "batch upsert needs a unique property"
            )
        missing = [d for d in defs if d.name not in existing]
        if missing:
            client.post(f"{base}/batch/create", {"inputs": [d.payload() for d in missing]})
        created[object_type] = [d.name for d in missing]
    return created
