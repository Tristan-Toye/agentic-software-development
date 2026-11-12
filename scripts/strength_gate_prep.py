#!/usr/bin/env python3
"""Phase 5's test-strength gate, prepared in one call: the evidence Opus needs
to rule on vacuous tests. The audit and the ruling stay with Opus.

Steps (independent; one failing does not stop the next):

    scope      every path in X's working tree is a named `--test-path` (or the
               pending `.agent-staging/`, reported); uncommitted test paths are
               listed, because the commit goes through gate_commit.py by hand
    coverage   every `PROMISE_CHECKLIST` member (`.discovery/checklists/<ID>.md`,
               the file build_records.py wrote at Phase 3) appears in the unit
               author's `--report`; the ones that do not are listed. A repository's
               own coverage script runs as given with `--coverage-cmd` and its exit
               code and tail are the evidence (read its --help first)
    weak       the weak-pattern flags over the named test files — `is_some(`,
               `is_ok(`, `len() > 0`, a bare `assert!`/`assert`, `toBeTruthy()`
               — as file:line hits. A sampling guide, never a gate
    red-run    the new tests run on the stubs through run_tests.py (`--test-cmd`),
               and every test found in the named files is judged one at a time
               from the run's log (run_tests.outcomes): `failed` is the wanted
               red; `ok` is a vacuous candidate; no result line is unjudged
               (a collection or compile error leaves every test there). A failure
               count is never the judgement

It never routes a vacuous test, never audits the checklist index and never
re-spawns an author: it lists what the audit should read. The failing tests
print with their first assertion line so Opus can ask "fails for the right
reason?".

Usage:
    ASD_DRIVER_STRENGTH_GATE_PREP=on python3 strength_gate_prep.py --dossier W-014 \\
        --worktree X --test-path tests/unit/test_flush.py ... --report unit-report.md \\
        --test-cmd 'cargo test -p flush' [--coverage-cmd 'python3 tools/check_promise_coverage.py ...'] [--log DOSSIER_FILE]

Exit 0 clean (every test red, nothing uncovered, no weak flag), 1 findings, 2 surprise.
Modes and the evidence file: see driver_common.py.
"""

from __future__ import annotations

import os
import re
import shlex
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import driver_common as dc  # noqa: E402
import merge_packages as mp  # noqa: E402

WEAK = [
    ("is_some", re.compile(r"\bis_some\(")),
    ("is_ok", re.compile(r"\bis_ok\(")),
    ("len>0", re.compile(r"len\(\)?\s*>\s*0|\.length\s*>\s*0|len\([^)]*\)\s*>\s*0")),
    ("bare-assert", re.compile(r"^\s*assert!\([^=<>!]*\)\s*;?\s*$|^\s*assert\s+[\w.\[\]()]+\s*$")),
    ("truthy", re.compile(r"toBeTruthy\(\)|toBeDefined\(\)|\.Should\(\)\.NotBeNull\(\)")),
]
TEST_ATTR = re.compile(r"^\s*(#\[(?:\w+::)?test\b|\[(Fact|Theory|Test|TestMethod)\b)")


def test_files(wt: str, paths: list[str]) -> list[str]:
    out = []
    for p in paths:
        full = os.path.join(wt, p)
        if os.path.isdir(full):
            for base, _, names in os.walk(full):
                out += [os.path.relpath(os.path.join(base, n), wt) for n in sorted(names)]
        elif os.path.isfile(full):
            out.append(p)
    return out


def test_names(text: str) -> list[str]:
    """Test names by the common shapes: python `def test_x`, rust/C# attribute then
    the next fn/method, JS it('x')/test('x'). Names a log can be searched for."""
    names: list[str] = []
    lines = text.splitlines()
    for i, line in enumerate(lines):
        m = re.match(r"\s*(?:async\s+)?def (test\w*)\(", line)
        if m:
            names.append(m.group(1))
        elif TEST_ATTR.match(line):
            for nxt in lines[i + 1:i + 6]:
                n = re.search(r"\b(?:fn|void|Task)\s+(\w+)\s*\(|\bpublic\s+[\w<>\[\]]+\s+(\w+)\s*\(", nxt)
                if n:
                    names.append(n.group(1) or n.group(2))
                    break
        else:
            j = re.match(r"\s*(?:it|test)\(\s*['\"](.+?)['\"]", line)
            if j:
                names.append(j.group(1))
    return list(dict.fromkeys(names))


def weak_hits(wt: str, files: list[str]) -> list[str]:
    hits = []
    for f in files:
        try:
            lines = open(os.path.join(wt, f), encoding="utf-8", errors="replace").read().splitlines()
        except OSError:
            continue
        for n, line in enumerate(lines, 1):
            for label, rx in WEAK:
                if rx.search(line):
                    hits.append(f"{f}:{n} [{label}] {line.strip()[:100]}")
    return hits


