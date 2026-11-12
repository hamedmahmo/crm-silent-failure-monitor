"""Tests for monitor/hubspot/snapshot.py: reading the test account back into a Snapshot.

Mocked transports only. The round-trip tests seed the fake portal with apply_seed, read it back
and compare with the seed plan, which is the answer key the live run is scored against.
"""

from __future__ import annotations

import json
from datetime import date, datetime

import pytest
from hubspot_fake import INDUSTRY_OPTIONS, FakeHubSpot

import generate
from monitor.checks import findings_for_run, load_checks, run_checks
from monitor.hubspot.client import HubSpotClient, HubSpotError
from monitor.hubspot.guard import GuardError
from monitor.hubspot.seed import apply_seed, build_seed, industry_map
from monitor.hubspot.snapshot import (
    SOURCE_REF,
    date_part,
    normalize_timestamp,
    read_hubspot_snapshot,
)
from monitor.params import load_params
from monitor.scoring import score
from monitor.snapshot import (
    COLUMNS,
    PRIMARY_KEYS,
    SnapshotError,
    connect,
    record_ids_by_type,
    store_snapshot,
)

PORTAL = "4242"
SEED_DAY = date(2026, 11, 13)
PARAMS = load_params()


def client_for(fake: FakeHubSpot) -> HubSpotClient:
    return HubSpotClient("token", transport=fake.transport(), sleep=lambda s: None)


def read(fake: FakeHubSpot, as_of: date = SEED_DAY):
    return read_hubspot_snapshot(client_for(fake), PORTAL, as_of, PARAMS)


@pytest.fixture(scope="module")
def seeded():
    tables, manifest = generate.build(seed=42, defects=True)
    plan = build_seed(tables, manifest, SEED_DAY, "77", industry_map(INDUSTRY_OPTIONS))
    fake = FakeHubSpot(portal_id=int(PORTAL))
    apply_seed(client_for(fake), plan, PORTAL)
    return plan, fake


@pytest.fixture(scope="module")
def snapshot(seeded):
    _, fake = seeded
    return read(fake)


def comparable(rows: list[dict[str, str]], table: str) -> list[dict[str, object]]:
    """Rows sorted by key, with owner_id reduced to blank or set (OWN-xx against 77)."""
    out = []
    for row in sorted(rows, key=lambda r: r[PRIMARY_KEYS[table]]):
        row = dict(row)
        if "owner_id" in row:
            row["owner_id"] = bool(row["owner_id"])
        out.append(row)
    return out


# ---------------------------------------------------------------- round trip


def test_snapshot_metadata(snapshot):
    assert snapshot.source == "hubspot"
    assert snapshot.source_ref == SOURCE_REF
    assert PORTAL not in snapshot.source_ref
    assert snapshot.as_of == SEED_DAY


@pytest.mark.parametrize("table", ["companies", "contacts", "deals"])
def test_round_trip_matches_the_seed_tables(seeded, snapshot, table):
    plan, _ = seeded
    assert list(snapshot.tables[table][0]) == COLUMNS[table]
    assert comparable(snapshot.tables[table], table) == comparable(plan.tables[table], table)


def test_owners_are_the_portal_owners_without_names(snapshot):
    assert snapshot.tables["owners"] == [{"owner_id": "77", "name": "", "active": "true"}]
    text = json.dumps(snapshot.tables)
    assert "Ownerfirst" not in text
    assert "Ownerlast" not in text
    assert "owner.example.com" not in text


def test_checks_on_the_snapshot_find_exactly_the_live_manifest(seeded, snapshot):
    plan, _ = seeded
    con = connect(":memory:")
    run_id = store_snapshot(con, snapshot, "hubspot", datetime(2026, 11, 13))
    checks = load_checks()
    run_checks(con, run_id, checks, PARAMS)
    result = score(
        findings_for_run(con, run_id), checks, plan.manifest, record_ids_by_type(con, run_id)
    )
    assert (result.planted_total, result.caught_total, result.false_positive_total) == (75, 75, 0)
    con.close()


def test_reading_sends_no_writes(seeded):
    _, fake = seeded
    before = len(fake.writes())
    read(fake)
    assert len(fake.writes()) == before


def test_lists_ask_for_100_per_page_and_follow_paging(seeded):
    _, fake = seeded
    before = len(fake.requests)
    read(fake)
    lists = [
        r
        for r in fake.requests[before:]
        if r.method == "GET" and r.url.path == "/crm/objects/2026-09/contacts"
    ]
    assert len(lists) == 11  # 1,031 contacts
    assert {r.url.params["limit"] for r in lists} == {"100"}
    assert "archived" not in lists[0].url.params or lists[0].url.params["archived"] == "false"


