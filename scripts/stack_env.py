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
  skip_banner    regex (or list) of the line a test prints when it returns early under skip_env; run_tests counts it as skipped, not passed
  suite_markers  command words that mark the full stack suite (sync only: it is never run with tests skipped)
  fixture_stamp  argv printing the tree's fake-llm source stamp (`Sources:<sha>`); {stamp_dir} is the checkout it runs in
  fixture_probe  argv printing the stamp the RUNNING fixture serves (exit 1 = not answering, 2 = no stamp line)
  leftover_projects list of {"name", "owner_probe" (argv printing the checkout that owns the project, empty = no project),
                 "window_probe" (argv printing the suite window file: root, then holder pid; exit 0 when absent),
                 "pid_probe" (argv with {pid}; exit 0 = alive), "down" (argv, run from {root} = the persistent checkout)}
  fixture_rebuild argv that rebuilds and restarts the fixture from {root} (it takes the stack lease itself and exits 75
                 when it is held)
Placeholders as in stack_repair. Off with ASD_STACK_ENV_SYNC=0 (drift is then reported and skipped, never synced).
One sync at a time across runners (stack_repair's lock); at most MAX_PER_HOUR syncs per VM per hour.

Fixture freshness (`fixture()`, run after the env check): the repo's env script refuses every DB-backed test when the
shared fake-llm fixture serves another stamp than the tree's. The stamp includes the root Cargo files, so a dossier
that changes a dependency always differs from development. Handled the same way, never an owner question and never
a rebuild from a worktree:
  rebuild  stale + the PERSISTENT checkout stamps the same as the tree + the stack lease free: fixture_rebuild from the
           persistent checkout, under stack_repair's lock, at most MAX_PER_HOUR per VM per hour (own key), holding the
           stack lease. A full-suite run (suite_markers, /stub) whose tree stamps differently from the persistent
           checkout rebuilds from the tree being integrated instead: it owns the stack for the whole run and never skips.
  skip     otherwise (stamps differ, lease held, rebuild failed or still stale): `STACK-ENV: skipped — fake-llm
           fixture ...`, the run goes ahead with skip_env, the stub's full suite covers those tests.
Off with ASD_STACK_FIXTURE_REBUILD=0 (stale is then skipped, never rebuilt).
Leftover project (`leftover()`, for a session's `RUNNER: ENV stack foreign_project_state ...`): a compose project an
earlier suite left up from the persistent checkout makes a worktree's bring-up refuse. The repo's config lists such
projects (`leftover_projects`); the runner tears one down itself ONLY when it can prove it is dead:
  safe     the suite window's holder pid (line 2 of the window file) is no longer alive in the VM (or there is no
           window) AND the stack lease is free. Then `down` runs from the PERSISTENT checkout under stack_repair's lock,
           at most LEFTOVER_MAX_PER_HOUR per VM per hour. The repo's own down takes the window guard and the lease itself.
  skip     otherwise (live holder, unreadable pid, lease held, down failed or left it up, cap reached): `STACK-ENV:
           skipped — leftover project ...`; the session skips its live stack tests, the stub's full suite covers them.
Never an owner question. Independent of ASD_STACK_REPAIR; off with ASD_STACK_LEFTOVER_REPAIR=0.

Ownership hand-off (`handoff()`, for a /stub run; `handoff: true` on a leftover_projects entry): the shared project belongs to
whichever checkout last brought it up. A /stub run builds in the stack checkout (`<main>-stack`), so a project the MAIN checkout
owns refuses its bring-up (`foreign_project_state owner=<main>`), and the reverse. Only between those two persistent
checkouts of the same repo, and only with the stack lease free and no live suite window, the runner hands it over: the
repo's own `down` from the OWNING checkout, then the entry's `handoff_up` (default: the config's `stack_up`) from the stack
checkout, under stack_repair's lock, at most LEFTOVER_MAX_PER_HOUR per VM per hour (own key). An owner that is neither of the two
(a worktree) is never touched here: a registered worktree is alive and is a skip; an unregistered one goes through
the dead-proof of `leftover` first. The result is PROVEN, never assumed: the owner probe must name the stack checkout and the
env script's settings file (`env_file`, default .runtime.env) must exist in it, else the state is `failed` with the
exact reason. `verify()` is the same post-check for any "repaired" claim. A /stub run turns a failure into a stop (run_dossier:
`STACK-ENV: stopped`), never an owner question. Config key `stack_state_files` lists the untracked state
stack_checkout.py copies into the stack checkout.

    stack_env.py check --tree T     prints `ok`, or the missing names; exit 1 on drift
    stack_env.py sync --tree T      prints the STACK-ENV line; exit 0 ok/synced, 1 skipped
    stack_env.py fixture --tree T [--suite]   same for the fixture; exit 0 ok/rebuilt, 1 skipped
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
import stack_checkout  # noqa: E402
import stack_repair  # noqa: E402

MAX_PER_HOUR = 4
LEFTOVER_MAX_PER_HOUR = 3
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


def _argv(template, cfg: dict, tree: str, root: str | None = None, stamp_dir: str | None = None) -> list[str]:
    root = root or main_root(tree)
    sub = {"vm": cfg["vm"], "root": root, "tree": tree, "compose_dir": cfg.get("compose_dir") or ".",
           "stamp_dir": stamp_dir or tree}
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


RC_LEASE_HELD = 75  # what fixture_rebuild exits with when flock finds the stack lease taken


def _stamp(cfg: dict, tree: str, where: str, run) -> str | None:
    """The `Sources:<sha>` line source_stamp.sh prints in checkout `where`, or None when it cannot say."""
    rc, out = run(_argv(cfg["fixture_stamp"], cfg, tree, stamp_dir=where), 120)
    lines = out.strip().splitlines()
    return lines[-1].strip() if rc == 0 and lines else None


def _served(cfg: dict, tree: str, run) -> str | None:
    """What the running fixture serves: its stamp, "no stamp line" for a pre-stamp image (probe exit 2), None when
    it does not answer (exit 1, or the VM is out of reach): not a fact about its stamp, so nothing to repair."""
    rc, out = run(_argv(cfg["fixture_probe"], cfg, tree), 60)
    if rc == 0 and out.strip():
        return out.strip().splitlines()[-1].strip()
    return "no stamp line" if rc == 2 else None


def fixture(tree: str, cfg: dict, suite: bool = False, run=stack_repair._run, log=lambda _t: None) -> dict:
    """{"state": ok|rebuilt|skipped, "tree": stamp, "served": stamp, "why": str}. Never raises."""
    if not (cfg.get("fixture_stamp") and cfg.get("fixture_probe")):
        return {"state": "ok", "tree": "", "served": "", "why": ""}
    served = _served(cfg, tree, run)
    if served is None:
        return {"state": "ok", "tree": "", "served": "", "why": "fixture not answering"}
    mine = _stamp(cfg, tree, tree, run)
    if mine is None or mine == served:
        return {"state": "ok", "tree": mine or "", "served": served, "why": ""}

    def skipped(why: str) -> dict:
        return {"state": "skipped", "tree": mine, "served": served, "why": why}

    if os.environ.get("ASD_STACK_FIXTURE_REBUILD", "1") == "0":
        return skipped("automatic rebuild is off (ASD_STACK_FIXTURE_REBUILD=0)")
    if not cfg.get("fixture_rebuild"):
        return skipped("the repo's config names no fixture_rebuild command")
    root = main_root(tree)
    base = root
    if root != tree:
        theirs = _stamp(cfg, tree, root, run)
        if theirs != mine:
            if not suite:
                return skipped(f"the persistent checkout {root} is on {branch_of(root)} and stamps "
                               f"{(theirs or 'nothing')[:20]}, so a rebuild would have to come from a worktree, "
                               "which the shared fixture must never be bound to")
            base = tree  # the full suite owns the stack for its whole run and is never skipped
    with stack_repair._Lock(900) as got:
        if not got:
            return skipped("another stack repair or sync held the lock for 15 min")
        again = _served(cfg, tree, run)  # the holder before us has usually rebuilt it
        if again == mine:
            return {"state": "ok", "tree": mine, "served": again, "why": "in step after waiting for the lock"}
        if not lease_free(cfg, tree, run):
            return skipped("another run holds the stack lease, so the fixture is not restarted under it")
        now = time.time()
        key = f"fixture:{cfg['vm']}"
        if len(stack_repair._attempts(key, now)) >= MAX_PER_HOUR:
            return skipped(f"{MAX_PER_HOUR} fixture rebuilds in the last hour, not trying again")
        stack_repair._record(key, now)
        argv = _argv(cfg["fixture_rebuild"], cfg, tree, root=base)
        log(f"stack-env: fake-llm fixture stale; {' '.join(argv)}")
        rc, out = run(argv, 1800)
        if rc == RC_LEASE_HELD:
            return skipped("another run holds the stack lease, so the fixture is not restarted under it")
        now_served = _served(cfg, tree, run)
        if rc == 0 and now_served == mine:
            return {"state": "rebuilt", "tree": mine, "served": now_served,
                    "why": f"rebuilt from {'the persistent checkout' if base == root else 'the tree being integrated'}"}
        tail = out.strip().splitlines()[-1][:160] if out.strip() else ""
        return skipped(f"the rebuild exited {rc} and the fixture still serves {(now_served or 'nothing')[:20]}"
                       f"{': ' + tail if tail else ''}")


def leftover_config(tree: str) -> dict | None:
    cfg = stack_repair.load_config(main_root(tree))
    return cfg if cfg and cfg.get("leftover_projects") else None
def _sub(template, cfg: dict, root: str, **extra) -> list[str]:
    """Placeholder substitution by plain replace: these argv hold shell and docker `{{...}}` / `${...}` braces."""
    sub = {"vm": cfg["vm"], "root": root, "compose_dir": cfg.get("compose_dir") or ".", **extra}
    out = []
    for a in template:
        a = str(a)
        for k, v in sub.items():
            a = a.replace("{" + k + "}", str(v))
        out.append(a)
    return out
def _window(proj: dict, cfg: dict, root: str, run) -> tuple[str, str] | None:
    """(holder root, holder pid) of the suite window file; ("", "") when there is none; None when unreadable."""
    if not proj.get("window_probe"):
        return ("", "")
    rc, out = run(_sub(proj["window_probe"], cfg, root), 60)
    if rc != 0:
        return None
    lines = [ln.strip() for ln in out.splitlines() if ln.strip()]
    return (lines[0], lines[1] if len(lines) > 1 else "") if lines else ("", "")
def _leftover_one(proj: dict, tree: str, cfg: dict, root: str, run, log) -> dict:
    name = str(proj.get("name") or "?")
    def res(state: str, why: str) -> dict:
        return {"state": state, "name": name, "why": why}
    if not (proj.get("owner_probe") and proj.get("down") and proj.get("pid_probe") and cfg.get("lease_probe")):
        return res("skipped", "the repo's config does not name every probe and the down command")
    rc, out = run(_sub(proj["owner_probe"], cfg, root), 60)
    if rc != 0:
        return res("skipped", f"could not read the project's owner (exit {rc})")
    owner = out.strip().splitlines()[-1].strip() if out.strip() else ""
    if not owner:
        return res("none", "the project is not up")
    win = _window(proj, cfg, root, run)
    if win is None:
        return res("skipped", "could not read the suite window, so it is not proven dead")
    holder, pid = win
    if holder and not pid.isdigit():
        return res("skipped", f"the suite window names {holder} without a readable holder pid, so it is not proven dead")
    if holder and run(_sub(proj["pid_probe"], cfg, root, pid=pid), 60)[0] == 0:
        return res("skipped", f"the suite window holder pid {pid} ({holder}) is still alive")
    if not lease_free(cfg, tree, run):
        return res("skipped", "another run holds the stack lease")
    with stack_repair._Lock(900) as got:
        if not got:
            return res("skipped", "another stack repair held the lock for 15 min")
        if not lease_free(cfg, tree, run):  # the lock holder before us may have started something
            return res("skipped", "another run holds the stack lease")
        now = time.time()
        key = f"leftover:{cfg['vm']}"
        if len(stack_repair._attempts(key, now)) >= LEFTOVER_MAX_PER_HOUR:
            return res("skipped", f"{LEFTOVER_MAX_PER_HOUR} leftover repairs in the last hour, not trying again")
        stack_repair._record(key, now)
        argv = _sub(proj["down"], cfg, root)
        log(f"stack-env: leftover project {name} (owner {owner}, window holder "
            f"{('pid ' + pid + ' dead') if holder else 'none'}, lease free); {' '.join(argv)}")
        rc, out = run(argv, 900)
        left = run(_sub(proj["owner_probe"], cfg, root), 60)
        if rc == 0 and left[0] == 0 and not left[1].strip():
            return res("repaired", f"torn down with the repo's own down from {root}")
        tail = out.strip().splitlines()[-1][:160] if out.strip() else ""
        return res("skipped", f"the down exited {rc}{': ' + tail if tail else ''}")
def leftover(tree: str, cfg: dict, only: str = "", run=stack_repair._run, log=lambda _t: None) -> dict:
    """{"state": none|repaired|skipped, "name": str, "why": str}. Tears a leftover compose project down only when
    provably dead (see the module docstring). `only` limits it to the projects whose name it contains. Never raises."""
    projects = [p for p in cfg.get("leftover_projects") or [] if isinstance(p, dict) and (not only or str(p.get("name")) in only)]
    if not projects:
        return {"state": "none", "name": "", "why": "no leftover project is configured for this"}
    if os.environ.get("ASD_STACK_LEFTOVER_REPAIR", "1") == "0":
        return {"state": "skipped", "name": str(projects[0].get("name")), "why": "automatic leftover repair is off (ASD_STACK_LEFTOVER_REPAIR=0)"}
    root = main_root(tree)
    last = {"state": "none", "name": "", "why": ""}
    for proj in projects:
        try:
            r = _leftover_one(proj, tree, cfg, root, run, log)
        except Exception as e:  # noqa: BLE001 - never ends a run
            r = {"state": "skipped", "name": str(proj.get("name")), "why": f"the check crashed: {e!r}"}
        if r["state"] == "skipped":
            return r
        if r["state"] == "repaired" or last["state"] == "none":
            last = r
    return last
# ---- ownership hand-off between the two persistent checkouts (main <-> stack checkout) ----
def persistent_pair(root: str) -> tuple[str, str]:
    """(main checkout, stack checkout) for `root`, whichever of the two it is."""
    real = os.path.realpath(root).rstrip("/")
    if real.endswith(stack_checkout.SUFFIX) and stack_checkout.is_stack_checkout(real):
        real = real[: -len(stack_checkout.SUFFIX)]
    return real, stack_checkout.stack_path(real)


def registered_worktrees(main: str) -> set[str]:
    try:
        r = subprocess.run(["git", "-C", main, "worktree", "list", "--porcelain"], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return set()
    return {os.path.realpath(ln[9:].strip()) for ln in r.stdout.splitlines() if ln.startswith("worktree ")}


def project_owner(proj: dict, cfg: dict, root: str, run) -> tuple[str | None, str]:
    """(owner checkout or "" when the project is not up, "") or (None, why) when the probe cannot say."""
    if not proj.get("owner_probe"):
        return None, "the repo's config names no owner_probe"
    rc, out = run(_sub(proj["owner_probe"], cfg, root), 60)
    if rc != 0:
        return None, f"could not read the project's owner (exit {rc})"
    return (out.strip().splitlines()[-1].strip() if out.strip() else ""), ""


def _same(a: str, b: str) -> bool:
    return os.path.realpath(a).rstrip("/") == os.path.realpath(b).rstrip("/")


def settings_file(cfg: dict, root: str) -> str:
    return os.path.join(root, cfg.get("env_file") or ".runtime.env")


def verify(root: str, cfg: dict, run=stack_repair._run) -> str:
    """"" when the stack checkout of `root` is proven usable, else the exact reason. Proof: the env script's settings file exists
    in the stack checkout, and no configured hand-off project is up under another checkout than the stack checkout."""
    main, stack = persistent_pair(root)
    if not os.path.isfile(settings_file(cfg, stack)):
        return (f"the env script's settings file {cfg.get('env_file') or '.runtime.env'} does not exist in the stack checkout {stack}"
                + ("" if os.path.isfile(settings_file(cfg, main)) else f" nor in the main checkout {main}, so there is nothing to copy (up.sh writes it)"))
    for proj in cfg.get("leftover_projects") or []:
        if isinstance(proj, dict) and proj.get("handoff") and proj.get("owner_probe"):
            owner, why = project_owner(proj, cfg, main, run)
            if owner is None:
                return f"project {proj.get('name')}: {why}"
            if owner and not _same(owner, stack):
                return f"project {proj.get('name')} is owned by {owner}, not the stack checkout {stack}"
    return ""


def handoff(root: str, cfg: dict, run=stack_repair._run, log=lambda _t: None) -> dict:
    """{"state": ok|none|handed|skipped|failed, "name", "owner", "why"}. Makes the stack checkout own the shared project when the
    main checkout does (see the module docstring). Never raises. `skipped` = a precondition refuses it (lease, window, a
    live foreign owner, the attempt cap); `failed` = it ran and the post-check did not prove it."""
    main, stack = persistent_pair(root)
    projs = [p for p in cfg.get("leftover_projects") or [] if isinstance(p, dict) and p.get("handoff")]
    name = str(projs[0].get("name")) if projs else ""

    def res(state: str, why: str, owner: str = "") -> dict:
        return {"state": state, "name": name, "owner": owner, "why": why}

    if not projs:
        return res("none", "no hand-off project is configured")
    if not stack_checkout.is_stack_checkout(stack):
        return res("skipped", f"{stack} is not a stack checkout of {main}")
    proj = projs[0]
    try:
        return _handoff_one(proj, main, stack, cfg, run, log, res)
    except Exception as e:  # noqa: BLE001 - never ends a run
        return res("failed", f"the hand-off crashed: {e!r}")


def _handoff_one(proj: dict, main: str, stack: str, cfg: dict, run, log, res) -> dict:
    if not (proj.get("down") and cfg.get("lease_probe") and (proj.get("handoff_up") or cfg.get("stack_up"))):
        return res("skipped", "the repo's config does not name the down, the bring-up and the lease probe")
    owner, why = project_owner(proj, cfg, main, run)
    if owner is None:
        return res("skipped", why)
    if not owner:
        return res("none", "the project is not up", owner)
    if _same(owner, stack):
        why = verify(main, cfg, run)
        return res("failed", why, owner) if why else res("ok", "the stack checkout already owns it", owner)
    if not _same(owner, main):
        # Neither persistent checkout: a registered worktree is alive and never touched; an unregistered one is only torn
        # down through `leftover`'s dead-proof (suite window holder gone + lease free), then brought up from the stack checkout.
        if os.path.realpath(owner) in registered_worktrees(main):
            return res("skipped", f"it is owned by the worktree {owner}, which is still registered (alive): not touched", owner)
        r = _leftover_one(proj, main, cfg, main, run, log)
        if r["state"] == "skipped":
            return res("skipped", f"owned by {owner}, which is not one of the two persistent checkouts: {r['why']}", owner)
        owner = ""
    win = _window(proj, cfg, main, run)
    if win is None:
        return res("skipped", "could not read the suite window, so a live suite is not ruled out", owner)
    holder, pid = win
    if holder and not pid.isdigit():
        return res("skipped", f"the suite window names {holder} without a readable holder pid", owner)
    if holder and proj.get("pid_probe") and run(_sub(proj["pid_probe"], cfg, main, pid=pid), 60)[0] == 0:
        return res("skipped", f"the suite window holder pid {pid} ({holder}) is still alive", owner)
    if not lease_free(cfg, stack, run):
        return res("skipped", "another run holds the stack lease", owner)
    with stack_repair._Lock(900) as got:
        if not got:
            return res("skipped", "another stack repair held the lock for 15 min", owner)
        if not lease_free(cfg, stack, run):
            return res("skipped", "another run holds the stack lease", owner)
        now = time.time()
        key = f"handoff:{cfg['vm']}"
        if len(stack_repair._attempts(key, now)) >= LEFTOVER_MAX_PER_HOUR:
            return res("skipped", f"{LEFTOVER_MAX_PER_HOUR} hand-offs in the last hour, not trying again", owner)
        stack_repair._record(key, now)
        if owner:
            argv = _sub(proj["down"], cfg, owner)
            log(f"stack-env: hand-off of {proj.get('name')} from {owner} to {stack}; {' '.join(argv)}")
            rc, out = run(argv, 900)
            if rc != 0:
                tail = out.strip().splitlines()[-1][:160] if out.strip() else ""
                return res("failed", f"the down from {owner} exited {rc}{': ' + tail if tail else ''}", owner)
        argv = _sub(proj.get("handoff_up") or cfg["stack_up"], cfg, stack)
        log(f"stack-env: hand-off bring-up from {stack}; {' '.join(argv)}")
        rc, out = run(argv, 1800)
        now_owner, why = project_owner(proj, cfg, main, run)
        if rc != 0:
            tail = out.strip().splitlines()[-1][:160] if out.strip() else ""
            return res("failed", f"the bring-up from {stack} exited {rc}{': ' + tail if tail else ''}", now_owner or "")
        if not now_owner or not _same(now_owner, stack):
            return res("failed", f"after the bring-up the project owner is {now_owner or 'nobody'}, expected {stack}"
                       + (f" ({why})" if why else ""), now_owner or "")
        bad = verify(main, cfg, run)
        if bad:
            return res("failed", bad, now_owner)
        return res("handed", f"owner {main} -> {stack}: the repo's own down, then the bring-up from the stack checkout", now_owner)

def leftover_line(res: dict) -> str:
    if res["state"] == "repaired":
        return f"STACK-ENV: leftover project {res['name']} repaired ({res['why']})"
    return (f"STACK-ENV: skipped — leftover project {res['name']} was not torn down ({res['why']}). "
            "The live stack tests did not run; say so in the PR note (they run in the stub's full suite). Not an owner question.")
def fixture_line(res: dict) -> str:
    stamps = f"this tree stamps {res['tree'][:20]}, the running fixture served {res['served'][:20]}"
    if res["state"] == "rebuilt":
        return f"STACK-ENV: fake-llm fixture rebuilt ({stamps}; {res['why']})"
    return (f"STACK-ENV: skipped — fake-llm fixture is stale ({stamps}) and {res['why']}. "
            "The DB-backed stack tests did not run; say so in the PR note (they run in the stub's full suite). Not an owner question.")


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

    def check(name: str, ok: bool, why: str = "") -> None:
        nonlocal fails
        print(("ok   " if ok else "FAIL ") + name + ("" if ok or not why else f" ({why})"))
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

        # ---- the fake-llm fixture: stale handling, all through fakes (no VM, no ~/.agentic) ----
        cfg.update({"fixture_stamp": ["stamp", "{stamp_dir}"], "fixture_probe": ["fprobe"],
                    "fixture_rebuild": ["rebuild", "{root}"], "suite_markers": ["--stack"]})
        os.environ.pop("ASD_STACK_FIXTURE_REBUILD", None)
        main, tree = os.path.realpath(main), os.path.realpath(tree)  # tmp is a symlink on macOS
        fx = {"served": "Sources:aaa", "stamps": {tree: "Sources:aaa", main: "Sources:aaa"}, "lease": 0,
              "rebuild_rc": 0, "rebuilt_to": None}
        fcalls: list[list[str]] = []

        def ffake(argv, _t):
            fcalls.append(argv)
            if argv[0] == "fprobe":
                return (0, fx["served"] + "\n") if fx["served"] else (1, "")
            if argv[0] == "stamp":
                return 0, fx["stamps"][argv[1]] + "\n"
            if argv[0] == "probe":
                return fx["lease"], ""
            if argv[0] == "rebuild":
                if fx["rebuild_rc"] == 0 and fx["rebuilt_to"]:
                    fx["served"] = fx["rebuilt_to"]
                return fx["rebuild_rc"], "build done"
            return 127, ""

        def rebuilds():
            return [c for c in fcalls if c[0] == "rebuild"]

        r = fixture(tree, cfg, False, ffake)
        check("fixture fresh: ok, nothing rebuilt", r["state"] == "ok" and rebuilds() == [])
        fx["served"] = None
        check("fixture not answering: ok (not a fact about its stamp)", fixture(tree, cfg, False, ffake)["state"] == "ok" and rebuilds() == [])
        fx.update(served="Sources:old", stamps={tree: "Sources:new", main: "Sources:new"}, rebuilt_to="Sources:new")
        fcalls.clear()
        r = fixture(tree, cfg, False, ffake)
        check("fixture stale, persistent stamp matches, lease free: rebuilt from the persistent checkout",
              r["state"] == "rebuilt" and rebuilds() == [["rebuild", main]] and "rebuilt" in fixture_line(r))
        fx.update(served="Sources:old", stamps={tree: "Sources:new", main: "Sources:dev"})
        fcalls.clear()
        r = fixture(tree, cfg, False, ffake)
        check("fixture stale, persistent stamp differs: skipped, never rebuilt from the worktree",
              r["state"] == "skipped" and rebuilds() == [] and fixture_line(r).startswith("STACK-ENV: skipped — fake-llm fixture"))
        fx.update(stamps={tree: "Sources:new", main: "Sources:new"}, lease=1)
        fcalls.clear()
        r = fixture(tree, cfg, False, ffake)
        check("fixture stale, lease held: skipped, nothing rebuilt", r["state"] == "skipped" and "lease" in r["why"] and rebuilds() == [])
        fx.update(stamps={tree: "Sources:new", main: "Sources:dev"}, lease=0, rebuilt_to="Sources:new")
        fcalls.clear()
        r = fixture(tree, cfg, True, ffake)
        check("suite run with a differing persistent stamp: rebuilt from the tree being integrated, never skipped",
              r["state"] == "rebuilt" and rebuilds() == [["rebuild", tree]])
        fx.update(served="Sources:old", rebuild_rc=1)
        fcalls.clear()
        r = fixture(tree, cfg, True, ffake)
        check("suite run whose rebuild fails: reported skipped for the caller to run it as asked anyway",
              r["state"] == "skipped" and "exited 1" in r["why"])
        fx.update(stamps={tree: "Sources:new", main: "Sources:new"}, rebuild_rc=75, served="Sources:old")
        r = fixture(tree, cfg, False, ffake)
        check("rebuild exit 75 (flock found the lease taken): skipped as lease held", r["state"] == "skipped" and "lease" in r["why"])
        cfg["vm"] = "fake2"  # a fresh hourly budget
        fx.update(rebuild_rc=0, rebuilt_to="Sources:old", served="Sources:old")
        r = fixture(tree, cfg, False, ffake)
        check("a rebuild that leaves it stale is a skip, not a pass", r["state"] == "skipped" and "still serves" in r["why"])
        os.environ["ASD_STACK_FIXTURE_REBUILD"] = "0"
        fcalls.clear()
        check("ASD_STACK_FIXTURE_REBUILD=0: skipped, nothing rebuilt", fixture(tree, cfg, False, ffake)["state"] == "skipped" and rebuilds() == [])
        os.environ.pop("ASD_STACK_FIXTURE_REBUILD")
        cfg["vm"] = "fake3"
        fx.update(rebuild_rc=0, rebuilt_to="Sources:new", served="Sources:old")
        for _ in range(6):
            fx["served"] = "Sources:old"
            r = fixture(tree, cfg, False, ffake)
        check("bounded: past the hourly cap it skips", r["state"] == "skipped" and "last hour" in r["why"])
        del cfg["fixture_stamp"]
        check("no fixture config: nothing checked", fixture(tree, cfg, False, ffake)["state"] == "ok")
        # ---- a leftover compose project: torn down only when provably dead (fakes only; no VM, no ~/.agentic) ----
        lcfg = {"vm": "lfake", "lease_probe": ["probe"], "leftover_projects": [{
            "name": "proj-w1", "owner_probe": ["owner"], "window_probe": ["window"], "pid_probe": ["alive", "{pid}"],
            "down": ["down", "{root}"]}]}
        lf = {"up": True, "window": (0, main + "\n4242\n"), "alive": 1, "lease": 0, "down_rc": 0, "down_clears": True}
        lcalls: list[list[str]] = []
        def lfake(argv, _t):
            lcalls.append(argv)
            if argv[0] == "owner":
                return 0, (main + "\n") if lf["up"] else "\n"
            if argv[0] == "window":
                return lf["window"]
            if argv[0] == "alive":
                return lf["alive"], ""
            if argv[0] == "probe":
                return lf["lease"], ""
            if argv[0] == "down":
                if lf["down_clears"]:
                    lf["up"] = False
                return lf["down_rc"], "down output"
            return 127, ""
        def downs():
            return [c for c in lcalls if c[0] == "down"]
        os.environ.pop("ASD_STACK_LEFTOVER_REPAIR", None)
        r = leftover(tree, lcfg, "foreign_project_state owner=x proj-w1", lfake)
        check("leftover: holder pid dead, lease free: down runs from the persistent checkout",
              r["state"] == "repaired" and downs() == [["down", main]] and "repaired" in leftover_line(r))
        lf["up"] = True
        lcalls.clear()
        lf["window"] = (0, "")
        r = leftover(tree, lcfg, "", lfake)
        check("leftover: no window file at all counts as dead", r["state"] == "repaired" and len(downs()) == 1)
        lf.update(up=True, window=(0, main + "\n4242\n"), alive=0)
        lcalls.clear()
        r = leftover(tree, lcfg, "", lfake)
        check("leftover: live holder pid: skipped, nothing torn down",
              r["state"] == "skipped" and "alive" in r["why"] and downs() == []
              and leftover_line(r).startswith("STACK-ENV: skipped \u2014 leftover project proj-w1") and "Not an owner question" in leftover_line(r))
        lf.update(alive=1, window=(0, main + "\nnot-a-pid\n"))
        r = leftover(tree, lcfg, "", lfake)
        check("leftover: an unreadable pid is not proven dead: skipped", r["state"] == "skipped" and downs() == [])
        lf.update(window=(1, ""))
        r = leftover(tree, lcfg, "", lfake)
        check("leftover: window probe fails: skipped", r["state"] == "skipped" and downs() == [])
        lf.update(window=(0, main + "\n4242\n"), lease=1)
        r = leftover(tree, lcfg, "", lfake)
        check("leftover: lease held: skipped, nothing torn down", r["state"] == "skipped" and "lease" in r["why"] and downs() == [])
        lf.update(lease=0, down_rc=1, down_clears=False)
        r = leftover(tree, lcfg, "", lfake)
        check("leftover: down fails: skipped", r["state"] == "skipped" and "exited 1" in r["why"])
        lf.update(down_rc=0, down_clears=False)
        r = leftover(tree, lcfg, "", lfake)
        check("leftover: a down that leaves the project up is a skip, not a pass", r["state"] == "skipped")
        lf.update(up=False)
        lcalls.clear()
        check("leftover: project not up: nothing to do, nothing run", leftover(tree, lcfg, "", lfake)["state"] == "none" and downs() == [])
        lcfg["vm"] = "lfake2"  # a fresh hourly budget
        lf.update(up=True, down_rc=0, down_clears=False)
        for _ in range(LEFTOVER_MAX_PER_HOUR):
            leftover(tree, lcfg, "", lfake)
        lcalls.clear()
        lf["down_clears"] = True
        r = leftover(tree, lcfg, "", lfake)
        check("leftover: past the hourly cap it skips without running down", r["state"] == "skipped" and "last hour" in r["why"] and downs() == [])
        lcfg["vm"] = "lfake3"
        os.environ["ASD_STACK_LEFTOVER_REPAIR"] = "0"
        lcalls.clear()
        r = leftover(tree, lcfg, "", lfake)
        check("leftover: ASD_STACK_LEFTOVER_REPAIR=0: skipped, nothing run", r["state"] == "skipped" and lcalls == [])
        os.environ.pop("ASD_STACK_LEFTOVER_REPAIR")
        r = leftover(tree, lcfg, "some-other-project", lfake)
        check("leftover: a project the refusal does not name is left alone", r["state"] == "none")
        incomplete = {"vm": "lfake4", "leftover_projects": [{"name": "p", "down": ["down"]}]}
        check("leftover: an incomplete config never tears down", leftover(tree, incomplete, "", lfake)["state"] == "skipped")
        shipped = stack_repair.load_config("/x/config-harness")
        check("the shipped config-harness entry lists nightwatch-w108 with every key",
              bool(shipped) and shipped["leftover_projects"][0]["name"] == "nightwatch-w108"
              and all(shipped["leftover_projects"][0].get(k) for k in ("owner_probe", "window_probe", "pid_probe", "down"))
              and "opmode-stack.sh down" in shipped["leftover_projects"][0]["down"][-1])
        # ---- the ownership hand-off main <-> stack checkout: fakes and temp repos only, never a VM or ~/.agentic ----
        os.environ.update({"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"})
        horigin = os.path.join(os.path.realpath(tmp), "horigin.git")
        hmain = os.path.join(os.path.realpath(tmp), "hrepo")
        subprocess.run(["git", "init", "-q", "--bare", horigin], check=True)
        subprocess.run(["git", "clone", "-q", horigin, hmain], check=True, stderr=subprocess.DEVNULL)
        open(os.path.join(hmain, "a.txt"), "w").write("1\n")
        subprocess.run(["git", "-C", hmain, "add", "a.txt"], check=True)
        subprocess.run(["git", "-C", hmain, "commit", "-q", "-m", "one"], check=True)
        subprocess.run(["git", "-C", hmain, "push", "-q", "origin", "HEAD:refs/heads/dev"], check=True, stderr=subprocess.DEVNULL)
        open(os.path.join(hmain, ".runtime.env"), "w").write("A=hand-off-secret\n")
        rc_, out_ = stack_checkout.sync(hmain, "origin/dev", state_files=[".runtime.env"])
        hstack = stack_checkout.stack_path(hmain)
        check("hand-off setup: the stack checkout exists with the settings file copied", rc_ == 0 and os.path.isfile(os.path.join(hstack, ".runtime.env")))
        hwork = os.path.join(os.path.realpath(tmp), "hrepo-W-9")
        subprocess.run(["git", "-C", hmain, "worktree", "add", "-q", "--detach", hwork], check=True, stderr=subprocess.DEVNULL)
        hcfg = {"vm": "h1", "lease_probe": ["lease"], "stack_up": ["unused"], "env_file": ".runtime.env",
                "leftover_projects": [{"name": "proj", "handoff": True, "owner_probe": ["owner"], "window_probe": ["window"],
                                       "pid_probe": ["pid", "{pid}"], "down": ["down", "{root}"], "handoff_up": ["up", "{root}"]}]}
        hs = {"owner": hmain, "lease": 0, "window": "", "up_sets": True, "down_rc": 0, "up_rc": 0}
        hcalls: list[list[str]] = []

        def hfake(argv, _t):
            hcalls.append(argv)
            if argv[0] == "owner":
                return 0, hs["owner"] + "\n"
            if argv[0] == "lease":
                return hs["lease"], ""
            if argv[0] == "window":
                return 0, hs["window"]
            if argv[0] == "pid":
                return 1, ""
            if argv[0] == "down":
                if hs["down_rc"] == 0:
                    hs["owner"] = ""
                return hs["down_rc"], ""
            if argv[0] == "up":
                if hs["up_sets"] and hs["up_rc"] == 0:
                    hs["owner"] = argv[1]
                return hs["up_rc"], ""
            return 0, ""

        def acts() -> list[str]:
            return [a[0] for a in hcalls if a[0] in ("down", "up")]

        r = handoff(hmain, hcfg, hfake)
        check("hand-off: the main checkout owns it and the lease is free: its own down from main, then the bring-up from the stack checkout, proven",
              r["state"] == "handed" and acts() == ["down", "up"] and [a for a in hcalls if a[0] == "down"][0][1] == hmain
              and [a for a in hcalls if a[0] == "up"][0][1] == hstack and hs["owner"] == hstack, str(r))
        hcalls.clear()
        check("hand-off: a run in the stack checkout itself finds it already owned: nothing run", handoff(hstack, hcfg, hfake)["state"] == "ok" and acts() == [])
        hcfg["vm"] = "h2"
        hs.update(owner=hmain, lease=1)
        hcalls.clear()
        r = handoff(hmain, hcfg, hfake)
        check("hand-off: refused when the stack lease is held: no down, no bring-up, the reason names the lease",
              r["state"] == "skipped" and "lease" in r["why"] and acts() == [] and hs["owner"] == hmain, str(r))
        hcfg["vm"] = "h3"
        hs.update(lease=0, up_sets=False)
        hcalls.clear()
        r = handoff(hmain, hcfg, hfake)
        check("hand-off: up exits 0 but the owner did not change to the stack checkout: failed with the exact reason, never handed",
              r["state"] == "failed" and "expected" in r["why"] and hstack in r["why"] and r["state"] != "handed", str(r))
        hs.update(owner=hmain)
        check("verify: the project still owned by the main checkout is not usable", "is owned by" in verify(hmain, hcfg, hfake))
        hcfg["vm"] = "h4"
        hs.update(owner=hwork, up_sets=True)
        hcalls.clear()
        r = handoff(hmain, hcfg, hfake)
        check("hand-off: a project owned by a still-registered worktree is never touched", r["state"] == "skipped" and "alive" in r["why"] and acts() == [], str(r))
        hcfg["vm"] = "h5"
        hs.update(owner=hmain, down_rc=1)
        hcalls.clear()
        r = handoff(hmain, hcfg, hfake)
        check("hand-off: a failed down stops there (no bring-up) and says so", r["state"] == "failed" and acts() == ["down"] and "down" in r["why"], str(r))
        hs.update(down_rc=0)
        for v in ("h6",):
            hcfg["vm"] = v
            for _ in range(LEFTOVER_MAX_PER_HOUR):
                hs.update(owner=hmain)
                handoff(hmain, hcfg, hfake)
            hs.update(owner=hmain)
            hcalls.clear()
            r = handoff(hmain, hcfg, hfake)
            check("hand-off: bounded per hour, then skipped without running anything", r["state"] == "skipped" and "last hour" in r["why"] and acts() == [], str(r))
        os.remove(os.path.join(hstack, ".runtime.env"))
        hs.update(owner=hstack)
        why = verify(hmain, hcfg, hfake)
        check("verify: a stack checkout without the settings file is not usable, the reason names the file", ".runtime.env" in why and "stack checkout" in why, why)
        check("verify and the hand-off never print a value", "hand-off-secret" not in why and "hand-off-secret" not in str(r))
        check("the shipped config-harness entry has the hand-off keys and the state list",
              bool(shipped) and shipped["leftover_projects"][0].get("handoff") is True
              and "opmode-stack.sh up" in shipped["leftover_projects"][0]["handoff_up"][-1]
              and ".runtime.env" in shipped.get("stack_state_files", []))
    print(f"{'FAILED' if fails else 'passed'}: stack_env selftest")
    return 1 if fails else 0


def cli(argv: list[str]) -> int:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("action", nargs="?", choices=("check", "sync", "fixture"))
    ap.add_argument("--tree", default=".")
    ap.add_argument("--suite", action="store_true")
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
    if a.action == "fixture":
        fres = fixture(tree, cfg, a.suite, log=lambda t: print(t, file=sys.stderr))
        print("STACK-ENV: fake-llm fixture in step" if fres["state"] == "ok" else fixture_line(fres))
        return 1 if fres["state"] == "skipped" else 0
    res = sync(tree, cfg, log=lambda t: print(t, file=sys.stderr))
    print(line(res))
    return 1 if res["state"] == "skipped" else 0


if __name__ == "__main__":
    sys.exit(cli(sys.argv[1:]))
