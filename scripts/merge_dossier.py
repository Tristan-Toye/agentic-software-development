#!/usr/bin/env python3
"""Merge all of a dossier's work into its stub as one GitHub stack, then start the cleanup run.

The owner's Merge button on the dashboard runs this; no agent does the merge.

# Usage

    merge_dossier.py ID --root R [--dry-run] [--method merge|squash|rebase]
    merge_dossier.py --selftest

1. **The pull requests.** Every open pull request whose head branch names the dossier id, in any case (the build's
   `feat/w-128-…`, the stacked `fix/W-128-deferred`, …). A `plan/…` head is never one.
2. **One chain.** They must form one line, bottom to top: each one's base is the head of the one below, and the
   bottom one's base is an open stub (`stub/*` or `release/*`). A fork, a gap, a draft, or a bottom PR into
   anything else refuses, naming why. So does a live run of the dossier.
3. **The stack.** Two or more PRs are linked into one stack on GitHub (`gh stack link <bottom> … <top>`; an
   existing stack is updated, never shrunk). The stack is merged with `gh stack merge <top> --yes --merge`: an
   all-or-nothing merge of every member into the stub. A single PR is merged with `gh pr merge <n> --merge`.
4. **Check.** Every PR must read MERGED afterwards; else it reports the ones that did not.
5. **After.** It marks the PRs started in the merge detector's `<repo>.merges.json` (so the detector does not
   start a second cleanup), writes a `runner` line on the run, and starts `run_dossier.py ID --command after-merge
   --items <PRs>` in the background.

State: `<key>.merge.json` `{state: merging|merged|failed, prs, base, error, ts}`, read by the dashboard. Prints one
line. Exit 0 merged, 1 refused or failed, 2 bad usage. `--dry-run` resolves and checks, and changes nothing.
`ASD_GH_BIN` replaces `gh` (tests).
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import shlex
import subprocess
import sys
import contextlib
import io
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
RUNNER = os.environ.get("ASD_RUNNER_BIN") or os.path.join(HERE, "run_dossier.py")


def now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def runs_dir() -> str:
    return os.environ.get("ASD_RUNS_DIR") or os.path.expanduser("~/.agentic/runs")


def repo_name(root: str) -> str:
    try:
        common = subprocess.run(["git", "-C", root, "rev-parse", "--path-format=absolute", "--git-common-dir"],
                                capture_output=True, text=True, timeout=10).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        common = ""
    main = re.sub(r"/\.git/?$", "", common) if common else root
    return os.path.basename(main.rstrip("/"))


def gh(root: str, *args: str, timeout: int = 600) -> tuple[int, str]:
    cmd = shlex.split(os.environ.get("ASD_GH_BIN") or "gh") + list(args)
    try:
        p = subprocess.run(cmd, cwd=root, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return 124, "timed out"
    except OSError as e:
        return 127, str(e)
    return p.returncode, (p.stdout if p.returncode == 0 else (p.stdout + p.stderr)).strip()


def write_json(path: str, obj) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(obj, fh)
    os.replace(tmp, path)


def read_json(path: str, default):
    try:
        with open(path) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


def append_line(path: str, obj: dict) -> None:
    with open(path, "a") as fh:
        fh.write(json.dumps(obj) + "\n")


def alive(pid) -> bool:
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, TypeError, ValueError):
        return False


def is_stub(branch: str) -> bool:
    return bool(re.match(r"(stub|release)/", branch))


def chain_of(prs: list[dict], dossier: str) -> tuple[list[dict], str]:
    """The dossier's open PRs as one bottom-to-top chain, or ([], why not)."""
    pat = re.compile(r"(^|[^A-Za-z0-9])" + re.escape(dossier) + r"(?![0-9])", re.I)
    mine = [p for p in prs if pat.search(p.get("headRefName", "")) and not p.get("headRefName", "").startswith("plan/")]
    if not mine:
        return [], f"no open pull request of {dossier}"
    heads = {p["headRefName"]: p for p in mine}
    bottoms = [p for p in mine if p.get("baseRefName") not in heads]
    if len(bottoms) != 1:
        return [], f"{dossier}'s pull requests do not form one chain: {len(bottoms)} of them are based outside it (" + \
            ", ".join(f"#{p['number']} into {p.get('baseRefName')}" for p in bottoms) + ")"
    chain = [bottoms[0]]
    while True:
        up = [p for p in mine if p.get("baseRefName") == chain[-1]["headRefName"]]
        if len(up) > 1:
            return [], f"#{chain[-1]['number']} has {len(up)} pull requests on top of it; merge one chain at a time"
        if not up:
            break
        chain.append(up[0])
    if len(chain) != len(mine):
        rest = [p for p in mine if p not in chain]
        return [], "some of its pull requests are not in the chain: " + ", ".join(f"#{p['number']}" for p in rest)
    return chain, ""