def test_association_reads_are_batched_by_1000(seeded):
    _, fake = seeded
    before = len(fake.requests)
    read(fake)
    reads = [
        json.loads(r.content)
        for r in fake.requests[before:]
        if r.url.path == "/crm/associations/2026-09/contacts/companies/batch/read"
    ]
    assert [len(b["inputs"]) for b in reads] == [1000, 31]


# ---------------------------------------------------------------- guard and stages


def test_guard_runs_first_and_a_refused_portal_gets_no_other_request():
    fake = FakeHubSpot(account_type="STANDARD")
    with pytest.raises(GuardError):
        read(fake)
    assert [r.url.path for r in fake.requests] == ["/account-info/2026-09/details"]


def test_a_pipeline_without_a_params_stage_stops_the_run():
    fake = FakeHubSpot()
    fake.pipelines[0]["stages"] = [s for s in fake.pipelines[0]["stages"] if s["id"] != "closedwon"]
    with pytest.raises(SnapshotError, match="closedwon"):
        read(fake)
    assert fake.sent("GET", "/crm/objects/") == []


def test_no_default_pipeline_stops_the_run():
    fake = FakeHubSpot()
    fake.pipelines = []
    with pytest.raises(SnapshotError, match="default"):
        read(fake)


# ---------------------------------------------------------------- records added by hand


def hand_made_portal() -> tuple[FakeHubSpot, dict[str, str]]:
    """A small portal with seeded-style and hand-made records."""
    fake = FakeHubSpot()
    ids = {
        "company": fake.add_record(
            "companies",
            {"name": "Hand Made", "domain": "handmade.example.com", "industry": "RETAIL"},
        ),
        "seeded_company": fake.add_record(
            "companies",
            {"northwind_id": "COMP-9001", "name": "Seeded", "industry": "MACHINERY"},
        ),
        "contact": fake.add_record(
            "contacts",
            {
                "firstname": "Sam",
                "email": "sam@handmade.example.com",
                "hs_analytics_source": "DIRECT_TRAFFIC",
            },
        ),
        "seeded_contact": fake.add_record(
            "contacts",
            {
                "northwind_id": "CONT-90001",
                "northwind_created_at": "2026-03-01T10:00:00Z",
                "hs_analytics_source": "OFFLINE",
            },
        ),
        "deal": fake.add_record(
            "deals",
            {
                "dealname": "Hand deal",
                "dealstage": "qualifiedtobuy",
                "closedate": "2026-12-01T00:00:00Z",
            },
        ),
    }
    return fake, ids


def by_key(snapshot, table):
    return {r[PRIMARY_KEYS[table]]: r for r in snapshot.tables[table]}


def test_records_without_northwind_id_get_hs_ids_and_hubspot_fields():
    fake, ids = hand_made_portal()
    snap = read(fake)
    company = by_key(snap, "companies")[f"HS-{ids['company']}"]
    assert company["created_at"] == "2026-11-12T19:01:02Z"
    contact = by_key(snap, "contacts")[f"HS-{ids['contact']}"]
    assert contact["source"] == "DIRECT_TRAFFIC"
    assert contact["created_at"] == "2026-11-12T19:01:02Z"
    deal = by_key(snap, "deals")[f"HS-{ids['deal']}"]
    assert deal["close_date"] == "2026-12-01"
    assert deal["stage_entered_at"] == ""


def test_seeded_contact_with_blank_source_stays_blank():
    fake, _ = hand_made_portal()
    contact = by_key(read(fake), "contacts")["CONT-90001"]
    assert contact["source"] == ""
    assert contact["created_at"] == "2026-03-01T10:00:00Z"


def test_industry_maps_back_to_generator_labels():
    fake, ids = hand_made_portal()
    fake.add_record("companies", {"northwind_id": "COMP-9002", "industry": "ACCOUNTING"})
    fake.add_record("companies", {"northwind_id": "COMP-9003", "industry": "NOT_AN_OPTION"})
    companies = by_key(read(fake), "companies")
    assert companies["COMP-9001"]["industry"] == "Manufacturing"
    assert companies[f"HS-{ids['company']}"]["industry"] == "Retail"
    assert companies["COMP-9002"]["industry"] == "Accounting"  # outside the map: HubSpot label
    assert companies["COMP-9003"]["industry"] == "NOT_AN_OPTION"


