"""Tests for monitor/report.py: the report model and its HTML and markdown renderings."""

from __future__ import annotations

import re
from datetime import date, datetime
from pathlib import Path

import pytest

import simulate_day2
from monitor.checks import SEVERITIES, load_checks
from monitor.params import load_params
from monitor.pipeline import run_once
from monitor.report import (
    PALETTE,
    Card,
    ErrorCard,
    Report,
    SampleRow,
    ScoreSummary,
    TableDiff,
    card_status,
    display_value,
    render_html,
    render_markdown,
    sort_cards,
)
from monitor.snapshot import connect

RUN_AT = datetime(2026, 7, 1, 6, 30, 0)
STATUSES = ("new", "persisting", "resolved")


def make_card(check_id: str, severity: str, status: str, **overrides: object) -> Card:
    fields: dict[str, object] = {
        "check_id": check_id,
        "title": f"Title of {check_id}",
        "severity": severity,
        "owner": "revops",
        "why_it_matters": f"Why {check_id} matters.",
        "record_type": "deals",
        "status": status,
        "open_count": 0 if status == "resolved" else 3,
        "new_count": 2 if status == "new" else 0,
        "persisting_count": 0 if status == "resolved" else (1 if status == "new" else 3),
        "resolved_count": 2 if status == "resolved" else 0,
        "sample_columns": ("deal_id", "amount"),
        "samples": (
            SampleRow("DEAL-00001", "new" if status == "new" else status, ("DEAL-00001", None)),
        ),
    }
    fields.update(overrides)
    return Card(**fields)  # type: ignore[arg-type]


def make_report(**overrides: object) -> Report:
    fields: dict[str, object] = {
        "project": "CRM Silent-Failure Monitor",
        "run_label": "day2",
        "run_at": RUN_AT,
        "as_of": date(2026, 7, 1),
        "source_label": "Offline demo (CSV)",
        "previous_label": "day1",
        "previous_as_of": date(2026, 6, 30),
        "open_issues": 6,
        "new_issues": 2,
        "resolved_issues": 2,
        "records_affected": 5,
        "score": ScoreSummary(
            caught=6, planted=6, false_positives=0, rows=(), out_of_scope=("D05",)
        ),
        "cards": (
            make_card("deal_amount_too_large", "critical", "new", owner="finance"),
            make_card("deal_no_company", "high", "persisting"),
            make_card("contact_no_owner", "medium", "resolved", owner="sales-ops"),
        ),
        "errors": (),
        "passing": ("Deal with no owner",),
        "record_diff": (TableDiff("deals", 1, 2, 3),),
    }
    fields.update(overrides)
    return Report(**fields)  # type: ignore[arg-type]


def cards_in(html: str) -> list[dict[str, str]]:
    return [
        {"id": m.group(1), "severity": m.group(2), "status": m.group(3), "owner": m.group(4)}
        for m in re.finditer(
            r'<article class="card" id="check-([a-z0-9_]+)" data-severity="([a-z]+)" '
            r'data-status="([a-z]+)" data-owner="([a-z-]+)"',
            html,
        )
    ]


def select_options(html: str, name: str) -> list[str]:
    block = re.search(rf'<select id="filter-{name}".*?</select>', html, re.S)
    assert block, name
    return [v for v in re.findall(r'<option value="([^"]*)"', block.group(0)) if v]


# ---------------------------------------------------------------- model rules


@pytest.mark.parametrize(
    ("new", "persisting", "resolved", "expected"),
    [
        (2, 3, 0, "new"),
        (1, 0, 4, "new"),
        (0, 3, 1, "persisting"),
        (0, 0, 2, "resolved"),
        (0, 0, 0, None),
    ],
)
def test_card_status(new: int, persisting: int, resolved: int, expected: str | None) -> None:
    assert card_status(new, persisting, resolved) == expected