def plan(root: str, dossier: str) -> tuple[list[dict], str]:
    """Resolve and check: (the chain, "") or ([], why it cannot merge)."""
    key = f"{repo_name(root)}.{dossier}"
    st = read_json(os.path.join(runs_dir(), key + ".runner.json"), {}) or {}
    if st.get("state") in ("running", "waiting", "paused", "queued") and alive(st.get("pid")):
        return [], f"a {st.get('command') or 'run'} run of {dossier} is still live; merge when it is finished"
    rc, out = gh(root, "pr", "list", "--state", "open", "--limit", "200", "--json", "number,headRefName,baseRefName,isDraft,title,url")
    if rc != 0:
        return [], "gh pr list failed: " + out[:200]
    try:
        prs = json.loads(out or "[]")
    except ValueError:
        return [], "gh pr list gave no JSON"
    chain, why = chain_of(prs, dossier)
    if not chain:
        return [], why
    base = chain[0].get("baseRefName", "")
    if not is_stub(base):
        return [], f"#{chain[0]['number']} goes into {base}, not a stub; only work on a stub is merged here"
    # A dossier still in flight when its stub was integrated moves to the next stub, but only once its old stub is in
    # development: before that, the next stub lacks the old batch and the retargeted PR would carry it along.
    state = stub_into_dev(root, base)
    if state == "open":
        return [], f"its stub {base} is being integrated into development; it moves to the next stub once that merges"
    if state == "merged":
        nxt, why = accepting_stub(root, base)
        if not nxt:
            return [], why
        rc, out = gh(root, "pr", "edit", str(chain[0]["number"]), "--base", nxt)
        if rc != 0:
            return [], f"could not move #{chain[0]['number']} from {base} to {nxt}: {out[:200]}"
        append_line(os.path.join(runs_dir(), f"{repo_name(root)}.{dossier}.jsonl"), {"ts": now(), "kind": "runner", "text": f"moved #{chain[0]['number']} from {base} (integrated) to the accepting stub {nxt}"})
        chain[0]["baseRefName"] = nxt
    drafts = [p for p in chain if p.get("isDraft")]
    if drafts:
        return [], "still a draft: " + ", ".join(f"#{p['number']}" for p in drafts) + "; mark it ready for review first"
    return chain, ""


def stub_into_dev(root: str, stub: str) -> str:
    """The stub's pull request into development: "open", "merged", or "" (none: it still accepts dossiers)."""
    rc, out = gh(root, "pr", "list", "--head", stub, "--base", "development", "--state", "all", "--json", "state", "-q", "[.[].state] | join(\",\")")
    states = out.split(",") if rc == 0 and out else []
    return "open" if "OPEN" in states else "merged" if "MERGED" in states else ""


def accepting_stub(root: str, old: str) -> tuple[str, str]:
    """The stub new work goes to: the newest stub/* with no pull request into development, which must already hold the
    integrated `old` stub (it was cut, or synced, after `old` merged). None yet: cut stub/<today> from development."""
    subprocess.run(["git", "-C", root, "fetch", "-q", "origin", "--prune"], capture_output=True, timeout=180)
    out = subprocess.run(["git", "-C", root, "for-each-ref", "--format=%(refname:short)", "refs/remotes/origin/stub/"],
                         capture_output=True, text=True, timeout=30).stdout.split()
    stubs = sorted((b.removeprefix("origin/") for b in out), reverse=True)
    for b in stubs:
        if b == old or stub_into_dev(root, b):
            continue
        held = subprocess.run(["git", "-C", root, "merge-base", "--is-ancestor", f"origin/{old}", f"origin/{b}"], capture_output=True).returncode == 0
        if not held:
            return "", f"the accepting stub {b} does not hold {old} yet; it takes development first (/stub Step 1), then the dossier moves"
        return b, ""
    import datetime as _d
    new = "stub/" + _d.date.today().isoformat()
    r = subprocess.run(["git", "-C", root, "push", "origin", f"origin/development:refs/heads/{new}"], capture_output=True, text=True, timeout=120)
    if r.returncode != 0 and "already exists" not in r.stderr:
        return "", f"could not cut {new}: {r.stderr.strip()[:200]}"
    return new, ""


def mark_detector(repo: str, chain: list[dict], dossier: str) -> None:
    """Record the PRs as started in the merge detector's file, so it does not start a second cleanup run."""
    path = os.path.join(runs_dir(), f"{repo}.merges.json")
    seen = read_json(path, {}) or {}
    for p in chain:
        seen[str(p["number"])] = {"id": dossier, "base": chain[0].get("baseRefName", ""), "mergedAt": now(), "state": "started", "at": now()}
    write_json(path, seen)


