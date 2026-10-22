#!/usr/bin/env python3
"""Mark the changed lines a Phase 7 lens should look at for failure handling, logging and security.

The reviewer is blind to the diff, and its one pass is spent on what `SCOPE` names. Three
questions it is worst at finding unaided are shapes a pattern can point at: an error that
is swallowed or loses its cause, an external call with no timeout, a retry loop with no
bound, a log line that tells an operator nothing (or tells an attacker a secret), and a
change that reaches a trust boundary at all. This script finds those shapes on the lines a
build added between `--base` and `--head`, and reports each as a CURRENT-STATE location
(`path:line`, the line as it reads now), never as diff text, so the lens stays blind to the
history the way `SCOPE`'s `(changed)` marks already allow. A marker is a reason to look,
never a verdict: the lens judges it, and an unjudged marker is not a change request.

# Usage

    review_signals.py --root DIR --base SHA [--head HEAD] [--max N=40] [--field]
    review_signals.py --selftest

Prints one line per marker, `path:line  <group>/<kind>  <the line, cut at 120 chars>`, at
most `--max` of them (the rest are counted on a `... <n> more` line, never dropped
silently), then three summary lines for the build log:

    FAILURE-SIGNALS: <n> markers in <k> files (swallow 2, timeout 1) | none in <m> files
    LOG-SIGNALS: <n> markers in <k> files (bare-log 1) | none in <m> files
    SECURITY-TRIGGERS: secrets 2 (deploy/secrets/a.sh, ...), ci 1 (...) | none

`--field` prints the same as one `SIGNALS: |` payload block for the architecture lens
(`compose_payloads.py --review-base SHA` runs this itself).

# Kinds

Scanned: source files the build added or changed, outside tests (`tests/`, `test/`,
`test-support/`, `*_test.*`, `test_*.py`, `spec/`), on added lines only. Comment lines are
skipped and string literals are blanked before a code pattern is matched.

failure/
- `swallow` — an error dropped on the floor: Rust `let _ = call(..)`, `.ok();`,
  `Err(_) =>` into anything but another `Err`, `.unwrap_or_default()` on a call; Python a bare `except:`, an `except` whose
  body is `pass` or `...`, `contextlib.suppress(`; shell `|| true`.
- `cause-dropped` — an error converted with its cause thrown away: Rust `map_err(|_|`,
  `Err(_) => Err(..)`,
  Python `raise ... from None` (a plain `raise` inside `except` keeps the cause).
- `timeout` — an external call with no bound: `reqwest::Client::new()`, `reqwest::get(`,
  `TcpStream::connect(` outside `timeout(`, Python `requests.<verb>(` or `urlopen(` without
  `timeout=`, `subprocess.run/check_output/check_call(` without `timeout=`, `curl` without
  `--max-time`/`-m`.
- `retry` — a `loop {` / `while True:` / `while true` whose next 20 lines retry (`retry`,
  `backoff`, `attempt`) with no bound in sight (`max`, `limit`, `attempts <`, `for _ in`).

logging/
- `print-log` — `println!`/`eprintln!` in library code (not `src/bin/`, `main.rs`,
  `build.rs`), or `print(` in a Python module that imports `logging`.
- `bare-log` — an `error`/`warn` log call whose whole argument list is one literal: no
  field, no placeholder, nothing an operator can act on.
- `error-not-logged` — an `error`/`warn` log inside an `Err(e)` arm or an `except ... as
  e:` block that never names the error.
- `secret-in-log` — a log or print call that names a secret-looking identifier
  (`token`, `secret`, `password`, `api_key`, `credential`, `private_key`, `bearer`) and no
  redaction (`redact`, `mask`, `len()`, `is_some`, `is_empty`).

security/ (by path, plus two content kinds)
- `secrets`, `auth`, `ci`, `infra`, `deps`, `migrations` — a changed path that matches the
  category's globs (below), after the repo's own table.
- `exec` — a process or shell started from code: `Command::new(`, `subprocess.run(`
  (and `Popen`/`call`/`check_*`),
  `os.system(`, `sh -c`, `eval `.
- `sql` — SQL built by formatting: `format!(`, an f-string, `%` or `.format(` on a line
  holding SELECT/INSERT/UPDATE/DELETE.

Documentation (`*.md`, `*.pdf`, `docs/`, `.discovery/`) and test paths never trigger a
security category: a doc or a test about secrets is not a trust boundary. `*token*` is
deliberately not an `auth` glob (an LLM codebase counts tokens everywhere); a repo whose
tokens are credentials adds it in its table.

# The repo's trigger table

`.review-triggers.md` at the root of `--root`, a Markdown table: every row is
`| glob | category | why |`. A row adds its glob to the category; category `exempt`
removes every path the glob matches from all categories. A glob is an `fnmatch` pattern
on the repo-relative path (`*` crosses `/`). A row with an empty `why` is refused: the
table records rulings, and a ruling has a reason.

Exit codes: 0 the report printed (markers or none); 2 unusable input (not a git
repository, an unknown base, a table row without a reason).

No third-party imports: this runs wherever python3 does.
"""

