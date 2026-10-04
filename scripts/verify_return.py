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
agent's own report — every verdict comes from git and the files on disk.

# Usage

    verify_return.py --root X --branch B --base SHA
        [--owned p1,p2]
        [--contract-hash H --contract-files f1,f2]
        [--release HOLDER --provider P --model M]
        [--log DOSSIER --agent ID]
    verify_return.py --selftest

`--root` is the worktree the agent worked in; `--branch` the branch it was told
to commit on; `--base` the sha that worktree forked from.

What it prints (one line each unless noted):

- the worktree's state, from `git_state.py`: branch, HEAD, ahead/behind of the
  base, in-progress operation, status counts;
- `BRANCH` when HEAD is not on `--branch`;
- the new commits (`BASE..HEAD`, up to 4 shown) and the per-file `+added/-deleted`
  of their combined diff (up to 5 files); `NO NEW COMMITS` when there are none;
- `TOUCHED_BEYOND`: every file the commits or the working tree changed that is
  not under `--owned` (a comma list of files, directories, or globs; absolute
  paths inside the root are accepted) — `none` when all are owned;
- `STUBS_LEFT`: the number of `unimplemented!(` / `todo!(` calls left in the owned
  Rust files (comment lines skipped); an implementer's answer is 0;
- the contract hash: sha256 of `--contract-files` (relative to `--root`, joined as
  compose_payloads.py joins them) against `--contract-hash` (a full hash or a
  prefix) — `stable` or `CHANGED`;
- the release of the agent's admission slot, when `--release HOLDER` is given
  (with `--provider` and `--model`): spawn_admission.py's own last line;
- with `--log DOSSIER --agent ID`: the mechanical half of the build-log line is
  appended through `dossier_edit.py` and echoed:
  `<ID> RETURNED: <sha> on <branch>, <files>, <n> unimplemented left, hash stable,
  tree exact` (plus `, TOUCHED_BEYOND: ...` when there is any). The orchestrator
  adds only its ruling sentence, if there is one. The line states facts, flagged
  ones included, so a flagged return is logged as flagged.

The release and the log happen whether or not a check flagged anything: the
agent is finished either way, and the log must say what it did.

Exit codes: 0 every check clean; 1 something is flagged (no commits, a file
outside `--owned`, stubs left, a changed contract hash, a dirty tree, the wrong
branch, a failed release or log); 2 unusable input.

No third-party imports: this runs wherever python3 and git do.
"""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import os
import re
import subprocess
import sys
import tempfile

GIT = "/usr/bin/git" if os.path.exists("/usr/bin/git") else "git"
SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
MAX_LINES = 20
STUB_RE = re.compile(r"\b(?:unimplemented|todo)!\s*\(")
MAX_RS_FILES = 200


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
    _, porcelain = git(root, "status", "--porcelain", "--untracked-files=all")
    dirty = [l[3:].split(" -> ")[-1] for l in porcelain.splitlines() if len(l) > 3]
    paths = sorted({r[2] for r in rows} | set(dirty))
    return paths, rows, dirty  # type: ignore[return-value]


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
    return hashlib.sha256("\n\n".join(texts).encode("utf-8")).hexdigest()


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
    beyond: list[str] = []
    if owned:
        beyond = [p for p in paths if not is_owned(p, owned)]
        shown = ", ".join(beyond[:5]) + (f" (+{len(beyond) - 5})" if len(beyond) > 5 else "")
        out.append(f"TOUCHED_BEYOND: {shown or 'none'}")
        if beyond:
            flags.append("touched-beyond")
        elif commits and len(rows) <= 3:
            parts[-1] += " only"
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

    if args.contract_hash:
        files = [f for f in args.contract_files.split(",") if f]
        try:
            now = contract_digest(root, files)
        except OSError as err:
            now = ""
            out.append(f"contract hash: cannot read the contract files ({err})")
        if now and now.startswith(args.contract_hash.lower()):
            out.append(f"contract hash: stable ({now[:8]})")
            parts.append("hash stable")
        else:
            if now:
                out.append(f"contract hash: CHANGED, expected {args.contract_hash[:8]} now {now[:8]}")
            parts.append("HASH CHANGED")
            flags.append("hash")

    if dirty:
        out.append(f"tree: dirty, {len(dirty)} path(s): {', '.join(dirty[:3])}")
        parts.append(f"tree dirty ({len(dirty)})")
        flags.append("dirty")
    else:
        parts.append("tree exact")

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
    ap.add_argument("--contract-hash", help="the CONTRACT_HASH the payload carried (full or prefix)")
    ap.add_argument("--contract-files", default="", help="comma list of the contract files, relative to --root")
    ap.add_argument("--release", metavar="HOLDER", help="release this holder's admission slot")
    ap.add_argument("--provider")
    ap.add_argument("--model")
    ap.add_argument("--log", metavar="DOSSIER", help="append the mechanical RETURNED line to this dossier's Build log")
    ap.add_argument("--agent", help='with --log: e.g. "P2 (ses_x)"')
    ap.add_argument("--selftest", action="store_true", help="check the checker")
    args = ap.parse_args(sys.argv[1:] if argv is None else argv)
    if args.selftest:
        return selftest()
    if not (args.root and args.branch and args.base):
        ap.error("--root, --branch and --base are required unless --selftest")
    if args.contract_hash and not args.contract_files:
        ap.error("--contract-hash needs --contract-files")
    if args.release and not (args.provider and args.model):
        ap.error("--release needs --provider and --model")
    if args.log and not args.agent:
        ap.error("--log needs --agent")
    rc, text = git(os.path.abspath(args.root), "rev-parse", "--is-inside-work-tree")
    if rc != 0 or text != "true":
        print(f"verify_return: {args.root} is not a git work tree", file=sys.stderr)
        return 2
    rc, _ = git(os.path.abspath(args.root), "rev-parse", "--verify", "--quiet", f"{args.base}^{{commit}}")
    if rc != 0:
        print(f"verify_return: base {args.base} is not a commit in {args.root}", file=sys.stderr)
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

    out.append("verdict: " + ("clean" if not flags else "FLAGGED " + ", ".join(flags)))
    print("\n".join(clamp(out)))
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
            check("a changed contract is flagged", "contract hash: CHANGED" in out and "hash" in out.splitlines()[-1])
            check("a dirty tree is flagged", "tree: dirty, 1 path(s): scratch.txt" in out)
            check("the wrong branch is flagged", "BRANCH: HEAD is on feat/p1, expected feat/other" in out)
            check("every flag reaches the verdict", rc == 1 and all(w in out.splitlines()[-1] for w in ("branch", "touched-beyond", "hash", "dirty")))

            # --- no new commits; a directory owned; --log records the flagged facts ---
            git(root, "checkout", "-q", "-b", "feat/empty", base)
            put(root, "scratch.txt", "")
            os.remove(os.path.join(root, "scratch.txt"))
            rc, out = call(["--root", root, "--branch", "feat/empty", "--base", base, "--owned", "src", "--log", dossier, "--agent", "P2 (ses_y)"])
            check("no new commits is flagged", rc == 1 and "NO NEW COMMITS" in out and "no-commits" in out)
            check("the flagged return is logged as flagged", "- P2 (ses_y) RETURNED: NO NEW COMMITS" in open(dossier, encoding="utf-8").read())
            check("an owned directory counts its .rs files for stubs", "STUBS_LEFT: 2" in out or "STUBS_LEFT: 2 (" in out)

            # --- unusable input ---
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
