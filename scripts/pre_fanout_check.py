#!/usr/bin/env python3
"""Phase 3's mechanical checks before the fan-out, in one call.

Everything here is a grep, a parse or a set check. Every judgement the phase
owns stays with the orchestrator: which test file owns a criterion, the
`PROMISE_CHECKLIST` derivation and its seven diffs, the contract review's
`AMBIGUITY:` and `DEFECT:` lines, the test files and their sizes.

Steps (each prints one status line; a failed step does not stop the others,
because they read independent facts):

    validate      validate_pipeline.py --dossier ID (disjoint owned paths, no
                  bodies in the contract, criteria carry owner and env)
    receipts      validate_pipeline.py --dossier ID --pre-fanout (hooks:,
                  contract_review:, PAYLOAD-LINT:, ADMISSION:, SUITE-RUNNER:,
                  GATES:, CENSUS:, CHECKLIST-IDS:)
    criteria      every acceptance criterion's `(owner: PATH; env: E)` names a
                  path some work package owns; the criterion -> owner map is
                  printed for Opus to read (it chooses owners, the driver
                  only finds the ones that name nothing)
    census        with `--member NAME` (repeatable), the exact-output assertion
                  recipes of phase-3.md over the tree, and with `--census-pattern
                  REGEX` any pin pattern: hits are printed, capped, for Opus to
                  read and own
    frozen        every `N things (grep ... )` in the dossier carries a command;
                  the command is run (read-only commands only) and compared
    gates         with `--gates FILE`, run_gates.py over the materialised
                  contract in X (the repository's own text gates)
    slices        with `--slices FILE` (JSON `{"surface": ["member", ...]}`) the
                  slices and `.discovery/checklists/<ID>.md` agree: every member
                  in exactly one slice, none unknown

Exit 0 clean, 1 findings, 2 surprise. See driver_common.py for modes and the
evidence file.

Usage:
    ASD_DRIVER_PRE_FANOUT_CHECK=on python3 pre_fanout_check.py --dossier W-014 \\
        --worktree X [--member Flush.add ...] [--gates FILE] [--slices FILE] [--log DOSSIER_FILE]
"""

from __future__ import annotations

import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import driver_common as dc  # noqa: E402
import merge_packages as mp  # noqa: E402

OWNER_RE = re.compile(r"\(owner:\s*([^;)]*?)\s*;\s*env:\s*([^)]*?)\s*\)")
FROZEN_RE = re.compile(r"(\d+)\s+([A-Za-z][\w ./-]{0,40}?)\s*\(((?:grep|git|wc|find|rg|ls)\b[^)]*)\)")
UNSAFE = re.compile(r"[;&`$<>]|\brm\b|\bsudo\b|\|\s*(sh|bash)\b")


def recipes(member: str) -> list[str]:
    m = re.escape(member)
    return [
        rf"assert_eq!\(.*({m})",
        rf"assert .*{m}.* == [\[{{(]",
        rf"{m}.*(toEqual|toStrictEqual|toBe)\(",
        rf"(Assert\.(Equal|AreEqual)|assertEquals)\(.*{m}",
    ]


def criteria_items(text: str) -> list[tuple[int, str]]:
    m = re.search(r"^## Acceptance criteria\s*$(.*?)(?=^## |\Z)", text, re.M | re.S)
    if not m:
        return []
    items: list[tuple[int, str]] = []
    for line in m.group(1).splitlines():
        n = re.match(r"^\s*(\d+)[.)]\s+(.*)", line)
        if n:
            items.append((int(n.group(1)), n.group(2)))
        elif items and line.strip():
            items[-1] = (items[-1][0], items[-1][1] + " " + line.strip())
    return items


