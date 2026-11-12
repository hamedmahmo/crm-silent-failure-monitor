"""Tests for monitor/hubspot/seed.py: what gets seeded, how dates move, and what is sent."""

from __future__ import annotations

import copy
import json
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest
from hubspot_fake import INDUSTRY_OPTIONS, FakeHubSpot

import generate
from monitor.checks import findings_for_run, load_checks, run_checks
from monitor.hubspot.client import HubSpotClient
from monitor.hubspot.guard import GuardError
from monitor.hubspot.seed import (
    SeedError,
    apply_seed,
    build_seed,
    hubspot_domain,
    hubspot_email,
    industry_map,
    resolve_owner,
    skipped_defect_ids,
    verify_seed,
    write_seed_output,
)
from monitor.params import load_params
from monitor.scoring import score
from monitor.snapshot import (
    PRIMARY_KEYS,
    Snapshot,
    connect,
    read_csv_snapshot,
    record_ids_by_type,
    store_snapshot,
)

SEED_DAY = date(2026, 11, 13)
OWNER = "77"
PORTAL = "4242"
INDUSTRIES = industry_map(INDUSTRY_OPTIONS)
OPEN = set(generate.OPEN_STAGES)


@pytest.fixture(scope="module")
def generated():
    return generate.build(seed=42, defects=True)


@pytest.fixture(scope="module")
def plan(generated):
    tables, manifest = generated
    return build_seed(tables, manifest, SEED_DAY, OWNER, INDUSTRIES)


def client_for(fake: FakeHubSpot) -> HubSpotClient:
    return HubSpotClient("token", transport=fake.transport(), sleep=lambda s: None)


def defect_ids(manifest: dict, defect: str) -> set[str]:
    [entry] = [e for e in manifest["defects"] if e["id"] == defect]
    return set(entry["record_ids"])


# ---------------------------------------------------------------- what is seeded


def test_skipped_defects_are_the_in_scope_ones_hubspot_rejects(generated):
    assert skipped_defect_ids(generated[1]) == ["D01", "D02", "D03", "D09", "D10"]


def test_skips_exactly_the_records_of_those_defects(generated, plan):
    _, manifest = generated
    expected = set().union(*(defect_ids(manifest, d) for d in ("D01", "D02", "D03", "D09", "D10")))
    assert set(plan.skipped["contacts"]) | set(plan.skipped["deals"]) == expected
    assert plan.skipped["companies"] == []
    assert {t: len(plan.tables[t]) for t in ("companies", "contacts", "deals")} == {
        "companies": 203,
        "contacts": 1031,
        "deals": 570,
    }
    assert {t: len(plan.inputs[t]) for t in plan.inputs} == {
        "companies": 203,
        "contacts": 1031,
        "deals": 570,
    }


def test_live_manifest_has_the_12_plantable_defects_and_75_records(plan):
    m = plan.manifest
    assert [e["id"] for e in m["defects"]] == [
        "D04", "D06", "D07", "D08", "D16", "D17", "D18", "D19", "D20", "D21", "D22", "D23",
    ]  # fmt: skip
    assert sum(len(e["record_ids"]) for e in m["defects"]) == 75
    assert all(e["hubspot_plantable"] for e in m["defects"])
    assert m["as_of"] == SEED_DAY.isoformat()
    seeded = {
        row[PRIMARY_KEYS[t]] for t in ("companies", "contacts", "deals") for row in plan.tables[t]
    }
    assert {rid for e in m["defects"] for rid in e["record_ids"]} <= seeded


def test_build_seed_does_not_change_its_input(generated):
    tables, manifest = generated
    before = copy.deepcopy((tables, manifest))
    build_seed(tables, manifest, SEED_DAY, OWNER, INDUSTRIES)
    assert (tables, manifest) == before


# ---------------------------------------------------------------- dates


def test_shift_is_days_since_the_generator_as_of(plan):
    assert plan.shift_days == 136
    assert plan.seed_day == SEED_DAY