def step_scope(d: dc.Driver, wt: str, test_paths: list[str]) -> None:
    st = dc.worktree_state(wt)
    if st["operation"]:
        raise dc.Surprise(f"{wt} is mid-operation ({', '.join(st['operation'])})")
    changed = [ln[3:].strip().split(" -> ")[-1] for ln in st["tracked_changes"] + st["untracked"]]
    staging = [p for p in changed if p.startswith(".agent-staging")]
    outside = [p for p in changed if p not in staging and not mp.inside(p, test_paths)]
    uncommitted_tests = [p for p in changed if p not in staging and mp.inside(p, test_paths)]
    d.facts["uncommitted_test_paths"] = uncommitted_tests[:30]
    d.facts["outside_test_paths"] = outside[:30]
    problems = []
    if outside:
        problems.append(f"{len(outside)} path(s) outside the named TEST_PATHS: {', '.join(outside[:5])}")
    if staging:
        problems.append(".agent-staging/ still present (delete before the commit)")
    if uncommitted_tests:
        problems.append(f"{len(uncommitted_tests)} test path(s) not committed yet (gate_commit.py)")
    d.step("scope", "finding" if problems else "ok", "; ".join(problems) or "working tree holds only committed work")
    for pr in problems:
        d.finding("scope: " + pr)


def step_coverage(d: dc.Driver, args, wt: str) -> None:
    sys.path.insert(0, dc.SCRIPTS)
    import build_records

    cl = os.path.join(args.root, ".discovery", "checklists", f"{args.dossier}.md")
    if args.report:
        if not os.path.isfile(cl):
            raise dc.Surprise(f"checklist file not found: {cl}")
        if not os.path.isfile(args.report):
            raise dc.Surprise(f"--report file not found: {args.report}")
        members = build_records.checklist_members(open(cl, encoding="utf-8").read())
        report = open(args.report, encoding="utf-8", errors="replace").read()
        missing = [m for m in members if m not in report]
        d.facts["checklist_members"] = len(members)
        d.facts["uncovered_in_report"] = missing
        if missing:
            d.step("coverage", "finding", f"{len(missing)} of {len(members)} checklist member(s) absent from the report")
            d.finding("uncovered checklist members: " + ", ".join(missing[:8]))
        else:
            d.step("coverage", "ok", f"all {len(members)} members named in the report (the report's own claim; the audit reads the tests)")
    else:
        d.step("coverage", "skipped", "no --report given")
    if args.coverage_cmd:
        rc, out = dc.sh(shlex.split(args.coverage_cmd), cwd=wt, timeout=600)
        d.facts["coverage_cmd"] = {"exit": rc, "tail": dc.cap(out, 6)}
        d.step("coverage-cmd", "ok" if rc == 0 else "finding", f"exit {rc}: " + (dc.cap(out, 1) or [""])[-1])
        if rc != 0:
            d.finding(f"the repository's coverage script exited {rc} (read its output, not just the exit)")


def step_red_run(d: dc.Driver, args, wt: str, files: list[str]) -> None:
    if not args.test_cmd:
        d.step("red-run", "skipped", "no --test-cmd given")
        return
    sys.path.insert(0, dc.SCRIPTS)
    import run_tests

    names: dict[str, list[str]] = {}
    for f in files:
        for n in test_names(open(os.path.join(wt, f), encoding="utf-8", errors="replace").read()):
            names.setdefault(n, []).append(f)
    logp = os.path.join(tempfile.mkdtemp(prefix="asd-redrun-"), "red-run.log")
    rc, digest = dc.script("run_tests.py", "--cwd", wt, "--log", logp, "--", *shlex.split(args.test_cmd), cwd=wt, timeout=3600)
    if not os.path.isfile(logp):
        raise dc.Surprise(f"run_tests.py left no log at {logp} (exit {rc}): {' | '.join(dc.cap(digest, 3))}")
    text = open(logp, encoding="utf-8", errors="replace").read()
    parser, totals, fails = run_tests.parse_log(text)
    got = run_tests.outcomes(text, list(names)) if names else {}
    red = [n for n, o in got.items() if o == "failed"]
    vacuous = [n for n, o in got.items() if o == "ok"]
    unjudged = [n for n, o in got.items() if o is None]
    first = {f.name: (f.detail[0] if f.detail else "") for f in fails}
    d.facts["red_run"] = {
        "log": logp, "exit": rc, "parser": parser or "none", "tests_found": len(names),
        "red": red[:40], "passed_on_stub": vacuous[:40], "no_result_line": unjudged[:40],
        "failing_first_line": {k: v[:140] for k, v in list(first.items())[:25]},
    }
    d.step("red-run", "ok" if not vacuous and not unjudged and names else "finding",
           f"{len(names)} test(s) found; {len(red)} red, {len(vacuous)} passed on the stub, {len(unjudged)} unjudged; full log {logp}")
    if not names:
        d.finding("no test names found in the named files — judge the run by hand")
    if vacuous:
        d.finding("passed against the stubs (vacuous candidates): " + ", ".join(vacuous[:8]))
    if unjudged:
        why = " (the run printed no per-test lines: a compile or collection error?)" if rc != 0 and not totals.passed and not totals.failed else ""
        d.finding(f"{len(unjudged)} test(s) have no result line{why}: " + ", ".join(unjudged[:6]))


