"""Command line entry point. The graded, scriptable artifact.

    l2c run <project_dir | plan.pdf> [--out out/]
    l2c validate <elements.json>
    l2c truth <project_dir>          check against the project's *_dismatch.xlsx
"""

from __future__ import annotations

import argparse
import os
import sys

from . import io_json
from .pipeline import ProjectResult, find_plan, run_plan


def _resolve(target: str) -> str:
    if os.path.isdir(target):
        plan = find_plan(target)
        if not plan:
            sys.exit(f"no L2C_PLAN_STR_*.pdf found in {target}")
        return plan
    if not os.path.isfile(target):
        sys.exit(f"not found: {target}")
    return target


def _print_summary(result: ProjectResult) -> None:
    t = result.totals
    print(f"\nproject        {result.project}")
    print(f"plan file      {result.plan_file}")
    print(f"unit system    {result.unit_system}   {result.unit_evidence}")
    print(f"sheets         {t['sheets']}  extracted={t['sheets_extracted']}  "
          f"skipped={t['sheets_skipped']}")
    print(f"elements       {t['elements']}  located={t['located']}  "
          f"mean confidence={t['mean_confidence']}")
    print(f"elapsed        {result.elapsed_s}s\n")
    print(f"{'FEUILLET':<9}{'PG':>4}  {'TYPE':<10}{'NIVEAU':<18}{'ELEM':>5}{'LOC':>5}  STATUS")
    for s in result.sheets:
        if s.status == "skipped":
            continue
        print(f"{s.feuillet:<9}{s.page:>4}  {str(s.type_element):<10}"
              f"{str(s.niveau):<18}{s.records:>5}{s.located:>5}  {s.status}")
    warn = [(s.feuillet, w) for s in result.sheets for w in s.diagnostics.get("warnings", [])]
    if warn:
        print(f"\n{len(warn)} warning(s):")
        for feuillet, w in warn[:12]:
            print(f"  {feuillet}: {w}")


def cmd_run(args: argparse.Namespace) -> int:
    plan = _resolve(args.target)
    total = {"n": 0}

    def progress(i: int, n: int, sheet: str) -> None:
        if not args.quiet:
            print(f"\r  page {i}/{n}  {sheet:<10}", end="", flush=True)
        total["n"] = n

    result = run_plan(plan, progress=progress)
    if not args.quiet:
        print("\r" + " " * 40, end="\r")

    outdir = os.path.join(args.out, result.project)
    n = io_json.write_records(os.path.join(outdir, "elements_plan.json"), result.records)
    io_json.write_run_manifest(os.path.join(outdir, "run_manifest.json"), result)
    _print_summary(result)
    print(f"wrote {n} records -> {outdir}/elements_plan.json")
    print(f"wrote manifest   -> {outdir}/run_manifest.json")
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    import json as _json

    if not os.path.isfile(args.path):
        print(f"not found: {args.path}\n  run `l2c run <project>` first to produce it")
        return 2
    try:
        count, errors = io_json.validate_file(args.path)
    except _json.JSONDecodeError as e:
        print(f"INVALID: {args.path} is not valid JSON ({e})")
        return 1
    if errors:
        print(f"INVALID: {len(errors)} problem(s) in {count} record(s)")
        for e in errors[:20]:
            print("  " + e)
        return 1
    print(f"VALID: {count} records conform to Appendix A")
    return 0


def cmd_truth(args: argparse.Namespace) -> int:
    from . import answer_key

    if not os.path.isdir(args.project):
        print(f"not a directory: {args.project}")
        return 2
    keys = [f for f in sorted(os.listdir(args.project)) if f.lower().endswith("_dismatch.xlsx")]
    if not keys:
        print(f"no *_dismatch.xlsx answer key in {args.project} (only CLP has one)")
        return 2
    result = run_plan(_resolve(args.project))
    rows = answer_key.load(os.path.join(args.project, keys[0]))
    verdicts = answer_key.check(result.records, rows, result.unit_system)
    print(f"{'FEUILLET':<9}{'LOCALISATION':<26}{'PLAN L2C':<20}{'RECORDS':>8}  VERDICT")
    for v in verdicts:
        print(f"{v.row.feuillet:<9}{v.row.localisation:<26}{v.row.plan:<20}"
              f"{v.candidates:>8}  {'PASS' if v.found else 'FAIL'}")
    ok = sum(v.found for v in verdicts)
    print(f"\n{ok}/{len(verdicts)} answer-key rows derived from the plan")

    from .da.pipeline import run_da

    da = run_da(args.project)
    dv = answer_key.check_atelier(da.records, rows, da.unit_system)
    print(f"\n{'FEUILLET':<9}{'LOCALISATION':<26}{'ATELIER':<20}{'RECORDS':>8}  VERDICT  (DA side)")
    for v in dv:
        print(f"{v.row.feuillet:<9}{v.row.localisation:<26}{v.row.atelier:<20}"
              f"{v.candidates:>8}  {'PASS' if v.found else 'miss'}"
              + (f"   {v.matched.fichier}" if v.matched else ""))
    print(f"\n{sum(v.found for v in dv)}/{len(dv)} answer-key rows found in the shop drawings "
          "(DA reading is in progress - DA_PLAN.md)")
    return 0 if ok == len(verdicts) else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="l2c", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="extract a project and write JSON")
    r.add_argument("target", help="project directory or plan PDF")
    r.add_argument("--out", default="out", help="output directory (default: out)")
    r.add_argument("--quiet", action="store_true")
    r.set_defaults(func=cmd_run)

    v = sub.add_parser("validate", help="check a JSON file against Appendix A")
    v.add_argument("path")
    v.set_defaults(func=cmd_validate)

    t = sub.add_parser("truth", help="check the plan side against the answer key")
    t.add_argument("project", help="project directory containing *_dismatch.xlsx")
    t.set_defaults(func=cmd_truth)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
