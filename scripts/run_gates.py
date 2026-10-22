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
    run_gates.py --from-workflow FILE [FILE...]
    run_gates.py --selftest

`--from-workflow` prints a gates file derived from GitHub Actions workflows'
`run:` steps (see "From a CI workflow" below) and runs nothing. `--list` prints the parsed gates (name, command, needs) and runs nothing.
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
`--only` name, a workflow `--from-workflow` cannot parse); 3 the worktree has
a merge, rebase, cherry-pick or bisect in progress (MERGE_HEAD, rebase-merge/,
rebase-apply/, CHERRY_PICK_HEAD, BISECT_LOG in `git rev-parse --git-path`):
a gate read a half-mutated tree once, so the run refuses to start. `--list`
and `--from-workflow` do not check.

# From a CI workflow

`--from-workflow .github/workflows/ci.yml [more.yml]` writes the gates file
to stdout: one gate per single-line `run:` step, and per `run: |` block step
when it is safe to flatten (plain commands; backslash, `&&`, `||` and `|` continuations
joined; it runs as `set -e; ...`). Workflow, job and step `env:` scalars
become an `export` prefix and `working-directory:` a `(cd DIR && ...)`
prefix. Names are `<job>-<step name slug>`. A step is never silently dropped:
`uses:` steps, jobs with `services:`/`container:`/a reusable `uses:`,
`github.*` conditions and `secrets.*` are CI-only and only counted (an `env:` var whose value is a `${{ }}` expression is dropped and counted too), on the one
`# from-workflow:` summary line (also printed to stderr); a step it cannot
flatten safely (compound shell, heredoc, `${{ }}` expressions, other `if:`,
`continue-on-error`, a non-bash `shell:`, a folded scalar) is listed as
`# skipped: job/step - why`. So is a step that is no local pre-push gate: one
that runs the test suite (`cargo test`, `cargo nextest run`, `pytest`, `bats`,
`run_all_tests` — Phase 2's full-suite verdict covers it, VM and services
included), one that needs a service the host lacks (a database or NATS URL,
`docker`, `limactl`), one that only reports on the runner (`df`, `du`,
`echo`), and one named by `--skip-step GLOB` (repeatable, matched on the
derived gate name). The YAML parser is a minimal stdlib subset
(block maps and lists, `|` blocks, plain and quoted scalars); tabs, flow
`steps: [..]`, anchors, aliases and `<<:` are refused with exit 2.

No third-party imports: this runs wherever python3 and bash do.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import contextlib
import io
import os
import fnmatch
import re
import shutil
import subprocess
import sys
import tempfile
import time

NAME_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
NEEDS_RE = re.compile(r"^(?P<rest>.*)\[needs:\s*(?P<tool>[^\]]+)\]\s*$")
FAILURE_RE = re.compile(r"DEFECT|FAIL|ERROR|error|Error|panicked|not ok|✗")

GIT_STATE = ("MERGE_HEAD", "rebase-merge", "rebase-apply", "CHERRY_PICK_HEAD", "BISECT_LOG")
EXIT_GIT_STATE = 3


def git_state_in_progress(root: str) -> str | None:
    """Name of the first merge/rebase/cherry-pick/bisect marker in root's git dir, else None."""
    for marker in GIT_STATE:
        try:
            proc = subprocess.run(["git", "-C", root, "rev-parse", "--git-path", marker],
                                  capture_output=True, text=True, check=False)
        except OSError:
            return None
        if proc.returncode != 0:
            return None  # not a git worktree: nothing to guard
        path = proc.stdout.strip()
        if not os.path.isabs(path):
            path = os.path.join(root, path)
        if os.path.exists(path):
            return marker
    return None


class WorkflowError(Exception):
    pass


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _meaningful(line: str) -> bool:
    t = line.strip()
    return bool(t) and not t.startswith("#")


def _scalar(v: str) -> str | None:
    """Plain/quoted one-line scalar value; None when it cannot be read safely."""
    v = v.strip()
    if not v:
        return ""
    if v[0] == "'":
        m = re.match(r"^'((?:[^']|'')*)'\s*(#.*)?$", v)
        return m.group(1).replace("''", "'") if m else None
    if v[0] == '"':
        m = re.match(r'^"([^"\\]*)"\s*(#.*)?$', v)
        return m.group(1) if m else None
    if v[0] in "[{&*!|>%@`":
        return None
    return re.split(r"\s+#", v, maxsplit=1)[0].strip()


