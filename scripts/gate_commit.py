#!/usr/bin/env python3
"""Run the gates, stage ONLY the named paths, commit, optionally push — one call, at most 12 lines.

The recorded runs made 130 commits by hand and chained the gates in front of
each one in whatever order came to mind (`typos`, `cargo fmt`, a doc check,
`git add <paths> && git commit -S`, `git push`): 246 calls, five to nine model
steps per commit, each step re-billing the whole context. 57 of the 130 commits
held nothing but a dossier. The chain was also where the invariants slipped: a
`git add -A` once swept an unrelated file into a contract commit. This script
is the chain as one process, with the invariants enforced instead of
remembered: the gates run first and a failing gate stops everything, only the
named paths are staged (it has no way to stage anything else), a path that has
no change is a refusal rather than an empty commit, and the output is a verdict
instead of a transcript.

# Usage

    gate_commit.py --root X --paths p1,p2 -m MESSAGE
                   [--gates FILE] [--sign] [--no-verify] [--push]
                   [--forbid-id DOSSIER_ID]
    gate_commit.py --selftest

`--paths` is a comma list of files or directories, relative to `--root` (or
absolute inside it). `.`, the root itself and a path outside the root are
refused: there is no way to say "everything". Every named path must have a
change (modified, deleted, or untracked) or the call is refused before any
gate runs.

`--forbid-id ID` (local mode: the dossier is gitignored, so its id means
nothing in a commit) refuses the commit when ID appears in the commit
message, a named path, a file name under it, or the text of a file under it,
and names each. Nothing is staged. The Phase 2 contract commit and the Phase
5 test commit pass the dossier id; cite the Jira key instead.

`--gates FILE` runs `run_gates.py FILE --root X` first (`run_gates.py --from-workflow` generates one from CI; the gates file format is
documented there). The gates see the named paths in `$GATE_PATHS`, space
separated, so a gate may narrow itself to them. A failing gate refuses the
commit: nothing is staged, nothing committed. Without `--gates` no gate runs,
and the output says so.

The commit is `git add -- PATHS` then `git commit -m MESSAGE -- PATHS`, so a
change already staged elsewhere in the index stays out of it. `--sign` adds
`-S`; `--no-verify` skips the repository's commit hooks (for a phase that logs
that policy). `--push` then runs `git push`, or `git push -u origin HEAD` when
the branch has no upstream yet; a failed push leaves the commit in place and
exits 1.

Output (at most 12 lines): the gate verdict lines, `commit: <sha> <subject>
(N file(s))`, `push: ...` when asked, and `left: N path(s) still uncommitted`.

Exit codes: 0 committed (and pushed, when asked); 1 refused (a gate failed, a
path has no change, the commit or the push failed); 2 unusable input.

No third-party imports: this runs wherever python3 and git do.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import tempfile

GIT = "/usr/bin/git" if os.path.exists("/usr/bin/git") else "git"
MAX_LINES = 12
SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))


def git(root: str, *args: str) -> tuple[int, str]:
    proc = subprocess.run([GIT, "-C", root, *args], capture_output=True, text=True, check=False)
    return proc.returncode, (proc.stdout + proc.stderr).strip()


def clamp(lines: list[str]) -> list[str]:
    """At most MAX_LINES lines, the overflow named in the last one."""
    if len(lines) <= MAX_LINES:
        return lines
    return lines[: MAX_LINES - 1] + [f"(+{len(lines) - MAX_LINES + 1} more line(s))"]


def normalise_paths(root: str, raw: str) -> list[str]:
    """Comma list -> paths relative to root; refuses anything that could mean 'everything'."""
    out: list[str] = []
    real_root = os.path.realpath(root)
    for item in (p.strip() for p in raw.split(",")):
        if not item:
            continue
        absolute = os.path.realpath(item if os.path.isabs(item) else os.path.join(root, item))
        rel = os.path.relpath(absolute, real_root)
        if rel == "." or rel == ".." or rel.startswith(".." + os.sep):
            raise ValueError(f"path {item!r} is the root or outside it; name the files to commit")
        if re.search(r"[*?\[]", rel):
            raise ValueError(f"path {item!r} is a glob; name the files to commit")
        out.append(rel)
    if not out:
        raise ValueError("--paths names no path")
    return out


def run_gates(root: str, gates: str, paths: list[str]) -> tuple[bool, list[str]]:
    env = dict(os.environ, GATE_PATHS=" ".join(paths))
    proc = subprocess.run([sys.executable, os.path.join(SCRIPTS_DIR, "run_gates.py"), gates, "--root", root],
                          capture_output=True, text=True, check=False, env=env)
    lines = (proc.stdout + proc.stderr).splitlines()
    kept: list[str] = []
    excerpt = 0
    for line in lines:
        if line.startswith("run_gates: logging to"):
            continue
        if line.startswith(" "):  # a failure's excerpt: three lines at most in all
            if excerpt < 3:
                kept.append(line.rstrip())
                excerpt += 1
            continue
        if line.startswith("PASS "):
            continue  # the summary line counts them
        kept.append(line.rstrip())
    return proc.returncode == 0, kept


def commit_cmd(message: str, paths: list[str], sign: bool, no_verify: bool) -> list[str]:
    cmd = ["commit", "-m", message]
    if sign:
        cmd.append("-S")
    if no_verify:
        cmd.append("--no-verify")
    return cmd + ["--", *paths]


def forbidden_id_hits(root: str, paths: list[str], dossier_id: str) -> list[str]:
    """Files under `paths` whose name or text carries `dossier_id`."""
    files: list[str] = []
    for rel in paths:
        full = os.path.join(root, rel)
        if os.path.isdir(full):
            for base, dirs, names in os.walk(full):
                dirs[:] = [d for d in dirs if d != ".git"]
                files += [os.path.relpath(os.path.join(base, n), root) for n in names]
        else:
            files.append(rel)
    hits = []
    for rel in files:
        if dossier_id in rel:
            hits.append(f"{rel} (path)")
            continue
        try:
            with open(os.path.join(root, rel), encoding="utf-8", errors="ignore") as fh:
                if dossier_id in fh.read():
                    hits.append(f"{rel} (text)")
        except OSError:
            pass
    return hits


# Test files, fixtures and corpora: a rewrite there changes what a test proves.
TEST_PATH_RE = re.compile(r"(^|/)(tests?|testdata|fixtures?|corpora|corpus|spec)(/|$)|(_test|_tests|\.test|\.spec)\.[\w]+$|(^|/)test_[^/]+$")


def snapshot(root: str, paths: list[str]) -> dict[str, bytes]:
    """The bytes of every file under the named paths, as the author left them."""
    out: dict[str, bytes] = {}
    for rel in paths:
        full = os.path.join(root, rel)
        files = [os.path.join(b, n) for b, d, ns in os.walk(full) for n in ns if ".git" not in d] if os.path.isdir(full) else [full]
        for f in files:
            try:
                with open(f, "rb") as fh:
                    out[os.path.relpath(f, root)] = fh.read()
            except OSError:
                pass
    return out


def hook_rewrites(root: str, before: dict[str, bytes]) -> dict[str, list[tuple[str, str]]]:
    """Files whose committed bytes differ from the author's: {path: [(old line, new line), ...]}."""
    import difflib
    out: dict[str, list[tuple[str, str]]] = {}
    for rel, old in before.items():
        p = subprocess.run([GIT, "-C", root, "show", f"HEAD:{rel}"], capture_output=True)
        if p.returncode != 0 or p.stdout == old:
            continue
        a, b = old.decode("utf-8", "replace").splitlines(), p.stdout.decode("utf-8", "replace").splitlines()
        pairs = []
        for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b).get_opcodes():
            if tag != "equal":
                pairs += list(zip(a[i1:i2] or [""], b[j1:j2] or [""]))
        out[rel] = pairs or [("(bytes)", "(bytes)")]
    return out


