#!/usr/bin/env python3
"""stub_sync_pr.py — land a stub's development sync through a side-branch pull request, in one call.

The stub's branch rule refuses a direct push (GH013), so `/resolve-merge` Step 4 "The stub's own pull request"
pushes the committed merge of development to a side branch and lets GitHub merge it into the stub. This does that
whole step from the worktree that holds the merge commit:

  1. checks HEAD holds origin/<stub> and adds something to it (nothing to sync: exit 0);
  2. pushes HEAD to `sync/<stub-with-dashes>-<YYYY-MM-DD>` (never --force; a name already used by other work gets
     `-2`, `-3`, ...; a branch that already holds HEAD, or that HEAD only moves forward, is reused);
  3. opens the pull request into the stub (or reuses the open one of that branch);
  4. turns on auto-merge with a merge commit (`gh pr merge --auto --merge`, never squash or rebase);
  5. waits for its CI with wait_ci.py (unless --no-wait) and passes its exit code on.

Safe to run again: each step finds what an earlier run already did. The last line is always
`SYNC-PR: #<n> <url> auto-merge=<on|off> ci=<green|red|pending|skipped>`.

Usage:
    stub_sync_pr.py --root <worktree> --stub stub/2026-11-18 [--date YYYY-MM-DD] [--body-file F] [--no-wait]
    stub_sync_pr.py --selftest

Exit codes: 0 pushed, PR open with auto-merge and CI green (or nothing to sync, or --no-wait);
1 CI red or pending at the timeout, or auto-merge refused (the PR stays open for the owner); 2 bad arguments,
HEAD not on top of the stub, a pending merge, or a git/gh call that failed.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))


class Fail(Exception):
    pass


def run(cwd: str, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    p = subprocess.run(list(args), cwd=cwd, capture_output=True, text=True)
    if check and p.returncode != 0:
        raise Fail(f"{' '.join(args[:4])} failed: {(p.stderr or p.stdout).strip()[:400]}")
    return p


def git(cwd: str, *args: str, check: bool = True) -> str:
    return run(cwd, "git", *args, check=check).stdout.strip()


def is_ancestor(cwd: str, a: str, b: str) -> bool:
    return run(cwd, "git", "merge-base", "--is-ancestor", a, b, check=False).returncode == 0


def base_name(stub: str, date: str) -> str:
    return "sync/" + stub.replace("/", "-") + "-" + date


def pick_branch(cwd: str, stub: str, date: str, head: str) -> str:
    """The first sync branch name HEAD can be pushed to without force: absent on origin, already HEAD, or behind it."""
    base = base_name(stub, date)
    for i in range(1, 50):
        name = base if i == 1 else f"{base}-{i}"
        remote = git(cwd, "ls-remote", "--heads", "origin", f"refs/heads/{name}").split()
        if not remote:
            return name
        tip = remote[0]
        if tip == head:
            return name
        if run(cwd, "git", "cat-file", "-e", tip, check=False).returncode != 0:
            git(cwd, "fetch", "-q", "origin", f"refs/heads/{name}")
        if is_ancestor(cwd, tip, head):
            return name
    raise Fail(f"no free sync branch name after {base}-49")


def open_pr(cwd: str, branch: str, stub: str) -> dict | None:
    out = run(cwd, "gh", "pr", "list", "--head", branch, "--base", stub, "--state", "open",
              "--json", "number,url,autoMergeRequest").stdout
    prs = json.loads(out or "[]")
    return prs[0] if prs else None


def sync(a: argparse.Namespace) -> int:
    cwd = a.root
    if not os.path.isdir(cwd):
        raise Fail(f"no worktree at {cwd}")
    gitdir = git(cwd, "rev-parse", "--git-dir")
    if os.path.exists(os.path.join(cwd, gitdir, "MERGE_HEAD")) or os.path.exists(os.path.join(gitdir, "MERGE_HEAD")):
        raise Fail("a merge is still pending (MERGE_HEAD): commit it first (Step 4)")
    git(cwd, "fetch", "-q", "origin", f"refs/heads/{a.stub}:refs/remotes/origin/{a.stub}")
    head = git(cwd, "rev-parse", "HEAD")
    stub_tip = git(cwd, "rev-parse", f"origin/{a.stub}")
    if not is_ancestor(cwd, stub_tip, head):
        raise Fail(f"HEAD {head[:9]} does not hold origin/{a.stub} {stub_tip[:9]}: merge development into the stub's tip")
    if head == stub_tip:
        print(f"nothing to sync: HEAD is origin/{a.stub}")
        print("SYNC-PR: none")
        return 0
    branch = pick_branch(cwd, a.stub, a.date, head)
    git(cwd, "push", "-q", "origin", f"HEAD:refs/heads/{branch}")
    print(f"pushed {head[:9]} to {branch}")
    pr = open_pr(cwd, branch, a.stub)
    if pr is None:
        body = open(a.body_file, encoding="utf-8").read() if a.body_file else (
            f"Merges development into `{a.stub}` so the stub's pull request into development no longer conflicts. "
            "The stub's branch rule refuses a direct push, so the merge lands through this pull request with "
            "auto-merge (a merge commit, never squash or rebase).\n")
        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as fh:
            fh.write(body)
        try:
            run(cwd, "gh", "pr", "create", "--base", a.stub, "--head", branch,
                "--title", f"Sync {a.stub} with development", "--body-file", fh.name)
        finally:
            os.unlink(fh.name)
        for _ in range(5):
            pr = open_pr(cwd, branch, a.stub)
            if pr:
                break
            time.sleep(a.settle)
        if pr is None:
            raise Fail(f"opened a pull request from {branch} but cannot find it")
        print(f"opened #{pr['number']} {pr['url']}")
    else:
        print(f"reusing open #{pr['number']} {pr['url']}")
    auto = "on" if pr.get("autoMergeRequest") else ""
    if not auto:
        p = run(cwd, "gh", "pr", "merge", str(pr["number"]), "--auto", "--merge", check=False)
        if p.returncode == 0:
            auto = "on"
        else:
            auto = "off"
            print(f"auto-merge refused, the owner merges #{pr['number']}: {(p.stderr or p.stdout).strip()[:300]}")
    ci, code = "skipped", 0
    if not a.no_wait:
        w = subprocess.run([sys.executable, os.path.join(HERE, "wait_ci.py"), "--pr", str(pr["number"])], cwd=cwd)
        code = w.returncode
        ci = {0: "green", 1: "red"}.get(code, "pending")
        code = 0 if code == 0 else 1
    if auto == "off":
        code = 1
    print(f"SYNC-PR: #{pr['number']} {pr['url']} auto-merge={auto} ci={ci}")
    return code


def selftest() -> int:
    fails: list[str] = []

    def check(name: str, ok: bool) -> None:
        print(("PASS " if ok else "FAIL ") + name)
        if not ok:
            fails.append(name)

    check("branch name", base_name("stub/2026-11-18", "2026-11-28") == "sync/stub-2026-11-18-2026-11-28")
    with tempfile.TemporaryDirectory() as tmp:
        env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t",
                   GIT_COMMITTER_EMAIL="t@t", GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")
        os.environ.update({k: env[k] for k in ("GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_NAME",
                                               "GIT_COMMITTER_EMAIL", "GIT_CONFIG_GLOBAL", "GIT_CONFIG_NOSYSTEM")})
        bare, wt = os.path.join(tmp, "origin.git"), os.path.join(tmp, "wt")
        sh = lambda cwd, *c: subprocess.run(list(c), cwd=cwd, check=True, capture_output=True)
        sh(tmp, "git", "init", "-q", "--bare", "-b", "main", bare)
        sh(tmp, "git", "clone", "-q", bare, wt)
        sh(wt, "git", "commit", "-q", "--allow-empty", "-m", "base")
        sh(wt, "git", "push", "-q", "origin", "HEAD:refs/heads/stub/x")
        # a fake gh: records its calls, answers pr list from a state file
        bindir = os.path.join(tmp, "bin")
        os.makedirs(bindir)
        state, calls = os.path.join(tmp, "prs.json"), os.path.join(tmp, "calls.log")
        with open(state, "w") as fh:
            fh.write("[]")
        with open(os.path.join(bindir, "gh"), "w") as fh:
            fh.write(f"""#!{sys.executable}