def run(d: dc.Driver, args) -> int:
    if not args.worktree or not args.test_path or not args.dossier:
        raise dc.Surprise("--worktree, --dossier and at least one --test-path are required")
    wt = os.path.abspath(args.worktree)
    files = test_files(wt, args.test_path)
    if not files:
        raise dc.Surprise("none of the --test-path entries exists in the worktree")
    d.facts["test_files"] = files[:40]
    step_scope(d, wt, args.test_path)
    step_coverage(d, args, wt)
    hits = weak_hits(wt, files)
    d.facts["weak_hits"] = hits[:40]
    d.step("weak", "finding" if hits else "ok", f"{len(hits)} flag(s) over {len(files)} file(s) (a reading guide; a clean grep proves nothing)")
    if hits:
        d.finding(f"weak-pattern flags: {len(hits)}; first {hits[0]}")
    step_red_run(d, args, wt, files)
    d.summary = f"strength gate prep: {len(d.findings)} finding(s) over {len(files)} test file(s)"
    return 1 if d.findings else 0


def build_parser(ap) -> None:
    ap.add_argument("--worktree", help="X, the build worktree holding the committed tests on stubs")
    ap.add_argument("--test-path", action="append", default=[], help="a TEST_PATHS entry (file or directory; repeatable)")
    ap.add_argument("--report", help="the unit author's report (names the checklist members it covers)")
    ap.add_argument("--test-cmd", help="the command that runs the new tests only, on the stubs")
    ap.add_argument("--coverage-cmd", help="the repository's own promise-coverage command, run as given")


def selftest() -> int:
    import tempfile as tf

    c = dc.Checker("strength_gate_prep")
    src = ("#[test]\nfn good_one() {\n    assert_eq!(f(), 3);\n}\n#[test]\nfn vacuous_one() {\n    assert!(f().is_some());\n}\n"
           "def test_py():\n    assert x\n")
    c("test names found for rust and python", test_names(src) == ["good_one", "vacuous_one", "test_py"])
    c("js it() names", test_names("it('does a thing', () => {})\n") == ["does a thing"])
    with tf.TemporaryDirectory() as tmp:
        wt = os.path.join(tmp, "x")
        dc.fixture_repo(wt)
        dc.fixture_commit(wt, "src/lib.rs", "x\n", "init")
        dc.fixture_commit(wt, "tests/t.rs", src.split("def test_py")[0], "tests")
        c("weak hits flag is_some", any("is_some" in h for h in weak_hits(wt, ["tests/t.rs"])))
        main = os.path.join(tmp, "main")
        dc.fixture_write(main, ".discovery/checklists/W-1.md", "- Flush.add — return: gives 3\n- Flush.drop — error: refuses\n")
        rep = os.path.join(tmp, "report.md")
        open(rep, "w").write("covered Flush.add via good_one\n")
        log_cmd = ("printf 'running 2 tests\\ntest good_one ... FAILED\\ntest vacuous_one ... ok\\n\\nfailures:\\n\\n---- good_one stdout ----\\n"
                   "thread good_one panicked at tests/t.rs:3:5:\\nassertion left == right\\n\\nfailures:\\n    good_one\\n\\n"
                   "test result: FAILED. 1 passed; 1 failed; 0 ignored; 0 measured; 0 filtered out\\n'; exit 101")
        env = {"ASD_DRIVER_STRENGTH_GATE_PREP": "on"}
        args = ["--dossier", "W-1", "--root", main, "--worktree", wt, "--test-path", "tests/t.rs", "--report", rep, "--test-cmd", f"sh -c \"{log_cmd}\""]
        rc, out = dc.run_self("strength_gate_prep.py", *args, env={})
        c("off refuses", rc == 3)
        rc, out = dc.run_self("strength_gate_prep.py", *args, env=env)
        c("findings exit 1", rc == 1 and "verdict=findings" in out)
        c("vacuous candidate named", "vacuous_one" in out)
        c("uncovered member named", "Flush.drop" in out)
        c("weak flag counted", "weak-pattern flags" in out)
        ev = os.path.join(main, ".git", "asd-drivers", "driver-evidence", "W-1")
        import json
        data = json.load(open(os.path.join(ev, os.listdir(ev)[0])))
        c("good_one judged red with its first line", data["facts"]["red_run"]["red"] == ["good_one"]
          and "good_one" in data["facts"]["red_run"]["failing_first_line"])
        dc.fixture_write(wt, "tests/extra.rs", "x\n")
        rc, out = dc.run_self("strength_gate_prep.py", *args, env=env)
        c("an uncommitted path outside TEST_PATHS is a scope finding", "outside the named TEST_PATHS" in out)
        rc, out = dc.run_self("strength_gate_prep.py", "--dossier", "W-1", "--root", main, "--worktree", wt, "--test-path", "nope/", env=env)
        c("no such test path is a surprise", rc == 2)
    return c.report()


if __name__ == "__main__":
    sys.exit(dc.drive("strength_gate_prep", None, build_parser, run, selftest))
