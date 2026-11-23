#!/usr/bin/env python3
"""Which delivery flow a dossier ships through: `stub` or `kanban`, from the repo's switches and five gates.

    classify_delivery.py --dossier PATH --root R [--override stub|kanban] [--write]
    classify_delivery.py --landed --root R --since REF [--until REF]
    classify_delivery.py --selftest

The repo's flows come from delivery_flows.py (name = basename of R's main checkout). Only stub on: stub. Only
kanban on: kanban, no gates. Both on: kanban only when every gate passes, one evidence line each:

    G1 exactly one implementer package   counted from `## Work packages` (the UT and IT test rows do not count)
    G2 no shared/public interface change  the planner's line in front matter `delivery_gates:`
    G3 no irreversible migration          the planner's line in front matter `delivery_gates:`; a migration file among the
                                          owned paths is named in the evidence (additive ones may pass: "irreversible"
                                          cannot be read from a path, so the planner's line stays the judge)
    G4 provable without the full stack    CHECKED MECHANICALLY; the planner's line can only add a fail. A criterion's
                                          `(owner: <test>; env: E)` fails it when E is not local/ci (vm, container...),
                                          when its owner test also owns a stack criterion, when the owner path matches
                                          the repo's `stack_tests` globs (stack-repair config), or, as a fallback, when
                                          the criterion text says VM / Lima / live stack / stack database / --stack
    G5 every blocker is done or kanban    read from the blockers' own front matter in .discovery/dossiers

The planner states G2-G4 as a block list, one line each, `G2|G3|G4 pass|fail: <evidence>` (validate_pipeline.py checks
the shape and refuses a kanban dossier carrying a `fail`); a gate it did not state fails, so the gate is never
assumed. `--override stub` is always allowed (the planner or owner moves kanban to stub); `--override kanban` past a
failed gate, or in a repo with kanban off, is refused (exit 1). `--write` sets `delivery:` through dossier_edit.py
(a stub verdict on a dossier with no `delivery:` writes nothing: absent means stub). Prints the gate lines and
`DELIVERY: <verdict>`; exit 0, or 1 refused, 2 unusable input.

`--landed` lists the kanban pull requests merged into `--until` (default `origin/development`) after `--since` (the
stub's cut point): one `#<pr> <dossier> <title>` line each, found by matching the first-parent merge commits'
`#<pr>` against the `pr:` of dossiers with `delivery: kanban`. /stub names them as extra suspects (stub.md).
"""
from __future__ import annotations

import argparse
import fnmatch
import glob
import json
import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import delivery_flows  # noqa: E402
import validate_pipeline as vp  # noqa: E402

GATE_NAMES = {
    "G1": "one implementer package",
    "G2": "no shared interface change",
    "G3": "no irreversible migration",
    "G4": "provable without the full stack",
    "G5": "blockers done or kanban",
}


OWNER_RE = re.compile(r"\(owner:\s*([^;)]*?)\s*;\s*env:\s*([^)]*?)\s*\)")
STACK_WORDS_RE = re.compile(r"\b(VM|Lima|live[- ]stack|full[- ]stack|stack database|shared stack|LiveGraph|NIGHTWATCH_\w+_TESTS)\b|--stack|docker compose", re.I)
LOCAL_ENVS = ("local", "ci")
MIGRATION_RE = re.compile(r"(^|/)(migrations?|alembic/versions|db/migrate)/|\.sql$", re.I)


def criteria(body: str) -> list[tuple[int, str]]:
    sec = vp.sections(body).get("## Acceptance criteria", "")
    items: list[tuple[int, str]] = []
    for line in sec.splitlines():
        n = re.match(r"^\s*(\d+)[.)]\s+(.*)", line)
        if n:
            items.append((int(n.group(1)), n.group(2)))
        elif items and line.strip():
            items[-1] = (items[-1][0], items[-1][1] + " " + line.strip())
    return items