def test_sort_cards_by_severity_then_status_then_count() -> None:
    cards = [
        make_card("low_new", "low", "new"),
        make_card("high_resolved", "high", "resolved"),
        make_card("high_persisting_big", "high", "persisting", open_count=9),
        make_card("high_persisting_small", "high", "persisting", open_count=2),
        make_card("high_new", "high", "new"),
        make_card("critical_persisting", "critical", "persisting"),
    ]
    assert [c.check_id for c in sort_cards(cards)] == [
        "critical_persisting",
        "high_new",
        "high_persisting_big",
        "high_persisting_small",
        "high_resolved",
        "low_new",
    ]


def test_display_value_marks_blank_and_edge_whitespace() -> None:
    assert display_value(None) == ("blank", True)
    assert display_value("closedwon ") == ("closedwon␠", True)
    assert display_value(" x") == ("␠x", True)
    assert display_value("closedwon") == ("closedwon", False)


# ---------------------------------------------------------------- HTML


def test_header_and_tiles() -> None:
    html = render_html(make_report())
    assert "<h1>CRM Silent-Failure Monitor</h1>" in html
    assert "2026-07-01 06:30 UTC" in html
    assert "Offline demo (CSV)" in html
    assert "compared with day1 (as of 2026-06-30)" in html
    tiles = dict(
        re.findall(
            r'<div class="tile" data-tile="([a-z-]+)">.*?<p class="value">(.*?)</p>', html, re.S
        )
    )
    assert tiles == {
        "open": "6",
        "new": "2",
        "resolved": "2",
        "records": "5",
        "score": "6 of 6",
    }
    assert "0 false positives" in html


def test_score_tile_without_manifest() -> None:
    html = render_html(make_report(score=None))
    assert re.search(r'data-tile="score">.*?<p class="value">not scored</p>', html, re.S)


def test_cards_render_in_given_order_with_data_attributes() -> None:
    html = render_html(make_report())
    assert cards_in(html) == [
        {
            "id": "deal_amount_too_large",
            "severity": "critical",
            "status": "new",
            "owner": "finance",
        },
        {"id": "deal_no_company", "severity": "high", "status": "persisting", "owner": "revops"},
        {
            "id": "contact_no_owner",
            "severity": "medium",
            "status": "resolved",
            "owner": "sales-ops",
        },
    ]
    assert "Why deal_no_company matters." in html
    assert ">NEW<" in html and ">PERSISTING<" in html and ">RESOLVED<" in html


def test_filters_list_values_present() -> None:
    html = render_html(make_report())
    assert select_options(html, "severity") == ["critical", "high", "medium"]
    assert select_options(html, "owner") == ["finance", "revops", "sales-ops"]
    assert select_options(html, "status") == ["new", "persisting", "resolved"]


def test_sample_table_marks_blank_values() -> None:
    html = render_html(make_report())
    assert '<span class="blank">blank</span>' in html


def test_errors_render_before_cards() -> None:
    error = ErrorCard("broken_check", "Broken check", "high", "revops", "BinderException: <bad>")
    html = render_html(make_report(errors=(error,)))
    assert html.index("broken_check") < html.index('id="check-deal_amount_too_large"')
    assert "BinderException: &lt;bad&gt;" in html


def test_html_is_escaped() -> None:
    card = make_card("deal_no_company", "high", "new", title="<script>alert(1)</script>")
    html = render_html(make_report(cards=(card,)))
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html


def test_html_is_self_contained() -> None:
    html = render_html(make_report())
    assert "http://" not in html and "https://" not in html
    assert "<link" not in html
    assert "@import" not in html
    assert not re.search(r"<(script|img|iframe)[^>]+src=", html)
    assert '<meta name="viewport"' in html
    assert "@media (prefers-color-scheme: dark)" in html
    assert "@media print" in html


# ---------------------------------------------------------------- contrast


