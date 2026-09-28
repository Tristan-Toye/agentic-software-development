#!/usr/bin/env python3
"""Run one test command and print a short digest instead of raw output.

The orchestrator hand-types a command like

    limactl shell nightwatch -- bash -lc 'export PATH=...; cd <worktree> \\
        && . deploy/host/test-env.sh && CARGO_TARGET_DIR=... cargo test ...' \\
        > log; grep ...

once per test run — 244 times across 10 recorded runs — and the raw log (up
to 50 KB) lands in its context every time, on an expensive model that pays
for that context again on every later step. This script makes the run ONE
call: it builds the same command, streams every byte of stdout+stderr to a
log file, and prints only a bounded digest — the command, the exit code, the
wall time, the log path, the parsed pass/fail totals, and up to
`--max-failures` failing tests with a few lines of detail each. Nothing else
reaches stdout, however large the run.

# Usage

    run_tests.py [--log FILE] [--max-failures N=10] [--lines N=15]
                 [--line-chars N=300]
                 [--vm NAME --workdir DIR [--env-script FILE] [--path-prepend DIR]]
                 [--cwd DIR]
                 -- <command...>
    run_tests.py --selftest

Without `--vm` the command runs directly (optionally in `--cwd`). With
`--vm NAME --workdir DIR` it runs inside `limactl shell NAME -- bash -lc
'<export PATH=...:$PATH;> cd DIR && <. ENV-SCRIPT &&> COMMAND'`, with every
part correctly shell-quoted — the same shape the orchestrator was typing by
hand.

`--log` defaults to a file under `$TMPDIR` named after a hash of the
command, so repeat runs of the same command reuse one path.

# Parsers (auto-detected; several may apply to one log)

cargo/libtest: sums `test result: ok./FAILED. X passed; Y failed; Z
ignored...` across test binaries (cargo prints one such line per binary),
reads the `failures:` name list and the `---- NAME stdout ----` panic
blocks, and pulls out `error[E....]:`/`error:` compile errors with their
` --> path:line`.
cargo-nextest: `FAIL [..] crate test::name` lines and the
`Summary [..] N tests run: X passed, Y failed` line.
pytest: `FAILED path::test - message` lines and the `=== N failed, M passed
in ...s ===` summary (counts in any order).
bats/TAP: `not ok N desc` lines, with any following `# ...` diagnostic lines
as detail.
generic fallback (only when nothing above matched): lines containing
`FAIL`, `FAILED`, or `Error`.

When nothing parses at all, the digest is the last `--lines` lines of the
log instead. Every printed line is cut at `--line-chars` characters (with a
trailing "…"), so one giant line — a 68 KB panic message on one line, say —
never blows up the digest.

Exit codes: the run command's own exit code (so callers can branch on it);
2 for unusable input (no command, or `--vm` without `--workdir`).

No third-party imports: this runs wherever python3 does.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import shlex
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field


@dataclass
class Totals:
    passed: int = 0
    failed: int = 0
    ignored: int = 0


@dataclass
class Failure:
    name: str
    detail: list[str] = field(default_factory=list)


# ---------------------------------------------------------------- building the run


def build_command(
    vm: str | None,
    workdir: str | None,
    env_script: str | None,
    path_prepend: str | None,
    command: list[str],
) -> tuple[list[str], str]:
    """Return (argv, display). argv is what subprocess.run receives directly
    (no shell=True at this level); display is a short human string for the
    digest header."""
    joined = shlex.join(command)
    if not vm:
        return list(command), joined
    parts = []
    if path_prepend:
        parts.append(f"export PATH={shlex.quote(path_prepend)}:$PATH;")
    parts.append(f"cd {shlex.quote(workdir or '')} &&")
    if env_script:
        parts.append(f". {shlex.quote(env_script)} &&")
    parts.append(joined)
    inner = " ".join(parts)
    argv = ["limactl", "shell", vm, "--", "bash", "-lc", inner]
    return argv, f"limactl shell {vm} -- bash -lc '{inner}'"


def default_log_path(command: list[str]) -> str:
    h = hashlib.sha1("\x00".join(command).encode("utf-8", "replace")).hexdigest()[:16]
    return os.path.join(tempfile.gettempdir(), f"run_tests-{h}.log")


# ---------------------------------------------------------------------- parsers


def cut(line: str, n: int) -> str:
    line = line.rstrip("\n")
    return line if len(line) <= n else line[: max(n - 1, 0)] + "…"


def parse_cargo(text: str) -> tuple[Totals, list[Failure], bool]:
    result_re = re.compile(r"^test result: \w+\.\s*(\d+) passed;\s*(\d+) failed;\s*(\d+) ignored;", re.M)
    matches = result_re.findall(text)
    compile_re = re.compile(r"^(error(?:\[E\d+\])?):\s*(.*?)\n\s*-->\s*(\S+)", re.M)
    compiles = compile_re.findall(text)
    if not matches and not compiles:
        return Totals(), [], False
    totals = Totals(
        passed=sum(int(m[0]) for m in matches),
        failed=sum(int(m[1]) for m in matches),
        ignored=sum(int(m[2]) for m in matches),
    )
    names: list[str] = []
    for nm in re.finditer(r"^failures:\n((?:[ ]{4}.+\n)+)", text, re.M):
        names += [l.strip() for l in nm.group(1).splitlines() if l.strip()]
    blocks: dict[str, list[str]] = {}
    for bm in re.finditer(r"^---- (\S+) stdout ----\n(.*?)(?=\n---- |\nfailures:|\Z)", text, re.M | re.S):
        blocks[bm.group(1)] = [l for l in bm.group(2).strip("\n").splitlines()]
    failures = [Failure(n, blocks.get(n, [])) for n in names]
    for etype, msg, loc in compiles:
        failures.append(Failure(loc, [f"{etype}: {msg}"]))
    return totals, failures, True


def parse_nextest(text: str) -> tuple[Totals, list[Failure], bool]:
    fail_re = re.compile(r"^\s*FAIL\s+\[[^\]]*\]\s+(\S+)\s+(\S+)", re.M)
    fails = fail_re.findall(text)
    summary_m = re.search(r"Summary \[[^\]]*\]\s*\d+ tests run:\s*(\d+) passed,\s*(\d+) failed", text)
    if not fails and not summary_m:
        return Totals(), [], False
    totals = Totals()
    if summary_m:
        totals = Totals(passed=int(summary_m.group(1)), failed=int(summary_m.group(2)))
    failures = [Failure(f"{crate} {name}") for crate, name in fails]
    return totals, failures, True


def parse_pytest(text: str) -> tuple[Totals, list[Failure], bool]:
    fail_re = re.compile(r"^FAILED (\S+) - (.*)$", re.M)
    fails = fail_re.findall(text)
    summary_m = re.search(r"^=+ (.+?) in [\d.]+s ?=*$", text, re.M)
    if not fails and not summary_m:
        return Totals(), [], False
    totals = Totals()
    if summary_m:
        counts = dict((v, int(k)) for k, v in re.findall(r"(\d+) (failed|passed|skipped|error)", summary_m.group(1)))
        totals = Totals(passed=counts.get("passed", 0), failed=counts.get("failed", 0), ignored=counts.get("skipped", 0))
    failures = [Failure(path, [msg]) for path, msg in fails]
    return totals, failures, True


def parse_bats(text: str) -> tuple[Totals, list[Failure], bool]:
    lines = text.splitlines()
    oks = 0
    failures: list[Failure] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if re.match(r"^ok \d+\b", line):
            oks += 1
        elif re.match(r"^not ok \d+\b", line):
            m = re.match(r"^not ok (\d+)\s*-?\s*(.*)$", line)
            detail = []
            j = i + 1
            while j < len(lines) and lines[j].strip().startswith("#"):
                detail.append(lines[j].strip())
                j += 1
            name = f"{m.group(1)} {m.group(2)}".strip() if m else line
            failures.append(Failure(name, detail))
            i = j - 1
        i += 1
    if oks == 0 and not failures:
        return Totals(), [], False
    return Totals(passed=oks, failed=len(failures)), failures, True


def parse_generic(text: str) -> tuple[Totals, list[Failure], bool]:
    keep = [line for line in text.splitlines() if re.search(r"\bFAIL(?:ED)?\b|\bError\b", line)]
    if not keep:
        return Totals(), [], False
    return Totals(failed=len(keep)), [Failure(line.strip()) for line in keep], True


PARSERS: list[tuple[str, "callable[[str], tuple[Totals, list[Failure], bool]]"]] = [
    ("cargo", parse_cargo),
    ("nextest", parse_nextest),
    ("pytest", parse_pytest),
    ("bats/tap", parse_bats),
]


def parse_log(text: str) -> tuple[str, Totals, list[Failure]]:
    """Auto-detect and combine every specific parser that finds something;
    fall back to the generic FAIL/Error scan only when none of them did."""
    totals = Totals()
    failures: list[Failure] = []
    matched: list[str] = []
    for name, fn in PARSERS:
        t, f, ok = fn(text)
        if ok:
            matched.append(name)
            totals.passed += t.passed
            totals.failed += t.failed
            totals.ignored += t.ignored
            failures += f
    if matched:
        return ",".join(matched), totals, failures
    t, f, ok = parse_generic(text)
    if ok:
        return "generic", t, f
    return "", Totals(), []


# --------------------------------------------------------------------- digest


def format_body(
    parser: str,
    totals: Totals,
    failures: list[Failure],
    max_failures: int,
    lines_n: int,
    line_chars: int,
    log_text: str,
) -> list[str]:
    """The reusable part of the digest: totals + capped failures, or (when
    nothing parsed) the log's tail. No header — callers own that."""
    out: list[str] = []
    if parser:
        out.append(f"passed {totals.passed}  failed {totals.failed}  ignored {totals.ignored}  ({parser})")
        shown = failures[:max_failures]
        for fl in shown:
            out.append(f"FAIL {fl.name}")
            for l in fl.detail[:lines_n]:
                out.append("  " + cut(l, line_chars))
        remaining = len(failures) - len(shown)
        if remaining > 0:
            out.append(f"(+{remaining} more failures, see log)")
    else:
        out.append("no recognized test output; last lines of the log:")
        for l in log_text.splitlines()[-lines_n:]:
            out.append("  " + cut(l, line_chars))
    return out


