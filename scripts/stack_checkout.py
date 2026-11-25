#!/usr/bin/env python3
"""The stack checkout: the persistent checkout a /stub run builds and tests the stub head from.

The main checkout of a repo that commits `.discovery/` is read-only (the main-checkout lock): it stays on
development, because the dashboard and auto work read its dossiers. A full stack run still needs a checkout that
outlives the run and is not a secondary worktree: the shared VM stack bind-mounts files from the checkout that
brought it up, a repo's `up.sh` may refuse a worktree (config-harness does), and the receipt is bound to that
checkout's head. That checkout is `<parent>/<main name>-stack`: a local clone of the main checkout (objects
hard-linked, `origin` pointing at the main checkout's own remote), always detached, so no branch cleanup ever
matches it and no worktree listing names it. It is its own main checkout, so `stack_env.py` brings the stack up and
rebuilds the fixture from it for a run in it. The dashboard leaves it out of its dossier roots.

`sync` makes it exist and point at REF (fetched from origin):

- absent: `git clone --local --no-checkout <main> <stack>`, `origin` set to the main checkout's origin URL;
- present: it must be a clone (not a worktree) with that same origin, else exit 2, untouched; tracked changes or
  untracked files refuse it too (nothing is ever discarded: exit 2 names them); else `git checkout --detach REF`.

Ignored files (a `.runtime.env` written by `up.sh`, build output) stay between runs.

# Usage

    stack_checkout.py sync --root /abs/main/checkout --ref origin/stub/2026-11-18 [--no-fetch]
    stack_checkout.py path --root /abs/main/checkout
    stack_checkout.py --selftest

`sync` prints `STACK-CHECKOUT: <path> <full sha>` and exits 0; exit 2 for a refusal, with the reason.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys

SUFFIX = "-stack"


def _git(*args: str, cwd: str | None = None, timeout: int = 600) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=timeout)


def _last(r: subprocess.CompletedProcess) -> str:
    return (r.stderr.strip().splitlines() or [""])[-1][:200]


def own_root(tree: str) -> str | None:
    """`tree`'s top level when it is a checkout with its own git directory (not a secondary worktree), else None."""
    top = _git("-C", tree, "rev-parse", "--show-toplevel", timeout=20)
    gd = _git("-C", tree, "rev-parse", "--absolute-git-dir", timeout=20)
    cd = _git("-C", tree, "rev-parse", "--path-format=absolute", "--git-common-dir", timeout=20)
    if top.returncode or gd.returncode or cd.returncode:
        return None
    if os.path.realpath(gd.stdout.strip()) != os.path.realpath(cd.stdout.strip()):
        return None
    return os.path.realpath(top.stdout.strip())


def origin_url(tree: str) -> str:
    return _git("-C", tree, "remote", "get-url", "origin", timeout=20).stdout.strip()


def stack_path(main: str) -> str:
    main = os.path.realpath(main).rstrip("/")
    return os.path.join(os.path.dirname(main), os.path.basename(main) + SUFFIX)


def is_stack_checkout(tree: str) -> bool:
    """True when `tree` is a stack checkout: `<main>-stack`, its own clone, with the same origin as `<main>`."""
    real = os.path.realpath(tree).rstrip("/")
    if not real.endswith(SUFFIX) or own_root(real) != real:
        return False
    main = real[: -len(SUFFIX)]
    url = origin_url(real)
    return bool(url) and own_root(main) == main and origin_url(main) == url


def sync(main: str, ref: str, fetch: bool = True) -> tuple[int, str]:
    main = os.path.realpath(main)
    if own_root(main) != main:
        return 2, f"stack_checkout: {main} is not a main checkout"
    url = origin_url(main)
    if not url:
        return 2, f"stack_checkout: {main} has no origin remote"
    path = stack_path(main)
    if os.path.lexists(path):
        if own_root(path) != path or origin_url(path) != url:
            return 2, f"stack_checkout: {path} exists but is not a clone of {url}; left untouched"
        dirty = _git("-C", path, "status", "--porcelain", "--untracked-files=all").stdout.splitlines()
        if dirty:
            shown = ", ".join(l[3:] for l in dirty[:5]) + (" …" if len(dirty) > 5 else "")
            return 2, f"stack_checkout: {path} has {len(dirty)} uncommitted change(s) ({shown}); nothing discarded"
    else:
        c = _git("clone", "--quiet", "--local", "--no-checkout", main, path)
        if c.returncode != 0:
            return 2, f"stack_checkout: clone failed: {_last(c)}"
        _git("-C", path, "remote", "set-url", "origin", url)
        fetch = True
    if fetch:
        f = _git("-C", path, "fetch", "--quiet", "--prune", "origin")
        if f.returncode != 0:
            return 2, f"stack_checkout: fetch failed: {_last(f)}"
    sha = _git("-C", path, "rev-parse", "--verify", "--quiet", ref + "^{commit}").stdout.strip()
    if not sha:
        return 2, f"stack_checkout: {ref} names no commit"
    c = _git("-C", path, "checkout", "--quiet", "--detach", sha)
    if c.returncode != 0:
        return 2, f"stack_checkout: checkout failed: {_last(c)}"
    head = _git("-C", path, "rev-parse", "HEAD").stdout.strip()
    if head != sha:
        return 2, f"stack_checkout: {path} is at {head[:12]}, not {sha[:12]}"
    return 0, f"STACK-CHECKOUT: {path} {sha}"


