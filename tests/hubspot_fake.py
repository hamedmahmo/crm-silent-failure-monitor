"""An in-memory stand-in for the HubSpot API, served through httpx.MockTransport.

It implements only the endpoints the monitor uses, at API version 2026-09, and records every
request so tests can check what was sent. It never touches the network.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

import httpx

V = "2026-09"
OBJECTS = ("companies", "contacts", "deals")
DEAL_STAGES = (
    "appointmentscheduled",
    "qualifiedtobuy",
    "presentationscheduled",
    "decisionmakerboughtin",
    "contractsent",
    "closedwon",
    "closedlost",
)
# HUBSPOT_DEFINED association type IDs: the unlabelled default, and "Primary" where one exists.
DEFAULT_TYPE = {
    ("contacts", "companies"): 279,
    ("deals", "companies"): 341,
    ("deals", "contacts"): 3,
}
PRIMARY_TYPE = {("contacts", "companies"): 1, ("deals", "companies"): 5}
# Custom datetime properties come back with milliseconds, like HubSpot's own timestamps.
DATETIME_PROPERTIES = (
    "northwind_created_at",
    "northwind_churned_at",
    "northwind_stage_entered_at",
    "northwind_closed_at",
)
CREATED = "2026-11-12T19:01:02.345Z"
NO_ASSOCIATIONS = "crm.associations.NO_ASSOCIATIONS_FOUND"

INDUSTRY_OPTIONS = [
    {"label": "Computer Software", "value": "COMPUTER_SOFTWARE"},
    {"label": "Logistics and Supply Chain", "value": "LOGISTICS_AND_SUPPLY_CHAIN"},
    {"label": "Retail", "value": "RETAIL"},
    {"label": "Machinery", "value": "MACHINERY"},
    {"label": "Financial Services", "value": "FINANCIAL_SERVICES"},
    {"label": "Education Management", "value": "EDUCATION_MANAGEMENT"},
    {"label": "Media Production", "value": "MEDIA_PRODUCTION"},
    {"label": "Oil & Energy", "value": "OIL_ENERGY"},
    {"label": "Construction", "value": "CONSTRUCTION"},
    {"label": "Hospitality", "value": "HOSPITALITY"},
    {"label": "Farming", "value": "FARMING"},
    {"label": "Telecommunications", "value": "TELECOMMUNICATIONS"},
    {"label": "Accounting", "value": "ACCOUNTING"},
]


@dataclass
class Scripted:
    method: str
    path_contains: str
    status: int
    headers: dict[str, str] = field(default_factory=dict)
    body: object = None
    times: int = 1


class FakeHubSpot:
    def __init__(
        self,
        account_type: str = "DEVELOPER_TEST",
        portal_id: int = 4242,
        owners: list[dict] | None = None,
        auto_company_for_contacts: bool = False,
        auto_lifecycle_on_deal: str | None = None,
    ) -> None:
        self.account_type = account_type
        self.portal_id = portal_id
        self.owners = (
            owners
            if owners is not None
            else [
                {
                    "id": "77",
                    "archived": False,
                    "type": "PERSON",
                    "firstName": "Ownerfirst",
                    "lastName": "Ownerlast",
                    "email": "ownerfirst.ownerlast@owner.example.com",
                }
            ]
        )
        self.pipelines = [
            {"id": "default", "label": "Sales Pipeline", "stages": [{"id": s} for s in DEAL_STAGES]}
        ]
        # extra association types per (from_type, to_type, from_id, to_id), e.g. Primary
        self.association_types: dict[tuple[str, str, str, str], list[dict]] = {}
        self.association_page = 500  # associations per record per page in batch read
        self.no_association_errors = False  # list records with none in `errors` (207)
        self.list_limits: list[int] = []
        self.auto_company_for_contacts = auto_company_for_contacts
        self.auto_lifecycle_on_deal = auto_lifecycle_on_deal
        self.requests: list[httpx.Request] = []
        self.objects: dict[str, dict[str, dict[str, str]]] = {t: {} for t in OBJECTS}
        self.groups: dict[str, set[str]] = {t: set() for t in OBJECTS}
        self.properties: dict[str, dict[str, dict]] = {
            "companies": {
                "name": {"name": "name"},
                "industry": {
                    "name": "industry",
                    "type": "enumeration",
                    "options": INDUSTRY_OPTIONS,
                },
            },
            "contacts": {
                "email": {"name": "email"},
                "hs_analytics_source": {
                    "name": "hs_analytics_source",
                    "modificationMetadata": {"readOnlyValue": True},
                },
            },
            "deals": {"dealname": {"name": "dealname"}},
        }
        self.associations: list[tuple[str, str, str, str]] = []
        self.archived: list[tuple[str, str]] = []
        self.scripted: list[Scripted] = []
        self._next_id = 1000

    # ------------------------------------------------------------ helpers for tests

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def script(self, method: str, path_contains: str, status: int, **kwargs) -> None:
        self.scripted.append(Scripted(method, path_contains, status, **kwargs))

    def sent(self, method: str | None = None, contains: str = "") -> list[httpx.Request]:
        return [
            r
            for r in self.requests
            if (method is None or r.method == method) and contains in r.url.path
        ]

    def writes(self) -> list[httpx.Request]:
        """Requests that would change data (search and batch read are POST but read-only)."""
        return [
            r
            for r in self.requests
            if r.method in ("POST", "PATCH", "PUT", "DELETE")
            and not r.url.path.endswith(("/search", "/batch/read"))
        ]

    def add_record(self, object_type: str, properties: dict[str, str]) -> str:
        """Create a record directly, as someone working in HubSpot would."""
        hs_id = self.new_id()
        self.objects[object_type][hs_id] = {"hs_object_id": hs_id, "createdate": CREATED}
        self.objects[object_type][hs_id].update(properties)
        return hs_id

    def add_association(
        self, from_type: str, to_type: str, from_id: str, to_id: str, primary: bool = False
    ) -> None:
        self.associations.append((from_type, to_type, from_id, to_id))
        if primary:
            self.association_types[(from_type, to_type, from_id, to_id)] = [
                {
                    "category": "HUBSPOT_DEFINED",
                    "typeId": PRIMARY_TYPE[(from_type, to_type)],
                    "label": "Primary",
                }
            ]

    def by_northwind_id(self, object_type: str) -> dict[str, dict[str, str]]:
        return {
            p["northwind_id"]: p for p in self.objects[object_type].values() if "northwind_id" in p
        }

    # ------------------------------------------------------------ routing

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        for s in self.scripted:
            if s.times > 0 and s.method == request.method and s.path_contains in request.url.path:
                s.times -= 1
                return httpx.Response(s.status, headers=s.headers, json=s.body)
        path = request.url.path
        body = json.loads(request.content) if request.content else None
        parts = path.strip("/").split("/")

        if path == f"/account-info/{V}/details":
            return ok({"portalId": self.portal_id, "accountType": self.account_type})
        if path == f"/crm/owners/{V}":
            archived = request.url.params.get("archived") == "true"
            return ok({"results": [o for o in self.owners if o["archived"] == archived]})
        if path == f"/crm/pipelines/{V}/deals":
            return ok({"results": self.pipelines})
        if parts[:3] == ["crm", "associations", V] and parts[5:] == ["batch", "read"]:
            return self.read_associations(parts[3], parts[4], body)
        if parts[:2] == ["crm", "properties"] and parts[2] == V:
            return self.handle_properties(request.method, parts[3:], body)
        if parts[:2] == ["crm", "objects"] and parts[2] == V:
            return self.handle_objects(request, parts[3:], body)
        if parts[:2] == ["crm", "associations"] and parts[2] == V:
            from_type, to_type = parts[3], parts[4]
            for item in body["inputs"]:
                self.associations.append((from_type, to_type, item["from"]["id"], item["to"]["id"]))
                if (
                    self.auto_lifecycle_on_deal
                    and from_type == "deals"
                    and to_type == "contacts"
                    and item["to"]["id"] in self.objects["contacts"]
                ):
                    self.objects["contacts"][item["to"]["id"]]["lifecyclestage"] = (
                        self.auto_lifecycle_on_deal
                    )
            return ok({"status": "COMPLETE", "results": []})
        return httpx.Response(404, json={"message": f"no route {path}", "category": "NOT_FOUND"})

    def handle_properties(self, method: str, rest: list[str], body) -> httpx.Response:
        object_type = rest[0]
        if rest[1:] == ["groups"]:
            if method == "GET":
                return ok({"results": [{"name": g} for g in sorted(self.groups[object_type])]})
            self.groups[object_type].add(body["name"])
            return httpx.Response(201, json=body)
        if rest[1:] == ["batch", "create"]:
            for item in body["inputs"]:
                self.properties[object_type][item["name"]] = item
            return httpx.Response(201, json={"status": "COMPLETE", "results": body["inputs"]})
        if len(rest) == 1:
            return ok({"results": list(self.properties[object_type].values())})
        prop = self.properties[object_type].get(rest[1])
        if prop is None:
            return httpx.Response(404, json={"message": "property not found"})
        return ok(prop)

    def handle_objects(self, request: httpx.Request, rest: list[str], body) -> httpx.Response:
        object_type = rest[0]
        store = self.objects[object_type]
        if rest[1:] == ["batch", "upsert"]:
            invalid = invalid_email(object_type, [i["properties"] for i in body["inputs"]])
            if invalid:
                return invalid
            results = []
            index = self.by_northwind_id(object_type)
            for item in body["inputs"]:
                assert item["idProperty"] == "northwind_id"
                existing = index.get(item["id"])
                if existing is None:
                    hs_id = self.new_id()
                    store[hs_id] = {
                        "hs_object_id": hs_id,
                        "northwind_id": item["id"],
                        "createdate": CREATED,
                    }
                    self.after_create(object_type, store[hs_id], item["properties"])
                    new = True
                else:
                    hs_id, new = existing["hs_object_id"], False
                store[hs_id].update(item["properties"])
                results.append({"id": hs_id, "new": new, "properties": dict(store[hs_id])})
            return ok({"status": "COMPLETE", "results": results})
        if rest[1:] == ["batch", "read"]:
            index = self.by_northwind_id(object_type)
            results = [
                {"id": index[i["id"]]["hs_object_id"], "properties": dict(index[i["id"]])}
                for i in body["inputs"]
                if i["id"] in index
            ]
            return ok({"status": "COMPLETE", "results": results})
        if rest[1:] == ["batch", "archive"]:
            for item in body["inputs"]:
                store.pop(item["id"], None)
                self.archived.append((object_type, item["id"]))
            return httpx.Response(204)
        if rest[1:] == ["search"]:
            return self.search(object_type, body)
        if len(rest) == 1 and request.method == "GET":
            return self.list_page(object_type, request.url.params)
        if len(rest) == 1 and request.method == "POST":
            invalid = invalid_email(object_type, [body["properties"]])
            if invalid:
                return invalid
            hs_id = self.new_id()
            store[hs_id] = {"hs_object_id": hs_id, **body["properties"]}
            self.after_create(object_type, store[hs_id], body["properties"])
            return httpx.Response(201, json={"id": hs_id, "properties": dict(store[hs_id])})
        if len(rest) == 2 and request.method == "GET":
            record = store.get(rest[1])
            if record is None:
                return httpx.Response(404, json={"message": "not found"})
            return ok({"id": rest[1], "properties": dict(record)})
        return httpx.Response(404, json={"message": "no route"})

    def list_page(self, object_type: str, params: httpx.QueryParams) -> httpx.Response:
        assert params.get("archived", "false") == "false", "archived records were requested"
        limit = int(params.get("limit", "10"))
        assert limit <= 100, f"limit {limit} is above HubSpot's 100"
        self.list_limits.append(limit)
        wanted = [p for p in params.get("properties", "").split(",") if p]
        records = sorted(self.objects[object_type].values(), key=lambda r: int(r["hs_object_id"]))
        start = int(params.get("after") or 0)
        page = records[start : start + limit]
        results = []
        for r in page:
            props = {"hs_object_id": r["hs_object_id"], "createdate": r.get("createdate")}
            for name in wanted:
                value = r.get(name)
                if value and name in DATETIME_PROPERTIES and len(value) == 10:
                    value += "T00:00:00.000Z"  # a date sent to a datetime property: midnight UTC
                elif value and name in DATETIME_PROPERTIES and "." not in value:
                    value = value.replace("Z", ".000Z")
                props[name] = value  # HubSpot returns requested-but-empty properties as null
            results.append({"id": r["hs_object_id"], "properties": props, "archived": False})
        result: dict = {"results": results}
        if start + limit < len(records):
            result["paging"] = {"next": {"after": str(start + limit)}}
        return ok(result)

    def read_associations(self, from_type: str, to_type: str, body: dict) -> httpx.Response:
        results, errors = [], []
        for item in body["inputs"]:
            from_id = item["id"]
            found = []
            for a_from, a_to, f, t in self.associations:
                if (a_from, a_to) == (from_type, to_type) and f == from_id:
                    key, other = (a_from, a_to, f, t), t
                elif (a_from, a_to) == (to_type, from_type) and t == from_id:
                    key, other = (a_from, a_to, f, t), f
                else:
                    continue
                types = [
                    {
                        "category": "HUBSPOT_DEFINED",
                        "typeId": DEFAULT_TYPE.get((from_type, to_type), 0),
                        "label": None,
                    },
                    *self.association_types.get(key, []),
                ]
                found.append({"toObjectId": int(other), "associationTypes": types})
            if not found:
                if self.no_association_errors:
                    errors.append(
                        {
                            "status": "error",
                            "category": "OBJECT_NOT_FOUND",
                            "subCategory": NO_ASSOCIATIONS,
                            "message": f"No {to_type} is associated with {from_type} {from_id}.",
                            "context": {"fromObjectId": [from_id]},
                        }
                    )
                continue
            start = int(item.get("after") or 0)
            entry: dict = {
                "from": {"id": from_id},
                "to": found[start : start + self.association_page],
            }
            if start + self.association_page < len(found):
                entry["paging"] = {"next": {"after": str(start + self.association_page)}}
            results.append(entry)
        payload: dict = {"status": "COMPLETE", "results": results}
        if errors:
            payload |= {"errors": errors, "numErrors": len(errors)}
            return httpx.Response(207, json=payload)
        return ok(payload)

    def after_create(self, object_type: str, record: dict, props: dict) -> None:
        record.setdefault("createdate", CREATED)
        if object_type == "contacts":
            # HubSpot sets Original source on API-created contacts.
            record.setdefault("hs_analytics_source", "OFFLINE")
        if object_type == "contacts" and self.auto_company_for_contacts and "email" in props:
            domain = props["email"].split("@")[-1]
            hs_id = self.new_id()
            self.objects["companies"][hs_id] = {"hs_object_id": hs_id, "domain": domain}

    def search(self, object_type: str, body: dict) -> httpx.Response:
        records = list(self.objects[object_type].values())
        for group in body.get("filterGroups", []):
            for f in group["filters"]:
                name, op = f["propertyName"], f["operator"]
                if op == "HAS_PROPERTY":
                    records = [r for r in records if r.get(name)]
                elif op == "NOT_HAS_PROPERTY":
                    records = [r for r in records if not r.get(name)]
                elif op == "EQ":
                    records = [r for r in records if r.get(name) == f["value"]]
                else:
                    raise AssertionError(f"unsupported operator {op}")
        records.sort(key=lambda r: int(r["hs_object_id"]))
        start = int(body.get("after") or 0)
        limit = body.get("limit", 10)
        page = records[start : start + limit]
        result = {
            "total": len(records),
            "results": [{"id": r["hs_object_id"], "properties": dict(r)} for r in page],
        }
        if start + limit < len(records):
            result["paging"] = {"next": {"after": str(start + limit)}}
        return ok(result)

    def new_id(self) -> str:
        self._next_id += 1
        return str(self._next_id)


def invalid_email(object_type: str, records: list[dict]) -> httpx.Response | None:
    """HubSpot rejects contact emails on the reserved .example top-level domain with
    400 INVALID_EMAIL (probe run, 2026-11-12)."""
    if object_type != "contacts":
        return None
    for props in records:
        email = props.get("email", "")
        if email.lower().endswith(".example"):
            return httpx.Response(
                400,
                json={
                    "status": "error",
                    "category": "VALIDATION_ERROR",
                    "message": f"Email address {email} is invalid",
                },
            )
    return None


def ok(payload: object) -> httpx.Response:
    return httpx.Response(200, json=payload)