def stack_globs(root: str) -> list[str]:
    """The repo's own list of stack-only test paths (`stack_tests` in its stack-repair config), if it has one."""
    try:
        import stack_repair
        cfg = stack_repair.load_config(root) or {}
    except Exception:  # noqa: BLE001 - no config is the common case
        return []
    g = cfg.get("stack_tests") or []
    return [str(x) for x in g] if isinstance(g, list) else []


def stack_needs(body: str, root: str) -> list[str]:
    """One finding per criterion whose proof needs the full stack, naming the criterion and its test. Empty = none."""
    globs = stack_globs(root)
    parsed = []
    for n, text in criteria(body):
        m = OWNER_RE.search(text)
        parsed.append((n, text, m.group(1).strip() if m else "", m.group(2).strip().lower() if m else ""))
    stack_owners = {o for _, _, o, e in parsed if o and e and e not in LOCAL_ENVS}
    out = []
    for n, text, owner, env in parsed:
        why = None
        words = STACK_WORDS_RE.search(OWNER_RE.sub("", text))
        if env and env not in LOCAL_ENVS:
            why = f"env: {env}"
        elif owner and any(fnmatch.fnmatch(owner, g) for g in globs):
            why = "its test is in the repo's stack_tests"
        elif owner in stack_owners:
            why = "its test file also proves another criterion on the stack"
        elif words:
            why = f"wording names the stack ({words.group(0)})"
        if why:
            out.append(f"criterion {n} ({owner or 'no owner'}): {why}")
    return out


def migrations(rows: list[tuple[str, list[str]]]) -> list[str]:
    return sorted({p for _, ps in rows for p in ps if MIGRATION_RE.search(p)})


def parse(path: str) -> tuple[dict, str]:
    fm, body, _ = vp.split_front_matter(open(path, encoding="utf-8").read())
    return fm, body


def blocker_status(root: str, dossier_id: str) -> tuple[str, str]:
    """(status, delivery) of a blocker's dossier, or ("missing", "")."""
    hits = sorted(glob.glob(os.path.join(root, ".discovery", "dossiers", f"{dossier_id}*.md")))
    for h in hits:
        try:
            fm, _ = parse(h)
        except (OSError, ValueError):
            continue
        if fm.get("id") == dossier_id:
            return str(fm.get("status") or ""), str(fm.get("delivery") or "stub")
    return "missing", ""


def gates(fm: dict, body: str, root: str) -> dict[str, tuple[bool, str]]:
    """Each gate's (passed, evidence line)."""
    out: dict[str, tuple[bool, str]] = {}
    pk = vp.implementer_packages(vp.parse_packages(vp.sections(body).get("## Work packages", "")))
    out["G1"] = (len(pk) == 1, f"{len(pk)} implementer package(s): {', '.join(pk) or 'none'}")
    stated = fm.get("delivery_gates") or []
    for g in ("G2", "G3", "G4"):
        line = next((str(x) for x in stated if str(x).startswith(g + " ")), None)
        if line is None:
            out[g] = (False, "not stated in front matter `delivery_gates:` (an unstated gate fails)")
        else:
            out[g] = (line.split(":", 1)[0].endswith(" pass"), line.split(":", 1)[1].strip())
    mig = migrations(vp.parse_packages(vp.sections(body).get("## Work packages", "")))
    if mig:
        out["G3"] = (out["G3"][0], out["G3"][1] + f" [migration file(s) in owned paths: {', '.join(mig)}]")
    need = stack_needs(body, root)
    if need:
        shown = "; ".join(need[:4]) + (f"; and {len(need) - 4} more" if len(need) > 4 else "")
        out["G4"] = (False, f"mechanical: {shown}. The stack proof waits for /stub (planner said: {out['G4'][1]})")
    bad = []
    blockers = fm.get("blocked_by") or []
    for b in blockers if isinstance(blockers, list) else []:
        status, delivery = blocker_status(root, str(b))
        if status != "done" and delivery != "kanban":
            bad.append(f"{b} ({status}, {delivery})")
    out["G5"] = (not bad, f"open stub-only blocker(s): {', '.join(bad)}" if bad
                 else f"{len(blockers) if isinstance(blockers, list) else 0} blocker(s), all done or kanban")
    return out


