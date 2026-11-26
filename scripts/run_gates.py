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
found on PATH. Two more optional trailing annotations, in any order with it:
` [paths: glob,glob]` names the files whose change can alter the gate (see "Path-affected
gates") and ` [kind: compile|lint|format|test]` classifies it (see "The contract commit").
Gate names must be unique and match `[A-Za-z0-9_.-]+`.

    lint: ruff check .
    unit: pytest -q tests/unit
    e2e: npm run test:e2e [needs: npx]
    # a comment
    fmt: cargo fmt --check

# Usage

    run_gates.py GATES_FILE [--root DIR] [--only NAME[,NAME]] [--list]
                 [--log-dir DIR] [--jobs N=1] [--lines N=15]
                 [--line-chars N=300] [--timeout SECONDS]
                 [--path-prepend DIR ...] [--env-script FILE] [--detach]
                 [--affected-since REV] [--compile-lint-only] [--fresh]
    run_gates.py --wait ID [--wait-cap SECONDS=270]
    run_gates.py --from-workflow FILE [FILE...]
    run_gates.py --selftest

`--from-workflow` prints a gates file derived from GitHub Actions workflows'
`run:` steps (see "From a CI workflow" below) and runs nothing. `--list` prints the parsed gates (name, command, needs) and runs nothing.
`--only` restricts the run to the named gates (comma list); an unknown name
is refused. `--jobs N` runs gates concurrently in a thread pool (default 1,
sequential); output is still printed in the gates file's order regardless of
finishing order, so a rerun's transcript reads the same way twice.
A failing gate's excerpt is parsed first: rustc/clippy errors and warnings
(`file:line  error[CODE]: message`, each distinct one once, errors before
warnings), rustfmt diffs per file, ruff, tsc, mypy and eslint findings, and any
failing test through `run_tests.parse_log` (`tests: passed N failed M`, then
`test NAME  file:line first-assertion`). Output that parses to nothing falls
back to the matching lines below. At most `--lines` items print; a last line
`(+K more, truncated: open the log)` says the log holds more, and the log is
opened only then. A gate's `FAIL` line keeps its log path.
`--log-dir` picks where full per-gate logs land (default: a fresh temp
directory, whose path is printed once). `--lines` and `--line-chars` bound
the failure excerpt: up to N lines that match
`DEFECT|FAIL|ERROR|error|Error|panicked|not ok|✗`, or the last N lines when
none match, each cut to `--line-chars` characters. `--timeout` caps each
gate's wall time; a gate that times out is reported FAIL.

# The toolchain on every gate's PATH

`--path-prepend DIR` (repeatable; the first given is first on PATH) and
`--env-script FILE` mean what they mean in `run_tests.py`: DIR is prepended to
the PATH of every gate, and FILE is sourced (`. FILE && <gate>`) before every
gate. `$HOME`, `$VAR` and a leading `~/` in DIR expand here. A `[needs: tool]`
check looks on the same PATH. A repository whose toolchain is only on an
interactive PATH (cargo under `~/.cargo/bin`) runs green with
`--path-prepend "$HOME/.cargo/bin"`, with no `export PATH=...` in any line. A
gate that exits 127 is still a failure, but the summary counts it apart:
"command not found — environment, not a gate failure".

There is no `--vm`: a gate that must run in the VM carries its own
`limactl shell NAME -- ...` in its line, or the list runs inside the VM.

# Path-affected gates: --affected-since REV
Every push used to run the whole list. `--affected-since REV` diffs the working tree
(tracked, staged, unstaged and untracked files, the dossier build log excluded)
against commit REV and runs only the gates a changed path touches. A gate knows its
paths from its `[paths: glob,glob]` annotation (a glob without `/` matches a basename
at any depth, a trailing `/` is a directory, `**` crosses directories), else from its
command when that is one well-known tool (`cargo`, `ruff`, `mypy`, `pytest`, `tsc`,
`eslint`, `go` ... in `a && b` chains; scripts, pipes and `make` are not). Every gate
left out prints `SKIP name — skipped: no changed paths`, never silently. Fail safe: a
changed path no path-known gate claims, or a REV git cannot resolve, runs EVERY gate
(a line says so); a gate with no known paths always runs. `--list` shows each gate's
paths and kind. REV is the last head the full list passed on (the first push's head).
In this mode a gate that already passed on the same inputs is not rerun: it prints
`REUSED name — green receipt ...`. Per-gate receipts sit beside the whole-list ones in
the worktree's git dir (`run_gates-receipts/gates/`), keyed by gate name, command and a
hash of the tree entries its paths claim (the whole tree for a gate with no paths); a
pass writes one only when the tree is unchanged after the run. `--fresh` ignores them.
Without `--affected-since` the list runs in full and per-gate receipts are not read:
that is the mode for the first push of a branch and the final head before the PR.
# The contract commit: --compile-lint-only
A stub commit has bodies that fail loudly, so only compile and lint can be judged.
`--compile-lint-only` runs the gates whose kind is compile or lint and reports the rest
as `SKIP name — skipped: <kind> gate, the contract commit compiles and lints only`. The
kind comes from `[kind:]`, else the tool and subcommand (`cargo clippy` lint, `cargo
check` compile, `cargo fmt` format, `cargo test` test), else the gate's name; a gate
whose kind is still unknown runs. Neither flag writes the whole-list receipt.
# Long runs: --detach, then --wait

`--detach` starts the same run (every other flag kept) in its own session,
SIGHUP ignored, stdio detached, so it survives the calling shell or tool call
returning. It prints the run id, the state directory, the receipt path and the
exact `--wait` command, and exits 0 at once. The state directory
(`$RUN_GATES_STATE_DIR`, default `<tmp>/run_gates-detach/<id>/`) holds
`state.json`, `out.txt` (what a foreground run prints) and, once the run ends,
`exit` (its exit code).

`--wait ID` blocks up to `--wait-cap` seconds (default 270, or $ASD_WAIT_CAP: under
the 5-minute prompt cache of a sub-agent, which goes cold in a longer block; an
orchestrator may pass a longer cap), then prints `out.txt` — the same summary a foreground run prints —
and exits with the run's own exit code. Still running at the cap: it prints
`run_gates: run ID still running ...` and exits 6; call it again. 125: the run
died without writing an exit code. 2: no such run.

# Receipts: a green run is never repeated on the same tree

A full run (no `--only`, `--affected-since` or `--compile-lint-only`) that passes writes a receipt into the worktree's own
git dir (`git rev-parse --git-path run_gates-receipts/<gates-sha>.json`): the
gates file's sha256, a fingerprint of the working tree, HEAD, the passed and
skipped gate names, and the time. The next run of the same gates file on the
same tree reuses it and runs nothing: it prints `REUSED <n> gates — receipt
<time> on <head>` and the usual summary line with `(reused)`, exit 0. The
case it exists for: a fix-mode implementer runs the gates in the integration
worktree and commits, and the orchestrator's own pre-push gate run on that
head used to repeat the whole list minutes later.

The fingerprint is the tree `git write-tree` builds from the working tree
through a throwaway index — tracked and untracked files, `.gitignore`
honoured — minus `.discovery/dossiers/`. The build log lives in the dossier
and the orchestrator writes it between every step, a `RETURNED` line
included, and no gate derived from CI judges it; every other path counts,
`.discovery/analysis/` too. So any edit, commit, merge or new file outside the
dossiers changes the fingerprint and the gates run again. A receipt is also
refused when a gate it skipped for a missing tool would run now. `--fresh`
ignores receipts; `--receipt` checks for one without running anything (exit 0
a matching green receipt, 4 none). Outside a git worktree no receipt is
written or read.

Exit codes: 0 all gates passed (skips allowed); 1 at least one gate failed;
2 unusable input (bad gates file, duplicate or malformed gate name, unknown
`--only` name, a workflow `--from-workflow` cannot parse); 3 the worktree has
a merge, rebase, cherry-pick or bisect in progress (MERGE_HEAD, rebase-merge/,
rebase-apply/, CHERRY_PICK_HEAD, BISECT_LOG in `git rev-parse --git-path`):
a gate read a half-mutated tree once, so the run refuses to start. `--list`
and `--from-workflow` do not check. 4 `--receipt` found no matching green
receipt. 6 `--wait` hit its cap with the run still going; 125 a detached run died
without an exit code.

# From a CI workflow

`--from-workflow .github/workflows/ci.yml [more.yml]` writes the gates file
to stdout: one gate per single-line `run:` step, and per `run: |` block step
when it is safe to flatten (plain commands; backslash, `&&`, `||` and `|` continuations
joined; it runs as `set -e; ...`). Workflow, job and step `env:` scalars
become an `export` prefix and `working-directory:` a `(cd DIR && ...)`
prefix. Names are `<job>-<step name slug>`. A step is never silently dropped:
jobs with `services:`/`container:`/a reusable `uses:`,
`github.*` conditions and `secrets.*` are CI-only and only counted (an `env:` var whose value is a `${{ }}` expression is dropped and counted too), on the one
`# from-workflow:` summary line (also printed to stderr); a `uses:` step is one of
three: a checker action with a local twin (`CHECKER_TWINS`, e.g. `crate-ci/typos`
becomes the gate `typos .`), a setup/cache/checkout/upload/download action (only
counted), or any other action, listed as `# skipped: job/step - uses: ACTION ...`
so it is never silent; a step it cannot
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
import hashlib
import json
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build_slots  # noqa: E402

NAME_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
NEEDS_RE = re.compile(r"^(?P<rest>.*)\[needs:\s*(?P<tool>[^\]]+)\]\s*$")
FAILURE_RE = re.compile(r"DEFECT|FAIL|ERROR|error|Error|panicked|not ok|✗")

GIT_STATE = ("MERGE_HEAD", "rebase-merge", "rebase-apply", "CHERRY_PICK_HEAD", "BISECT_LOG")
EXIT_GIT_STATE = 3
EXIT_NO_RECEIPT = 4
RECEIPT_IGNORED = ".discovery/dossiers"


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


def _git(root: str, *args: str, env: dict | None = None) -> tuple[int, str]:
    try:
        proc = subprocess.run(["git", "-C", root, *args], capture_output=True, text=True,
                              check=False, env=env)
    except OSError:
        return 1, ""
    return proc.returncode, proc.stdout.strip()


def tree_fingerprint(root: str) -> tuple[str, str] | None:
    """(tree sha of the working tree minus RECEIPT_IGNORED, HEAD sha), or None outside a git worktree.

    Built through a throwaway index so the real index is never touched."""
    rc, head = _git(root, "rev-parse", "--verify", "--quiet", "HEAD")
    if rc != 0 or not head:
        return None
    fd, index = tempfile.mkstemp(prefix="run_gates-index-")
    os.close(fd)
    env = dict(os.environ, GIT_INDEX_FILE=index)
    try:
        steps = (("read-tree", "HEAD"), ("add", "-A", "--", "."),
                 ("rm", "-r", "-q", "--cached", "--ignore-unmatch", "--", RECEIPT_IGNORED))
        for step in steps:
            if _git(root, *step, env=env)[0] != 0:
                return None
        rc, tree = _git(root, "write-tree", env=env)
        return (tree, head) if rc == 0 and tree else None
    finally:
        with contextlib.suppress(OSError):
            os.remove(index)


def receipt_path(root: str, gates_sha: str) -> str | None:
    rc, path = _git(root, "rev-parse", "--git-path", f"run_gates-receipts/{gates_sha[:16]}.json")
    if rc != 0 or not path:
        return None
    return path if os.path.isabs(path) else os.path.join(root, path)


def matching_receipt(root: str, gates_sha: str, fingerprint: tuple[str, str] | None) -> dict | None:
    """The green receipt for this gates file on this tree, else None."""
    if fingerprint is None:
        return None
    path = receipt_path(root, gates_sha)
    if not path:
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            rec = json.load(fh)
    except (OSError, ValueError):
        return None
    if rec.get("gates_sha256") != gates_sha or rec.get("tree") != fingerprint[0]:
        return None
    if any(shutil.which(tool) for tool in rec.get("skipped_needs", [])):
        return None  # a gate skipped then would run now
    return rec


def write_receipt(root: str, gates_sha: str, fingerprint: tuple[str, str], passed: list[str],
                  skipped: list[dict]) -> str | None:
    path = receipt_path(root, gates_sha)
    if not path:
        return None
    rec = {"gates_sha256": gates_sha, "tree": fingerprint[0], "head": fingerprint[1],
           "passed": passed, "skipped": [g["name"] for g in skipped],
           "skipped_needs": sorted({g["needs"] for g in skipped}),
           "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(rec, fh)
    except OSError:
        return None
    return path


# ---------------------------------------------------------------------------
# Path-affected gates and per-gate receipts.
#
# A gate is `path-known` when its line carries `[paths: glob,glob]` or its command is one
# well-known tool invocation (INFERRED_PATHS); every other gate is `path-unknown` and always
# runs. A changed path no path-known gate claims makes EVERY gate run (fail safe).
RUST_PATHS = ["*.rs", "Cargo.toml", "Cargo.lock", "build.rs", "rust-toolchain*", ".cargo/**", "clippy.toml", ".clippy.toml", "rustfmt.toml", ".rustfmt.toml", "deny.toml"]
PY_PATHS = ["*.py", "*.pyi", "pyproject.toml", "setup.cfg", "setup.py", "tox.ini", "ruff.toml", ".ruff.toml", "mypy.ini", ".mypy.ini", "pyrightconfig.json", ".flake8", "requirements*.txt", "uv.lock", "poetry.lock"]
JS_PATHS = ["*.ts", "*.tsx", "*.js", "*.jsx", "*.mjs", "*.cjs", "*.mts", "*.cts", "package.json", "package-lock.json", "pnpm-lock.yaml", "yarn.lock", "bun.lock*", "tsconfig*.json", ".eslintrc*", "eslint.config.*", ".prettierrc*", "prettier.config.*", "biome.json*"]
GO_PATHS = ["*.go", "go.mod", "go.sum", "go.work", ".golangci*"]
DOTNET_PATHS = ["*.cs", "*.csproj", "*.sln", "*.slnx", "*.props", "*.targets", "*.resx", "*.editorconfig", ".editorconfig",
                "global.json", "nuget.config", "NuGet.Config", "packages.lock.json", "appsettings*.json", "*.runsettings"]
TF_PATHS = ["*.tf", "*.tfvars", "*.tf.json", "*.tftest.hcl", ".terraform.lock.hcl", ".terraform-version"]
INFERRED_PATHS = {
    "cargo": RUST_PATHS, "rustfmt": RUST_PATHS, "rustc": RUST_PATHS,
    "ruff": PY_PATHS, "mypy": PY_PATHS, "pyright": PY_PATHS, "black": PY_PATHS, "isort": PY_PATHS,
    "flake8": PY_PATHS, "pylint": PY_PATHS, "pytest": PY_PATHS,
    "tsc": JS_PATHS, "eslint": JS_PATHS, "prettier": JS_PATHS, "biome": JS_PATHS,
    "go": GO_PATHS, "gofmt": GO_PATHS, "golangci-lint": GO_PATHS,
    "dotnet": DOTNET_PATHS, "terraform": TF_PATHS, "tofu": TF_PATHS,
}
# Paths the pipeline itself writes (dossiers, analysis, staging): no tool gate reads them, so they never make every
# gate run; a gate that reads one (a dossier-state checker) is path-unknown and runs anyway.
PIPELINE_PATHS = [".discovery/**", ".agent-staging/**"]
EXPORT_PREFIX_RE = re.compile(r"""^\s*(?:export\s+[A-Za-z_][A-Za-z0-9_]*=(?:'[^']*'|"[^"$`\\]*"|[^\s;|&<>$`()'"]*)\s*;\s*)+""")
INFERRED_KINDS = {"clippy": "lint", "check": "compile", "build": "compile", "doc": "compile", "fmt": "format",
                  "format": "format", "validate": "compile",
                  "test": "test", "nextest": "test", "vet": "lint"}
TOOL_KINDS = {"ruff": "lint", "mypy": "compile", "pyright": "compile", "tsc": "compile", "eslint": "lint",
              "flake8": "lint", "pylint": "lint", "golangci-lint": "lint", "black": "format", "isort": "format",
              "prettier": "format", "gofmt": "format", "rustfmt": "format", "pytest": "test"}
NAME_KIND_RE = (("test", re.compile(r"(^|[-_.])(test|tests|unit|e2e|spec|integration|bats|nextest)([-_.]|$)")),
                ("format", re.compile(r"(^|[-_.])(fmt|format|prettier|black)([-_.]|$)")),
                ("lint", re.compile(r"(^|[-_.])(lint|clippy|eslint|ruff|vet)([-_.]|$)")),
                ("compile", re.compile(r"(^|[-_.])(build|check|compile|typecheck|types|tsc|mypy|doc|docs)([-_.]|$)")))


def _segments(command: str) -> list[list[str]] | None:
    """Token lists of a plain `a && b` command, None when it holds other shell syntax. Leading
    `export NAME=value;` statements (from-workflow writes a job's env that way) only set the
    environment, so they are dropped first."""
    command = EXPORT_PREFIX_RE.sub("", command)
    if re.search(r"[|;<>`$()\n]|\|\|", command):
        return None
    try:
        return [shlex.split(seg) for seg in command.split("&&")]
    except ValueError:
        return None


def _tool_words(tokens: list[str]) -> list[str]:
    while tokens and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", tokens[0]):
        tokens = tokens[1:]  # leading VAR=value
    if tokens and os.path.basename(tokens[0]) in ("python", "python3") and tokens[1:2] == ["-m"]:
        tokens = tokens[2:]
    elif tokens and tokens[0] in ("npx", "bunx", "pnpx"):
        tokens = [t for t in tokens[1:] if t != "--"]
    elif tokens[:2] in (["pnpm", "exec"], ["yarn", "exec"], ["npm", "exec"]):
        tokens = [t for t in tokens[2:] if t != "--"]
    return tokens


def infer_paths(command: str) -> list[str] | None:
    """Globs a well-known single-tool command reads, else None (path-unknown: always runs)."""
    segs = _segments(command)
    if not segs:
        return None
    out: list[str] = []
    for tokens in segs:
        words = _tool_words(tokens)
        if not words or os.path.basename(words[0]) not in INFERRED_PATHS:
            return None
        globs = INFERRED_PATHS[os.path.basename(words[0])]
        # a path argument outside the tool's own files (`cargo run -- deploy/x.yml`, a script) is read too: path-unknown
        if any("/" in w and not w.startswith("-") and not path_matches(w[2:] if w.startswith("./") else w, globs)
               for w in words[1:]):
            return None
        out += [g for g in globs if g not in out]
    return out


def infer_kind(gate: dict) -> str | None:
    """compile / lint / format / test, from `[kind:]`, else the command's tool and subcommand, else the name."""
    if gate.get("kind"):
        return gate["kind"]
    segs = _segments(gate["command"])
    kinds: set[str] = set()
    for tokens in segs or []:
        words = _tool_words(tokens)
        if not words:
            continue
        tool = os.path.basename(words[0])
        if tool in ("cargo", "go", "dotnet", "terraform", "tofu"):
            sub = next((w for w in words[1:] if not w.startswith(("-", "+"))), "")
            k = INFERRED_KINDS.get(sub)
            if tool == "go" and sub == "test":
                k = "test"
        else:
            k = TOOL_KINDS.get(tool)
        if k is None:
            kinds = set()
            break
        kinds.add(k)
    if len(kinds) == 1:
        return next(iter(kinds))
    for k, rx in NAME_KIND_RE:
        if rx.search(gate["name"]):
            return k
    return None


def glob_regex(pat: str) -> re.Pattern:
    """gitignore-flavoured: no `/` matches a basename anywhere; trailing `/` a directory; `**` crosses `/`."""
    pat = pat.strip().lstrip("/")
    if pat.startswith("./"):
        pat = pat[2:]
    if pat.endswith("/"):
        pat += "**"
    anywhere = "/" not in pat
    out, i = "", 0
    while i < len(pat):
        if pat.startswith("**/", i):
            out += "(?:.*/)?"
            i += 3
        elif pat.startswith("**", i):
            out += ".*"
            i += 2
        elif pat[i] == "*":
            out += "[^/]*"
            i += 1
        elif pat[i] == "?":
            out += "[^/]"
            i += 1
        else:
            out += re.escape(pat[i])
            i += 1
    return re.compile(("(?:.*/)?" if anywhere else "") + out + r"\Z")


def path_matches(path: str, globs: list[str]) -> bool:
    return any(glob_regex(g).match(path) for g in globs)


def resolve_gate_paths(gates: list[dict]) -> None:
    """Fill each gate's `paths` from its command when the line declares none."""
    for g in gates:
        if g.get("paths") is None:
            inferred = infer_paths(g["command"])
            if inferred:
                g["paths"], g["paths_src"] = inferred, "inferred"


def _git_raw(root: str, *args: str) -> str | None:
    try:
        proc = subprocess.run(["git", "-C", root, *args], capture_output=True, text=True, check=False)
    except OSError:
        return None
    return proc.stdout if proc.returncode == 0 else None


def changed_paths(root: str, rev: str, tree: str) -> list[str] | None:
    """Paths whose content differs between commit `rev` and the working-tree `tree` (untracked included,
    the dossier build log excluded), None when git cannot say."""
    if _git(root, "rev-parse", "--verify", "--quiet", rev + "^{commit}")[0] != 0:
        return None
    raw = _git_raw(root, "diff-tree", "-r", "--name-only", "-z", "--no-renames", rev, tree)
    if raw is None:
        return None
    return sorted(p for p in raw.split("\0") if p and not p.startswith(RECEIPT_IGNORED + "/"))


def select_affected(gates: list[dict], changed: list[str]) -> tuple[dict[str, str], str]:
    """({gate name: skip reason} for every gate no changed path touches, note).
    A changed path claimed by no path-known gate runs everything: empty skip map."""
    known = [g for g in gates if g.get("paths")]
    unclaimed = [p for p in changed if not path_matches(p, PIPELINE_PATHS)
                 and not any(path_matches(p, g["paths"]) for g in known)]
    if unclaimed:
        more = f" (+{len(unclaimed) - 1} more)" if len(unclaimed) > 1 else ""
        return {}, f"changed path {unclaimed[0]!r}{more} maps to no gate; running every gate"
    skips = {g["name"]: "skipped: no changed paths" for g in known
             if not any(path_matches(p, g["paths"]) for p in changed)}
    return skips, f"{len(changed)} changed path(s)"


def select_contract(gates: list[dict]) -> tuple[dict[str, str], list[str]]:
    """The contract (stub) commit compiles and lints only: ({gate name: skip reason}, names of gates run
    because their kind is unknown, which fails safe to running)."""
    skips: dict[str, str] = {}
    unknown: list[str] = []
    for g in gates:
        kind = infer_kind(g)
        if kind is None:
            unknown.append(g["name"])
        elif kind not in ("compile", "lint"):
            skips[g["name"]] = f"skipped: {kind} gate, the contract commit compiles and lints only"
    return skips, unknown


def gate_inputs_hash(gate: dict, fingerprint: tuple[str, str], entries: list[tuple[str, str]]) -> str:
    """What a gate's pass depends on: the tree entries its paths claim, else the whole tree."""
    if not gate.get("paths"):
        return "tree:" + fingerprint[0]
    h = hashlib.sha256()
    for path, ident in entries:
        if path_matches(path, gate["paths"]):
            h.update(f"{ident} {path}\n".encode())
    return "paths:" + h.hexdigest()


def tree_entries(root: str, tree: str) -> list[tuple[str, str]]:
    raw = _git_raw(root, "ls-tree", "-r", "-z", tree)
    out = []
    for rec in (raw or "").split("\0"):
        meta, _, path = rec.partition("\t")
        if path and not path.startswith(RECEIPT_IGNORED + "/"):
            out.append((path, meta))
    return out


def gate_receipt_file(root: str, gate: dict, inputs: str) -> str | None:
    key = hashlib.sha256(json.dumps([gate["name"], gate["command"], inputs]).encode()).hexdigest()[:24]
    rc, path = _git(root, "rev-parse", "--git-path", f"run_gates-receipts/gates/{key}.json")
    if rc != 0 or not path:
        return None
    return path if os.path.isabs(path) else os.path.join(root, path)


def gate_receipt(root: str, gate: dict, inputs: str) -> dict | None:
    path = gate_receipt_file(root, gate, inputs)
    try:
        with open(path or "", encoding="utf-8") as fh:
            rec = json.load(fh)
    except (OSError, ValueError):
        return None
    ok = rec.get("gate") == gate["name"] and rec.get("command") == gate["command"] and rec.get("inputs") == inputs
    return rec if ok else None


def write_gate_receipt(root: str, gate: dict, inputs: str, head: str) -> None:
    path = gate_receipt_file(root, gate, inputs)
    if not path:
        return
    rec = {"gate": gate["name"], "command": gate["command"], "inputs": inputs, "head": head,
           "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    with contextlib.suppress(OSError):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(rec, fh)


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


# `uses:` steps that check the source have a local twin; a gate list built from `run:` steps alone
# misses them (a spelling check went red in CI with 105 green local gates). Setup, cache, checkout,
# upload and download actions are only counted; any other action is listed as skipped by name.
CHECKER_TWINS = {"crate-ci/typos": "typos ."}
SETUP_ACTION = re.compile(
    r"^actions/|checkout|cache|setup|install|toolchain|upload|download|artifact|login|auth|configure|"
    r"^docker/|^dtolnay/rust-toolchain|^swatinem/|^codecov/|^github/codeql|slack|notify|release|publish|deploy|"
    r"^peaceiris/|^softprops/|^mozilla-actions/", re.I)


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
                    action = st["uses"].split("@")[0].strip() if isinstance(st["uses"], str) else ""
                    twin = CHECKER_TWINS.get(action.lower())
                    ucond = st.get("if")
                    if twin and not (isinstance(ucond, str) and "github." in ucond):
                        base = _slug(f"{job}-{st['name']}" if isinstance(st.get("name"), str) and st.get("name") else f"{job}-{action}")
                        name, nn = base, 2
                        while name in names:
                            name, nn = f"{base}-{nn}", nn + 1
                        names.add(name)
                        gates.append((name, twin))
                    elif action and not twin and not SETUP_ACTION.search(action) and "run" not in st:
                        unsafe.append(f"# skipped: {label} - uses: {action} reads the source but has no local twin in CHECKER_TWINS")
                        count("uses")
                    else:
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


ANNOT_RE = re.compile(r"^(?P<rest>.*)\[(?P<key>needs|paths|kind):\s*(?P<val>[^\]]+)\]\s*$")
KINDS = ("compile", "lint", "format", "test")


def parse_gates(text: str) -> list[dict]:
    gates: list[dict] = []
    seen: set[str] = set()
    for lineno, raw in enumerate(text.splitlines(), 1):
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        annot: dict[str, str] = {}
        while True:  # trailing `[needs: T]`, `[paths: G,G]`, `[kind: K]`, in any order
            m = ANNOT_RE.match(stripped)
            if not m or m.group("key") in annot:
                break
            annot[m.group("key")] = m.group("val").strip()
            stripped = m.group("rest").strip()
        needs = annot.get("needs")
        name, sep, command = stripped.partition(":")
        name = name.strip()
        command = command.strip()
        if not sep or not name or not command or not NAME_RE.match(name):
            raise GateFileError(f"line {lineno}: malformed gate line: {raw!r}")
        if name in seen:
            raise GateFileError(f"line {lineno}: duplicate gate name {name!r}")
        kind = annot.get("kind")
        if kind is not None and kind not in KINDS:
            raise GateFileError(f"line {lineno}: [kind: {kind}] is not one of {', '.join(KINDS)}")
        paths = [x.strip() for x in annot["paths"].split(",") if x.strip()] if "paths" in annot else None
        if "paths" in annot and not paths:
            raise GateFileError(f"line {lineno}: empty [paths: ] list")
        seen.add(name)
        gates.append({"name": name, "command": command, "needs": needs, "line": lineno,
                      "paths": paths, "paths_src": "declared" if paths else None, "kind": kind})
    if not gates:
        raise GateFileError("the gates file names no gate")
    return gates


def gate_env(path_prepend: list[str] | None) -> dict:
    """The environment every gate runs in: this process's, with each
    --path-prepend DIR (`$HOME`, `~/` expanded) in front of PATH, in order."""
    env = dict(os.environ)
    if path_prepend:
        dirs = [os.path.expandvars(os.path.expanduser(d)) for d in path_prepend]
        env["PATH"] = os.pathsep.join(dirs + [env.get("PATH", "")]).rstrip(os.pathsep)
    return env


def gate_script(command: str, env_script: str | None) -> str:
    """The `bash -c` script for one gate: the command, behind `. ENV-SCRIPT &&`
    when --env-script is given (the same shape run_tests.py builds)."""
    if not env_script:
        return command
    return f". {shlex.quote(os.path.expanduser(env_script))} && {{ {command}\n}}"


def run_gate(command: str, root: str, timeout: float | None,
             env: dict | None = None, env_script: str | None = None) -> dict:
    start = time.perf_counter()
    try:
        proc = subprocess.run(
            ["bash", "-c", gate_script(command, env_script)], cwd=root, capture_output=True,
            text=True, timeout=timeout, check=False, env=env,
        )
        return {"rc": proc.returncode, "output": proc.stdout + proc.stderr,
                "elapsed": time.perf_counter() - start, "timed_out": False}
    except subprocess.TimeoutExpired as err:
        # TimeoutExpired can carry raw bytes even under text=True.
        text = lambda v: v.decode("utf-8", "replace") if isinstance(v, bytes) else (v or "")
        out = text(err.stdout) + text(err.stderr)
        return {"rc": None, "output": out, "elapsed": time.perf_counter() - start, "timed_out": True}


ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
RUSTC_DIAG = re.compile(r"^(error|warning)(?:\[([A-Za-z0-9_]+)\])?: (.+)\n\s*--> (\S+)\s*$", re.M)
LINT_NAME = re.compile(r"(clippy::[a-z0-9_-]+|#\[(?:warn|deny)\(([a-z0-9_:]+)\)\])")
RUSTFMT_DIFF = re.compile(r"^Diff in (\S+?)(?: at line |:)(\d+):?\s*$", re.M)
RUFF_CONCISE = re.compile(r"^(\S+?):(\d+):\d+: ([A-Z]{1,4}\d{2,4}) (?:\[\*\] )?(.*)$", re.M)
RUFF_FULL = re.compile(r"^([A-Z]{1,4}\d{3,4}) (?:\[\*\] )?(.+)\n\s*--> (\S+?:\d+)(?::\d+)?\s*$", re.M)
WOULD_REFORMAT = re.compile(r"^Would reformat: (\S+)", re.M)
TSC_ERR = re.compile(r"^(\S+?)\((\d+),\d+\): error (TS\d+): (.*)$", re.M)
MYPY_ERR = re.compile(r"^(\S+?):(\d+): error: (.*)$", re.M)
ESLINT_ERR = re.compile(r"^\s+(\d+):\d+\s+error\s+(.*?)\s{2,}(\S+)\s*$")


def structured_items(output: str) -> list[str]:
    """What a failing gate's tool said, parsed: one `file:line  message` item per distinct
    diagnostic (rustc/clippy errors and warnings, rustfmt diffs per file, ruff, tsc, mypy,
    eslint) or per failing test (cargo, pytest, bun, nextest, ... through run_tests.parse_log).
    Duplicates (the same error once per target) collapse. Errors come before warnings."""
    text = ANSI.sub("", output)
    items: list[str] = []
    seen: set[str] = set()

    def add(item: str) -> None:
        if item not in seen:
            seen.add(item)
            items.append(item)

    errors, warnings = [], []
    for m in RUSTC_DIAG.finditer(text):
        kind, code, msg, loc = m.groups()
        if re.match(r"(?:aborting due to|could not compile|\d+ warnings? emitted|.*generated \d+ warnings?)", msg):
            continue
        lint = LINT_NAME.search(text[m.end():m.end() + 600])
        tag = (code or (lint.group(2) or lint.group(1) if lint else "")) or ""
        row = f"{loc}  {kind}{'[' + tag + ']' if tag else ''}: {msg}"
        (errors if kind == "error" else warnings).append(row)
    for row in errors + warnings:
        add(row)
    fmt: dict[str, list[str]] = {}
    for m in RUSTFMT_DIFF.finditer(text):
        fmt.setdefault(m.group(1), []).append(m.group(2))
    for path, ls in fmt.items():
        add(f"{path}:{ls[0]}  rustfmt diff ({len(ls)} hunk{'s' if len(ls) != 1 else ''})")
    for m in RUFF_CONCISE.finditer(text):
        add(f"{m.group(1)}:{m.group(2)}  {m.group(3)} {m.group(4)}")
    for m in RUFF_FULL.finditer(text):
        add(f"{m.group(3)}  {m.group(1)} {m.group(2)}")
    for m in WOULD_REFORMAT.finditer(text):
        add(f"{m.group(1)}  would reformat")
    for m in TSC_ERR.finditer(text):
        add(f"{m.group(1)}:{m.group(2)}  {m.group(3)} {m.group(4)}")
    for m in MYPY_ERR.finditer(text):
        add(f"{m.group(1)}:{m.group(2)}  mypy: {m.group(3)}")
    current = ""
    for line in text.splitlines():
        if line and not line[0].isspace() and ":" not in line.split(" ")[0] and "/" in line and not line.startswith("error"):
            current = line.strip()
            continue
        m = ESLINT_ERR.match(line)
        if m and current:
            add(f"{current}:{m.group(1)}  eslint {m.group(3)}: {m.group(2)}")
    try:
        import run_tests
        parser, totals, fails = run_tests.parse_log(text)
    except Exception:  # a parser bug never hides a gate's failure
        parser, totals, fails = "", None, []
    if parser and parser != "generic" and fails:
        # Compile errors came through the rustc scan above; tests are listed by name.
        tests = [f for f in fails if not re.fullmatch(r"\S+:\d+(?::\d+)?", f.name)]
        if tests:
            add(f"tests: passed {totals.passed} failed {totals.failed} ({parser})")
        for f in tests:
            head = run_tests.failure_headline(f)
            add(f"test {f.name}" + (f"  {head}" if head else ""))
    return items


def digest_lines(output: str, n: int, chars: int) -> list[str]:
    """The lines printed under a failing gate (before the 4-space indent): parsed items first,
    at most n of them, then a `(+K more ...)` marker that says the log holds more; when the
    output parses to nothing, the regex excerpt of old (matching lines, else the tail)."""
    items = structured_items(output)
    if not items:
        lines = output.splitlines()
        matched = [l for l in lines if FAILURE_RE.search(l)]
        shown = [l[:chars] for l in (matched[:n] if matched else lines[-n:])]
        if matched and len(matched) > n:
            shown.append(f"(+{len(matched) - n} more matching lines, truncated: open the log)")
        elif not matched and len(lines) > n:
            shown.append("(no failure pattern matched; last lines shown, truncated: open the log)")
        return shown
    shown = [i[:chars] for i in items[:n]]
    if len(items) > n:
        shown.append(f"(+{len(items) - n} more, truncated: open the log)")
    return shown


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


EXIT_STILL_RUNNING = 6
EXIT_DIED = 125


def state_root() -> str:
    return os.environ.get("RUN_GATES_STATE_DIR") or os.path.join(tempfile.gettempdir(), "run_gates-detach")


def detach(argv: list[str], args: argparse.Namespace) -> int:
    """Start this same run in a new session and return at once; see "Long runs"."""
    if not args.gates_file:
        print("run_gates: --detach needs a GATES_FILE", file=sys.stderr)
        return 2
    run_id = time.strftime("%Y%m%dT%H%M%S") + "-" + os.urandom(3).hex()
    sdir = os.path.join(state_root(), run_id)
    os.makedirs(sdir)
    out_path, exit_path = os.path.join(sdir, "out.txt"), os.path.join(sdir, "exit")
    child = [a for a in argv if a != "--detach"]
    receipt = None
    try:
        with open(args.gates_file, encoding="utf-8") as fh:
            sha = hashlib.sha256(fh.read().encode("utf-8")).hexdigest()
        receipt = receipt_path(os.path.abspath(args.root), sha)
    except OSError:
        pass
    script = ('"$@" > "$OUT" 2>&1 < /dev/null; echo $? > "$EXIT.tmp"; mv "$EXIT.tmp" "$EXIT"')
    proc = subprocess.Popen(
        ["bash", "-c", script, "run_gates", sys.executable, os.path.abspath(__file__), *child],
        env={**os.environ, "OUT": out_path, "EXIT": exit_path},
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True, preexec_fn=lambda: signal.signal(signal.SIGHUP, signal.SIG_IGN),
    )
    with open(os.path.join(sdir, "state.json"), "w", encoding="utf-8") as fh:
        json.dump({"id": run_id, "pid": proc.pid, "argv": child, "cwd": os.getcwd(), "out": out_path,
                   "exit": exit_path, "receipt": receipt, "started": time.time()}, fh)
    print(f"run_gates: detached run {run_id} (pid {proc.pid})")
    print(f"state {sdir}")
    print(f"receipt {receipt or '(none: not a git worktree)'}")
    print(f"wait: {shlex.quote(os.path.abspath(__file__))} --wait {run_id}")
    return 0


def wait_run(run_id: str, cap: float) -> int:
    """Block up to `cap` seconds for a detached run, then print its output and
    return its exit code; EXIT_STILL_RUNNING at the cap."""
    sdir = os.path.join(state_root(), run_id)
    try:
        with open(os.path.join(sdir, "state.json"), encoding="utf-8") as fh:
            st = json.load(fh)
    except (OSError, ValueError):
        print(f"run_gates: no detached run {run_id!r} under {state_root()}", file=sys.stderr)
        return 2

    def finished() -> bool:
        return os.path.exists(st["exit"])

    def alive() -> bool:
        try:
            os.kill(st["pid"], 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return True

    deadline = time.monotonic() + cap
    while not finished():
        if not alive() and not finished():
            break
        if time.monotonic() >= deadline:
            print(f"run_gates: run {run_id} still running after {cap:.0f}s (pid {st['pid']}); "
                  f"call --wait {run_id} again. Output so far: {st['out']}")
            return EXIT_STILL_RUNNING
        time.sleep(min(0.2, max(0.0, deadline - time.monotonic())))
    try:
        with open(st["out"], encoding="utf-8", errors="replace") as fh:
            sys.stdout.write(fh.read())
    except OSError:
        pass
    if not finished():
        print(f"run_gates: run {run_id} died without an exit code; output above is what it wrote")
        return EXIT_DIED
    try:
        with open(st["exit"], encoding="utf-8") as fh:
            return int(fh.read().strip())
    except ValueError:
        return EXIT_DIED


def parse_or_usage(ap: argparse.ArgumentParser, argv: list[str]) -> argparse.Namespace:
    """parse_args, but an unknown argument prints the usage line and a hint, then exits 2."""
    args, extra = ap.parse_known_args(argv)
    if extra:
        ap.print_usage(sys.stderr)
        print(f"{ap.prog}: unknown argument(s): {' '.join(extra)}; `{ap.prog} --help` lists every flag", file=sys.stderr)
        raise SystemExit(2)
    return args


def write_build_log(args: argparse.Namespace, root: str, passed: int, failed: int, skipped: int, reused: int) -> None:
    """--build-log: append the one-line result to the dossier's Build log, through dossier_edit's lock."""
    if not args.build_log:
        return
    import dossier_edit
    sha = "unknown"
    try:
        sha = subprocess.run(["git", "-C", root, "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()[:12]
    except (OSError, subprocess.CalledProcessError):
        pass
    line = (f"GATES: run {'red' if failed else 'green'} on {sha} — {passed} passed, {failed} failed, {skipped} skipped"
            + (f" ({reused} reused)" if reused else ""))
    print(dossier_edit.log_or_say(args.build_log, [line]))


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0], allow_abbrev=False)
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
    ap.add_argument("--fresh", action="store_true", help="run every gate even when a green receipt matches this tree")
    ap.add_argument("--affected-since", metavar="REV",
                    help="run only the gates a path changed since REV touches (diff of the working tree against REV); "
                         "a gate green on the same inputs is reused, every other gate is reported skipped, "
                         "and a changed path no gate claims runs them all")
    ap.add_argument("--compile-lint-only", action="store_true",
                    help="the contract (stub) commit: run only compile and lint gates; the rest are reported skipped")
    ap.add_argument("--receipt", action="store_true", help="exit 0 when a green receipt matches this tree, 4 when none; run nothing")
    ap.add_argument("--path-prepend", action="append", default=[], metavar="DIR",
                    help="prepend DIR to every gate's PATH (repeatable; $HOME and ~/ expand)")
    ap.add_argument("--env-script", metavar="FILE", help="source FILE before every gate (`. FILE && gate`)")
    ap.add_argument("--detach", action="store_true",
                    help="start the run in the background, print a run id and return at once")
    ap.add_argument("--wait", metavar="ID", help="wait for a detached run, print its summary, exit with its code")
    ap.add_argument("--wait-cap", type=float, default=float(os.environ.get("ASD_WAIT_CAP") or 270), metavar="SECONDS",
                    help="--wait: longest block (default 270, or $ASD_WAIT_CAP); exit 6 when the run is still going")
    ap.add_argument("--build-log", metavar="DOSSIER",
                    help="after a gate run, append `GATES: run <green|red> on <sha> — <counts>` to this dossier's Build log")
    ap.add_argument("--selftest", action="store_true", help="check the checker")
    args = parse_or_usage(ap, argv)
    if args.selftest:
        return selftest()
    if args.wait:
        return wait_run(args.wait, args.wait_cap)
    if args.detach:
        return detach(argv, args)
    if args.from_workflow:
        return from_workflow(args.from_workflow, args.skip_step)
    if not args.gates_file:
        ap.error("GATES_FILE is required unless --selftest")

    try:
        with open(args.gates_file, encoding="utf-8") as fh:
            gates_text = fh.read()
        gates = parse_gates(gates_text)
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

    resolve_gate_paths(gates)
    if args.list:
        for g in gates:
            needs = f"  [needs: {g['needs']}]" if g["needs"] else ""
            paths = f"  [paths: {','.join(g['paths'])}] ({g['paths_src']})" if g["paths"] else ""
            kind = f"  [kind: {infer_kind(g) or 'unknown'}]"
            print(f"{g['name']}: {g['command']}{needs}{paths}{kind}")
        return 0

    state = git_state_in_progress(os.path.abspath(args.root))
    if state:
        print(f"run_gates: refusing to run: {state} present in the worktree's git dir "
              "(merge, rebase, cherry-pick or bisect in progress); finish or abort it first", file=sys.stderr)
        return EXIT_GIT_STATE

    root = os.path.abspath(args.root)
    gates_sha = hashlib.sha256(gates_text.encode("utf-8")).hexdigest()
    full = not (args.only or args.affected_since or args.compile_lint_only)
    fingerprint = tree_fingerprint(root) if not args.only else None
    rec = matching_receipt(root, gates_sha, fingerprint) if fingerprint and not args.fresh else None
    if args.receipt and not full:
        print("run_gates: --receipt checks the full list; drop --only, --affected-since and --compile-lint-only")
        return EXIT_NO_RECEIPT
    if args.receipt:
        if rec:
            print(f"run_gates: green receipt {rec['at']} on {rec['head'][:12]} matches this tree")
            return 0
        print("run_gates: no green receipt matches this tree" + ("" if full else " (--only never has one)"))
        return EXIT_NO_RECEIPT
    if rec:
        print(f"REUSED {len(rec['passed']) + len(rec['skipped'])} gates — receipt {rec['at']} on "
              f"{rec['head'][:12]}, same gates file and tree; --fresh runs them again")
        print(f"run_gates: {len(rec['passed'])} passed, 0 failed, {len(rec['skipped'])} skipped (reused)")
        write_build_log(args, os.path.abspath(args.root), len(rec['passed']), 0, len(rec['skipped']), len(rec['passed']))
        return 0

    # Selection: which gates this call leaves out, each with a printed reason (never silent).
    skip_reason: dict[str, str] = {}
    if args.compile_lint_only:
        cl_skips, unknown_kind = select_contract(gates)
        skip_reason.update(cl_skips)
        print("run_gates: contract commit: compile and lint gates only"
              + (f"; kind unknown, running anyway: {', '.join(unknown_kind)}" if unknown_kind else ""))
    entries: list[tuple[str, str]] = []
    if args.affected_since:
        changed = changed_paths(root, args.affected_since, fingerprint[0]) if fingerprint else None
        if changed is None:
            print(f"run_gates: affected: cannot diff against {args.affected_since!r} here; running every selected gate")
        else:
            af_skips, note = select_affected(gates, changed)
            for name, why in af_skips.items():
                skip_reason.setdefault(name, why)
            print(f"run_gates: affected since {args.affected_since}: {note}; "
                  f"{len(gates) - len(skip_reason)} of {len(gates)} gates selected")
    if fingerprint:
        entries = tree_entries(root, fingerprint[0])

    log_dir = args.log_dir or tempfile.mkdtemp(prefix="run_gates-")
    os.makedirs(log_dir, exist_ok=True)
    print(f"run_gates: logging to {log_dir}")

    to_run = []
    results: list[tuple[str, dict, dict | None]] = [None] * len(gates)  # type: ignore[list-item]
    reused: dict[int, dict] = {}
    for i, g in enumerate(gates):
        if g["name"] in skip_reason:
            results[i] = ("skip", g, skip_reason[g["name"]])
            continue
        if args.affected_since and fingerprint and not args.fresh and not (
                g["needs"] and shutil.which(g["needs"], path=gate_env(args.path_prepend).get("PATH")) is None):
            hit = gate_receipt(root, g, gate_inputs_hash(g, fingerprint, entries))
            if hit:
                results[i] = ("reused", g, hit)
                continue
        if g["needs"] and shutil.which(g["needs"], path=gate_env(args.path_prepend).get("PATH")) is None:
            results[i] = ("skip", g, f"needs {g['needs']!r}, not on PATH")
        else:
            to_run.append(i)

    env = build_slots.held_env(gate_env(args.path_prepend))
    env_script = args.env_script
    if env_script and not os.path.isfile(os.path.expanduser(env_script)):
        print(f"run_gates: --env-script {env_script}: no such file", file=sys.stderr)
        return 2

    def work(i: int) -> tuple[int, dict]:
        return i, run_gate(gates[i]["command"], root, args.timeout, env, env_script)

    # One host build slot for the whole run (build_slots.py): every run builds in its own worktree, so
    # fifteen runs gating at once were fifteen cargo builds and a swapping host. Waiting is not gate time:
    # --timeout starts once the slot is held.
    with build_slots.slot("run_gates " + root, log=lambda m: print("run_gates: " + m, flush=True)) as waited:
        if waited >= 1:
            print(f"run_gates: got a host build slot after {waited:.0f}s", flush=True)
        if args.jobs > 1 and len(to_run) > 1:
            with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as ex:
                for i, res in ex.map(work, to_run):
                    results[i] = ("ran", gates[i], res)
        else:
            for i in to_run:
                i, res = work(i)
                results[i] = ("ran", gates[i], res)

    passed = failed = skipped = notfound = nreused = 0
    for kind, g, res in results:
        if kind == "skip":
            skipped += 1
            print(f"SKIP {g['name']} — {res}")
            continue
        if kind == "reused":
            nreused += 1
            print(f"REUSED {g['name']} — green receipt {res['at']} on {res['head'][:12]}, same inputs "
                  f"({'its paths' if g['paths'] else 'whole tree'}); --fresh runs it again")
            continue
        assert res is not None
        path = write_log(log_dir, g["name"], res["output"])
        if res["timed_out"]:
            failed += 1
            print(f"FAIL {g['name']} (timeout after {args.timeout:.1f}s, {res['elapsed']:.1f}s) — log {path}")
            for line in digest_lines(res["output"], args.lines, args.line_chars):
                print(f"    {line}")
        elif res["rc"] == 0:
            passed += 1
            print(f"PASS {g['name']} ({res['elapsed']:.1f}s)")
        else:
            failed += 1
            tag = ""
            if res["rc"] == 127:
                notfound += 1
                tag = " — command not found: environment, not a gate failure"
            print(f"FAIL {g['name']} (exit {res['rc']}, {res['elapsed']:.1f}s){tag} — log {path}")
            for line in digest_lines(res["output"], args.lines, args.line_chars):
                print(f"    {line}")

    print(f"run_gates: {passed + nreused} passed, {failed} failed, {skipped} skipped"
          + (f" ({nreused} reused from per-gate receipts)" if nreused else ""))
    if notfound:
        print(f"run_gates: {notfound} of the {failed} failed gate(s) exited 127 — command not found — "
              "environment, not a gate failure; put the toolchain on PATH with --path-prepend DIR "
              "or --env-script FILE, then rerun")
    if fingerprint and not args.only and tree_fingerprint(root) == fingerprint:
        # Fingerprint again: a gate that rewrote the tree (a formatter) leaves no receipt.
        for kind, g, res in results:
            if kind == "ran" and res["rc"] == 0 and not res["timed_out"]:
                write_gate_receipt(root, g, gate_inputs_hash(g, fingerprint, entries), fingerprint[1])
        if full and not failed:
            write_receipt(root, gates_sha, fingerprint,
                          [g["name"] for kind, g, _ in results if kind == "ran"],
                          [{"name": g["name"], "needs": g["needs"]} for kind, g, _ in results if kind == "skip"])
    write_build_log(args, root, passed + nreused, failed, skipped, nreused)
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
      - name: Check spelling
        uses: crate-ci/typos@v1.24.0
      - name: Odd lint
        uses: someorg/odd-lint@v2
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


CLIPPY_LOG = """    Checking foo v0.1.0 (/repo/crates/foo)
error: this `if` has identical blocks
  --> crates/foo/src/lib.rs:42:9
   |
42 |         if a { 1 } else { 1 }
   |
   = note: `-D clippy::if-same-then-else` implied by `-D warnings`
   = help: to override `-D warnings` add `#[allow(clippy::if_same_then_else)]`

error[E0599]: no method named `frob` found for struct `Foo` in the current scope
 --> crates/foo/src/main.rs:7:7
  |
7 |     f.frob();
  |       ^^^^

error[E0599]: no method named `frob` found for struct `Foo` in the current scope
 --> crates/foo/src/main.rs:7:7
  |
7 |     f.frob();
  |       ^^^^

warning: unused variable: `y`
 --> crates/foo/src/lib.rs:3:9

error: could not compile `foo` (lib) due to 2 previous errors
"""

RUSTFMT_LOG = """Diff in /repo/crates/foo/src/lib.rs:12:
-fn a(){}
+fn a() {}
Diff in /repo/crates/foo/src/lib.rs:30:
-fn b(){}
+fn b() {}
Diff in /repo/crates/bar/src/main.rs at line 4:
-let x=1;
+let x = 1;
"""

RUFF_LOG = """src/app/util.py:10:1: F401 [*] `os` imported but unused
src/app/util.py:22:5: E741 Ambiguous variable name: `l`
Found 2 errors.
Would reformat: src/app/other.py
"""

TSC_LOG = """src/index.ts(12,5): error TS2322: Type 'string' is not assignable to type 'number'.
src/index.ts(30,1): error TS2304: Cannot find name 'foo'.
Found 2 errors in the same file, starting at: src/index.ts:12
"""

CARGO_TEST_GATE_LOG = """running 2 tests
test ok_one ... ok
test bad_one ... FAILED

failures:

---- bad_one stdout ----
thread 'bad_one' panicked at crates/foo/tests/t.rs:9:5:
assertion `left == right` failed

failures:
    bad_one

test result: FAILED. 1 passed; 1 failed; 0 ignored; 0 measured; 0 filtered out; finished in 0.00s
"""

BUN_GATE_LOG = """bun test v1.1.20

a.test.ts:
error: expect(received).toBe(expected)

Expected: 1
Received: 2

      at <anonymous> (/repo/a.test.ts:5:12)
\u2717 a > b [0.5ms]
(fail) a > b [0.5ms]

 0 pass
 1 fail
Ran 1 tests across 1 files. [5.00ms]
"""


def selftest() -> int:
    os.environ["ASD_BUILD_SLOTS"] = "0"  # a self-test never waits on, or takes, a real host build slot
    failures: list[str] = []

    def check(name: str, ok: bool) -> None:
        if not ok:
            failures.append(name)


    # The failure digest: parsed items first, deduplicated, with file:line, capped with a marker.
    items = structured_items(CLIPPY_LOG)
    check("clippy: errors first with file:line, lint name, no duplicate, no summary line",
          items == ["crates/foo/src/lib.rs:42:9  error[clippy::if-same-then-else]: this `if` has identical blocks",
                    "crates/foo/src/main.rs:7:7  error[E0599]: no method named `frob` found for struct `Foo` in the current scope",
                    "crates/foo/src/lib.rs:3:9  warning: unused variable: `y`"])
    items = structured_items(RUSTFMT_LOG)
    check("rustfmt: one item per file with its first line and hunk count",
          items == ["/repo/crates/foo/src/lib.rs:12  rustfmt diff (2 hunks)", "/repo/crates/bar/src/main.rs:4  rustfmt diff (1 hunk)"])
    items = structured_items(RUFF_LOG)
    check("ruff: code and file:line, would-reformat file",
          items == ["src/app/util.py:10  F401 `os` imported but unused", "src/app/util.py:22  E741 Ambiguous variable name: `l`",
                    "src/app/other.py  would reformat"])
    items = structured_items(TSC_LOG)
    check("tsc: file:line and TS code", items[0] == "src/index.ts:12  TS2322 Type 'string' is not assignable to type 'number'." and len(items) == 2)
    items = structured_items(CARGO_TEST_GATE_LOG)
    check("a test failing inside a gate: counts, name, first panic location and message",
          items == ["tests: passed 1 failed 1 (cargo)", "test bad_one  crates/foo/tests/t.rs:9:5 assertion `left == right` failed"])
    items = structured_items(BUN_GATE_LOG)
    check("bun test inside a gate", items[-1] == "test a > b  /repo/a.test.ts:5:12 error: expect(received).toBe(expected)")
    check("unparsed output yields no items", structured_items("DEFECT: x\nall good\n") == [])
    many = "\n".join(f"s{i}.py:{i}:1: E501 line too long" for i in range(1, 31))
    shown = digest_lines(many, 5, 300)
    check("a long item list is capped with a marker pointing at the log",
          len(shown) == 6 and shown[-1] == "(+25 more, truncated: open the log)")
    shown = digest_lines("\n".join(f"ERROR {i}" for i in range(40)), 5, 300)
    check("the regex excerpt says it was truncated", len(shown) == 6 and "truncated" in shown[-1])
    shown = digest_lines("one\nFAIL two\n", 5, 300)
    check("a short regex excerpt has no marker", shown == ["FAIL two"])
    with tempfile.TemporaryDirectory() as tmp2:
        gpath = os.path.join(tmp2, "g.txt")
        fmt = os.path.join(tmp2, "fmt.txt")
        with open(fmt, "w", encoding="utf-8") as fh:
            fh.write(RUFF_LOG)
        with open(gpath, "w", encoding="utf-8") as fh:
            fh.write(f"lint: cat {shlex.quote(fmt)}; exit 1\n")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = main([gpath, "--log-dir", os.path.join(tmp2, "l")])
        out = buf.getvalue()
        check("a failing gate prints the parsed items indented under its FAIL line, log path kept",
              rc == 1 and "FAIL lint (exit 1" in out and "    src/app/util.py:10  F401" in out and "lint.log" in out)

    with tempfile.TemporaryDirectory() as tmp:
        timed = run_gate("printf partial; exec sleep 5", tmp, 0.5)
        check("a gate that prints and outlives its timeout is a timed-out result, its output kept",
              timed["timed_out"] and timed["rc"] is None and "partial" in timed["output"])
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

        # --- receipts: a green run on an unchanged tree is reused ---
        rrepo = os.path.join(tmp, "rrepo")
        os.makedirs(rrepo)
        rgit = lambda *a: subprocess.run(["git", "-C", rrepo, "-c", "user.email=t@t", "-c", "user.name=t", *a],
                                         capture_output=True, text=True)
        if rgit("init", "-q").returncode == 0:
            counter = os.path.join(tmp, "count.txt")
            rg = os.path.join(tmp, "rgates.txt")
            with open(rg, "w", encoding="utf-8") as fh:
                fh.write(f"count: echo x >> {counter}\nmissing: true [needs: totally-not-a-real-tool-xyz]\n")
            with open(os.path.join(rrepo, "a.txt"), "w") as fh:
                fh.write("1\n")
            os.makedirs(os.path.join(rrepo, ".discovery", "dossiers"))
            with open(os.path.join(rrepo, ".discovery", "dossiers", "W-1.md"), "w") as fh:
                fh.write("log\n")
            rgit("add", "-A")
            rgit("commit", "-qm", "init")

            def rrun(*extra: str) -> tuple[int, str]:
                o = io.StringIO()
                with contextlib.redirect_stdout(o), contextlib.redirect_stderr(io.StringIO()):
                    code = main([rg, "--root", rrepo, "--log-dir", os.path.join(tmp, "rl"), *extra])
                return code, o.getvalue()

            def runs() -> int:
                return sum(1 for _ in open(counter)) if os.path.exists(counter) else 0

            rc0, _ = rrun("--receipt")
            check("no receipt before a run: --receipt exits 4", rc0 == 4)
            rrun()
            rc1, out1 = rrun()
            check("second run on the same tree reuses the receipt", rc1 == 0 and runs() == 1
                  and "REUSED 2 gates" in out1 and "1 passed, 0 failed, 1 skipped (reused)" in out1)
            check("--receipt finds it", rrun("--receipt")[0] == 0)
            bl = os.path.join(tmp, "W-bl.md")
            with open(bl, "w", encoding="utf-8") as fh:
                fh.write("---\nid: W-bl\n---\n\n# T\n\n## Build log\n\n- first\n")
            _, outbl = rrun("--build-log", bl)
            gotbl = open(bl, encoding="utf-8").read()
            check("--build-log appends a GATES: run line, also on a reused receipt",
                  re.search(r"^- GATES: run green on [0-9a-f]{12} — 1 passed, 0 failed, 1 skipped \(1 reused\)$", gotbl, re.M) is not None
                  and "build log: 1 line(s) written" in outbl)
            ebl = io.StringIO()
            with contextlib.redirect_stderr(ebl):
                try:
                    main([rg, "--bogus"])
                    raised = 0
                except SystemExit as ex:
                    raised = ex.code
            check("an unknown argument prints the usage line and a hint, exit 2",
                  raised == 2 and "usage:" in ebl.getvalue() and "--help" in ebl.getvalue())
            with open(os.path.join(rrepo, ".discovery", "dossiers", "W-1.md"), "a") as fh:
                fh.write("RETURNED line\n")
            rrun()
            check("a build-log edit in the dossier keeps the receipt", runs() == 1)
            rgit("commit", "-qam", "log")
            rrun()
            check("a commit of the build log alone keeps the receipt", runs() == 1)
            with open(os.path.join(rrepo, "a.txt"), "a") as fh:
                fh.write("2\n")
            rrun()
            check("an uncommitted code edit runs the gates again", runs() == 2)
            with open(os.path.join(rrepo, "new.txt"), "w") as fh:
                fh.write("n\n")
            rrun()
            check("an untracked file runs the gates again", runs() == 3)
            rrun("--fresh")
            check("--fresh runs them even with a matching receipt", runs() == 4)
            rrun("--only", "count")
            rrun("--only", "count")
            check("--only never reuses nor writes a receipt", runs() == 6)
            with open(rg, "a", encoding="utf-8") as fh:
                fh.write("other: true\n")
            rrun()
            check("a changed gates file runs them again", runs() == 7)
            with open(rg, "a", encoding="utf-8") as fh:
                fh.write("bad: false\n")
            rrun()
            rc_bad, _ = rrun()
            check("a red run writes no receipt", rc_bad == 1 and runs() == 9)
            check("the real index is untouched", "A " not in subprocess.run(
                ["git", "-C", rrepo, "status", "--porcelain"], capture_output=True, text=True).stdout)

        # --- path-affected selection, per-gate receipts, full vs contract modes ---
        parsed = parse_gates("a: x [kind: lint] [paths: *.py,docs/] [needs: sometool]\nb: y [paths: **/*.rs]\nc: z\n")
        check("annotations parse in any order", parsed[0]["needs"] == "sometool" and parsed[0]["kind"] == "lint"
              and parsed[0]["paths"] == ["*.py", "docs/"] and parsed[1]["paths"] == ["**/*.rs"] and parsed[2]["paths"] is None)
        try:
            parse_gates("a: x [kind: nonsense]\n")
            bad_kind = False
        except GateFileError:
            bad_kind = True
        check("an unknown [kind:] is refused", bad_kind)
        check("glob: basename pattern matches at any depth", path_matches("a/b/c.py", ["*.py"]) and not path_matches("c.pyc", ["*.py"]))
        check("glob: ** and directory patterns", path_matches("x/y/z.rs", ["**/*.rs"]) and path_matches("docs/a/b.md", ["docs/"])
              and not path_matches("xdocs/a.md", ["docs/"]) and path_matches("Cargo.toml", ["Cargo.toml"])
              and path_matches("crates/a/Cargo.toml", ["Cargo.toml"]) and not path_matches("src/a/b.rs", ["src/*.rs"]))
        check("infer: cargo and ruff chains are path-known, scripts and pipes are not",
              infer_paths("cargo clippy --all-targets -- -D warnings") == RUST_PATHS
              and infer_paths("ruff check . && ruff format --check .") == PY_PATHS
              and infer_paths("python3 -m mypy src") == PY_PATHS and infer_paths("npx tsc --noEmit") == JS_PATHS
              and infer_paths("./scripts/check.sh") is None and infer_paths("cargo fmt --check | tee x") is None
              and infer_paths("cargo check && ./x.sh") is None and infer_paths("make lint") is None)
        check("infer: from-workflow export prefixes are dropped; dotnet and terraform are path-known; a path outside the tool's files is not",
              infer_paths("export CARGO_NET_RETRY='10'; export A=\"x y\"; cargo clippy --workspace") == RUST_PATHS
              and infer_paths("export TF_VAR_x='[\"a\"]'; terraform fmt -check") == TF_PATHS
              and infer_paths("dotnet test --project Source/A.Tests/A.Tests.csproj --no-build") == DOTNET_PATHS
              and infer_paths("cargo deny --manifest-path ./Cargo.toml check") == RUST_PATHS
              and infer_paths("export X='1'; cargo run --bin lc -- deploy/compose/a.yml") is None
              and infer_paths("export X=$(pwd); cargo build") is None and infer_paths("export X='1'; bash scripts/x.sh") is None)
        check("infer kind: dotnet and terraform subcommands",
              [infer_kind({"name": "n", "command": c, "kind": None}) for c in
               ("dotnet build A.sln", "dotnet test --project A.csproj", "terraform validate", "dotnet format --verify-no-changes")]
              == ["compile", "test", "compile", "format"])
        kinds = {n: infer_kind({"name": n, "command": c, "kind": None}) for n, c in (
            ("a", "cargo clippy --workspace"), ("b", "cargo fmt --check"), ("c", "cargo nextest run"),
            ("d", "ruff check ."), ("e", "cargo check --locked"), ("lint-all", "make everything"), ("z", "make everything"))}
        check("infer kind: tool and subcommand, then the gate name, else unknown",
              kinds == {"a": "lint", "b": "format", "c": "test", "d": "lint", "e": "compile", "lint-all": "lint", "z": None})
        gs = [{"name": "rust", "paths": ["*.rs"]}, {"name": "py", "paths": ["*.py"]}, {"name": "any", "paths": None}]
        sk, _ = select_affected(gs, ["x/a.rs"])
        check("select_affected: only gates a changed path touches stay", sk == {"py": "skipped: no changed paths"})
        sk, note = select_affected(gs, ["x/a.rs", "docs/readme.md"])
        check("select_affected: a changed path no gate claims runs everything", sk == {} and "maps to no gate" in note)
        sk, note = select_affected(gs, ["x/a.rs", ".discovery/dossiers/W-1.md", ".agent-staging/r.md"])
        check("select_affected: pipeline paths (.discovery, .agent-staging) never make every gate run",
              sk == {"py": "skipped: no changed paths"} and "maps to no gate" not in note)
        sk, _ = select_affected(gs, [])
        check("select_affected: no changed path skips every path-known gate, never the unknown one",
              set(sk) == {"rust", "py"})

        arepo = os.path.join(tmp, "arepo")
        os.makedirs(os.path.join(arepo, "src"))
        os.makedirs(os.path.join(arepo, "app"))
        agit = lambda *a: subprocess.run(["git", "-C", arepo, "-c", "user.email=t@t", "-c", "user.name=t", *a],
                                         capture_output=True, text=True)
        if agit("init", "-q").returncode == 0:
            cnt = {n: os.path.join(tmp, f"cnt-{n}") for n in ("rust", "py", "fmt", "unk")}
            ag = os.path.join(tmp, "agates.txt")
            with open(ag, "w", encoding="utf-8") as fh:
                fh.write(f"rust: echo r >> {cnt['rust']} [paths: *.rs,Cargo.toml] [kind: compile]\n"
                         f"py: echo p >> {cnt['py']} [paths: *.py] [kind: lint]\n"
                         f"fmt: echo f >> {cnt['fmt']} [paths: *.py,*.rs] [kind: format]\n")
            for rel in ("src/a.rs", "app/b.py", "README.md"):
                with open(os.path.join(arepo, rel), "w") as fh:
                    fh.write("1\n")
            agit("add", "-A")
            agit("commit", "-qm", "base")

            def arun(*extra: str, gates_file: str = ag) -> tuple[int, str]:
                o = io.StringIO()
                with contextlib.redirect_stdout(o), contextlib.redirect_stderr(o):
                    code = main([gates_file, "--root", arepo, "--log-dir", os.path.join(tmp, "al"), *extra])
                return code, o.getvalue()

            def n(name: str) -> int:
                return sum(1 for _ in open(cnt[name])) if os.path.exists(cnt[name]) else 0

            def edit(rel: str) -> None:
                with open(os.path.join(arepo, rel), "a") as fh:
                    fh.write("x\n")

            rc, out = arun("--affected-since", "HEAD")
            check("affected: a clean tree skips every path-known gate with an explicit line",
                  rc == 0 and out.count("skipped: no changed paths") == 3 and (n("rust"), n("py"), n("fmt")) == (0, 0, 0))
            edit("app/b.py")
            rc, out = arun("--affected-since", "HEAD")
            check("affected: a python edit runs py and fmt, skips rust with the skip line",
                  rc == 0 and (n("rust"), n("py"), n("fmt")) == (0, 1, 1)
                  and "SKIP rust — skipped: no changed paths" in out and "PASS py" in out)
            rc, out = arun("--affected-since", "HEAD")
            check("receipt hit: the same inputs are not rerun", rc == 0 and (n("py"), n("fmt")) == (1, 1)
                  and "REUSED py" in out and "REUSED fmt" in out and "2 reused from per-gate receipts" in out)
            edit("app/b.py")
            arun("--affected-since", "HEAD")
            check("receipt miss: changed inputs rerun the gate", (n("py"), n("fmt")) == (2, 2))
            edit("src/a.rs")
            rc, out = arun("--affected-since", "HEAD")
            check("affected: a rust edit runs rust, reuses py (its paths are unchanged), reruns fmt",
                  (n("rust"), n("py"), n("fmt")) == (1, 2, 3) and "REUSED py" in out)
            arun("--affected-since", "HEAD", "--fresh")
            check("--fresh ignores per-gate receipts", (n("rust"), n("py"), n("fmt")) == (2, 3, 4))
            edit("README.md")
            rc, out = arun("--affected-since", "HEAD", "--fresh")
            check("fail safe: a changed path no gate claims runs every gate",
                  "maps to no gate" in out and (n("rust"), n("py"), n("fmt")) == (3, 4, 5) and "skipped: no changed" not in out)
            rc, out = arun("--affected-since", "no-such-rev", "--fresh")
            check("fail safe: an unusable base rev runs every gate, says so",
                  rc == 0 and "cannot diff against" in out and (n("rust"), n("py"), n("fmt")) == (4, 5, 6))
            rc, out = arun()
            check("full mode (first push, final head) runs the whole list despite per-gate receipts",
                  rc == 0 and (n("rust"), n("py"), n("fmt")) == (5, 6, 7) and "SKIP" not in out and "per-gate" not in out)
            rc, out = arun("--compile-lint-only", "--fresh")
            check("contract commit: compile and lint run, format is skipped with a reason",
                  rc == 0 and (n("rust"), n("py"), n("fmt")) == (6, 7, 7)
                  and "SKIP fmt — skipped: format gate" in out and "contract commit" in out)
            with open(ag, "a", encoding="utf-8") as fh:
                fh.write(f"unk: echo u >> {cnt['unk']}\n")
            arun("--compile-lint-only", "--fresh")
            check("contract commit: a gate of unknown kind still runs (fail safe)", n("unk") == 1)
            arun("--affected-since", "HEAD")
            arun("--affected-since", "HEAD")
            check("a path-unknown gate keys its receipt on the whole tree: reused when nothing changed, never skipped",
                  n("unk") == 1)
            edit("README.md")
            arun("--affected-since", "HEAD")
            check("a path-unknown gate reruns once the tree changes", n("unk") == 2)
            check("per-gate receipts live in the repo's git dir, not in ~/.agentic",
                  os.path.isdir(os.path.join(arepo, ".git", "run_gates-receipts", "gates")))
            with open(ag, "w", encoding="utf-8") as fh:
                fh.write(f"bad: exit 3 [paths: *.py]\n")
            rc, out = arun("--affected-since", "HEAD", "--fresh")
            rc2, out2 = arun("--affected-since", "HEAD")
            check("a red gate leaves no receipt and reruns", rc == 1 and rc2 == 1 and "REUSED" not in out2)

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
              "uses: 3" in wout and "secrets: 1" in wout and "if-github: 1" in wout
              and "service-job: 1" in wout and "deploy.sh" not in wout and "pytest" not in wout)
        check("a `uses:` checker with a local twin is a gate", "build-check-spelling: typos ." in wout)
        check("an unknown non-setup `uses:` step is listed by name as skipped",
              "# skipped: build/Odd lint - uses: someorg/odd-lint" in wout and "uses: 3" in wout)
        check("summary on stderr too", "run_gates: from-workflow: 7 gates" in err_io.getvalue())
        wgates = os.path.join(tmp, "from-wf.txt")
        with open(wgates, "w", encoding="utf-8") as fh:
            fh.write(wout)
        try:
            check("derived file parses as a gates file", len(parse_gates(wout)) == 7)
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

        # --path-prepend, --env-script, exit 127
        tools = os.path.join(tmp, "tools")
        os.makedirs(tools)
        tool = os.path.join(tools, "faketool-xyz")
        with open(tool, "w", encoding="utf-8") as fh:
            fh.write("#!/bin/sh\necho faketool ran\n")
        os.chmod(tool, 0o755)
        envsh = os.path.join(tmp, "env.sh")
        with open(envsh, "w", encoding="utf-8") as fh:
            fh.write("export FROM_ENV_SCRIPT=yes\n")
        pgates = os.path.join(tmp, "path-gates.txt")
        with open(pgates, "w", encoding="utf-8") as fh:
            fh.write("tool: faketool-xyz\nenvv: test \"$FROM_ENV_SCRIPT\" = yes\n"
                     "gated: faketool-xyz [needs: faketool-xyz]\n")
        out_io = io.StringIO()
        with contextlib.redirect_stdout(out_io):
            rc_n = main([pgates, "--root", tmp, "--log-dir", os.path.join(tmp, "l1")])
        o = out_io.getvalue()
        check("without --path-prepend the tool gate is exit 127, labelled environment",
              rc_n == 1 and "exit 127" in o and "environment, not a gate failure" in o
              and "1 of the 2 failed gate(s) exited 127" in o and "SKIP gated" in o)
        out_io = io.StringIO()
        with contextlib.redirect_stdout(out_io):
            rc_p = main([pgates, "--root", tmp, "--log-dir", os.path.join(tmp, "l2"),
                         "--path-prepend", tools, "--env-script", envsh])
        o = out_io.getvalue()
        check("--path-prepend and --env-script make every gate green",
              rc_p == 0 and "3 passed, 0 failed, 0 skipped" in o and "127" not in o)
        out_io = io.StringIO()
        with contextlib.redirect_stdout(out_io):
            main([pgates, "--root", tmp, "--log-dir", os.path.join(tmp, "l3"), "--path-prepend", "$HOME/nope",
                  "--path-prepend", tools, "--only", "tool"])
        check("repeatable --path-prepend with $HOME expansion", "1 passed, 0 failed" in out_io.getvalue())
        with contextlib.redirect_stderr(io.StringIO()):
            check("a missing --env-script file is refused with exit 2",
                  main([pgates, "--root", tmp, "--env-script", os.path.join(tmp, "nope.sh")]) == 2)

        # --detach then --wait
        os.environ["RUN_GATES_STATE_DIR"] = os.path.join(tmp, "state")
        try:
            dgates = os.path.join(tmp, "detach-gates.txt")
            with open(dgates, "w", encoding="utf-8") as fh:
                fh.write("slow: sleep 1.5; echo done\nbad: exit 3\n")
            t0 = time.monotonic()
            out_io = io.StringIO()
            with contextlib.redirect_stdout(out_io):
                rc_d = main([dgates, "--root", tmp, "--detach", "--jobs", "2"])
            d = out_io.getvalue()
            m = re.search(r"detached run (\S+) \(pid", d)
            check("--detach returns at once with a run id, state dir and receipt line",
                  rc_d == 0 and m is not None and time.monotonic() - t0 < 1.4 and "receipt " in d and "--wait" in d)
            if m:
                rid = m.group(1)
                out_io = io.StringIO()
                with contextlib.redirect_stdout(out_io):
                    rc_c = wait_run(rid, 0.2)
                check("--wait at a short cap says still running, exit 6",
                      rc_c == EXIT_STILL_RUNNING and "still running" in out_io.getvalue())
                out_io = io.StringIO()
                with contextlib.redirect_stdout(out_io):
                    rc_w = wait_run(rid, 30)
                w = out_io.getvalue()
                check("--wait prints the foreground summary and the run's exit code",
                      rc_w == 1 and "PASS slow" in w and "FAIL bad (exit 3" in w
                      and "1 passed, 1 failed, 0 skipped" in w)
            with contextlib.redirect_stderr(io.StringIO()):
                check("--wait on an unknown id exits 2", wait_run("nope", 1) == 2)
        finally:
            os.environ.pop("RUN_GATES_STATE_DIR", None)

    for item in failures:
        print("SELFTEST FAIL  " + item)
    print(f"selftest: {len(failures)} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
