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
  kept, and so is the own worktree of a live run (running, waiting, paused or queued with a live pid): that run is
  between builds, not done with them. A live run's sub-worktrees (its fan-out) go once idle.
- **In order, only as much as needed.** Worktrees of no live run first, then live runs' idle sub-worktrees, then the
  main checkout; it stops as soon as `ASD_DISK_RECLAIM_GIB` (default 80) GiB are free.
- **The repository's own part.** A repository that keeps build output elsewhere too (a VM's per-checkout target
  directories, a disk image that only shrinks after a trim) ships `.agentic/reclaim.sh`; after a reclaim it runs as
  `bash .agentic/reclaim.sh <checkout> …` in the main checkout, with every checkout whose build output went.

The runner refuses to start a stage below `ASD_DISK_FLOOR_GIB` (default 30) GiB free: it queues, starts a reclaim,
and goes on once the disk has room (run_dossier.py `take_slot`). The dashboard checks every 5 minutes, reclaims
below the reclaim threshold, and shows a banner below the floor (references/runner.md, "Disk space").

# Usage

    disk_guard.py status [--json]          free space, thresholds, and what a reclaim would remove
    disk_guard.py reclaim [--dry-run]      reclaim until the target is free (one reclaim at a time; a second exits 0)
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

LIVE = ("running", "waiting", "paused", "queued")
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


def status() -> dict:
    mains, _ = mains_and_live()
    probe = mains[0] if mains else os.path.expanduser("~")
    rows = candidates()
    return {"freeGiB": round(free_gib(probe), 1), "floorGiB": floor_gib(), "targetGiB": target_gib(),
            "low": free_gib(probe) < floor_gib(), "reclaimable": [r for r in rows if r["rank"] is not None],
            "kept": [r for r in rows if r["rank"] is None], "checked": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}


def reclaim(dry: bool = False, log=print) -> int:
    lock = open(os.path.join(runs_dir() if os.path.isdir(runs_dir()) else tempfile.gettempdir(), "disk-reclaim.lock"), "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        log("a reclaim is already running")
        return 0
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
        hook = os.path.join(main, ".agentic", "reclaim.sh")
        if os.path.isfile(hook):
            log(f"running {hook} for {len(checkouts)} checkout(s)")
            try:
                subprocess.run(["bash", hook, *checkouts], cwd=main, timeout=1800)
            except (OSError, subprocess.SubprocessError) as e:
                log(f"{hook} failed: {e}")
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
        for wt in ("W-1", "W-1-P2", "W-2", "plan-x"):
            g("worktree", "add", "-q", "-b", wt, os.path.join(t, f"demo-{wt}"))
        old = time.time() - 3 * 3600
        for wt in ("demo", "demo-W-1", "demo-W-1-P2", "demo-W-2", "demo-plan-x"):
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
        os.makedirs(os.path.join(repo, ".agentic"))
        mark = os.path.join(t, "hook.txt")
        open(os.path.join(repo, ".agentic", "reclaim.sh"), "w").write(f'printf "%s\\n" "$@" > {mark}\n')
        os.environ.update(ASD_RUNS_DIR=rd, ASD_DISK_IDLE_MIN="60")
        rows = {os.path.basename(r["checkout"]): r for r in candidates()}
        check("the own worktree of a live run is kept", rows["demo-W-1"]["rank"] is None)
        check("a live run's idle fan-out worktree may go", rows["demo-W-1-P2"]["rank"] == 2)
        check("a dead run's worktree may go first", rows["demo-W-2"]["rank"] == 1)
        check("a build written minutes ago is kept", rows["demo-plan-x"]["rank"] is None)
        check("the main checkout goes last", rows["demo"]["rank"] == 3)
        os.environ["ASD_DISK_RECLAIM_GIB"] = "1e9"   # never reached: everything reclaimable goes
        reclaim(log=lambda _m: None)
        gone = lambda wt: not os.path.isdir(os.path.join(t, wt, "target"))
        check("reclaim removes what may go", gone("demo-W-2") and gone("demo-W-1-P2") and gone("demo"))
        check("reclaim keeps what may not", not gone("demo-W-1") and not gone("demo-plan-x"))
        check("sources and worktrees stay", os.path.isdir(os.path.join(t, "demo-W-2", ".git")) or os.path.isfile(os.path.join(t, "demo-W-2", ".git")))
        hooked = open(mark).read().split() if os.path.exists(mark) else []
        check("the repo's reclaim hook gets the checkouts", sorted(os.path.basename(h) for h in hooked) == ["demo", "demo-W-1-P2", "demo-W-2"])
        os.environ["ASD_DISK_RECLAIM_GIB"] = "0"
        os.makedirs(os.path.join(t, "demo-W-2", "target"))
        reclaim(log=lambda _m: None)
        check("reclaim stops once the target is free", not gone("demo-W-2"))
        st = status()
        check("status reports free space and thresholds", st["freeGiB"] > 0 and st["floorGiB"] == 30 and "reclaimable" in st)
    print("FAIL" if fails else "PASS (all cases)")
    return 1 if fails else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    ap.add_argument("cmd", nargs="?", choices=["status", "reclaim"], default="status")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if a.cmd == "reclaim":
        return reclaim(a.dry_run)
    st = status()
    if a.json:
        print(json.dumps(st))
        return 0
    print(f"{st['freeGiB']} GiB free (floor {st['floorGiB']:.0f}, reclaim target {st['targetGiB']:.0f}){' — LOW' if st['low'] else ''}")
    for r in st["reclaimable"]:
        print(f"  may go  {r['dir']}  ({r['why']}, idle {r['idleMin']} min)")
    for r in st["kept"]:
        print(f"  kept    {r['dir']}  ({r['why']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