from __future__ import annotations

import argparse
import fnmatch
import os
import re
import subprocess
import sys
import tempfile
from collections import Counter, defaultdict

SOURCE_EXT = {".rs", ".py", ".sh", ".bash", ".ts", ".js", ".go"}
TEST_PATH = re.compile(r"(^|/)(tests?|test-support|test_support|spec|__tests__)/|_test\.[a-z]+$|(^|/)test_[^/]*\.py$")
DOC_PATH = re.compile(r"\.(md|pdf|txt|rst)$|(^|/)(docs|\.discovery|\.agent-staging)/")
BIN_PATH = re.compile(r"(^|/)src/bin/|(^|/)(main|build)\.rs$")

SECURITY_GLOBS: dict[str, list[str]] = {
    "secrets": ["*secret*", "*.sops.*", ".sops.yaml", "*vault*", "*openbao*", "*.pem", "*.key",
                ".env*", "*/.env*", "*credential*", "*keyring*"],
    "auth": ["*auth*", "*session*", "*permission*", "*acl*", "*rbac*", "*policy*",
             "*login*", "*jwt*", "*nkey*"],
    "ci": [".github/workflows/*", ".gitlab-ci.yml", "Jenkinsfile", "*/Jenkinsfile", "lefthook.yml",
           ".pre-commit-config.yaml", "bitbucket-pipelines.yml"],
    "infra": ["*.tf", "*.tfvars", "*Dockerfile*", "*compose*.yml", "*compose*.yaml", "deploy/*",
              "infra/*", "*/k8s/*", "*/helm/*"],
    "deps": ["Cargo.toml", "*/Cargo.toml", "Cargo.lock", "package.json", "*/package.json",
             "package-lock.json", "pnpm-lock.yaml", "yarn.lock", "requirements*.txt",
             "pyproject.toml", "poetry.lock", "uv.lock", "go.mod", "go.sum", "deny.toml"],
    "migrations": ["*/migrations/*", "*.sql"],
}
TABLE = ".review-triggers.md"

SECRET_WORD = re.compile(r"\b\w*(token|secret|password|passwd|api_?key|credential|private_key|bearer)\w*\b", re.I)
REDACTED = re.compile(r"redact|mask|\.len\(\)|len\(|is_some|is_none|is_empty|\bhash", re.I)
RUST_LOG = re.compile(r"\b(?:tracing::|log::)?(error|warn|info|debug|trace)!\s*\(")
PY_LOG = re.compile(r"\b(?:log|logger|logging|LOG|_log|_logger)\.(error|warning|warn|exception|info|debug|critical)\s*\(")
PRINT_RS = re.compile(r"\b(e?println)!\s*\(")
PRINT_PY = re.compile(r"(?<![\w.])print\s*\(")


