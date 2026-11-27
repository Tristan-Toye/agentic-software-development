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

The stack checkout's settings file (config-harness `.runtime.env`) is NEVER copied from the main checkout. `sync` rebuilds
it with the repo's own step, the config key `stack_env_regen` ({command, path, source}; the command runs for the stack
checkout and writes `path`): when the file is missing in the stack checkout, or older than `source` (the encrypted
bootstrap values), `regen_env` runs it, keeps the file 0600 and adds it to the clone's `.git/info/exclude` unless git
ignores it. A failing step gives `STACK-ENV: stopped \u2014 settings file regen failed: <exit> <last output line, scrubbed>`
(exit 2) and nothing is copied instead. The other untracked state a repo keeps (the NATS operator-mode key set under
`deploy/compose/nats/opmode/`) is still COPIED, by `sync`, from the main checkout, as the repo's config lists it in
`stack_state_files` (a trailing `/` marks a directory) when the stack checkout lacks it or holds an older copy: files 0600,
directories 0700, written through a temp file and renamed, symlinks skipped, contents never printed (the output names the
paths only). Nothing here generates a key, and a path the main checkout lacks is skipped and named. Each copied path is
also added to the stack checkout's own `.git/info/exclude` when git does not already ignore it, so the dirty-tree refusal
above never trips on state it put there itself.

# Usage

    stack_checkout.py sync --root /abs/main/checkout --ref origin/stub/2026-11-18 [--no-fetch]
    stack_checkout.py path --root /abs/main/checkout
    stack_checkout.py --selftest

`sync` prints `STACK-CHECKOUT: <path> <full sha>` (then `STACK-STATE: copied <paths>` when it copied any) and exits
0; exit 2 for a refusal, with the reason.
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import stack_repair  # noqa: E402

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


def state_files_for(main: str) -> list[str]:
    """The untracked state paths the repo's stack-repair config lists (`stack_state_files`), [] when none."""
    cfg = stack_repair.load_config(main)
    raw = (cfg or {}).get("stack_state_files") or []
    return [p for p in raw if isinstance(p, str) and p.strip() and not os.path.isabs(p) and ".." not in p.split("/")]


def _newer(src: str, dst: str) -> bool:
    try:
        return not os.path.exists(dst) or os.stat(src).st_mtime > os.stat(dst).st_mtime
    except OSError:
        return False


def _copy_file(src: str, dst: str) -> None:
    """Copy `src` over `dst` at mode 0600 through a temp file beside it; the content is never read into Python text."""
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    tmp = f"{dst}.tmp.{os.getpid()}"
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as out, open(src, "rb") as inp:
            shutil.copyfileobj(inp, out)
        os.replace(tmp, dst)
        os.chmod(dst, 0o600)
    finally:
        if os.path.lexists(tmp):
            os.remove(tmp)


def _exclude(tree: str, rel: str) -> None:
    """Make git in `tree` ignore `rel` (its own .git/info/exclude) unless it already does."""
    probe = rel.rstrip("/") + ("/x" if rel.endswith("/") else "")
    if _git("-C", tree, "check-ignore", "-q", probe, timeout=20).returncode == 0:
        return
    ex = _git("-C", tree, "rev-parse", "--path-format=absolute", "--git-path", "info/exclude", timeout=20).stdout.strip()
    if not ex:
        return
    os.makedirs(os.path.dirname(ex), exist_ok=True)
    line = "/" + rel
    try:
        have = open(ex, encoding="utf-8").read().splitlines()
    except OSError:
        have = []
    if line not in have:
        with open(ex, "a", encoding="utf-8") as f:
            f.write(line + "\n")