def luminance(hex_color: str) -> float:
    channels = [int(hex_color.lstrip("#")[i : i + 2], 16) / 255 for i in (0, 2, 4)]
    linear = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def contrast(a: str, b: str) -> float:
    high, low = sorted([luminance(a), luminance(b)], reverse=True)
    return (high + 0.05) / (low + 0.05)


@pytest.mark.parametrize("mode", ["light", "dark"])
def test_contrast_meets_wcag_aa(mode: str) -> None:
    tokens = PALETTE[mode]
    pairs = [
        ("text", "surface"),
        ("text", "page"),
        ("text-secondary", "surface"),
        ("text-secondary", "page"),
        ("accent-text", "surface"),
    ]
    pairs += [(f"sev-{s}-fg", f"sev-{s}-bg") for s in SEVERITIES]
    pairs += [(f"pill-{s}-fg", f"pill-{s}-bg") for s in STATUSES]
    for fg, bg in pairs:
        ratio = contrast(tokens[fg], tokens[bg])
        assert ratio >= 4.5, f"{mode}: {fg} on {bg} is {ratio:.2f}:1"


def test_palette_tokens_used_in_css() -> None:
    html = render_html(make_report())
    for mode in ("light", "dark"):
        for name, value in PALETTE[mode].items():
            assert f"--{name}: {value};" in html, (mode, name)


# ---------------------------------------------------------------- markdown


def test_markdown_summary_and_order() -> None:
    md = render_markdown(make_report())
    assert md.startswith("# CRM Silent-Failure Monitor")
    assert "caught 6 of 6 planted records, 0 false positives" in md
    assert "| Open issues | 6 |" in md
    positions = [md.index(f"`{cid}`") for cid in ("deal_amount_too_large", "deal_no_company")]
    assert positions == sorted(positions)
    assert "contact_no_owner" in md


def test_markdown_escapes_pipes() -> None:
    card = make_card("deal_no_company", "high", "new", title="a | b")
    md = render_markdown(make_report(cards=(card,)))
    assert "a \\| b" in md


# ---------------------------------------------------------------- real day-2 report


@pytest.fixture(scope="module")
def day2_report(tmp_path_factory: pytest.TempPathFactory, northwind_defect_dir: Path) -> Report:
    tmp = tmp_path_factory.mktemp("report")
    simulate_day2.simulate(northwind_defect_dir, tmp / "day2", seed=42)
    checks, params = load_checks(), load_params()
    with connect(tmp / "monitor.duckdb") as con:
        run_once(con, northwind_defect_dir, "day1", checks, params, loaded_at=RUN_AT)
        return run_once(con, tmp / "day2", "day2", checks, params, loaded_at=RUN_AT).report


def test_real_report_has_every_status(day2_report: Report) -> None:
    assert {c.status for c in day2_report.cards} == set(STATUSES)
    assert day2_report.previous_label == "day1"
    assert day2_report.score is not None
    assert day2_report.score.false_positives == 0
    assert day2_report.score.caught == day2_report.score.planted


def test_real_report_cards_sorted(day2_report: Report) -> None:
    assert list(day2_report.cards) == sort_cards(list(day2_report.cards))


def test_real_report_samples(day2_report: Report) -> None:
    for card in day2_report.cards:
        assert 1 <= len(card.samples) <= 10, card.check_id
        statuses = [row.status for row in card.samples]
        assert statuses == sorted(statuses, key=STATUSES.index), card.check_id
        assert all(len(row.values) == len(card.sample_columns) for row in card.samples)
        if card.status == "resolved":
            assert set(statuses) == {"resolved"}


def test_real_report_totals(day2_report: Report) -> None:
    cards = day2_report.cards
    assert day2_report.open_issues == sum(c.open_count for c in cards)
    assert day2_report.new_issues == sum(c.new_count for c in cards)
    assert day2_report.resolved_issues == sum(c.resolved_count for c in cards)
    assert day2_report.records_affected <= day2_report.open_issues
    assert day2_report.run_at == RUN_AT