def merge(root: str, dossier: str, method: str, dry: bool, no_cleanup: bool = False) -> int:
    repo = repo_name(root)
    key = f"{repo}.{dossier}"
    d = runs_dir()
    os.makedirs(d, exist_ok=True)
    state_path = os.path.join(d, key + ".merge.json")
    chain, why = plan(root, dossier)
    nums = [p["number"] for p in chain]
    if not chain:
        if not dry:
            write_json(state_path, {"state": "failed", "prs": [], "base": "", "error": why, "ts": now()})
        print(f"merge_dossier: refused — {why}")
        return 1
    base = chain[0]["baseRefName"]
    label = ", ".join(f"#{n}" for n in nums)
    if dry:
        print(f"merge_dossier: would merge {label} into {base}" + (" as one stack" if len(nums) > 1 else ""))
        return 0
    write_json(state_path, {"state": "merging", "prs": nums, "base": base, "error": "", "ts": now(), "pid": os.getpid()})
    if len(nums) > 1:
        rc, out = gh(root, "stack", "link", *[str(n) for n in nums])
        if rc != 0:
            return fail(state_path, nums, base, f"gh stack link {' '.join(map(str, nums))} failed: {out[-300:]}")
        rc, out = gh(root, "stack", "merge", str(nums[-1]), "--yes", f"--{method}", timeout=1800)
    else:
        rc, out = gh(root, "pr", "merge", str(nums[0]), f"--{method}", timeout=600)
    if rc != 0:
        return fail(state_path, nums, base, out[-400:] or f"exit {rc}")
    open_left = []
    for n in nums:
        rc, st = gh(root, "pr", "view", str(n), "--json", "state", "-q", ".state")
        if rc != 0 or st.strip() != "MERGED":
            open_left.append(f"#{n} ({st.strip() or 'unknown'})")
    if open_left:
        return fail(state_path, nums, base, "not merged afterwards: " + ", ".join(open_left))
    write_json(state_path, {"state": "merged", "prs": nums, "base": base, "error": "", "ts": now()})
    append_line(os.path.join(d, key + ".jsonl"), {"ts": now(), "kind": "runner", "text": f"merged {label} into {base}" + (" as one stack" if len(nums) > 1 else "") + " (your Merge button)"})
    mark_detector(repo, chain, dossier)
    if no_cleanup:
        print(f"merge_dossier: merged {label} into {base}")
        return 0
    after = start_run(root, dossier, "after-merge", nums)
    print(f"merge_dossier: merged {label} into {base}; cleanup: {after[:200]}")
    return 0


def start_run(root: str, dossier: str, command: str, nums: list[int]) -> str:
    """Start the dossier's agent on the runner for `command` with the PR numbers; its first output line."""
    r = subprocess.run([sys.executable, RUNNER, dossier, "--root", root, "--command", command, "--items", ",".join(map(str, nums)), "--model", "sonnet"],
                       capture_output=True, text=True, timeout=60)
    return (r.stdout.strip().splitlines() or [r.stderr.strip()] or [""])[0] if (r.stdout or r.stderr) else ""


# ---------------------------------------------------------------- the merge queue
# Merge presses queue per repository, first in first out; one worker per repository works the queue: for each
# dossier it waits for the dossier's own run, resolves conflicts through the dossier's agent (`/resolve-merge`), merges
# by script, runs `/after-merge` to the end, then takes the next. Nothing merges while another dossier's cleanup runs.

def queue_path(repo: str) -> str:
    return os.path.join(runs_dir(), f"{repo}.merge-queue.json")


def locked(repo: str):
    import fcntl
    os.makedirs(runs_dir(), exist_ok=True)
    fh = open(os.path.join(runs_dir(), f"{repo}.merge-queue.lock"), "a+")
    fcntl.flock(fh, fcntl.LOCK_EX)
    return fh


def set_state(key: str, **kw) -> None:
    p = os.path.join(runs_dir(), key + ".merge.json")
    write_json(p, {**(read_json(p, {}) or {}), "ts": now(), **kw})


def enqueue(root: str, dossier: str) -> int:
    """Add the dossier to its repository's merge queue (once) and make sure a worker runs it."""
    repo = repo_name(root)
    with locked(repo):
        q = read_json(queue_path(repo), {}) or {}
        items = q.get("items") or []
        if not any(i["id"] == dossier for i in items):
            items.append({"id": dossier, "ts": now()})
        q["items"], q["root"] = items, root
        write_json(queue_path(repo), q)
        pos = [i["id"] for i in items].index(dossier) + 1
        set_state(f"{repo}.{dossier}", state="queued", position=pos, error="", prs=[], pid=None)
        worker = q.get("worker")
        if not (worker and alive(worker)):
            log = os.path.join(runs_dir(), f"{repo}.merge-queue.log")
            with open(log, "ab") as fh:
                p = subprocess.Popen([sys.executable, os.path.abspath(__file__), "--worker", "--root", root], stdin=subprocess.DEVNULL,
                                     stdout=fh, stderr=subprocess.STDOUT, start_new_session=True)
            q["worker"] = p.pid
            write_json(queue_path(repo), q)
    print(f"merge_dossier: {dossier} is number {pos} in the merge queue")
    return 0


