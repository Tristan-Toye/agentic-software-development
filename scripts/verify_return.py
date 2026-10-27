#!/usr/bin/env python3
"""Verify a returned subagent mechanically, in one call, in at most 20 lines.

Every time an implementer or a test author returned, the orchestrator typed the
same compound command by hand — `git_state.py <worktree> --base <fork>`, `git
show --stat <sha>`, `rg -c 'unimplemented!' <file>`, a `shasum` of the contract
to prove the hash stable, `test -f` on paths the author must not have written,
`spawn_admission.py release`, then one build-log line "P2 (ses_x) RETURNED: <sha>
on -p2, db.rs +20/-2 only, 0 unimplemented left, hash stable, tree exact". The
recorded runs did this 60 times: 123 calls and 5,847 credits with the release
calls. Each piece already exists as a tool; this script is the fixed bundle of
them, so a return costs one step instead of three to five. It never reads the
agent's own report — every verdict comes from git and the files on disk —
with one exception, `--report`, below: it reads the report only for claims the
agent's tool list makes impossible.

# Usage

    verify_return.py --root X --branch B --base SHA
        [--owned p1,p2] [--shared-tree p1,p2]
        [--contract-hash H [--contract-files f1,f2]]
        [--payload FILE --payload-sha256 H]
        [--release HOLDER --provider P --model M]
        [--log DOSSIER --agent ID] [--print-line --agent ID]
        [--report FILE --kind KIND [--host claude|opencode]]
    verify_return.py --selftest

`--root` is the worktree the agent worked in; `--branch` the branch it was told
to commit on; `--base` the sha that worktree forked from.

What it prints (one line each unless noted) — the `verdict:` line (and
`RETURNED-LINE:`, when asked for) first, so a reader that loses the tail to
truncation still has the verdict, then the detail:

- `verdict: clean` or `verdict: FLAGGED <flags>`;

- the worktree's state, from `git_state.py`: branch, HEAD, ahead/behind of the
  base, in-progress operation, status counts;
- `BRANCH` when HEAD is not on `--branch`;
- the new commits (`BASE..HEAD`, up to 4 shown) and the per-file `+added/-deleted`
  of their combined diff (up to 5 files); `NO NEW COMMITS` when there are none;
- `TOUCHED_BEYOND`: every file the commits or the working tree changed that is
  not under `--owned` (a comma list of files, directories, or globs; absolute
  paths inside the root are accepted) — `none` when all are owned. Paths under
  `--shared-tree` (the sibling authors' OWNED_PATHS in the same worktree) are
  excluded: a concurrent author's files are judged by that author's own
  verification, never this one's, and its dirty-tree flag likewise counts only
  its own paths;
- `STUBS_LEFT`: the number of `unimplemented!(` / `todo!(` calls left in the owned
  Rust files (comment lines skipped); an implementer's answer is 0;
- the contract hash: with `--payload`, sha256 of the CONTRACT block extracted
  from that payload — payload_hash.py's canonical recipe, the same bytes
  compose_payloads.py stamped — against `--contract-hash` (a full hash or a
  prefix): `stable` or `CHANGED`. Without `--payload`, sha256 of
  `--contract-files` (relative to `--root`, joined as compose_payloads.py
  joins them; a directory entry is refused — the composer hashes files only)
  against the same expected hash. When both are given and the block is stable,
  the files are also checked against the stamped hash: `contract files: differ
  from the stamped contract` names stubs that moved after the stamp — a
  formatter, an edit — and the orchestrator rules on it;
- the payload: sha256 of `--payload` (the file a workflow spawn read) against
  `--payload-sha256` — `stable` or `CHANGED`; a changed payload means the agent
  may have read something other than what was linted;
- the release of the agent's admission slot, when `--release HOLDER` is given
  (with `--provider` and `--model`): spawn_admission.py's own last line;
- with `--log DOSSIER --agent ID`: the mechanical half of the build-log line is
  appended through `dossier_edit.py` and echoed:
  `<ID> RETURNED: <sha> on <branch>, <files>, <n> unimplemented left, hash stable,
  tree exact` (plus `, TOUCHED_BEYOND: ...` when there is any). The orchestrator
  adds only its ruling sentence, if there is one. The line states facts, flagged
  ones included, so a flagged return is logged as flagged.

With `--report FILE --kind KIND` the report is read for claims, never facts.
It prints `UNVERIFIED: <kind> claims a test run but has no Bash; re-run it
yourself` when the report claims a shell
run (`N passed`, `ran pytest` / `cargo test` / `go test` / `npm test`, `exit
code N`, `git diff shows`) and the kind's tool list has no Bash. The list is the
`tools:` line of `claude/agents/<kind>.md` (host `claude`, the default); under
`opencode` the boundary is a permission map, so nothing is checked. Fenced
blocks and `>` quotes are skipped, so quoted output is not a claim. A phantom
verification is a fabricated verdict, not a wrong one: it flags the return, and
the orchestrator re-runs that class of check itself and never logs the claim as
fact. The report's own footprint claims (`TOUCHED_BEYOND: ...`, "no hook
rewrote my files") are checked against the commit range: any path the range
shows changed that the report does not account for is flagged
`REPORT-FOOTPRINT` with the paths named — the verdict comes from the range,
not the report.

`--print-line` (with `--agent`) prints the same line as `RETURNED-LINE: <line>`
without writing it: the phase workflows (`workflows/phase4-wave.js`) run many of
these at once, and the orchestrator logs the lines itself, in one append, so
concurrent verifications never race on the dossier. They never pass `--release`
either: it frees the holder's whole wave, so the orchestrator releases once,
after the last return.

The release and the log happen whether or not a check flagged anything: the
agent is finished either way, and the log must say what it did.

Exit codes: 0 every check clean; 1 something is flagged (no commits, a file
outside `--owned`, stubs left, a changed contract hash, a dirty tree, the wrong
branch, an unverifiable claim in the report, a failed release or log); 2 unusable input.

No third-party imports: this runs wherever python3 and git do.
"""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import importlib.util
import os
import re
import subprocess
import sys
import tempfile

