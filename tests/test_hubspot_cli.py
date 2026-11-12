"""Tests for `python -m monitor hubspot ...`. Mocked transports only."""

from __future__ import annotations

import json
from datetime import date

import pytest
from hubspot_fake import FakeHubSpot

from monitor import cli
from monitor.hubspot.client import HubSpotClient


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> FakeHubSpot:
    fake = FakeHubSpot(portal_id=4242)
    monkeypatch.setattr(
        cli,
        "make_hubspot_client",
        lambda token: HubSpotClient(token, transport=fake.transport(), sleep=lambda s: None),
    )
    monkeypatch.setattr("monitor.hubspot.seed.time.sleep", lambda s: None)
    monkeypatch.setattr("monitor.hubspot.probe.time.sleep", lambda s: None)
    return fake


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HUBSPOT_ACCESS_TOKEN", "pat-test")
    monkeypatch.setenv("HUBSPOT_PORTAL_ID", "4242")


@pytest.mark.parametrize("command", [["check"], ["probe"], ["seed"], ["reset"], ["reset", "--yes"]])
def test_missing_environment_exits_2_before_any_request(fake, command, capsys):
    assert cli.main(["hubspot", *command]) == 2
    assert "HUBSPOT_ACCESS_TOKEN" in capsys.readouterr().err
    assert fake.requests == []


def test_missing_portal_id_exits_2(fake, monkeypatch, capsys):
    monkeypatch.setenv("HUBSPOT_ACCESS_TOKEN", "pat-test")
    assert cli.main(["hubspot", "check"]) == 2
    assert "HUBSPOT_PORTAL_ID" in capsys.readouterr().err
    assert fake.requests == []


def test_check_is_read_only(fake, env, capsys):
    assert cli.main(["hubspot", "check"]) == 0
    out = capsys.readouterr().out
    assert "developer test account" in out
    assert "pat-test" not in out
    assert fake.writes() == []


def test_check_refuses_a_standard_account(fake, env, capsys):
    fake.account_type = "STANDARD"
    assert cli.main(["hubspot", "check"]) == 1
    assert "not a developer test account" in capsys.readouterr().err


def test_seed_writes_output_and_prints_counts(fake, env, tmp_path, capsys):
    code = cli.main(["hubspot", "seed", "--out", str(tmp_path), "--day", "2026-11-13"])
    out = capsys.readouterr().out
    assert code == 0, out
    assert "203 companies, 1031 contacts, 570 deals" in out
    assert "75 planted records" in out
    info = json.loads((tmp_path / "seed.json").read_text(encoding="utf-8"))
    assert info["seed_day"] == "2026-11-13"
    assert info["owner_id"] == "77"


def test_seed_defaults_to_today(fake, env, tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "today_utc", lambda: date(2026, 12, 1))
    assert cli.main(["hubspot", "seed", "--out", str(tmp_path)]) == 0
    info = json.loads((tmp_path / "seed.json").read_text(encoding="utf-8"))
    assert info["seed_day"] == "2026-12-01"


def test_seed_exits_1_when_post_seed_checks_fail(fake, env, tmp_path, capsys):
    fake.auto_company_for_contacts = True
    assert cli.main(["hubspot", "seed", "--out", str(tmp_path), "--day", "2026-11-13"]) == 1
    assert "without northwind_id" in capsys.readouterr().err


def test_seed_uses_hubspot_owner_id_when_several_owners(fake, env, tmp_path, monkeypatch, capsys):
    fake.owners = [{"id": "77", "archived": False}, {"id": "78", "archived": False}]
    assert cli.main(["hubspot", "seed", "--out", str(tmp_path), "--day", "2026-11-13"]) == 1
    assert "HUBSPOT_OWNER_ID" in capsys.readouterr().err
    assert fake.writes() == []
    monkeypatch.setenv("HUBSPOT_OWNER_ID", "78")
    assert cli.main(["hubspot", "seed", "--out", str(tmp_path), "--day", "2026-11-13"]) == 0


def test_reset_without_yes_deletes_nothing(fake, env, tmp_path, capsys):
    cli.main(["hubspot", "seed", "--out", str(tmp_path), "--day", "2026-11-13"])
    capsys.readouterr()
    assert cli.main(["hubspot", "reset"]) == 0
    out = capsys.readouterr().out
    assert "dry run" in out
    assert "203 companies, 1031 contacts, 570 deals" in out
    assert fake.archived == []


def test_reset_yes_archives(fake, env, tmp_path, capsys):
    cli.main(["hubspot", "seed", "--out", str(tmp_path), "--day", "2026-11-13"])
    assert cli.main(["hubspot", "reset", "--yes"]) == 0
    assert len(fake.archived) == 203 + 1031 + 570


def test_probe_writes_the_verification_doc(fake, env, tmp_path, capsys):
    doc = tmp_path / "hubspot-verification.md"
    options = tmp_path / "industry_options.json"
    assert cli.main(["hubspot", "probe", "--doc", str(doc), "--options-out", str(options)]) == 0
    assert "| P1 |" in doc.read_text(encoding="utf-8")
    assert json.loads(options.read_text(encoding="utf-8"))[0]["value"] == "COMPUTER_SOFTWARE"
    assert "P1" in capsys.readouterr().out


def test_probe_exits_1_when_a_probe_contradicts_the_plan(fake, env, tmp_path):
    fake.auto_company_for_contacts = True
    args = ["--doc", str(tmp_path / "v.md"), "--options-out", str(tmp_path / "o.json")]
    assert cli.main(["hubspot", "probe", *args]) == 1


def test_makefile_has_the_hubspot_targets():
    from monitor import ROOT

    text = (ROOT / "Makefile").read_text(encoding="utf-8")
    assert "hubspot-check:" in text
    assert "seed-hubspot:" in text
    assert "reset-hubspot:" in text
    reset_recipe = text.split("reset-hubspot:")[1].split("\n\n")[0]
    assert "--yes" not in reset_recipe
