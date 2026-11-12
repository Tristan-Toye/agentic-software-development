#!/usr/bin/env python3
"""Phase 6, one test round in one call: run the gates and the suite, digest the
result, and classify every failure into EVIDENCE. No rulings.

It does not decide who is wrong. The six-row arbitration table, the
repeat-fingerprint stop, the budget, the corrections and the `ARBITRATION:` lines
all stay with Opus. For each failing test it prints what a ruling reads:

    file        the test file the failure names (from its name or detail)
    package     the work package whose OWNED_PATHS contain that file (or none)
    kind_hint   harness-noise-candidate | assertion | timeout | unknown, from the
                failure text alone; a hint for row 4 (`check_harness_edit.py`
                makes the boundary mechanical) and never a ruling
    first_line  the first detail line of the failure
    repeat_site whether this file failed in an earlier round of this dossier

Steps:
    gates   run_gates.py over X with `--gates FILE` (it reuses a green receipt on
            an unchanged tree); a failed gate prints its excerpt
    suite   run_tests.py with `--run-tests-arg` pass-throughs (`--vm=NAME`,
            `--workdir=DIR`, `--repeat=N` ...) and the suite command after `--`;
            the log is parsed with run_tests.parse_log, never read through grep
    table   failures grouped by package, counts by kind_hint, FLAKY lines when a
            repeat ran, and the sites seen in earlier rounds (kept in
            `.git/asd-drivers/driver-state/<ID>/sites.json`, mode on only)

A suite that exits non-zero with no parseable failure (an unknown output format,
a runner crash) is a surprise, never "green" and never "no failures".

Usage:
    ASD_DRIVER_TEST_ROUND=on python3 test_round.py --dossier W-014 --worktree X --round 1 \\
        [--gates FILE] [--run-tests-arg=--vm=nightwatch ...] [--log DOSSIER_FILE] -- cargo test --workspace

Exit 0 green, 1 failures (evidence printed), 2 surprise. See driver_common.py.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import driver_common as dc  # noqa: E402
import merge_packages as mp  # noqa: E402

HARNESS = re.compile(r"error\[E\d+\]|cannot find|unresolved import|ImportError|ModuleNotFoundError|SyntaxError|NameError|"
                     r"fixture .* not found|collection error|undefined reference|No such file|CS\d{4}|could not compile", re.I)
ASSERTION = re.compile(r"assert|panicked at|expected|left\s*==|Expected|Actual|!=|mismatch", re.I)
TIMEOUT = re.compile(r"timed out|timeout|deadline exceeded", re.I)
FILE_RE = re.compile(r"([\w./-]+\.(?:py|rs|ts|tsx|js|jsx|cs|java|go|rb|sh|bats|hcl|kt))")


def kind_hint(text: str) -> str:
    if HARNESS.search(text):
        return "harness-noise-candidate"
    if TIMEOUT.search(text):
        return "timeout"
    if ASSERTION.search(text):
        return "assertion"
    return "unknown"


def failure_file(name: str, detail: list[str]) -> str:
    for text in [name] + detail:
        m = FILE_RE.search(text)
        if m:
            return m.group(1)
    m = re.match(r"([\w./-]+)::", name)
    return m.group(1) if m else ""


def package_of(path: str, rows: list[dict]) -> str:
    if not path:
        return "unknown"
    for r in rows:
        if mp.inside(path, r["owned"]):
            return r["id"]
    return "none"


def classify(fails, rows: list[dict], prior_sites: list[str]) -> list[dict]:
    out = []
    for f in fails:
        blob = " ".join(f.detail)
        file = failure_file(f.name, f.detail)
        out.append({
            "test": f.name, "file": file, "package": package_of(file, rows), "kind_hint": kind_hint(f.name + " " + blob),
            "first_line": (f.detail[0] if f.detail else "")[:160], "repeat_site": bool(file) and file in prior_sites,
        })
    return out


def run(d: dc.Driver, args) -> int:
    if not args.worktree or not args.dossier:
        raise dc.Surprise("--worktree and --dossier are required")
    cmd = [a for a in args.command if a != "--"] if args.command else []
    if not cmd:
        raise dc.Surprise("the suite command is missing (put it after --)")
    wt = os.path.abspath(args.worktree)
    sys.path.insert(0, dc.SCRIPTS)
    import run_tests

    dossier_file = args.log or os.path.join(args.root, ".discovery", "dossiers", f"{args.dossier}.md")
    rows: list[dict] = []
    if os.path.isfile(dossier_file):
        try:
            rows = mp.parse_package_table(open(dossier_file, encoding="utf-8").read())
        except dc.Surprise as err:
            d.facts["packages"] = f"not read: {err}"
    st = dc.worktree_state(wt)
    d.facts.update({"head": st["head"], "round": args.round})

    if args.gates:
        rc, out = dc.script("run_gates.py", args.gates, "--root", wt, cwd=wt, timeout=3600)
        d.facts["gates_tail"] = dc.cap(out, 6)
        if rc == 0:
            d.step("gates", "ok", "green" + (" (receipt reused)" if "REUSED" in out else ""))
        elif rc == 1:
            d.step("gates", "finding", "a gate failed")
            for ln in dc.cap(out, 8):
                d.finding("gates: " + ln)
        else:
            raise dc.Surprise(f"run_gates.py exited {rc}: {' | '.join(dc.cap(out, 3))}")
    else:
        d.step("gates", "skipped", "no --gates FILE given")

    logp = os.path.join(tempfile.mkdtemp(prefix="asd-round-"), "suite.log")
    extra = [a for a in args.run_tests_arg]
    if args.log and not d.shadow and not any(a.startswith("--repeat") for a in extra):
        extra += ["--build-log", args.log]
    rc, digest = dc.script("run_tests.py", "--cwd", wt, "--log", logp, *extra, "--", *cmd, cwd=wt, timeout=7200)
    logf = logp if os.path.isfile(logp) else (logp + ".run1" if os.path.isfile(logp + ".run1") else "")
    if not logf:
        raise dc.Surprise(f"run_tests.py left no log (exit {rc}): {' | '.join(dc.cap(digest, 3))}")
    text = open(logf, encoding="utf-8", errors="replace").read()
    parser, totals, fails = run_tests.parse_log(text)
    d.facts["suite"] = {"exit": rc, "parser": parser or "none", "passed": totals.passed, "failed": totals.failed, "ignored": totals.ignored, "log": logf}
    flaky = re.findall(r"^(FLAKY .+|FAILS EVERY RUN .+)$", digest, re.M)
    d.facts["flaky_lines"] = flaky[:10]
    if rc != 0 and not fails and not flaky:
        raise dc.Surprise(
            f"the suite exited {rc} but no failure parsed (parser: {parser or 'none'}); unknown output format or a runner crash. "
            f"Read the log: {logf}")
    if rc == 0 and not fails:
        d.step("suite", "ok", f"green: {totals.passed} passed, {totals.ignored} ignored; log {logf}")
    else:
        d.step("suite", "finding", f"exit {rc}: {totals.passed} passed, {totals.failed} failed ({len(fails)} parsed); log {logf}")

    sites_path = args.sites_file or os.path.join(dc.evidence_root(args.root), "driver-state", args.dossier, "sites.json")
    prior: list[str] = []
    prior_rounds: dict = {}
    if os.path.isfile(sites_path):
        try:
            prior_rounds = json.load(open(sites_path, encoding="utf-8"))
            prior = sorted({s for r, sites in prior_rounds.items() if r != str(args.round) for s in sites})
        except (OSError, ValueError):
            raise dc.Surprise(f"cannot read the sites file {sites_path}")
    table = classify(fails, rows, prior)
    d.facts["failures"] = table[:60]
    by_pkg: dict[str, int] = {}
    by_kind: dict[str, int] = {}
    for t in table:
        by_pkg[t["package"]] = by_pkg.get(t["package"], 0) + 1
        by_kind[t["kind_hint"]] = by_kind.get(t["kind_hint"], 0) + 1
    d.facts["by_package"] = by_pkg
    d.facts["by_kind_hint"] = by_kind
    if table:
        d.step("table", "finding", f"{len(table)} failure(s); by package {by_pkg}; by hint {by_kind}")
        if len(table) > 3:
            d.finding(f"{len(table)} failures: more than three, so the case-file section of phase-6-conditional applies (yours to read)")
        repeats = sorted({t["file"] for t in table if t["repeat_site"]})
        if repeats:
            d.finding("REPEAT SITE (failed in an earlier round): " + ", ".join(repeats[:5]) + " — the repeat-fingerprint stop is yours to apply")
        for t in table[:6]:
            d.finding(f"{t['package']} [{t['kind_hint']}] {t['test'][:70]} @ {t['file'] or '?'}: {t['first_line'][:70]}")
        if len(table) > 6:
            d.finding(f"(+{len(table) - 6} more failures in the evidence file)")
    if flaky:
        d.finding("repeat run: " + "; ".join(flaky[:3]) + " — row 6: find the mechanism, never rerun until green")
    if not d.shadow:
        prior_rounds[str(args.round)] = sorted({t["file"] for t in table if t["file"]})
        os.makedirs(os.path.dirname(sites_path), exist_ok=True)
        with open(sites_path, "w", encoding="utf-8") as fh:
            json.dump(prior_rounds, fh, indent=1, sort_keys=True)
    d.summary = f"round {args.round} on {st['head'][:9]}: " + (
        f"green, {totals.passed} passed" if not fails and rc == 0 and not d.findings else f"{len(fails)} failure(s), {len(d.findings)} finding(s)")
    return 1 if (fails or flaky or d.findings) else 0


def build_parser(ap) -> None:
    ap.add_argument("--worktree", help="X, the build worktree")
    ap.add_argument("--round", type=int, default=1, help="the arbitration round number (keys the repeat-site memory)")
    ap.add_argument("--gates", help="gates file for run_gates.py")
    ap.add_argument("--run-tests-arg", action="append", default=[], metavar="ARG", help="a run_tests.py flag, with = for values: --run-tests-arg=--vm=nightwatch (repeatable)")
    ap.add_argument("--sites-file", help="the repeat-site memory (default .git/asd-drivers/driver-state/<ID>/sites.json)")
    ap.add_argument("command", nargs="*", help="the suite command, after --")


def selftest() -> int:
    import tempfile as tf

    c = dc.Checker("test_round")
    c("kind hint: compile error is a harness candidate", kind_hint("error[E0432]: unresolved import") == "harness-noise-candidate")
    c("kind hint: assertion", kind_hint("thread panicked at src/x.rs:3: assertion left == right") == "assertion")
    c("kind hint: unknown", kind_hint("weird") == "unknown")
    c("file from detail", failure_file("t", ["thread t panicked at tests/t.rs:3:5:"]) == "tests/t.rs")
    c("package of a path", package_of("tests/t.rs", [{"id": "UT", "owned": ["tests/"]}]) == "UT" and package_of("zz.rs", [{"id": "UT", "owned": ["tests/"]}]) == "none")
    with tf.TemporaryDirectory() as tmp:
        wt = os.path.join(tmp, "x")
        dc.fixture_repo(wt)
        dc.fixture_commit(wt, "a", "1\n", "init")
        main = os.path.join(tmp, "main")
        dc.fixture_write(main, ".discovery/dossiers/W-1.md",
                         "---\nid: W-1\n---\n## Work packages\n\n| Package | Owned paths | Depends on |\n|---|---|---|\n| UT t | tests/ | — |\n| P1 a | src/ | — |\n\n## Build log\n")
        red = ("printf 'running 2 tests\\ntest a ... FAILED\\ntest b ... ok\\n\\nfailures:\\n\\n---- a stdout ----\\n"
               "thread a panicked at tests/t.rs:3:5:\\nassertion left == right\\n\\nfailures:\\n    a\\n\\n"
               "test result: FAILED. 1 passed; 1 failed; 0 ignored; 0 measured; 0 filtered out\\n'; exit 101")
        green = "printf 'test result: ok. 3 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out\\n'"
        crash = "printf 'Segmentation noise\\n'; exit 139"
        env = {"ASD_DRIVER_TEST_ROUND": "on"}
        base = ["--dossier", "W-1", "--root", main, "--worktree", wt]
        rc, out = dc.run_self("test_round.py", *base, "--", "sh", "-c", red, env={})
        c("off refuses", rc == 3)
        rc, out = dc.run_self("test_round.py", *base, "--round", "1", "--", "sh", "-c", red, env=env)
        c("red suite exits 1 with the failure classified", rc == 1 and "UT [assertion]" in out and "tests/t.rs" in out)
        sites = os.path.join(main, ".git", "asd-drivers", "driver-state", "W-1", "sites.json")
        c("the sites file records round 1", os.path.isfile(sites) and json.load(open(sites)) == {"1": ["tests/t.rs"]})
        rc, out = dc.run_self("test_round.py", *base, "--round", "2", "--", "sh", "-c", red, env=env)
        c("the same file in round 2 is a REPEAT SITE", "REPEAT SITE" in out)
        rc, out = dc.run_self("test_round.py", *base, "--round", "3", "--", "sh", "-c", green, env=env)
        c("green suite exits 0", rc == 0 and "verdict=clean" in out)
        rc, out = dc.run_self("test_round.py", *base, "--round", "4", "--", "sh", "-c", crash, env=env)
        c("an unknown crash output is a surprise, not green", rc == 2 and "no failure parsed" in out)
        shadow_main = os.path.join(tmp, "main2")
        dc.fixture_write(shadow_main, ".discovery/dossiers/W-1.md", open(os.path.join(main, ".discovery/dossiers/W-1.md")).read())
        rc, out = dc.run_self("test_round.py", "--dossier", "W-1", "--root", shadow_main, "--worktree", wt, "--round", "1", "--", "sh", "-c", red,
                              env={"ASD_DRIVER_TEST_ROUND": "shadow"})
        c("shadow runs but keeps no sites memory", rc == 1 and not os.path.exists(os.path.join(shadow_main, ".git", "asd-drivers", "driver-state")))
    return c.report()


if __name__ == "__main__":
    sys.exit(dc.drive("test_round", None, build_parser, run, selftest))
