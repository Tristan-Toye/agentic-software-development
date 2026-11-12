#!/usr/bin/env python3
"""Standing repair of the shared environment a dossier run depends on: the Lima VM and its docker compose stack.

Opt-in: nothing here runs unless ASD_STACK_REPAIR=1, because a repair restarts resources that other runs share.

Detection is read-only and cheap:
  vm     `limactl list --json`                          the VM's status must be Running
  stack  `limactl shell <vm> -- ... docker compose ps`  every service running, none unhealthy
  disk   free space of the repo's disk against the floor (detected, never repaired: the owner decides what to delete)

Repair uses only the commands the repo's config names (argv lists, never a shell string built here):
  vm     config "vm_start"    (limactl start {vm})
  stack  config "stack_up"    (the repo's documented up.sh, which takes the repo's own stack lease itself)

Config, first found wins: $ASD_STACK_REPAIR_CONFIG, <root>/.agentic/stack-repair.json, then the plugin's
config/stack-repair.json under "repos"/<repo directory name>. Keys: vm, compose_dir, vm_start, stack_up,
stack_ps (optional override). Placeholders in argv items: {vm} {root} {compose_dir}.

One repair at a time across runners: an flock on <dir>/stack-repair.lock, where waiting runners re-detect once they
get it (the holder has usually fixed it). Bound: at most MAX_PER_HOUR repair attempts per resource per hour, kept in
<dir>/stack-repair-state.json; past that the caller asks the owner as before. <dir> is $ASD_STACK_REPAIR_DIR or ~/.agentic.

    stack_repair.py --root R [--check]      print the findings (read-only)
Python 3 standard library only.
"""

from __future__ import annotations

import fcntl
import json
import os
import subprocess
import sys
import time

MAX_PER_HOUR = 2
HOUR = 3600.0
HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_PS = ["limactl", "shell", "{vm}", "--", "bash", "-lc", "cd {root}/{compose_dir} && docker compose ps --format json"]


def enabled() -> bool:
    return os.environ.get("ASD_STACK_REPAIR", "").strip() == "1"


def state_dir() -> str:
    d = os.environ.get("ASD_STACK_REPAIR_DIR") or os.path.expanduser("~/.agentic")
    os.makedirs(d, exist_ok=True)
    return d


def load_config(root: str) -> dict | None:
    paths = []
    if os.environ.get("ASD_STACK_REPAIR_CONFIG"):
        paths.append((os.environ["ASD_STACK_REPAIR_CONFIG"], None))
    paths.append((os.path.join(root, ".agentic", "stack-repair.json"), None))
    paths.append((os.path.join(os.path.dirname(HERE), "config", "stack-repair.json"), os.path.basename(os.path.abspath(root))))
    for path, repo in paths:
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            continue
        if repo is not None:
            data = (data.get("repos") or {}).get(repo)
        if isinstance(data, dict) and data.get("vm"):
            return data
    return None


def _argv(template, cfg: dict, root: str) -> list[str]:
    sub = {"vm": cfg["vm"], "root": root, "compose_dir": cfg.get("compose_dir") or "."}
    return [str(a).format(**sub) for a in template]


def _run(argv: list[str], timeout: float) -> tuple[int, str]:
    try:
        p = subprocess.run(argv, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=timeout)
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except subprocess.TimeoutExpired:
        return 124, f"timed out after {int(timeout)} s"
    except OSError as e:
        return 127, str(e)


def _json_rows(text: str) -> list[dict]:
    text = text.strip()
    if not text:
        return []
    try:
        data = json.loads(text)
        return [r for r in (data if isinstance(data, list) else [data]) if isinstance(r, dict)]
    except ValueError:
        rows = []
        for ln in text.splitlines():
            try:
                r = json.loads(ln)
            except ValueError:
                continue
            if isinstance(r, dict):
                rows.append(r)
        return rows


def detect_vm(cfg: dict) -> str | None:
    """None when the VM is Running, else a short reason."""
    rc, out = _run(["limactl", "list", "--json"], 60)
    if rc != 0:
        return f"limactl list failed (exit {rc}): {out.strip()[:120]}"
    for r in _json_rows(out):
        if r.get("name") == cfg["vm"]:
            st = str(r.get("status") or "")
            return None if st == "Running" else f"VM {cfg['vm']} is {st or 'in an unknown state'}"
    return f"VM {cfg['vm']} is not in limactl list"


def detect_stack(cfg: dict, root: str) -> str | None:
    """None when every compose service runs and none is unhealthy, else a short reason."""
    rc, out = _run(_argv(cfg.get("stack_ps") or DEFAULT_PS, cfg, root), 120)
    if rc != 0:
        return f"docker compose ps failed (exit {rc}): {out.strip()[:120]}"
    rows = _json_rows(out)
    if not rows:
        return "docker compose ps lists no service"
    bad = []
    for r in rows:
        state, health = str(r.get("State") or ""), str(r.get("Health") or "")
        if state != "running" or health == "unhealthy":
            bad.append(f"{r.get('Service') or r.get('Name') or '?'} {health or state}")
    return "; ".join(bad) or None