def blank_strings(line: str) -> str:
    """String literals emptied, so a word inside a message is not code."""
    out = re.sub(r'r#*"[^"]*"#*', '""', line)
    out = re.sub(r'"(?:\\.|[^"\\])*"', '""', out)
    return re.sub(r"'(?:\\.|[^'\\])*'", "''", out)


def is_comment(line: str, ext: str) -> bool:
    s = line.lstrip()
    if ext == ".rs" or ext in (".ts", ".js", ".go"):
        return s.startswith("//") or s.startswith("*") or s.startswith("/*")
    return s.startswith("#")


def call_text(lines: list[str], i: int, limit: int = 15) -> str:
    """The call starting on line i, joined until its parentheses balance."""
    depth, parts = 0, []
    for line in lines[i:i + limit]:
        parts.append(line)
        depth += line.count("(") - line.count(")")
        if depth <= 0:
            break
    return " ".join(p.strip() for p in parts)


def log_args(call: str) -> str:
    start = call.find("(")
    return call[start + 1: call.rfind(")")] if start >= 0 else ""


def error_bound_above(lines: list[str], i: int, ext: str) -> str | None:
    """The error variable an enclosing `Err(e)` arm or `except ... as e:` binds, if one
    sits within 6 lines above."""
    for line in reversed(lines[max(0, i - 6):i]):
        m = re.search(r"Err\((\w+)\)", line) if ext == ".rs" else re.search(r"except\b.*\bas\s+(\w+)\s*:", line)
        if m and m.group(1) != "_":
            return m.group(1)
    return None