def _parse_map(lines: list[str]) -> dict:
    """One block map: key -> str | list[str] (literal block) | dict (nested scalar map)
    | None (unreadable value). Keys keep file order."""
    out: dict = {}
    if not lines:
        return out
    base = min(_indent(l) for l in lines if _meaningful(l))
    i = 0
    while i < len(lines):
        line = lines[i]
        if not _meaningful(line):
            i += 1
            continue
        if _indent(line) != base:
            raise WorkflowError(f"unexpected indentation at {line.strip()[:60]!r}")
        m = re.match(r"^\s*([A-Za-z0-9_.-]+|'[^']*'|\"[^\"]*\"):(?:\s+(.*))?$", line)
        if not m:
            raise WorkflowError(f"cannot read line {line.strip()[:60]!r}")
        key = m.group(1).strip("'\"")
        val = (m.group(2) or "").strip()
        j = i + 1
        while j < len(lines) and (not _meaningful(lines[j]) or _indent(lines[j]) > base):
            j += 1
        child = [l for l in lines[i + 1:j]]
        if re.match(r"^[|>][+-]?\d?\s*(#.*)?$", val):
            if val[0] == ">":
                out[key] = None
            else:
                body = [l for l in child]
                while body and not body[-1].strip():
                    body.pop()
                ind = min((_indent(l) for l in body if l.strip()), default=0)
                out[key] = [l[ind:] if l.strip() else "" for l in body]
        elif val == "" or val.startswith("#"):
            if any(_meaningful(l) for l in child):
                if any(l.strip().startswith("- ") or l.strip() == "-" for l in child if _meaningful(l)):
                    out[key] = ["<list>"]
                else:
                    try:
                        sub = _parse_map(child)
                        out[key] = {k: v for k, v in sub.items() if isinstance(v, str)} if all(
                            isinstance(v, str) for v in sub.values()) else sub
                    except WorkflowError:
                        out[key] = None
            else:
                out[key] = ""
        else:
            sv = _scalar(val)
            if any(_meaningful(l) for l in child):
                sv = None  # a plain scalar continued on following lines
            out[key] = sv
        i = j
    return out


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9_.-]+", "-", text.lower()).strip("-.")[:48] or "step"


def _flatten_block(body: list[str]) -> tuple[str | None, str]:
    """(one-line command, "") when a `run: |` body is safe to flatten, else (None, why)."""
    merged: list[str] = []
    carry = ""
    for raw in body:
        t = raw.strip()
        if not t or t.startswith("#"):
            continue
        if re.search(r"<<-?\s*[\"']?\w", t):
            return None, "heredoc"
        t = (carry + " " + t).strip() if carry else t
        carry = ""
        if t.endswith("\\"):
            carry = t[:-1].rstrip()
        elif re.search(r"(&&|\|\||\|)$", t):
            carry = t
        else:
            merged.append(t)
    if carry:
        return None, "dangling continuation"
    if not merged:
        return None, "empty script"
    for t in merged:
        if re.match(r"^(if|for|while|until|case|select|function|then|else|elif|fi|do|done|esac)\b", t) \
                or re.search(r"(^|[;&|]\s*)(if|for|while|until|case)\s", t) or t.endswith(("{", "(")) \
                or t.startswith(("}", ")")) or re.match(r"^[A-Za-z_]\w*\(\)", t):
            return None, "compound shell (if/for/while/case/function)"
        if re.search(r"\$\{?(GITHUB|RUNNER)_|\bexit\b", t):
            return None, "uses CI-only env or exit"
    return "set -e; " + "; ".join(merged), ""


def _env_prefix(env: dict) -> tuple[str, str, int]:
    """(export prefix, refusal why, number of expression-valued vars dropped)."""
    parts = []
    dropped = 0
    for k, v in env.items():
        if not isinstance(v, str) or not re.match(r"^[A-Za-z_]\w*$", k):
            return "", f"env {k} unreadable", 0
        if "${{" in v:
            dropped += 1
            continue
        parts.append(f"export {k}='{v.replace(chr(39), chr(39) + chr(92) + chr(39) + chr(39))}'; ")
    return "".join(parts), "", dropped