def format_digest(
    display_cmd: str,
    exit_code: int,
    wall: float,
    log_path: str,
    parser: str,
    totals: Totals,
    failures: list[Failure],
    max_failures: int,
    lines_n: int,
    line_chars: int,
    log_text: str,
) -> str:
    short = display_cmd if len(display_cmd) <= 200 else display_cmd[:197] + "..."
    out = [f"$ {short}", f"exit {exit_code}  {wall:.1f}s  log: {log_path}"]
    out += format_body(parser, totals, failures, max_failures, lines_n, line_chars, log_text)
    return "\n".join(out)


# ------------------------------------------------------------------------ run


def run(args: argparse.Namespace) -> int:
    command = args.command
    if not command:
        print("run_tests: no command given after --", file=sys.stderr)
        return 2
    if args.vm and not args.workdir:
        print("run_tests: --vm requires --workdir", file=sys.stderr)
        return 2

    argv, display = build_command(args.vm, args.workdir, args.env_script, args.path_prepend, command)
    log_path = args.log or default_log_path(command)
    log_dir = os.path.dirname(os.path.abspath(log_path))
    os.makedirs(log_dir, exist_ok=True)

    start = time.time()
    with open(log_path, "w", encoding="utf-8", errors="replace") as fh:
        proc = subprocess.run(
            argv,
            stdout=fh,
            stderr=subprocess.STDOUT,
            cwd=(args.cwd if not args.vm else None),
            check=False,
        )
    wall = time.time() - start

    with open(log_path, encoding="utf-8", errors="replace") as fh:
        log_text = fh.read()
    parser, totals, failures = parse_log(log_text)
    print(
        format_digest(
            display, proc.returncode, wall, log_path, parser, totals, failures,
            args.max_failures, args.lines, args.line_chars, log_text,
        )
    )
    return proc.returncode