def step_criteria(d: dc.Driver, text: str) -> None:
    items = criteria_items(text)
    rows = mp.parse_package_table(text)
    owned = [p for r in rows for p in r["owned"]]
    bad, mapping = [], {}
    for n, body in items:
        m = OWNER_RE.search(body)
        if not m:
            bad.append(f"criterion {n} has no (owner: ...; env: ...)")
            continue
        owner, env = m.group(1).strip(), m.group(2).strip()
        mapping[str(n)] = {"owner": owner, "env": env}
        if not mp.inside(owner, owned):
            bad.append(f"criterion {n}: owner {owner} is in no work package's owned paths")
    d.facts["criterion_owner_map"] = mapping
    d.facts["environments"] = sorted({v["env"] for v in mapping.values()})
    if not items:
        bad.append("no numbered criteria found under ## Acceptance criteria")
    if bad:
        d.step("criteria", "finding", f"{len(bad)} criterion problem(s)")
        for b in bad[:8]:
            d.finding(b)
    else:
        d.step("criteria", "ok", f"{len(items)} criteria, each owned by a work-package path; envs: {', '.join(d.facts['environments'])}")


def step_census(d: dc.Driver, wt: str, args) -> None:
    patterns = [(m, p) for m in args.member for p in recipes(m)] + [("pattern", p) for p in args.census_pattern]
    if not patterns:
        d.step("census", "skipped", "no --member or --census-pattern given")
        return
    hits: dict[str, list[str]] = {}
    for label, pat in patterns:
        rc, out = dc.git(wt, "grep", "-n", "-E", pat, "--", ".")
        if rc == 0:
            hits.setdefault(label, []).extend(out.splitlines())
        elif rc != 1:
            raise dc.Surprise(f"git grep -E {pat!r} failed ({rc}): {out.strip()[:100]}")
    d.facts["census_hits"] = {k: v[:8] for k, v in hits.items()}
    total = sum(len(v) for v in hits.values())
    if total:
        d.step("census", "finding", f"{total} hit(s) over {len(hits)} member(s)/pattern(s): each needs an owner or a criterion")
        for k, v in list(hits.items())[:5]:
            d.finding(f"census {k}: {len(v)} hit(s), first {v[0][:120]}")
    else:
        d.step("census", "ok", "no hits")


def step_frozen(d: dc.Driver, text: str, wt: str) -> None:
    ran, off = [], []
    for m in FROZEN_RE.finditer(text.split("## Build log")[0]):
        count, noun, cmd = int(m.group(1)), m.group(2).strip(), m.group(3).strip()
        if UNSAFE.search(cmd):
            off.append(f"{count} {noun}: command not run (shell metacharacters)")
            continue
        rc, out = dc.sh(cmd, cwd=wt, shell=True, timeout=60)
        nums = re.findall(r"^\s*(\d+)\s*$", out, re.M)
        if len(nums) == 1:
            got = int(nums[0])
            ran.append({"claim": f"{count} {noun}", "command": cmd, "got": got, "match": got == count})
            if got != count:
                d.finding(f"frozen count '{count} {noun}': `{cmd}` gives {got} — the mechanism wins; log an erratum and fix the number")
        else:
            off.append(f"{count} {noun}: `{cmd}` printed no single number (rc {rc})")
    d.facts["frozen_counts"] = ran
    d.facts["frozen_not_comparable"] = off[:10]
    wrong = [r for r in ran if not r["match"]]
    d.step("frozen", "finding" if wrong else "ok",
           f"{len(ran)} count(s) re-derived, {len(wrong)} differ; {len(off)} not comparable")