GIT = "/usr/bin/git" if os.path.exists("/usr/bin/git") else "git"
SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, os.path.join(SCRIPTS_DIR, name + ".py"))
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


ph = _load("payload_hash")  # the one CONTRACT_HASH recipe, shared with compose_payloads.py

MAX_LINES = 20
STUB_RE = re.compile(r"\b(?:unimplemented|todo)!\s*\(")
MAX_RS_FILES = 200
AGENTS_DIR = os.path.join(os.path.dirname(SCRIPTS_DIR), "claude", "agents")
# Conservative: each pattern is a run's own phrasing, matched outside fences and quotes.
SHELL_CLAIMS = (
    ("a test run", re.compile(r"\b\d+\s+(?:tests?\s+)?passed\b", re.IGNORECASE)),
    ("a test run", re.compile(
        r"\bran\s+(?:the\s+)?`?(?:pytest|cargo\s+test|go\s+test|npm\s+(?:run\s+)?test|make\s+test)\b",
        re.IGNORECASE)),
    ("an exit code", re.compile(r"\bexit\s+(?:code|status)\s*[:=]?\s*\d+\b", re.IGNORECASE)),
    ("a git diff", re.compile(r"\b(?:ran\s+`?git\s+diff|git\s+diff\s+(?:shows|confirms|is\s+empty))", re.IGNORECASE)),
)
# A report's own footprint claim: its TOUCHED_BEYOND line, or a hook claim
# asserting a clean footprint. Run phrasings, like SHELL_CLAIMS, matched
# outside fences and quotes.
TOUCHED_LINE = re.compile(r"^\s*[-*]?\s*TOUCHED_BEYOND:\s*(.*)$")
HOOK_CLAIMS = (
    re.compile(r"\bno hook rewrote\b", re.IGNORECASE),
    re.compile(r"\bhooks?\s+(?:did\s+not|didn't)\s+(?:touch|rewrite|change)\b", re.IGNORECASE),
    re.compile(r"\bhook outputs?\b[^.\n]*\b(?:none|no\s+changes|nothing)\b", re.IGNORECASE),
)


def git(root: str, *args: str) -> tuple[int, str]:
    proc = subprocess.run([GIT, "-C", root, *args], capture_output=True, text=True, check=False)
    return proc.returncode, proc.stdout.strip() if proc.returncode == 0 else (proc.stdout + proc.stderr).strip()


def script(name: str, *args: str) -> tuple[int, list[str]]:
    proc = subprocess.run([sys.executable, os.path.join(SCRIPTS_DIR, name), *args], capture_output=True, text=True, check=False)
    return proc.returncode, (proc.stdout + proc.stderr).splitlines()


def relative_owned(root: str, raw: str) -> list[str]:
    """The comma list as root-relative entries (a trailing / dropped)."""
    out: list[str] = []
    real = os.path.realpath(root)
    for item in (p.strip() for p in raw.split(",")):
        if not item or item.startswith("#"):
            continue
        if os.path.isabs(item):
            item = os.path.relpath(os.path.realpath(item), real)
        out.append(item.rstrip("/"))
    return out


def is_owned(path: str, owned: list[str]) -> bool:
    return any(path == o or path.startswith(o + "/") or fnmatch.fnmatch(path, o) for o in owned)


def changed_files(root: str, base: str) -> tuple[list[str], list[tuple[str, str, str]], list[str]]:
    """(all changed paths, numstat rows of BASE..HEAD, dirty working-tree paths)."""
    _, numstat = git(root, "diff", "--numstat", f"{base}..HEAD")
    rows = [tuple(l.split("\t", 2)) for l in numstat.splitlines() if l.count("\t") >= 2]
    dirty = porcelain_paths(root)
    paths = sorted({r[2] for r in rows} | set(dirty))
    return paths, rows, dirty  # type: ignore[return-value]


def porcelain_paths(root: str) -> list[str]:
    """Working-tree paths from `git status --porcelain -z`. NUL-separated
    records cannot be mis-split (a path's first character can never be eaten
    by a status-prefix parse, and no quoting is ever applied); a rename or
    copy record carries the original path as the next NUL entry, which is
    skipped — the new path is the one on disk."""
    rc, raw = git(root, "status", "--porcelain", "-z", "--untracked-files=all")
    return parse_porcelain_z(raw) if rc == 0 else []


def parse_porcelain_z(raw: str) -> list[str]:
    out: list[str] = []
    entries = raw.split("\0")
    i = 0
    while i < len(entries):
        entry = entries[i]
        i += 1
        if not entry:
            continue
        path = entry[3:] if len(entry) > 3 else ""
        if entry[0] in "RC" and i < len(entries):
            i += 1
        if path:
            out.append(path)
    return out


def rust_files(root: str, owned: list[str]) -> list[str]:
    files: list[str] = []
    for entry in owned:
        full = os.path.join(root, entry)
        if os.path.isfile(full) and entry.endswith(".rs"):
            files.append(entry)
        elif os.path.isdir(full):
            for dirpath, _dirs, names in os.walk(full):
                for name in sorted(names):
                    if name.endswith(".rs"):
                        files.append(os.path.relpath(os.path.join(dirpath, name), root))
                    if len(files) >= MAX_RS_FILES:
                        return files
    return sorted(set(files))