def derive_gates(paths: list[str]) -> tuple[list[tuple[str, str]], list[str], dict]:
    """(gates [(name, command)], unsafe-skip lines, CI-only skip counts by kind)."""
    gates: list[tuple[str, str]] = []
    unsafe: list[str] = []
    ci: dict[str, int] = {}
    names: set[str] = set()

    def count(kind: str, n: int = 1) -> None:
        ci[kind] = ci.get(kind, 0) + n

    for path in paths:
        try:
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
        except OSError as err:
            raise WorkflowError(f"{path}: {err}")
        lines = text.splitlines()
        for n, l in enumerate(lines, 1):
            if re.match(r"^\s*\t", l) or "\t" in l[:_indent(l) + 1] and l.startswith("\t"):
                raise WorkflowError(f"{path}:{n}: tab indentation")
            if re.match(r"^\s*(-\s+)?<<:", l) or re.search(r":\s+[&*][A-Za-z_]\w*\s*(#.*)?$", l):
                raise WorkflowError(f"{path}:{n}: YAML anchor/alias/merge key is not supported")
            if re.match(r"^\s*(-\s+)?steps:\s*[\[{]", l):
                raise WorkflowError(f"{path}:{n}: flow-style steps are not supported")
        top = [i for i, l in enumerate(lines) if _meaningful(l) and _indent(l) == 0]
        def block_of(key: str) -> list[str] | None:
            for k, i in enumerate(top):
                if re.match(rf"^{key}:\s*(#.*)?$", lines[i]):
                    end = top[k + 1] if k + 1 < len(top) else len(lines)
                    return lines[i + 1:end]
            return None
        jobs = block_of("jobs")
        if not jobs or not any(_meaningful(l) for l in jobs):
            raise WorkflowError(f"{path}: no `jobs:` block")
        try:
            wf_env = _parse_map(block_of("env") or [])
        except WorkflowError as err:
            raise WorkflowError(f"{path}: {err}")
        jbase = min(_indent(l) for l in jobs if _meaningful(l))
        starts = [i for i, l in enumerate(jobs) if _meaningful(l) and _indent(l) == jbase]
        for k, si in enumerate(starts):
            ei = starts[k + 1] if k + 1 < len(starts) else len(jobs)
            m = re.match(r"^\s*([A-Za-z0-9_.-]+):\s*(#.*)?$", jobs[si])
            if not m:
                raise WorkflowError(f"{path}: cannot read job header {jobs[si].strip()[:60]!r}")
            job = m.group(1)
            body = jobs[si + 1:ei]
            # split the job body at its own key level, keeping `steps:` apart
            bbase = min((_indent(l) for l in body if _meaningful(l)), default=0)
            ks = [i for i, l in enumerate(body) if _meaningful(l) and _indent(l) == bbase]
            steps_lines: list[str] = []
            head: list[str] = []
            for q, ki in enumerate(ks):
                qe = ks[q + 1] if q + 1 < len(ks) else len(body)
                if re.match(r"^\s*steps:\s*(#.*)?$", body[ki]):
                    steps_lines = body[ki + 1:qe]
                else:
                    head.extend(body[ki:qe])
            try:
                hm = _parse_map(head)
            except WorkflowError as err:
                raise WorkflowError(f"{path}: job {job}: {err}")
            nsteps = sum(1 for l in steps_lines if re.match(r"^\s*-(\s|$)", l) and _indent(l) == min(
                (_indent(x) for x in steps_lines if _meaningful(x)), default=0))
            if "uses" in hm:
                count("reusable-workflow-job", max(nsteps, 1))
                continue
            if "services" in hm or "container" in hm:
                count("service-job", nsteps)
                continue
            if isinstance(hm.get("if"), str) and "github." in hm["if"]:
                count("job-if-github", nsteps)
                continue
            if not steps_lines:
                continue
            dash = min(_indent(l) for l in steps_lines if _meaningful(l))
            items: list[list[str]] = []
            for l in steps_lines:
                if _meaningful(l) and _indent(l) == dash and re.match(r"^\s*-(\s|$)", l):
                    items.append([l])
                elif items:
                    items[-1].append(l)
                elif _meaningful(l):
                    raise WorkflowError(f"{path}: job {job}: cannot read steps")
            job_env = hm.get("env") if isinstance(hm.get("env"), dict) else {}
            for idx, item in enumerate(items, 1):
                first = item[0]
                item = [re.sub(r"^(\s*)-(\s+|$)", lambda mm: mm.group(1) + " " + " " * len(mm.group(2) or " "), first, count=1)] + item[1:]
                try:
                    st = _parse_map(item)
                except WorkflowError as err:
                    raise WorkflowError(f"{path}: job {job} step {idx}: {err}")
                label = f"{job}/{st.get('name') if isinstance(st.get('name'), str) and st.get('name') else idx}"
                if "uses" in st:
                    count("uses")
                    continue
                if "run" not in st:
                    count("no-run")
                    continue
                raw = json_like = "\n".join(item)
                if "secrets." in raw:
                    count("secrets")
                    continue
                cond = st.get("if")
                if isinstance(cond, str) and "github." in cond:
                    count("if-github")
                    continue
                if isinstance(cond, str) and cond.strip() not in ("always()", "success()"):
                    unsafe.append(f"# skipped: {label} - conditional `if: {cond[:60]}`")
                    continue
                if cond is None and "if" in st:
                    unsafe.append(f"# skipped: {label} - unreadable `if:`")
                    continue
                if "${{" in raw or "$GITHUB_" in raw or "$RUNNER_" in raw:
                    unsafe.append(f"# skipped: {label} - uses a ${{{{ }}}} expression or GitHub env var")
                    continue
                if str(st.get("continue-on-error", "")).lower() == "true":
                    unsafe.append(f"# skipped: {label} - continue-on-error (CI ignores its result)")
                    continue
                if st.get("shell") not in (None, "bash", "sh"):
                    unsafe.append(f"# skipped: {label} - shell: {st.get('shell')}")
                    continue
                run = st["run"]
                if run is None:
                    unsafe.append(f"# skipped: {label} - folded or multi-line plain scalar `run:`")
                    continue
                if isinstance(run, list):
                    cmd, why = _flatten_block(run)
                    if cmd is None:
                        unsafe.append(f"# skipped: {label} - {why}")
                        continue
                else:
                    cmd = run
                    if not cmd.strip():
                        continue
                env = {}
                for e in (wf_env, job_env, st.get("env") if isinstance(st.get("env"), dict) else {}):
                    env.update(e)
                prefix, why, dropped = _env_prefix(env)
                if dropped:
                    count("env-expr-dropped", dropped)
                if why:
                    unsafe.append(f"# skipped: {label} - {why}")
                    continue
                wd = st.get("working-directory")
                if isinstance(wd, str) and wd and wd != ".":
                    cmd = f"(cd {wd} && {cmd})"
                elif "working-directory" in st and not isinstance(wd, str):
                    unsafe.append(f"# skipped: {label} - unreadable working-directory")
                    continue
                base = _slug(f"{job}-{st['name']}" if isinstance(st.get("name"), str) and st.get("name") else f"{job}-{idx}")
                name, nn = base, 2
                while name in names:
                    name, nn = f"{base}-{nn}", nn + 1
                names.add(name)
                gates.append((name, prefix + cmd))
    return gates, unsafe, ci