import json, sys
a = sys.argv[1:]
open({calls!r}, "a").write(" ".join(a) + "\\n")
prs = json.load(open({state!r}))
if a[:2] == ["pr", "list"]:
    head = a[a.index("--head") + 1]
    print(json.dumps([p for p in prs if p["head"] == head]))
elif a[:2] == ["pr", "create"]:
    prs.append({{"number": 900 + len(prs), "url": "u", "head": a[a.index("--head") + 1], "autoMergeRequest": None}})
    json.dump(prs, open({state!r}, "w"))
elif a[:2] == ["pr", "merge"]:
    if "refuse" in open({calls!r}).read().split("\\n")[0]:
        print("auto-merge is not allowed", file=sys.stderr); sys.exit(1)
    for p in prs:
        if str(p["number"]) == a[2]:
            p["autoMergeRequest"] = {{"mergeMethod": "MERGE"}}
    json.dump(prs, open({state!r}, "w"))
""")
        os.chmod(os.path.join(bindir, "gh"), 0o755)
        os.environ["PATH"] = bindir + os.pathsep + os.environ["PATH"]
        ns = lambda: argparse.Namespace(root=wt, stub="stub/x", date="2026-11-28", body_file=None, no_wait=True, settle=0)

        check("nothing to sync when HEAD is the stub", sync(ns()) == 0 and not os.path.exists(calls))
        sh(wt, "git", "commit", "-q", "--allow-empty", "-m", "merge of development")
        check("first run: pushes, opens, auto-merges", sync(ns()) == 0)
        log = open(calls).read()
        check("gh pr create into the stub from the sync branch",
              "pr create --base stub/x --head sync/stub-x-2026-11-28" in log)
        check("auto-merge with a merge commit", "pr merge 900 --auto --merge" in log)
        check("the sync branch holds HEAD", subprocess.run(["git", "ls-remote", "origin", "refs/heads/sync/stub-x-2026-11-28"],
              cwd=wt, capture_output=True, text=True).stdout.split()[0] == git(wt, "rev-parse", "HEAD"))
        check("second run: no second PR, no second merge call",
              sync(ns()) == 0 and open(calls).read().count("pr create") == 1 and open(calls).read().count("pr merge") == 1)
        sh(wt, "git", "commit", "-q", "--allow-empty", "-m", "fix a red check")
        check("a fix on top fast-forwards the same branch and PR",
              sync(ns()) == 0 and open(calls).read().count("pr create") == 1
              and git(wt, "ls-remote", "origin", "refs/heads/sync/stub-x-2026-11-28").split()[0] == git(wt, "rev-parse", "HEAD"))
        # other work on the same name: a fresh name, never --force
        sh(wt, "git", "checkout", "-q", "-b", "other", "origin/stub/x")
        sh(wt, "git", "commit", "-q", "--allow-empty", "-m", "unrelated")
        check("a diverged branch of the same name gets -2", pick_branch(wt, "stub/x", "2026-11-28", git(wt, "rev-parse", "HEAD"))
              == "sync/stub-x-2026-11-28-2")
        sh(wt, "git", "checkout", "-q", "--detach", "origin/stub/x~0")
        sh(wt, "git", "commit", "-q", "--allow-empty", "-m", "c")
        sh(wt, "git", "push", "-q", "origin", "HEAD:refs/heads/stub/x")
        sh(wt, "git", "checkout", "-q", "other")
        try:
            sync(ns())
            check("HEAD not on top of the stub is refused", False)
        except Fail as e:
            check("HEAD not on top of the stub is refused", "does not hold" in str(e))
    print("stub_sync_pr selftest " + ("OK" if not fails else f"FAILED ({len(fails)})"))
    return 0 if not fails else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    ap.add_argument("--root", help="the worktree that holds the committed merge of development")
    ap.add_argument("--stub", help="the stub branch, e.g. stub/2026-11-18")
    ap.add_argument("--date", default=time.strftime("%Y-%m-%d"), help="the date in the sync branch name (default today)")
    ap.add_argument("--body-file", help="the pull request body: conflicted files, how each was resolved, test results")
    ap.add_argument("--no-wait", action="store_true", help="do not wait for CI")
    ap.add_argument("--settle", type=float, default=3, help=argparse.SUPPRESS)
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if not a.root or not a.stub or not a.stub.startswith("stub/"):
        print("stub_sync_pr.py: --root and --stub stub/<date> are required", file=sys.stderr)
        return 2
    try:
        return sync(a)
    except Fail as e:
        print(f"stub_sync_pr.py: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