def count_stubs(root: str, files: list[str]) -> tuple[int, list[tuple[str, int]]]:
    per: list[tuple[str, int]] = []
    for rel in files:
        try:
            with open(os.path.join(root, rel), encoding="utf-8", errors="replace") as fh:
                n = sum(1 for line in fh if not line.lstrip().startswith("//") and STUB_RE.search(line))
        except OSError:
            continue
        if n:
            per.append((rel, n))
    return sum(n for _, n in per), per


def contract_digest(root: str, files: list[str]) -> str:
    """sha256 of the contract files joined with a blank line — compose_payloads.py's CONTRACT_HASH."""
    texts = []
    for rel in files:
        path = rel if os.path.isabs(rel) else os.path.join(root, rel)
        with open(path, encoding="utf-8") as fh:
            texts.append(fh.read())
    return ph.contract_hash("\n\n".join(texts))


def agent_tools(kind: str, host: str, agents_dir: str | None = None) -> list[str] | None:
    """The kind's tool list from `<agents_dir>/<kind>.md`'s `tools:` line under
    `claude`; None when it cannot be told (opencode's boundary is a permission
    map, and an unreadable or tool-less file is unknown, not empty)."""
    if host != "claude":
        return None
    try:
        with open(os.path.join(agents_dir or AGENTS_DIR, kind + ".md"), encoding="utf-8") as handle:
            lines = handle.read().split("\n")
    except OSError:
        return None
    if not lines or lines[0] != "---":
        return None
    for line in lines[1:]:
        if line == "---":
            break
        match = re.match(r"^tools:\s*(.+)$", line)
        if match:
            return [t.strip() for t in match.group(1).split(",") if t.strip()]
    return None


def shell_claims(report: str) -> list[str]:
    """The kinds of shell run the report claims, outside fences and `>` quotes."""
    kinds: list[str] = []
    fenced = False
    for line in report.splitlines():
        if line.lstrip().startswith("```"):
            fenced = not fenced
            continue
        if fenced or line.lstrip().startswith(">"):
            continue
        for label, pattern in SHELL_CLAIMS:
            if label not in kinds and pattern.search(line):
                kinds.append(label)
    return kinds


def footprint_claim(report: str) -> tuple[bool, list[str]]:
    """(the report makes a footprint or hook claim, the paths its
    TOUCHED_BEYOND line names). Outside fences and `>` quotes, like
    shell_claims. A hook claim with no TOUCHED_BEYOND line claims a clean
    footprint (an empty list), not no claim."""
    claimed: list[str] | None = None
    hook = False
    fenced = False
    for line in report.splitlines():
        if line.lstrip().startswith("```"):
            fenced = not fenced
            continue
        if fenced or line.lstrip().startswith(">"):
            continue
        match = TOUCHED_LINE.match(line)
        if match and claimed is None:
            claimed = [p.strip() for p in match.group(1).split(",")
                       if p.strip() and p.strip().lower() not in ("none", "not checked")]
        if any(rx.search(line) for rx in HOOK_CLAIMS):
            hook = True
    if claimed is None and hook:
        claimed = []
    return (claimed is not None or hook), claimed or []


def clamp(lines: list[str]) -> list[str]:
    return lines if len(lines) <= MAX_LINES else lines[: MAX_LINES - 1] + [f"(+{len(lines) - MAX_LINES + 1} more line(s))"]