def test_only_open_deal_close_and_stage_entered_dates_move(generated, plan):
    tables, _ = generated
    original = {d["deal_id"]: d for d in tables["deals"]}
    n = timedelta(days=plan.shift_days)
    for deal in plan.tables["deals"]:
        before = original[deal["deal_id"]]
        if before["stage"] in OPEN:
            assert (
                date.fromisoformat(deal["close_date"])
                == date.fromisoformat(before["close_date"]) + n
            )
            assert datetime.fromisoformat(deal["stage_entered_at"]) == (
                datetime.fromisoformat(before["stage_entered_at"]) + n
            )
            unchanged = {k for k in deal if k not in ("close_date", "stage_entered_at")}
        else:
            unchanged = set(deal)
        assert {k: deal[k] for k in unchanged} == {k: before[k] for k in unchanged}
    # Company domains and contact emails are rewritten separately (.example -> .example.com).
    rewritten = {"owners": set(), "companies": {"domain"}, "contacts": {"email"}}
    for table, columns in rewritten.items():
        key = PRIMARY_KEYS[table]
        kept = {r[key] for r in plan.tables[table]}

        def other_columns(rows, columns=columns):
            return [{k: v for k, v in r.items() if k not in columns} for r in rows]

        assert other_columns(plan.tables[table]) == other_columns(
            [r for r in tables[table] if r[key] in kept]
        )


def test_timestamps_keep_the_generator_format(plan):
    for deal in plan.tables["deals"]:
        if deal["stage_entered_at"]:
            assert deal["stage_entered_at"].endswith("Z")
            assert len(deal["stage_entered_at"]) == len("2026-05-08T07:31:36Z")


@pytest.mark.parametrize("seed_day", [date(2026, 7, 1), SEED_DAY, date(2027, 6, 30)])
def test_checks_on_the_seed_tables_find_exactly_the_live_manifest(generated, seed_day):
    tables, manifest = generated
    plan = build_seed(tables, manifest, seed_day, OWNER, INDUSTRIES)
    con = connect(":memory:")
    snapshot = Snapshot("hubspot-seed", "test", seed_day, plan.tables)
    run_id = store_snapshot(con, snapshot, "seed", datetime(2026, 11, 13))
    checks = load_checks()
    run_checks(con, run_id, checks, load_params())
    result = score(
        findings_for_run(con, run_id), checks, plan.manifest, record_ids_by_type(con, run_id)
    )
    assert (result.planted_total, result.caught_total, result.false_positive_total) == (75, 75, 0)
    con.close()


# ---------------------------------------------------------------- payloads


def test_owner_ids_all_map_to_the_one_owner(generated, plan):
    for object_type, key in (
        ("companies", "company_id"),
        ("contacts", "contact_id"),
        ("deals", "deal_id"),
    ):
        rows = {r[key]: r for r in plan.tables[object_type]}
        for item in plan.inputs[object_type]:
            if rows[item["id"]]["owner_id"]:
                assert item["properties"]["hubspot_owner_id"] == OWNER
            else:
                assert "hubspot_owner_id" not in item["properties"]


def test_blank_values_are_left_out(plan):
    for inputs in plan.inputs.values():
        for item in inputs:
            assert all(v != "" and v is not None for v in item["properties"].values())


def test_input_shape(plan):
    for inputs in plan.inputs.values():
        for item in inputs:
            assert set(item) == {"idProperty", "id", "properties"}
            assert item["idProperty"] == "northwind_id"


def test_company_properties(plan):
    company = next(i for i in plan.inputs["companies"] if i["id"] == "COMP-0001")
    assert company["properties"] == {
        "name": "Quartz Stone Systems",
        "domain": "quartzstone.example.com",
        "industry": "FINANCIAL_SERVICES",
        "country": "FR",
        "northwind_employee_band": "1-10",
        "northwind_plan_tier": "starter",
        "hubspot_owner_id": OWNER,
        "northwind_created_at": "2024-05-02T08:16:32Z",
    }