def classify(dossier: str, root: str, override: str | None = None) -> tuple[str, list[str], str | None]:
    """(verdict, evidence lines, refusal or None)."""
    flows = delivery_flows.flows_of(delivery_flows.repo_name(root))
    fm, body = parse(dossier)
    lines: list[str] = []
    if flows["stub"] and not flows["kanban"]:
        verdict, ok = "stub", False
        lines.append("flows: stub only")
    elif flows["kanban"] and not flows["stub"]:
        verdict, ok = "kanban", True
        lines.append("flows: kanban only (no gates)")
    else:
        lines.append("flows: stub and kanban")
        g = gates(fm, body, root)
        for k, (passed, ev) in g.items():
            lines.append(f"{k} {'pass' if passed else 'FAIL'} — {GATE_NAMES[k]}: {ev}")
        ok = all(p for p, _ in g.values())
        verdict = "kanban" if ok else "stub"
    refusal = None
    if override == "stub":
        verdict = "stub"
    elif override == "kanban":
        if flows["kanban"] and ok:
            verdict = "kanban"
        else:
            refusal = "kanban refused: " + ("the repo has kanban off" if not flows["kanban"] else "a gate failed")
    return verdict, lines, refusal


def write(dossier: str, verdict: str) -> str:
    fm, _ = parse(dossier)
    if verdict == "stub" and "delivery" not in fm:
        return "delivery: absent (means stub), nothing written"
    r = subprocess.run([sys.executable, os.path.join(HERE, "dossier_edit.py"), "set", dossier, f"delivery={verdict}"],
                       capture_output=True, text=True)
    return (r.stdout + r.stderr).strip() or f"delivery: {verdict}"