# Steps a pre-push gate list must not run locally, each listed by name, never dropped:
# the suite (Phase 2's one full-suite verdict covers it, with its VM and services), a step
# that needs a service the local host lacks, and a step that only reports on the runner.
SUITE_STEP = re.compile(r"\bcargo\s+(?:\+\S+\s+)?(?:test|nextest\s+run)\b|\bpytest\b|\bbats\b|\brun_all_tests\.sh\b")
SERVICE_STEP = re.compile(r"postgres(?:ql)?://|DATABASE_URL|_DB_\w*=|nats://|redis://|\bpg_isready\b|(?:^|[;&|(]\s*|\$\(\s*)(?:sudo\s+)?(?:docker|docker-compose|limactl)\s")
DIAGNOSTIC_WORD = re.compile(r"^(?:df|du|free|ls|echo|uname|nproc|lscpu|true|:)\b")


def local_skip(cmd: str, name: str, skip_globs: list[str]) -> str | None:
    """Why a derived gate is not a local pre-push gate, or None when it is one."""
    for glob in skip_globs:
        if fnmatch.fnmatch(name, glob):
            return f"--skip-step {glob}"
    if SUITE_STEP.search(cmd):
        return "runs the test suite: the Phase 2 full-suite verdict (SUITE-COMMAND) covers it"
    if SERVICE_STEP.search(cmd):
        return "needs a service the local host lacks (database, NATS, docker, VM)"
    body = re.sub(r"^(?:export [^;]*; )+", "", cmd).removeprefix("set -e; ")
    pieces = [p.strip() for p in re.split(r"&&|\|\||;", body) if p.strip()]
    if pieces and all(DIAGNOSTIC_WORD.match(p) for p in pieces):
        return "reports on the CI runner only (df, du, echo, ...)"
    return None


