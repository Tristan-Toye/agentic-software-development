#!/usr/bin/env python3
"""Remove a run's own worktree, and only that: the one call auto mode may always allow.

Runs leave worktrees beside the main checkout (`<repo>-<ID>`, `<repo>-<ID>-bisect`,
`<repo>-plan-<KEY>`, `<repo>-exp-…`). Removing one with `git worktree remove
--force` is a destructive command the auto-mode safety check rightly refuses
when it cannot see whose tree it is, and every refusal stops a run on the
owner. This script makes the call safe enough to allow outright:

- the path must be a worktree registered in `--root`'s repository, never the
  main checkout, and a sibling of it whose name starts with `<repo>-`;
- uncommitted agent work has no second copy (`safe_revert.py`): a tree with
  tracked changes or untracked files is refused unless `--copy-to DIR`
  (outside the tree) is given, which first saves the diff and every untracked
  file there and prints where. Ignored files (build output) are dropped;
- `--branch` also deletes the worktree's branch, only when it is merged into
  HEAD of `--root` or already on its remote (`git branch -d`, never `-D`).

# Usage

    remove_worktree.py --root /abs/main/checkout PATH [PATH ...] [--copy-to DIR] [--branch]
    remove_worktree.py --selftest

Exit 0: every PATH removed (a PATH that is already gone counts, after a
prune). Exit 2: a PATH refused, with the reason; nothing of it was touched.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile


def git(cwd: str, *a: str, check: bool = True) -> str:
    r = subprocess.run(["git", "-C", cwd, *a], capture_output=True, text=True)
    if check and r.returncode != 0:
        raise RuntimeError(f"git {' '.join(a)}: {(r.stderr or r.stdout).strip()}")
    return r.stdout


def worktrees(root: str) -> list[dict]:
    out, cur = [], {}
    for line in git(root, "worktree", "list", "--porcelain").splitlines():
        if not line:
            if cur:
                out.append(cur)
            cur = {}
            continue
        k, _, v = line.partition(" ")
        cur[k] = v or True
    if cur:
        out.append(cur)
    return out


def refusal(root: str, path: str) -> str | None:
    """Why PATH may not be removed under ROOT, or None."""
    root, path = os.path.realpath(root), os.path.realpath(path)
    trees = worktrees(root)
    if not trees:
        return "no worktrees listed"
    main = os.path.realpath(trees[0]["worktree"])
    if path == main or path == root:
        return "that is the main checkout"
    if os.path.dirname(path) != os.path.dirname(main):
        return f"not beside the main checkout ({os.path.dirname(main)})"
    if not os.path.basename(path).startswith(os.path.basename(main) + "-"):
        return f"its name does not start with {os.path.basename(main)}-"
    if not any(os.path.realpath(t["worktree"]) == path for t in trees):
        return "not a worktree of this repository"
    return None


def dirty(path: str) -> tuple[str, list[str]]:
    diff = git(path, "diff", "HEAD", "--binary", check=False)
    untracked = [f for f in git(path, "ls-files", "--others", "--exclude-standard", "-z").split("\0") if f]
    return diff, untracked


def save(path: str, dest: str, diff: str, untracked: list[str]) -> str:
    out = os.path.join(os.path.realpath(dest), os.path.basename(path))
    os.makedirs(out, exist_ok=True)
    with open(os.path.join(out, "changes.diff"), "w") as fh:
        fh.write(diff)
    for f in untracked:
        tgt = os.path.join(out, "untracked", f)
        os.makedirs(os.path.dirname(tgt), exist_ok=True)
        if os.path.isfile(os.path.join(path, f)):
            shutil.copy2(os.path.join(path, f), tgt)
    return out


def remove(root: str, path: str, copy_to: str | None, branch: bool) -> tuple[bool, str]:
    real = os.path.realpath(path)
    if not os.path.exists(real):
        git(root, "worktree", "prune")
        return True, f"gone already: {path} (pruned)"
    why = refusal(root, real)
    if why:
        return False, f"REFUSED {path}: {why}"
    diff, untracked = dirty(real)
    note = ""
    if diff.strip() or untracked:
        if not copy_to:
            return False, (f"REFUSED {path}: uncommitted work ({len(untracked)} untracked file(s), "
                           f"{'a' if diff.strip() else 'no'} tracked diff); pass --copy-to DIR to save it first")
        if os.path.realpath(copy_to).startswith(real + os.sep) or os.path.realpath(copy_to) == real:
            return False, f"REFUSED {path}: --copy-to lies inside the tree being removed"
        note = f"; uncommitted work saved to {save(real, copy_to, diff, untracked)}"
    head = next((t for t in worktrees(root) if os.path.realpath(t["worktree"]) == real), {})
    br = str(head.get("branch", "")).removeprefix("refs/heads/")
    git(root, "worktree", "remove", "--force", real)
    git(root, "worktree", "prune")
    if branch and br:
        r = subprocess.run(["git", "-C", root, "branch", "-d", br], capture_output=True, text=True)
        note += f"; branch {br} deleted" if r.returncode == 0 else f"; branch {br} kept (not merged: {r.stderr.strip()[:120]})"
    return True, f"removed {path}{note}"


def selftest() -> int:
    fails = 0

    def check(name: str, ok: bool) -> None:
        nonlocal fails
        fails += not ok
        print(("ok   " if ok else "FAIL ") + name)

    with tempfile.TemporaryDirectory() as t:
        t = os.path.realpath(t)
        root = os.path.join(t, "repo")
        os.makedirs(root)
        for a in (["init", "-q", "-b", "main"], ["config", "user.email", "t@t"], ["config", "user.name", "t"]):
            git(root, *a)
        open(os.path.join(root, "a.txt"), "w").write("a\n")
        git(root, "add", "."); git(root, "commit", "-qm", "init")
        wt = os.path.join(t, "repo-W-1")
        git(root, "worktree", "add", "-q", "-b", "w1", wt)
        other = os.path.join(t, "elsewhere-W-1")
        git(root, "worktree", "add", "-q", "-b", "w2", other)
        ok, msg = remove(root, root, None, False)
        check("refuses the main checkout", not ok and "main checkout" in msg)
        ok, msg = remove(root, other, None, False)
        check("refuses a worktree not named <repo>-", not ok and os.path.isdir(other))
        ok, msg = remove(root, os.path.join(t, "repo-unrelated"), None, False)
        check("a path already gone is pruned, not an error", ok and "gone already" in msg)
        os.makedirs(os.path.join(t, "repo-plain"))
        ok, msg = remove(root, os.path.join(t, "repo-plain"), None, False)
        check("refuses a sibling directory that is not a worktree", not ok and "not a worktree" in msg)
        open(os.path.join(wt, "new.txt"), "w").write("agent work\n")
        open(os.path.join(wt, "a.txt"), "w").write("changed\n")
        ok, msg = remove(root, wt, None, False)
        check("refuses uncommitted work without --copy-to", not ok and os.path.isdir(wt))
        ok, msg = remove(root, wt, os.path.join(wt, "save"), False)
        check("refuses a --copy-to inside the tree", not ok and os.path.isdir(wt))
        save_dir = os.path.join(t, "saved")
        ok, msg = remove(root, wt, save_dir, True)
        saved = os.path.join(save_dir, "repo-W-1")
        check("with --copy-to it saves the diff and untracked files, then removes",
              ok and not os.path.exists(wt) and os.path.isfile(os.path.join(saved, "untracked", "new.txt"))
              and "changed" in open(os.path.join(saved, "changes.diff")).read())
        check("--branch deletes a merged branch only", "branch w1 deleted" in msg)
        wt3 = os.path.join(t, "repo-W-3")
        git(root, "worktree", "add", "-q", "-b", "w3", wt3)
        open(os.path.join(wt3, "b.txt"), "w").write("b\n")
        git(wt3, "add", "."); git(wt3, "commit", "-qm", "w3 work")
        ok, msg = remove(root, wt3, None, True)
        check("a clean tree is removed; an unmerged branch is kept", ok and not os.path.exists(wt3) and "kept" in msg)
    print("all ok" if not fails else f"{fails} failed")
    return 1 if fails else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    ap.add_argument("paths", nargs="*")
    ap.add_argument("--root", help="the repository's main checkout")
    ap.add_argument("--copy-to", help="save uncommitted work here (outside the tree) before removing")
    ap.add_argument("--branch", action="store_true", help="also delete the worktree's branch when merged")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if not a.root or not a.paths:
        ap.error("--root and at least one PATH are required")
    bad = False
    for p in a.paths:
        try:
            ok, msg = remove(a.root, p, a.copy_to, a.branch)
        except RuntimeError as e:
            ok, msg = False, f"REFUSED {p}: {e}"
        print(msg)
        bad |= not ok
    return 2 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
