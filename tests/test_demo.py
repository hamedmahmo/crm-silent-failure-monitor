"""Tests for the monitor CLI: `monitor run` and the offline `monitor demo`."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from monitor import cli

TIMESTAMP_RE = re.compile(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2} UTC")
SCORE_RE = re.compile(r"caught (\d+) of (\d+) planted records, (\d+) false positives")


@pytest.fixture()
def opened(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    calls: list[str] = []
    monkeypatch.setattr(cli.webbrowser, "open", lambda url: calls.append(url) or True)
    monkeypatch.delenv("CI", raising=False)
    return calls


def run_demo(tmp_path: Path, *extra: str) -> int:
    return cli.main(
        ["demo", "--out", str(tmp_path / "out"), "--reports", str(tmp_path / "reports"), *extra]
    )


def test_demo_runs_both_days_and_scores_them(tmp_path: Path, capsys, opened) -> None:
    assert run_demo(tmp_path, "--no-open") == 0
    out = capsys.readouterr().out
    scores = SCORE_RE.findall(out)
    assert len(scores) == 2, out
    for caught, planted, false_positives in scores:
        assert caught == planted
        assert false_positives == "0"
    assert "day1" in out and "day2" in out
    assert re.search(r"day2 .*\d+ new, \d+ persisting, \d+ resolved", out), out
    for name in ["day1.html", "day1.md", "day2.html", "day2.md", "latest.html", "latest.md"]:
        assert (tmp_path / "reports" / name).is_file(), name
    latest = (tmp_path / "reports" / "latest.html").read_text(encoding="utf-8")
    assert latest == (tmp_path / "reports" / "day2.html").read_text(encoding="utf-8")
    assert opened == []


def test_demo_is_repeatable_apart_from_timestamps(tmp_path: Path, opened) -> None:
    assert run_demo(tmp_path / "a", "--no-open") == 0
    assert run_demo(tmp_path / "b", "--no-open") == 0
    for name in ["latest.html", "latest.md"]:
        a = (tmp_path / "a" / "reports" / name).read_text(encoding="utf-8")
        b = (tmp_path / "b" / "reports" / name).read_text(encoding="utf-8")
        assert TIMESTAMP_RE.search(a), name
        assert TIMESTAMP_RE.sub("<ts>", a) == TIMESTAMP_RE.sub("<ts>", b), name


def test_demo_rerun_starts_from_a_fresh_database(tmp_path: Path, capsys, opened) -> None:
    run_demo(tmp_path, "--no-open")
    first = capsys.readouterr().out
    run_demo(tmp_path, "--no-open")
    assert capsys.readouterr().out == first


def test_demo_opens_latest_report(tmp_path: Path, opened) -> None:
    assert run_demo(tmp_path) == 0
    assert len(opened) == 1
    assert opened[0].startswith("file:")
    assert opened[0].endswith("latest.html")


def test_demo_does_not_open_browser_in_ci(
    tmp_path: Path, opened, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CI", "true")
    assert run_demo(tmp_path) == 0
    assert opened == []


def test_run_on_clean_data_finds_nothing(tmp_path: Path, capsys, northwind_clean_dir: Path) -> None:
    code = cli.main(
        [
            "run",
            "--source", "csv",
            "--path", str(northwind_clean_dir),
            "--db", str(tmp_path / "m.duckdb"),
            "--label", "clean",
            "--reports", str(tmp_path / "reports"),
        ]
    )  # fmt: skip
    assert code == 0
    out = capsys.readouterr().out
    assert "0 open issues" in out
    assert "not scored" in out  # clean output has no manifest
    assert (tmp_path / "reports" / "latest.html").is_file()


def test_run_fresh_resets_the_database(tmp_path: Path, capsys, northwind_defect_dir: Path) -> None:
    args = [
        "run",
        "--path", str(northwind_defect_dir),
        "--db", str(tmp_path / "m.duckdb"),
        "--label", "x",
        "--reports", str(tmp_path / "reports"),
    ]  # fmt: skip
    cli.main(args)
    cli.main(args)
    assert "0 new" in capsys.readouterr().out  # second run compared with the first
    cli.main([*args, "--fresh"])
    out = capsys.readouterr().out
    assert "no previous run" in out


def test_run_exits_1_when_a_check_errors(tmp_path: Path, capsys, northwind_clean_dir: Path) -> None:
    checks = tmp_path / "checks"
    checks.mkdir()
    (checks / "bad.yaml").write_text(
        "- id: broken\n"
        "  title: Broken\n"
        "  severity: high\n"
        "  owner: revops\n"
        "  record_type: deals\n"
        "  defects: [D19]\n"
        "  why_it_matters: Test.\n"
        "  sample_columns: [deal_id]\n"
        "  sql: SELECT nope FROM missing_table\n",
        encoding="utf-8",
    )
    code = cli.main(
        [
            "run",
            "--path", str(northwind_clean_dir),
            "--db", str(tmp_path / "m.duckdb"),
            "--label", "x",
            "--reports", str(tmp_path / "reports"),
            "--checks", str(checks),
        ]
    )  # fmt: skip
    assert code == 1
    assert "broken" in capsys.readouterr().out