def from_workflow(paths: list[str], skip_globs: list[str] | None = None) -> int:
    try:
        gates, unsafe, ci = derive_gates(paths)
    except WorkflowError as err:
        print(f"run_gates: --from-workflow refused: {err}", file=sys.stderr)
        return 2
    kept = []
    for name, cmd in gates:
        why = local_skip(cmd, name, skip_globs or [])
        if why:
            unsafe.append(f"# skipped: {name} - {why}")
        else:
            kept.append((name, cmd))
    gates = kept
    ci_part = ", ".join(f"{k}: {v}" for k, v in sorted(ci.items())) or "none"
    summary = (f"# from-workflow: {len(gates)} gates; {sum(ci.values())} CI-only steps skipped ({ci_part}); "
               f"{len(unsafe)} listed below as skipped")
    print(summary)
    for name, cmd in gates:
        print(f"{name}: {cmd}")
    for line in unsafe:
        print(line)
    print(summary.replace("# ", "run_gates: ", 1), file=sys.stderr)
    if not gates:
        print("run_gates: no gate could be derived", file=sys.stderr)
        return 2
    return 0


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
    ap.add_argument("--from-workflow", nargs="+", metavar="FILE", help="print a gates file derived from workflow `run:` steps and run nothing")
    ap.add_argument("--skip-step", action="append", default=[], metavar="GLOB",
                    help="--from-workflow: list a derived gate whose name matches GLOB as skipped")
    ap.add_argument("--selftest", action="store_true", help="check the checker")
    args = ap.parse_args(argv)
    if args.selftest:
        return selftest()
    if args.from_workflow:
        return from_workflow(args.from_workflow, args.skip_step)
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

    state = git_state_in_progress(os.path.abspath(args.root))
    if state:
        print(f"run_gates: refusing to run: {state} present in the worktree's git dir "
              "(merge, rebase, cherry-pick or bisect in progress); finish or abort it first", file=sys.stderr)
        return EXIT_GIT_STATE

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