def markers_for(path: str, lines: list[str], added: set[int]) -> list[tuple[int, str, str]]:
    """(line number, group/kind, line) for every marker on an added line of one file."""
    ext = os.path.splitext(path)[1]
    out: list[tuple[int, str, str]] = []
    imports_logging = ext == ".py" and any(re.match(r"\s*(import logging|from logging )", l) for l in lines)
    for n in sorted(added):
        if n < 1 or n > len(lines):
            continue
        raw = lines[n - 1]
        if not raw.strip() or is_comment(raw, ext):
            continue
        code = blank_strings(raw)

        def mark(kind: str) -> None:
            out.append((n, kind, raw.strip()))

        if ext == ".rs":
            if re.search(r"\bErr\(\s*_\s*\)\s*=>\s*(return\s+)?Err\(", code) or re.search(r"map_err\(\s*\|\s*_\s*\|", code):
                mark("failure/cause-dropped")
            elif re.search(r"\blet\s+_\s*=\s*[^;]*\(", code) or re.search(r"\.ok\(\)\s*;", code) \
                    or re.search(r"\bErr\(\s*_\s*\)\s*=>", code) or re.search(r"\)\s*\.unwrap_or_default\(\)", code):
                mark("failure/swallow")
            if re.search(r"reqwest::(Client::new\(\)|get\()", code) or \
                    (re.search(r"TcpStream::connect\(", code) and "timeout(" not in code):
                mark("failure/timeout")
        elif ext == ".py":
            nxt = blank_strings(lines[n]).strip() if n < len(lines) else ""
            if re.match(r"\s*except\s*:", code) or "contextlib.suppress(" in code or \
                    (re.match(r"\s*except\b", code) and (re.search(r":\s*(pass|\.\.\.)\s*$", code) or nxt in ("pass", "..."))):
                mark("failure/swallow")
            if re.search(r"\braise\b.*\bfrom\s+None\b", code):
                bound = error_bound_above(lines, n - 1, ext)
                if not (bound and re.search(r"\b%s\b" % re.escape(bound), raw)):
                    mark("failure/cause-dropped")  # unless the message carries the error itself
            call = blank_strings(call_text(lines, n - 1))
            if (re.search(r"\brequests\.(get|post|put|delete|patch|head|request)\s*\(", code) or re.search(r"\burlopen\s*\(", code)
                    or re.search(r"\bsubprocess\.(run|check_output|check_call)\s*\(", code)) and "timeout" not in call:
                mark("failure/timeout")
        elif ext in (".sh", ".bash"):
            if re.search(r"\|\|\s*true\b", code):
                mark("failure/swallow")
            if re.search(r"(^\s*|[;&|]\s*|\$\(\s*|\bthen\s+|\bdo\s+)curl\s", code) and not re.search(r"--max-time|\s-m\s|--connect-timeout", raw):
                mark("failure/timeout")
        if re.search(r"^\s*(loop\s*\{|while\s+(True|true)\b)", code):
            window = " ".join(blank_strings(l) for l in lines[n:n + 20])
            if re.search(r"retry|retries|backoff|attempt", window, re.I) and \
                    not re.search(r"\bmax\w*|MAX_\w+|limit|attempts?\s*[<>]=?|for\s+_\s+in|\btake\(", window):
                mark("failure/retry")

        log = RUST_LOG.search(code) if ext == ".rs" else PY_LOG.search(code) if ext == ".py" else None
        printed = PRINT_RS.search(code) if ext == ".rs" else PRINT_PY.search(code) if ext == ".py" else None
        if printed and ((ext == ".rs" and not BIN_PATH.search(path)) or (ext == ".py" and imports_logging)):
            mark("logging/print-log")
        if log or printed:
            call = call_text(lines, n - 1)
            args = blank_strings(log_args(call))
            level = log.group(1) if log else ""
            if level in ("error", "warn", "warning", "exception", "critical"):
                only_literal = re.fullmatch(r'\s*(""|\'\')\s*,?\s*', args) is not None
                placeholder = re.search(r"\{[^}]*\}|%[sdrf]", log_args(call)) is not None
                if only_literal and not placeholder:
                    mark("logging/bare-log")
                bound = error_bound_above(lines, n - 1, ext)
                if bound and not re.search(r"\b%s\b" % re.escape(bound), log_args(call)) and level != "exception":
                    mark("logging/error-not-logged")
            if SECRET_WORD.search(args) and not REDACTED.search(args):
                mark("logging/secret-in-log")

        if re.search(r"\bCommand::new\(|\bsubprocess\.(run|Popen|call|check_output|check_call)\(|\bos\.system\(|\bsh\s+-c\b|^\s*eval\s", code if ext != ".sh" else raw):
            mark("security/exec")
        if re.search(r"\b(SELECT|INSERT|UPDATE|DELETE)\b", raw) and \
                re.search(r"format!\(|\bf[\"']|\.format\(|[\"']\s*%\s*[\w(]", raw):
            mark("security/sql")
    return out


def load_table(root: str) -> tuple[dict[str, list[str]], list[str]]:
    """Category globs after the repo's `.review-triggers.md`, and its exempt globs."""
    globs = {k: list(v) for k, v in SECURITY_GLOBS.items()}
    exempt: list[str] = []
    path = os.path.join(root, TABLE)
    if not os.path.isfile(path):
        return globs, exempt
    with open(path, encoding="utf-8") as fh:
        for raw in fh:
            cells = [c.strip().strip("`") for c in raw.strip().strip("|").split("|")]
            if not raw.lstrip().startswith("|") or len(cells) < 3 or set(cells[0]) <= {"-", ":", " "} or cells[0].lower() == "glob":
                continue
            glob, cat, why = cells[0], cells[1].lower(), cells[2]
            if not why:
                raise ValueError(f"{TABLE}: the row for {glob} has no reason")
            if cat == "exempt":
                exempt.append(glob)
            else:
                globs.setdefault(cat, []).append(glob)
    return globs, exempt


def categorise(paths: list[str], globs: dict[str, list[str]], exempt: list[str]) -> dict[str, list[str]]:
    hits: dict[str, list[str]] = defaultdict(list)
    for p in paths:
        if DOC_PATH.search(p) or TEST_PATH.search(p) or any(fnmatch.fnmatch(p, g) for g in exempt):
            continue
        for cat, gs in globs.items():
            if any(fnmatch.fnmatch(p, g) or fnmatch.fnmatch(os.path.basename(p), g) for g in gs):
                hits[cat].append(p)
    return hits