def detect_disk(root: str, floor_gib: float) -> str | None:
    import shutil
    try:
        free = shutil.disk_usage(root).free / 2**30
    except OSError:
        return None
    return f"{free:.0f} GiB free, below the {floor_gib:.0f} GiB floor" if free < floor_gib else None


def detect(cfg: dict, root: str) -> dict[str, str]:
    """{resource: reason} for everything broken (vm, stack). The stack is only probed when the VM runs."""
    out = {}
    vm = detect_vm(cfg)
    if vm:
        out["vm"] = vm
    else:
        st = detect_stack(cfg, root)
        if st:
            out["stack"] = st
    return out


class _Lock:
    """Cross-process exclusive lock; waits (polling) up to `timeout` seconds."""

    def __init__(self, timeout: float):
        self.timeout = timeout
        self.f = None

    def __enter__(self):
        self.f = open(os.path.join(state_dir(), "stack-repair.lock"), "a+")
        end = time.time() + self.timeout
        while True:
            try:
                fcntl.flock(self.f, fcntl.LOCK_EX | fcntl.LOCK_NB)
                self.f.seek(0)
                self.f.truncate()
                self.f.write(f"{os.getpid()}\n")
                self.f.flush()
                return True
            except OSError:
                if time.time() >= end:
                    self.f.close()
                    self.f = None
                    return False
                time.sleep(min(1.0, max(0.05, end - time.time())))

    def __exit__(self, *a):
        if self.f:
            try:
                fcntl.flock(self.f, fcntl.LOCK_UN)
            finally:
                self.f.close()


def _attempts(resource: str, now: float) -> list[float]:
    try:
        with open(os.path.join(state_dir(), "stack-repair-state.json"), encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        data = {}
    return [t for t in data.get(resource, []) if isinstance(t, (int, float)) and now - t < HOUR]


def _record(resource: str, now: float) -> None:
    path = os.path.join(state_dir(), "stack-repair-state.json")
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        data = {}
    data = {k: [t for t in v if now - t < HOUR] for k, v in data.items() if isinstance(v, list)}
    data.setdefault(resource, []).append(now)
    tmp = path + f".{os.getpid()}"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f)
    os.replace(tmp, path)


def repair(cfg: dict, root: str, resources: dict[str, str], log, wait_s: float = 1800) -> dict[str, str]:
    """Try to repair each broken resource. Returns {resource: outcome} where outcome starts with
    'repaired', 'failed', 'bound' (attempts used up) or 'no-command'; the caller asks the owner for any but 'repaired'.
    Never called unless enabled(); `log(text)` records every action."""
    results: dict[str, str] = {}
    if not enabled():
        return {r: "off" for r in resources}
    with _Lock(wait_s) as got:
        if not got:
            return {r: "failed: the repair lock stayed busy" for r in resources}
        # Whoever held the lock before us has usually fixed it: look again before touching anything.
        still = detect(cfg, root)
        for res in ("vm", "stack"):
            if res not in resources:
                continue
            if res not in still and not (res == "stack" and "vm" in still):
                log(f"repair {res}: already healthy after waiting for the lock")
                results[res] = "repaired: healthy when the lock was taken"
                continue
            if res == "stack" and "vm" in still:
                results[res] = "failed: the VM is down"
                continue
            now = time.time()
            key = f"{res}:{cfg['vm']}"
            used = _attempts(key, now)
            if len(used) >= MAX_PER_HOUR:
                log(f"repair {res}: {len(used)} attempts in the last hour, not trying again")
                results[res] = f"bound: {len(used)} repairs in the last hour"
                continue
            template = cfg.get("vm_start") if res == "vm" else cfg.get("stack_up")
            if not template:
                results[res] = "no-command"
                continue
            argv = _argv(template, cfg, root)
            _record(key, now)
            log(f"repair {res} (attempt {len(used) + 1}/{MAX_PER_HOUR} this hour): {' '.join(argv)}")
            rc, out = _run(argv, 600 if res == "vm" else 1800)
            after = detect(cfg, root)
            if rc == 0 and res not in after:
                log(f"repair {res}: done")
                results[res] = "repaired"
                still = after
            else:
                tail = out.strip().splitlines()[-1][:160] if out.strip() else ""
                log(f"repair {res}: failed (exit {rc}) {tail}")
                results[res] = f"failed: exit {rc} {tail}".strip()
                if res == "vm":
                    break
    return results


def main(argv: list[str]) -> int:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True)
    ap.add_argument("--check", action="store_true", default=True)
    a = ap.parse_args(argv)
    cfg = load_config(a.root)
    if not cfg:
        print("no stack-repair config for this repo")
        return 0
    found = detect(cfg, a.root)
    print(json.dumps(found) if found else "ok")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
