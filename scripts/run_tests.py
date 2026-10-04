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
                 [--cwd DIR] [--test NAME ...] [--repeat N] [--detach] [--print-cmd]
                 -- <command...>
    run_tests.py --wait LOG [--timeout SECONDS=3600]
    run_tests.py --digest LOG
    run_tests.py --selftest

Without `--vm` the command runs directly (optionally in `--cwd`). With
`--vm NAME --workdir DIR` it runs inside `limactl shell NAME -- bash -lc
'<export PATH=...:$PATH;> cd DIR && <. ENV-SCRIPT &&> COMMAND'`.

The VM command is built so it pastes into a fresh shell unchanged: the inner
`bash -lc` script is one single-quoted argument (`shlex.join`, the same string
that is run), and inside it `$HOME` / `$PATH` / a leading `~/` stay LIVE, to be
expanded by the VM's own bash — the outer macOS shell never sees them. (Before,
the printed line was not safely quoted, so pasting it let the outer shell expand
`$HOME` to the Mac's home, and an implementer ran darwin cargo in the VM.)
`--print-cmd` prints that full command and runs nothing; the digest header
shows it cut at 200 characters.

`--log` defaults to a file under `$TMPDIR` named after a hash of the
command, so repeat runs of the same command reuse one path.

# Detached runs, digests of old logs, repeats, one test

`--detach` starts the same run in its own session (setsid, SIGHUP ignored,
stdio detached) so it survives the calling shell or tool call returning, then
prints four lines — the pid, the log path, the pid file `LOG.pid` and the exact
`--wait` command — and exits 0 at once. When the run ends the child writes
`LOG.exit` and then `LOG.digest` (the digest it would have printed).

`--wait LOG` blocks, silently, until `LOG.digest` exists, prints it, and exits
with the run's own exit code (read from `LOG.exit`). 124 means `--timeout`
passed with the run still going; 125 means the process died without writing a
digest (killed), and a digest of the log so far is printed instead; 2 means no
detached run was started for LOG.

`--digest LOG` prints the digest of a log already on disk (no run): totals,
capped failures, or the tail when nothing parses. Exit 1 when it holds failures,
else 0. `--wait` and `--digest` need no command after `--`.

`--repeat N` runs the command N times (logs `LOG.run1` ... `LOG.runN`) and
prints one `RUN k/N pass|fail  exit E  Ts` line per run, then `FLAKY name
(failed X/N)` for every test that failed in some runs but not all, `FAILS EVERY
RUN name` for those that failed in all, and the capped digest of the first
failing run. Exit 0 when every run passed, else the first nonzero exit code.

`--test NAME` (repeatable) appends the filter to a cargo command, after a `--`
(added when missing): `cargo test -p x -- NAME`. A name that matches no test
line in the log is reported `NOT RUN` and turns an otherwise green exit into 3,
so a typo cannot pass as green. Only a `cargo` command takes `--test`.

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
2 for unusable input (no command, `--vm` without `--workdir`, `--test` on a
non-cargo command); 3 a `--test` name matched nothing; 124/125 from `--wait`
(see above).

No third-party imports: this runs wherever python3 does.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import os
import re
import shlex
import signal
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


def inner_quote(value: str) -> str:
    """Quote one word of the VM's inner `bash -lc` script. A word with no `$`
    or backtick gets plain `shlex.quote`. A word with `$` (`$HOME/.cargo/bin`)
    or a leading `~/` goes in double quotes with `"`, `\\` and backtick
    escaped, so the VM's bash still expands `$HOME` and `$PATH` — single-quoting
    it (the old behaviour) would have made them literal, and leaving it bare
    inside an unquoted outer string let the OUTER shell expand them."""
    if value.startswith("~/"):
        value = "$HOME/" + value[2:]
    if "$" not in value:
        return shlex.quote(value)
    return '"' + re.sub(r'(["\\`])', r"\\\1", value) + '"'


