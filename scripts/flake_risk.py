#!/usr/bin/env python3
"""List the lines in test files that make a test likely to flake under load.

A blind test author never runs its tests on a loaded machine, and the recorded
flaky tests share a few shapes: a sleep used to wait for something, a
wall-clock bound under one second, a read of the real clock, threads or tasks
whose interleaving the assertion depends on, state shared between tests, a
fixed port or path, and unseeded randomness. This script finds those shapes by
pattern, so Phase 6 can log them and Phase 7 can decide whether the `tests`
lens runs and which lines it reads. A marker is a reason to look, never a
verdict: the lens judges whether the line is a race.

# Usage

    flake_risk.py [--max N=40] FILE ...
    flake_risk.py --selftest

Prints one line per marker, `path:line  <kind>  <the line, cut at 120
characters>`, at most `--max` of them, then one summary line:

    FLAKE-RISK: <n> markers in <k> of <m> files (sleep 2, wall-clock 1)
    FLAKE-RISK: none in <m> files

Kinds: `sleep`, `wall-clock` (a sub-second duration on a line that asserts,
compares, or names a timeout or deadline), `clock` (a read
of the wall clock; a monotonic read is the deadline of a poll loop and is not
flagged), `concurrency` (a thread,
task or process started by the test), `shared-state` (a static, a global or an
environment variable written), `fixed-resource` (a fixed port or `/tmp`
path), `random` (an unseeded generator). Comment lines are skipped.

Exit codes: 0 no marker; 1 markers found; 2 unusable input (no file, or a file
that cannot be read).

No third-party imports: this runs wherever python3 does.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import os
import re
import sys
import tempfile

ONE_SECOND_MS = 1000
LINE_CHARS = 120
COMMENT = re.compile(r"^\s*(//|#(?!\[)|--|\*|/\*)")

# Each kind: plain patterns, matched anywhere on a non-comment line.
PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("sleep", re.compile(
        r"\bthread::sleep\b|\btime::sleep\b|\btime\.sleep\(|\basyncio\.sleep\(|"
        r"\bThread\.Sleep\(|\bTask\.Delay\(|\bsetTimeout\(|^\s*sleep\s+[\d.]")),
    ("wall-clock", re.compile(r"\btimeout\s*=\s*0?\.\d")),
    # Wall-clock time used as data. A monotonic read (`Instant::now()`,
    # `time.monotonic()`) is not flagged: in a test it is almost always the
    # deadline of a poll loop, which is the stable form.
    ("clock", re.compile(
        r"\bSystemTime::now\(\)|\b(Utc|Local)::now\(\)|\btime\.time\(\)|"
        r"\bdatetime\.(utc)?now\(|\bDateTime(Offset)?\.(Utc)?Now\b|\bDate\.now\(\)|"
        r"\bnew Date\(\)")),
    ("concurrency", re.compile(
        r"\bthread::spawn\b|\btokio::spawn\b|\bspawn_blocking\b|\bJoinSet\b|"
        r"\bjoin_all\b|\bthreading\.Thread\(|\b(Thread|Process)PoolExecutor\b|"
        r"\basyncio\.(gather|create_task)\(|\bTask\.Run\(|\bParallel\.|"
        r"\bPromise\.all\(|\bnew Thread\(|\bfork\(\)")),
    ("shared-state", re.compile(
        r"\bstatic mut\b|\blazy_static!|\bOnce(Lock|Cell)\b|\bthread_local!|"
        r"\benv::(set|remove)_var\(|\bos\.environ\[[^\]]+\]\s*=(?!=)|"
        r"\bos\.environ\.(setdefault|update)\(|\bsetenv\(|"
        r"\bSetEnvironmentVariable\(|\bprocess\.env\.\w+\s*=(?!=)|^\s*global\s+\w")),
    ("fixed-resource", re.compile(
        r"\b(127\.0\.0\.1|localhost|0\.0\.0\.0):\d{2,5}\b|[\"']/tmp/[\w./-]+[\"']")),
    ("random", re.compile(
        r"\bthread_rng\(\)|\brand::random\b|\brandom\.(random|randint|choice|shuffle|sample)\(|"
        r"\bMath\.random\(\)|\bnew Random\(\)")),
]
# Sub-second duration literals flag only where they bound something: on a
# line that asserts, compares, or names a timeout or deadline. Elsewhere a
# short duration is a poll interval or a configured delay, which is stable.
MILLIS = re.compile(r"\b(?:from_millis|FromMilliseconds)\(\s*([\d_]+)")
SUB_MILLI = re.compile(r"\bfrom_(?:micros|nanos)\(|\bfrom_secs_f(?:32|64)\(\s*0?\.\d")
BOUND_CONTEXT = re.compile(r"assert|elapsed|timeout|deadline|within|[<>]=?\s*(?:\w+::)*(?:Duration|from_)")


def markers(text: str) -> list[tuple[int, str, str]]:
    """Every (line number, kind, line) on a non-comment line, in line order."""
    found: list[tuple[int, str, str]] = []
    for number, line in enumerate(text.splitlines(), start=1):
        if COMMENT.match(line):
            continue
        kinds = [kind for kind, pattern in PATTERNS if pattern.search(line)]
        if "wall-clock" not in kinds and BOUND_CONTEXT.search(line):
            short = SUB_MILLI.search(line) or any(
                int(value.replace("_", "")) < ONE_SECOND_MS for value in MILLIS.findall(line))
            if short:
                kinds.append("wall-clock")
        for kind in kinds:
            found.append((number, kind, line.strip()))
    return found


def run(paths: list[str], cap: int) -> int:
    if not paths:
        print("flake_risk: no file given", file=sys.stderr)
        return 2
    rows: list[str] = []
    counts: dict[str, int] = {}
    hit_files = 0
    for path in paths:
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                text = fh.read()
        except OSError as err:
            print(f"flake_risk: cannot read {path}: {err.strerror}", file=sys.stderr)
            return 2
        found = markers(text)
        if found:
            hit_files += 1
        for number, kind, line in found:
            counts[kind] = counts.get(kind, 0) + 1
            cut = line if len(line) <= LINE_CHARS else line[: LINE_CHARS - 1] + "…"
            rows.append(f"{path}:{number}  {kind}  {cut}")
    for row in rows[:cap]:
        print(row)
    if len(rows) > cap:
        print(f"… {len(rows) - cap} more")
    if not rows:
        print(f"FLAKE-RISK: none in {len(paths)} files")
        return 0
    kinds = ", ".join(f"{kind} {n}" for kind, n in sorted(counts.items()))
    print(f"FLAKE-RISK: {len(rows)} markers in {hit_files} of {len(paths)} files ({kinds})")
    return 1


def selftest() -> int:
    failures: list[str] = []

    def kinds_of(text: str) -> list[str]:
        return [kind for _, kind, _ in markers(text)]

    cases = [
        ("rust sleep", "    std::thread::sleep(Duration::from_secs(2));", ["sleep"]),
        ("sub-second millis", "    assert!(t < Duration::from_millis(50));", ["wall-clock"]),
        ("millis with underscore", "let d = Duration::from_millis(1_500);", []),
        ("one second is the floor", "assert!(e < Duration::from_millis(1000));", []),
        ("poll interval is not a bound", "const POLL: Duration = Duration::from_millis(50);", []),
        ("poll sleep is a sleep, not a bound", "tokio::time::sleep(Duration::from_millis(20)).await;", ["sleep"]),
        ("sub-second timeout argument", "let r = recv_timeout(Duration::from_millis(200));", ["wall-clock"]),
        ("micros in a comparison", "assert!(d < Duration::from_micros(5));", ["wall-clock"]),
        ("elapsed against a sub-second bound", "assert!(t.elapsed() < Duration::from_millis(300));", ["wall-clock"]),
        ("elapsed against the floor", "assert!(t.elapsed() < Duration::from_secs(1));", []),
        ("monotonic deadline", "let deadline = Instant::now() + Duration::from_secs(45);", []),
        ("wall clock as data", "let stamp = SystemTime::now();", ["clock"]),
        ("tokio spawn", "let h = tokio::spawn(async move { work().await });", ["concurrency"]),
        ("env var written", "std::env::set_var(\"KEY\", \"v\");", ["shared-state"]),
        ("env var compared is not a write", "if os.environ[\"K\"] == \"v\":", []),
        ("fixed port", "let addr = \"127.0.0.1:8080\";", ["fixed-resource"]),
        ("unseeded random", "let mut rng = rand::thread_rng();", ["random"]),
        ("seeded random", "let mut rng = StdRng::seed_from_u64(7);", []),
        ("python sleep", "    time.sleep(0.1)", ["sleep"]),
        ("python timeout", "    q.get(timeout=0.2)", ["wall-clock"]),
        ("rust comment", "    // thread::sleep is never used here", []),
        ("python comment", "    # time.sleep(1)", []),
        ("rust attribute is code", "#[tokio::test] async fn t() { tokio::spawn(x()); }", ["concurrency"]),
        ("csharp now", "var t = DateTime.UtcNow;", ["clock"]),
        ("clean line", "assert_eq!(queue.flush(), 3);", []),
    ]
    for name, text, want in cases:
        got = kinds_of(text)
        if got != want:
            failures.append(f"{name}: want {want}, got {got}")

    with tempfile.TemporaryDirectory() as tmp:
        clean = os.path.join(tmp, "clean_test.rs")
        risky = os.path.join(tmp, "risky_test.py")
        with open(clean, "w", encoding="utf-8") as fh:
            fh.write("fn t() {\n    assert_eq!(1 + 1, 2);\n}\n")
        with open(risky, "w", encoding="utf-8") as fh:
            fh.write("import time\n\ndef test_x():\n    time.sleep(0.5)\n    assert done()\n")
        def quiet(paths: list[str]) -> tuple[int, str]:
            out = io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
                rc = run(paths, 40)
            return rc, out.getvalue()

        rc, _ = quiet([clean])
        if rc != 0:
            failures.append("a clean file exits 0")
        rc, out = quiet([clean, risky])
        if rc != 1 or f"{risky}:4  sleep" not in out or "1 markers in 1 of 2 files (sleep 1)" not in out:
            failures.append(f"a marker exits 1 with its location and summary: {out!r}")
        if quiet([os.path.join(tmp, "missing.rs")])[0] != 2:
            failures.append("an unreadable file exits 2")
        if quiet([])[0] != 2:
            failures.append("no file exits 2")

    for failure in failures:
        print("FAIL", failure)
    print(f"selftest: {len(failures)} failure(s)")
    return 1 if failures else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("paths", nargs="*", metavar="FILE")
    ap.add_argument("--max", type=int, default=40, help="marker lines printed at most")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)
    if args.selftest:
        return selftest()
    return run(args.paths, args.max)


if __name__ == "__main__":
    sys.exit(main())
