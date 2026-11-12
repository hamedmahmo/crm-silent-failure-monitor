"""Score a run's findings against defects_manifest.json.

The planted set for a check is the manifest record_ids of the defects it targets, limited to
IDs that exist in the check's record_type table. That limit lets one defect that spans two
tables (D17: contacts and deals) be scored by two checks.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from monitor.checks import Check
from monitor.snapshot import SCOPE_TABLES


@dataclass(frozen=True)
class CheckScore:
    check_id: str
    defects: tuple[str, ...]
    planted: set[str]
    found: set[str]

    @property
    def caught(self) -> set[str]:
        return self.found & self.planted

    @property
    def missed(self) -> set[str]:
        return self.planted - self.found

    @property
    def false_positives(self) -> set[str]:
        return self.found - self.planted


@dataclass(frozen=True)
class Score:
    checks: list[CheckScore]
    planted_total: int
    caught_total: int
    false_positive_total: int
    uncovered: list[str]  # in-scope defects that no check targets
    out_of_scope: list[str]

    @property
    def missed_total(self) -> int:
        return self.planted_total - self.caught_total


def in_scope_defect_ids(entries: Iterable[Mapping]) -> list[str]:
    """Defects whose record types are all tables the monitor snapshots."""
    return [e["id"] for e in entries if set(e["record_types"]) <= set(SCOPE_TABLES)]


def score(
    findings: Mapping[str, set[str]],
    checks: list[Check],
    manifest: Mapping,
    ids_by_type: Mapping[str, set[str]],
) -> Score:
    entries = {e["id"]: e for e in manifest["defects"]}
    in_scope = in_scope_defect_ids(manifest["defects"])

    check_scores = []
    for check in checks:
        of_type = ids_by_type.get(check.record_type, set())
        planted = {
            record_id
            for defect_id in check.defects
            if defect_id in in_scope
            for record_id in entries[defect_id]["record_ids"]
            if record_id in of_type
        }
        check_scores.append(
            CheckScore(check.id, check.defects, planted, set(findings.get(check.id, set())))
        )

    caught = 0
    for defect_id in in_scope:
        found_by_targeting = set().union(*(s.found for s in check_scores if defect_id in s.defects))
        caught += len(set(entries[defect_id]["record_ids"]) & found_by_targeting)

    covered = {d for check in checks for d in check.defects}
    return Score(
        checks=check_scores,
        planted_total=sum(len(entries[d]["record_ids"]) for d in in_scope),
        caught_total=caught,
        false_positive_total=sum(len(s.false_positives) for s in check_scores),
        uncovered=[d for d in in_scope if d not in covered],
        out_of_scope=[e["id"] for e in manifest["defects"] if e["id"] not in in_scope],
    )


def summary_line(result: Score) -> str:
    return (
        f"caught {result.caught_total} of {result.planted_total} planted records, "
        f"{result.false_positive_total} false positives"
    )