def build_command(
    vm: str | None,
    workdir: str | None,
    env_script: str | None,
    path_prepend: str | None,
    command: list[str],
) -> tuple[list[str], str]:
    """Return (argv, display). argv is what subprocess.run receives directly
    (no shell=True at this level); display is `shlex.join(argv)` — the very
    command that is run, quoted so it pastes into a fresh shell unchanged (the
    inner script is one single-quoted word, so `$HOME` reaches the VM literal)."""
    joined = shlex.join(command)
    if not vm:
        return list(command), joined
    parts = []
    if path_prepend:
        parts.append(f"export PATH={inner_quote(path_prepend)}:$PATH;")
    parts.append(f"cd {inner_quote(workdir or '')} &&")
    if env_script:
        parts.append(f". {inner_quote(env_script)} &&")
    parts.append(joined)
    inner = " ".join(parts)
    argv = ["limactl", "shell", vm, "--", "bash", "-lc", inner]
    return argv, shlex.join(argv)


def add_test_filters(command: list[str], names: list[str]) -> list[str]:
    """`cargo test -p x` + names -> `cargo test -p x -- NAME ...` (a `--` is
    added when missing; names go last, where libtest reads its filters)."""
    if not command or os.path.basename(command[0]) != "cargo":
        raise ValueError("--test only extends a cargo command")
    out = list(command)
    if "--" not in out:
        out.append("--")
    return out + list(names)


def names_not_run(log_text: str, names: list[str]) -> list[str]:
    """Names with no `test PATH ... ok|FAILED|ignored` line containing them.
    Empty when the log holds no libtest line at all (a compile error says
    nothing about which tests ran)."""
    ran = re.findall(r"^test (\S+) \.\.\. (?:ok|FAILED|ignored)", log_text, re.M)
    if not ran:
        return []
    return [n for n in names if not any(n in r for r in ran)]


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


def execute(argv: list[str], log_path: str, cwd: str | None) -> tuple[int, float, str]:
    """One run: every byte of stdout+stderr to log_path. Returns (exit, wall, log text)."""
    os.makedirs(os.path.dirname(os.path.abspath(log_path)), exist_ok=True)
    start = time.time()
    with open(log_path, "w", encoding="utf-8", errors="replace") as fh:
        proc = subprocess.run(argv, stdout=fh, stderr=subprocess.STDOUT, cwd=cwd, check=False)
    wall = time.time() - start
    with open(log_path, encoding="utf-8", errors="replace") as fh:
        return proc.returncode, wall, fh.read()


def digest_of(path: str, args: argparse.Namespace) -> tuple[str, int]:
    """(digest text, failure count) of a log on disk, no run."""
    with open(path, encoding="utf-8", errors="replace") as fh:
        text = fh.read()
    parser, totals, failures = parse_log(text)
    body = format_body(parser, totals, failures, args.max_failures, args.lines, args.line_chars, text)
    head = f"digest of {path} ({len(text)} chars)"
    return "\n".join([head, *body]), len(failures)


def single_run(args: argparse.Namespace, argv: list[str], display: str, log_path: str) -> int:
    rc, wall, log_text = execute(argv, log_path, args.cwd if not args.vm else None)
    parser, totals, failures = parse_log(log_text)
    out = format_digest(display, rc, wall, log_path, parser, totals, failures,
                        args.max_failures, args.lines, args.line_chars, log_text)
    missing = names_not_run(log_text, args.test) if args.test else []
    if missing:
        out += f"\nNOT RUN (matched no test): {', '.join(missing)}"
        rc = rc or 3
    print(out)
    return rc


