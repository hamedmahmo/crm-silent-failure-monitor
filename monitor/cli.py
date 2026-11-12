"""Command line: `python -m monitor run | demo | hubspot ...`."""

from __future__ import annotations

import argparse
import json
import os
import sys
import webbrowser
from collections.abc import Callable
from datetime import UTC, date, datetime
from pathlib import Path
from types import ModuleType

from monitor import CHECKS_DIR, ROOT
from monitor.checks import Check, CheckError, load_checks
from monitor.datagen import generator, simulator
from monitor.hubspot.client import (
    API_VERSION,
    OWNER_ENV,
    PORTAL_ENV,
    TOKEN_ENV,
    ConfigError,
    HubSpotClient,
    HubSpotError,
    load_env,
    required_env,
)
from monitor.hubspot.guard import check_portal
from monitor.hubspot.probe import render_verification, run_probes
from monitor.hubspot.properties import PROPERTIES
from monitor.hubspot.reset import reset
from monitor.hubspot.seed import (
    apply_seed,
    build_seed,
    industry_map,
    resolve_owner,
    verify_seed,
    write_seed_output,
)
from monitor.hubspot.snapshot import read_hubspot_snapshot
from monitor.params import load_params
from monitor.pipeline import (
    RunOutcome,
    read_manifest,
    run_once,
    run_snapshot,
    summary_lines,
    write_reports,
)
from monitor.snapshot import SnapshotError, connect, write_csv_snapshot

DEFAULT_DB = Path("data/output/monitor.duckdb")
HUBSPOT_OUT = Path("data/output/hubspot")
HUBSPOT_LIVE_DB = Path("data/output/hubspot-live.duckdb")
HUBSPOT_REPORTS = Path("reports/hubspot")
HUBSPOT_ONLY = ("as_of", "manifest", "save_snapshot")
VERIFICATION_DOC = ROOT / "docs" / "hubspot-verification.md"


def data_scripts() -> tuple[ModuleType, ModuleType]:
    """Import the stdlib-only generator and day-2 simulator from data/."""
    return generator(), simulator()


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
    if args.source == "hubspot":
        return hubspot_command(lambda c, p, a: _run_hubspot(c, p, a, checks))(args)
    if args.path is None:
        print("run --source csv needs --path FOLDER", file=sys.stderr)
        return 2
    used = [f"--{name.replace('_', '-')}" for name in HUBSPOT_ONLY if getattr(args, name)]
    if used:
        print(f"{', '.join(used)} only apply to --source hubspot", file=sys.stderr)
        return 2
    db = args.db or DEFAULT_DB
    if args.fresh:
        remove_database(db)
    label = args.label or args.path.name
    try:
        with connect(db) as con:
            outcome = run_once(con, args.path, label, checks, load_params())
    except SnapshotError as exc:
        print(f"cannot read snapshot: {exc}", file=sys.stderr)
        return 2
    return _finish_run(outcome, args.reports or Path("reports"), label)


def _finish_run(
    outcome: RunOutcome, reports: Path, label: str, unscored: str | None = None, notes=()
) -> int:
    paths = write_reports(outcome.report, reports, label)
    lines = summary_lines(outcome, unscored) if unscored else summary_lines(outcome)
    print("\n".join([*lines, *notes]))
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


# ---------------------------------------------------------------- hubspot


def make_hubspot_client(token: str) -> HubSpotClient:
    return HubSpotClient(token)


def today_utc() -> date:
    return datetime.now(UTC).date()


HubSpotBody = Callable[[HubSpotClient, str, argparse.Namespace], int]


def hubspot_command(body: HubSpotBody) -> Callable[[argparse.Namespace], int]:
    """Load .env, require the token and portal ID (exit 2 if missing), report errors (exit 1)."""

    def run(args: argparse.Namespace) -> int:
        load_env()
        try:
            token = required_env(TOKEN_ENV)
            portal_id = required_env(PORTAL_ENV)
        except ConfigError as exc:
            print(exc, file=sys.stderr)
            return 2
        with make_hubspot_client(token) as client:
            try:
                return body(client, portal_id, args)
            except HubSpotError as exc:
                print(f"error: {exc}", file=sys.stderr)
                return 1

    return run