def run_live(key: str) -> bool:
    st = read_json(os.path.join(runs_dir(), key + ".runner.json"), {}) or {}
    return st.get("state") in ("running", "waiting", "paused", "queued") and alive(st.get("pid"))


def wait_run(key: str, since: str, timeout_s: float) -> str:
    """Wait for the run started at `since` to end; its end state ("done", "failed", "stopped") or "timeout"."""
    poll = float(os.environ.get("ASD_MERGE_POLL") or 15)
    end = time.time() + timeout_s
    while time.time() < end:
        st = read_json(os.path.join(runs_dir(), key + ".runner.json"), {}) or {}
        if str(st.get("started") or "") >= since and not run_live(key):
            return str(st.get("state") or "stopped") if alive(st.get("pid")) or st.get("state") in ("done", "failed", "stopped") else "stopped"
        time.sleep(poll)
    return "timeout"


def conflicting(root: str, nums: list[int]) -> tuple[list[int], str]:
    """The PRs GitHub reports as conflicting with their base. GitHub computes this lazily: UNKNOWN is asked again."""
    tries = int(os.environ.get("ASD_MERGE_MERGEABLE_TRIES") or 12)
    bad = []
    for n in nums:
        for _ in range(tries):
            rc, out = gh(root, "pr", "view", str(n), "--json", "mergeable", "-q", ".mergeable")
            if rc != 0:
                return [], f"gh pr view {n} failed: {out[:160]}"
            if out.strip() != "UNKNOWN":
                break
            time.sleep(float(os.environ.get("ASD_MERGE_POLL") or 15) / 3)
        if out.strip() == "CONFLICTING":
            bad.append(n)
    return bad, ""


def work_one(root: str, dossier: str) -> None:
    repo = repo_name(root); key = f"{repo}.{dossier}"
    run_timeout = float(os.environ.get("ASD_MERGE_RUN_TIMEOUT") or 4 * 3600)
    if run_live(key):
        set_state(key, state="waiting", error="", pid=os.getpid(), note="waiting for the dossier's own run to finish")
        while run_live(key):
            time.sleep(float(os.environ.get("ASD_MERGE_POLL") or 15))
    for attempt in range(1, 3):
        chain, why = plan(root, dossier)
        if not chain:
            set_state(key, state="failed", error=why, pid=None); return
        nums = [p["number"] for p in chain]
        bad, err = conflicting(root, nums)
        if err:
            set_state(key, state="failed", error=err, pid=None); return
        if not bad:
            break
        if attempt == 2:
            set_state(key, state="failed", prs=nums, error="still conflicting after the agent's resolution: " + ", ".join(f"#{n}" for n in bad), pid=None); return
        since = now()
        set_state(key, state="resolving", prs=nums, error="", pid=os.getpid(), note="conflicts in " + ", ".join(f"#{n}" for n in bad) + "; the dossier's agent resolves them")
        append_line(os.path.join(runs_dir(), key + ".jsonl"), {"ts": now(), "kind": "runner", "text": "merge queue: " + ", ".join(f"#{n}" for n in bad) + " conflict with their base; starting /resolve-merge"})
        start_run(root, dossier, "resolve-merge", nums)
        end = wait_run(key, since, run_timeout)
        if end != "done":
            set_state(key, state="failed", prs=nums, error=f"the conflict resolution run ended {end}; the queue went on without this dossier", pid=None); return
    set_state(key, state="merging", pid=os.getpid())
    import io, contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = merge(root, dossier, "merge", False, no_cleanup=True)
    if rc != 0:
        return  # merge() recorded "failed" and why
    st = read_json(os.path.join(runs_dir(), key + ".merge.json"), {}) or {}
    nums = st.get("prs") or []
    since = now()
    set_state(key, state="cleanup", pid=os.getpid(), note="merged; /after-merge runs")
    start_run(root, dossier, "after-merge", nums)
    end = wait_run(key, since, run_timeout)
    set_state(key, state="merged", pid=None, note=f"merged; cleanup {end}")