def verify(args: argparse.Namespace) -> tuple[list[str], list[str], list[str]]:
    """(report lines, flags, the pieces of the RETURNED line)."""
    root = os.path.abspath(args.root)
    out: list[str] = []
    flags: list[str] = []
    parts: list[str] = []

    rc, lines = script("git_state.py", root, "--base", args.base, "--paths", "5", "--commits", "3")
    keep = ("branch:", "HEAD:", "base ", "in progress:", "status:")
    out += [l for l in lines if l.startswith(keep)] or [f"git_state failed (exit {rc}): {' '.join(lines)[:160]}"]
    if rc != 0:
        flags.append("git_state")

    _, branch = git(root, "rev-parse", "--abbrev-ref", "HEAD")
    if branch != args.branch:
        out.append(f"BRANCH: HEAD is on {branch}, expected {args.branch}")
        flags.append("branch")

    rc, log = git(root, "log", "--format=%h %s", f"{args.base}..HEAD")
    commits = log.splitlines() if rc == 0 else []
    _, sha = git(root, "rev-parse", "--short", "HEAD")
    paths, rows, dirty = changed_files(root, args.base)
    if not commits:
        out.append("NO NEW COMMITS since the base")
        flags.append("no-commits")
    else:
        out.append(f"commits: {len(commits)} since {args.base[:7]}")
        out += ["  " + l[:100] for l in commits[:4]]
        if len(commits) > 4:
            out.append(f"  (+{len(commits) - 4} more)")
        added = sum(int(r[0]) for r in rows if r[0].isdigit())
        deleted = sum(int(r[1]) for r in rows if r[1].isdigit())
        for r in rows[:5]:
            out.append(f"  {r[2]} +{r[0]}/-{r[1]}")
        if len(rows) > 5:
            out.append(f"  (+{len(rows) - 5} more file(s))")
        if len(rows) <= 3:
            parts.append(", ".join(f"{os.path.basename(r[2])} +{r[0]}/-{r[1]}" for r in rows))
        else:
            parts.append(f"{len(rows)} files +{added}/-{deleted}")

    owned = relative_owned(root, args.owned) if args.owned else []
    shared = relative_owned(root, args.shared_tree) if args.shared_tree else []

    def is_shared(path: str) -> bool:
        return bool(shared) and is_owned(path, shared)

    beyond: list[str] = []
    if owned:
        beyond = [p for p in paths if not is_owned(p, owned) and not is_shared(p)]
        shown = ", ".join(beyond[:5]) + (f" (+{len(beyond) - 5})" if len(beyond) > 5 else "")
        out.append(f"TOUCHED_BEYOND: {shown or 'none'}")
        if beyond:
            flags.append("touched-beyond")
        elif commits and len(rows) <= 3:
            parts[-1] += " only"
        sibling_paths = [p for p in paths if not is_owned(p, owned) and is_shared(p)]
        if sibling_paths:
            out.append(f"shared-tree: {len(sibling_paths)} path(s) belong to sibling authors "
                       "in this worktree, judged by their own verification")
        rs = rust_files(root, owned)
        if rs:
            total, per = count_stubs(root, rs)
            detail = ", ".join(f"{f}:{n}" for f, n in per[:3])
            out.append(f"STUBS_LEFT: {total}" + (f" ({detail})" if detail else "") + f" in {len(rs)} owned .rs file(s)")
            parts.append(f"{total} unimplemented left")
            if total:
                flags.append("stubs")
        else:
            out.append("STUBS_LEFT: not checked (no owned .rs file)")
    else:
        out.append("TOUCHED_BEYOND: not checked (no --owned)")

    report_text: str | None = None
    if args.report:
        try:
            with open(args.report, encoding="utf-8") as handle:
                report_text = handle.read()
        except OSError as err:
            out.append(f"report: cannot read {args.report} ({err})")

    if args.contract_hash:
        expected = args.contract_hash.lower()
        block = None
        if args.payload:
            try:
                with open(args.payload, encoding="utf-8") as fh:
                    block = ph.resolve_contract_block(fh.read())
            except OSError as err:
                out.append(f"contract hash: cannot read the payload ({err})")
            if block is None:
                out.append("contract hash: the payload carries no CONTRACT block")
        if block is not None:
            now = ph.contract_hash(block)
            if now.startswith(expected):
                out.append(f"contract hash: stable ({now[:8]})")
                parts.append("hash stable")
            else:
                out.append(f"contract hash: CHANGED, expected {expected[:8]} now {now[:8]}")
                parts.append("HASH CHANGED")
                flags.append("hash")
        else:
            files = [f for f in args.contract_files.split(",") if f]
            try:
                now = contract_digest(root, files)
            except OSError as err:
                now = ""
                out.append(f"contract hash: cannot read the contract files ({err})")
            if now and now.startswith(expected):
                out.append(f"contract hash: stable ({now[:8]})")
                parts.append("hash stable")
            else:
                if now:
                    out.append(f"contract hash: CHANGED, expected {expected[:8]} now {now[:8]}")
                parts.append("HASH CHANGED")
                flags.append("hash")
        if args.contract_files and block is not None:
            files = [f for f in args.contract_files.split(",") if f]
            try:
                disk = contract_digest(root, files)
                if not disk.startswith(expected):
                    out.append(f"contract files: differ from the stamped contract "
                               f"({disk[:8]} vs {expected[:8]}) — the stubs moved after the stamp")
                    flags.append("contract-files")
            except OSError as err:
                out.append(f"contract files: cannot read the contract files ({err})")

    if args.payload:
        try:
            with open(args.payload, "rb") as fh:
                now = hashlib.sha256(fh.read()).hexdigest()
        except OSError as err:
            now = ""
            out.append(f"payload: cannot read {args.payload} ({err})")
        if now and now.startswith((args.payload_sha256 or "").lower()) and args.payload_sha256:
            out.append(f"payload: stable ({now[:8]})")
        else:
            if now:
                out.append(f"payload: CHANGED, expected {(args.payload_sha256 or '')[:8]} now {now[:8]}")
            parts.append("PAYLOAD CHANGED")
            flags.append("payload")

    own_dirty = [p for p in dirty if not is_shared(p)]
    sibling_dirty = [p for p in dirty if is_shared(p)]
    if own_dirty:
        out.append(f"tree: dirty, {len(own_dirty)} path(s): {', '.join(own_dirty[:3])}")
        parts.append(f"tree dirty ({len(own_dirty)})")
        flags.append("dirty")
    else:
        parts.append("tree exact")
    if sibling_dirty and not own_dirty:
        parts.append(f"tree exact for this author ({len(sibling_dirty)} sibling path(s) not judged here)")

    if args.report:
        tools = agent_tools(args.kind, args.host)
        claims = shell_claims(report_text) if report_text is not None else []
        if tools is None:
            out.append(f"UNVERIFIED: not checked (no tool list for {args.kind} under {args.host})")
        elif claims and not any(t.split("(")[0] == "Bash" for t in tools):
            out.append(f"UNVERIFIED: {args.kind} claims {' and '.join(claims)} but has no Bash; re-run it yourself")
            parts.append(f"UNVERIFIED: claims {' and '.join(claims)}, no Bash")
            flags.append("unverified")
        else:
            out.append("UNVERIFIED: none")
        if report_text is not None and owned and beyond:
            makes_claim, claimed = footprint_claim(report_text)
            if makes_claim:
                unaccounted = [p for p in beyond if p not in claimed]
                if unaccounted:
                    out.append("REPORT-FOOTPRINT: the commit range shows "
                               f"{len(unaccounted)} path(s) the report does not account for: "
                               + ", ".join(unaccounted[:5]))
                    parts.append(f"REPORT-FOOTPRINT: {len(unaccounted)} unaccounted path(s)")
                    flags.append("report-footprint")

    if beyond:
        parts.append("TOUCHED_BEYOND: " + ", ".join(beyond[:3]) + (f" (+{len(beyond) - 3})" if len(beyond) > 3 else ""))
    if not commits:
        parts.insert(0, "NO NEW COMMITS")
    else:
        parts.insert(0, f"{sha} on {branch}")
    return out, flags, parts


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--root", help="the worktree the agent worked in")
    ap.add_argument("--branch", help="the branch it was told to commit on")
    ap.add_argument("--base", help="the sha the worktree forked from")
    ap.add_argument("--owned", default="", help="comma list of the files/dirs/globs the agent owns")
    ap.add_argument("--shared-tree", default="",
                    help="comma list of the sibling authors' paths in the same worktree, "
                         "judged by their own verification, never this one's")
    ap.add_argument("--contract-hash", help="the CONTRACT_HASH the payload carried (full or prefix)")
    ap.add_argument("--contract-files", default="", help="comma list of the contract files, relative to --root")
    ap.add_argument("--payload", metavar="FILE", help="the payload file a workflow spawn read")
    ap.add_argument("--payload-sha256", metavar="H", help="with --payload: the hash it was linted at (full or prefix)")
    ap.add_argument("--release", metavar="HOLDER", help="release this holder's admission slot")
    ap.add_argument("--provider")
    ap.add_argument("--model")
    ap.add_argument("--log", metavar="DOSSIER", help="append the mechanical RETURNED line to this dossier's Build log")
    ap.add_argument("--agent", help='with --log or --print-line: e.g. "P2 (agent a1b2c3)"')
    ap.add_argument("--print-line", action="store_true", help="print the RETURNED line as RETURNED-LINE: ... without logging it")
    ap.add_argument("--report", metavar="FILE", help="the agent's report, read only for claims its tools cannot back")
    ap.add_argument("--kind", help="with --report: the agent kind, e.g. unit-test-author")
    ap.add_argument("--host", default="claude", choices=("claude", "opencode"), help="with --report: where the tool list comes from")
    ap.add_argument("--selftest", action="store_true", help="check the checker")
    args = ap.parse_args(sys.argv[1:] if argv is None else argv)
    if args.selftest:
        return selftest()
    if not (args.root and args.branch and args.base):
        ap.error("--root, --branch and --base are required unless --selftest")
    if args.contract_hash and not (args.contract_files or args.payload):
        ap.error("--contract-hash needs --contract-files (or --payload, whose CONTRACT block it hashes)")
    if args.release and not (args.provider and args.model):
        ap.error("--release needs --provider and --model")
    if args.report and not args.kind:
        ap.error("--report needs --kind")
    if (args.log or args.print_line) and not args.agent:
        ap.error("--log and --print-line need --agent")
    rc, text = git(os.path.abspath(args.root), "rev-parse", "--is-inside-work-tree")
    if rc != 0 or text != "true":
        print(f"verify_return: {args.root} is not a git work tree", file=sys.stderr)
        return 2
    rc, _ = git(os.path.abspath(args.root), "rev-parse", "--verify", "--quiet", f"{args.base}^{{commit}}")
    if rc != 0:
        print(f"verify_return: base {args.base} is not a commit in {args.root}", file=sys.stderr)
        return 2
    for entry in (f for f in args.contract_files.split(",") if f):
        if os.path.isdir(os.path.join(os.path.abspath(args.root), entry)):
            print(f"verify_return: contract file entry is a directory: {entry} — "
                  "compose_payloads.py hashes files only; pass the file list", file=sys.stderr)
            return 2

    out, flags, parts = verify(args)

    if args.release:
        rc, lines = script("spawn_admission.py", "release", "--provider", args.provider, "--model", args.model, "--holder", args.release)
        out.append("release: " + (lines[-1].strip()[:140] if lines else f"exit {rc}"))
        if rc != 0:
            flags.append("release")
    if args.log:
        line = f"{args.agent} RETURNED: " + ", ".join(parts)
        rc, lines = script("dossier_edit.py", "log", args.log, line)
        out.append(("logged: " if rc == 0 else f"LOG FAILED (exit {rc}): {' '.join(lines)[:100]} -- ") + line[:200])
        if rc != 0:
            flags.append("log")

    header: list[str] = []
    if args.print_line:
        header.append(f"RETURNED-LINE: {args.agent} RETURNED: " + ", ".join(parts))
    header.append("verdict: " + ("clean" if not flags else "FLAGGED " + ", ".join(flags)))
    # The RETURNED line and the verdict first: a reader that loses the tail of
    # the output to truncation still has them.
    print("\n".join(clamp(header + out)))
    return 1 if flags else 0