def test_archived_owners_are_inactive():
    fake = FakeHubSpot()
    fake.owners.append({"id": "78", "archived": True, "firstName": "Gone", "lastName": "Owner"})
    owners = read(fake).tables["owners"]
    assert owners == [
        {"owner_id": "77", "name": "", "active": "true"},
        {"owner_id": "78", "name": "", "active": "false"},
    ]


# ---------------------------------------------------------------- associations


def test_primary_company_wins_over_an_unlabelled_one():
    fake, ids = hand_made_portal()
    fake.add_association("contacts", "companies", ids["contact"], ids["seeded_company"])
    fake.add_association("contacts", "companies", ids["contact"], ids["company"], primary=True)
    contact = by_key(read(fake), "contacts")[f"HS-{ids['contact']}"]
    assert contact["company_id"] == f"HS-{ids['company']}"


def test_without_a_primary_the_lowest_record_id_wins():
    fake, ids = hand_made_portal()
    fake.add_association("deals", "companies", ids["deal"], ids["company"])
    fake.add_association("deals", "companies", ids["deal"], ids["seeded_company"])
    fake.add_association("deals", "contacts", ids["deal"], ids["contact"])
    fake.add_association("deals", "contacts", ids["deal"], ids["seeded_contact"])
    deal = by_key(read(fake), "deals")[f"HS-{ids['deal']}"]
    assert deal["company_id"] == "COMP-9001"  # sorts before HS-...
    assert deal["primary_contact_id"] == "CONT-90001"


@pytest.mark.parametrize("as_errors", [False, True])
def test_records_with_no_associations_get_blanks(as_errors):
    fake, ids = hand_made_portal()
    fake.no_association_errors = as_errors
    fake.add_association("deals", "companies", ids["deal"], ids["company"])
    snap = read(fake)
    assert by_key(snap, "deals")[f"HS-{ids['deal']}"]["primary_contact_id"] == ""
    assert by_key(snap, "contacts")[f"HS-{ids['contact']}"]["company_id"] == ""


def test_other_association_errors_fail_the_run():
    fake, ids = hand_made_portal()
    fake.script(
        "POST",
        "/batch/read",
        207,
        body={
            "status": "COMPLETE",
            "results": [],
            "numErrors": 1,
            "errors": [{"status": "error", "category": "RATE_LIMITS", "message": "slow down"}],
        },
    )
    with pytest.raises(HubSpotError, match="slow down"):
        read(fake)


def test_per_record_association_paging_is_followed():
    fake, ids = hand_made_portal()
    fake.association_page = 1
    extra = fake.add_record("companies", {"northwind_id": "COMP-0001"})
    fake.add_association("contacts", "companies", ids["contact"], ids["company"])
    fake.add_association("contacts", "companies", ids["contact"], ids["seeded_company"])
    fake.add_association("contacts", "companies", ids["contact"], extra)
    contact = by_key(read(fake), "contacts")[f"HS-{ids['contact']}"]
    assert contact["company_id"] == "COMP-0001"  # only on the third page
    reads = fake.sent("POST", "/contacts/companies/batch/read")
    assert any(i.get("after") for r in reads for i in json.loads(r.content)["inputs"])


def test_associations_to_records_outside_the_snapshot_are_ignored():
    fake, ids = hand_made_portal()
    fake.add_association("contacts", "companies", ids["contact"], "999999")
    contact = by_key(read(fake), "contacts")[f"HS-{ids['contact']}"]
    assert contact["company_id"] == ""


# ---------------------------------------------------------------- value formats


@pytest.mark.parametrize(
    "value, expected",
    [
        ("2024-05-02T08:16:32Z", "2024-05-02T08:16:32Z"),
        ("2024-05-02T08:16:32.000Z", "2024-05-02T08:16:32Z"),
        ("2024-05-02T08:16:32.648Z", "2024-05-02T08:16:32Z"),
        ("2024-05-02T10:16:32+02:00", "2024-05-02T08:16:32Z"),
        ("", ""),
        (None, ""),
    ],
)
def test_normalize_timestamp(value, expected):
    assert normalize_timestamp(value) == expected


@pytest.mark.parametrize(
    "value, expected",
    [
        ("2026-10-13T00:00:00Z", "2026-10-13"),
        ("2026-10-13T00:00:00.000Z", "2026-10-13"),
        ("2026-10-13", "2026-10-13"),
        ("", ""),
        (None, ""),
    ],
)
def test_date_part(value, expected):
    assert date_part(value) == expected