def main(argv: list[str] | None = None) -> int:
    raw = sys.argv[1:] if argv is None else argv
    if "--" in raw:
        idx = raw.index("--")
        own_args, command = raw[:idx], raw[idx + 1 :]
    else:
        own_args, command = raw, []

    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--log", help="log file path (default: a hash of the command, under $TMPDIR)")
    ap.add_argument("--max-failures", type=int, default=10)
    ap.add_argument("--lines", type=int, default=15, help="detail lines per failure / tail lines when nothing parses")
    ap.add_argument("--line-chars", type=int, default=300)
    ap.add_argument("--vm", help="run inside `limactl shell VM -- bash -lc ...`")
    ap.add_argument("--workdir", help="cd here inside the VM (required with --vm)")
    ap.add_argument("--env-script", help="sourced after cd, inside the VM")
    ap.add_argument("--path-prepend", help="prepended to PATH, inside the VM")
    ap.add_argument("--cwd", help="cwd for a direct (non-VM) run")
    ap.add_argument("--selftest", action="store_true", help="check the checker")
    args = ap.parse_args(own_args)
    args.command = command
    if args.selftest:
        return selftest()
    return run(args)


# -------------------------------------------------------------------- selftest

CARGO_MULTI_BINARY = """running 3 tests
test a ... ok
test b ... ok
test c ... FAILED

failures:

---- c stdout ----
thread 'c' panicked at src/lib.rs:10:5:
assertion failed: left == right

failures:
    c

test result: FAILED. 2 passed; 1 failed; 0 ignored; 0 measured; 0 filtered out; finished in 0.01s

running 2 tests
test d ... ok
test e ... ok

test result: ok. 2 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out; finished in 0.00s
"""