def worker(root: str) -> int:
    """Work the repository's merge queue to the end, one dossier at a time."""
    repo = repo_name(root)
    with locked(repo):
        q = read_json(queue_path(repo), {}) or {}
        if q.get("worker") and q["worker"] != os.getpid() and alive(q["worker"]):
            return 0
        q["worker"] = os.getpid(); write_json(queue_path(repo), q)
    while True:
        with locked(repo):
            q = read_json(queue_path(repo), {}) or {}
            items = q.get("items") or []
            for i, it in enumerate(items):   # the rest learn their place
                set_state(f"{repo}.{it['id']}", position=i + 1) if i else None
            if not items:
                q["worker"] = None; write_json(queue_path(repo), q)
                return 0
            head = items[0]["id"]
        try:
            work_one(q.get("root") or root, head)
        except Exception as e:  # one dossier's failure never stops the queue
            set_state(f"{repo}.{head}", state="failed", error=f"{type(e).__name__}: {e}", pid=None)
        with locked(repo):
            q = read_json(queue_path(repo), {}) or {}
            q["items"] = [i for i in (q.get("items") or []) if i["id"] != head]
            write_json(queue_path(repo), q)


def unqueue(root: str, dossier: str) -> int:
    """Take a dossier out of the queue, unless it is the one being worked on."""
    repo = repo_name(root)
    with locked(repo):
        q = read_json(queue_path(repo), {}) or {}
        items = q.get("items") or []
        if items and items[0]["id"] == dossier and q.get("worker") and alive(q["worker"]):
            print(f"merge_dossier: {dossier} is being merged now; it cannot leave the queue"); return 1
        q["items"] = [i for i in items if i["id"] != dossier]
        write_json(queue_path(repo), q)
        set_state(f"{repo}.{dossier}", state="cancelled", pid=None, error="")
    print(f"merge_dossier: {dossier} left the merge queue")
    return 0


# ---------------------------------------------------------------- integrate a stub
# Part 1: every finished dossier on the stub goes through the merge queue, first in first out (PR number order).
# Part 2: the /stub run on the runner (key <repo>.<stub slug>) proves the batch and opens the stub's PR. Dossiers still
# in flight (a draft PR, or a live run) stay out: their stub closes, and the merge queue moves them to the next stub
# once this one is in development.

def integrate(root: str, branch: str) -> int:
    repo = repo_name(root); slug = branch.replace("/", "-"); key = f"{repo}.{slug}"
    log = os.path.join(runs_dir(), key + ".jsonl")
    st_path = os.path.join(runs_dir(), key + ".integrate.json")
    def state(**kw):
        write_json(st_path, {**(read_json(st_path, {}) or {}), "ts": now(), "pid": os.getpid(), **kw})
    def line(text, kind="runner"):
        append_line(log, {"ts": now(), "kind": kind, "text": text})
    if stub_into_dev(root, branch):
        start_stub(root, branch, slug)   # already integrating: resume the /stub run
        state(state="stub", note="the stub's PR into development is open; /stub resumed")
        return 0
    rc, out = gh(root, "pr", "list", "--base", branch, "--state", "open", "--limit", "100", "--json", "number,headRefName,isDraft")
    if rc != 0:
        state(state="failed", error="gh pr list failed: " + out[:200]); return 1
    prs = sorted(json.loads(out or "[]"), key=lambda p: p["number"])
    ids = []
    for p in prs:
        m = re.search(r"([A-Za-z][A-Za-z0-9]*-\d+)", p["headRefName"])
        if m and not p["headRefName"].startswith("plan/"):
            i = m.group(1).upper()
            if i not in [x[0] for x in ids]:
                ids.append((i, p))
    finished = [i for i, p in ids if not p.get("isDraft") and not run_live(f"{repo}.{i}")]
    inflight = [i for i, p in ids if i not in finished]
    state(state="merging", finished=finished, inflight=inflight, error="")
    line(f"start /merge-queue {branch}")
    line(("Integrating " + branch + ". Merging the finished dossiers first, in order: " + (", ".join(finished) or "none") + "."
          + (" Still in flight, so they move to the next stub once this one is in development: " + ", ".join(inflight) + "." if inflight else "")), "update")
    for i in finished:
        with contextlib.redirect_stdout(io.StringIO()):
            enqueue(root, i)
    poll = float(os.environ.get("ASD_MERGE_POLL") or 15)
    end = time.time() + float(os.environ.get("ASD_MERGE_RUN_TIMEOUT") or 8 * 3600)
    while time.time() < end:
        sts = {i: (read_json(os.path.join(runs_dir(), f"{repo}.{i}.merge.json"), {}) or {}) for i in finished}
        if all(v.get("state") in ("merged", "failed", "cancelled") for v in sts.values()):
            break
        time.sleep(poll)
    bad = {i: v for i, v in sts.items() if v.get("state") != "merged"}
    if bad:
        why = "; ".join(f"{i}: {v.get('state')} {v.get('error', '')[:120]}" for i, v in bad.items())
        line("The merge queue did not merge every finished dossier, so /stub did not start: " + why, "update")
        state(state="failed", error=why); return 1
    line("All finished dossiers are merged into " + branch + ". Starting /stub: the full stack run, the audit and the stub's PR into development.", "update")
    start_stub(root, branch, slug)
    state(state="stub", note="/stub runs")
    return 0