WORKFLOW_FIXTURE = """\
name: CI
on: [push]
env:
  RUSTFLAGS: -D warnings
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Fmt
        run: cargo fmt --check
      - name: Check sub
        working-directory: sub
        run: cargo check --locked
      - name: Suite
        run: cargo nextest run --workspace
      - name: Stack self-test
        run: bash scripts/run_all_tests_stack_test.sh
      - name: Compose up
        run: docker compose up -d
      - name: Disk
        run: df -h / && du -sh target || true
      - name: Slow
        run: ./slow.sh
      - name: Scripts
        run: |
          # comment
          ./a.sh
          ./b.sh \\
            --x --y
      - name: Loop
        run: |
          for f in a b; do
            echo $f
          done
      - name: Heredoc
        run: |
          cat <<EOF
          hi
          EOF
      - name: Cache
        uses: actions/cache@v4
      - name: Deploy
        if: github.event_name == 'push'
        run: ./deploy.sh
      - name: Token
        run: ./publish.sh ${{ secrets.TOKEN }}
      - name: Lint
        run: ruff check .
  db:
    runs-on: ubuntu-latest
    services:
      postgres:
        image: postgres
    steps:
      - run: pytest
"""


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


        # --- #132: refuse during a merge/rebase/cherry-pick/bisect ---
        repo = os.path.join(tmp, "repo")
        os.makedirs(repo)
        gitq = lambda *a: subprocess.run(["git", "-C", repo, *a], capture_output=True, text=True)
        if gitq("init", "-q").returncode == 0:
            ok_gates = os.path.join(tmp, "ok.txt")
            with open(ok_gates, "w", encoding="utf-8") as fh:
                fh.write("a: true\n")
            with contextlib.redirect_stdout(io.StringIO()):
                rc_clean = main([ok_gates, "--root", repo, "--log-dir", os.path.join(tmp, "l3")])
            check("clean repo runs", rc_clean == 0)
            for marker in ("MERGE_HEAD", "CHERRY_PICK_HEAD", "BISECT_LOG", "rebase-merge"):
                gp = gitq("rev-parse", "--git-path", marker).stdout.strip()
                gp = gp if os.path.isabs(gp) else os.path.join(repo, gp)
                if marker == "rebase-merge":
                    os.makedirs(gp)
                else:
                    open(gp, "w").close()
                err_io = io.StringIO()
                with contextlib.redirect_stderr(err_io), contextlib.redirect_stdout(io.StringIO()):
                    rc_m = main([ok_gates, "--root", repo, "--log-dir", os.path.join(tmp, "l3")])
                check(f"{marker} in progress refused with exit 3", rc_m == 3 and marker in err_io.getvalue())
                if marker == "rebase-merge":
                    os.rmdir(gp)
                else:
                    os.remove(gp)

        # --- #142: --from-workflow ---
        wf = os.path.join(tmp, "ci.yml")
        with open(wf, "w", encoding="utf-8") as fh:
            fh.write(WORKFLOW_FIXTURE)
        out_io, err_io = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out_io), contextlib.redirect_stderr(err_io):
            rc_w = main(["--from-workflow", wf])
        wout = out_io.getvalue()
        check("from-workflow exits 0", rc_w == 0)
        check("single-line run is a gate", "build-fmt: export RUSTFLAGS='-D warnings'; cargo fmt --check" in wout)
        check("env exported and working-directory wrapped",
              "(cd sub && cargo check --locked)" in wout)
        check("the suite, a service step and a runner report are listed, never gates",
              "# skipped: build-suite - runs the test suite" in wout
              and "# skipped: build-compose-up - needs a service" in wout
              and "# skipped: build-disk - reports on the CI runner only" in wout
              and "build-stack-self-test: " in wout)
        check("safe block flattened to one gate", "set -e; ./a.sh; ./b.sh --x --y" in wout)
        check("compound block listed as skipped with why",
              "# skipped: build/Loop - compound shell" in wout)
        check("heredoc block listed as skipped", "# skipped: build/Heredoc - heredoc" in wout)
        check("uses/secrets/github-event/service steps are counted, not gates",
              "uses: 2" in wout and "secrets: 1" in wout and "if-github: 1" in wout
              and "service-job: 1" in wout and "deploy.sh" not in wout and "pytest" not in wout)
        check("summary on stderr too", "run_gates: from-workflow: 6 gates" in err_io.getvalue())
        wgates = os.path.join(tmp, "from-wf.txt")
        with open(wgates, "w", encoding="utf-8") as fh:
            fh.write(wout)
        try:
            check("derived file parses as a gates file", len(parse_gates(wout)) == 6)
        except GateFileError as err:
            check("derived file parses as a gates file", False)
        out_io = io.StringIO()
        with contextlib.redirect_stdout(out_io), contextlib.redirect_stderr(io.StringIO()):
            main(["--from-workflow", wf, "--skip-step", "build-sl*"])
        check("--skip-step lists the named step", "# skipped: build-slow - --skip-step build-sl*" in out_io.getvalue()
              and "\nbuild-slow:" not in out_io.getvalue())
        bad = os.path.join(tmp, "bad.yml")
        with open(bad, "w", encoding="utf-8") as fh:
            fh.write("jobs:\n  a:\n    steps: [ {run: x} ]\n")
        err_io = io.StringIO()
        with contextlib.redirect_stderr(err_io):
            rc_b = main(["--from-workflow", bad])
        check("flow-style steps refused with exit 2", rc_b == 2 and "refused" in err_io.getvalue())

    for item in failures:
        print("SELFTEST FAIL  " + item)
    print(f"selftest: {len(failures)} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
