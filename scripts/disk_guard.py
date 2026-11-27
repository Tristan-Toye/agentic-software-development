#!/usr/bin/env python3
"""Keep the disk from filling under concurrent dossier runs: report free space, and reclaim build output nobody uses.

Every run builds in its own worktree (`<repo>-<ID>`), and every implementer and test author of a fan-out in its own
sub-worktree (`<repo>-<ID>-P3`, `<repo>-<ID>-TE-…`). A Rust workspace leaves 6 to 20 GB of `target/` in each, the main
checkout's `target/` grows past 100 GB, and a VM that builds the same checkouts keeps its own copy. With a dozen runs
the disk fills, every build and commit fails with "No space left on device", and a VM whose disk image cannot grow
fails every command with an Input/output error. This script is the one place that decides what may go:

- **Build output only.** It deletes the build directories (`target/`; `ASD_DISK_BUILD_DIRS`, colon-separated) of git
  checkouts beside each repository's main checkout, and of the main checkout itself. Never a source file, never a
  worktree, never a branch: everything it removes is rebuilt by the next build.
- **Never a build in use.** A build directory with a write in the last `ASD_DISK_IDLE_MIN` minutes (default 60) is
  kept, and so is the own worktree of a running run (state running with a live pid). A live run's sub-worktrees (its
  fan-out) go once idle, and so does the own worktree of a parked run (queued, waiting or paused with a live pid):
  no build runs there now, and a cold rebuild beats a full disk.
- **In order, only as much as needed.** Worktrees of no live run first, then live runs' idle sub-worktrees, then
  parked runs' own worktrees, then the main checkout; it stops as soon as `ASD_DISK_RECLAIM_GIB` (default 80) GiB are free.
- **The repository's own part.** A repository that keeps build output elsewhere too (a VM's per-checkout target
  directories, a disk image that only shrinks after a trim) ships `.agentic/reclaim.sh`; after a reclaim it runs as
  `bash .agentic/reclaim.sh <checkout> …` in the main checkout, with every checkout whose build output went.
  `sweep` runs the same hook on a timer whatever the free space (the dashboard, hourly), with the dossier worktrees
  whose run is no longer live and whose host build is idle: what a VM keeps fills on its own.

- **A VM's Docker disk.** A repository whose Docker runs in a Colima profile or a Lima instance has a disk of its
  own (30 GB for the podcast engine), which fills with dangling stage images. Each engine is read in the VM
  (`colima ssh -p P -- df -P /var/lib/docker`, `limactl shell I -- df -P …`); below the VM floor
  (`ASD_DISK_VM_FLOOR_PCT` 15 % or `ASD_DISK_VM_FLOOR_GIB` 4 GiB free) a reclaim runs `docker image prune -f` (dangling
  only, never `-a`) then `docker builder prune -f`, both `--filter until=1h`: never a volume, a container, a tagged
  image or a young layer. A VM is never started, stopped, deleted or resized. Engines: `ASD_DISK_DOCKER_ENGINES`
  (`kind:name[:context],…`, or `none`), else `<repo>/.agentic/docker-engines.json`
  (`{"engines": [{"kind": "colima", "name": "podcast-engine", "context": "colima-podcast-engine"}]}`), else detected
  from the repo's scripts. `ASD_DISK_VM_GUARD=0` turns the VM guard off.

The runner refuses to start a stage below `ASD_DISK_FLOOR_GIB` (default 30) GiB free: it queues, starts a reclaim,
and goes on once the disk has room (run_dossier.py `take_slot`). The dashboard checks every 5 minutes, reclaims
below the reclaim threshold, and shows a banner below the floor (references/runner.md, "Disk space").

# Usage

    disk_guard.py status [--json]          free space, thresholds, and what a reclaim would remove
    disk_guard.py reclaim [--dry-run]      reclaim until the target is free, and prune a low VM engine (one at a time)
    disk_guard.py engines [--root R]       JSON: the Docker engines (of repo R, or every active repo) and their free space
    disk_guard.py sweep                    run each repo's .agentic/reclaim.sh with its finished runs' idle worktrees
    disk_guard.py --selftest
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

LIVE = ("running", "testing", "waiting", "paused", "queued")
ID = re.compile(r"^([A-Za-z]+-\d+)(?:-(.+))?$")


def env_f(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name) or default)
    except ValueError:
        return default


def floor_gib() -> float: return env_f("ASD_DISK_FLOOR_GIB", 30)
def target_gib() -> float: return env_f("ASD_DISK_RECLAIM_GIB", 80)
def idle_min() -> float: return env_f("ASD_DISK_IDLE_MIN", 60)
def build_dirs() -> list[str]: return [d for d in (os.environ.get("ASD_DISK_BUILD_DIRS") or "target").split(":") if d]
def runs_dir() -> str: return os.environ.get("ASD_RUNS_DIR") or os.path.expanduser("~/.agentic/runs")


def free_gib(path: str) -> float:
    return shutil.disk_usage(path).free / 2**30


def alive(pid) -> bool:
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, TypeError, ValueError):
        return False


def records() -> list[tuple[str, dict]]:
    out = []
    try:
        names = [n for n in os.listdir(runs_dir()) if n.endswith(".runner.json")]
    except OSError:
        return []
    for n in names:
        try:
            with open(os.path.join(runs_dir(), n)) as fh:
                out.append((n[: -len(".runner.json")], json.load(fh)))
        except (OSError, ValueError):
            continue
    return out


def mains_and_live() -> tuple[list[str], set[str]]:
    """The main checkouts the runner has worked in, and the `<repo>.<ID>` keys of live runs."""
    mains, live = set(), set()
    for key, st in records():
        root = st.get("root")
        if root and os.path.isdir(root):
            mains.add(os.path.realpath(root))
        if st.get("state") in LIVE and alive(st.get("pid")):
            live.add(key)
    return sorted(mains), live


def parked_runs() -> set[str]:
    """Live runs with no stage process working: queued for a slot, waiting on the owner, or paused on a limit."""
    return {key for key, st in records() if st.get("state") in ("queued", "waiting", "paused") and alive(st.get("pid"))}


def worktrees(main: str) -> list[str]:
    try:
        out = subprocess.run(["git", "-C", main, "worktree", "list", "--porcelain"], capture_output=True, text=True, timeout=20).stdout
    except (OSError, subprocess.SubprocessError):
        return [main]
    wts = [os.path.realpath(l[9:]) for l in out.splitlines() if l.startswith("worktree ")]
    return wts or [main]


def last_write(d: str) -> float:
    """The newest write under a build directory, read cheaply: the directory itself, its profiles, and their
    `deps`, `incremental`, `.fingerprint` and `build` directories (a file added there changes the directory)."""
    newest = 0.0
    def see(p: str) -> None:
        nonlocal newest
        try:
            newest = max(newest, os.stat(p).st_mtime)
        except OSError:
            pass
    see(d)
    try:
        for prof in os.scandir(d):
            if prof.is_dir(follow_symlinks=False):
                see(prof.path)
                for sub in ("deps", "incremental", ".fingerprint", "build"):
                    see(os.path.join(prof.path, sub))
    except OSError:
        pass
    return newest


def candidates(now: float | None = None) -> list[dict]:
    """Every build directory a reclaim may remove, in the order it removes them, plus the kept ones with why."""
    now = now or time.time()
    mains, live = mains_and_live()
    parked = parked_runs()
    rows = []
    for main in mains:
        repo = os.path.basename(main)
        for wt in worktrees(main):
            base = os.path.basename(wt)
            owner, sub = None, False
            if wt != main and base.startswith(repo + "-"):
                m = ID.match(base[len(repo) + 1:])
                if m:
                    owner, sub = f"{repo}.{m.group(1)}", bool(m.group(2))
            for b in build_dirs():
                d = os.path.join(wt, b)
                if not os.path.isdir(d):
                    continue
                idle = (now - last_write(d)) / 60
                if wt == main:
                    rank, why = 3, "the main checkout"
                elif owner in parked and not sub:
                    # Queued, waiting or paused: no build runs there now, and one may not for hours. Its next build
                    # starts cold, which beats a full disk, so it goes after the fan-outs, before the main checkout.
                    rank, why = 2.5, f"the own worktree of parked run {owner} (queued, waiting or paused)"
                elif owner in live and not sub:
                    rank, why = None, f"the own worktree of live run {owner}"
                elif owner in live:
                    rank, why = 2, f"a fan-out worktree of live run {owner}"
                else:
                    rank, why = 1, f"no live run ({owner or 'not a dossier worktree'})"
                if rank is not None and idle < idle_min():
                    rank, why = None, f"written {idle:.0f} min ago ({why})"
                rows.append({"dir": d, "checkout": wt, "main": main, "rank": rank, "why": why, "idleMin": round(idle)})
    return sorted(rows, key=lambda r: (r["rank"] is None, r["rank"] or 0, -r["idleMin"]))


# ---- Docker engines: a VM's own disk (Colima profile, Lima instance) ----
# A repository whose Docker runs in a VM has a second disk the Mac's free space never shows: each rebuild of a stage
# image under the same tag leaves the old one dangling, and a 30 GB VM fills in days ("no space left on device").
# Which engines a repository uses, in order: `ASD_DISK_DOCKER_ENGINES` (every repo; `none` turns the VM guard off),
# else `<main>/.agentic/docker-engines.json`, else detection from the repo's scripts (a literal `colima … --profile P`,
# `PROFILE="P"` beside a colima call, `limactl shell I`). Reclaiming touches dangling images and old build cache only.

VM_KINDS = ("colima", "lima")


def vm_floor_pct() -> float: return env_f("ASD_DISK_VM_FLOOR_PCT", 15)
def vm_floor_gib() -> float: return env_f("ASD_DISK_VM_FLOOR_GIB", 4)
def vm_young() -> str: return os.environ.get("ASD_DISK_VM_YOUNG") or "1h"


def make_engine(kind: str, name: str, context: str | None = None) -> dict | None:
    if kind not in VM_KINDS or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", name or ""):
        return None
    if not context:
        context = ("colima" if name == "default" else f"colima-{name}") if kind == "colima" else f"lima-{name}"
    return {"kind": kind, "name": name, "context": context}


def parse_engine_env(v: str) -> list[dict]:
    """`kind:name[:context]` entries, comma-separated; anything else is ignored."""
    out = []
    for part in v.split(","):
        bits = part.strip().split(":")
        e = make_engine(bits[0], bits[1], bits[2] if len(bits) > 2 and bits[2] else None) if len(bits) >= 2 else None
        if e:
            out.append(e)
    return out


def detect_engines(root: str) -> list[dict]:
    """The engines a repository's scripts name literally (a repo that picks its engine at run time lists it in the config)."""
    found: dict[tuple, dict] = {}
    files = []
    for d in ("scripts", ".agentic", "deploy"):
        base = os.path.join(root, d)
        try:
            files += [os.path.join(base, n) for n in sorted(os.listdir(base)) if n.endswith(".sh")][:60]
        except OSError:
            pass
    for f in files:
        try:
            with open(f, errors="replace") as fh:
                text = fh.read(400_000)
        except OSError:
            continue
        if re.search(r"\bcolima\s+(?:start|status|ssh|stop)\b", text):
            names = re.findall(r"\bcolima\s+\w+\s+(?:--profile|-p)[ =][\"']?([A-Za-z0-9][\w.-]*)", text)
            names += re.findall(r"^PROFILE=[\"']?([A-Za-z0-9][\w.-]*)[\"']?\s*$", text, re.M)
            for n in names:
                e = make_engine("colima", n)
                if e: found[("colima", n)] = e
        for n in re.findall(r"\blimactl\s+shell\s+[\"']?([A-Za-z][\w.-]*)", text):
            e = make_engine("lima", n)
            if e: found[("lima", n)] = e
    return list(found.values())


