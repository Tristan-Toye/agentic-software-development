#!/usr/bin/env python3
"""Read the shadow evidence of the mechanical-phase drivers next to what the orchestrator did by hand.

In shadow mode (`ASD_DRIVER_<NAME>=shadow`, set through scripts/driver_modes.py) the
orchestrator does a phase's manual steps as today and then runs the driver once; the
driver saves `<git common dir>/asd-drivers/driver-shadow/<dossier>/<driver>-<ts>.json`.
This report puts, per driver:

    runs        the number of shadow runs and their exit codes (2 = a surprise)
    findings    what the driver found (from the evidence files)
    build log   the orchestrator's own Build log lines for that phase (the dossier's
                `## Build log`, read with dossier_edit.section; the dossier is
                <root>/.discovery/dossiers/<ID>.md, or the copy in the worktree its
                front matter names when that one has the longer log)
    agreement   only where it is mechanical, else `needs human read`; never guessed:
                finalize_pr    driver found conflict markers / a duplicate ADR / a DEFECT
                               vs the orchestrator logged a fix of one
                merge_packages driver's TOUCHED_BEYOND vs a scope / drift / TOUCHED_BEYOND line
                test_round     the driver's failed-test count vs the failed counts of the
                               `SUITE:` lines
                pre_fanout_check, strength_gate_prep: always `needs human read`

and one verdict line per driver:

    READY-FOR-ON  at least 3 dossiers with shadow runs, no surprise exit, no disagreement
                  (dossiers marked `needs human read` do not block; read them)
    NOT-YET       with the reason (fewer than 3 dossiers, a surprise exit, a disagreement)
    NO-DATA       no shadow evidence at all

Usage:
    driver_shadow_report.py [ID ...] [--root DIR] [--driver NAME ...] [--json]
    driver_shadow_report.py --selftest
Without IDs, every dossier that has shadow evidence. `--evidence DIR` and `--dossiers DIR`
override the two locations (tests). Exit 0 report printed, 2 unusable input.

No third-party imports.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import dossier_edit  # noqa: E402
import driver_modes  # noqa: E402

DRIVERS = driver_modes.NAMES
MIN_DOSSIERS = 3
LINE_SHOW = 6
# The orchestrator's build-log lines that belong to each driver's phase.
PHASE_LINES = {
    "pre_fanout_check": r"(?i)phase 3|census|frozen|checklist|receipt|validat|slice|criteria|\bgates?\b",
    "merge_packages": r"(?i)merge|TOUCHED_BEYOND|scope|drift|owned|conflict",
    "strength_gate_prep": r"(?i)strength|coverage|weak|stub|red[- ]run",
    "test_round": r"SUITE|(?i:arbitrat|round|failing|failed)",
    "finalize_pr": r"(?i)finaliz|conflict marker|duplicate|\bADR\b|sync|merge of",
}
FINALIZE_FOUND = re.compile(r"(?i)conflict marker|duplicat\w* ADR|^DEFECT|\bDEFECT\b")
FINALIZE_LOGGED = re.compile(r"(?i)(conflict marker|duplicat\w* ADR|ADR \w* ?(collision|duplicate|renumber)|resolved? (the )?conflict)")
MERGE_LOGGED = re.compile(r"(?i)TOUCHED_BEYOND|\bscope\b|\bdrift\b|outside (its )?(owned|OWNED)")
SUITE_FAILED = re.compile(r"(\d+) failed")


def split_front(text: str) -> dict:
    lines = text.split("\n")
    out: dict = {}
    if lines and lines[0].strip() == "---":
        for ln in lines[1:]:
            if ln.strip() == "---":
                break
            k, sep, v = ln.partition(":")
            if sep:
                out[k.strip()] = v.strip().strip("'\"")
    return out


def build_log(path: str) -> list[str]:
    """The Build log lines of a dossier file, newest last; empty when there is no section."""
    try:
        text = dossier_edit.read(path)
        sec = dossier_edit.section(text, "Build log")
    except (OSError, dossier_edit.Refused):
        return []
    rows = [re.sub(r"^\d+\t", "", r) for r in sec.split("\n")[1:]]
    return [r.strip() for r in rows if r.strip()]


def dossier_log(dossiers_dir: str, dossier: str) -> tuple[list[str], str]:
    """(Build log lines, the file they came from). The worktree copy of a dossier in flight
    can hold lines the main copy has not; the longer log wins."""
    main = os.path.join(dossiers_dir, f"{dossier}.md")
    best: tuple[list[str], str] = ([], "")
    paths = [main]
    try:
        wt = split_front(dossier_edit.read(main)).get("worktree", "")
        if wt:
            paths.insert(0, os.path.join(os.path.expanduser(wt), ".discovery", "dossiers", f"{dossier}.md"))
    except OSError:
        pass
    for p in paths:
        if os.path.isfile(p):
            rows = build_log(p)
            if len(rows) > len(best[0]) or not best[1]:
                best = (rows, p)
    return best


def load_runs(evidence: str, dossier: str) -> tuple[dict[str, list[dict]], list[str]]:
    """{driver: [evidence dict, oldest first]} for one dossier's shadow directory, plus notes on unreadable files."""
    runs: dict[str, list[dict]] = {}
    notes: list[str] = []
    for f in sorted(glob.glob(os.path.join(evidence, "driver-shadow", dossier, "*.json"))):
        try:
            with open(f, encoding="utf-8") as fh:
                data = json.load(fh)
            name = str(data.get("driver") or os.path.basename(f).rsplit("-", 1)[0])
            data["_file"] = f
            runs.setdefault(name, []).append(data)
        except (OSError, ValueError) as err:
            notes.append(f"{f}: unreadable ({err})")
    return runs, notes