def sync_state(main: str, stack: str, rels: list[str]) -> tuple[list[str], list[str]]:
    """Copy the main checkout's untracked stack state into `stack`: (copied paths, paths main lacks). Names only."""
    copied: list[str] = []
    lacking: list[str] = []
    for rel in rels:
        src, dst = os.path.join(main, rel), os.path.join(stack, rel)
        if rel.endswith("/"):
            if not os.path.isdir(src) or os.path.islink(src):
                lacking.append(rel)
                continue
            os.makedirs(dst, exist_ok=True)
            os.chmod(dst, 0o700)
            for dirpath, dirnames, filenames in os.walk(src):
                here = os.path.join(dst, os.path.relpath(dirpath, src))
                os.makedirs(here, exist_ok=True)
                os.chmod(here, 0o700)
                for name in filenames:
                    f = os.path.join(dirpath, name)
                    if os.path.islink(f):
                        continue
                    t = os.path.join(here, name)
                    if _newer(f, t):
                        _copy_file(f, t)
                        copied.append(os.path.relpath(t, stack))
        else:
            if not os.path.isfile(src) or os.path.islink(src):
                lacking.append(rel)
                continue
            if _newer(src, dst):
                _copy_file(src, dst)
                copied.append(rel)
        _exclude(stack, rel)
    return copied, lacking


def regen_config(main: str, cfg: dict | None = None) -> dict | None:
    """The repo's `stack_env_regen` entry ({"command": [...], "path": "<settings file>", "source": "<encrypted source>"}), else None."""
    cfg = cfg if cfg is not None else (stack_repair.load_config(main) or {})
    r = cfg.get("stack_env_regen")
    if not (isinstance(r, dict) and isinstance(r.get("command"), list) and r["command"] and isinstance(r.get("path"), str)):
        return None
    for k in ("path", "source"):
        v = r.get(k, "")
        if v and (not isinstance(v, str) or os.path.isabs(v) or ".." in v.split("/")):
            return None
    return {"cfg": cfg, **r}


def regen_needed(stack: str, rg: dict) -> bool:
    """The settings file is missing in `stack`, or older than the encrypted source the repo keeps beside it."""
    dst = os.path.join(stack, rg["path"])
    if not os.path.isfile(dst):
        return True
    src = os.path.join(stack, rg.get("source") or "")
    try:
        return bool(rg.get("source")) and os.path.isfile(src) and os.stat(src).st_mtime > os.stat(dst).st_mtime
    except OSError:
        return False


def _scrub(text: str) -> str:
    """One stderr line with anything value-shaped removed: NAME=value pairs and long token-like runs."""
    text = re.sub(r"([A-Za-z_][A-Za-z0-9_]*)=\S+", r"\1=<redacted>", text)
    text = re.sub(r"[A-Za-z0-9_+/=.-]{24,}", "<redacted>", text)
    return text.strip()[:160]


def stop_line(reason: str) -> str:
    return f"STACK-ENV: stopped \u2014 {reason}"


def regen_env(main: str, stack: str, run=None, rg: dict | None = None) -> tuple[str, str]:
    """Make the stack checkout's settings file by the repo's OWN step (config `stack_env_regen`), never by copying it from
    the main checkout. (state, text): ("none", "") no regen configured, ("fresh", ...) present and not older than its source,
    ("regenerated", ...), ("failed", "settings file regen failed: <exit> <last output line, scrubbed>"). The file is chmod
    0600 and excluded in the clone's .git/info/exclude when git does not ignore it. Never prints or reads a value."""
    rg = rg if rg is not None else regen_config(main)
    if not rg:
        return "none", ""
    if not regen_needed(stack, rg):
        return "fresh", f"STACK-ENV: {rg['path']} is fresh in {stack}, not regenerated"
    run = run or stack_repair._run
    sub = {"vm": str(rg["cfg"].get("vm", "")), "root": stack, "tree": stack}
    try:
        argv = [str(a).format(**sub) for a in rg["command"]]
    except (KeyError, IndexError, ValueError):
        return "failed", "settings file regen failed: the stack_env_regen command has an unknown placeholder"
    rc, out = run(argv, 1800)
    dst = os.path.join(stack, rg["path"])
    if rc == 0 and not os.path.isfile(dst):
        rc, out = 1, "the command exited 0 but wrote no " + rg["path"]
    if rc != 0:
        lines = [l for l in (out or "").strip().splitlines() if l.strip()]
        tail = _scrub(lines[-1]) if lines else ""
        return "failed", f"settings file regen failed: exit {rc}{': ' + tail if tail else ''}"
    try:
        os.chmod(dst, 0o600)
    except OSError:
        pass
    _exclude(stack, rg["path"])
    return "regenerated", f"STACK-ENV: {rg['path']} regenerated in {stack} by the repo's own step"


