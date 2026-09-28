#!/usr/bin/env python3
"""Run a fixed list of repository gates in one call and print only a verdict.

An orchestrator on an expensive model re-bills its whole context on every
step. Ten recorded runs spent roughly 4% of their credits running about
fifteen repository gate scripts one per step — one model turn per lint, per
test suite, per format check — each turn paying for the whole conversation
again just to relay a PASS or a wall of scrolling test output. This script
runs every gate in one call from a plain-text list, writes each gate's full
output to its own log file (never to stdout), and prints one line per gate
plus, for a failure, a short excerpt of the lines that actually look like the
failure — not the other 4,995 lines around it.

# The gates file

One gate per line, `name: command`; `command` runs with `bash -c` in --root.
`#` starts a whole-line comment; blank lines are ignored. A line may end with
` [needs: <tool>]` — the gate is reported SKIP, never run, when <tool> is not
found on PATH. Gate names must be unique and match `[A-Za-z0-9_.-]+`.

    lint: ruff check .
    unit: pytest -q tests/unit
    e2e: npm run test:e2e [needs: npx]
    # a comment
    fmt: cargo fmt --check

# Usage

    run_gates.py GATES_FILE [--root DIR] [--only NAME[,NAME]] [--list]
                 [--log-dir DIR] [--jobs N=1] [--lines N=15]
                 [--line-chars N=300] [--timeout SECONDS]
    run_gates.py --selftest

`--list` prints the parsed gates (name, command, needs) and runs nothing.
`--only` restricts the run to the named gates (comma list); an unknown name
is refused. `--jobs N` runs gates concurrently in a thread pool (default 1,
sequential); output is still printed in the gates file's order regardless of
finishing order, so a rerun's transcript reads the same way twice.
`--log-dir` picks where full per-gate logs land (default: a fresh temp
directory, whose path is printed once). `--lines` and `--line-chars` bound
the failure excerpt: up to N lines that match
`DEFECT|FAIL|ERROR|error|Error|panicked|not ok|✗`, or the last N lines when
none match, each cut to `--line-chars` characters. `--timeout` caps each
gate's wall time; a gate that times out is reported FAIL.

Exit codes: 0 all gates passed (skips allowed); 1 at least one gate failed;
2 unusable input (bad gates file, duplicate or malformed gate name, unknown
`--only` name).

No third-party imports: this runs wherever python3 and bash do.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import contextlib
import io
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

NAME_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
NEEDS_RE = re.compile(r"^(?P<rest>.*)\[needs:\s*(?P<tool>[^\]]+)\]\s*$")
FAILURE_RE = re.compile(r"DEFECT|FAIL|ERROR|error|Error|panicked|not ok|✗")


class GateFileError(Exception):
    pass


def parse_gates(text: str) -> list[dict]:
    gates: list[dict] = []
    seen: set[str] = set()
    for lineno, raw in enumerate(text.splitlines(), 1):
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        needs = None
        m = NEEDS_RE.match(stripped)
        if m:
            stripped = m.group("rest").strip()
            needs = m.group("tool").strip()
        name, sep, command = stripped.partition(":")
        name = name.strip()
        command = command.strip()
        if not sep or not name or not command or not NAME_RE.match(name):
            raise GateFileError(f"line {lineno}: malformed gate line: {raw!r}")
        if name in seen:
            raise GateFileError(f"line {lineno}: duplicate gate name {name!r}")
        seen.add(name)
        gates.append({"name": name, "command": command, "needs": needs, "line": lineno})
    if not gates:
        raise GateFileError("the gates file names no gate")
    return gates


def run_gate(command: str, root: str, timeout: float | None) -> dict:
    start = time.perf_counter()
    try:
        proc = subprocess.run(
            ["bash", "-c", command], cwd=root, capture_output=True, text=True,
            timeout=timeout, check=False,
        )
        return {"rc": proc.returncode, "output": proc.stdout + proc.stderr,
                "elapsed": time.perf_counter() - start, "timed_out": False}
    except subprocess.TimeoutExpired as err:
        out = (err.stdout or "") + (err.stderr or "")
        return {"rc": None, "output": out, "elapsed": time.perf_counter() - start, "timed_out": True}


def excerpt(output: str, n: int, chars: int) -> list[str]:
    lines = output.splitlines()
    matched = [l for l in lines if FAILURE_RE.search(l)]
    chosen = matched[:n] if matched else lines[-n:]
    return [l[:chars] for l in chosen]


def write_log(log_dir: str, name: str, output: str) -> str:
    path = os.path.join(log_dir, f"{name}.log")
    with open(path, "w", encoding="utf-8", errors="replace") as fh:
        fh.write(output)
    return path


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("gates_file", nargs="?")
    ap.add_argument("--root", default=".", help="directory each gate's command runs in")
    ap.add_argument("--only", metavar="NAME[,NAME]", help="run only these gates")
    ap.add_argument("--list", action="store_true", help="print the parsed gates and run nothing")
    ap.add_argument("--log-dir", help="where full per-gate logs go (default: a fresh temp dir)")
    ap.add_argument("--jobs", type=int, default=1, metavar="N", help="concurrent gates (default 1)")
    ap.add_argument("--lines", type=int, default=15, metavar="N", help="failure excerpt line cap (default 15)")
    ap.add_argument("--line-chars", type=int, default=300, metavar="N", help="failure excerpt char cap per line (default 300)")
    ap.add_argument("--timeout", type=float, default=None, metavar="SECONDS", help="per-gate wall-time cap")
    ap.add_argument("--selftest", action="store_true", help="check the checker")
    args = ap.parse_args(argv)
    if args.selftest:
        return selftest()
    if not args.gates_file:
        ap.error("GATES_FILE is required unless --selftest")

    try:
        with open(args.gates_file, encoding="utf-8") as fh:
            gates = parse_gates(fh.read())
    except (OSError, GateFileError) as err:
        print(f"run_gates: {err}", file=sys.stderr)
        return 2

    if args.only:
        wanted = [x.strip() for x in args.only.split(",") if x.strip()]
        known = {g["name"] for g in gates}
        unknown = [w for w in wanted if w not in known]
        if unknown:
            print(f"run_gates: unknown gate name(s): {', '.join(unknown)}", file=sys.stderr)
            return 2
        gates = [g for g in gates if g["name"] in wanted]

    if args.list:
        for g in gates:
            needs = f"  [needs: {g['needs']}]" if g["needs"] else ""
            print(f"{g['name']}: {g['command']}{needs}")
        return 0

    log_dir = args.log_dir or tempfile.mkdtemp(prefix="run_gates-")
    os.makedirs(log_dir, exist_ok=True)
    print(f"run_gates: logging to {log_dir}")
    root = os.path.abspath(args.root)

    to_run = []
    results: list[tuple[str, dict, dict | None]] = [None] * len(gates)  # type: ignore[list-item]
    for i, g in enumerate(gates):
        if g["needs"] and shutil.which(g["needs"]) is None:
            results[i] = ("skip", g, None)
        else:
            to_run.append(i)

    def work(i: int) -> tuple[int, dict]:
        return i, run_gate(gates[i]["command"], root, args.timeout)

    if args.jobs > 1 and len(to_run) > 1:
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as ex:
            for i, res in ex.map(work, to_run):
                results[i] = ("ran", gates[i], res)
    else:
        for i in to_run:
            i, res = work(i)
            results[i] = ("ran", gates[i], res)

    passed = failed = skipped = 0
    for kind, g, res in results:
        if kind == "skip":
            skipped += 1
            print(f"SKIP {g['name']} — needs {g['needs']!r}, not on PATH")
            continue
        assert res is not None
        path = write_log(log_dir, g["name"], res["output"])
        if res["timed_out"]:
            failed += 1
            print(f"FAIL {g['name']} (timeout after {args.timeout:.1f}s, {res['elapsed']:.1f}s) — log {path}")
            for line in excerpt(res["output"], args.lines, args.line_chars):
                print(f"    {line}")
        elif res["rc"] == 0:
            passed += 1
            print(f"PASS {g['name']} ({res['elapsed']:.1f}s)")
        else:
            failed += 1
            print(f"FAIL {g['name']} (exit {res['rc']}, {res['elapsed']:.1f}s) — log {path}")
            for line in excerpt(res["output"], args.lines, args.line_chars):
                print(f"    {line}")

    print(f"run_gates: {passed} passed, {failed} failed, {skipped} skipped")
    return 1 if failed else 0


def selftest() -> int:
    failures: list[str] = []

    def check(name: str, ok: bool) -> None:
        if not ok:
            failures.append(name)

    with tempfile.TemporaryDirectory() as tmp:
        long_line = "x" * 70_000
        fail_script = os.path.join(tmp, "fail.py")
        with open(fail_script, "w", encoding="utf-8") as fh:
            fh.write(
                "import sys\n"
                "for i in range(4998):\n"
                "    print('line', i)\n"
                "print('DEFECT: something broke')\n"
                f"print('{long_line}')\n"
                "sys.exit(1)\n"
            )
        gates_path = os.path.join(tmp, "gates.txt")
        with open(gates_path, "w", encoding="utf-8") as fh:
            fh.write(
                "# a comment\n\n"
                "passing: echo ok\n"
                f"failing: {sys.executable} {fail_script}\n"
                "skipped: true [needs: totally-not-a-real-tool-xyz]\n"
            )

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = main([gates_path, "--list"])
        check("list exits 0", rc == 0)
        check("list shows all 3 gates", buf.getvalue().count("\n") >= 3 and "skipped" in buf.getvalue())

        log_dir = os.path.join(tmp, "logs")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = main([gates_path, "--log-dir", log_dir])
        out = buf.getvalue()
        check("run exits 1 (a gate failed)", rc == 1)
        check("passing gate reported PASS", "PASS passing" in out)
        check("failing gate reported FAIL with a log path", "FAIL failing" in out and "log " in out)
        check("skipped gate reported SKIP", "SKIP skipped" in out)
        check("summary line present", "run_gates: 1 passed, 1 failed, 1 skipped" in out)
        check("the DEFECT line surfaced in the excerpt", "DEFECT: something broke" in out)
        check("no printed line exceeds the char cap", all(len(l) <= 310 for l in out.splitlines()))
        log_path = os.path.join(log_dir, "failing.log")
        check("full 5,000-line output landed in the log file", os.path.exists(log_path) and os.path.getsize(log_path) > 65_000)

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = main([gates_path, "--only", "passing", "--log-dir", log_dir])
        out_only = buf.getvalue()
        check("--only runs just the requested gate", rc == 0 and "PASS passing" in out_only and "failing" not in out_only)

        buf_err = io.StringIO()
        with contextlib.redirect_stderr(buf_err):
            rc = main([gates_path, "--only", "nope", "--log-dir", log_dir])
        check("--only refuses an unknown name", rc == 2 and "unknown" in buf_err.getvalue())

        dup_path = os.path.join(tmp, "dup.txt")
        with open(dup_path, "w", encoding="utf-8") as fh:
            fh.write("a: echo 1\na: echo 2\n")
        buf_err = io.StringIO()
        with contextlib.redirect_stderr(buf_err):
            rc = main([dup_path])
        check("duplicate gate name refused", rc == 2 and "duplicate" in buf_err.getvalue())

        bad_path = os.path.join(tmp, "bad.txt")
        with open(bad_path, "w", encoding="utf-8") as fh:
            fh.write("no colon here\n")
        buf_err = io.StringIO()
        with contextlib.redirect_stderr(buf_err):
            rc = main([bad_path])
        check("malformed gate line refused", rc == 2)

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = main([gates_path, "--jobs", "3", "--log-dir", os.path.join(tmp, "logs2")])
        out_jobs = buf.getvalue()
        check(
            "jobs=3 run still reports all three, in file order",
            rc == 1
            and "PASS passing" in out_jobs
            and "FAIL failing" in out_jobs
            and "SKIP skipped" in out_jobs
            and out_jobs.index("passing") < out_jobs.index("failing") < out_jobs.index("skipped"),
        )

    for item in failures:
        print("SELFTEST FAIL  " + item)
    print(f"selftest: {len(failures)} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