def _hubspot_check(client: HubSpotClient, portal_id: str, args: argparse.Namespace) -> int:
    check_portal(client, portal_id)
    owners = client.get(f"/crm/owners/{API_VERSION}", params={"archived": "false"}) or {}
    print(f"Portal OK: developer test account, and its ID matches {PORTAL_ENV}.")
    print(f"Active owners: {len(owners.get('results') or [])}")
    for object_type, defs in PROPERTIES.items():
        props = client.get(f"/crm/properties/{API_VERSION}/{object_type}") or {}
        existing = {p["name"] for p in props.get("results") or []}
        present = sum(1 for d in defs if d.name in existing)
        print(f"Northwind properties on {object_type}: {present} of {len(defs)} present")
    return 0


def _hubspot_probe(client: HubSpotClient, portal_id: str, args: argparse.Namespace) -> int:
    today = today_utc()
    results = run_probes(client, portal_id, today)
    args.doc.parent.mkdir(parents=True, exist_ok=True)
    args.doc.write_text(render_verification(results, today), encoding="utf-8")
    p5 = next((r for r in results if r.probe_id == "P5"), None)
    if p5 is not None and p5.observed.get("options"):
        args.options_out.parent.mkdir(parents=True, exist_ok=True)
        args.options_out.write_text(
            json.dumps(p5.observed["options"], indent=2) + "\n", encoding="utf-8"
        )
    for r in results:
        print(f"{r.probe_id} {'as planned' if r.as_planned else 'NOT as planned'}: {r.detail}")
    print(f"Results: {args.doc}")
    return 0 if all(r.as_planned for r in results) else 1


def _hubspot_seed(client: HubSpotClient, portal_id: str, args: argparse.Namespace) -> int:
    check_portal(client, portal_id)
    owners = client.get(f"/crm/owners/{API_VERSION}", params={"archived": "false"}) or {}
    owner_id = resolve_owner(owners.get("results") or [], os.environ.get(OWNER_ENV) or None)
    industry = client.get(f"/crm/properties/{API_VERSION}/companies/industry") or {}
    industries = industry_map(industry.get("options") or [])

    seed_day = args.day or today_utc()
    tables, manifest = generator().build(seed=args.seed, defects=True)
    plan = build_seed(tables, manifest, seed_day, owner_id, industries)
    result = apply_seed(client, plan, portal_id)
    problems = verify_seed(client, plan)
    write_seed_output(args.out, plan, result)

    counts = {t: len(plan.inputs[t]) for t in plan.inputs}
    planted = sum(len(e["record_ids"]) for e in plan.manifest["defects"])
    print(
        f"Seeded {counts['companies']} companies, {counts['contacts']} contacts, "
        f"{counts['deals']} deals as of {seed_day} (open deal dates moved {plan.shift_days} days)"
    )
    print(f"  created {sum(result.created.values())}, updated {sum(result.updated.values())}")
    print(f"  {result.associations} associations")
    print(f"  {planted} planted records in {len(plan.manifest['defects'])} defects")
    print(f"Answer key: {args.out}")
    for problem in problems:
        print(f"post-seed check: {problem}", file=sys.stderr)
    return 1 if problems else 0


def _hubspot_reset(client: HubSpotClient, portal_id: str, args: argparse.Namespace) -> int:
    counts = reset(client, portal_id, yes=args.yes)
    summary = (
        f"{counts['companies']} companies, {counts['contacts']} contacts, {counts['deals']} deals"
    )
    if args.yes:
        print(f"Archived {summary} tagged with northwind_id.")
    else:
        print(f"dry run: {summary} tagged with northwind_id would be archived.")
        print("Run `uv run python -m monitor hubspot reset --yes` to archive them.")
    return 0


def _run_hubspot(
    client: HubSpotClient, portal_id: str, args: argparse.Namespace, checks: list[Check]
) -> int:
    """`run --source hubspot`: read the test account (read-only) and run the checks on it."""
    as_of = args.as_of or today_utc()
    label = args.label or f"hubspot-{as_of}"
    manifest, unscored, notes = None, "no --manifest given", []
    if args.manifest:
        manifest = read_manifest(args.manifest)
        if manifest is None:
            unscored = f"no answer key at {args.manifest}"
        elif manifest.get("as_of") != as_of.isoformat():
            notes.append(
                f"  note: the answer key is for {manifest.get('as_of')}; records that aged "
                "past their dates since then show up as false positives"
            )
    params = load_params()
    try:
        snapshot = read_hubspot_snapshot(client, portal_id, as_of, params)
    except SnapshotError as exc:
        print(f"cannot read snapshot: {exc}", file=sys.stderr)
        return 2
    if args.save_snapshot:
        write_csv_snapshot(snapshot, args.save_snapshot)
        notes.append(f"Snapshot saved: {args.save_snapshot}")
    db = args.db or HUBSPOT_LIVE_DB
    if args.fresh:
        remove_database(db)
    with connect(db) as con:
        outcome = run_snapshot(con, snapshot, label, checks, params, manifest)
    return _finish_run(outcome, args.reports or HUBSPOT_REPORTS, label, unscored, notes)


