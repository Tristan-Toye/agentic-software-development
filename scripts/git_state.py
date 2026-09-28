#!/usr/bin/env python3
"""Print one bounded snapshot of a worktree's git state, in one call.

An orchestrator on an expensive model re-bills its whole context on every
step. Ten recorded runs made 490 hand-typed state checks — `cd <worktree> &&
git status && git diff --stat && git log -3 && sed -n 40,80p file` and its
cousins — each one a separate step, each one printing whatever git felt like
printing that day: unbounded diffs, hundreds of untracked paths, no upstream
or in-progress-operation context unless a second and third command were typed
too. This script asks git the same handful of questions in one process and
prints one bounded report: current position, how it relates to its upstream
and (optionally) a base branch, a status a human can read in five lines
instead of five hundred, a diff summary rather than a diff, recent history,
and whether a merge/rebase/cherry-pick/bisect is mid-flight. Nothing here
prints a file's contents unless `--diff` is passed, and even then the diff is
capped.

# Usage

    git_state.py WORKTREE [--base BRANCH] [--paths N=20] [--commits N=5]
                 [--diff] [--worktrees]
    git_state.py --selftest

WORKTREE is any directory inside a git work tree (a linked worktree is fine;
its `.git` is a file, and git resolves it correctly for everything here,
including the in-progress-operation check, which reads worktree-specific
state under the real git-dir, not the shared one).

`--base BRANCH` adds ahead/behind and the merge-base sha against BRANCH,
alongside (not instead of) the upstream ahead/behind, which is always shown
when an upstream exists. `--paths N` bounds how many status paths and diff
stat lines are printed (each list past N ends in `(+N more)`); `--commits N`
bounds the log. `--worktrees` adds a capped `git worktree list`. `--diff`
adds a capped, cut unified diff against HEAD (both staged and unstaged
changes) — the one thing here that shows file content, and only on request.

Exit codes: 0 done; 2 when WORKTREE is not a git work tree.

No third-party imports: this runs wherever python3 and git do.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile

GIT = "/usr/bin/git"
CONFLICT_CODES = {"DD", "AU", "UD", "UA", "DU", "AA", "UU"}
DIFF_LINE_CAP = 200
DIFF_CHAR_CAP = 300


def git(worktree: str, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [GIT, "-C", worktree, *args], capture_output=True, text=True, check=False
    )


def branch_and_head(worktree: str) -> tuple[str | None, bool, str, str]:
    sym = git(worktree, "symbolic-ref", "-q", "--short", "HEAD")
    detached = sym.returncode != 0
    branch = None if detached else sym.stdout.strip()
    sha = git(worktree, "rev-parse", "--short", "HEAD").stdout.strip()
    subject = git(worktree, "log", "-1", "--format=%s").stdout.strip()
    return branch, detached, sha, subject


def upstream_status(worktree: str) -> tuple[str | None, int | None, int | None]:
    up = git(worktree, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
    if up.returncode != 0:
        return None, None, None
    name = up.stdout.strip()
    counts = git(worktree, "rev-list", "--left-right", "--count", "@{u}...HEAD")
    if counts.returncode != 0 or not counts.stdout.split():
        return name, None, None
    behind, ahead = counts.stdout.split()
    return name, int(ahead), int(behind)


def base_status(worktree: str, base: str) -> tuple[str | None, int | None, int | None]:
    mb = git(worktree, "merge-base", base, "HEAD")
    if mb.returncode != 0:
        return None, None, None
    merge_base = mb.stdout.strip()
    counts = git(worktree, "rev-list", "--left-right", "--count", f"{base}...HEAD")
    if counts.returncode != 0 or not counts.stdout.split():
        return merge_base, None, None
    behind, ahead = counts.stdout.split()
    return merge_base, int(ahead), int(behind)


def status_summary(worktree: str, cap: int) -> tuple[dict[str, int], list[tuple[str, str]]]:
    out = git(worktree, "status", "--porcelain=v1", "--untracked-files=all")
    rows: list[tuple[str, str]] = []
    staged = modified = untracked = conflicted = 0
    for line in (l for l in out.stdout.splitlines() if l):
        code, path = line[:2], line[3:]
        if code in CONFLICT_CODES:
            conflicted += 1
        elif code == "??":
            untracked += 1
        else:
            if code[0] not in (" ", "?"):
                staged += 1
            if code[1] not in (" ", "?"):
                modified += 1
        rows.append((code, path))
    counts = {"staged": staged, "modified": modified, "untracked": untracked,
              "conflicted": conflicted, "total": len(rows)}
    return counts, rows[:cap]


def diff_stat(worktree: str, cap: int) -> tuple[str, list[str], str, list[str]]:
    def stat_of(extra: list[str]) -> tuple[str, list[str]]:
        out = git(worktree, "diff", *extra, "--stat")
        lines = [l for l in out.stdout.splitlines() if l.strip()]
        if not lines:
            return "0 files changed", []
        return lines[-1].strip(), [l.strip() for l in lines[:-1]][:cap]

    staged_summary, staged_files = stat_of(["--cached"])
    unstaged_summary, unstaged_files = stat_of([])
    return staged_summary, staged_files, unstaged_summary, unstaged_files


def recent_commits(worktree: str, n: int) -> list[str]:
    out = git(worktree, "log", f"-{n}", "--format=%h %s")
    return out.stdout.splitlines()


def git_dir_for(worktree: str) -> str | None:
    """The worktree's own git-dir (not the shared common dir), correctly
    resolved even when this worktree's `.git` is a file pointing elsewhere."""
    out = git(worktree, "rev-parse", "--git-dir")
    if out.returncode != 0:
        return None
    gd = out.stdout.strip()
    if not os.path.isabs(gd):
        gd = os.path.join(worktree, gd)
    return os.path.normpath(gd)