def repo_engines(root: str) -> list[dict]:
    env = os.environ.get("ASD_DISK_DOCKER_ENGINES")
    if env is not None and env.strip():
        return [] if env.strip() == "none" else parse_engine_env(env)
    try:
        with open(os.path.join(root, ".agentic", "docker-engines.json")) as fh:
            cfg = json.load(fh)
        out = []
        for c in (cfg.get("engines") if isinstance(cfg, dict) else None) or []:
            if isinstance(c, dict):
                e = make_engine(str(c.get("kind", "")), str(c.get("name", "")), str(c["context"]) if c.get("context") else None)
                if e: out.append(e)
        return out
    except (OSError, ValueError):
        pass
    return detect_engines(root)


def active_roots() -> list[str]:
    """The main checkouts of the active repos (active-repos.json) plus those the runner has worked in."""
    roots = set(mains_and_live()[0])
    try:
        with open(os.environ.get("ASD_ACTIVE_REPOS_FILE") or os.path.expanduser("~/.agentic/active-repos.json")) as fh:
            for r in (json.load(fh).get("repos") or {}).values():
                if isinstance(r, str) and os.path.isdir(r):
                    roots.add(os.path.realpath(r))
    except (OSError, ValueError, AttributeError):
        pass
    return sorted(roots)