def repeat_run(args: argparse.Namespace, argv: list[str], display: str, log_path: str) -> int:
    """N runs; per-run verdict, flaky tests (failed in some runs, not all)."""
    n = args.repeat
    first_rc = 0
    fail_counts: dict[str, int] = {}
    first_fail: tuple[str, Totals, list[Failure], str] | None = None
    short = display if len(display) <= 200 else display[:197] + "..."
    out = [f"$ {short}", f"repeat {n}x  logs: {log_path}.run1 .. .run{n}"]
    failed_runs = 0
    for k in range(1, n + 1):
        run_log = f"{log_path}.run{k}"
        rc, wall, text = execute(argv, run_log, args.cwd if not args.vm else None)
        parser, totals, failures = parse_log(text)
        bad = rc != 0 or bool(failures)
        out.append(f"RUN {k}/{n} {'fail' if bad else 'pass'}  exit {rc}  {wall:.1f}s")
        if bad:
            failed_runs += 1
            first_rc = first_rc or rc or 1
            for name in {f.name for f in failures}:
                fail_counts[name] = fail_counts.get(name, 0) + 1
            if first_fail is None:
                first_fail = (parser, totals, failures, text)
    out.append(f"{n - failed_runs} of {n} runs passed")
    flaky = sorted((c, nm) for nm, c in fail_counts.items() if c < n)
    always = sorted(nm for nm, c in fail_counts.items() if c == n)
    for c, nm in flaky[: args.max_failures]:
        out.append(f"FLAKY {nm} (failed {c}/{n})")
    for nm in always[: args.max_failures]:
        out.append(f"FAILS EVERY RUN {nm}")
    if first_fail:
        parser, totals, failures, text = first_fail
        out.append("first failing run:")
        out += ["  " + l for l in format_body(parser, totals, failures, args.max_failures, args.lines, args.line_chars, text)]
    print("\n".join(out))
    return first_rc


def detach(args: argparse.Namespace, own_args: list[str], command: list[str], log_path: str) -> int:
    """Start the same run in its own session and return at once."""
    for suffix in (".digest", ".exit", ".pid"):
        with contextlib.suppress(OSError):
            os.remove(log_path + suffix)
    child = [sys.executable, os.path.abspath(__file__),
             *[a for a in own_args if a != "--detach"], "--log", log_path, "--detached-child", "--", *command]
    proc = subprocess.Popen(child, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, start_new_session=True, close_fds=True)
    with open(log_path + ".pid", "w", encoding="utf-8") as fh:
        fh.write(f"{proc.pid}\n")
    print(f"detached: pid {proc.pid}")
    print(f"log: {log_path}")
    print(f"pid file: {log_path}.pid")
    print(f"wait: python3 {shlex.quote(os.path.abspath(__file__))} --wait {shlex.quote(log_path)}")
    return 0


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def read_int(path: str, default: int) -> int:
    try:
        with open(path, encoding="utf-8") as fh:
            return int(fh.read().strip())
    except (OSError, ValueError):
        return default


def wait(args: argparse.Namespace, log_path: str) -> int:
    """Block silently until the detached run's digest exists, then print it."""
    digest_path = log_path + ".digest"
    if not os.path.exists(log_path + ".pid") and not os.path.exists(digest_path):
        print(f"run_tests: no detached run for {log_path} (no {log_path}.pid)", file=sys.stderr)
        return 2
    deadline = time.monotonic() + args.timeout
    dead_polls = 0
    pause = 0.2
    while True:
        if os.path.exists(digest_path):
            with open(digest_path, encoding="utf-8", errors="replace") as fh:
                print(fh.read().rstrip("\n"))
            return read_int(log_path + ".exit", 0)
        pid = read_int(log_path + ".pid", 0)
        if pid and not alive(pid):
            dead_polls += 1
            if dead_polls >= 3:  # grace: the digest is written just before the child exits
                print(f"run_tests: pid {pid} ended without a digest (killed?); the log so far:")
                if os.path.exists(log_path):
                    print(digest_of(log_path, args)[0])
                return 125
        if time.monotonic() >= deadline:
            print(f"run_tests: still running after {args.timeout:.0f}s (pid {pid}); wait again with the same command")
            return 124
        time.sleep(pause)
        pause = min(pause * 1.5, 5.0)


def run(args: argparse.Namespace, own_args: list[str]) -> int:
    command = args.command
    if not command:
        print("run_tests: no command given after --", file=sys.stderr)
        return 2
    if args.vm and not args.workdir:
        print("run_tests: --vm requires --workdir", file=sys.stderr)
        return 2
    if args.test:
        try:
            command = add_test_filters(command, args.test)
        except ValueError as err:
            print(f"run_tests: {err}", file=sys.stderr)
            return 2

    argv, display = build_command(args.vm, args.workdir, args.env_script, args.path_prepend, command)
    if args.print_cmd:
        print(display)
        return 0
    log_path = args.log or default_log_path(args.command)
    if args.detach:
        return detach(args, own_args, args.command, log_path)
    if args.repeat and args.repeat > 1:
        return repeat_run(args, argv, display, log_path)
    return single_run(args, argv, display, log_path)