cmd_hubspot_check = hubspot_command(_hubspot_check)
cmd_hubspot_probe = hubspot_command(_hubspot_probe)
cmd_hubspot_seed = hubspot_command(_hubspot_seed)
cmd_hubspot_reset = hubspot_command(_hubspot_reset)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m monitor",
        description="Snapshot CRM data, run data-quality checks and report what changed.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="load one snapshot as a new run and write its report")
    run.add_argument(
        "--source",
        choices=["csv", "hubspot"],
        default="csv",
        help="csv: a generator output folder; hubspot: read the test account (read-only)",
    )
    run.add_argument("--path", type=Path, help="folder with the snapshot CSVs (csv only)")
    run.add_argument(
        "--db",
        type=Path,
        help=f"database (default {DEFAULT_DB}; hubspot: {HUBSPOT_LIVE_DB})",
    )
    run.add_argument(
        "--label", help="run label (default: the folder name; hubspot: hubspot-<as-of>)"
    )
    run.add_argument(
        "--reports", type=Path, help=f"report folder (default reports; hubspot: {HUBSPOT_REPORTS})"
    )
    run.add_argument(
        "--as-of",
        type=date.fromisoformat,
        help="hubspot: the as-of date for the checks (default: today, UTC)",
    )
    run.add_argument(
        "--manifest", type=Path, help="hubspot: answer key to score against (defects_manifest.json)"
    )
    run.add_argument(
        "--save-snapshot", type=Path, help="hubspot: also save what was read as CSV in this folder"
    )
    run.add_argument("--checks", type=Path, default=CHECKS_DIR, help="folder with check YAML")
    run.add_argument("--fresh", action="store_true", help="delete the database first")
    run.set_defaults(func=cmd_run)

    demo = sub.add_parser("demo", help="offline demo: generate day 1, simulate day 2, run both")
    demo.add_argument("--out", type=Path, default=Path("data/output"), help="data folder")
    demo.add_argument("--reports", type=Path, default=Path("reports"), help="report folder")
    demo.add_argument("--seed", type=int, default=42, help="random seed (default 42)")
    demo.add_argument("--no-open", action="store_true", help="don't open the report in a browser")
    demo.set_defaults(func=cmd_demo)

    hubspot = sub.add_parser(
        "hubspot", help="HubSpot developer test account: check, probe, seed, reset"
    )
    hub = hubspot.add_subparsers(dest="hubspot_command", required=True)
    check = hub.add_parser(
        "check", help="read-only: confirm the token's portal is the test account"
    )
    check.set_defaults(func=cmd_hubspot_check)
    probe = hub.add_parser("probe", help="check HubSpot behaviour the seed depends on (P1-P5)")
    probe.add_argument("--doc", type=Path, default=VERIFICATION_DOC, help="results file")
    probe.add_argument(
        "--options-out",
        type=Path,
        default=HUBSPOT_OUT / "industry_options.json",
        help="where to save the industry option list (P5)",
    )
    probe.set_defaults(func=cmd_hubspot_probe)
    seed = hub.add_parser("seed", help="load Northwind day 1 with the plantable defects")
    seed.add_argument("--seed", type=int, default=42, help="random seed (default 42)")
    seed.add_argument("--day", type=date.fromisoformat, help="seed day (default: today, UTC)")
    seed.add_argument("--out", type=Path, default=HUBSPOT_OUT, help="answer key folder")
    seed.set_defaults(func=cmd_hubspot_seed)
    reset = hub.add_parser("reset", help="archive records tagged northwind_id (dry run by default)")
    reset.add_argument("--yes", action="store_true", help="archive them; without it, only count")
    reset.set_defaults(func=cmd_hubspot_reset)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)