CARGO_COMPILE_ERROR = """   Compiling foo v0.1.0
error[E0433]: failed to resolve: use of undeclared crate or module `bar`
 --> src/main.rs:3:5
  |
3 |     bar::do_it();
  |     ^^^ use of undeclared crate or module `bar`

error: could not compile `foo` due to previous error
"""

NEXTEST_LOG = """    Starting 10 tests
        FAIL [   0.023s] my-crate tests::foo
        FAIL [   0.031s] my-crate tests::bar
------------
     Summary [   0.045s] 10 tests run: 8 passed, 2 failed, 0 skipped
"""

PYTEST_LOG = """collected 10 items
FAILED tests/test_x.py::test_y - assert 1 == 2
FAILED tests/test_z.py::test_w - AssertionError
=== 2 failed, 8 passed in 1.23s ===
"""

BATS_LOG = """1..3
ok 1 first test
not ok 2 second test
# (in test file x.bats, line 5)
# `false' failed
ok 3 third test
"""

GENERIC_LOG = """Running suite...
Error: something broke
Test FAILED unexpectedly
Done.
"""

NO_PARSE_LOG = """Compiling...
Linking...
Build finished in 3.2s
"""


def selftest() -> int:
    failures_list: list[str] = []

    def check(name: str, ok: bool) -> None:
        if not ok:
            failures_list.append(name)

    parser, totals, fails = parse_log(CARGO_MULTI_BINARY)
    check("cargo multi-binary totals", totals == Totals(passed=4, failed=1, ignored=0))
    check("cargo multi-binary one failure named c", len(fails) == 1 and fails[0].name == "c")
    check("cargo panic detail captured", any("panicked at src/lib.rs:10:5" in l for l in fails[0].detail))

    parser, totals, fails = parse_log(CARGO_COMPILE_ERROR)
    check("compile error detected", parser.startswith("cargo"))
    check("compile error location", fails and fails[0].name == "src/main.rs:3:5")

    parser, totals, fails = parse_log(NEXTEST_LOG)
    check("nextest totals", totals == Totals(passed=8, failed=2))
    check("nextest names", [f.name for f in fails] == ["my-crate tests::foo", "my-crate tests::bar"])

    parser, totals, fails = parse_log(PYTEST_LOG)
    check("pytest totals", totals == Totals(passed=8, failed=2))
    check("pytest failure detail", fails[0].detail == ["assert 1 == 2"])

    parser, totals, fails = parse_log(BATS_LOG)
    check("bats totals", totals == Totals(passed=2, failed=1))
    check("bats detail captured", fails and fails[0].detail == ["# (in test file x.bats, line 5)", "# `false' failed"])

    parser, totals, fails = parse_log(GENERIC_LOG)
    check("generic fallback triggers", parser == "generic" and totals.failed == 2)

    parser, totals, fails = parse_log(NO_PARSE_LOG)
    check("no-parse log parses to nothing", parser == "" and not fails)
    body = format_body(parser, totals, fails, 10, 2, 300, NO_PARSE_LOG)
    check("no-parse falls back to tail", body[1:] == ["  Linking...", "  Build finished in 3.2s"])

    # A 68 KB single-line panic must come out cut, not blow up the digest.
    huge_line = "x" * 70000
    huge_log = f"""running 1 test
test z ... FAILED

failures:

---- z stdout ----
thread 'z' panicked at src/lib.rs:1:1:
{huge_line}

failures:
    z

test result: FAILED. 0 passed; 1 failed; 0 ignored; 0 measured; 0 filtered out; finished in 0.00s
"""
    parser, totals, fails = parse_log(huge_log)
    check("huge panic line captured before cutting", any(len(l) > 300 for l in fails[0].detail))
    body = format_body(parser, totals, fails, 10, 15, 300, huge_log)
    check("digest cuts the huge line", all(len(l) <= 302 for l in body))  # 300 chars + "  " prefix
    check("digest still marks the cut", any(l.endswith("…") for l in body))

    check("cut respects line_chars", len(cut("a" * 500, 50)) == 50 and cut("a" * 500, 50).endswith("…"))
    check("cut leaves short lines alone", cut("short", 50) == "short")

    # --vm builds the right argv, without ever invoking limactl.
    argv, display = build_command("nightwatch", "/work/x", "deploy/host/test-env.sh", "/opt/bin", ["cargo", "test"])
    check("vm argv shape", argv[:6] == ["limactl", "shell", "nightwatch", "--", "bash", "-lc"])
    inner = argv[6]
    check("vm cds into workdir", "cd /work/x &&" in inner)
    check("vm sources env script", ". deploy/host/test-env.sh &&" in inner)
    check("vm prepends PATH", "export PATH=/opt/bin:$PATH;" in inner)
    check("vm keeps the real command", inner.endswith("cargo test"))
    argv2, _ = build_command(None, None, None, None, ["echo", "hi there"])
    check("no-vm argv is the command itself, quoted for display only", argv2 == ["echo", "hi there"])

    # A real, tiny run end to end, with --log in a temp dir.
    with tempfile.TemporaryDirectory() as tmp:
        log_path = os.path.join(tmp, "x.log")
        rc = main(["--log", log_path, "--", "python3", "-c", "print('x')"])
        check("real run exits 0", rc == 0)
        check("real run writes the log", os.path.exists(log_path) and open(log_path).read().strip() == "x")

        rc = main(["--log", log_path, "--", "python3", "-c", "import sys; sys.exit(7)"])
        check("real run propagates the exit code", rc == 7)

        rc = main(["--vm", "nightwatch", "--", "true"])
        check("--vm without --workdir is unusable input", rc == 2)

        rc = main(["--log", log_path])
        check("no command after -- is unusable input", rc == 2)

    for item in failures_list:
        print("SELFTEST FAIL  " + item)
    print(f"selftest: {len(failures_list)} failure(s)")
    return 1 if failures_list else 0


if __name__ == "__main__":
    sys.exit(main())