def engines_of(roots: list[str]) -> list[dict]:
    seen: dict[tuple, dict] = {}
    for r in roots:
        for e in repo_engines(r):
            seen.setdefault((e["kind"], e["name"]), {**e, "repos": []})["repos"].append(os.path.basename(r))
    return list(seen.values())


def vm_cmd(e: dict, inner: list[str]) -> list[str]:
    return (["colima", "ssh", "-p", e["name"], "--", *inner] if e["kind"] == "colima"
            else ["limactl", "shell", e["name"], "--", *inner])


def run_q(cmd: list[str], timeout: int = 60) -> subprocess.CompletedProcess | None:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return None


def engine_free(e: dict) -> dict:
    """The free space of the disk that holds /var/lib/docker, read in the VM; `reachable` False when the VM does not
    answer (a stopped VM is left alone: this script never starts one)."""
    p = run_q(vm_cmd(e, ["sh", "-c", "df -P /var/lib/docker 2>/dev/null || df -P /"]), 30)
    out = {**e, "reachable": False, "low": False}
    if not p or p.returncode != 0:
        return out
    rows = [l.split() for l in p.stdout.strip().splitlines() if l.strip() and not l.startswith("Filesystem")]
    try:
        total, avail = int(rows[-1][1]) * 1024, int(rows[-1][3]) * 1024
    except (IndexError, ValueError):
        return out
    if total <= 0:
        return out
    frac = avail / total
    out.update(reachable=True, totalGiB=round(total / 2**30, 1), freeGiB=round(avail / 2**30, 1), freeFrac=round(frac, 3),
               floorPct=vm_floor_pct(), floorGiB=vm_floor_gib(),
               low=frac * 100 < vm_floor_pct() or avail / 2**30 < vm_floor_gib())
    return out


