"""Compare two snapshots record by record (used to check the HubSpot read-back, milestone 5).

`expected` is usually the seed's answer key (data/output/hubspot) and `actual` a live snapshot
saved with `run --source hubspot --save-snapshot`. owner_id is compared only as blank or set,
because the answer key keeps the generator's OWN-xx IDs and the portal maps them all to one
HubSpot owner. The owners table is not compared, for the same reason.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from monitor.snapshot import COLUMNS, PRIMARY_KEYS, Snapshot, SnapshotError, read_csv_snapshot

COMPARED_TABLES = ("companies", "contacts", "deals")
MAX_EXAMPLES = 5


def _value(column: str, value: str) -> object:
    return bool(value) if column == "owner_id" else value


def _examples(ids: list[str]) -> str:
    more = f" and {len(ids) - MAX_EXAMPLES} more" if len(ids) > MAX_EXAMPLES else ""
    return ", ".join(ids[:MAX_EXAMPLES]) + more


def compare_snapshots(expected: Snapshot, actual: Snapshot) -> list[str]:
    """One line per difference: missing or extra records per table, then per column."""
    problems: list[str] = []
    for table in COMPARED_TABLES:
        key = PRIMARY_KEYS[table]
        want = {r[key]: r for r in expected.tables[table]}
        got = {r[key]: r for r in actual.tables[table]}
        missing = sorted(set(want) - set(got))
        extra = sorted(set(got) - set(want))
        if missing:
            problems.append(f"{table}: {len(missing)} missing: {_examples(missing)}")
        if extra:
            problems.append(f"{table}: {len(extra)} extra: {_examples(extra)}")
        both = sorted(set(want) & set(got))
        for column in COLUMNS[table]:
            differ = [
                rid
                for rid in both
                if _value(column, want[rid][column]) != _value(column, got[rid][column])
            ]
            if differ:
                first = differ[0]
                problems.append(
                    f"{table}.{column}: {len(differ)} records differ: {_examples(differ)} "
                    f"(e.g. {first}: expected {want[first][column]!r}, "
                    f"got {got[first][column]!r})"
                )
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="compare_snapshots", description="Compare two snapshot folders record by record."
    )
    parser.add_argument("expected", type=Path, help="answer key folder, e.g. data/output/hubspot")
    parser.add_argument("actual", type=Path, help="snapshot folder, e.g. data/output/hubspot-live")
    args = parser.parse_args(argv)
    try:
        expected, actual = read_csv_snapshot(args.expected), read_csv_snapshot(args.actual)
    except SnapshotError as exc:
        print(f"cannot read snapshot: {exc}", file=sys.stderr)
        return 2
    for table in COMPARED_TABLES:
        print(f"{table}: {len(expected.tables[table])} expected, {len(actual.tables[table])} read")
    print("owners: not compared (every OWN-xx maps to one HubSpot owner)")
    problems = compare_snapshots(expected, actual)
    print("\n".join(problems) if problems else "no differences")
    return 1 if problems else 0
