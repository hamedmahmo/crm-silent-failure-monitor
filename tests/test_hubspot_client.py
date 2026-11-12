"""Tests for monitor/hubspot/client.py. Mocked transports only."""

from __future__ import annotations

import json
import logging

import httpx
import pytest

from monitor.hubspot import client as client_module
from monitor.hubspot.client import (
    API_VERSION,
    ConfigError,
    HubSpotClient,
    HubSpotError,
    chunks,
    required_env,
)

TOKEN = "pat-na1-test-token-0000"


def make(handler, sleeps: list[float] | None = None, **kwargs) -> HubSpotClient:
    record = sleeps if sleeps is not None else []
    return HubSpotClient(
        TOKEN, transport=httpx.MockTransport(handler), sleep=record.append, **kwargs
    )


def test_api_version_is_date_based():
    assert API_VERSION == "2026-09"


def test_bearer_header_on_every_request():
    seen = []

    def handler(request):
        seen.append(request.headers.get("authorization"))
        return httpx.Response(200, json={})

    c = make(handler)
    c.get("/account-info/2026-09/details")
    c.post("/crm/objects/2026-09/deals/search", {"filterGroups": []})
    assert seen == [f"Bearer {TOKEN}"] * 2


def test_token_not_in_repr_logs_or_errors(caplog):
    def handler(request):
        return httpx.Response(400, json={"message": "bad input", "category": "VALIDATION_ERROR"})

    c = make(handler)
    caplog.set_level(logging.DEBUG)
    with pytest.raises(HubSpotError) as exc_info:
        c.get("/crm/objects/2026-09/deals")
    assert TOKEN not in repr(c)
    assert TOKEN not in str(exc_info.value)
    assert TOKEN not in caplog.text


def test_error_message_has_hubspot_message_and_category():
    def handler(request):
        return httpx.Response(400, json={"message": "Property x does not exist", "category": "X"})

    with pytest.raises(HubSpotError, match="400.*X.*Property x does not exist"):
        make(handler).get("/crm/objects/2026-09/deals")


def test_400_fails_without_retry():
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(400, json={"message": "no"})

    with pytest.raises(HubSpotError):
        make(handler).get("/x")
    assert len(calls) == 1


def test_429_waits_for_retry_after():
    responses = iter(
        [
            httpx.Response(429, headers={"Retry-After": "3"}, json={"errorType": "RATE_LIMIT"}),
            httpx.Response(200, json={"ok": True}),
        ]
    )
    sleeps: list[float] = []
    assert make(lambda r: next(responses), sleeps).get("/x") == {"ok": True}
    assert sleeps == [3.0]


def test_5xx_backs_off_and_gives_up_after_max_attempts():
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(502, json={"message": "bad gateway"})

    sleeps: list[float] = []
    with pytest.raises(HubSpotError, match="502"):
        make(handler, sleeps).get("/x")
    assert len(calls) == 5
    assert len(sleeps) == 4
    assert all(later > earlier for earlier, later in zip(sleeps, sleeps[1:], strict=False))


def test_transport_error_is_retried():
    calls = []

    def handler(request):
        calls.append(1)
        if len(calls) == 1:
            raise httpx.ConnectError("connection reset")
        return httpx.Response(200, json={"ok": True})

    assert make(handler).get("/x") == {"ok": True}
    assert len(calls) == 2


def test_chunks():
    assert [len(c) for c in chunks(list(range(250)), 100)] == [100, 100, 50]
    assert chunks([], 100) == []


def test_batch_upsert_sends_100_per_call():
    sizes = []

    def handler(request):
        inputs = json.loads(request.content)["inputs"]
        sizes.append(len(inputs))
        assert request.url.path == "/crm/objects/2026-09/contacts/batch/upsert"
        return httpx.Response(
            200, json={"status": "COMPLETE", "results": [{"id": i["id"]} for i in inputs]}
        )

    inputs = [{"idProperty": "northwind_id", "id": f"C{i}", "properties": {}} for i in range(250)]
    results = make(handler).batch_upsert("contacts", inputs)
    assert sizes == [100, 100, 50]
    assert len(results) == 250


