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


# ---------------------------------------------------------------- run --source hubspot


def seed_key(tmp_path, day: str = "2026-11-13"):
    """Seed the fake portal and return the answer key folder."""
    key = tmp_path / "key"
    assert cli.main(["hubspot", "seed", "--out", str(key), "--day", day]) == 0
    return key


def live_args(tmp_path, *extra: str) -> list[str]:
    return [
        "run",
        "--source",
        "hubspot",
        "--db",
        str(tmp_path / "live.duckdb"),
        "--reports",
        str(tmp_path / "reports"),
        *extra,
    ]


def test_run_hubspot_needs_the_environment_before_any_request(fake, capsys):
    assert cli.main(["run", "--source", "hubspot"]) == 2
    assert "HUBSPOT_ACCESS_TOKEN" in capsys.readouterr().err
    assert fake.requests == []


def test_run_csv_still_needs_a_path(capsys):
    assert cli.main(["run"]) == 2
    assert "--path" in capsys.readouterr().err


@pytest.mark.parametrize(
    "extra", [["--as-of", "2026-11-13"], ["--manifest", "x.json"], ["--save-snapshot", "x"]]
)
def test_hubspot_only_options_are_refused_for_csv(extra, tmp_path, capsys):
    assert cli.main(["run", "--path", str(tmp_path), *extra]) == 2
    assert "--source hubspot" in capsys.readouterr().err


def test_run_hubspot_scores_against_the_answer_key(fake, env, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HUBSPOT_PORTAL_ID", "987654321")
    fake.portal_id = 987654321
    key = seed_key(tmp_path)
    capsys.readouterr()
    args = ["--as-of", "2026-11-13", "--manifest", str(key / "defects_manifest.json")]
    assert cli.main(live_args(tmp_path, *args)) == 0, capsys.readouterr()
    out = capsys.readouterr().out
    assert "hubspot-2026-11-13 (as of 2026-11-13)" in out
    assert "caught 75 of 75 planted records, 0 false positives" in out
    for name in ("hubspot-2026-11-13.html", "hubspot-2026-11-13.md", "latest.html"):
        text = (tmp_path / "reports" / name).read_text(encoding="utf-8")
        assert "987654321" not in text
        assert "pat-test" not in text


def test_second_live_run_shows_everything_persisting(fake, env, tmp_path, capsys):
    key = seed_key(tmp_path)
    args = ["--as-of", "2026-11-13", "--manifest", str(key / "defects_manifest.json")]
    assert cli.main(live_args(tmp_path, "--label", "first", *args)) == 0
    capsys.readouterr()
    assert cli.main(live_args(tmp_path, "--label", "second", *args)) == 0
    assert (
        "75 open issues in 13 checks: 0 new, 75 persisting, 0 resolved" in capsys.readouterr().out
    )


def test_as_of_defaults_to_today_and_no_manifest_means_not_scored(
    fake, env, tmp_path, monkeypatch, capsys
):
    seed_key(tmp_path)
    monkeypatch.setattr(cli, "today_utc", lambda: date(2026, 11, 13))
    capsys.readouterr()
    assert cli.main(live_args(tmp_path)) == 0
    out = capsys.readouterr().out
    assert "hubspot-2026-11-13 (as of 2026-11-13)" in out
    assert "not scored" in out


def test_missing_manifest_file_is_not_scored(fake, env, tmp_path, capsys):
    seed_key(tmp_path)
    capsys.readouterr()
    missing = tmp_path / "nope" / "defects_manifest.json"
    args = ["--as-of", "2026-11-13", "--manifest", str(missing)]
    assert cli.main(live_args(tmp_path, *args)) == 0
    assert f"not scored (no answer key at {missing})" in capsys.readouterr().out


def test_answer_key_for_another_day_is_flagged(fake, env, tmp_path, capsys):
    key = seed_key(tmp_path)
    capsys.readouterr()
    args = ["--as-of", "2026-11-20", "--manifest", str(key / "defects_manifest.json")]
    cli.main(live_args(tmp_path, *args))
    assert "answer key is for 2026-11-13" in capsys.readouterr().out


def test_save_snapshot_writes_a_csv_snapshot(fake, env, tmp_path):
    from monitor.snapshot import read_csv_snapshot

    seed_key(tmp_path)
    live = tmp_path / "live"
    assert cli.main(live_args(tmp_path, "--as-of", "2026-11-13", "--save-snapshot", str(live))) == 0
    snap = read_csv_snapshot(live)
    assert snap.as_of == date(2026, 11, 13)
    assert {t: len(rows) for t, rows in snap.tables.items()} == {
        "owners": 1,
        "companies": 203,
        "contacts": 1031,
        "deals": 570,
    }


def test_live_runs_default_to_their_own_database_and_reports(fake, env, tmp_path, monkeypatch):
    seed_key(tmp_path)
    monkeypatch.chdir(tmp_path)
    assert cli.main(["run", "--source", "hubspot", "--as-of", "2026-11-13"]) == 0
    assert (tmp_path / "data" / "output" / "hubspot-live.duckdb").is_file()
    assert (tmp_path / "reports" / "hubspot" / "latest.html").is_file()
    assert not (tmp_path / "reports" / "latest.html").exists()


def test_run_hubspot_refuses_a_standard_account(fake, env, tmp_path, capsys):
    fake.account_type = "STANDARD"
    assert cli.main(live_args(tmp_path)) == 1
    assert "not a developer test account" in capsys.readouterr().err


def test_run_hubspot_stage_mismatch_exits_2(fake, env, tmp_path, capsys):
    fake.pipelines[0]["stages"] = fake.pipelines[0]["stages"][:-1]
    assert cli.main(live_args(tmp_path)) == 2
    assert "closedlost" in capsys.readouterr().err


def test_makefile_has_the_snapshot_target():
    from monitor import ROOT

    text = (ROOT / "Makefile").read_text(encoding="utf-8")
    recipe = text.split("snapshot-hubspot:")[1].split("\n\n")[0]
    assert "--source hubspot" in recipe
    for word in ("seed", "reset", "--yes", "probe"):
        assert word not in recipe.replace("--save-snapshot", "")


def test_makefile_has_the_hubspot_targets():
    from monitor import ROOT

    text = (ROOT / "Makefile").read_text(encoding="utf-8")
    assert "hubspot-check:" in text
    assert "seed-hubspot:" in text
    assert "reset-hubspot:" in text
    reset_recipe = text.split("reset-hubspot:")[1].split("\n\n")[0]
    assert "--yes" not in reset_recipe