def start_stub(root: str, branch: str, slug: str) -> str:
    r = subprocess.run([sys.executable, RUNNER, slug, "--root", root, "--command", "stub", "--items", branch], capture_output=True, text=True, timeout=60)
    return (r.stdout or r.stderr).strip()[:200]


def fail(state_path: str, nums: list[int], base: str, why: str) -> int:
    write_json(state_path, {"state": "failed", "prs": nums, "base": base, "error": why, "ts": now()})
    print(f"merge_dossier: failed — {why}")
    return 1


# ---------------------------------------------------------------- selftest

FAKE_GH = r'''
import json, os, sys
d = os.environ["FAKE_GH_DIR"]
a = sys.argv[1:]
open(os.path.join(d, "calls.txt"), "a").write(" ".join(a) + "\n")
prs = json.load(open(os.path.join(d, "prs.json")))
if a[:2] == ["pr", "list"]:
    if "--head" in a and a[a.index("--head") + 1].startswith(("stub/", "release/")): print(""); sys.exit(0)
    sel = [p for p in prs if p.get("state", "OPEN") == "OPEN"]
    if "--base" in a: sel = [p for p in sel if p["baseRefName"] == a[a.index("--base") + 1]] if a[a.index("--base") + 1].startswith("stub/") else sel
    print(json.dumps(sel)); sys.exit(0)
if a[:2] == ["stack", "link"]:
    open(os.path.join(d, "linked.txt"), "w").write(" ".join(a[2:]))
    sys.exit(int(os.environ.get("FAKE_LINK_RC", "0")))
if a[:2] in (["stack", "merge"], ["pr", "merge"]):
    if os.environ.get("FAKE_MERGE_RC", "0") != "0": print("merge blocked by branch protection"); sys.exit(1)
    nums = open(os.path.join(d, "linked.txt")).read().split() if a[0] == "stack" else [a[2]]
    for p in prs:
        if str(p["number"]) in nums: p["state"] = "MERGED"
    json.dump(prs, open(os.path.join(d, "prs.json"), "w")); sys.exit(0)
if a[:2] == ["pr", "view"]:
    p = next(p for p in prs if str(p["number"]) == a[2])
    print(p.get("mergeable", "MERGEABLE") if "mergeable" in a else p.get("state", "OPEN")); sys.exit(0)
sys.exit(3)
'''


