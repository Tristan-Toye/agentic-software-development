#!/usr/bin/env python3
"""Create and warm every implementer worktree of a wave in one call.

Phase 4 used to run one `git worktree add` per implementer and then
`.worktree-setup.sh` once per worktree, each a separate model step; the setup
(a warm build directory clone) is the slow half and the worktrees are
independent. This script adds the worktrees one after another (git's own locks
make that the safe order; it takes milliseconds) and then runs the repository's
`.worktree-setup.sh <X> <worktree>` for all of them at once.

    wave_worktrees.py --root X [--base SHA] --wt PATH BRANCH [BASE] [--wt ...] [--jobs N] [--no-setup]
    wave_worktrees.py --selftest

`--base` is the fork point for every `--wt` that names none (default: HEAD of
X). A dependent package forks from its dependency's branch head, so it gives
its own BASE. The setup step is the repository's, never the plugin's: absent or
not executable it is skipped; a non-zero exit is reported and the worktree is
kept (slower, never wrong). One line per worktree, then a summary line.
Exit 0: every worktree created; 1: a worktree could not be created (the rest
are still made); 2: unusable input. A worktree that exists is never touched.

No third-party imports: this runs wherever python3 does.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import contextlib
import io
import os
import subprocess
import sys
import tempfile
import time


def run(cmd: list[str], cwd: str | None = None) -> tuple[int, str]:
    proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, check=False)
    return proc.returncode, (proc.stdout + proc.stderr).strip()


def last_line(text: str) -> str:
    lines = [l for l in text.splitlines() if l.strip()]
    return lines[-1].strip() if lines else ""


def add_worktree(root: str, path: str, branch: str, base: str) -> tuple[bool, str]:
    if os.path.exists(path):
        return False, "path exists; not touched"
    rc, out = run(["git", "-C", root, "worktree", "add", path, "-b", branch, base])
    return rc == 0, last_line(out) if rc else "created"


def warm(root: str, path: str) -> str:
    setup = os.path.join(root, ".worktree-setup.sh")
    if not (os.path.isfile(setup) and os.access(setup, os.X_OK)):
        return "no .worktree-setup.sh"
    rc, out = run([setup, root, path])
    return ("warmed: " if rc == 0 else "SETUP FAILED (exit %d, worktree kept): " % rc) + last_line(out)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    ap.add_argument("--root", help="X, the main checkout of the run")
    ap.add_argument("--base", help="fork point for a --wt with no BASE (default: HEAD of X)")
    ap.add_argument("--wt", nargs="+", action="append", default=[], metavar="PATH BRANCH [BASE]")
    ap.add_argument("--jobs", type=int, default=0, help="parallel setups (default: one per worktree, at most 8)")
    ap.add_argument("--no-setup", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)
    if args.selftest:
        return selftest()
    if not args.root or not args.wt:
        ap.error("--root and at least one --wt are required")
    if any(not 2 <= len(w) <= 3 for w in args.wt):
        ap.error("--wt takes PATH BRANCH [BASE]")
    rc, head = run(["git", "-C", args.root, "rev-parse", "HEAD"])
    if rc != 0:
        print("wave_worktrees: %s is not a git checkout" % args.root, file=sys.stderr)
        return 2
    default_base = args.base or head

    made: list[str] = []
    failed = 0
    for spec in args.wt:
        path = os.path.abspath(spec[0])
        ok, msg = add_worktree(args.root, path, spec[1], spec[2] if len(spec) == 3 else default_base)
        print("%s  %s  %s" % ("ADDED " if ok else "FAILED", path, msg))
        if ok:
            made.append(path)
        else:
            failed += 1
    if made and not args.no_setup:
        jobs = args.jobs or min(8, len(made))
        with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, jobs)) as pool:
            for path, msg in zip(made, pool.map(lambda p: warm(args.root, p), made)):
                print("SETUP   %s  %s" % (path, msg))
    print("wave_worktrees: %d created, %d failed" % (len(made), failed))
    return 1 if failed else 0


def selftest() -> int:
    failures: list[str] = []

    def check(name: str, ok: bool, detail: object = "") -> None:
        print(("PASS " if ok else "FAIL ") + name + ("" if ok else " :: %r" % (detail,)))
        if not ok:
            failures.append(name)

    env = {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull,
           "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    saved = {k: os.environ.get(k) for k in env}
    os.environ.update(env)
    try:
        with tempfile.TemporaryDirectory() as td:
            td = os.path.realpath(td)
            root = os.path.join(td, "repo")
            os.makedirs(root)
            run(["git", "init", "-q", "-b", "main", root])
            with open(os.path.join(root, "a.txt"), "w", encoding="utf-8") as fh:
                fh.write("a\n")
            setup = os.path.join(root, ".worktree-setup.sh")
            with open(setup, "w", encoding="utf-8") as fh:
                fh.write('#!/bin/sh\necho "$2" > "$2/.warm"\nsleep 1\necho cloned target\n')
            os.chmod(setup, 0o755)
            run(["git", "-C", root, "add", "-A"])
            run(["git", "-C", root, "commit", "-q", "-m", "init"])
            p1, p2, p3 = (os.path.join(td, "repo-W-1-P%d" % i) for i in (1, 2, 3))
            buf = io.StringIO()
            t0 = time.monotonic()
            with contextlib.redirect_stdout(buf):
                rc = main(["--root", root, "--wt", p1, "b-p1", "--wt", p2, "b-p2", "--wt", p3, "b-p3"])
            wall = time.monotonic() - t0
            out = buf.getvalue()
            check("three worktrees created", rc == 0 and all(os.path.isdir(p) for p in (p1, p2, p3)), out)
            check("setup ran in each worktree", all(os.path.isfile(os.path.join(p, ".warm")) for p in (p1, p2, p3)), out)
            check("setups ran in parallel (3 x 1 s sleeps in under 2.8 s)", wall < 2.8, wall)
            check("one summary line", "3 created, 0 failed" in out, out)
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = main(["--root", root, "--no-setup", "--wt", p1, "b-x", "--wt", os.path.join(td, "ok"), "b-ok"])
            check("an existing path fails alone, the other is still made", rc == 1 and os.path.isdir(os.path.join(td, "ok")), buf.getvalue())
            with contextlib.redirect_stderr(io.StringIO()):
                rc_bad = main(["--root", td, "--wt", os.path.join(td, "z"), "b-z"])
            check("a non-git root is unusable (2)", rc_bad == 2)
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    print("%d failure(s)" % len(failures))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