def test_contact_properties(plan):
    contact = next(i for i in plan.inputs["contacts"] if i["id"] == "CONT-00001")
    assert contact["properties"] == {
        "firstname": "Shay",
        "lastname": "Millbrook",
        "email": "shay.millbrook@quartzstone.example.com",
        "jobtitle": "VP Sales",
        "lifecyclestage": "lead",
        "hubspot_owner_id": OWNER,
        "northwind_source": "OFFLINE",
        "northwind_created_at": "2024-05-02T08:16:32Z",
    }


def test_deal_properties(plan):
    deal = next(i for i in plan.inputs["deals"] if i["id"] == "DEAL-00001")
    row = next(d for d in plan.tables["deals"] if d["deal_id"] == "DEAL-00001")
    assert deal["properties"] == {
        "dealname": "Quartz Stone Systems - DEAL-00001",
        "pipeline": "default",
        "dealstage": "appointmentscheduled",
        "amount": "34500",
        "deal_currency_code": "USD",
        "closedate": f"{row['close_date']}T00:00:00Z",
        "hubspot_owner_id": OWNER,
        "northwind_created_at": "2024-11-12T06:08:24Z",
        "northwind_stage_entered_at": row["stage_entered_at"],
    }


def test_hubspot_domain_appends_com_to_the_example_tld():
    assert hubspot_domain("quartzstone.example") == "quartzstone.example.com"
    assert hubspot_domain("QuartzStone.Example") == "QuartzStone.Example.com"
    assert hubspot_domain("quartzstone.example.com") == "quartzstone.example.com"
    assert hubspot_domain("") == ""


def test_hubspot_email_rewrites_only_the_domain():
    assert hubspot_email("a.b@quartzstone.example") == "a.b@quartzstone.example.com"
    assert hubspot_email("a.b@quartzstone.example.com") == "a.b@quartzstone.example.com"
    assert hubspot_email("") == ""


def test_every_seeded_email_and_domain_is_under_example_com(plan):
    domains = [c["domain"] for c in plan.tables["companies"] if c["domain"]]
    emails = [c["email"] for c in plan.tables["contacts"] if c["email"]]
    assert domains and emails
    assert all(d.lower().endswith(".example.com") for d in domains)
    assert all(e.lower().endswith(".example.com") for e in emails)
    sent = [i["properties"].get("email", "") for i in plan.inputs["contacts"]]
    sent += [i["properties"].get("domain", "") for i in plan.inputs["companies"]]
    assert not [v for v in sent if v.lower().endswith(".example")]


def test_domain_rewrite_keeps_duplicate_domains_and_maps_each_email(generated, plan):
    tables, _ = generated
    before = {c["company_id"]: c["domain"].lower() for c in tables["companies"]}
    after = {c["company_id"]: c["domain"].lower() for c in plan.tables["companies"]}
    assert len(set(after.values())) == len({before[k] for k in after})
    emails = {c["contact_id"]: c["email"] for c in tables["contacts"]}
    for c in plan.tables["contacts"]:
        assert c["email"] == hubspot_email(emails[c["contact_id"]])


def test_deal_without_company_gets_a_plain_name(generated, plan):
    no_company = defect_ids(generated[1], "D19")
    for item in plan.inputs["deals"]:
        if item["id"] in no_company:
            assert item["properties"]["dealname"] == f"Northwind {item['id']}"


def test_blank_industry_is_left_out_and_others_are_mapped(generated, plan):
    blank = {r["company_id"] for r in plan.tables["companies"] if not r["industry"]}
    assert blank, "D04 should leave some industries blank"
    values = set(INDUSTRIES.values())
    for item in plan.inputs["companies"]:
        if item["id"] in blank:
            assert "industry" not in item["properties"]
        else:
            assert item["properties"]["industry"] in values


def test_same_inputs_give_identical_payloads(generated, plan):
    tables, manifest = generated
    again = build_seed(tables, manifest, SEED_DAY, OWNER, INDUSTRIES)
    assert json.dumps(again.inputs, sort_keys=True) == json.dumps(plan.inputs, sort_keys=True)
    assert again.associations == plan.associations


