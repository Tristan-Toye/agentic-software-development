#!/usr/bin/env python3
"""Keep the shared stack's settings in step with the branch a test run is on, without asking the owner.

A branch can add a setting the shared stack needs (a new password in the repo's secrets catalog, say). The stack
was started from an older checkout, so its env file lacks it, and every test that sources the repo's env script
refuses ("X is missing from .runtime.env; re-run up.sh"). That is drift, not a test failure and not an owner
decision, and it is handled here:

  check  the setting NAMES the branch's catalog lists against the NAMES in the env file the branch's env script
         reads (the tree's own when it has one, else the main checkout's). Values are never read into anything that is printed,
         logged or returned: a line counts only by its `NAME=` prefix.
  sync   on drift, re-run the repo's up.sh (config `stack_up`) from the PERSISTENT checkout, the only one allowed
         to own the shared stack (a worktree's up.sh refuses: the containers would bind-mount paths that vanish
         with it). That fixes the env file for every tree at once, but only when the persistent checkout's own
         catalog already lists the missing names; when it is on an older branch (development, while the stub
         that added them is still open), no up.sh can produce them. Also not while another run holds the stack
         lease. In all those cases: SKIP. The caller runs without the env script, with the repo's skip
         switches set (the stack tests report loudly as not run), and says so in the PR note; the stub's full
         suite, run from the persistent checkout on the stub, covers them. The persistent checkout is never
         switched, pulled or written to here.

Config: the repo's stack-repair config (scripts/stack_repair.py finds it), keys
  env_script     the env script whose use marks a run as a stack run (e.g. deploy/host/test-env.sh)
  env_catalog    {"file": "<json path in the tree>", "key": "<top-level list>", "field": "variable"}
  env_file       the env file name (default .runtime.env), looked up in the tree, then the main checkout
  lease_probe    argv; exit 0 = the stack lease is free now, 1 = held
  stack_up       argv, run from the persistent checkout {root} (stack_repair's key)
  skip_env       {NAME: value} set on a skipped run
  suite_markers  command words that mark the full stack suite (sync only: it is never run with tests skipped)
Placeholders as in stack_repair. Off with ASD_STACK_ENV_SYNC=0 (drift is then reported and skipped, never synced).
One sync at a time across runners (stack_repair's lock); at most MAX_PER_HOUR syncs per VM per hour.

    stack_env.py check --tree T     prints `ok`, or the missing names; exit 1 on drift
    stack_env.py sync --tree T      prints the STACK-ENV line; exit 0 ok/synced, 1 skipped
    stack_env.py --selftest
Python 3 standard library only.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import stack_repair  # noqa: E402

MAX_PER_HOUR = 4
NAME_RE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)=")


def main_root(tree: str) -> str:
    """The main checkout of `tree` (itself when it is the main checkout)."""
    try:
        r = subprocess.run(["git", "-C", tree, "rev-parse", "--path-format=absolute", "--git-common-dir"],
                           capture_output=True, text=True, timeout=20)
        common = r.stdout.strip()
        if r.returncode == 0 and common:
            return os.path.dirname(common.rstrip("/"))
    except (OSError, subprocess.SubprocessError):
        pass
    return tree


def branch_of(root: str) -> str:
    try:
        r = subprocess.run(["git", "-C", root, "branch", "--show-current"], capture_output=True, text=True, timeout=20)
        return r.stdout.strip() or "a detached head"
    except (OSError, subprocess.SubprocessError):
        return "an unknown branch"


def config_for(tree: str) -> dict | None:
    """The repo's config when it describes the stack env (env_catalog), else None."""
    cfg = stack_repair.load_config(main_root(tree))
    return cfg if cfg and cfg.get("env_catalog") and cfg.get("env_script") else None


