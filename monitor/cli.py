"""Command line: `python -m monitor run ...` and `python -m monitor demo ...`."""

from __future__ import annotations

import argparse
import os
import sys
import webbrowser
from pathlib import Path
from types import ModuleType

from monitor import CHECKS_DIR, ROOT
from monitor.checks import CheckError, load_checks
from monitor.params import load_params
from monitor.pipeline import run_once, summary_lines, write_reports
from monitor.snapshot import SnapshotError, connect

DATA_DIR = ROOT / "data"
DEFAULT_DB = Path("data/output/monitor.duckdb")


def data_scripts() -> tuple[ModuleType, ModuleType]:
    """Import the stdlib-only generator and day-2 simulator from data/."""
    if str(DATA_DIR) not in sys.path:
        sys.path.insert(0, str(DATA_DIR))
    import generate
    import simulate_day2

    return generate, simulate_day2


def remove_database(db: Path) -> None:
    for path in (db, db.with_name(db.name + ".wal")):
        if path.exists():
            path.unlink()


def cmd_run(args: argparse.Namespace) -> int:
    try:
        checks = load_checks(args.checks)
    except CheckError as exc:
        print(f"invalid check definition: {exc}", file=sys.stderr)
        return 2
    if args.fresh:
        remove_database(args.db)
    label = args.label or args.path.name
    try:
        with connect(args.db) as con:
            outcome = run_once(con, args.path, label, checks, load_params())
    except SnapshotError as exc:
        print(f"cannot read snapshot: {exc}", file=sys.stderr)
        return 2
    paths = write_reports(outcome.report, args.reports, label)
    print("\n".join(summary_lines(outcome)))
    print("Reports: " + ", ".join(str(p) for p in paths if p.suffix == ".html"))
    return 1 if outcome.errors else 0


def cmd_demo(args: argparse.Namespace) -> int:
    generate, simulate_day2 = data_scripts()
    out: Path = args.out
    day1, day2 = out / "day1", out / "day2"
    tables, manifest = generate.build(seed=args.seed, scale=1.0, defects=True)
    generate.write_output(day1, tables, manifest, args.seed, 1.0)
    simulate_day2.simulate(day1, day2, seed=args.seed)

    db = out / "monitor.duckdb"
    remove_database(db)
    checks, params = load_checks(), load_params()
    failed = False
    with connect(db) as con:
        for label, folder in (("day1", day1), ("day2", day2)):
            outcome = run_once(con, folder, label, checks, params)
            write_reports(outcome.report, args.reports, label)
            print("\n".join(summary_lines(outcome)))
            failed = failed or bool(outcome.errors)

    latest = Path(args.reports) / "latest.html"
    print(f"Reports: {Path(args.reports) / 'day1.html'}, {Path(args.reports) / 'day2.html'}")
    print(f"Latest (day 2): {latest}")
    if not args.no_open and not os.environ.get("CI"):
        webbrowser.open(latest.resolve().as_uri())
    return 1 if failed else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m monitor",
        description="Snapshot CRM data, run data-quality checks and report what changed.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="load one snapshot as a new run and write its report")
    run.add_argument("--source", choices=["csv"], default="csv", help="snapshot source")
    run.add_argument("--path", type=Path, required=True, help="folder with the snapshot CSVs")
    run.add_argument("--db", type=Path, default=DEFAULT_DB, help=f"database (default {DEFAULT_DB})")
    run.add_argument("--label", help="run label (default: the folder name)")
    run.add_argument("--reports", type=Path, default=Path("reports"), help="report folder")
    run.add_argument("--checks", type=Path, default=CHECKS_DIR, help="folder with check YAML")
    run.add_argument("--fresh", action="store_true", help="delete the database first")
    run.set_defaults(func=cmd_run)

    demo = sub.add_parser("demo", help="offline demo: generate day 1, simulate day 2, run both")
    demo.add_argument("--out", type=Path, default=Path("data/output"), help="data folder")
    demo.add_argument("--reports", type=Path, default=Path("reports"), help="report folder")
    demo.add_argument("--seed", type=int, default=42, help="random seed (default 42)")
    demo.add_argument("--no-open", action="store_true", help="don't open the report in a browser")
    demo.set_defaults(func=cmd_demo)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)