def test_associations(generated, plan):
    skipped_contacts = set(plan.skipped["contacts"])
    contacts = plan.tables["contacts"]
    deals = plan.tables["deals"]
    by_kind: dict[tuple[str, str], list[tuple[str, str]]] = {}
    for from_type, to_type, a, b in plan.associations:
        by_kind.setdefault((from_type, to_type), []).append((a, b))
    assert len(by_kind[("contacts", "companies")]) == sum(1 for c in contacts if c["company_id"])
    assert len(by_kind[("deals", "companies")]) == sum(1 for d in deals if d["company_id"])
    expected_deal_contacts = [
        d
        for d in deals
        if d["primary_contact_id"] and d["primary_contact_id"] not in skipped_contacts
    ]
    assert len(by_kind[("deals", "contacts")]) == len(expected_deal_contacts)
    assert sum(1 for d in deals if d["primary_contact_id"] in skipped_contacts) == 1


# ---------------------------------------------------------------- industry and owner


def test_industry_map_covers_every_generator_industry():
    assert set(INDUSTRIES) == set(generate.INDUSTRIES)


def test_industry_map_matches_the_live_portal_options():
    """tests/fixtures/hubspot_industry_options.json holds the 12 options the map uses, copied
    from the portal by probe P5 on 2026-11-12 (the other 136 options are left out)."""
    live = json.loads(
        (Path(__file__).parent / "fixtures" / "hubspot_industry_options.json").read_text(
            encoding="utf-8"
        )
    )
    assert len(live) == 12
    assert not any(o["hidden"] for o in live)
    assert industry_map(live) == INDUSTRIES


def test_industry_map_names_missing_labels():
    options = [o for o in INDUSTRY_OPTIONS if o["label"] != "Farming"]
    with pytest.raises(SeedError, match="Agriculture"):
        industry_map(options)


def test_resolve_owner_uses_the_only_active_owner():
    assert resolve_owner([{"id": "77", "archived": False}]) == "77"


def test_resolve_owner_needs_a_choice_when_there_are_several():
    owners = [{"id": "77", "archived": False}, {"id": "78", "archived": False}]
    with pytest.raises(SeedError, match="HUBSPOT_OWNER_ID"):
        resolve_owner(owners)
    assert resolve_owner(owners, configured="78") == "78"


def test_resolve_owner_rejects_an_unknown_or_missing_owner():
    with pytest.raises(SeedError, match="HUBSPOT_OWNER_ID"):
        resolve_owner([{"id": "77", "archived": False}], configured="99")
    with pytest.raises(SeedError, match="no active owner"):
        resolve_owner([])


# ---------------------------------------------------------------- applying the seed


def test_apply_runs_the_guard_first_and_sends_objects_in_order(plan):
    fake = FakeHubSpot()
    result = apply_seed(client_for(fake), plan, PORTAL)
    paths = [r.url.path for r in fake.requests]
    assert paths[0] == "/account-info/2026-09/details"
    first = {
        kind: next(i for i, p in enumerate(paths) if p.endswith(kind))
        for kind in (
            "companies/batch/upsert",
            "contacts/batch/upsert",
            "deals/batch/upsert",
            "batch/associate/default",
        )
    }
    assert list(first.values()) == sorted(first.values())
    assert any("/crm/properties/2026-09/" in p for p in paths[: first["companies/batch/upsert"]])
    assert result.created == {"companies": 203, "contacts": 1031, "deals": 570}
    assert result.associations == len(plan.associations)


def test_associations_use_hubspot_ids(plan):
    fake = FakeHubSpot()
    apply_seed(client_for(fake), plan, PORTAL)
    contact_hs = {hs: p["northwind_id"] for hs, p in fake.objects["contacts"].items()}
    company_hs = {hs: p["northwind_id"] for hs, p in fake.objects["companies"].items()}
    contact_company = {
        (contact_hs[a], company_hs[b])
        for f, t, a, b in fake.associations
        if (f, t) == ("contacts", "companies")
    }
    expected = {(a, b) for f, t, a, b in plan.associations if (f, t) == ("contacts", "companies")}
    assert contact_company == expected