def selftest() -> int:
    fails: list[str] = []

    def check(name: str, ok: bool, why: str = "") -> None:
        print(("ok   " if ok else "FAIL ") + name + ("" if ok or not why else "  — " + why[:400]))
        if not ok:
            fails.append(name)

    P = lambda n, head, base, draft=False: {"number": n, "headRefName": head, "baseRefName": base, "isDraft": draft, "title": "t", "url": "u"}
    c, why = chain_of([P(427, "feat/W-128-x", "stub/2026-11-03"), P(429, "fix/W-128-deferred", "feat/W-128-x"), P(500, "feat/w-130-y", "stub/2026-11-03")], "W-128")
    check("chain: the build PR then the stacked one, bottom to top; another dossier's PR is not in it", [p["number"] for p in c] == [427, 429])
    c, why = chain_of([P(1, "feat/w-7-a", "stub/x"), P(2, "fix/W-7-b", "stub/x")], "W-7")
    check("chain: two PRs both on the stub are not one chain", not c and "one chain" in why)
    c, why = chain_of([P(1, "plan/W-7", "development")], "W-7")
    check("chain: a plan branch is never a dossier's PR", not c and "no open pull request" in why)
    check("chain: W-12 is not W-128", chain_of([P(1, "feat/W-128-x", "stub/x")], "W-12")[0] == [])

    with tempfile.TemporaryDirectory() as tmp:
        fdir, rdir, root = (os.path.join(tmp, x) for x in ("gh", "runs", "repo"))
        for x in (fdir, rdir, root):
            os.makedirs(x)
        subprocess.run(["git", "init", "-q", root], check=True)
        open(os.path.join(fdir, "gh.py"), "w").write(FAKE_GH)
        fake_runner = os.path.join(tmp, "runner.py")
        open(fake_runner, "w").write("import sys\nopen(sys.argv[0] + '.args', 'w').write(' '.join(sys.argv[1:]))\nprint('started')\n")
        env = dict(os.environ, ASD_GH_BIN=f"{sys.executable} {os.path.join(fdir, 'gh.py')}", FAKE_GH_DIR=fdir, ASD_RUNS_DIR=rdir)
        global RUNNER
        saved = RUNNER
        RUNNER = fake_runner

        def go(prs: list[dict], *extra: str, **envx: str) -> tuple[int, str, list[str]]:
            json.dump(prs, open(os.path.join(fdir, "prs.json"), "w"))
            if os.path.exists(os.path.join(fdir, "calls.txt")):
                os.remove(os.path.join(fdir, "calls.txt"))
            old = dict(os.environ)
            os.environ.update(env, **envx)
            try:
                import io, contextlib
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf):
                    rc = merge(root, "W-128", "merge", "--dry-run" in extra)
            finally:
                os.environ.clear(); os.environ.update(old)
            calls = open(os.path.join(fdir, "calls.txt")).read().splitlines() if os.path.exists(os.path.join(fdir, "calls.txt")) else []
            return rc, buf.getvalue(), calls

        stack = [P(427, "feat/W-128-x", "stub/2026-11-03"), P(429, "fix/W-128-deferred", "feat/W-128-x")]
        rc, out, calls = go(stack, "--dry-run")
        check("dry run: names the PRs and the stub, changes nothing", rc == 0 and "#427, #429 into stub/2026-11-03 as one stack" in out and not any(c.startswith(("stack", "pr merge")) for c in calls))
        rc, out, calls = go(stack)
        key = "repo.W-128"
        st = read_json(os.path.join(rdir, key + ".merge.json"), {})
        check("merge: links the stack bottom to top, then merges it up to the top PR with a merge commit",
              rc == 0 and "stack link 427 429" in calls and "stack merge 429 --yes --merge" in calls and st.get("state") == "merged")
        seen = read_json(os.path.join(rdir, "repo.merges.json"), {})
        check("merge: the detector will not start a second cleanup", seen.get("427", {}).get("state") == "started" and seen.get("429", {}).get("state") == "started")
        args = open(fake_runner + ".args").read()
        check("merge: the cleanup run starts with the merged PRs", "W-128" in args and "--command after-merge" in args and "--items 427,429" in args)
        check("merge: the run gets a line saying so", any("merged #427, #429 into stub/2026-11-03 as one stack" in json.loads(l)["text"] for l in open(os.path.join(rdir, key + ".jsonl"))))
        rc, out, calls = go([P(427, "feat/W-128-x", "stub/2026-11-03")])
        check("merge: a single PR merges with gh pr merge, no stack", rc == 0 and "pr merge 427 --merge" in calls and not any(c.startswith("stack") for c in calls))
        rc, out, calls = go(stack, FAKE_MERGE_RC="1")
        check("merge: a refused merge is reported, nothing claims merged", rc == 1 and read_json(os.path.join(rdir, key + ".merge.json"), {}).get("state") == "failed" and "branch protection" in out)
        rc, out, calls = go([P(427, "feat/W-128-x", "development")])
        check("refuse: a PR into anything but a stub", rc == 1 and "not a stub" in out)
        rc, out, calls = go([P(427, "feat/W-128-x", "stub/x", True)])
        check("refuse: a draft", rc == 1 and "draft" in out)
        write_json(os.path.join(rdir, key + ".runner.json"), {"pid": os.getpid(), "state": "running", "command": "fix-deferred"})
        rc, out, calls = go(stack)
        check("refuse: while a run of the dossier is live", rc == 1 and "still live" in out and not calls)

        # ---- the merge queue: FIFO; a conflict wakes the dossier's agent, then the script merges, then cleanup ----
        FAKE_RUN = r"""
import json, os, sys, datetime
a = sys.argv[1:]; d = os.environ["FAKE_GH_DIR"]; rdir = os.environ["ASD_RUNS_DIR"]
dossier, cmd = a[0], a[a.index("--command") + 1]
open(os.path.join(d, "runs.txt"), "a").write(f"{dossier} {cmd}\n")
if cmd == "resolve-merge":
    prs = json.load(open(os.path.join(d, "prs.json")))
    for p in prs: p["mergeable"] = "MERGEABLE"
    json.dump(prs, open(os.path.join(d, "prs.json"), "w"))
ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
json.dump({"state": "done", "pid": 2 ** 22 + 7, "started": ts, "command": cmd}, open(os.path.join(rdir, "repo." + dossier + ".runner.json"), "w"))
print("started")
"""
        fr = os.path.join(tmp, "fakerun.py"); open(fr, "w").write(FAKE_RUN)
        os.remove(os.path.join(rdir, key + ".runner.json"))
        prs2 = [dict(P(427, "feat/W-128-x", "stub/s"), mergeable="CONFLICTING"), P(429, "fix/W-128-deferred", "feat/W-128-x"), P(500, "feat/w-130-y", "stub/s")]
        json.dump(prs2, open(os.path.join(fdir, "prs.json"), "w"))
        for f in ("calls.txt", "runs.txt"):
            if os.path.exists(os.path.join(fdir, f)): os.remove(os.path.join(fdir, f))
        old = dict(os.environ)
        os.environ.update(env, ASD_RUNNER_BIN=fr, ASD_MERGE_POLL="0.2")
        try:
            import io, contextlib
            with contextlib.redirect_stdout(io.StringIO()):
                enqueue(root, "W-128"); enqueue(root, "W-130")
            qp = queue_path("repo")
            done = False
            for _ in range(300):
                q = read_json(qp, {}) or {}
                if not q.get("items") and not (q.get("worker") and alive(q["worker"])):
                    done = True; break
                time.sleep(0.1)
        finally:
            os.environ.clear(); os.environ.update(old)
        runs = open(os.path.join(fdir, "runs.txt")).read().split("\n") if os.path.exists(os.path.join(fdir, "runs.txt")) else []
        calls = open(os.path.join(fdir, "calls.txt")).read().split("\n")
        check("queue: worked to the end", done)
        check("queue: a conflict wakes the dossier's agent first, then its cleanup; FIFO order", runs[:3] == ["W-128 resolve-merge", "W-128 after-merge", "W-130 after-merge"], str(runs))
        m128 = [i for i, c in enumerate(calls) if c.startswith("stack merge 429")]
        m130 = [i for i, c in enumerate(calls) if c.startswith("pr merge 500")]
        check("queue: the script merges each, W-128 before W-130", bool(m128) and bool(m130) and m128[0] < m130[0], str(calls))
        # ---- integrate: the finished dossiers through the queue, the one in flight left, then /stub ----
        for f in ("runs.txt", "calls.txt"):
            if os.path.exists(os.path.join(fdir, f)): os.remove(os.path.join(fdir, f))
        for n in os.listdir(rdir):
            if n.endswith((".merge.json", ".runner.json")) or n.startswith("repo.merge-queue"): os.remove(os.path.join(rdir, n))
        prs3 = [P(427, "feat/W-128-x", "stub/s"), P(429, "fix/W-128-deferred", "feat/W-128-x"), P(500, "feat/w-130-y", "stub/s"), P(600, "feat/W-131-z", "stub/s", True)]
        json.dump(prs3, open(os.path.join(fdir, "prs.json"), "w"))
        old = dict(os.environ)
        os.environ.update(env, ASD_RUNNER_BIN=fr, ASD_MERGE_POLL="0.2")
        RUNNER = fr   # integrate starts /stub in this process; the queue's worker reads ASD_RUNNER_BIN
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                rc = integrate(root, "stub/s")
        finally:
            os.environ.clear(); os.environ.update(old)
        runs = open(os.path.join(fdir, "runs.txt")).read().split("\n")
        ist = read_json(os.path.join(rdir, "repo.stub-s.integrate.json"), {})
        tl = [json.loads(x)["text"] for x in open(os.path.join(rdir, "repo.stub-s.jsonl"))]
        check("integrate: finished dossiers merge first, FIFO, then /stub starts; the draft stays in flight",
              rc == 0 and runs[:3] == ["W-128 after-merge", "W-130 after-merge", "stub-s stub"] and ist.get("state") == "stub" and ist.get("inflight") == ["W-131"], str(runs) + str(ist))
        check("integrate: the stub's timeline gets its own merge-queue section and notes", tl[0] == "start /merge-queue stub/s" and any("W-131" in t for t in tl))
        check("queue: both read merged", read_json(os.path.join(rdir, "repo.W-128.merge.json"), {}).get("state") == "merged" and read_json(os.path.join(rdir, "repo.W-130.merge.json"), {}).get("state") == "merged")
        RUNNER = saved
    print("FAILED: " + ", ".join(fails) if fails else "all ok")
    return 1 if fails else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    ap.add_argument("id", nargs="?")
    ap.add_argument("--root")
    ap.add_argument("--method", choices=["merge", "squash", "rebase"], default="merge")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--enqueue", action="store_true", help="add to the repository's merge queue (FIFO) and start its worker")
    ap.add_argument("--unqueue", action="store_true", help="take out of the merge queue")
    ap.add_argument("--worker", action="store_true", help="work the repository's merge queue to the end")
    ap.add_argument("--integrate", metavar="STUB", help="merge every finished dossier on the stub through the queue, then start /stub")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args(argv)
    if a.selftest:
        return selftest()
    if a.worker and a.root:
        return worker(os.path.abspath(a.root))
    if a.integrate and a.root:
        if not re.fullmatch(r"(?:stub|release)/[\w.-]+", a.integrate):
            print("merge_dossier: --integrate takes a stub branch, e.g. stub/2026-11-03", file=sys.stderr); return 2
        return integrate(os.path.abspath(a.root), a.integrate)
    if not a.id or not a.root or not re.fullmatch(r"[A-Za-z][A-Za-z0-9]*-\d+", a.id):
        ap.print_usage(sys.stderr)
        return 2
    if a.enqueue:
        return enqueue(os.path.abspath(a.root), a.id)
    if a.unqueue:
        return unqueue(os.path.abspath(a.root), a.id)
    return merge(os.path.abspath(a.root), a.id, a.method, a.dry_run)


if __name__ == "__main__":
    sys.exit(main())