def landed(root: str, since: str, until: str = "origin/development") -> list[str]:
    """`#<pr> <dossier id> <title>` for each kanban PR merged in since..until (first-parent merges, oldest first)."""
    import re
    r = subprocess.run(["git", "-C", root, "log", "--first-parent", "--reverse", "--format=%s", f"{since}..{until}"],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise OSError(r.stderr.strip() or "git log failed")
    prs = [m.group(1) for m in (re.search(r"#(\d+)", ln) for ln in r.stdout.splitlines()) if m]
    kanban: dict[str, tuple[str, str]] = {}
    for h in sorted(glob.glob(os.path.join(root, ".discovery", "dossiers", "W-*.md"))):
        try:
            fm, _ = parse(h)
        except (OSError, ValueError):
            continue
        if fm.get("delivery") == "kanban":
            for url in fm.get("pr") or []:
                kanban[str(url).rstrip("/").rsplit("/", 1)[-1]] = (str(fm.get("id")), str(fm.get("title")))
    return [f"#{n} {kanban[n][0]} {kanban[n][1]}" for n in prs if n in kanban]


def selftest() -> int:
    bad: list[str] = []
    with tempfile.TemporaryDirectory() as td:
        os.environ["ASD_DELIVERY_FLOWS"] = os.path.join(td, "flows.json")
        root = os.path.join(td, "repo")
        dd = os.path.join(root, ".discovery", "dossiers")
        os.makedirs(dd)
        pk = "| P1 fix | src/a.py | — |\n| UT unit tests | tests/u.py | — |\n| IT it | tests/i.py | — |\n"
        gl = "delivery_gates:\n  - G2 pass: no member used outside src/a.py\n  - G3 pass: no migration\n  - G4 pass: unit tests only\n"

        def dossier(name: str, id_: str, status="ready", extra="", rows=pk, delivery="") -> str:
            p = os.path.join(dd, name)
            open(p, "w").write(f"---\nid: {id_}\nstatus: {status}\n{delivery}{extra}---\n\n## Problem\nx\n\n"
                               f"## Work packages\n\n| Package | Owned paths | Depends on |\n|---|---|---|\n{rows}\n"
                               "## Acceptance criteria\n1. x\n")
            return p

        def check(name: str, ok: bool) -> None:
            if not ok:
                bad.append(name)

        def setflows(stub: bool, kanban: bool) -> None:
            json.dump({os.path.basename(root): {"stub": stub, "kanban": kanban}}, open(os.environ["ASD_DELIVERY_FLOWS"], "w"))

        good = dossier("W-001.md", "W-001", extra=gl + "blocked_by: []\n")
        check("default flows: stub", classify(good, root)[0] == "stub")
        setflows(False, True)
        check("kanban only: kanban without gates", classify(dossier("W-002.md", "W-002", rows=pk + "| P2 b | src/b.py | — |\n"), root)[0] == "kanban")
        setflows(True, True)
        v, lines, ref = classify(good, root)
        check("both on, all gates pass: kanban, five evidence lines", v == "kanban" and ref is None and sum(l[:2] in GATE_NAMES for l in lines) == 5)
        two = dossier("W-003.md", "W-003", extra=gl, rows=pk + "| P2 b | src/b.py | — |\n")
        check("G1: two packages fail to stub", classify(two, root)[0] == "stub" and any(l.startswith("G1 FAIL") for l in classify(two, root)[1]))
        check("G2-G4 unstated fail", classify(dossier("W-004.md", "W-004"), root)[0] == "stub")
        fail3 = dossier("W-005.md", "W-005", extra=gl.replace("G3 pass", "G3 fail"))
        check("a stated fail keeps stub", classify(fail3, root)[0] == "stub")
        open_b = dossier("W-006.md", "W-006", status="ready")
        done_b = dossier("W-007.md", "W-007", status="done")
        kan_b = dossier("W-008.md", "W-008", status="ready", delivery="delivery: kanban\n")
        for nm, blk, want in (("open stub blocker", "W-006", "stub"), ("done blocker", "W-007", "kanban"),
                              ("kanban blocker", "W-008", "kanban"), ("missing blocker", "W-099", "stub")):
            d = dossier("W-010.md", "W-010", extra=gl + f"blocked_by: [{blk}]\n")
            check(f"G5 {nm}", classify(d, root)[0] == want)
        check("override stub always allowed", classify(good, root, "stub")[0] == "stub" and classify(good, root, "stub")[2] is None)
        check("override kanban past a failed gate refused", classify(two, root, "kanban")[2] is not None and classify(two, root, "kanban")[0] == "stub")
        def swap_crit(d: str, crit: str) -> str:
            head = open(d).read().split("## Acceptance criteria")[0]
            open(d, "w").write(head + "## Acceptance criteria\n" + crit)
            return d
        vm = swap_crit(dossier("W-020.md", "W-020", extra=gl), "1. a (owner: tests/u.py; env: local)\n2. b (owner: tests/i.py; env: vm)\n")
        v, lines, _ = classify(vm, root)
        check("G4 mechanical fail on env: vm despite a stated pass", v == "stub" and any(
            l.startswith("G4 FAIL") and "criterion 2 (tests/i.py): env: vm" in l and "planner said: unit tests only" in l for l in lines))
        check("G4 mechanical fail cannot be overridden to kanban", classify(vm, root, "kanban")[2] is not None)
        check("G4 unit-only criteria stay kanban", classify(swap_crit(dossier("W-024.md", "W-024", extra=gl),
              "1. a (owner: tests/u.py; env: local)\n2. b (owner: tests/i.py; env: ci)\n"), root)[0] == "kanban")
        words = swap_crit(dossier("W-021.md", "W-021", extra=gl), "1. Runs against the stack database. (owner: tests/u.py; env: local)\n")
        check("G4 keyword fallback", classify(words, root)[0] == "stub")
        check("G4 a test file shared with a stack criterion", len(stack_needs(
            "## Acceptance criteria\n1. a (owner: tests/i.py; env: vm)\n2. b (owner: tests/i.py; env: local)\n", root)) == 2)
        cfgp = os.path.join(td, "sr.json")
        json.dump({"vm": "v", "stack_tests": ["tests/db_*.py"]}, open(cfgp, "w"))
        os.environ["ASD_STACK_REPAIR_CONFIG"] = cfgp
        check("G4 repo stack_tests glob", stack_needs("## Acceptance criteria\n1. a (owner: tests/db_x.py; env: local)\n", root) != []
              and stack_needs("## Acceptance criteria\n1. a (owner: tests/u.py; env: ci)\n", root) == [])
        os.environ.pop("ASD_STACK_REPAIR_CONFIG")
        migd = dossier("W-023.md", "W-023", extra=gl, rows=pk.replace("src/a.py", "src/a.py, db/migrations/0072_x.sql"))
        check("G3 evidence names a migration file", any(l.startswith("G3 pass") and "0072_x.sql" in l for l in classify(migd, root)[1]))
        check("override kanban on passing gates fine", classify(good, root, "kanban")[:1] == ("kanban",))
        setflows(True, False)
        check("override kanban with kanban off refused", classify(good, root, "kanban")[2] is not None)
        setflows(True, True)
        r = subprocess.run([sys.executable, os.path.abspath(__file__), "--dossier", good, "--root", root, "--write"],
                           capture_output=True, text=True, env=os.environ)
        check("CLI prints verdict and writes delivery", r.returncode == 0 and "DELIVERY: kanban" in r.stdout
              and "delivery: kanban" in open(good).read())
        r = subprocess.run([sys.executable, os.path.abspath(__file__), "--dossier", two, "--root", root, "--override", "kanban"],
                           capture_output=True, text=True, env=os.environ)
        check("CLI refuses kanban past a failed gate, exit 1", r.returncode == 1)
        r = subprocess.run([sys.executable, os.path.abspath(__file__), "--dossier", two, "--root", root, "--write"],
                           capture_output=True, text=True, env=os.environ)
        check("stub verdict writes nothing when delivery is absent", r.returncode == 0 and "delivery:" not in open(two).read().split("---")[1])
        g = lambda *x: subprocess.run(["git", "-C", root, "-c", "user.name=t", "-c", "user.email=t@t", *x],
                                      capture_output=True, text=True, check=True)
        g("init", "-q", "-b", "development")
        g("commit", "-q", "--allow-empty", "-m", "cut")
        for n in ("12", "13"):
            g("commit", "-q", "--allow-empty", "-m", f"Merge pull request #{n} from o/feat/x")
        txt = open(good).read()
        open(good, "w").write(txt.replace("status: ready\n", "status: done\npr: [https://github.com/o/r/pull/13]\n", 1))
        check("landed lists only the kanban PR after the cut", landed(root, "HEAD~2", "HEAD") == ["#13 W-001 None"])
        check("landed is empty past the last merge", landed(root, "HEAD", "HEAD") == [])
    for b in bad:
        print(f"FAIL  {b}")
    print("selftest: " + ("FAIL" if bad else "ok"))
    return 1 if bad else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dossier")
    ap.add_argument("--root")
    ap.add_argument("--override", choices=["stub", "kanban"])
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--landed", action="store_true")
    ap.add_argument("--since")
    ap.add_argument("--until", default="origin/development")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if a.landed:
        if not a.root or not a.since:
            ap.error("--landed needs --root and --since")
        try:
            print("\n".join(landed(a.root, a.since, a.until)) or "none")
        except OSError as e:
            print(f"unusable: {e}", file=sys.stderr)
            return 2
        return 0
    if not a.dossier or not a.root:
        ap.error("--dossier and --root are required")
    try:
        verdict, lines, refusal = classify(a.dossier, a.root, a.override)
    except (OSError, ValueError) as e:
        print(f"unusable: {e}", file=sys.stderr)
        return 2
    print("\n".join(lines))
    if refusal:
        print(f"REFUSED: {refusal}", file=sys.stderr)
        print(f"DELIVERY: {verdict}")
        return 1
    print(f"DELIVERY: {verdict}")
    if a.write:
        print(write(a.dossier, verdict))
    return 0


if __name__ == "__main__":
    sys.exit(main())
