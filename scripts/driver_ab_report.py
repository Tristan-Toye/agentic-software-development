#!/usr/bin/env python3
"""Compare dossiers built with a mechanical-phase driver against a baseline arm,
and say per driver: keep, or back to shadow.

Per dossier it reads what the repository already records:

    defects, gaps, contract_changes   the `## Build log` markers budget_report.py
                                      counts (DEFECT lines, GAP:, CONTRACT-CHANGE:)
    red_ci                            `SUITE: ci run <url> on <sha> — N failed / ...`
                                      lines with N > 0 (wait_ci.py --log wrote them);
                                      not measured when the log has no `SUITE: ci` line
    owner_questions                   `<repo>.<ID>.Q<n>.jsonl` files in the questions
                                      store ($ASD_QUESTIONS_DIR or ~/.agentic/questions);
                                      not measured when that directory does not exist
    requests, cost_usd                from a `budget_report.py --json` file
                                      (`--budget FILE`); not measured without one

A metric whose source is missing prints `not measured`, never 0, and is left out
of every comparison.

The verdict for a driver (its dossiers in build order):
    A dossier is WORSE when any measured quality counter (defects, gaps,
    contract_changes, red_ci, owner_questions) is above the baseline median for
    that counter. Two worse dossiers in a row: BACK-TO-SHADOW. Otherwise KEEP.
    With no measured dossier the verdict is `not measured`.
Requests and cost are shown against the baseline median as information: the plan's
success bar (cache-read down 20% in the driver's phases) needs the per-phase
measurement of ledger/optimisations/scripts/measure_orchestrator_phases.py,
which this script does not repeat.

Usage:
    driver_ab_report.py --baseline W-101,W-102,... \\
        --driver merge_packages=W-110,W-111 --driver finalize_pr=W-112 \\
        [--dossiers .discovery/dossiers] [--questions-dir DIR] [--budget budget.json] \\
        [--discovery .discovery] [--json out.json]
    driver_ab_report.py --selftest

Exit 0 report printed, 2 unusable input.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import re
import statistics
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

QUALITY = ("defects", "gaps", "contract_changes", "red_ci", "owner_questions")
COST = ("requests", "cost_usd")
CI_RE = re.compile(r"SUITE: ci run .*?—\s*(\d+)\s+failed")
CI_ANY = re.compile(r"SUITE: ci run")


def dossier_file(dossiers: str, did: str) -> str | None:
    hits = sorted(glob.glob(os.path.join(dossiers, f"{did}.md")) + glob.glob(os.path.join(dossiers, f"{did}-*.md")))
    return hits[0] if hits else None


def build_log_metrics(path: str) -> dict:
    import budget_report

    q = budget_report.quality(path)
    if not q.get("build_log"):
        return {"defects": None, "gaps": None, "contract_changes": None, "red_ci": None}
    text = open(path, encoding="utf-8", errors="replace").read()
    log = text[text.find("## Build log"):]
    ci_lines = CI_ANY.findall(log)
    red = sum(1 for m in CI_RE.finditer(log) if int(m.group(1)) > 0)
    return {"defects": q["defects"], "gaps": q["gaps"], "contract_changes": q["contract_changes"],
            "red_ci": red if ci_lines else None}


def owner_questions(qdir: str | None, did: str) -> int | None:
    if not qdir or not os.path.isdir(qdir):
        return None
    pat = re.compile(r"\." + re.escape(did) + r"\.Q\d+\.jsonl$")
    return sum(1 for n in os.listdir(qdir) if pat.search(n))


def budget_by_dossier(path: str | None) -> dict:
    if not path:
        return {}
    data = json.load(open(path, encoding="utf-8"))
    out: dict[str, dict] = {}
    for r in data.get("runs", []):
        if r.get("host") != "claude":
            continue
        cur = out.setdefault(r["run"], {"requests": 0, "cost_usd": 0.0})
        cur["requests"] += r.get("requests") or 0
        cur["cost_usd"] += r.get("cost") or 0.0
    return out


def collect(did: str, args, budget: dict) -> dict:
    m: dict = {"id": did}
    path = dossier_file(args.dossiers, did)
    m.update(build_log_metrics(path) if path else {"defects": None, "gaps": None, "contract_changes": None, "red_ci": None})
    m["dossier_file"] = path
    m["owner_questions"] = owner_questions(args.questions_dir, did) if path else None  # no dossier record: unknown, not 0
    b = budget.get(did)
    m["requests"] = b["requests"] if b else None
    m["cost_usd"] = round(b["cost_usd"], 2) if b else None
    drv = {}
    for name in ("shadow", "evidence"):
        sub = "driver-shadow" if name == "shadow" else "driver-evidence"
        drv[name] = len(glob.glob(os.path.join(args.discovery, sub, did, "*.json"))) if args.discovery and os.path.isdir(args.discovery) else None
    m["driver_files"] = drv
    return m


def median_of(rows: list[dict], key: str) -> float | None:
    vals = [r[key] for r in rows if r.get(key) is not None]
    return statistics.median(vals) if vals else None


def is_worse(row: dict, base: dict) -> tuple[bool | None, list[str]]:
    """(worse?, the counters above baseline). None when nothing of this dossier was comparable."""
    comparable, above = 0, []
    for k in QUALITY:
        if row.get(k) is None or base.get(k) is None:
            continue
        comparable += 1
        if row[k] > base[k]:
            above.append(f"{k} {row[k]} > {base[k]:g}")
    return (None if comparable == 0 else bool(above)), above


def verdict(rows: list[dict], base: dict) -> dict:
    flags, detail = [], []
    streak = 0
    back = False
    for r in rows:
        w, above = is_worse(r, base)
        flags.append(w)
        detail.append({"id": r["id"], "worse": w, "above": above})
        streak = streak + 1 if w else 0 if w is False else streak
        if streak >= 2:
            back = True
    measured = [f for f in flags if f is not None]
    if not measured:
        v = "not measured"
    elif back:
        v = "BACK-TO-SHADOW"
    else:
        v = "KEEP"
    out = {"verdict": v, "dossiers": detail, "measured": len(measured), "of": len(rows)}
    for k in COST:
        b, a = base.get(k), median_of(rows, k)
        out[k] = {"baseline_median": b, "driver_median": a,
                  "change_pct": round(100 * (a - b) / b, 1) if (a is not None and b) else None}
    return out


def fmt(v) -> str:
    return "not measured" if v is None else f"{v:g}" if isinstance(v, float) else str(v)


def report(base_rows: list[dict], drivers: dict[str, list[dict]]) -> dict:
    base = {k: median_of(base_rows, k) for k in QUALITY + COST}
    out = {"baseline": {"dossiers": [r["id"] for r in base_rows], "medians": base}, "drivers": {}, "rows": {}}
    for r in base_rows:
        out["rows"][r["id"]] = r
    for name, rows in drivers.items():
        out["drivers"][name] = verdict(rows, base)
        for r in rows:
            out["rows"][r["id"]] = r
    return out


def print_report(rep: dict) -> None:
    cols = QUALITY + COST
    print("metric            " + "  ".join(f"{c:>15}" for c in cols))
    print("baseline median   " + "  ".join(f"{fmt(rep['baseline']['medians'][c]):>15}" for c in cols))
    for name, v in rep["drivers"].items():
        print(f"\n{name}: {v['verdict']}  ({v['measured']} of {v['of']} dossier(s) comparable)")
        for dd in v["dossiers"]:
            r = rep["rows"][dd["id"]]
            state = {None: "not comparable", True: "WORSE", False: "ok"}[dd["worse"]]
            print(f"  {dd['id']:<8}" + "  ".join(f"{c}={fmt(r.get(c))}" for c in cols) + f"  -> {state}" +
                  (f" ({'; '.join(dd['above'])})" if dd["above"] else ""))
        for k in COST:
            c = v[k]
            print(f"  {k}: baseline {fmt(c['baseline_median'])}, driver {fmt(c['driver_median'])}" +
                  (f" ({c['change_pct']:+g}%)" if c["change_pct"] is not None else ""))


def parse_ids(text: str) -> list[str]:
    return [t.strip() for t in text.split(",") if t.strip()]


def run(args) -> int:
    if not args.baseline or not args.driver:
        print("need --baseline IDS and at least one --driver NAME=IDS", file=sys.stderr)
        return 2
    drivers_ids: dict[str, list[str]] = {}
    for spec in args.driver:
        name, _, ids = spec.partition("=")
        if not ids:
            print(f"--driver wants NAME=IDS, got {spec!r}", file=sys.stderr)
            return 2
        drivers_ids[name] = parse_ids(ids)
    try:
        budget = budget_by_dossier(args.budget)
    except (OSError, ValueError) as err:
        print(f"cannot read --budget: {err}", file=sys.stderr)
        return 2
    base_rows = [collect(i, args, budget) for i in parse_ids(args.baseline)]
    drivers = {n: [collect(i, args, budget) for i in ids] for n, ids in drivers_ids.items()}
    rep = report(base_rows, drivers)
    print_report(rep)
    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(rep, fh, indent=1, sort_keys=True)
        print(f"\nwritten: {args.json}")
    return 0


def selftest() -> int:
    from driver_common import Checker

    c = Checker("driver_ab_report")
    with tempfile.TemporaryDirectory() as tmp:
        dd = os.path.join(tmp, "dossiers")
        os.makedirs(dd)

        def mk(did, defects=0, gaps=0, red=None):
            lines = ["## Build log", "RETURNED P1 ok"] + ["DEFECT: x"] * defects + ["GAP: y"] * gaps
            if red is not None:
                lines.append(f"SUITE: ci run https://x/1 on abc — {red} failed / 10 passed")
            open(os.path.join(dd, f"{did}.md"), "w").write("---\nid: %s\n---\n\n" % did + "\n".join(lines) + "\n")

        for i in (1, 2, 3):
            mk(f"W-10{i}", defects=1, red=0)
        mk("W-110", defects=1, red=0)      # equal to baseline: ok
        mk("W-111", defects=3, red=0)      # worse
        mk("W-112", defects=1, red=2)      # worse (red CI)
        mk("W-120", defects=5)             # worse once
        mk("W-121", defects=0)             # ok again
        mk("W-122", defects=9)             # worse, but not in a row
        qd = os.path.join(tmp, "q")
        os.makedirs(qd)
        for n in ("repo.W-111.Q1.jsonl", "repo.W-111.Q2.jsonl"):
            open(os.path.join(qd, n), "w").write("{}\n")
        bj = os.path.join(tmp, "budget.json")
        json.dump({"runs": [{"host": "claude", "run": "W-101", "requests": 100, "cost": 10.0},
                            {"host": "claude", "run": "W-102", "requests": 120, "cost": 12.0},
                            {"host": "claude", "run": "W-110", "requests": 70, "cost": 7.0}]}, open(bj, "w"))
        ns = argparse.Namespace(dossiers=dd, questions_dir=qd, budget=bj, discovery=None)
        budget = budget_by_dossier(bj)
        base_rows = [collect(i, ns, budget) for i in ("W-101", "W-102", "W-103")]
        c("defects counted from the build log", base_rows[0]["defects"] == 1)
        c("red_ci 0 when CI ran green", base_rows[0]["red_ci"] == 0)
        c("a log with no CI line is not measured, not zero", collect("W-120", ns, budget)["red_ci"] is None)
        c("owner_questions counted from the store", collect("W-111", ns, budget)["owner_questions"] == 2)
        c("a missing questions dir is not measured", owner_questions(os.path.join(tmp, "nope"), "W-111") is None)
        c("requests not measured without budget data", collect("W-111", ns, budget)["requests"] is None)
        rep = report(base_rows, {
            "merge_packages": [collect(i, ns, budget) for i in ("W-110", "W-111", "W-112")],
            "finalize_pr": [collect(i, ns, budget) for i in ("W-120", "W-121", "W-122")],
            "test_round": [collect("W-999", ns, budget)],
        })
        c("two worse in a row -> back to shadow", rep["drivers"]["merge_packages"]["verdict"] == "BACK-TO-SHADOW")
        c("worse dossiers not in a row -> keep", rep["drivers"]["finalize_pr"]["verdict"] == "KEEP")
        c("a dossier with no record is not measured", rep["drivers"]["test_round"]["verdict"] == "not measured")
        c("requests change shown against the baseline median", rep["drivers"]["merge_packages"]["requests"]["driver_median"] == 70
          and rep["drivers"]["merge_packages"]["requests"]["change_pct"] == round(100 * (70 - 110) / 110, 1))
        c("cost not measured when no driver dossier has it", rep["drivers"]["finalize_pr"]["requests"]["driver_median"] is None)
        c("fmt prints not measured for None", fmt(None) == "not measured" and fmt(0) == "0")
        out = os.path.join(tmp, "o.json")
        a = argparse.Namespace(baseline="W-101,W-102,W-103", driver=["merge_packages=W-110,W-111,W-112"], dossiers=dd,
                               questions_dir=qd, budget=bj, discovery=None, json=out)
        c("run prints and writes json", run(a) == 0 and os.path.isfile(out))
        a.driver = ["bad"]
        c("a malformed --driver is rejected", run(a) == 2)
    return c.report()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="driver A/B: per-driver keep / back-to-shadow verdicts")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--baseline", help="comma list of baseline dossier ids")
    ap.add_argument("--driver", action="append", default=[], metavar="NAME=IDS", help="a driver and its dossiers in build order (repeatable)")
    ap.add_argument("--dossiers", default=os.path.join(".discovery", "dossiers"))
    ap.add_argument("--questions-dir", default=os.environ.get("ASD_QUESTIONS_DIR") or os.path.expanduser("~/.agentic/questions"))
    ap.add_argument("--budget", help="a budget_report.py --json file (requests, cost)")
    ap.add_argument("--discovery", default=".discovery", help="counts the driver-shadow / driver-evidence files per dossier")
    ap.add_argument("--json", help="write the report here")
    args = ap.parse_args(argv)
    if args.selftest:
        return selftest()
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
