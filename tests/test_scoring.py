"""Tests for monitor/scoring.py, on small hand-made inputs."""

from __future__ import annotations

from monitor.checks import Check
from monitor.scoring import in_scope_defect_ids, score, summary_line


def make_check(check_id: str, record_type: str, defects: tuple[str, ...]) -> Check:
    return Check(
        id=check_id,
        title=check_id,
        severity="high",
        owner="revops",
        record_type=record_type,
        defects=defects,
        why_it_matters="Test check.",
        sample_columns=("x",),
        sql="SELECT 1 AS record_id",
        source="test.yaml",
    )


def entry(defect_id: str, record_types: list[str], record_ids: list[str]) -> dict:
    return {
        "id": defect_id,
        "record_types": record_types,
        "count": len(record_ids),
        "record_ids": record_ids,
    }


MANIFEST = {
    "defects": [
        entry("D04", ["companies"], ["COMP-0001", "COMP-0002"]),
        entry("D17", ["contacts", "deals"], ["CONT-00001", "CONT-00002", "DEAL-00001"]),
        entry("D19", ["deals"], ["DEAL-00009"]),
        entry("D11", ["usage_events"], ["EVT-000001"]),
        entry("D15", ["support_tickets", "contacts"], ["TCK-00001"]),
    ]
}
IDS_BY_TYPE = {
    "owners": {"OWN-01"},
    "companies": {"COMP-0001", "COMP-0002", "COMP-0003"},
    "contacts": {"CONT-00001", "CONT-00002", "CONT-00003"},
    "deals": {"DEAL-00001", "DEAL-00009"},
}


def test_in_scope_defect_ids() -> None:
    assert in_scope_defect_ids(MANIFEST["defects"]) == ["D04", "D17", "D19"]


def test_caught_missed_and_false_positives() -> None:
    checks = [make_check("company_blank", "companies", ("D04",))]
    findings = {"company_blank": {"COMP-0001", "COMP-0003"}}
    result = score(findings, checks, MANIFEST, IDS_BY_TYPE)
    [check_score] = result.checks
    assert check_score.caught == {"COMP-0001"}
    assert check_score.missed == {"COMP-0002"}
    assert check_score.false_positives == {"COMP-0003"}


def test_split_check_is_limited_to_its_record_type() -> None:
    checks = [
        make_check("contact_no_owner", "contacts", ("D17",)),
        make_check("deal_no_owner", "deals", ("D17",)),
    ]
    findings = {"contact_no_owner": {"CONT-00001", "CONT-00002"}, "deal_no_owner": set()}
    result = score(findings, checks, MANIFEST, IDS_BY_TYPE)
    by_id = {c.check_id: c for c in result.checks}
    assert by_id["contact_no_owner"].planted == {"CONT-00001", "CONT-00002"}
    assert by_id["contact_no_owner"].missed == set()
    assert by_id["deal_no_owner"].planted == {"DEAL-00001"}
    assert by_id["deal_no_owner"].missed == {"DEAL-00001"}


def test_totals_count_out_of_scope_and_uncovered_defects() -> None:
    checks = [
        make_check("company_blank", "companies", ("D04",)),
        make_check("contact_no_owner", "contacts", ("D17",)),
    ]
    findings = {
        "company_blank": {"COMP-0001", "COMP-0002"},
        "contact_no_owner": {"CONT-00001", "CONT-00002", "CONT-00003"},
    }
    result = score(findings, checks, MANIFEST, IDS_BY_TYPE)
    # Planted in scope: D04 (2) + D17 (3) + D19 (1) = 6. D11 and D15 are out of scope.
    assert result.planted_total == 6
    assert result.caught_total == 4
    assert result.missed_total == 2  # DEAL-00001 (no deal check for D17) and DEAL-00009 (D19)
    assert result.false_positive_total == 1  # CONT-00003
    assert result.uncovered == ["D19"]
    assert result.out_of_scope == ["D11", "D15"]


def test_record_found_by_any_targeting_check_counts_once() -> None:
    checks = [
        make_check("a", "companies", ("D04",)),
        make_check("b", "companies", ("D04",)),
    ]
    findings = {"a": {"COMP-0001"}, "b": {"COMP-0001", "COMP-0002"}}
    result = score(findings, checks, MANIFEST, IDS_BY_TYPE)
    assert result.caught_total == 2
    assert result.false_positive_total == 0


def test_checks_without_findings_are_scored() -> None:
    checks = [make_check("company_blank", "companies", ("D04",))]
    result = score({}, checks, MANIFEST, IDS_BY_TYPE)
    assert result.checks[0].missed == {"COMP-0001", "COMP-0002"}


def test_summary_line() -> None:
    checks = [make_check("company_blank", "companies", ("D04",))]
    findings = {"company_blank": {"COMP-0001", "COMP-0003"}}
    result = score(findings, checks, MANIFEST, IDS_BY_TYPE)
    assert summary_line(result) == "caught 1 of 6 planted records, 1 false positives"