def required(tree: str, cfg: dict) -> list[str]:
    cat = cfg["env_catalog"]
    try:
        with open(os.path.join(tree, cat["file"]), encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return []
    rows = data.get(cat.get("key") or "", []) if isinstance(data, dict) else []
    field = cat.get("field") or "variable"
    return [r[field] for r in rows if isinstance(r, dict) and isinstance(r.get(field), str)]


def env_path(tree: str, cfg: dict) -> str | None:
    name = cfg.get("env_file") or ".runtime.env"
    for d in (tree, main_root(tree)):
        p = os.path.join(d, name)
        if os.path.isfile(p):
            return p
    return None


def names_in(path: str | None) -> set[str]:
    """The NAMES assigned in an env file. A value never leaves this function."""
    out: set[str] = set()
    if not path:
        return out
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                m = NAME_RE.match(line)
                if m and line[m.end():].strip():
                    out.add(m.group(1))
    except OSError:
        pass
    return out


def missing(tree: str, cfg: dict) -> list[str]:
    have = names_in(env_path(tree, cfg))
    return [n for n in required(tree, cfg) if n not in have]


def _argv(template, cfg: dict, tree: str) -> list[str]:
    sub = {"vm": cfg["vm"], "root": main_root(tree), "tree": tree, "compose_dir": cfg.get("compose_dir") or "."}
    return [str(a).format(**sub) for a in template]


def lease_free(cfg: dict, tree: str, run=stack_repair._run) -> bool:
    if not cfg.get("lease_probe"):
        return False  # without a probe, never restart a stack someone may be using
    rc, _ = run(_argv(cfg["lease_probe"], cfg, tree), 60)
    return rc == 0


def sync(tree: str, cfg: dict, run=stack_repair._run, log=lambda _t: None) -> dict:
    """{"state": ok|synced|skipped, "missing": [...], "why": str}. Never raises."""
    gone = missing(tree, cfg)
    if not gone:
        return {"state": "ok", "missing": [], "why": ""}

    def skipped(why: str) -> dict:
        return {"state": "skipped", "missing": gone, "why": why}

    if os.environ.get("ASD_STACK_ENV_SYNC", "1") == "0":
        return skipped("automatic sync is off (ASD_STACK_ENV_SYNC=0)")
    if not cfg.get("stack_up"):
        return skipped("the repo's config names no stack_up command")
    root = main_root(tree)
    behind = [n for n in gone if n not in required(root, cfg)]
    if behind:
        return skipped(f"the persistent checkout {root} is on {branch_of(root)}, whose catalog does not have "
                       f"{', '.join(behind)} yet, so no up.sh can write it before the stub reaches that checkout")
    with stack_repair._Lock(900) as got:
        if not got:
            return skipped("another stack repair or sync held the lock for 15 min")
        gone = missing(tree, cfg)  # the holder before us has usually fixed it
        if not gone:
            return {"state": "ok", "missing": [], "why": "in step after waiting for the lock"}
        if not lease_free(cfg, tree, run):
            return skipped("another run holds the stack lease, so the stack is not restarted under it")
        now = time.time()
        key = f"env:{cfg['vm']}"
        used = stack_repair._attempts(key, now)
        if len(used) >= MAX_PER_HOUR:
            return skipped(f"{len(used)} syncs in the last hour, not trying again")
        stack_repair._record(key, now)
        argv = _argv(cfg["stack_up"], cfg, tree)
        log(f"stack-env: {', '.join(gone)} missing; {' '.join(argv)}")
        rc, out = run(argv, 1800)
        still = missing(tree, cfg)
        if rc == 0 and not still:
            return {"state": "synced", "missing": gone, "why": "up.sh from the persistent checkout"}
        tail = out.strip().splitlines()[-1][:160] if out.strip() else ""
        return {"state": "skipped", "missing": still or gone, "why": f"up.sh exit {rc}{': ' + tail if tail else ''}"}


def line(res: dict) -> str:
    names = ", ".join(res["missing"])
    if res["state"] == "ok":
        return "STACK-ENV: in step with this branch"
    if res["state"] == "synced":
        return f"STACK-ENV: synced ({names} were missing; up.sh re-run from the persistent checkout)"
    return (f"STACK-ENV: skipped — the stack's settings are behind this branch ({names} missing) and {res['why']}. "
            "The stack tests did not run; say so in the PR note (they run in the stub's full suite). Not an owner question.")


def is_stack_run(cfg: dict, env_script: str | None, command: list[str]) -> bool:
    """A run that sources the env script (as --env-script, or as a command word)."""
    want = os.path.normpath(cfg["env_script"])
    norm = lambda p: os.path.normpath(p).lstrip("./") if p else ""
    return norm(env_script).endswith(want) or any(norm(w).endswith(want) for w in command if cfg["env_script"] in w)


def is_suite_run(cfg: dict, command: list[str]) -> bool:
    """A full stack suite that sources the env script itself (config `suite_markers`): it gets the sync, never the
    skip, because the full suite is the place the stack tests must run."""
    marks = cfg.get("suite_markers") or []
    return any(m in command for m in marks)


def strip_env_script(cfg: dict, env_script: str | None, command: list[str]) -> tuple[str | None, list[str]]:
    """The same run without the env script: drop --env-script, and an inline `. <script> &&` in the command."""
    want = cfg["env_script"]
    if env_script and want in env_script:
        env_script = None
    out, i = [], 0
    while i < len(command):
        if command[i] in (".", "source") and i + 1 < len(command) and want in command[i + 1]:
            i += 2
            if i < len(command) and command[i] == "&&":
                i += 1
            continue
        out.append(command[i])
        i += 1
    return env_script, out


def selftest() -> int:
    fails = 0

    def check(name: str, ok: bool) -> None:
        nonlocal fails
        print(("ok   " if ok else "FAIL ") + name)
        fails += 0 if ok else 1

    def catalog(d: str, names: list[str]) -> None:
        with open(os.path.join(d, "cat.json"), "w") as f:
            json.dump({"bootstrap": [{"variable": n} for n in names]}, f)

    with tempfile.TemporaryDirectory() as tmp:
        os.environ["ASD_STACK_REPAIR_DIR"] = os.path.join(tmp, "state")
        os.environ.pop("ASD_STACK_ENV_SYNC", None)
        main = os.path.join(tmp, "repo")
        tree = os.path.join(tmp, "repo-W-1")
        os.makedirs(main)
        subprocess.run(["git", "init", "-q", main], check=True)
        subprocess.run(["git", "-C", main, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "x"], check=True)
        subprocess.run(["git", "-C", main, "worktree", "add", "-q", "--detach", tree], check=True)
        cfg = {"vm": "fake", "env_script": "deploy/host/test-env.sh",
               "env_catalog": {"file": "cat.json", "key": "bootstrap", "field": "variable"},
               "lease_probe": ["probe"], "stack_up": ["up", "{root}"], "skip_env": {"X_TESTS": "skip"}}
        catalog(tree, ["A", "B"])
        catalog(main, ["A"])  # the persistent checkout is on an older branch
        env = os.path.join(main, ".runtime.env")
        with open(env, "w") as f:
            f.write("A=secret-a\nexport C=1\nB=\n")
        check("main root of a worktree is the main checkout", os.path.realpath(main_root(tree)) == os.path.realpath(main))
        check("names only, an empty value counts as missing", names_in(env) == {"A", "C"})
        check("drift: B missing, read from the main checkout's file", missing(tree, cfg) == ["B"])

        calls: list[list[str]] = []
        state = {"lease": 0, "up": 0, "writes": True}

        def fake(argv, _timeout):
            calls.append(argv)
            if argv[0] == "probe":
                return state["lease"], ""
            if state["writes"]:  # up.sh writes the persistent checkout's file from ITS catalog
                with open(env, "w") as f:
                    f.write("".join(f"{n}=v\n" for n in required(main, cfg)))
            return state["up"], "up done"

        r = sync(tree, cfg, fake)
        check("persistent checkout behind the branch: skipped, nothing run, the reason names it",
              r["state"] == "skipped" and calls == [] and "does not have B" in r["why"])
        check("the skip line names the setting and never a value", "B missing" in line(r) and "secret" not in line(r))
        catalog(main, ["A", "B"])
        state["lease"] = 1
        r = sync(tree, cfg, fake)
        check("lease held: skipped, up.sh not run", r["state"] == "skipped" and "lease" in r["why"] and calls == [["probe"]])
        calls.clear()
        state["lease"] = 0
        r = sync(tree, cfg, fake)
        check("lease free: up.sh runs from the persistent checkout and the drift is gone",
              r["state"] == "synced" and calls[-1] == ["up", main_root(tree)] and missing(tree, cfg) == [])
        calls.clear()
        check("in step: nothing is run", sync(tree, cfg, fake)["state"] == "ok" and calls == [])
        with open(env, "w") as f:
            f.write("A=a\n")
        state["writes"] = False
        r = sync(tree, cfg, fake)
        check("an up.sh that leaves the drift is a skip, not a pass", r["state"] == "skipped" and "exit 0" in r["why"])
        os.environ["ASD_STACK_ENV_SYNC"] = "0"
        calls.clear()
        check("ASD_STACK_ENV_SYNC=0: skipped, nothing run", sync(tree, cfg, fake)["state"] == "skipped" and calls == [])
        os.environ.pop("ASD_STACK_ENV_SYNC")
        for _ in range(3):
            sync(tree, cfg, fake)
        r = sync(tree, cfg, fake)
        check("bounded: past MAX_PER_HOUR syncs it skips", r["state"] == "skipped" and "last hour" in r["why"])

        check("stack run: --env-script names the env script", is_stack_run(cfg, "deploy/host/test-env.sh", ["cargo", "test"]))
        check("stack run: an inline `. ./deploy/host/test-env.sh`", is_stack_run(cfg, None, [".", "./deploy/host/test-env.sh", "&&", "cargo"]))
        check("not a stack run: plain cargo test", not is_stack_run(cfg, None, ["cargo", "test", "-p", "x"]))
        check("the full suite is told apart by its marker", is_suite_run({"suite_markers": ["--stack"]}, ["scripts/run_all_tests.sh", "--stack"])
              and not is_suite_run({"suite_markers": ["--stack"]}, ["cargo", "test"]))
        es, cmd = strip_env_script(cfg, "deploy/host/test-env.sh", [".", "deploy/host/test-env.sh", "&&", "cargo", "nextest", "run"])
        check("skip: the env script is dropped, inline and flag", es is None and cmd == ["cargo", "nextest", "run"])
    print(f"{'FAILED' if fails else 'passed'}: stack_env selftest")
    return 1 if fails else 0


def cli(argv: list[str]) -> int:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("action", nargs="?", choices=("check", "sync"))
    ap.add_argument("--tree", default=".")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args(argv)
    if a.selftest:
        return selftest()
    tree = os.path.abspath(a.tree)
    cfg = config_for(tree)
    if not cfg:
        print("no stack env config for this repo")
        return 0
    if a.action == "check":
        gone = missing(tree, cfg)
        print("ok" if not gone else "missing: " + ", ".join(gone))
        return 1 if gone else 0
    res = sync(tree, cfg, log=lambda t: print(t, file=sys.stderr))
    print(line(res))
    return 1 if res["state"] == "skipped" else 0


if __name__ == "__main__":
    sys.exit(cli(sys.argv[1:]))