def sync(main: str, ref: str, fetch: bool = True, state_files: list[str] | None = None, regen_run=None,
         regen_cfg: dict | None = None) -> tuple[int, str]:
    main = os.path.realpath(main)
    if own_root(main) != main:
        return 2, f"stack_checkout: {main} is not a main checkout"
    url = origin_url(main)
    if not url:
        return 2, f"stack_checkout: {main} has no origin remote"
    path = stack_path(main)
    rels = state_files_for(main) if state_files is None else state_files
    if os.path.lexists(path):
        if own_root(path) != path or origin_url(path) != url:
            return 2, f"stack_checkout: {path} exists but is not a clone of {url}; left untouched"
        for rel in rels:
            if os.path.exists(os.path.join(path, rel)):
                _exclude(path, rel)  # state an earlier sync put there is never "dirty"
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
    out = f"STACK-CHECKOUT: {path} {sha}"
    try:
        copied, lacking = sync_state(main, path, rels)
    except OSError as e:
        return 2, f"stack_checkout: copying the stack state failed: {type(e).__name__} {e.filename or ''}".strip()
    if copied:
        out += "\nSTACK-STATE: copied " + ", ".join(copied)
    if lacking:
        out += "\nSTACK-STATE: absent in the main checkout, not copied: " + ", ".join(lacking)
    state, text = regen_env(main, path, regen_run, regen_cfg)
    if state == "failed":
        return 2, out + "\n" + stop_line(text)
    if state in ("regenerated", "fresh"):
        out += "\n" + text
    return 0, out


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

        # ---- the untracked stack state (.runtime.env, the opmode key set): copied, 0600/0700, never printed ----
        import io
        from contextlib import redirect_stdout, redirect_stderr
        mark = "SECRET-MARKER-9f3a"
        rels = [".runtime.env", "deploy/compose/nats/opmode/"]
        open(os.path.join(main, ".runtime.env"), "w").write(f"A={mark}\n")
        os.makedirs(os.path.join(main, "deploy/compose/nats/opmode"))
        open(os.path.join(main, "deploy/compose/nats/opmode/operator.seed"), "w").write(mark + "\n")
        os.chmod(os.path.join(main, ".runtime.env"), 0o644)
        buf = io.StringIO()
        with redirect_stdout(buf), redirect_stderr(buf):
            rc, out = sync(main, "origin/stub/x", state_files=rels)
            print(out)
        shown = buf.getvalue()
        sf = os.path.join(path, ".runtime.env")
        kf = os.path.join(path, "deploy/compose/nats/opmode/operator.seed")
        check("state: .runtime.env and the opmode dir are copied, the output names paths and no value",
              rc == 0 and open(sf).read() == f"A={mark}\n" and open(kf).read() == mark + "\n"
              and mark not in shown and "STACK-STATE: copied" in shown and ".runtime.env" in shown, shown)
        check("state: files 0600, the directory 0700 whatever the source modes",
              (os.stat(sf).st_mode & 0o777) == 0o600 and (os.stat(kf).st_mode & 0o777) == 0o600
              and (os.stat(os.path.dirname(kf)).st_mode & 0o777) == 0o700)
        rc, out = sync(main, "origin/stub/x", state_files=rels)
        check("state: not ignored by git, yet the next sync is not refused as dirty (excluded in the stack checkout)",
              rc == 0 and "copied" not in out and _git("-C", path, "status", "--porcelain", "--untracked-files=all").stdout.strip() == "", out)
        open(sf, "w").write("A=mine-newer\n")
        future = os.stat(sf).st_mtime + 100
        os.utime(sf, (future, future))
        sync(main, "origin/stub/x", state_files=rels)
        check("state: a newer copy in the stack checkout is kept", open(sf).read() == "A=mine-newer\n")
        os.utime(os.path.join(main, ".runtime.env"), (future + 100, future + 100))
        rc, out = sync(main, "origin/stub/x", state_files=rels)
        check("state: an older copy in the stack checkout is replaced from main", open(sf).read() == f"A={mark}\n" and ".runtime.env" in out)
        rc, out = sync(main, "origin/stub/x", state_files=[".runtime.env", "nope/"])
        check("state: a path main lacks is named, never generated", rc == 0 and "nope/" in out and not os.path.exists(os.path.join(path, "nope")))
        main4 = os.path.join(tmp, "fourth")
        _git("clone", "--quiet", origin, main4)
        check("state: the list comes from the config, not the code (none configured -> none copied)",
              state_files_for(main4) == [] and "STACK-STATE" not in sync(main4, "origin/stub/x")[1])
        # ---- the settings file is REGENERATED by the repo's own step, never copied ----
        main5 = os.path.join(tmp, "fifth")
        _git("clone", "--quiet", origin, main5)
        open(os.path.join(main5, ".runtime.env"), "w").write(f"A={mark}\n")  # the main checkout's file: must never be copied
        rg = {"cfg": {"vm": "v"}, "command": ["regen", "{root}"], "path": ".runtime.env", "source": "enc.env"}
        calls: list[list[str]] = []

        def fake_ok(argv, _t):
            calls.append(argv)
            f = os.path.join(argv[1], ".runtime.env")
            open(f, "w").write(f"B={mark}-regen\n")
            os.chmod(f, 0o644)
            return 0, ""

        buf = io.StringIO()
        with redirect_stdout(buf), redirect_stderr(buf):
            rc, out = sync(main5, "origin/stub/x", regen_run=fake_ok, regen_cfg=rg)
            print(out)
        s5 = stack_path(main5)
        sf5 = os.path.join(s5, ".runtime.env")
        check("regen: called when the stack checkout lacks the settings file, with the stack checkout as root, the main file is not copied",
              rc == 0 and len(calls) == 1 and calls[0] == ["regen", s5] and open(sf5).read() == f"B={mark}-regen\n"
              and "regenerated" in out and mark not in buf.getvalue(), out)
        check("regen: the file is 0600 and excluded in the clone, so the next sync is not refused as dirty",
              (os.stat(sf5).st_mode & 0o777) == 0o600 and _git("-C", s5, "status", "--porcelain", "--untracked-files=all").stdout.strip() == "")
        rc, out = sync(main5, "origin/stub/x", regen_run=fake_ok, regen_cfg=rg)
        check("regen: not called when the file is fresh (no source, or the source is older)", rc == 0 and len(calls) == 1 and "fresh" in out, out)
        open(os.path.join(s5, "enc.env"), "w").write("x\n")
        os.utime(os.path.join(s5, "enc.env"), (os.stat(sf5).st_mtime - 50, os.stat(sf5).st_mtime - 50))
        _exclude(s5, "enc.env")
        sync(main5, "origin/stub/x", regen_run=fake_ok, regen_cfg=rg)
        check("regen: an older source keeps the file, no call", len(calls) == 1)
        os.utime(os.path.join(s5, "enc.env"), (os.stat(sf5).st_mtime + 50, os.stat(sf5).st_mtime + 50))
        sync(main5, "origin/stub/x", regen_run=fake_ok, regen_cfg=rg)
        check("regen: called again when the file is older than the encrypted source", len(calls) == 2)
        os.remove(sf5)
        os.remove(os.path.join(main5, ".runtime.env"))

        def fake_bad(argv, _t):
            calls.append(argv)
            return 3, f"noise\nup.sh: boom A={mark} token {'x' * 40}\n"

        n0 = len(calls)
        buf = io.StringIO()
        with redirect_stdout(buf), redirect_stderr(buf):
            rc, out = sync(main5, "origin/stub/x", regen_run=fake_bad, regen_cfg=rg)
            print(out)
        check("regen: a failing step stops with the exact line, exit code and scrubbed last line, nothing copied, no value printed",
              rc == 2 and len(calls) == n0 + 1 and "STACK-ENV: stopped \u2014 settings file regen failed: exit 3: up.sh: boom" in out
              and mark not in buf.getvalue() and "x" * 40 not in buf.getvalue() and not os.path.exists(sf5), out)
        check("regen: no config key -> no regen", regen_env(main4, stack_path(main4))[0] == "none")
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