def agreement(driver: str, runs: list[dict], log: list[str], have_log: bool) -> tuple[str, str]:
    """(status, detail): status is agree | disagree | needs human read. Only where the comparison is well defined."""
    human = ("needs human read", "no mechanical comparison is defined for this driver")
    if driver == "finalize_pr":
        if not have_log:
            return "needs human read", "no dossier build log to compare with"
        found = [f for r in runs for f in r.get("findings", []) if FINALIZE_FOUND.search(f)]
        logged = [ln for ln in log if FINALIZE_LOGGED.search(ln)]
        if bool(found) == bool(logged):
            return "agree", ("driver and orchestrator both found/fixed it" if found else "neither the driver nor the orchestrator found a conflict marker or duplicate ADR")
        if found:
            return "disagree", f"driver found {found[0][:90]!r}; the build log has no such fix"
        return "disagree", f"the build log has {logged[0][:90]!r}; the driver found nothing"
    if driver == "merge_packages":
        if not have_log:
            return "needs human read", "no dossier build log to compare with"
        beyond = [r for r in runs if (r.get("facts") or {}).get("touched_beyond")]
        logged = [ln for ln in log if MERGE_LOGGED.search(ln)]
        if bool(beyond) == bool(logged):
            return "agree", ("both name a path beyond OWNED_PATHS" if beyond else "neither names a path beyond OWNED_PATHS")
        if beyond:
            return "disagree", "driver stopped at TOUCHED_BEYOND " + ", ".join((beyond[0]["facts"]["touched_beyond"].get("paths") or [])[:3]) + "; the build log has no scope/drift line"
        return "disagree", f"the build log has {logged[0][:90]!r}; the driver saw no path beyond OWNED_PATHS"
    if driver == "test_round":
        suites = [ln for ln in log if ln.lstrip("- ").startswith("SUITE")]
        if not suites:
            return "needs human read", "the build log has no SUITE: line"
        logged = {int(m.group(1)) for ln in suites for m in [SUITE_FAILED.search(ln)] if m}
        if not logged:
            return "needs human read", "the SUITE: lines carry no failed count"
        counts = []
        for r in runs:
            s = (r.get("facts") or {}).get("suite")
            if isinstance(s, dict) and isinstance(s.get("failed"), int):
                counts.append(s["failed"])
        if not counts:
            return "needs human read", "the shadow runs recorded no suite count (a surprise stop)"
        missing = [c for c in counts if c not in logged]
        if not missing:
            return "agree", f"driver failed counts {counts} all appear in SUITE: lines {sorted(logged)}"
        return "disagree", f"driver counted {missing} failed; the SUITE: lines say {sorted(logged)}"
    return human