def engines_status(roots: list[str] | None = None) -> list[dict]:
    if os.environ.get("ASD_DISK_VM_GUARD") == "0":
        return []
    return [engine_free(e) for e in engines_of(roots if roots is not None else active_roots())]


def reclaim_engine(e: dict, log, dry: bool = False) -> None:
    """Dangling images, then old build cache, in the engine's own docker: only until the disk is above the floor.
    Never `-a`, a volume, a container, a tagged image, or anything younger than ASD_DISK_VM_YOUNG (default 1h)."""
    steps = [("dangling images", ["image", "prune", "-f", "--filter", f"until={vm_young()}"]),
             ("build cache", ["builder", "prune", "-f", "--filter", f"until={vm_young()}"])]
    cur = engine_free(e)
    if not cur["reachable"]:
        log(f"engine {e['name']} ({e['kind']}): not reachable, left alone")
        return
    start = cur["freeGiB"]
    for what, args in steps:
        if not cur["low"]:
            break
        log(f"engine {e['name']}: {cur['freeGiB']} GiB free ({cur['freeFrac'] * 100:.0f}%), below the floor; "
            f"{'would prune' if dry else 'pruning'} {what}")
        if dry:
            continue
        p = run_q(["docker", "--context", e["context"], *args], 900)
        tail = [l for l in (p.stdout if p else "").splitlines() if "reclaimed" in l.lower()]
        log(f"engine {e['name']}: {what}: {tail[-1].strip() if tail else ('failed' if not p or p.returncode else 'nothing to remove')}")
        cur = engine_free(e)
        if not cur["reachable"]:
            return
    if not dry:
        log(f"engine {e['name']}: {cur['freeGiB']} GiB free ({cur['freeFrac'] * 100:.0f}%) after the reclaim, "
            f"{cur['freeGiB'] - start:+.1f} GiB{' — still below the floor' if cur['low'] else ''}")


def vm_low_for(root: str) -> list[str]:
    """Names of the engines of one repository that are below the VM floor now (the runner's queue check)."""
    return [e["name"] for e in engines_status([root]) if e["low"]]


def status() -> dict:
    mains, _ = mains_and_live()
    probe = mains[0] if mains else os.path.expanduser("~")
    rows = candidates()
    return {"freeGiB": round(free_gib(probe), 1), "floorGiB": floor_gib(), "targetGiB": target_gib(),
            "low": free_gib(probe) < floor_gib(), "reclaimable": [r for r in rows if r["rank"] is not None],
            "kept": [r for r in rows if r["rank"] is None], "engines": engines_status(), "checked": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}


