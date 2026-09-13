#!/usr/bin/env python3
"""Revert or delete paths an agent may hold uncommitted, with a copy taken first.

Uncommitted agent work has no second copy. Two recorded runs destroyed a whole
fix round with one `git checkout -- <path>`, and one of them broke the rule in
the same run that ratified it. A rule read is not a rule performed, so this
script is the mechanism: it refuses to touch a path that carries uncommitted
changes unless it has first copied that path, byte for byte, to a directory
OUTSIDE the repository — and it prints where.

A copy outside the repository is chosen over a commit on purpose. A commit
while blind agents run puts the file in the shared object store, reachable
from every worktree, and spends the blindness invariant for nothing a copy
does not give.

# Usage

    safe_revert.py --repo /abs/worktree --copy-to /abs/outside/dir PATH ...
    safe_revert.py --repo /abs/worktree --copy-to /abs/outside/dir --delete PATH ...
    safe_revert.py --selftest

Default mode reverts each PATH to HEAD (`git checkout HEAD -- PATH`) after
copying. `--delete` removes each PATH from the working tree after copying —
the delete-then-re-spawn case. An untracked PATH is copied and, in default
mode, left alone (there is no HEAD to revert to); with `--delete` it is
removed after the copy.

`--copy-to` must lie outside `--repo`. A copy inside the repository is not a
second copy: the next revert or clean takes it too.

Exit codes: 0 done; 1 refused (nothing touched); 2 unusable input.

No third-party imports: this runs wherever python3 and git do.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import time


def git(repo: str, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", repo, *args], capture_output=True, text=True, check=False
    )


def inside(child: str, parent: str) -> bool:
    child = os.path.realpath(child)
    parent = os.path.realpath(parent)
    return child == parent or child.startswith(parent + os.sep)


def status_of(repo: str, rel: str) -> str:
    """Return the two-character porcelain status of `rel`, or '' when clean."""
    out = git(repo, "status", "--porcelain", "--untracked-files=all", "--", rel)
    for line in out.stdout.splitlines():
        if line[3:] == rel or line[3:].endswith("/" + rel) or line[3:].startswith(rel + "/"):
            return line[:2]
    return ""


def copy_out(repo: str, rel: str, dest_root: str) -> str:
    src = os.path.join(repo, rel)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    dest = os.path.join(dest_root, stamp, rel)
    # Two copies of one path in one second must both survive: a copy that
    # overwrites the previous copy is not a second copy either.
    suffix = 0
    while os.path.exists(dest):
        suffix += 1
        dest = os.path.join(dest_root, f"{stamp}-{suffix}", rel)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    if os.path.isdir(src):
        shutil.copytree(src, dest)
    else:
        shutil.copy2(src, dest)
    return dest


def run(repo: str, copy_to: str, paths: list[str], delete: bool) -> int:
    if git(repo, "rev-parse", "--is-inside-work-tree").returncode != 0:
        print(f"safe_revert: {repo} is not a git worktree", file=sys.stderr)
        return 2
    if inside(copy_to, repo):
        print(
            f"safe_revert: REFUSED — --copy-to {copy_to} lies inside the repository. "
            "A copy the next revert or clean can take is not a second copy.",
            file=sys.stderr,
        )
        return 1
    os.makedirs(copy_to, exist_ok=True)

    rels: list[str] = []
    for p in paths:
        abs_p = p if os.path.isabs(p) else os.path.join(repo, p)
        if not inside(abs_p, repo):
            print(f"safe_revert: {p} is outside {repo}", file=sys.stderr)
            return 2
        rels.append(os.path.relpath(os.path.realpath(abs_p), os.path.realpath(repo)))

    # Plan every action before touching anything, so a refusal touches nothing.
    plan: list[tuple[str, str, str]] = []  # (rel, status, action)
    for rel in rels:
        exists = os.path.exists(os.path.join(repo, rel))
        st = status_of(repo, rel) if exists or True else ""
        if not exists and not st:
            print(f"safe_revert: {rel} does not exist and has no status; nothing to do")
            continue
        if st.strip() == "":
            action = "delete" if delete else "already clean, no revert needed"
        elif st.startswith("??"):
            action = "delete" if delete else "untracked, left in place"
        else:
            action = "delete" if delete else "revert"
        plan.append((rel, st or "  ", action))

    copies: list[str] = []
    for rel, st, action in plan:
        if st.strip() and os.path.exists(os.path.join(repo, rel)):
            copies.append(copy_out(repo, rel, copy_to))
            print(f"copied   {rel}  ({st.strip()})  ->  {copies[-1]}")
        elif action == "delete" and os.path.exists(os.path.join(repo, rel)):
            copies.append(copy_out(repo, rel, copy_to))
            print(f"copied   {rel}  (clean)  ->  {copies[-1]}")

    for rel, st, action in plan:
        target = os.path.join(repo, rel)
        if action == "revert":
            r = git(repo, "checkout", "HEAD", "--", rel)
            if r.returncode != 0:
                print(f"safe_revert: git checkout failed for {rel}: {r.stderr.strip()}", file=sys.stderr)
                return 2
            print(f"reverted {rel}")
        elif action == "delete":
            if os.path.isdir(target):
                shutil.rmtree(target)
            elif os.path.exists(target):
                os.remove(target)
            print(f"deleted  {rel}")
        else:
            print(f"skipped  {rel}  ({action})")

    print(f"safe_revert: {len(copies)} cop{'y' if len(copies) == 1 else 'ies'} under {copy_to}; log the path in ## Build log")
    return 0


def selftest() -> int:
    failures: list[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        repo = os.path.join(tmp, "repo")
        outside = os.path.join(tmp, "copies")
        os.makedirs(repo)
        subprocess.run(["git", "init", "-q", repo], check=True)
        subprocess.run(["git", "-C", repo, "config", "user.email", "t@example.com"], check=True)
        subprocess.run(["git", "-C", repo, "config", "user.name", "t"], check=True)
        with open(os.path.join(repo, "a.txt"), "w") as fh:
            fh.write("committed\n")
        subprocess.run(["git", "-C", repo, "add", "a.txt"], check=True)
        subprocess.run(["git", "-C", repo, "commit", "-q", "-m", "init"], check=True)

        # 1. modified tracked file: copied, then reverted
        with open(os.path.join(repo, "a.txt"), "w") as fh:
            fh.write("agent work\n")
        rc = run(repo, outside, ["a.txt"], delete=False)
        content = open(os.path.join(repo, "a.txt")).read()
        copied = [os.path.join(dp, f) for dp, _, fs in os.walk(outside) for f in fs]
        if rc != 0 or content != "committed\n" or not copied or open(copied[0]).read() != "agent work\n":
            failures.append(f"revert case: rc={rc} content={content!r} copies={copied}")

        # 2. copy-to inside the repo is refused and nothing is touched
        with open(os.path.join(repo, "a.txt"), "w") as fh:
            fh.write("agent work 2\n")
        rc = run(repo, os.path.join(repo, "copies"), ["a.txt"], delete=False)
        if rc != 1 or open(os.path.join(repo, "a.txt")).read() != "agent work 2\n":
            failures.append("inside-repo copy-to was not refused or the file was touched")

        # 3. untracked file: default mode copies and leaves it; --delete removes it
        with open(os.path.join(repo, "new.txt"), "w") as fh:
            fh.write("blind author output\n")
        rc = run(repo, outside, ["new.txt"], delete=False)
        if rc != 0 or not os.path.exists(os.path.join(repo, "new.txt")):
            failures.append("untracked default mode should leave the file in place")
        rc = run(repo, outside, ["new.txt"], delete=True)
        if rc != 0 or os.path.exists(os.path.join(repo, "new.txt")):
            failures.append("untracked --delete should remove the file")
        copies_new = [f for dp, _, fs in os.walk(outside) for f in fs if f == "new.txt"]
        if len(copies_new) < 2:
            failures.append(f"expected two copies of new.txt, found {len(copies_new)}")

        # 4. clean tracked file with --delete: still copied before the delete
        rc = run(repo, outside, ["a.txt"], delete=True)
        if rc != 0 or os.path.exists(os.path.join(repo, "a.txt")):
            failures.append("clean --delete should remove the file")

    for item in failures:
        print("SELFTEST FAIL  " + item)
    print(f"selftest: 4 cases, {len(failures)} failure(s)")
    return 1 if failures else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("paths", nargs="*", help="paths inside --repo, relative or absolute")
    ap.add_argument("--repo", help="the worktree the paths live in")
    ap.add_argument("--copy-to", help="a directory OUTSIDE the repository to copy into")
    ap.add_argument("--delete", action="store_true", help="remove the paths after copying instead of reverting")
    ap.add_argument("--selftest", action="store_true", help="check the checker")
    args = ap.parse_args()
    if args.selftest:
        return selftest()
    if not (args.repo and args.copy_to and args.paths):
        ap.error("--repo, --copy-to and at least one PATH are required unless --selftest")
    return run(os.path.abspath(args.repo), os.path.abspath(args.copy_to), args.paths, args.delete)


if __name__ == "__main__":
    sys.exit(main())