def in_progress_op(worktree: str) -> str | None:
    gd = git_dir_for(worktree)
    if not gd:
        return None
    checks = [
        ("merge", os.path.join(gd, "MERGE_HEAD")),
        ("rebase", os.path.join(gd, "rebase-merge")),
        ("rebase", os.path.join(gd, "rebase-apply")),
        ("cherry-pick", os.path.join(gd, "CHERRY_PICK_HEAD")),
        ("bisect", os.path.join(gd, "BISECT_LOG")),
    ]
    for name, path in checks:
        if os.path.exists(path):
            return name
    return None


def worktree_list(worktree: str, cap: int) -> tuple[list[str], int]:
    out = git(worktree, "worktree", "list")
    lines = out.stdout.splitlines()
    shown = lines[:cap]
    return shown, len(lines) - len(shown)


def bounded_diff(worktree: str, cap_lines: int, cap_chars: int) -> tuple[list[str], int]:
    out = git(worktree, "diff", "HEAD")
    lines = out.stdout.splitlines()
    shown = [l[:cap_chars] for l in lines[:cap_lines]]
    return shown, len(lines) - len(shown)


def run(worktree: str, base: str | None, paths_cap: int, commits_cap: int,
        show_diff: bool, show_worktrees: bool) -> int:
    check = git(worktree, "rev-parse", "--is-inside-work-tree")
    if check.returncode != 0 or check.stdout.strip() != "true":
        print(f"git_state: {worktree} is not a git work tree", file=sys.stderr)
        return 2

    branch, detached, sha, subject = branch_and_head(worktree)
    print(f"branch: {'detached HEAD' if detached else branch}")
    print(f"HEAD: {sha}  {subject}")

    up_name, up_ahead, up_behind = upstream_status(worktree)
    if up_name:
        print(f"upstream: {up_name}  ahead {up_ahead}, behind {up_behind}")
    else:
        print("upstream: none")

    if base:
        merge_base, b_ahead, b_behind = base_status(worktree, base)
        if merge_base:
            print(f"base {base}: ahead {b_ahead}, behind {b_behind}  merge-base {merge_base[:12]}")
        else:
            print(f"base {base}: not found")

    print(f"in progress: {in_progress_op(worktree) or 'none'}")

    counts, rows = status_summary(worktree, paths_cap)
    print(f"status: {counts['staged']} staged, {counts['modified']} modified, "
          f"{counts['untracked']} untracked, {counts['conflicted']} conflicted "
          f"({counts['total']} path(s) total)")
    for code, path in rows:
        print(f"  {code}  {path}")
    if counts["total"] > len(rows):
        print(f"  (+{counts['total'] - len(rows)} more)")

    staged_summary, staged_files, unstaged_summary, unstaged_files = diff_stat(worktree, paths_cap)
    print(f"diff --cached --stat: {staged_summary}")
    for f in staged_files:
        print(f"  {f}")
    print(f"diff --stat: {unstaged_summary}")
    for f in unstaged_files:
        print(f"  {f}")

    commits = recent_commits(worktree, commits_cap)
    print(f"commits (last {len(commits)}):")
    for c in commits:
        print(f"  {c}")

    if show_worktrees:
        lines, more = worktree_list(worktree, paths_cap)
        print("worktrees:")
        for l in lines:
            print(f"  {l}")
        if more:
            print(f"  (+{more} more)")

    if show_diff:
        lines, more = bounded_diff(worktree, DIFF_LINE_CAP, DIFF_CHAR_CAP)
        print("diff HEAD (bounded):")
        for l in lines:
            print(f"  {l}")
        if more:
            print(f"  (+{more} more line(s))")

    return 0