def step_slices(d: dc.Driver, args) -> None:
    if not args.slices:
        d.step("slices", "skipped", "no --slices FILE given")
        return
    sys.path.insert(0, dc.SCRIPTS)
    import build_records

    cl = os.path.join(args.root, ".discovery", "checklists", f"{args.dossier}.md")
    if not os.path.isfile(cl):
        raise dc.Surprise(f"checklist file not found: {cl} (build_records.py checklist write ...)")
    whole = build_records.checklist_members(open(cl, encoding="utf-8").read())
    try:
        slices = json.load(open(args.slices, encoding="utf-8"))
    except (OSError, ValueError) as err:
        raise dc.Surprise(f"cannot read --slices {args.slices}: {err}")
    if not isinstance(slices, dict) or not all(isinstance(v, list) for v in slices.values()):
        raise dc.Surprise("--slices must be a JSON object of lists")
    owners: dict[str, list[str]] = {}
    for surface, members in slices.items():
        for m in members:
            owners.setdefault(m, []).append(surface)
    unowned = [m for m in whole if m not in owners]
    dup = {m: s for m, s in owners.items() if len(s) > 1}
    unknown = [m for m in owners if m not in whole]
    d.facts["slice_counts"] = {"checklist_members": len(whole), "slices": {k: len(v) for k, v in slices.items()}}
    if unowned or dup or unknown:
        d.step("slices", "finding", f"{len(unowned)} unowned, {len(dup)} duplicated, {len(unknown)} unknown")
        if unowned:
            d.finding("checklist members in no slice: " + ", ".join(unowned[:6]))
        if dup:
            d.finding("members in two slices: " + ", ".join(list(dup)[:6]))
        if unknown:
            d.finding("slice members not in the checklist: " + ", ".join(unknown[:6]))
    else:
        d.step("slices", "ok", f"{len(whole)} member(s), each in exactly one of {len(slices)} slice(s)")


def validator_step(d: dc.Driver, name: str, wt_root: str, args, extra: list[str]) -> None:
    rc, out = dc.script("validate_pipeline.py", "--root", wt_root, "--dossier", args.dossier, *extra, cwd=wt_root)
    if rc not in (0, 1):
        raise dc.Surprise(f"validate_pipeline.py {' '.join(extra)} exited {rc}: {' | '.join(dc.cap(out, 3))}")
    defects = [ln for ln in out.splitlines() if ln.startswith("DEFECT")]
    warns = [ln for ln in out.splitlines() if ln.startswith("WARNING")]
    if rc == 1 and not defects:
        raise dc.Surprise(f"validate_pipeline.py {' '.join(extra)} exited 1 with no DEFECT line: {' | '.join(dc.cap(out, 3))}")
    d.facts[f"{name}_warnings"] = warns[:10]
    if defects:
        d.step(name, "finding", f"{len(defects)} DEFECT, {len(warns)} WARNING")
        for ln in defects[:6]:
            d.finding(ln)
    else:
        d.step(name, "ok", f"no DEFECT, {len(warns)} WARNING")


def run(d: dc.Driver, args) -> int:
    if not args.dossier:
        raise dc.Surprise("--dossier is required")
    path = os.path.join(args.root, ".discovery", "dossiers", f"{args.dossier}.md")
    if not os.path.isfile(path):
        raise dc.Surprise(f"dossier file not found: {path}")
    text = open(path, encoding="utf-8").read()
    wt = os.path.abspath(args.worktree) if args.worktree else args.root
    validator_step(d, "validate", args.root, args, [])
    validator_step(d, "receipts", args.root, args, ["--pre-fanout"] + (["--worktree", wt] if args.worktree else []))
    step_criteria(d, text)
    step_census(d, wt, args)
    step_frozen(d, text, wt)
    if args.gates:
        rc, out = dc.script("run_gates.py", args.gates, "--root", wt, cwd=wt)
        if rc == 0:
            d.step("gates", "ok", "text gates green over the materialised contract")
        elif rc == 1:
            d.step("gates", "finding", "a gate failed")
            for ln in dc.cap(out, 6):
                d.finding("gates: " + ln)
        else:
            raise dc.Surprise(f"run_gates.py exited {rc}: {' | '.join(dc.cap(out, 3))}")
    else:
        d.step("gates", "skipped", "no --gates FILE given")
    step_slices(d, args)
    n_bad = sum(1 for s in d.steps if s["status"] == "finding")
    d.summary = f"pre-fan-out check: {n_bad} of {len(d.steps)} step(s) with findings"
    return 1 if d.findings or n_bad else 0