def report(evidence: str, dossiers_dir: str, ids: list[str], only: list[str]) -> dict:
    if not ids:
        ids = sorted(os.path.basename(p) for p in glob.glob(os.path.join(evidence, "driver-shadow", "*")) if os.path.isdir(p))
    per: dict[str, dict] = {n: {"dossiers": []} for n in DRIVERS if not only or n in only}
    notes: list[str] = []
    for d in ids:
        runs_by, n = load_runs(evidence, d)
        notes += n
        log, src = dossier_log(dossiers_dir, d)
        for name, runs in runs_by.items():
            if name not in per:
                continue
            pat = re.compile(PHASE_LINES.get(name, r"$^"))
            status, detail = agreement(name, runs, log, bool(src))
            per[name]["dossiers"].append({
                "dossier": d,
                "runs": len(runs),
                "exits": [r.get("exit") for r in runs],
                "surprises": sum(1 for r in runs if r.get("exit") == 2),
                "findings": [f for r in runs for f in r.get("findings", [])],
                "stopped_at": [r["stopped_at"] for r in runs if r.get("stopped_at")],
                "build_log": [ln for ln in log if pat.search(ln)],
                "build_log_source": src,
                "agreement": status,
                "agreement_detail": detail,
            })
    for name, p in per.items():
        ds = p["dossiers"]
        surprises = [d["dossier"] for d in ds if d["surprises"]]
        disagree = [d["dossier"] for d in ds if d["agreement"] == "disagree"]
        human = [d["dossier"] for d in ds if d["agreement"] == "needs human read"]
        if not ds:
            p["verdict"], p["reason"] = "NO-DATA", "no shadow evidence"
        elif len(ds) < MIN_DOSSIERS:
            p["verdict"], p["reason"] = "NOT-YET", f"{len(ds)} of {MIN_DOSSIERS} dossiers with shadow runs"
        elif surprises:
            p["verdict"], p["reason"] = "NOT-YET", "surprise exit (2) in " + ", ".join(surprises)
        elif disagree:
            p["verdict"], p["reason"] = "NOT-YET", "driver and orchestrator disagree in " + ", ".join(disagree)
        else:
            p["verdict"] = "READY-FOR-ON"
            p["reason"] = f"{len(ds)} dossiers, no surprise exit, no disagreement" + (f"; {len(human)} need a human read: {', '.join(human)}" if human else "")
        if surprises and len(ds) < MIN_DOSSIERS:
            p["reason"] += "; surprise exit (2) in " + ", ".join(surprises)
        if disagree and len(ds) < MIN_DOSSIERS:
            p["reason"] += "; disagreement in " + ", ".join(disagree)
    return {"drivers": per, "dossiers": ids, "notes": notes}


def render(rep: dict) -> str:
    out: list[str] = []
    for name, p in rep["drivers"].items():
        out.append(f"== {name}")
        for d in p["dossiers"]:
            out.append(f"  {d['dossier']}: {d['runs']} shadow run(s), exits {d['exits']}" + (f", {d['surprises']} surprise(s)" if d["surprises"] else ""))
            for s in d["stopped_at"][:2]:
                out.append(f"    stopped at: {s[:160]}")
            for f in d["findings"][:LINE_SHOW]:
                out.append(f"    driver finding: {f[:160]}")
            if len(d["findings"]) > LINE_SHOW:
                out.append(f"    (+{len(d['findings']) - LINE_SHOW} more findings in the evidence file)")
            for ln in d["build_log"][-LINE_SHOW:]:
                out.append(f"    build log: {ln[:160]}")
            if not d["build_log"]:
                out.append("    build log: (no matching line)" if d["build_log_source"] else "    build log: (dossier not found)")
            out.append(f"    agreement: {d['agreement']} ({d['agreement_detail']})")
        out.append(f"  VERDICT {name}: {p['verdict']} ({p['reason']})")
    for n in rep["notes"]:
        out.append("note: " + n)
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Shadow evidence of the drivers next to the orchestrator's build-log lines.")
    ap.add_argument("ids", nargs="*", help="dossier ids (default: every dossier with shadow evidence)")
    ap.add_argument("--root", help="the main checkout (default: found from the working directory)")
    ap.add_argument("--driver", action="append", default=[], choices=DRIVERS, help="only this driver (repeatable)")
    ap.add_argument("--evidence", help="the asd-drivers directory (default: <git common dir>/asd-drivers)")
    ap.add_argument("--dossiers", help="the dossiers directory (default: <root>/.discovery/dossiers)")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args(argv)
    if a.selftest:
        return selftest()
    if a.evidence:
        evidence = a.evidence
        root = a.root or os.getcwd()
    else:
        import driver_common as dc

        root = a.root or dc.main_checkout(os.getcwd())
        evidence = dc.evidence_root(root)
    dossiers = a.dossiers or os.path.join(root, ".discovery", "dossiers")
    if not os.path.isdir(os.path.join(evidence, "driver-shadow")) and not a.ids:
        print(f"driver_shadow_report.py: no shadow evidence under {evidence}")
    rep = report(evidence, dossiers, a.ids, a.driver)
    print(json.dumps(rep, indent=1) if a.json else render(rep))
    return 0