def test_batch_errors_in_a_200_body_raise():
    def handler(request):
        return httpx.Response(
            200,
            json={
                "status": "COMPLETE",
                "results": [],
                "numErrors": 1,
                "errors": [{"message": "Property values were not valid", "category": "X"}],
            },
        )

    with pytest.raises(HubSpotError, match="Property values were not valid"):
        make(handler).batch_upsert("deals", [{"idProperty": "northwind_id", "id": "D1"}])


def test_207_multi_status_raises():
    def handler(request):
        return httpx.Response(207, json={"status": "COMPLETE", "results": [], "numErrors": 2})

    with pytest.raises(HubSpotError, match="2 errors"):
        make(handler).batch_upsert("deals", [{"idProperty": "northwind_id", "id": "D1"}])


def test_associate_sends_2000_per_call_to_default_endpoint():
    sizes = []

    def handler(request):
        assert (
            request.url.path == "/crm/associations/2026-09/deals/companies/batch/associate/default"
        )
        sizes.append(len(json.loads(request.content)["inputs"]))
        return httpx.Response(200, json={"status": "COMPLETE", "results": []})

    pairs = [(str(i), str(i + 1)) for i in range(2500)]
    assert make(handler).associate("deals", "companies", pairs) == 2500
    assert sizes == [2000, 500]


def test_search_follows_paging_and_paces_requests():
    pages = {
        None: {
            "total": 3,
            "results": [{"id": "1"}, {"id": "2"}],
            "paging": {"next": {"after": "2"}},
        },
        "2": {"total": 3, "results": [{"id": "3"}]},
    }
    bodies = []

    def handler(request):
        body = json.loads(request.content)
        bodies.append(body)
        return httpx.Response(200, json=pages[body.get("after")])

    clock = iter([0.0, 0.05, 0.05])
    sleeps: list[float] = []
    c = make(handler, sleeps, clock=lambda: next(clock))
    filters = [{"propertyName": "northwind_id", "operator": "HAS_PROPERTY"}]
    results = c.search("contacts", filters, properties=["northwind_id"])
    assert [r["id"] for r in results] == ["1", "2", "3"]
    assert bodies[0]["filterGroups"] == [{"filters": filters}]
    assert bodies[0]["limit"] == 200
    assert bodies[0]["properties"] == ["northwind_id"]
    assert sleeps and sleeps[0] == pytest.approx(0.15)


def test_count_uses_search_total():
    def handler(request):
        assert json.loads(request.content)["limit"] == 1
        return httpx.Response(200, json={"total": 42, "results": [{"id": "1"}]})

    assert make(handler).count("companies", []) == 42


def test_batch_archive_sends_100_per_call():
    sizes = []

    def handler(request):
        assert request.url.path == "/crm/objects/2026-09/deals/batch/archive"
        sizes.append(len(json.loads(request.content)["inputs"]))
        return httpx.Response(204)

    assert make(handler).batch_archive("deals", [str(i) for i in range(150)]) == 150
    assert sizes == [100, 50]


def test_required_env_names_the_missing_variable(monkeypatch):
    with pytest.raises(ConfigError, match="HUBSPOT_ACCESS_TOKEN"):
        required_env("HUBSPOT_ACCESS_TOKEN")
    monkeypatch.setenv("HUBSPOT_ACCESS_TOKEN", "  ")
    with pytest.raises(ConfigError):
        required_env("HUBSPOT_ACCESS_TOKEN")
    monkeypatch.setenv("HUBSPOT_ACCESS_TOKEN", TOKEN)
    assert required_env("HUBSPOT_ACCESS_TOKEN") == TOKEN


def test_missing_token_error_does_not_contain_other_values(monkeypatch):
    monkeypatch.setenv("HUBSPOT_PORTAL_ID", "999")
    with pytest.raises(ConfigError) as exc_info:
        required_env("HUBSPOT_ACCESS_TOKEN")
    assert "999" not in str(exc_info.value)


def test_load_env_reads_the_repo_env_file(monkeypatch):
    calls = []
    monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **k: calls.append((a, k)) or True)
    client_module.load_env()
    assert calls and str(calls[0][0][0]).endswith(".env")
    assert calls[0][1].get("override") is False


def test_real_transport_is_blocked_in_tests():
    c = HubSpotClient(TOKEN, sleep=lambda s: None, max_attempts=1)
    with pytest.raises((RuntimeError, HubSpotError), match="network access in tests"):
        c.get("/account-info/2026-09/details")