def selftest() -> int:
    import shutil
    import tempfile
    fails = 0

    def check(name: str, ok: bool, why: str = "") -> None:
        nonlocal fails
        print(("ok   " if ok else "FAIL ") + name + ("" if ok or not why else f" ({why})"))
        fails += 0 if ok else 1

    tmp = os.path.realpath(tempfile.mkdtemp(prefix="asd-stackco-"))
    try:
        env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
               "GIT_COMMITTER_EMAIL": "t@t"}
        os.environ.update(env)
        origin = os.path.join(tmp, "origin.git")
        main = os.path.join(tmp, "repo")
        _git("init", "--quiet", "--bare", origin)
        _git("clone", "--quiet", origin, main)
        _git("-C", main, "remote", "set-url", "origin", origin)
        open(os.path.join(main, "a.txt"), "w").write("1\n")
        _git("-C", main, "add", "a.txt")
        _git("-C", main, "commit", "--quiet", "-m", "one")
        _git("-C", main, "push", "--quiet", "origin", "HEAD:refs/heads/development")
        _git("-C", main, "push", "--quiet", "origin", "HEAD:refs/heads/stub/x")
        branch = _git("-C", main, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
        rc, out = sync(main, "origin/stub/x")
        path = stack_path(main)
        check("sync creates <main>-stack, its own clone (not a worktree), detached at the ref, origin = main's",
              rc == 0 and own_root(path) == path and origin_url(path) == origin
              and _git("-C", path, "symbolic-ref", "-q", "HEAD").returncode != 0 and out.startswith("STACK-CHECKOUT: ")
              and path not in _git("-C", main, "worktree", "list").stdout)
        check("the main checkout is not moved", _git("-C", main, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip() == branch)
        check("is_stack_checkout: the stack checkout yes, the main checkout no",
              is_stack_checkout(path) and not is_stack_checkout(main))
        other = os.path.join(tmp, "repo-W-1")
        _git("-C", main, "worktree", "add", "--quiet", "--detach", other)
        check("is_stack_checkout: another worktree no", not is_stack_checkout(other))
        open(os.path.join(main, "a.txt"), "w").write("2\n")
        _git("-C", main, "commit", "--quiet", "-am", "two")
        _git("-C", main, "push", "--quiet", "origin", "HEAD:refs/heads/stub/x")
        _git("-C", main, "reset", "--quiet", "--hard", "HEAD~1")
        rc, out = sync(main, "origin/stub/x")
        want = _git("-C", main, "rev-parse", "origin/stub/x").stdout.strip()
        check("sync moves an existing clean stack checkout to the new head",
              rc == 0 and _git("-C", path, "rev-parse", "HEAD").stdout.strip() == want)
        open(os.path.join(path, "junk.txt"), "w").write("x\n")
        rc, out = sync(main, "origin/development")
        check("a dirty stack checkout is refused, nothing discarded",
              rc == 2 and "junk.txt" in out and os.path.exists(os.path.join(path, "junk.txt"))
              and _git("-C", path, "rev-parse", "HEAD").stdout.strip() == want)
        os.remove(os.path.join(path, "junk.txt"))
        rc, out = sync(main, "origin/nope")
        check("an unknown ref is refused", rc == 2 and "names no commit" in out)
        rc, out = sync(other, "origin/stub/x")
        check("--root must be the main checkout, not a worktree", rc == 2 and "not a main checkout" in out)
        main2 = os.path.join(tmp, "plain")
        _git("clone", "--quiet", origin, main2)
        _git("-C", main2, "worktree", "add", "--quiet", "--detach", stack_path(main2), "origin/stub/x")
        rc, out = sync(main2, "origin/stub/x")
        check("a worktree at the stack path is refused, untouched", rc == 2 and "not a clone" in out
              and not is_stack_checkout(stack_path(main2)), out)
        main3 = os.path.join(tmp, "third")
        _git("clone", "--quiet", origin, main3)
        os.makedirs(stack_path(main3))
        rc, out = sync(main3, "origin/stub/x")
        check("a plain directory at the stack path is left untouched", rc == 2 and "not a clone" in out)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("all ok" if not fails else f"{fails} failed")
    return 1 if fails else 0


def main() -> int:
    if "--selftest" in sys.argv[1:]:
        return selftest()
    p = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("sync")
    s.add_argument("--root", required=True)
    s.add_argument("--ref", required=True)
    s.add_argument("--no-fetch", action="store_true")
    q = sub.add_parser("path")
    q.add_argument("--root", required=True)
    a = p.parse_args()
    if a.cmd == "path":
        print(stack_path(a.root))
        return 0
    rc, out = sync(a.root, a.ref, fetch=not a.no_fetch)
    print(out, file=sys.stdout if rc == 0 else sys.stderr)
    return rc


if __name__ == "__main__":
    sys.exit(main())