def run_detached_child(args: argparse.Namespace, own_args: list[str]) -> int:
    """The detached half: run, then leave LOG.exit and LOG.digest behind (the
    digest last, atomically, so a waiter that sees it can trust LOG.exit)."""
    signal.signal(signal.SIGHUP, signal.SIG_IGN)
    log_path = args.log or default_log_path(args.command)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = run(args, own_args)
    for suffix, text in ((".exit", f"{rc}\n"), (".digest", buf.getvalue())):
        tmp = f"{log_path}{suffix}.tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, log_path + suffix)
    return rc


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
    ap.add_argument("--path-prepend", help="prepended to PATH, inside the VM ($HOME stays live there)")
    ap.add_argument("--cwd", help="cwd for a direct (non-VM) run")
    ap.add_argument("--test", action="append", default=[], metavar="NAME", help="append a test filter to a cargo command")
    ap.add_argument("--repeat", type=int, default=0, metavar="N", help="run N times; report flaky tests")
    ap.add_argument("--detach", action="store_true", help="run detached; print the log path, pid file and --wait command")
    ap.add_argument("--wait", metavar="LOG", help="block until the detached run on LOG ends; print its digest")
    ap.add_argument("--timeout", type=float, default=3600, help="--wait: give up after this many seconds")
    ap.add_argument("--digest", metavar="LOG", help="print the digest of an existing log; run nothing")
    ap.add_argument("--print-cmd", action="store_true", help="print the full paste-safe command; run nothing")
    ap.add_argument("--detached-child", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--selftest", action="store_true", help="check the checker")
    args = ap.parse_args(own_args)
    args.command = command
    if args.selftest:
        return selftest()
    if args.digest:
        try:
            text, nfail = digest_of(args.digest, args)
        except OSError as err:
            print(f"run_tests: {err}", file=sys.stderr)
            return 2
        print(text)
        return 1 if nfail else 0
    if args.wait:
        return wait(args, args.wait)
    if args.detached_child:
        return run_detached_child(args, own_args)
    return run(args, own_args)


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

        # --- the $HOME bug: the printed VM command must paste into a fresh shell ---
        argv, display = build_command("nightwatch", "/work/x y", "deploy/host/test-env.sh", "$HOME/.cargo/bin", ["cargo", "test", "-p", "a b"])
        inner = argv[6]
        check("inner script keeps a literal $HOME", 'export PATH="$HOME/.cargo/bin":$PATH;' in inner)
        check("display is exactly the shlex-joined argv (paste-safe)", display == shlex.join(argv) and shlex.split(display) == argv)
        check("display single-quotes the whole inner script", "'export PATH=\"$HOME/.cargo/bin\":$PATH;" in display)
        _, d2 = build_command("vm", "/w", None, "~/.cargo/bin", ["true"])
        check("a leading ~/ becomes a live $HOME", '"$HOME/.cargo/bin"' in d2)
        probe = build_command("vm", "/", None, "$HOME/.cargo/bin", ["printenv", "PATH"])[0][6]
        env = dict(os.environ, HOME="/zz-vm-home")
        got = subprocess.run(["bash", "-c", probe], capture_output=True, text=True, env=env, check=False).stdout
        check("bash expands $HOME inside the VM script, not before", got.startswith("/zz-vm-home/.cargo/bin:"))
        rc = main(["--print-cmd", "--vm", "nightwatch", "--workdir", "/w", "--path-prepend", "$HOME/.cargo/bin", "--", "cargo", "test"])
        check("--print-cmd prints and runs nothing", rc == 0)

        # --- --test appends the filter after `--`, cargo only ---
        check("--test adds `--` and the name", add_test_filters(["cargo", "test", "-p", "x"], ["foo"]) == ["cargo", "test", "-p", "x", "--", "foo"])
        check("--test reuses an existing `--`", add_test_filters(["cargo", "test", "--", "--nocapture"], ["a", "b"]) == ["cargo", "test", "--", "--nocapture", "a", "b"])
        try:
            add_test_filters(["pytest"], ["x"])
            check("--test refuses a non-cargo command", False)
        except ValueError:
            pass
        rc = main(["--test", "x", "--", "python3", "-c", "pass"])
        check("--test on a non-cargo command is unusable input", rc == 2)
        libtest = "test a::b::foo_works ... ok\ntest a::c::bar ... FAILED\n"
        check("names_not_run finds the missing name", names_not_run(libtest, ["foo_works", "ghost"]) == ["ghost"])
        check("names_not_run is silent without libtest lines", names_not_run("error: could not compile\n", ["x"]) == [])

        # --- --digest reads a log, runs nothing ---
        dlog = os.path.join(tmp, "old.log")
        with open(dlog, "w", encoding="utf-8") as fh:
            fh.write(PYTEST_LOG)
        rc = main(["--digest", dlog])
        check("--digest exits 1 on a log with failures", rc == 1)
        with open(dlog, "w", encoding="utf-8") as fh:
            fh.write("test result: ok. 3 passed; 0 failed; 0 ignored; 0 measured\n")
        check("--digest exits 0 on a green log", main(["--digest", dlog]) == 0)
        check("--digest of a missing file is unusable input", main(["--digest", os.path.join(tmp, "nope.log")]) == 2)

        # --- --repeat finds the flaky test: fails on runs 1 and 3 of 3 ---
        counter = os.path.join(tmp, "count")
        flaky_py = (
            "import os, sys; p = sys.argv[1]; "
            "n = int(open(p).read()) if os.path.exists(p) else 0; open(p, 'w').write(str(n + 1)); "
            "print('FAILED tests/x.py::t_flaky - boom' if n % 2 == 0 else 'ok'); sys.exit(1 if n % 2 == 0 else 0)"
        )
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = main(["--log", os.path.join(tmp, "rep.log"), "--repeat", "3", "--", "python3", "-c", flaky_py, counter])
        out = buf.getvalue()
        check("--repeat prints a line per run", "RUN 1/3 fail" in out and "RUN 2/3 pass" in out and "RUN 3/3 fail" in out)
        check("--repeat names the flaky test with its rate", "FLAKY tests/x.py::t_flaky (failed 2/3)" in out)
        check("--repeat exits nonzero when any run failed", rc == 1)
        check("--repeat keeps one log per run", os.path.exists(os.path.join(tmp, "rep.log.run3")))

        # --- --detach / --wait: the run outlives the call, the digest is waited for ---
        dl = os.path.join(tmp, "det.log")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = main(["--log", dl, "--detach", "--", "python3", "-c", "import time; time.sleep(0.4); print('ok')"])
        out = buf.getvalue()
        check("--detach returns 0 at once and prints pid, log, pid file, wait", rc == 0 and "detached: pid" in out and f"pid file: {dl}.pid" in out and "--wait" in out)
        check("--detach wrote the pid file", os.path.exists(dl + ".pid"))
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = main(["--wait", dl, "--timeout", "30"])
        check("--wait prints the digest of the finished run", rc == 0 and "exit 0" in buf.getvalue() and dl in buf.getvalue())
        check("the detached run left LOG.exit and LOG.digest", read_int(dl + ".exit", -1) == 0 and os.path.exists(dl + ".digest"))
        dl2 = os.path.join(tmp, "det2.log")
        with contextlib.redirect_stdout(io.StringIO()):
            main(["--log", dl2, "--detach", "--", "python3", "-c", "import sys; sys.exit(7)"])
            rc = main(["--wait", dl2, "--timeout", "30"])
        check("--wait returns the run's own exit code", rc == 7)
        check("--wait on a log with no detached run is unusable input", main(["--wait", os.path.join(tmp, "never.log")]) == 2)
        slow = os.path.join(tmp, "slow.log")
        with contextlib.redirect_stdout(io.StringIO()):
            main(["--log", slow, "--detach", "--", "python3", "-c", "import time; time.sleep(5)"])
            rc = main(["--wait", slow, "--timeout", "0.3"])
        check("--wait times out with 124", rc == 124)
        with contextlib.suppress(OSError, ValueError):
            os.kill(read_int(slow + ".pid", 0), signal.SIGTERM)

    for item in failures_list:
        print("SELFTEST FAIL  " + item)
    print(f"selftest: {len(failures_list)} failure(s)")
    return 1 if failures_list else 0


if __name__ == "__main__":
    sys.exit(main())
