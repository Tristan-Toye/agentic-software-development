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
    gate_commit.py --selftest

`--paths` is a comma list of files or directories, relative to `--root` (or
absolute inside it). `.`, the root itself and a path outside the root are
refused: there is no way to say "everything". Every named path must have a
change (modified, deleted, or untracked) or the call is refused before any
gate runs.

`--gates FILE` runs `run_gates.py FILE --root X` first (the gates file format is
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

    for item in failures:
        print("SELFTEST FAIL  " + item)
    print(f"selftest: {len(failures)} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