def build_parser(ap) -> None:
    ap.add_argument("--worktree", help="X, the worktree holding the materialised contract (default: the main checkout)")
    ap.add_argument("--member", action="append", default=[], help="a contract member whose exact-output assertions to grep for (repeatable)")
    ap.add_argument("--census-pattern", action="append", default=[], metavar="REGEX", help="a pin pattern to grep for (repeatable)")
    ap.add_argument("--gates", help="gates file: the repository's text gates, run over X")
    ap.add_argument("--slices", help="JSON {surface: [members]} to check against the checklist")


def selftest() -> int:
    import tempfile

    c = dc.Checker("pre_fanout_check")
    text = ("## Work packages\n\n| Package | Owned paths | Depends on |\n|---|---|---|\n| P1 a | src/a.py | — |\n| UT t | tests/test_a.py | — |\n\n"
            "## Acceptance criteria\n\n1. Flush works. (owner: tests/test_a.py; env: local)\n"
            "2. Another one, on two\n   lines. (owner: tests/test_zzz.py; env: ci)\n3. No annotation here.\n\n## Build log\n")
    items = criteria_items(text)
    c("criteria parse with continuation lines", [n for n, _ in items] == [1, 2, 3] and "two lines" in items[1][1])
    c("owner regex", OWNER_RE.search(items[0][1]).group(1) == "tests/test_a.py")
    c("frozen regex finds a count with its command", FROZEN_RE.search("12 tables (grep -c 'CREATE TABLE' m.sql) here").group(1) == "12")
    c("recipes name the member", any("Flush.add" in r.replace("\\", "") for r in recipes("Flush.add")))
    with tempfile.TemporaryDirectory() as tmp:
        main = os.path.join(tmp, "main")
        dc.fixture_repo(main)
        dc.fixture_commit(main, "src/a.py", "def add():\n    pass\n", "init")
        dc.fixture_commit(main, "tests/old.py", "assert_eq!(add(), 1)\n", "old test")
        dc.fixture_commit(main, "migrations/1.sql", "CREATE TABLE a;\nCREATE TABLE b;\n", "migs")
        dc.fixture_write(main, ".discovery/dossiers/W-1.md",
                         "---\nid: W-1\nstatus: building\n---\n\n" + text.replace("## Build log\n", "")
                         + "Frozen: 5 tables (grep -c 'CREATE TABLE' migrations/1.sql).\n\n## Build log\n")
        dc.fixture_write(main, ".discovery/checklists/W-1.md", "- add — return: gives 1\n- sub — return: gives 2\n")
        sl = os.path.join(tmp, "slices.json")
        json.dump({"u1": ["add"], "u2": ["add", "ghost"]}, open(sl, "w"))
        env = {"ASD_DRIVER_PRE_FANOUT_CHECK": "on"}
        args = ["--dossier", "W-1", "--root", main, "--worktree", main, "--member", "add", "--slices", sl]
        rc, out = dc.run_self("pre_fanout_check.py", *args, env={})
        c("off refuses", rc == 3)
        rc, out = dc.run_self("pre_fanout_check.py", *args, env=env)
        c("findings exit 1", rc == 1 and "verdict=findings" in out)
        ev = os.path.join(main, ".discovery", "driver-evidence", "W-1")
        data = json.load(open(os.path.join(ev, sorted(os.listdir(ev))[0])))
        found = " | ".join(data["findings"])
        c("unknown owner path is flagged", "test_zzz.py" in found)
        c("missing annotation is flagged", "criterion 3 has no" in found)
        c("census hit on the old exact-output test", any("tests/old.py" in h for h in data["facts"].get("census_hits", {}).get("add", [])))
        c("frozen count mismatch is flagged (5 vs 2)", any("gives 2" in f for f in data["findings"]))
        c("slice problems flagged", "sub" in found and "ghost" in found and "two slices" in found)
        c("receipts step ran and found the missing receipts", any(s["step"] == "receipts" and s["status"] == "finding" for s in data["steps"]))
        rc, out = dc.run_self("pre_fanout_check.py", "--dossier", "W-9", "--root", main, env=env)
        c("a missing dossier is a surprise", rc == 2 and "not found" in out)
    return c.report()


if __name__ == "__main__":
    sys.exit(dc.drive("pre_fanout_check", None, build_parser, run, selftest))