def git(root: str, *args: str) -> str:
    proc = subprocess.run(["git", "-C", root, *args], capture_output=True, text=True)
    if proc.returncode != 0:
        raise ValueError(proc.stderr.strip() or f"git {' '.join(args)} failed")
    return proc.stdout


def added_lines(root: str, base: str, head: str) -> tuple[list[str], dict[str, set[int]]]:
    """Every path the range touched (deletions excluded), and the added line numbers per path."""
    paths = [p for p in git(root, "diff", "--name-only", "--diff-filter=d", f"{base}..{head}").splitlines() if p]
    added: dict[str, set[int]] = defaultdict(set)
    cur = None
    for line in git(root, "diff", "-U0", "--no-color", "--diff-filter=d", f"{base}..{head}").splitlines():
        if line.startswith("+++ "):
            cur = line[6:] if line.startswith("+++ b/") else None
        elif line.startswith("@@") and cur:
            m = re.search(r"\+(\d+)(?:,(\d+))?", line)
            if m:
                start, count = int(m.group(1)), int(m.group(2) or 1)
                added[cur].update(range(start, start + count))
    return paths, added


def report(root: str, base: str, head: str, cap: int) -> tuple[list[str], list[str]]:
    """(marker lines, summary lines)."""
    paths, added = added_lines(root, base, head)
    globs, exempt = load_table(root)
    markers = []
    scanned = 0
    for p in paths:
        ext = os.path.splitext(p)[1]
        if ext not in SOURCE_EXT or TEST_PATH.search(p) or p not in added:
            continue
        scanned += 1
        try:
            text = git(root, "show", f"{head}:{p}")
        except ValueError:
            continue
        for n, kind, line in markers_for(p, text.splitlines(), added[p]):
            markers.append((p, n, kind, line))
    lines = [f"{p}:{n}  {k}  {l[:120] + ('…' if len(l) > 120 else '')}" for p, n, k, l in markers[:cap]]
    if len(markers) > cap:
        lines.append(f"... {len(markers) - cap} more (raise --max to list them)")

    def summary(group: str, label: str) -> str:
        mine = [m for m in markers if m[2].startswith(group + "/")]
        if not mine:
            return f"{label}: none in {scanned} files"
        kinds = Counter(m[2].split("/")[1] for m in mine)
        files = len({m[0] for m in mine})
        return f"{label}: {len(mine)} markers in {files} files (" + ", ".join(f"{k} {v}" for k, v in kinds.most_common()) + ")"

    sec = categorise(paths, globs, exempt)
    for p, n, k, _ in markers:
        if k.startswith("security/"):
            sec[k.split("/")[1]].append(f"{p}:{n}")
    if sec:
        parts = []
        for cat in sorted(sec):
            uniq = list(dict.fromkeys(sec[cat]))
            shown = ", ".join(uniq[:4]) + (f", +{len(uniq) - 4}" if len(uniq) > 4 else "")
            parts.append(f"{cat} {len(uniq)} ({shown})")
        sec_line = "SECURITY-TRIGGERS: " + "; ".join(parts)
    else:
        sec_line = "SECURITY-TRIGGERS: none"
    return lines, [summary("failure", "FAILURE-SIGNALS"), summary("logging", "LOG-SIGNALS"), sec_line]


def field_block(lines: list[str], summaries: list[str]) -> str:
    body = summaries + (["Marked lines (current state, a reason to look, never a verdict):"] + ["  " + l for l in lines] if lines else [])
    return "SIGNALS: |\n" + "\n".join("  " + b for b in body)


# ---------------------------------------------------------------- selftest