def selftest() -> int:
    failures: list[str] = []

    def check(name: str, ok: bool) -> None:
        if not ok:
            failures.append(name)

    def ev(base: str, dossier: str, driver: str, n: int, exit_: int, findings=None, facts=None, stopped=None) -> None:
        d = os.path.join(base, "driver-shadow", dossier)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, f"{driver}-2026111{n}T000000Z.json"), "w") as fh:
            json.dump({"driver": driver, "mode": "shadow", "dossier": dossier, "exit": exit_, "findings": findings or [],
                       "facts": facts or {}, "stopped_at": stopped}, fh)

    def dossier(base: str, name: str, log: list[str], worktree: str = "") -> None:
        os.makedirs(base, exist_ok=True)
        fm = "---\nid: " + name + "\nstatus: building\n" + (f"worktree: {worktree}\n" if worktree else "") + "---\n"
        with open(os.path.join(base, name + ".md"), "w") as fh:
            fh.write(fm + "# T\n\n## Problem\n\nx\n\n## Build log\n\n" + "\n".join("- " + ln for ln in log) + "\n")

    with tempfile.TemporaryDirectory() as tmp:
        evd, dd = os.path.join(tmp, "asd-drivers"), os.path.join(tmp, "dossiers")
        # merge_packages: three dossiers, clean agreement (no TOUCHED_BEYOND either side)
        for i, d in enumerate(("W-1", "W-2", "W-3")):
            ev(evd, d, "merge_packages", 1, 0, facts={"merged": ["A"]})
            dossier(dd, d, ["Phase 5: merged A", "SUITE: local on abc, base def — 5 passed, 0 failed, 0 skipped; /x.log"])
        # test_round: W-1 agrees (2 failed in the SUITE line), W-2 disagrees, W-3 has a surprise
        ev(evd, "W-1", "test_round", 1, 1, ["x"], {"suite": {"failed": 2, "passed": 5}})
        dossier(dd, "W-1", ["SUITE: local on a, base b — 5 passed, 2 failed, 0 skipped; /l"])
        ev(evd, "W-2", "test_round", 1, 1, ["y"], {"suite": {"failed": 4, "passed": 5}})
        ev(evd, "W-3", "test_round", 1, 2, stopped="no failure parsed")
        # finalize_pr: three dossiers, W-2's driver found a duplicate ADR the orchestrator did not log
        for d in ("W-1", "W-2", "W-3"):
            ev(evd, d, "finalize_pr", 1, 1 if d == "W-2" else 0, ["duplicated ADR number(s): 0007"] if d == "W-2" else [])
        # pre_fanout_check: only two dossiers
        for d in ("W-1", "W-2"):
            ev(evd, d, "pre_fanout_check", 1, 0)
        # a corrupt evidence file is noted, not fatal
        with open(os.path.join(evd, "driver-shadow", "W-3", "strength_gate_prep-1.json"), "w") as fh:
            fh.write("{bad")
        rep = report(evd, dd, [], [])
        v = {n: p["verdict"] for n, p in rep["drivers"].items()}
        check("dossiers are found from the shadow directories", rep["dossiers"] == ["W-1", "W-2", "W-3"])
        check("merge_packages: 3 dossiers, no surprise, no disagreement -> READY-FOR-ON", v["merge_packages"] == "READY-FOR-ON")
        check("test_round: a surprise exit -> NOT-YET naming it", v["test_round"] == "NOT-YET" and "W-3" in rep["drivers"]["test_round"]["reason"])
        tr = {d["dossier"]: d for d in rep["drivers"]["test_round"]["dossiers"]}
        check("test_round: matching failed count agrees, differing disagrees, a surprise run has no count to compare",
              tr["W-1"]["agreement"] == "agree" and tr["W-2"]["agreement"] == "disagree" and tr["W-3"]["agreement"] == "needs human read" and tr["W-3"]["surprises"] == 1)
        dossier(dd, "W-2", ["round 1 arbitrated"])
        check("test_round: no SUITE line in the log needs a human read",
              report(evd, dd, ["W-2"], [])["drivers"]["test_round"]["dossiers"][0]["agreement"] == "needs human read")
        dossier(dd, "W-2", ["SUITE: local on a, base b — 5 passed, 4 failed, 0 skipped; /l"])
        check("test_round: a failed count present in a SUITE line agrees",
              report(evd, dd, ["W-2"], [])["drivers"]["test_round"]["dossiers"][0]["agreement"] == "agree")
        check("finalize_pr: a duplicate ADR the log never mentions disagrees -> NOT-YET", v["finalize_pr"] == "NOT-YET" and "disagree" in rep["drivers"]["finalize_pr"]["reason"])
        dossier(dd, "W-2", ["Phase 9: renumbered a duplicate ADR 0007 after the sync"])
        check("finalize_pr: logged and found both -> agree",
              report(evd, dd, ["W-2"], [])["drivers"]["finalize_pr"]["dossiers"][0]["agreement"] == "agree")
        check("pre_fanout_check: two dossiers -> NOT-YET with the count", v["pre_fanout_check"] == "NOT-YET" and "2 of 3" in rep["drivers"]["pre_fanout_check"]["reason"])
        check("pre_fanout_check: never a mechanical agreement", all(d["agreement"] == "needs human read" for d in rep["drivers"]["pre_fanout_check"]["dossiers"]))
        check("strength_gate_prep: only an unreadable file -> NO-DATA and a note", v["strength_gate_prep"] == "NO-DATA" and any("unreadable" in n for n in rep["notes"]))
        # merge_packages disagreement
        ev(evd, "W-3", "merge_packages", 2, 2, ["x"], {"touched_beyond": {"package": "B", "paths": ["src/x.py"]}})
        check("merge_packages: TOUCHED_BEYOND without a log line disagrees and surprises",
              report(evd, dd, [], [])["drivers"]["merge_packages"]["verdict"] == "NOT-YET")
        dossier(dd, "W-3", ["TOUCHED_BEYOND src/x.py accepted, package table updated"])
        m = report(evd, dd, ["W-3"], ["merge_packages"])
        check("merge_packages: a logged TOUCHED_BEYOND agrees; --driver narrows", m["drivers"]["merge_packages"]["dossiers"][0]["agreement"] == "agree" and list(m["drivers"]) == ["merge_packages"])
        # the worktree copy with the longer log wins
        wt = os.path.join(tmp, "wt")
        dossier(os.path.join(wt, ".discovery", "dossiers"), "W-9", ["a", "b", "SUITE: local on x, base y — 1 passed, 1 failed; /l"])
        dossier(dd, "W-9", ["a"], worktree=wt)
        check("the worktree copy of the dossier is read when its log is longer", len(dossier_log(dd, "W-9")[0]) == 3)
        check("a missing dossier is not fatal", dossier_log(dd, "W-404") == ([], ""))
        text = render(rep)
        check("the text names a verdict line per driver", all(f"VERDICT {n}:" in text for n in DRIVERS))
        check("--json round-trips", json.loads(json.dumps(rep))["drivers"]["merge_packages"]["verdict"] == "READY-FOR-ON")
        check("an empty evidence directory is NO-DATA for all", all(p["verdict"] == "NO-DATA" for p in report(os.path.join(tmp, "none"), dd, [], [])["drivers"].values()))
    if failures:
        print("driver_shadow_report selftest FAILED: " + "; ".join(failures))
        return 1
    print("driver_shadow_report selftest OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