def take_lock(log):
    """The one reclaim-or-sweep lock; None when another holds it."""
    lock = open(os.path.join(runs_dir() if os.path.isdir(runs_dir()) else tempfile.gettempdir(), "disk-reclaim.lock"), "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return lock
    except OSError:
        log("a reclaim or sweep is already running")
        lock.close()
        return None


def run_hook(main: str, checkouts: list[str], log) -> None:
    hook = os.path.join(main, ".agentic", "reclaim.sh")
    if os.path.isfile(hook):
        log(f"running {hook} for {len(checkouts)} checkout(s)")
        try:
            subprocess.run(["bash", hook, *checkouts], cwd=main, timeout=1800)
        except (OSError, subprocess.SubprocessError) as e:
            log(f"{hook} failed: {e}")


def sweep_targets(now: float | None = None) -> dict[str, list[str]]:
    """Per main checkout with a `.agentic/reclaim.sh`: the dossier worktrees (sub-worktrees included) whose run is
    not live and whose host build output, if any, is idle. A worktree that is no dossier's and the main checkout are
    never named: the repository's hook cleans only what a finished run left behind, plus its own orphans."""
    now = now or time.time()
    mains, live = mains_and_live()
    out: dict[str, list[str]] = {}
    for main in mains:
        if not os.path.isfile(os.path.join(main, ".agentic", "reclaim.sh")):
            continue
        repo, picked = os.path.basename(main), []
        for wt in worktrees(main):
            base = os.path.basename(wt)
            if wt == main or not base.startswith(repo + "-"):
                continue
            m = ID.match(base[len(repo) + 1:])
            if not m or f"{repo}.{m.group(1)}" in live:
                continue
            busy = any(os.path.isdir(os.path.join(wt, b)) and (now - last_write(os.path.join(wt, b))) / 60 < idle_min()
                       for b in build_dirs())
            if not busy:
                picked.append(wt)
        out[main] = picked
    return out


def sweep(log=print) -> int:
    """The repositories' own part on a timer, whatever the free space: build output a repository keeps elsewhere
    (a VM's per-checkout target directories, a disk image that only shrinks after a trim) fills on its own."""
    lock = take_lock(log)
    if lock is None:
        return 0
    for main, checkouts in sweep_targets().items():
        run_hook(main, checkouts, log)
    return 0


def reclaim_engines(dry: bool = False, log=print) -> None:
    for e in engines_status():
        if e["low"]:
            reclaim_engine(e, log, dry)


def reclaim(dry: bool = False, log=print) -> int:
    lock = take_lock(log)
    if lock is None:
        return 0
    reclaim_engines(dry, log)
    rows = [r for r in candidates() if r["rank"] is not None]
    done: dict[str, list[str]] = {}
    for r in rows:
        free = free_gib(r["main"])
        if free >= target_gib():
            log(f"{free:.0f} GiB free, at or above the {target_gib():.0f} GiB target: done")
            break
        log(f"{'would remove' if dry else 'removing'} {r['dir']} ({r['why']}, idle {r['idleMin']} min; {free:.0f} GiB free)")
        if not dry:
            shutil.rmtree(r["dir"], ignore_errors=True)
            done.setdefault(r["main"], []).append(r["checkout"])
    for main, checkouts in done.items():
        run_hook(main, checkouts, log)
    return 0


def selftest() -> int:
    fails = []
    def check(name: str, ok: bool) -> None:
        print(("PASS " if ok else "FAIL ") + name)
        if not ok: fails.append(name)
    with tempfile.TemporaryDirectory() as t:
        repo = os.path.join(t, "demo")
        os.makedirs(repo)
        g = lambda *a, cwd=repo: subprocess.run(["git", *a], cwd=cwd, capture_output=True, check=True)
        g("init", "-q"); g("-c", "user.email=a@b", "-c", "user.name=a", "commit", "-q", "--allow-empty", "-m", "x")
        for wt in ("W-1", "W-1-P2", "W-2", "W-3", "plan-x"):
            g("worktree", "add", "-q", "-b", wt, os.path.join(t, f"demo-{wt}"))
        old = time.time() - 3 * 3600
        for wt in ("demo", "demo-W-1", "demo-W-1-P2", "demo-W-2", "demo-W-3", "demo-plan-x"):
            d = os.path.join(t, wt, "target", "debug", "deps")
            os.makedirs(d)
            open(os.path.join(d, "lib.rlib"), "w").write("x")
            for p in (d, os.path.dirname(d), os.path.join(t, wt, "target")):
                os.utime(p, (old, old))
        fresh = os.path.join(t, "demo-plan-x", "target", "debug", "deps")
        os.utime(fresh, None)
        rd = os.path.join(t, "runs"); os.makedirs(rd)
        json.dump({"pid": os.getpid(), "root": repo, "state": "running"}, open(os.path.join(rd, "demo.W-1.runner.json"), "w"))
        json.dump({"pid": 999999, "root": repo, "state": "running"}, open(os.path.join(rd, "demo.W-2.runner.json"), "w"))
        json.dump({"pid": os.getpid(), "root": repo, "state": "queued"}, open(os.path.join(rd, "demo.W-3.runner.json"), "w"))
        os.makedirs(os.path.join(repo, ".agentic"))
        mark = os.path.join(t, "hook.txt")
        open(os.path.join(repo, ".agentic", "reclaim.sh"), "w").write(f'printf "%s\\n" "$@" > {mark}\n')
        os.environ.update(ASD_RUNS_DIR=rd, ASD_DISK_IDLE_MIN="60")
        rows = {os.path.basename(r["checkout"]): r for r in candidates()}
        check("the own worktree of a live run is kept", rows["demo-W-1"]["rank"] is None)
        check("a live run's idle fan-out worktree may go", rows["demo-W-1-P2"]["rank"] == 2)
        check("a dead run's worktree may go first", rows["demo-W-2"]["rank"] == 1)
        check("a queued run's idle own worktree may go, after the fan-outs", rows["demo-W-3"]["rank"] == 2.5)
        check("a build written minutes ago is kept", rows["demo-plan-x"]["rank"] is None)
        check("the main checkout goes last", rows["demo"]["rank"] == 3)
        os.environ["ASD_DISK_RECLAIM_GIB"] = "1e9"   # never reached: everything reclaimable goes
        reclaim(log=lambda _m: None)
        gone = lambda wt: not os.path.isdir(os.path.join(t, wt, "target"))
        check("reclaim removes what may go", gone("demo-W-2") and gone("demo-W-1-P2") and gone("demo-W-3") and gone("demo"))
        check("reclaim keeps what may not", not gone("demo-W-1") and not gone("demo-plan-x"))
        check("sources and worktrees stay", os.path.isdir(os.path.join(t, "demo-W-2", ".git")) or os.path.isfile(os.path.join(t, "demo-W-2", ".git")))
        hooked = open(mark).read().split() if os.path.exists(mark) else []
        check("the repo's reclaim hook gets the checkouts", sorted(os.path.basename(h) for h in hooked) == ["demo", "demo-W-1-P2", "demo-W-2", "demo-W-3"])
        os.environ["ASD_DISK_RECLAIM_GIB"] = "0"
        os.makedirs(os.path.join(t, "demo-W-2", "target"))
        reclaim(log=lambda _m: None)
        check("reclaim stops once the target is free", not gone("demo-W-2"))
        old = time.time() - 7200
        for p in (os.path.join(t, "demo-W-2", "target"),):
            os.utime(p, (old, old))
        tg = {os.path.basename(m): sorted(os.path.basename(c) for c in cs) for m, cs in sweep_targets().items()}
        check("sweep names the finished run's idle worktree, not live ones, the main, or a non-dossier one", tg == {"demo": ["demo-W-2"]})
        os.utime(os.path.join(t, "demo-W-2", "target"), None)
        check("sweep skips a finished run's worktree that is building again", all(cs == [] for cs in sweep_targets().values()))
        os.remove(mark)
        sweep(log=lambda _m: None)
        check("sweep runs the repo's hook even with no checkout (orphans and trim)", os.path.exists(mark))
        st = status()
        check("status reports free space and thresholds", st["freeGiB"] > 0 and st["floorGiB"] == 30 and "reclaimable" in st)
    fails += selftest_engines()
    print("FAIL" if fails else "PASS (all cases)")
    return 1 if fails else 0


def selftest_engines() -> list[str]:
    """Stub docker, colima and limactl in a temp directory first on PATH: the real ones and ~/.agentic are never used."""
    fails: list[str] = []
    def check(name: str, ok: bool) -> None:
        print(("PASS " if ok else "FAIL ") + name)
        if not ok: fails.append(name)
    keep = {k: os.environ.get(k) for k in ("PATH", "ASD_DISK_DOCKER_ENGINES", "ASD_ACTIVE_REPOS_FILE", "ASD_RUNS_DIR", "STUB_LOG", "STUB_DIR")}
    try:
        with tempfile.TemporaryDirectory() as t:
            bindir, state = os.path.join(t, "bin"), os.path.join(t, "state")
            os.makedirs(bindir); os.makedirs(state)
            log = os.path.join(t, "calls.log")
            # df output: total and available 1K blocks come from state/total and state/avail; a prune frees 5 GiB
            df = ('echo "Filesystem 1024-blocks Used Available Capacity Mounted"; '
                  'echo "/dev/vda 31457280 0 $(cat "$STUB_DIR/avail") 50% /var/lib/docker"')
            open(os.path.join(bindir, "colima"), "w").write(f'#!/bin/sh\necho "colima $*" >> "$STUB_LOG"\n[ -f "$STUB_DIR/down" ] && exit 1\n'
                                                            f'case "$1" in ssh) {df};; *) exit 0;; esac\n')
            open(os.path.join(bindir, "limactl"), "w").write(f'#!/bin/sh\necho "limactl $*" >> "$STUB_LOG"\n{df}\n')
            open(os.path.join(bindir, "docker"), "w").write(
                '#!/bin/sh\necho "docker $*" >> "$STUB_LOG"\n'
                'case "$*" in *"image prune"*) echo $(( $(cat "$STUB_DIR/avail") + $(cat "$STUB_DIR/image_gain") )) > "$STUB_DIR/avail"; echo "Total reclaimed space: 5GB";;\n'
                '*"builder prune"*) echo $(( $(cat "$STUB_DIR/avail") + $(cat "$STUB_DIR/cache_gain") )) > "$STUB_DIR/avail"; echo "Total:  2GB";; esac\n')
            for n in ("colima", "limactl", "docker"):
                os.chmod(os.path.join(bindir, n), 0o755)
            def put(avail_gib: float, image_gain_gib: float = 0, cache_gain_gib: float = 0):
                for n, v in (("avail", avail_gib), ("image_gain", image_gain_gib), ("cache_gain", cache_gain_gib)):
                    open(os.path.join(state, n), "w").write(str(int(v * 2**20)))
            calls = lambda: open(log).read().splitlines() if os.path.exists(log) else []
            os.environ.update(PATH=bindir + os.pathsep + os.environ.get("PATH", ""), STUB_LOG=log, STUB_DIR=state,
                              ASD_ACTIVE_REPOS_FILE=os.path.join(t, "none.json"), ASD_RUNS_DIR=os.path.join(t, "runs"))
            os.environ.pop("ASD_DISK_DOCKER_ENGINES", None)
            pe = os.path.join(t, "pe"); os.makedirs(os.path.join(pe, "scripts"))
            open(os.path.join(pe, "scripts", "docker-runtime.sh"), "w").write(
                'PROFILE="podcast-engine"\ncolima status --profile "$PROFILE" --json\ncolima start --profile "$PROFILE" --disk 30\n')
            check("detects a colima profile from a repo's scripts",
                  [(e["kind"], e["name"], e["context"]) for e in repo_engines(pe)] == [("colima", "podcast-engine", "colima-podcast-engine")])
            nw = os.path.join(t, "nw"); os.makedirs(os.path.join(nw, ".agentic"))
            open(os.path.join(nw, ".agentic", "reclaim.sh"), "w").write('limactl shell nightwatch -- fstrim -av\n')
            check("detects a lima instance from a repo's scripts", [(e["kind"], e["context"]) for e in repo_engines(nw)] == [("lima", "lima-nightwatch")])
            os.makedirs(os.path.join(pe, ".agentic"), exist_ok=True)
            json.dump({"engines": [{"kind": "colima", "name": "other", "context": "ctx-x"}, {"kind": "bad", "name": "x"}]},
                      open(os.path.join(pe, ".agentic", "docker-engines.json"), "w"))
            check("the config file wins over detection, bad entries are dropped",
                  [(e["name"], e["context"]) for e in repo_engines(pe)] == [("other", "ctx-x")])
            os.environ["ASD_DISK_DOCKER_ENGINES"] = "colima:podcast-engine,lima:nightwatch:my-ctx,bogus:x"
            check("the env override wins over the config", [(e["name"], e["context"]) for e in repo_engines(pe)] ==
                  [("podcast-engine", "colima-podcast-engine"), ("nightwatch", "my-ctx")])
            os.environ["ASD_DISK_DOCKER_ENGINES"] = "none"
            check("the env value none turns it off", repo_engines(pe) == [] and engines_status([pe]) == [])
            os.environ["ASD_DISK_DOCKER_ENGINES"] = "colima:podcast-engine"
            put(2)   # 2 of 30 GiB free: below 15 % and below 4 GiB
            e = engines_status([pe])[0]
            check("measures the VM disk: free GiB and fraction", e["reachable"] and e["freeGiB"] == 2.0 and e["totalGiB"] == 30.0 and e["low"])
            put(8)   # 27 %, 8 GiB
            check("above both floors is not low", not engines_status([pe])[0]["low"])
            put(4.2)  # 14 %: below the percent floor only
            check("below the percent floor alone is low", engines_status([pe])[0]["low"])
            put(4.6)  # 15.3 %, 4.6 GiB: above both
            check("just above the floor is not low", not engines_status([pe])[0]["low"])
            os.environ["ASD_DISK_VM_FLOOR_GIB"] = "6"
            check("the GiB floor is env-tunable", engines_status([pe])[0]["low"])
            os.environ.pop("ASD_DISK_VM_FLOOR_GIB")
            open(log, "w").close()
            put(2, image_gain_gib=10)
            msgs: list[str] = []
            reclaim_engine(engines_status([pe])[0], msgs.append)
            dc = [c for c in calls() if c.startswith("docker")]
            check("reclaim prunes dangling images only, in the engine's context, young layers kept",
                  dc == ["docker --context colima-podcast-engine image prune -f --filter until=1h"])
            check("reclaim stops once the disk is above the floor", not any("builder" in c for c in dc))
            check("reclaim logs what was freed", any("Total reclaimed space: 5GB" in m for m in msgs) and any("+10.0 GiB" in m for m in msgs))
            open(log, "w").close()
            put(2, image_gain_gib=1, cache_gain_gib=10)
            reclaim_engine(engines_status([pe])[0], lambda _m: None)
            dc = [c for c in calls() if c.startswith("docker")]
            check("still low after the images: the build cache goes next, in that order",
                  [c.split()[3] for c in dc] == ["image", "builder"] and "builder prune -f --filter until=1h" in dc[1])
            check("never -a, a volume, a container or a system prune",
                  not any(w in c for c in calls() for w in (" -a", "--all", "volume", "container", "system", "rmi", " rm ")))
            check("never starts, stops, deletes or resizes a VM",
                  not any(re.search(r"\b(start|stop|delete|resize)\b", c) for c in calls() if c.startswith(("colima", "limactl"))))
            put(2, image_gain_gib=0, cache_gain_gib=0)
            check("still low after both prunes is reported low", engines_status([pe])[0]["low"])
            open(os.path.join(state, "down"), "w").close()
            open(log, "w").close()
            e = engines_status([pe])[0]
            reclaim_engine(e, lambda _m: None)
            check("a VM that does not answer is not low and is left alone",
                  not e["reachable"] and not e["low"] and not any(c.startswith("docker") for c in calls()))
            os.remove(os.path.join(state, "down"))
            os.environ["ASD_DISK_DOCKER_ENGINES"] = "lima:nightwatch"
            put(1)
            check("a lima engine is read through limactl shell", engines_status([nw])[0]["low"] and any(c.startswith("limactl shell nightwatch") for c in calls()))
            os.environ["ASD_DISK_VM_GUARD"] = "0"
            check("ASD_DISK_VM_GUARD=0 turns the guard off", engines_status([nw]) == [])
            os.environ.pop("ASD_DISK_VM_GUARD")
            os.environ["ASD_DISK_DOCKER_ENGINES"] = "colima:podcast-engine"
            os.environ["ASD_DISK_RECLAIM_GIB"] = "0"
            put(2, image_gain_gib=10)
            open(log, "w").close()
            active = os.path.join(t, "active.json")
            json.dump({"repos": {"pe": pe}}, open(active, "w"))
            os.environ["ASD_ACTIVE_REPOS_FILE"] = active
            st = status()
            check("status lists the active repos' engines", [x["name"] for x in st["engines"]] == ["podcast-engine"] and st["engines"][0]["low"])
            reclaim(log=lambda _m: None)
            check("reclaim also prunes a low engine", any("image prune" in c for c in calls()))
    finally:
        for k, v in keep.items():
            if v is None: os.environ.pop(k, None)
            else: os.environ[k] = v
        os.environ.pop("ASD_DISK_RECLAIM_GIB", None)
    return fails


def main() -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    ap.add_argument("cmd", nargs="?", choices=["status", "reclaim", "sweep", "engines"], default="status")
    ap.add_argument("--root", help="engines: only this repository's engines")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if a.cmd == "sweep":
        return sweep()
    if a.cmd == "engines":
        print(json.dumps(engines_status([os.path.realpath(a.root)] if a.root else None)))
        return 0
    if a.cmd == "reclaim":
        return reclaim(a.dry_run)
    st = status()
    if a.json:
        print(json.dumps(st))
        return 0
    print(f"{st['freeGiB']} GiB free (floor {st['floorGiB']:.0f}, reclaim target {st['targetGiB']:.0f}){' — LOW' if st['low'] else ''}")
    for e in st["engines"]:
        print(f"  engine {e['name']} ({e['kind']}, context {e['context']}): " + (
            f"{e['freeGiB']} GiB of {e['totalGiB']} free ({e['freeFrac'] * 100:.0f}%){' — LOW' if e['low'] else ''}" if e["reachable"] else "not reachable"))
    for r in st["reclaimable"]:
        print(f"  may go  {r['dir']}  ({r['why']}, idle {r['idleMin']} min)")
    for r in st["kept"]:
        print(f"  kept    {r['dir']}  ({r['why']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