def test_seeding_twice_creates_nothing_new(plan):
    fake = FakeHubSpot()
    apply_seed(client_for(fake), plan, PORTAL)
    first = [r.content for r in fake.sent("POST", "/batch/upsert")]
    counts = {t: len(fake.objects[t]) for t in fake.objects}
    result = apply_seed(client_for(fake), plan, PORTAL)
    second = [r.content for r in fake.sent("POST", "/batch/upsert")][len(first) :]
    assert result.created == {"companies": 0, "contacts": 0, "deals": 0}
    assert result.updated == {"companies": 203, "contacts": 1031, "deals": 570}
    assert {t: len(fake.objects[t]) for t in fake.objects} == counts
    assert second == first


def test_guard_failure_stops_before_any_write(plan):
    fake = FakeHubSpot(account_type="STANDARD")
    with pytest.raises(GuardError):
        apply_seed(client_for(fake), plan, PORTAL)
    assert fake.writes() == []


# ---------------------------------------------------------------- post-seed checks


def test_verify_passes_after_a_clean_seed(plan):
    fake = FakeHubSpot()
    client = client_for(fake)
    apply_seed(client, plan, PORTAL)
    assert verify_seed(client, plan, sleep=lambda s: None) == []


def test_verify_reports_auto_created_companies(plan):
    fake = FakeHubSpot(auto_company_for_contacts=True)
    client = client_for(fake)
    apply_seed(client, plan, PORTAL)
    problems = verify_seed(client, plan, sleep=lambda s: None, attempts=1)
    assert any("without northwind_id" in p for p in problems)


def test_verify_reports_a_filled_d16_lifecycle_stage(generated, plan):
    fake = FakeHubSpot()
    client = client_for(fake)
    apply_seed(client, plan, PORTAL)
    d16 = sorted(defect_ids(generated[1], "D16"))[0]
    fake.by_northwind_id("contacts")[d16]["lifecyclestage"] = "opportunity"
    problems = verify_seed(client, plan, sleep=lambda s: None, attempts=1)
    assert any("D16" in p and d16 in p for p in problems)


def test_verify_waits_for_search_to_catch_up(plan):
    fake = FakeHubSpot()
    client = client_for(fake)
    apply_seed(client, plan, PORTAL)
    fake.script("POST", "/companies/search", 200, body={"total": 0, "results": []})
    sleeps: list[float] = []
    assert verify_seed(client, plan, sleep=sleeps.append) == []
    assert sleeps


def test_verify_reports_missing_records_after_the_last_attempt(plan):
    fake = FakeHubSpot()
    client = client_for(fake)
    apply_seed(client, plan, PORTAL)
    fake.script("POST", "/deals/search", 200, body={"total": 3, "results": []}, times=5)
    problems = verify_seed(client, plan, sleep=lambda s: None, attempts=2)
    assert any("deals" in p and "570" in p for p in problems)


# ---------------------------------------------------------------- output files


def test_write_seed_output(tmp_path, plan):
    fake = FakeHubSpot()
    result = apply_seed(client_for(fake), plan, PORTAL)
    write_seed_output(tmp_path, plan, result, ran_at=datetime(2026, 11, 13, 9, 30))
    snapshot = read_csv_snapshot(tmp_path)
    assert snapshot.as_of == SEED_DAY
    assert len(snapshot.tables["deals"]) == 570
    manifest = json.loads((tmp_path / "defects_manifest.json").read_text(encoding="utf-8"))
    assert manifest == plan.manifest
    info = json.loads((tmp_path / "seed.json").read_text(encoding="utf-8"))
    assert info == {
        "seed": 42,
        "seed_day": "2026-11-13",
        "shift_days": 136,
        "owner_id": OWNER,
        "ran_at": "2026-11-13T09:30:00",
        "skipped": {"companies": 0, "contacts": 19, "deals": 11},
        "created": {"companies": 203, "contacts": 1031, "deals": 570},
        "updated": {"companies": 0, "contacts": 0, "deals": 0},
        "associations": len(plan.associations),
        "planted": 75,
    }