def _git(cwd: str, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run([GIT, "-C", cwd, *args], capture_output=True, text=True, check=check)


def selftest() -> int:
    failures: list[str] = []

    def check(name: str, ok: bool) -> None:
        if not ok:
            failures.append(name)

    with tempfile.TemporaryDirectory() as tmp:
        repo = os.path.join(tmp, "repo")
        os.makedirs(repo)
        _git(repo, "init", "-q", "-b", "main")
        _git(repo, "config", "user.email", "t@example.com")
        _git(repo, "config", "user.name", "t")
        with open(os.path.join(repo, "a.txt"), "w") as fh:
            fh.write("one\n")
        _git(repo, "add", "a.txt")
        _git(repo, "commit", "-q", "-m", "init")

        _git(repo, "branch", "feature")
        _git(repo, "checkout", "-q", "-b", "topic")
        with open(os.path.join(repo, "b.txt"), "w") as fh:
            fh.write("two\n")
        _git(repo, "add", "b.txt")
        _git(repo, "commit", "-q", "-m", "topic work")

        _git(repo, "checkout", "-q", "main")
        with open(os.path.join(repo, "c.txt"), "w") as fh:
            fh.write("three\n")
        _git(repo, "add", "c.txt")
        _git(repo, "commit", "-q", "-m", "main advance")
        _git(repo, "checkout", "-q", "topic")

        wt = os.path.join(tmp, "wt")
        _git(repo, "worktree", "add", "-q", wt, "feature")
        check("linked worktree .git is a file", os.path.isfile(os.path.join(wt, ".git")))

        with open(os.path.join(wt, "a.txt"), "w") as fh:
            fh.write("one changed\n")  # unstaged modification
        with open(os.path.join(wt, "staged.txt"), "w") as fh:
            fh.write("staged\n")
        _git(wt, "add", "staged.txt")
        for i in range(30):
            with open(os.path.join(wt, f"u{i:03d}.txt"), "w") as fh:
                fh.write("x\n")

        out = subprocess.run(
            [sys.executable, os.path.abspath(__file__), wt, "--base", "main", "--paths", "5"],
            capture_output=True, text=True,
        )
        text = out.stdout
        check("run exits 0", out.returncode == 0)
        check("shows branch feature", "branch: feature" in text)
        check("shows in progress none for a clean worktree", "in progress: none" in text)
        check("shows base ahead/behind", "base main:" in text)
        check("status counts untracked at least 31", "untracked" in text)
        check("status caps paths and shows more", "more)" in text)
        check("staged file counted", "staged" in text and "1 staged" in text)
        check("diff --cached --stat shown", "diff --cached --stat:" in text)

        out_all = subprocess.run(
            [sys.executable, os.path.abspath(__file__), wt, "--paths", "1000"],
            capture_output=True, text=True,
        )
        check("uncapped run lists no '(+N more)' for status", "more)" not in out_all.stdout.split("diff --cached", 1)[0])

        out_wt = subprocess.run(
            [sys.executable, os.path.abspath(__file__), repo, "--worktrees"],
            capture_output=True, text=True,
        )
        check("worktree list shows both entries", "wt" in out_wt.stdout and "worktrees:" in out_wt.stdout)

        out_diff = subprocess.run(
            [sys.executable, os.path.abspath(__file__), wt, "--diff"],
            capture_output=True, text=True,
        )
        check("--diff shows a bounded diff section", "diff HEAD (bounded):" in out_diff.stdout)

        # in-progress merge conflict, in its own repo
        repo2 = os.path.join(tmp, "repo2")
        os.makedirs(repo2)
        _git(repo2, "init", "-q", "-b", "main")
        _git(repo2, "config", "user.email", "t@example.com")
        _git(repo2, "config", "user.name", "t")
        with open(os.path.join(repo2, "f.txt"), "w") as fh:
            fh.write("base\n")
        _git(repo2, "add", "f.txt")
        _git(repo2, "commit", "-q", "-m", "base")
        _git(repo2, "checkout", "-q", "-b", "side")
        with open(os.path.join(repo2, "f.txt"), "w") as fh:
            fh.write("side change\n")
        _git(repo2, "add", "f.txt")
        _git(repo2, "commit", "-q", "-m", "side change")
        _git(repo2, "checkout", "-q", "main")
        with open(os.path.join(repo2, "f.txt"), "w") as fh:
            fh.write("main change\n")
        _git(repo2, "add", "f.txt")
        _git(repo2, "commit", "-q", "-m", "main change")
        _git(repo2, "merge", "side", check=False)  # conflict expected

        out2 = subprocess.run(
            [sys.executable, os.path.abspath(__file__), repo2],
            capture_output=True, text=True,
        )
        check("merge-conflict run exits 0", out2.returncode == 0)
        check("in-progress merge detected", "in progress: merge" in out2.stdout)
        check("conflicted count is 1", "1 conflicted" in out2.stdout)

        # not a git work tree
        plain = os.path.join(tmp, "plain")
        os.makedirs(plain)
        out3 = subprocess.run([sys.executable, os.path.abspath(__file__), plain], capture_output=True, text=True)
        check("non-worktree exits 2", out3.returncode == 2)

    for item in failures:
        print("SELFTEST FAIL  " + item)
    print(f"selftest: {len(failures)} failure(s)")
    return 1 if failures else 0


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("worktree", nargs="?", help="any directory inside a git work tree")
    ap.add_argument("--base", metavar="BRANCH", help="also show ahead/behind and merge-base against BRANCH")
    ap.add_argument("--paths", type=int, default=20, metavar="N", help="cap on status paths and diff-stat lines shown (default 20)")
    ap.add_argument("--commits", type=int, default=5, metavar="N", help="how many recent commits to show (default 5)")
    ap.add_argument("--diff", action="store_true", help="also show a bounded unified diff against HEAD")
    ap.add_argument("--worktrees", action="store_true", help="also show a bounded `git worktree list`")
    ap.add_argument("--selftest", action="store_true", help="check the checker")
    args = ap.parse_args(argv)
    if args.selftest:
        return selftest()
    if not args.worktree:
        ap.error("WORKTREE is required unless --selftest")
    return run(os.path.abspath(args.worktree), args.base, args.paths, args.commits, args.diff, args.worktrees)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