def selftest() -> int:
    failures = []

    def check(name, cond, detail=""):
        print(("PASS  " if cond else "FAIL  ") + name + ("" if cond else f"  {detail}"))
        if not cond:
            failures.append(name)

    rs = [
        'fn a() {',
        '    let _ = fs::remove_file(&p);',
        '    let x = parse(s).ok();',
        '    match r { Err(_) => return None, Ok(v) => v }',
        '    let v = load().unwrap_or_default();',
        '    let e = r.map_err(|_| Error::Bad)?;',
        '    match r { Err(_) => Err(Error::Bad), Ok(v) => Ok(v) }',
        '    let c = reqwest::Client::new();',
        '    tracing::warn!("failed");',
        '    tracing::warn!(error = %e, "failed to load {path}");',
        '    println!("debug {x}");',
        '    tracing::info!(token = %token, "sent");',
        '    tracing::info!(token_len = token.len(), "sent");',
        '    // let _ = ignored(); in a comment',
        '    let msg = "let _ = not code()";',
        '    match r {',
        '        Err(err) => {',
        '            tracing::error!(path = %p, "could not open");',
        '        }',
        '    }',
        '    let q = format!("SELECT * FROM t WHERE id = {id}");',
        '    Command::new("sh").arg("-c");',
        '    loop {',
        '        if try_send().is_ok() { break }',
        '        retry_delay();',
        '    }',
        '}',
    ]
    got = {(n, k) for n, k, _ in markers_for("crates/x/src/lib.rs", rs, set(range(1, len(rs) + 1)))}
    want = {(2, "failure/swallow"), (3, "failure/swallow"), (4, "failure/swallow"), (5, "failure/swallow"),
            (6, "failure/cause-dropped"), (7, "failure/cause-dropped"), (8, "failure/timeout"),
            (9, "logging/bare-log"), (11, "logging/print-log"), (12, "logging/secret-in-log"),
            (18, "logging/error-not-logged"), (21, "security/sql"), (22, "security/exec"), (23, "failure/retry")}
    check("rust: every shape marked, nothing else", got == want, sorted(got ^ want))
    check("rust: only added lines are marked",
          {n for n, _, _ in markers_for("crates/x/src/lib.rs", rs, {2})} == {2})
    check("rust: println in a binary is not a print-log",
          not any(k == "logging/print-log" for _, k, _ in markers_for("crates/x/src/bin/tool.rs", rs, {11})))
    bounded = ['loop {', '    attempts += 1;', '    if attempts >= MAX_ATTEMPTS { break }', '    retry();', '}']
    check("rust: a bounded retry loop is not marked", not markers_for("a.rs", bounded, {1}))

    py = [
        'import logging, subprocess, requests',
        'log = logging.getLogger(__name__)',
        'try:',
        '    go()',
        'except Exception:',
        '    pass',
        'try:',
        '    go()',
        'except OSError as e:',
        '    log.error("could not go")',
        '    raise RuntimeError("bad") from None',
        'requests.get(url)',
        'requests.get(url, timeout=5)',
        'subprocess.run(["x"],',
        '               check=True)',
        'print("done")',
        'log.warning("token %s", api_token)',
    ]
    got = {(n, k) for n, k, _ in markers_for("scripts/tool.py", py, set(range(1, len(py) + 1)))}
    want = {(5, "failure/swallow"), (11, "failure/cause-dropped"), (10, "logging/bare-log"),
            (10, "logging/error-not-logged"), (12, "failure/timeout"), (14, "failure/timeout"),
            (14, "security/exec"), (16, "logging/print-log"), (17, "logging/secret-in-log")}
    check("python: every shape marked, nothing else", got == want, sorted(got ^ want))

    sh = ['rm -rf "$d" || true', 'curl -fsS "$u"', 'curl --max-time 10 "$u"', 'pkgs=(curl python3)']
    got = {(n, k) for n, k, _ in markers_for("deploy/x.sh", sh, {1, 2, 3, 4})}
    check("shell: swallow and an unbounded curl", got == {(1, "failure/swallow"), (2, "failure/timeout")}, got)

    with tempfile.TemporaryDirectory() as tmp:
        globs, exempt = load_table(tmp)
        cats = categorise(["deploy/secrets/seed.sh", ".github/workflows/ci.yml", "Cargo.lock",
                           "docs/adr/0001-auth.md", "crates/protocol/migrations/0003.sql", "src/lib.rs",
                           "crates/platform/tests/nats_auth_live_test.rs", "src/token_ledger.rs"], globs, exempt)
        check("paths land in their categories, docs never",
              cats == {"secrets": ["deploy/secrets/seed.sh"], "infra": ["deploy/secrets/seed.sh"],
                       "ci": [".github/workflows/ci.yml"], "deps": ["Cargo.lock"],
                       "migrations": ["crates/protocol/migrations/0003.sql"]}, dict(cats))
        with open(os.path.join(tmp, TABLE), "w") as fh:
            fh.write("| glob | category | why |\n|---|---|---|\n"
                     "| crates/sandbox-tool/* | auth | the sandbox is a trust boundary |\n"
                     "| deploy/fake-llm/* | exempt | a test double, never deployed |\n")
        globs, exempt = load_table(tmp)
        cats = categorise(["crates/sandbox-tool/src/run.rs", "deploy/fake-llm/app.py"], globs, exempt)
        check("the repo table adds and exempts", cats == {"auth": ["crates/sandbox-tool/src/run.rs"]}, dict(cats))
        with open(os.path.join(tmp, TABLE), "a") as fh:
            fh.write("| src/* | auth |  |\n")
        try:
            load_table(tmp)
            check("a row without a reason is refused", False)
        except ValueError:
            check("a row without a reason is refused", True)

        repo = os.path.join(tmp, "repo")
        os.makedirs(os.path.join(repo, "src"))
        run = lambda *a: subprocess.run(["git", "-C", repo, *a], capture_output=True, check=True)
        run("init", "-q")
        run("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "base")
        with open(os.path.join(repo, "src", "lib.rs"), "w") as fh:
            fh.write("fn a() {\n    let _ = go();\n}\n")
        os.makedirs(os.path.join(repo, "tests"))
        with open(os.path.join(repo, "tests", "a_test.rs"), "w") as fh:
            fh.write("fn t() { let _ = go(); }\n")
        with open(os.path.join(repo, "Cargo.toml"), "w") as fh:
            fh.write("[package]\n")
        run("add", "-A")
        run("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "change")
        lines, sums = report(repo, "HEAD~1", "HEAD", 40)
        check("a range: the source marker, tests skipped", lines == ["src/lib.rs:2  failure/swallow  let _ = go();"], lines)
        check("a range: the summaries", sums == ["FAILURE-SIGNALS: 1 markers in 1 files (swallow 1)",
                                                 "LOG-SIGNALS: none in 1 files",
                                                 "SECURITY-TRIGGERS: deps 1 (Cargo.toml)"], sums)
        block = field_block(lines, sums)
        check("the payload field is one block", block.startswith("SIGNALS: |\n  FAILURE-SIGNALS:") and "  src/lib.rs:2" in block, block)
        lines, _ = report(repo, "HEAD~1", "HEAD", 0)
        check("a cap never drops silently", lines == ["... 1 more (raise --max to list them)"], lines)
    print(f"selftest: {len(failures)} failure(s)")
    return 1 if failures else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--root")
    ap.add_argument("--base")
    ap.add_argument("--head", default="HEAD")
    ap.add_argument("--max", type=int, default=40)
    ap.add_argument("--field", action="store_true", help="print the SIGNALS payload block")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)
    if args.selftest:
        return selftest()
    if not args.root or not args.base:
        print("review_signals: --root and --base are required", file=sys.stderr)
        return 2
    try:
        lines, sums = report(os.path.abspath(args.root), args.base, args.head, args.max)
    except ValueError as err:
        print(f"review_signals: {err}", file=sys.stderr)
        return 2
    print(field_block(lines, sums) if args.field else "\n".join(lines + sums))
    return 0


if __name__ == "__main__":
    sys.exit(main())