def refuse(lines: list[str], reason: str) -> int:
    print("\n".join(clamp([*lines, f"REFUSED: {reason}"])))
    return 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--root", help="the worktree to commit in")
    ap.add_argument("--paths", help="comma list of the files or directories to stage and commit")
    ap.add_argument("-m", "--message", help="the commit message")
    ap.add_argument("--gates", help="a run_gates.py gates file; its gates run before anything is staged")
    ap.add_argument("--sign", action="store_true", help="commit with -S")
    ap.add_argument("--no-verify", action="store_true", help="skip the repository's commit hooks")
    ap.add_argument("--push", action="store_true", help="push after the commit")
    ap.add_argument("--forbid-id", metavar="DOSSIER_ID",
                    help="local mode: refuse when this dossier id is in a named path or a file's text")
    ap.add_argument("--selftest", action="store_true", help="check the checker")
    args = ap.parse_args(sys.argv[1:] if argv is None else argv)
    if args.selftest:
        return selftest()
    if not (args.root and args.paths and args.message):
        ap.error("--root, --paths and -m are required unless --selftest")
    root = os.path.abspath(args.root)
    rc, out = git(root, "rev-parse", "--is-inside-work-tree")
    if rc != 0 or out != "true":
        print(f"gate_commit: {root} is not a git work tree", file=sys.stderr)
        return 2
    try:
        paths = normalise_paths(root, args.paths)
    except ValueError as err:
        print(f"gate_commit: {err}", file=sys.stderr)
        return 2
    if args.gates and not os.path.isfile(args.gates):
        print(f"gate_commit: gates file not found: {args.gates}", file=sys.stderr)
        return 2

    out_lines: list[str] = []
    if args.forbid_id:
        hits = forbidden_id_hits(root, paths, args.forbid_id)
        if args.forbid_id.lower() in (args.message or "").lower():
            hits.insert(0, "the commit message")
        if hits:
            return refuse(out_lines, f"the dossier id {args.forbid_id} is local and gitignored; "
                          f"cite the Jira key instead, in: " + ", ".join(hits[:4]))
    for path in paths:
        rc, status = git(root, "status", "--porcelain", "--untracked-files=all", "--", path)
        if rc != 0:
            return refuse(out_lines, f"git status failed for {path}: {status[:120]}")
        if not status.strip():
            return refuse(out_lines, f"{path} is unchanged; nothing to commit for it")

    if args.gates:
        ok, gate_lines = run_gates(root, args.gates, paths)
        out_lines += gate_lines
        if not ok:
            return refuse(out_lines, "a gate failed; nothing staged, nothing committed")
    else:
        out_lines.append("gates: none given (no --gates file)")

    # #262: snapshot the author's bytes before the commit hooks run. A hook that rewrites a staged file (a spell
    # fixer with --write-changes, a formatter) changes a test's input or expected literal and the test can still pass.
    before = snapshot(root, paths)
    rc, text = git(root, "add", "--", *paths)
    if rc != 0:
        return refuse(out_lines, f"git add failed: {text[:200]}")
    rc, text = git(root, *commit_cmd(args.message, paths, args.sign, args.no_verify))
    if rc != 0:
        tail = [l for l in text.splitlines() if l.strip()][-3:]
        return refuse(out_lines + ["  " + l[:200] for l in tail], "git commit failed (the paths stay staged)")
    _, sha = git(root, "rev-parse", "--short", "HEAD")
    _, subject = git(root, "log", "-1", "--format=%s")
    _, names = git(root, "show", "--name-only", "--format=", "HEAD")
    out_lines.append(f"commit: {sha} {subject[:80]} ({len(names.splitlines())} file(s))")

    code = 0
    rewrites = hook_rewrites(root, before)
    for f, hunks in rewrites.items():
        kind = "HOOK-REWROTE-TEST" if TEST_PATH_RE.search(f) else "hook rewrote"
        for old, new in hunks[:6]:
            out_lines.append(f"{kind}: {f}: `{old.strip()[:90]}` -> `{new.strip()[:90]}`")
    if any(TEST_PATH_RE.search(f) for f in rewrites):
        out_lines.append("HOOK-REWROTE-TEST: a commit hook changed a test file after its author wrote it. A changed literal is a changed oracle: "
                         "restore the author's bytes, exempt them in the hook's config by the narrowest pattern, and commit again; "
                         "a green suite after the rewrite is no evidence.")
        code = 3
    if args.push:
        rc, _ = git(root, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
        rc, text = git(root, "push") if rc == 0 else git(root, "push", "-u", "origin", "HEAD")
        if rc == 0:
            out_lines.append("push: ok")
        else:
            tail = [l for l in text.splitlines() if l.strip()][-2:]
            out_lines.append("push: FAILED " + " | ".join(t[:120] for t in tail) + " (the commit stays)")
            code = 1
    _, left = git(root, "status", "--porcelain")
    out_lines.append(f"left: {len(left.splitlines())} path(s) still uncommitted")
    print("\n".join(clamp(out_lines)))
    return code


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
            rc = main(argv)
        return rc, buf.getvalue() + err.getvalue()

    def write(root: str, name: str, text: str) -> None:
        with open(os.path.join(root, name), "w", encoding="utf-8") as fh:
            fh.write(text)

    check("commit_cmd adds -S only with --sign", "-S" in commit_cmd("m", ["a"], True, False) and "-S" not in commit_cmd("m", ["a"], False, False))
    check("commit_cmd ends with the pathspec", commit_cmd("m", ["a", "b"], False, True)[-3:] == ["--", "a", "b"] and "--no-verify" in commit_cmd("m", ["a"], False, True))
    check("clamp bounds the output", len(clamp([str(i) for i in range(40)])) == MAX_LINES and clamp(["a"]) == ["a"])

    with tempfile.TemporaryDirectory() as tmp:
        root = os.path.join(tmp, "work")
        remote = os.path.join(tmp, "remote.git")
        os.makedirs(root)
        git(tmp, "init", "--bare", "-q", remote)
        git(tmp, "init", "-q", root)
        for key, value in (("user.name", "t"), ("user.email", "t@example.com"), ("commit.gpgsign", "false"), ("core.hooksPath", "/dev/null")):
            git(root, "config", key, value)
        git(root, "remote", "add", "origin", remote)
        write(root, "a.txt", "a1\n")
        write(root, "b.txt", "b1\n")
        git(root, "add", "a.txt", "b.txt")
        git(root, "commit", "-q", "-m", "base")
        git(root, "push", "-q", "-u", "origin", "HEAD")
        _, base = git(root, "rev-parse", "HEAD")

        # Only the named path is committed; the other change stays in the tree.
        write(root, "a.txt", "a2\n")
        write(root, "b.txt", "b2\n")
        rc, out = call(["--root", root, "--paths", "a.txt", "-m", "change a"])
        _, names = git(root, "show", "--name-only", "--format=", "HEAD")
        check("commits exactly the named path", rc == 0 and names == "a.txt")
        check("the other change stays uncommitted", git(root, "status", "--porcelain")[1].strip() == "M b.txt" and "left: 1 path(s)" in out)
        check("no gates file says so", "gates: none given" in out and "commit: " in out)

        # --forbid-id: a dossier id in a file's text or a path refuses, nothing staged.
        write(root, "b.txt", "see W-123 for context\n")
        _, head0 = git(root, "rev-parse", "HEAD")
        rc, out = call(["--root", root, "--paths", "b.txt", "-m", "x", "--forbid-id", "W-123"])
        check("--forbid-id refuses a file carrying the id", rc == 1 and "W-123" in out and "b.txt (text)" in out
              and git(root, "rev-parse", "HEAD")[1] == head0 and not git(root, "diff", "--cached", "--name-only")[1].strip())
        write(root, "test_w-123.txt", "x\n")
        write(root, "c.txt", "clean\n")
        rc, out = call(["--root", root, "--paths", "c.txt", "-m", "PROJ-9 contract for w-123", "--forbid-id", "W-123"])
        check("--forbid-id refuses a message carrying the id (any case), naming it", rc == 1 and "the commit message" in out and "W-123" in out and "c.txt" not in out.split("in:")[-1])
        rc, out = call(["--root", root, "--paths", "test_w-123.txt", "-m", "x", "--forbid-id", "w-123"])
        check("--forbid-id refuses an id in a path", rc == 1 and "(path)" in out)
        os.remove(os.path.join(root, "test_w-123.txt"))
        write(root, "b.txt", "b2\n")

        # An unchanged path is refused before anything happens.
        _, head = git(root, "rev-parse", "HEAD")
        rc, out = call(["--root", root, "--paths", "a.txt", "-m", "again"])
        check("an unchanged path is refused", rc == 1 and "a.txt is unchanged" in out and git(root, "rev-parse", "HEAD")[1] == head)

        # A failing gate refuses; nothing is staged.
        write(root, "a.txt", "a3\n")
        gates = os.path.join(tmp, "gates.txt")
        write(tmp, "gates.txt", "good: true\nbad: echo DEFECT: nope; exit 3\n")
        rc, out = call(["--root", root, "--paths", "a.txt", "-m", "gated", "--gates", gates])
        check("a failing gate refuses the commit", rc == 1 and "FAIL bad" in out and "a gate failed" in out)
        check("a refused commit stages nothing and commits nothing", git(root, "diff", "--cached", "--name-only")[1] == "" and git(root, "rev-parse", "HEAD")[1] == head)
        check("the refusal output is bounded", len(out.splitlines()) <= MAX_LINES)

        # Passing gates see $GATE_PATHS; a new untracked file is committable; push to a fresh upstream branch.
        write(tmp, "gates2.txt", 'sees: echo "$GATE_PATHS" | grep -q "a.txt c.txt"\n')
        write(root, "c.txt", "c1\n")
        rc, out = call(["--root", root, "--paths", "a.txt,c.txt", "-m", "a and new c", "--gates", os.path.join(tmp, "gates2.txt"), "--push"])
        _, names = git(root, "show", "--name-only", "--format=", "HEAD")
        check("gates pass and see GATE_PATHS", rc == 0 and "1 passed, 0 failed" in out)
        check("an untracked path is added and committed", sorted(names.splitlines()) == ["a.txt", "c.txt"])
        check("push: ok is reported and the remote has the commit", "push: ok" in out and git(remote, "rev-parse", "HEAD")[1] == git(root, "rev-parse", "HEAD")[1])
        check("the output stays within 12 lines", len(out.splitlines()) <= MAX_LINES)
        _, upstream = git(root, "rev-parse", "--abbrev-ref", "@{u}")
        write(root, "a.txt", "a4\n")
        rc, out = call(["--root", root, "--paths", "a.txt", "-m", "second push", "--push"])
        check("a second push uses the upstream", rc == 0 and "push: ok" in out and upstream.endswith("/" + git(root, "rev-parse", "--abbrev-ref", "HEAD")[1]))

        # A failed push keeps the commit and exits 1.
        git(root, "remote", "set-url", "origin", os.path.join(tmp, "nowhere.git"))
        write(root, "a.txt", "a5\n")
        rc, out = call(["--root", root, "--paths", "a.txt", "-m", "push fails", "--push"])
        check("a failed push exits 1 but keeps the commit", rc == 1 and "push: FAILED" in out and git(root, "log", "-1", "--format=%s")[1] == "push fails")

        # Unusable input: everything, outside, glob, non-repo, missing gates file.
        for label, paths in (("the root", "."), ("a parent", ".."), ("an outside path", "../x"), ("a glob", "*.txt")):
            rc, out = call(["--root", root, "--paths", paths, "-m", "x"])
            check(f"{label} is refused as unusable", rc == 2)
        rc, _ = call(["--root", tmp, "--paths", "a.txt", "-m", "x"])
        check("a non-repo root is unusable input", rc == 2)
        rc, _ = call(["--root", root, "--paths", "a.txt", "-m", "x", "--gates", os.path.join(tmp, "none.txt")])
        check("a missing gates file is unusable input", rc == 2)

        # #262: a commit hook that rewrites a staged test literal is reported, and the call exits 3.
        hook = os.path.join(root, ".git", "hooks", "pre-commit")
        with open(hook, "w") as fh:
            fh.write("#!/bin/sh\nfor f in $(git diff --cached --name-only); do sed -i.bak 's/-oout/-out/' \"$f\" && rm -f \"$f.bak\" && git add \"$f\"; done\n")
        os.chmod(hook, 0o755)
        git(root, "config", "core.hooksPath", os.path.dirname(hook))   # this repo runs no hooks otherwise
        os.makedirs(os.path.join(root, "tests"), exist_ok=True)
        write(root, "tests/curl_test.sh", "curl -oout.txt http://x\n")
        rc, out = call(["--root", root, "--paths", "tests/curl_test.sh", "-m", "add test"])
        check("a hook's rewrite of a test literal is named, exit 3", rc == 3 and "HOOK-REWROTE-TEST: tests/curl_test.sh: `curl -oout.txt http://x` -> `curl -out.txt http://x`" in out)
        write(root, "notes.md", "see -oout here\n")
        rc, out = call(["--root", root, "--paths", "notes.md", "-m", "notes"])
        check("a rewrite outside tests is reported, not an error", rc == 0 and "hook rewrote: notes.md" in out)
        write(root, "tests/clean_test.sh", "echo ok\n")
        rc, out = call(["--root", root, "--paths", "tests/clean_test.sh", "-m", "clean"])
        check("no rewrite, no report", rc == 0 and "rewrote" not in out.lower())
        os.remove(hook)
        git(root, "config", "core.hooksPath", "/dev/null")

    for item in failures:
        print("SELFTEST FAIL  " + item)
    print(f"selftest: {len(failures)} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