# -------------------------------------------------------------------- selftest


def selftest() -> int:
    import contextlib
    import io

    failures: list[str] = []

    def check(name: str, ok: bool) -> None:
        if not ok:
            failures.append(name)

    def call(argv: list[str]) -> tuple[int, str]:
        buf, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(err):
            try:
                rc = main(argv)
            except SystemExit as exit_:
                rc = int(exit_.code or 0)
        return rc, buf.getvalue() + err.getvalue()

    def put(root: str, name: str, text: str) -> None:
        full = os.path.join(root, name)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8") as fh:
            fh.write(text)

    check("is_owned: file, dir and glob", is_owned("a/b.rs", ["a/b.rs"]) and is_owned("a/b.rs", ["a"]) and is_owned("a/b.rs", ["a/*.rs"]) and not is_owned("ab/c.rs", ["a"]))

    saved_admission = os.environ.get("ASD_ADMISSION_DIR")
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["ASD_ADMISSION_DIR"] = os.path.join(tmp, "admission")
        try:
            root = os.path.join(tmp, "p1")
            os.makedirs(root)
            git(tmp, "init", "-q", root)
            for key, value in (("user.name", "t"), ("user.email", "t@example.com"), ("commit.gpgsign", "false"), ("core.hooksPath", "/dev/null")):
                git(root, "config", key, value)
            put(root, "src/lib.rs", "pub fn a() -> u8 { todo!() }\npub fn b() -> u8 { todo!() }\n// todo!() in a comment\n")
            put(root, "src/other.rs", "pub fn o() {}\n")
            put(root, "contract/c.rs", "pub fn a() -> u8;\n")
            git(root, "add", "-A")
            git(root, "commit", "-q", "-m", "base")
            git(root, "checkout", "-q", "-b", "feat/p1")
            _, base = git(root, "rev-parse", "HEAD")
            digest = contract_digest(root, ["contract/c.rs"])

            # --- a clean return: implemented one stub, one file, contract untouched ---
            put(root, "src/lib.rs", "pub fn a() -> u8 { 1 }\npub fn b() -> u8 { unimplemented!(\"later\") }\n// todo!() in a comment\n")
            git(root, "commit", "-q", "-am", "P1: fill a")
            dossier = os.path.join(tmp, "W-001-x.md")
            put(tmp, "W-001-x.md", "---\nid: W-001\n---\n\n# T\n\n## Build log\n\n- first\n")
            base_args = ["--root", root, "--branch", "feat/p1", "--base", base, "--owned", "src/lib.rs",
                         "--contract-hash", digest[:12], "--contract-files", "contract/c.rs"]
            rc, out = call(base_args)
            check("a one-file return inside --owned has no TOUCHED_BEYOND", "TOUCHED_BEYOND: none" in out)
            check("the new commit and its +/- are listed", "commits: 1 since" in out and "src/lib.rs +2/-2" in out)
            check("stubs left are counted without the comment line", "STUBS_LEFT: 1 (src/lib.rs:1) in 1 owned .rs" in out)
            check("a stable contract hash is reported", f"contract hash: stable ({digest[:8]})" in out)
            check("stubs left flag the return (an implementer must leave none)", rc == 1 and "FLAGGED stubs" in out)
            check("output is bounded", len(out.splitlines()) <= MAX_LINES)

            # --- the payload a workflow spawn read: stable, then changed ---
            put(tmp, "payload.md", "TASK: x\n")
            payload = os.path.join(tmp, "payload.md")
            with open(payload, "rb") as fh:
                payload_sha = hashlib.sha256(fh.read()).hexdigest()
            rc, out = call(base_args + ["--payload", payload, "--payload-sha256", payload_sha])
            check("a stable payload is reported", f"payload: stable ({payload_sha[:8]})" in out and "payload" not in out.splitlines()[0])
            rc, out = call(base_args + ["--payload", payload, "--payload-sha256", "0" * 64])
            check("a changed payload flags the return", "payload: CHANGED" in out and rc == 1 and "payload" in out.split("FLAGGED")[-1])

            # --- a fully clean return, with release and log ---
            put(root, "src/lib.rs", "pub fn a() -> u8 { 1 }\npub fn b() -> u8 { 2 }\n// todo!() in a comment\n")
            git(root, "commit", "-q", "-am", "P1: fill b")
            acquire = subprocess.run([sys.executable, os.path.join(SCRIPTS_DIR, "spawn_admission.py"), "acquire", "--provider", "p", "--model", "m", "--n", "1", "--holder", "W-001-p1"],
                                     capture_output=True, text=True, check=False)
            check("selftest fixture: slot acquired", acquire.returncode == 0)
            rc, out = call(base_args + ["--release", "W-001-p1", "--provider", "p", "--model", "m", "--log", dossier, "--agent", "P1 (ses_x)"])
            log_text = open(dossier, encoding="utf-8").read()
            check("a clean return exits 0 with a clean verdict", rc == 0 and "verdict: clean" in out and "STUBS_LEFT: 0" in out)
            check("the release line is echoed", "release: " in out and "released" in out.lower())
            check("the mechanical RETURNED line is appended to the Build log",
                  re.search(r"- P1 \(ses_x\) RETURNED: [0-9a-f]+ on feat/p1, lib\.rs \+2/-2 only, 0 unimplemented left, hash stable, tree exact\n$", log_text) is not None)
            check("the log line is echoed", "logged: P1 (ses_x) RETURNED:" in out)
            rc, _ = call(["--root", root, "--branch", "feat/p1", "--base", base, "--release", "W-001-p1", "--provider", "p", "--model", "m"])
            check("releasing a slot that is no longer held is flagged", rc == 1)

            # --- touched beyond, changed contract, dirty tree, wrong branch ---
            put(root, "src/other.rs", "pub fn o() { /* edited */ }\n")
            put(root, "contract/c.rs", "pub fn a() -> u16;\n")
            git(root, "commit", "-q", "-am", "P1: strays")
            put(root, "scratch.txt", "x\n")
            rc, out = call(["--root", root, "--branch", "feat/other", "--base", base, "--owned", "src/lib.rs",
                            "--contract-hash", digest, "--contract-files", "contract/c.rs"])
            check("TOUCHED_BEYOND names the strays, committed and dirty", "TOUCHED_BEYOND: contract/c.rs, scratch.txt, src/other.rs" in out)
            check("a changed contract is flagged", "contract hash: CHANGED" in out and "hash" in out.splitlines()[0])
            check("a dirty tree is flagged", "tree: dirty, 1 path(s): scratch.txt" in out)
            check("the wrong branch is flagged", "BRANCH: HEAD is on feat/p1, expected feat/other" in out)
            check("every flag reaches the verdict, printed first",
                  rc == 1 and out.splitlines()[0].startswith("verdict: FLAGGED")
                  and all(w in out.splitlines()[0] for w in ("branch", "touched-beyond", "hash", "dirty")))

            # --- the report's own footprint claims, against this commit range ---
            put(tmp, "fp-clean.md", "Done.\nTOUCHED_BEYOND: none\nno hook rewrote my files\n")
            put(tmp, "fp-honest.md", "Done.\nTOUCHED_BEYOND: contract/c.rs, scratch.txt, src/other.rs\n")

            def with_owned_report(name: str) -> tuple[int, str]:
                return call(["--root", root, "--branch", "feat/p1", "--base", base,
                             "--report", os.path.join(tmp, name), "--kind", "writer",
                             "--owned", "src/lib.rs"])

            rc, out = with_owned_report("fp-clean.md")
            check("a footprint claim the commit range contradicts is flagged, paths named",
                  "REPORT-FOOTPRINT: the commit range shows 3 path(s) the report does not account for" in out
                  and "src/other.rs" in out and "report-footprint" in out.splitlines()[0])
            rc, out = with_owned_report("fp-honest.md")
            check("a report that accounts for every range path raises no footprint flag",
                  "REPORT-FOOTPRINT" not in out)
            check("a hook claim with no TOUCHED_BEYOND line claims a clean footprint",
                  footprint_claim("no hook rewrote my files\n") == (True, [])
                  and footprint_claim("TOUCHED_BEYOND: a.rs, b.rs\n") == (True, ["a.rs", "b.rs"])
                  and footprint_claim("all green\n") == (False, []))

            # --- no new commits; a directory owned; --log records the flagged facts ---
            git(root, "checkout", "-q", "-b", "feat/empty", base)
            put(root, "scratch.txt", "")
            os.remove(os.path.join(root, "scratch.txt"))
            rc, out = call(["--root", root, "--branch", "feat/empty", "--base", base, "--owned", "src", "--log", dossier, "--agent", "P2 (ses_y)"])
            check("no new commits is flagged", rc == 1 and "NO NEW COMMITS" in out and "no-commits" in out)
            check("the flagged return is logged as flagged", "- P2 (ses_y) RETURNED: NO NEW COMMITS" in open(dossier, encoding="utf-8").read())
            check("an owned directory counts its .rs files for stubs", "STUBS_LEFT: 2" in out or "STUBS_LEFT: 2 (" in out)

            # --- a report claiming a run its tools cannot make ---
            agents = os.path.join(tmp, "agents")
            put(agents, "writer.md", "---\nname: writer\ntools: Read, Grep, Write\n---\nbody\n")
            put(agents, "builder.md", "---\nname: builder\ntools: Read, Bash, Edit\n---\nbody\n")
            put(tmp, "claim.md", "Wrote the tests.\nI ran cargo test, 12 passed.\n")
            put(tmp, "quiet.md", "Wrote the tests.\n> 12 passed (quoted from the brief)\n```\nexit code 0\n```\n")
            check("agent_tools reads the tools: line", agent_tools("writer", "claude", agents) == ["Read", "Grep", "Write"])
            check("opencode and unknown kinds have no tool list", agent_tools("writer", "opencode", agents) is None and agent_tools("nope", "claude", agents) is None)
            check("shell_claims finds a test run and skips quotes and fences",
                  shell_claims("ran pytest\nexit code 1") == ["a test run", "an exit code"] and shell_claims("> 12 passed\n```\nexit code 0\n```\n") == [])
            report_args = ["--root", root, "--branch", "feat/p1", "--base", base]
            global AGENTS_DIR
            saved_dir, AGENTS_DIR = AGENTS_DIR, agents
            try:
                def with_report(kind: str, name: str) -> tuple[int, str]:
                    return call(report_args + ["--report", os.path.join(tmp, name), "--kind", kind])
                rc, out = with_report("writer", "claim.md")
                check("a Write-only agent claiming a test run is flagged",
                      "UNVERIFIED: writer claims a test run but has no Bash; re-run it yourself" in out and "unverified" in out.splitlines()[0])
                rc, out = with_report("builder", "claim.md")
                check("the same claim from an agent with Bash is clean", "UNVERIFIED: none" in out and "unverified" not in out.splitlines()[0])
                rc, out = with_report("writer", "quiet.md")
                check("no claim is clean, quotes and fences included", "UNVERIFIED: none" in out)
            finally:
                AGENTS_DIR = saved_dir

            # --- porcelain -z: modified and untracked paths at the first
            # --- directory level parse whole, first character included ---
            check("porcelain -z records split on NUL, the rename original skipped",
                  parse_porcelain_z("R  new_name.rs\0old_name.rs\0?? src/x.rs\0 M y.rs\0")
                  == ["new_name.rs", "src/x.rs", "y.rs"])
            git(root, "checkout", "-q", "-b", "feat/porcelain", base)
            put(root, "Source/Tests/X.cs", "a\n")
            put(root, "Infrastructure/tests/plan_counts.sh", "b\n")
            put(root, "src/lib.rs", "pub fn a() -> u8 { 1 }\n")
            git(root, "commit", "-q", "-am", "P1: fill a")
            rc, out = call(["--root", root, "--branch", "feat/porcelain", "--base", base,
                            "--owned", "Source,Infrastructure,src"])
            check("an untracked and a modified path at the first directory level keep their first character",
                  "tree: dirty, 2 path(s): Infrastructure/tests/plan_counts.sh, Source/Tests/X.cs" in out
                  and "TOUCHED_BEYOND: none" in out)
            git(root, "clean", "-qfd")

            # --- a shared worktree: the sibling's paths are not this author's ---
            git(root, "checkout", "-q", "-b", "feat/shared", base)
            put(root, "src/lib.rs", "pub fn a() -> u8 { 1 }\npub fn b() -> u8 { 2 }\n")
            git(root, "commit", "-q", "-am", "P1: fill both")
            put(root, "src/sibling.rs", "pub fn s() {}\n")
            rc, out = call(["--root", root, "--branch", "feat/shared", "--base", base,
                            "--owned", "src/lib.rs", "--shared-tree", "src/sibling.rs"])
            check("a sibling author's paths are not this author's TOUCHED_BEYOND",
                  "TOUCHED_BEYOND: none" in out and "shared-tree: 1 path(s)" in out)
            check("a sibling's dirty file does not dirty this author's tree", "tree dirty" not in out)
            check("the shared-tree return is clean", rc == 0)

            # --- CONTRACT_HASH: the payload's CONTRACT block, the composer's recipe ---
            block_payload = ("TASK: implement\nCONTRACT: |\n  pub fn a() -> u8;\n\n"
                             "OWNED_PATHS: src/lib.rs\n")
            put(tmp, "p-block.md", block_payload)
            put(tmp, "p-block-outside.md", block_payload.replace("TASK: implement", "TASK: implement now"))
            put(tmp, "p-block-inside.md", block_payload.replace("u8;", "u16;"))
            block_hash = ph.contract_hash("pub fn a() -> u8;\n")

            def with_block(name: str, **extra: str) -> tuple[int, str]:
                block_path = os.path.join(tmp, name)
                with open(block_path, "rb") as fh:
                    block_sha = hashlib.sha256(fh.read()).hexdigest()
                argv = ["--root", root, "--branch", "feat/shared", "--base", base,
                        "--owned", "src/lib.rs", "--shared-tree", "src/sibling.rs",
                        "--contract-hash", block_hash, "--payload", block_path,
                        "--payload-sha256", block_sha]
                for key, value in extra.items():
                    argv += ["--" + key.replace("_", "-"), value]
                return call(argv)

            rc, out = with_block("p-block.md", contract_files="contract/c.rs")
            check("the CONTRACT block hash is the composer's canonical recipe",
                  f"contract hash: stable ({block_hash[:8]})" in out)
            check("contract files matching the stamp are not flagged", "contract files:" not in out)
            rc, out = with_block("p-block-outside.md")
            check("payload bytes changed outside CONTRACT raise no HASH-FLAG", "contract hash: stable" in out)
            rc, out = with_block("p-block-inside.md")
            check("a one-byte edit inside the CONTRACT block always flags",
                  "contract hash: CHANGED" in out and "hash" in out.splitlines()[0])
            rc, out = with_block("p-block.md", contract_files="src/lib.rs")
            check("stub files that moved after the stamp are named",
                  "contract files: differ from the stamped contract" in out
                  and "contract-files" in out.splitlines()[0])
            rc, _ = call(["--root", root, "--branch", "feat/shared", "--base", base,
                          "--owned", "src", "--contract-hash", "a" * 64, "--contract-files", "contract"])
            check("a directory in --contract-files is refused as the composer refuses it", rc == 2)
            check("extract_contract_block de-indents and stops at the next field",
                  ph.contract_hash(ph.extract_contract_block("CONTRACT: |\n  a\n\n  b\n\nNEXT: x\n"))
                  == ph.contract_hash("a\n\nb\n"))

            # --- unusable input ---
            git(root, "checkout", "-q", "feat/empty")
            # --print-line echoes the RETURNED line and writes nothing
            before = open(dossier, encoding="utf-8").read()
            rc, out = call(["--root", root, "--branch", "feat/empty", "--base", base, "--owned", "src", "--print-line", "--agent", "P3 (agent a1b2)"])
            check("--print-line echoes the RETURNED line first, the verdict second",
                  "RETURNED-LINE: P3 (agent a1b2) RETURNED: NO NEW COMMITS" in out
                  and out.splitlines()[0].startswith("RETURNED-LINE:") and out.splitlines()[1].startswith("verdict:"))
            check("--print-line writes no log", open(dossier, encoding="utf-8").read() == before)
            rc, _ = call(["--root", root, "--branch", "b", "--base", base, "--print-line"])
            check("--print-line without --agent is unusable input", rc == 2)
            rc, _ = call(["--root", tmp, "--branch", "b", "--base", base])
            check("a non-repo root is unusable input", rc == 2)
            rc, _ = call(["--root", root, "--branch", "b", "--base", "deadbeef"])
            check("an unknown base is unusable input", rc == 2)
            for bad in (["--contract-hash", "ab"], ["--release", "h"], ["--log", dossier]):
                rc, _ = call(["--root", root, "--branch", "b", "--base", base, *bad])
                check(f"{bad[0]} without its companions is unusable input", rc == 2)
        finally:
            if saved_admission is None:
                os.environ.pop("ASD_ADMISSION_DIR", None)
            else:
                os.environ["ASD_ADMISSION_DIR"] = saved_admission

    for item in failures:
        print("SELFTEST FAIL  " + item)
    print(f"selftest: {len(failures)} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
