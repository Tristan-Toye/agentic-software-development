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
import glob
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


# An outage or a GitHub 5xx is not a verdict on the PR: the queue waits it out, however long it lasts, and carries on
# where it was. Backs off to five minutes between tries. ASD_MERGE_NET_TRIES sets a limit; the default, 0, is none.
TRANSIENT_RE = re.compile(r"error connecting to|could not resolve host|temporary failure in name resolution|no such host|dial tcp|server misbehaving"
                          r"|connection (?:reset|refused|timed out)|operation timed out|i/o timeout|tls handshake|network is unreachable|unexpected eof"
                          r"|failed to connect|could not read from remote|http (?:502|503|504)|http 5\d\d:|the remote end hung up|timed out", re.I)
NET_WAITED = 0.0   # seconds this worker spent waiting out outages: not counted against any check's time limit


def retry_network(call) -> tuple[int, str]:
    global NET_WAITED
    tries = int(os.environ.get("ASD_MERGE_NET_TRIES") or 0)
    wait = float(os.environ.get("ASD_MERGE_NET_WAIT") or 30)
    i = 0
    while True:
        rc, out = call()
        i += 1
        if rc == 0 or not TRANSIENT_RE.search(out) or (tries and i >= tries):
            return rc, out
        pause = min(wait * 2 ** min(i - 1, 10), 300.0)
        time.sleep(pause)
        NET_WAITED += pause


def gh(root: str, *args: str, timeout: int = 600) -> tuple[int, str]:
    cmd = shlex.split(os.environ.get("ASD_GH_BIN") or "gh") + list(args)
    def call() -> tuple[int, str]:
        try:
            p = subprocess.run(cmd, cwd=root, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)
        except subprocess.TimeoutExpired:
            return 124, "timed out"
        except OSError as e:
            return 127, str(e)
        return p.returncode, (p.stdout if p.returncode == 0 else (p.stdout + p.stderr)).strip()
    return retry_network(call)


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
    if st.get("state") in ("running", "testing", "waiting", "paused", "queued") and alive(st.get("pid")):
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
    picked = (read_json(os.path.join(runs_dir(), f"{repo_name(root)}.{base.replace('/', '-')}.integrate.json"), {}) or {}).get("finished") or []
    if state == "open" and dossier not in picked:
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
    """The stub new work goes to, by the one rule every flow shares (scripts/stub_target.py): the accepting stub, or a
    new one cut from development only once no stub is being integrated. It must already hold the integrated `old`."""
    sys.path.insert(0, HERE)
    from stub_target import target
    try:
        rc, b = target(root, False)
    except RuntimeError as e:
        return "", str(e)
    if rc == 3:
        return "", f"{b.split()[-1]} is being integrated; the next stub is cut once it merges"
    subprocess.run(["git", "-C", root, "fetch", "-q", "origin", "--prune"], capture_output=True, timeout=180)
    held = subprocess.run(["git", "-C", root, "merge-base", "--is-ancestor", f"origin/{old}", f"origin/{b}"], capture_output=True).returncode == 0
    if not held:
        return "", f"the accepting stub {b} does not hold {old} yet; it takes development first (/stub Step 1), then the dossier moves"
    return b, ""


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
        # Without --base, linking moves the bottom PR to the repository's default branch.
        rc, out = gh(root, "stack", "link", "--base", base, *[str(n) for n in nums])
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
        waiting = any(i["id"] == dossier for i in items)
        if not waiting:
            items.append({"id": dossier, "ts": now()})
        q["items"], q["root"] = items, root
        write_json(queue_path(repo), q)
        pos = [i["id"] for i in items].index(dossier) + 1
        if not waiting:   # one already in the queue keeps its state: the worker may be busy with it
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
    return st.get("state") in ("running", "testing", "waiting", "paused", "queued") and alive(st.get("pid"))


QUEUE_RUNS = ("resolve-merge", "after-merge")


def still_building(key: str) -> bool:
    """A live run that is the dossier's own work. A run the merge queue started (`QUEUE_RUNS`) means the stack is already being merged."""
    st = read_json(os.path.join(runs_dir(), key + ".runner.json"), {}) or {}
    return run_live(key) and st.get("command") not in QUEUE_RUNS


def picked_stacks(ids: list, ready, before: list) -> list:
    """The stacks an integration merges: those ready now, and those an earlier start of the same integration picked.
    A restart never drops a stack, whatever it is doing at that moment."""
    return [i for i, p in ids if i in before or ready(i, p)]


def wait_run(key: str, since: str, timeout_s: float) -> str:
    """Wait for the run started at `since` to end; its end state ("done", "failed", "stopped") or "timeout"."""
    poll = float(os.environ.get("ASD_MERGE_POLL") or 15)
    end = time.time() + timeout_s
    while time.time() < end:
        st = read_json(os.path.join(runs_dir(), key + ".runner.json"), {}) or {}
        if st.get("state") in ("paused", "queued"):   # an outage, a usage limit or a run slot: not the run taking too long
            end += poll
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


def behind(root: str, nums: list[int]) -> list[int]:
    """The PRs a strict branch rule refuses until they hold the latest base. UNKNOWN (right after a merge) is asked again."""
    tries = int(os.environ.get("ASD_MERGE_MERGEABLE_TRIES") or 12)
    out = []
    for n in nums:
        st = ""
        for _ in range(tries):
            rc, st = gh(root, "pr", "view", str(n), "--json", "mergeStateStatus", "-q", ".mergeStateStatus")
            if rc != 0 or st.strip() != "UNKNOWN":
                break
            time.sleep(float(os.environ.get("ASD_MERGE_POLL") or 15) / 3)
        if rc == 0 and st.strip() == "BEHIND":
            out.append(n)
    return out


# ---------------------------------------------------------------- shared steps: what blocks a merge, fixed
# The same steps settle a dossier's stack (into its stub) and a non-code PR (into development), cheapest first:
#   1. GitHub's mergeability: UNKNOWN right after another merge, so it is asked again (conflicting()).
#   2. Id collisions, mechanical: a dossier id or an ADR/LRN number the PR adds that the base (or another open PR)
#      already has is renumbered on the PR's branch, in a scratch worktree, then pushed (no agent).
#   3. A branch behind its base: every merge puts the rest of the queue behind, and a strict branch rule refuses them,
#      so the base is merged into the branch (gh pr update-branch) and its checks run again.
#   4. Red checks: failed jobs are re-run once (flaky checks), then waited for.
#   5. What is left (a content conflict, a check still red): the dossier's own agent, a fresh /resolve-merge run under
#      the dossier's key (a live run of it gets a message instead), then the steps again, at most AGENT_ROUNDS times.

ID_FILE_RE = re.compile(r"^\.discovery/dossiers/([A-Z][A-Z0-9]*-\d+)-[^/]+\.md$")
ADR_FILE_RE = re.compile(r"^docs/adr/(\d{4})-[^/]+\.md$")


def git(root: str, *args: str, timeout: int = 300) -> tuple[int, str]:
    def call() -> tuple[int, str]:
        try:
            p = subprocess.run(["git", "-C", root, *args], capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            return 124, "timed out"
        return p.returncode, (p.stdout if p.returncode == 0 else p.stdout + p.stderr).strip()
    return retry_network(call) if args[0] in ("fetch", "push") else call()


def ids_on(root: str, ref: str) -> tuple[set[str], set[str]]:
    """(dossier ids, ADR numbers) in a tree."""
    _, d = git(root, "ls-tree", "--name-only", ref, ".discovery/dossiers/")
    _, a = git(root, "ls-tree", "--name-only", ref, "docs/adr/")
    return ({m.group(1) for l in d.splitlines() if (m := ID_FILE_RE.match(l.strip()))},
            {m.group(1) for l in a.splitlines() if (m := ADR_FILE_RE.match(l.strip()))})


def added_ids(root: str, base: str, head: str) -> tuple[list[tuple[str, str]], list[str]]:
    """What the branch adds over its base: [(dossier id, file)], [ADR number]."""
    _, out = git(root, "diff", "--name-only", "--diff-filter=A", f"origin/{base}...origin/{head}")
    dos = [(m.group(1), l) for l in out.splitlines() if (m := ID_FILE_RE.match(l))]
    adrs = [m.group(1) for l in out.splitlines() if (m := ADR_FILE_RE.match(l))]
    return dos, adrs


def collisions(root: str, base: str, head: str, others: list[str]) -> tuple[list[tuple[str, str]], bool]:
    """([(dossier id, file)] to renumber, whether an ADR number collides). A dossier id collides with the base or with
    an id another open PR's branch adds (the PR earlier in the queue keeps it)."""
    taken_d, taken_a = ids_on(root, f"origin/{base}")
    _, tree = git(root, "ls-tree", "--name-only", f"origin/{base}", ".discovery/dossiers/")
    on_base = set(tree.split("\n"))
    theirs: set[str] = set()
    for o in others:
        d, _ = added_ids(root, base, o)
        taken_d |= {i for i, _ in d}
        theirs |= {f for _, f in d}
    dos, adrs = added_ids(root, base, head)
    # The same file is the same dossier, not a collision: a branch cut after a plan landed carries that plan's dossier too.
    return [(i, f) for i, f in dos if i in taken_d and f not in on_base and f not in theirs], any(a in taken_a for a in adrs)


MIGRATION_RE = re.compile(r"^(.*/)?migrations/(\d+)[_-][^/]+\.sql$")


def migration_clashes(root: str, base: str, head: str) -> list[str]:
    """Migrations the head adds under a number another migration in the same folder already has, on the base or in the
    head. A fresh database cannot migrate then, and CI without a database cannot see it. Numbers the base already
    repeats on its own are not this head's."""
    git(root, "fetch", "-q", "origin", base, head)
    rc, added = git(root, "diff", "--no-renames", "--diff-filter=A", "--name-only", f"origin/{base}...origin/{head}")
    rc2, tree = git(root, "ls-tree", "-r", "--name-only", f"origin/{base}")
    if rc != 0 or rc2 != 0:
        return []   # the branch is not on origin: the merge's own checks have the last word
    def keyed(paths):
        return {p: (m.group(1) or "", m.group(2)) for p in paths if (m := MIGRATION_RE.match(p.strip()))}
    new, old = keyed(added.splitlines()), keyed(tree.splitlines())
    out = []
    for path, key in sorted(new.items()):
        others = sorted(p for p, k in {**old, **new}.items() if k == key and p != path)
        if others:
            out.append(f"{path} has the number of {others[0]}")
    return out


def next_free_id(root: str, prefix: str) -> str:
    """The next dossier id no branch on origin holds: base, stubs, plans and every open PR's head."""
    git(root, "fetch", "-q", "origin", "--prune")
    _, refs = git(root, "for-each-ref", "--format=%(refname:short)", "refs/remotes/origin/")
    nums = []
    for r in refs.split():
        d, _ = ids_on(root, r)
        nums += [int(i.split("-")[1]) for i in d if i.startswith(prefix + "-")]
    return f"{prefix}-{max(nums or [0]) + 1}"


def fix_ids(root: str, base: str, head: str, renumber: list[tuple[str, str]], adr: bool) -> str:
    """Renumber colliding ids on the branch, mechanically; "" or why it could not."""
    wt = os.path.join(tempfile.mkdtemp(prefix="asd-ids-"), "w")
    # The branch may have moved on GitHub (an update from its base): build on what is there, or the push is refused.
    rc, out = git(root, "fetch", "-q", "origin", head, base)
    if rc != 0:
        return "could not fetch " + head + ": " + out[:160]
    rc, out = git(root, "worktree", "add", "-q", "--detach", wt, f"origin/{head}")
    if rc != 0:
        return "could not check out " + head + ": " + out[:160]
    try:
        rc, out = git(wt, "merge", "--no-edit", "--no-ff", f"origin/{base}")
        if rc != 0:
            git(wt, "merge", "--abort")
            return "conflict"   # a content conflict: the agent's step
        notes = []
        for old, f in renumber:
            new = next_free_id(root, old.split("-")[0])
            nf = f.replace(f"/{old}-", f"/{new}-", 1)
            git(wt, "mv", f, nf)
            path = os.path.join(wt, nf)
            text = open(path, encoding="utf-8").read()
            open(path, "w", encoding="utf-8").write(re.sub(rf"(?<![A-Za-z0-9]){re.escape(old)}(?![0-9])", new, text))
            git(wt, "add", nf)
            notes.append(f"{old} -> {new}")
        if adr:
            vp = os.path.join(HERE, "validate_pipeline.py")
            r = subprocess.run([sys.executable, vp, "--finalize-ids", "--base", f"origin/{base}"], cwd=wt, capture_output=True, text=True)
            if r.returncode != 0:
                return "renumbering ADRs failed: " + (r.stdout + r.stderr).strip()[-200:]
            git(wt, "add", "-A")
            notes.append("ADR numbers finalized")
        rc, _ = git(wt, "diff", "--cached", "--quiet")
        if rc != 0:
            rc, out = git(wt, "commit", "-q", "-m", "renumber ids the base already holds: " + ", ".join(notes))
            if rc != 0:
                return "commit failed: " + out[-200:]
        rc, out = git(wt, "push", "-q", "origin", f"HEAD:{head}")
        return "" if rc == 0 else "push failed: " + out[-200:]
    finally:
        git(root, "worktree", "remove", "--force", wt)


def merge_base_in(root: str, head: str, base: str) -> str:
    """Merge the base into the branch and push; "" or why not. A conflict is left for the next round to find."""
    wt = os.path.join(tempfile.mkdtemp(prefix="asd-update-"), "w")
    rc, out = git(root, "fetch", "-q", "origin", head, base)
    if rc != 0:
        return "fetch failed: " + out[:160]
    rc, out = git(root, "worktree", "add", "-q", "--detach", wt, f"origin/{head}")
    if rc != 0:
        return "could not check out " + head + ": " + out[:160]
    try:
        rc, out = git(wt, "merge", "--no-edit", "--no-ff", f"origin/{base}")
        if rc != 0:
            git(wt, "merge", "--abort")
            return "conflict"
        rc, out = git(wt, "push", "-q", "origin", f"HEAD:{head}")
        return "" if rc == 0 else "push failed: " + out[-160:]
    finally:
        git(root, "worktree", "remove", "--force", wt)


def checks_state(root: str, n: int) -> str:
    """green | pending | red, from the PR's checks."""
    rc, out = gh(root, "pr", "checks", str(n), "--json", "bucket", "-q", "[.[].bucket] | join(\",\")")
    if rc not in (0, 1, 8):
        return "pending"
    b = out.split(",") if out else []
    return "red" if any(x in ("fail", "cancel") for x in b) else "pending" if "pending" in b else "green"


def rerun_failed(root: str, head: str) -> bool:
    """Re-run the failed jobs of the branch's newest runs, once; whether any was re-run."""
    rc, out = gh(root, "run", "list", "--branch", head, "--limit", "20", "--json", "databaseId,conclusion,headSha")
    try:
        runs = json.loads(out or "[]") if rc == 0 else []
    except ValueError:
        runs = []
    if not runs:
        return False
    tip = runs[0].get("headSha")
    did = False
    for r in runs:
        if r.get("headSha") == tip and r.get("conclusion") in ("failure", "cancelled", "timed_out"):
            did = gh(root, "run", "rerun", str(r["databaseId"]), "--failed")[0] == 0 or did
    return did


def wait_checks(root: str, nums: list[int], timeout_s: float) -> dict[int, str]:
    poll = float(os.environ.get("ASD_MERGE_POLL") or 15)
    end = time.time() + timeout_s
    waited = NET_WAITED
    while True:
        st = {n: checks_state(root, n) for n in nums}
        end += NET_WAITED - waited
        waited = NET_WAITED
        if "pending" not in st.values() or time.time() > end:
            return st
        time.sleep(poll)


AGENT_ATTEMPTS = 2   # /resolve-merge runs one PR may spend in one settle: once per PR head, two at most; then the owner is asked


def head_of(root: str, n: int) -> str:
    """The PR's current head commit ("" when unknown): a resolve run that pushed changed it."""
    rc, out = gh(root, "pr", "view", str(n), "--json", "headRefOid", "-q", ".headRefOid")
    return out.strip() if rc == 0 else ""


def ask_resolve_failed(root: str, dossier: str, why: str) -> None:
    """The one place the queue asks the owner about a conflict: /resolve-merge itself failed twice. Best effort."""
    ask = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ask_owner.py")
    try:
        subprocess.run([sys.executable, ask, "ask", "--root", root, "--dossier", dossier, "--question",
                        f"The merge queue could not bring the pull requests of {dossier} up to date: {why}. The dossier's agent tried twice. How should it go on?",
                        "--option", "A|Resolve by hand|You resolve the conflict on the branch; the queue merges it on the next press of Merge",
                        "--option", "B|Leave it|The pull requests stay open and conflicting", "--recommend", "A",
                        "--blocks", "the merge of " + dossier], capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        pass


def settle(root: str, key: str, agent_id: str, prs: list[dict], base: str, others: list[str], note, use_agent: bool = True) -> str:
    """Make the PRs mergeable and green, cheapest step first; "" when ready to merge, else why not. Without use_agent
    it stops before waking an agent: a batch that stays red goes back to one PR at a time, where the blame is clear."""
    nums = [p["number"] for p in prs]
    run_timeout = float(os.environ.get("ASD_MERGE_RUN_TIMEOUT") or 4 * 3600)
    check_timeout = float(os.environ.get("ASD_MERGE_CHECK_TIMEOUT") or 3600)
    attempts: dict[int, int] = {}   # per PR: resolve-merge runs started in this settle
    tried: dict[int, set] = {}      # per PR: the heads a run was started on and ended done
    rerun_used = False
    updates = 0
    link_conflicts: list[int] = []

    def link_chain() -> tuple[bool, str]:
        """Each PR of a stack holds the one below it, or `gh stack merge` refuses the stack: updating the bottom from the
        stub is not enough. (merged any in, why it could not); a conflict goes to the dossier's agent."""
        del link_conflicts[:]
        moved = False
        for lower, upper in zip(prs, prs[1:]):
            git(root, "fetch", "-q", "origin", lower["headRefName"], upper["headRefName"])
            if any(git(root, "rev-parse", "--verify", "-q", f"origin/{x['headRefName']}")[0] != 0 for x in (lower, upper)):
                continue   # not on origin: nothing to compare, and gh stack merge has the last word
            if git(root, "merge-base", "--is-ancestor", f"origin/{lower['headRefName']}", f"origin/{upper['headRefName']}")[0] == 0:
                continue
            note("fixing", f"updating #{upper['number']} with #{lower['number']}, the pull request below it")
            why = merge_base_in(root, upper["headRefName"], lower["headRefName"])
            if why == "conflict":
                link_conflicts.append(upper["number"])
            elif why:
                return False, f"could not update #{upper['number']} with #{lower['number']}: {why}"
            else:
                moved = True
        return moved, ""

    def update_behind() -> tuple[bool, str]:
        """(updated any, why it could not). A conflict from the update comes back as CONFLICTING on the next round."""
        nonlocal updates
        stale = behind(root, nums)
        if not stale:
            return False, ""
        updates += 1
        if updates > 5:
            return False, "still behind its base after 5 updates: " + ", ".join(f"#{n}" for n in stale)
        note("fixing", "updating " + ", ".join(f"#{n}" for n in stale) + " with the latest " + base)
        for n in stale:
            rc, out = gh(root, "pr", "update-branch", str(n), timeout=300)
            if rc != 0 and "conflict" not in out.lower():
                # GitHub can refuse this on a stacked PR: the same merge, by hand.
                p = next(x for x in prs if x["number"] == n)
                why = merge_base_in(root, p["headRefName"], p["baseRefName"])
                if why and why != "conflict":   # a conflict comes back as CONFLICTING on the next round
                    return False, f"could not update #{n}: {out[-120:]}; {why}"
        time.sleep(float(os.environ.get("ASD_MERGE_POLL") or 15))   # let GitHub see the push and start the checks
        return True, ""

    while True:
        # 2. ids, mechanical (the bottom PR carries the dossier; stacked ones are fixes)
        bottom = prs[0]
        ren, adr = collisions(root, bottom["baseRefName"], bottom["headRefName"], others)
        if ren or adr:
            note("fixing", "renumbering " + ", ".join(i for i, _ in ren) + (" and ADR numbers" if adr else "") + " the base already holds")
            why = fix_ids(root, bottom["baseRefName"], bottom["headRefName"], ren, adr)
            if why and why != "conflict":
                return why
            if not why:
                time.sleep(float(os.environ.get("ASD_MERGE_POLL") or 15))   # let GitHub see the push
        # 1. mergeability
        bad, err = conflicting(root, nums)
        if err:
            return err
        # A clash is the stack's to fix, so no check is worth waiting for until it is gone.
        clashes = migration_clashes(root, prs[0]["baseRefName"], prs[-1]["headRefName"]) if not bad else []
        # 3. up to date with the base, and each PR of a stack holding the one below it, before spending a wait on checks
        # that would have to run again
        if not bad and not clashes:
            updated, why = update_behind()
            if why:
                return why
            if updated:
                continue
            linked, why = link_chain()
            if why:
                return why
            if linked:
                time.sleep(float(os.environ.get("ASD_MERGE_POLL") or 15))
                continue
            bad = bad + link_conflicts
        # 3. checks (re-run red ones once)
        st = wait_checks(root, nums, check_timeout) if not bad and not clashes else {}
        red = [n for n, v in st.items() if v == "red"]
        if red and not rerun_used:
            rerun_used = True
            heads = [p["headRefName"] for p in prs if p["number"] in red]
            if any(rerun_failed(root, h) for h in heads):
                note("rerunning", "checks red on " + ", ".join(f"#{n}" for n in red) + "; re-running the failed jobs once")
                time.sleep(float(os.environ.get("ASD_MERGE_POLL") or 15))
                st = wait_checks(root, nums, check_timeout)
                red = [n for n, v in st.items() if v == "red"]
        pending = [n for n, v in st.items() if v == "pending"]
        if not bad and not clashes and not red and not pending:
            updated, why = update_behind()   # a merge from outside the queue can land while the checks ran
            if why:
                return why
            if updated:
                continue
            linked, why = link_chain()
            if why:
                return why
            if linked:
                continue
            if not link_conflicts:
                return ""
            bad = list(link_conflicts)
        if pending and not bad and not clashes and not red:
            return "checks still running after " + str(int(check_timeout / 60)) + " min: " + ", ".join(f"#{n}" for n in pending)
        # 4. the dossier's agent
        if not use_agent:
            return ("conflicting: " + ", ".join(f"#{n}" for n in bad)) if bad else ("migration numbers clash: " + "; ".join(clashes)) if clashes else ("checks red: " + ", ".join(f"#{n}" for n in red))
        targets = bad or ([n for n in red] if red else list(nums))
        heads = {n: head_of(root, n) for n in targets}
        spent = [n for n in targets if attempts.get(n, 0) >= AGENT_ATTEMPTS]
        unchanged = all(heads[n] and heads[n] in tried.get(n, set()) for n in targets)
        if spent or unchanged:
            rounds = f" after {max(attempts.get(n, 0) for n in targets)} resolve-merge attempt{'s' if max(attempts.get(n, 0) for n in targets) != 1 else ''}" + ("" if spent else "; its head did not change")
            why = ("still conflicting: " + ", ".join(f"#{n}" for n in bad) + rounds) if bad else ("migration numbers still clash: " + "; ".join(clashes) + rounds) if clashes else ("checks still red: " + ", ".join(f"#{n}" for n in red) + rounds)
            ask_resolve_failed(root, agent_id, why)
            return why
        for n in targets:
            attempts[n] = attempts.get(n, 0) + 1
        rerun_used = False   # the agent's push is a new head: a flaky job there gets its own re-run before it costs an attempt
        what = ("conflicts in " + ", ".join(f"#{n}" for n in bad)) if bad else ("migration numbers clash: " + "; ".join(clashes)) if clashes else ("red checks on " + ", ".join(f"#{n}" for n in red))
        note("resolving", what + "; the dossier's agent resolves it (resolve-merge, attempt " + str(max(attempts[n] for n in targets)) + f" of {AGENT_ATTEMPTS})")
        since = now()
        start_run(root, agent_id, "resolve-merge", nums)
        end = wait_run(f"{repo_name(root)}.{agent_id}", since, run_timeout)
        if end == "done":
            for n in targets:
                tried.setdefault(n, set()).add(heads[n])
        elif all(attempts[n] >= AGENT_ATTEMPTS for n in targets):
            why = f"resolve-merge ended {end} twice"
            ask_resolve_failed(root, agent_id, why)
            return why


def work_one(root: str, dossier: str) -> None:
    repo = repo_name(root); key = f"{repo}.{dossier}"
    run_timeout = float(os.environ.get("ASD_MERGE_RUN_TIMEOUT") or 4 * 3600)
    if run_live(key):
        set_state(key, state="waiting", error="", pid=os.getpid(), note="waiting for the dossier's own run to finish")
        while run_live(key):
            time.sleep(float(os.environ.get("ASD_MERGE_POLL") or 15))
    # A worker killed around the merge (a logout, a reboot) comes back to PRs GitHub has already merged. The merge
    # detector starts their /after-merge, so all that is left here is to say so.
    prior = read_json(os.path.join(runs_dir(), key + ".merge.json"), {}) or {}
    if prior.get("prs") and prior.get("state") in ("merging", "cleanup"):
        merged = [gh(root, "pr", "view", str(n), "--json", "state", "-q", ".state") for n in prior["prs"]]
        if all(rc == 0 and st.strip() == "MERGED" for rc, st in merged):
            set_state(key, state="merged", error="", pid=None, note="merged; found so after the merge queue restarted")
            return
    chain, why = plan(root, dossier)
    if not chain:
        set_state(key, state="failed", error=why, pid=None); return
    nums = [p["number"] for p in chain]
    def note(state, text):
        set_state(key, state=state, prs=nums, error="", pid=os.getpid(), note=text)
        append_line(os.path.join(runs_dir(), key + ".jsonl"), {"ts": now(), "kind": "runner", "text": "merge queue: " + text})
    why = settle(root, key, dossier, chain, chain[0]["baseRefName"], [], note)
    if why:
        set_state(key, state="failed", prs=nums, error=why, pid=None); return
    set_state(key, state="merging", pid=os.getpid())
    import io, contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = merge(root, dossier, "merge", False, no_cleanup=True)
    if rc != 0:
        return  # merge() recorded "failed" and why
    st = read_json(os.path.join(runs_dir(), key + ".merge.json"), {}) or {}
    nums = st.get("prs") or []
    # /after-merge runs beside the queue: it never pushes to the stub, and a question it asks must not hold up the next merge.
    start_run(root, dossier, "after-merge", nums)
    set_state(key, state="merged", pid=None, note="merged; /after-merge runs")


DOC_PATH_RE = re.compile(r"^(\.discovery/|docs/)|\.md$")


def enqueue_pr(root: str, n: int) -> int:
    """Add a non-code PR into development (a plan or research PR) to the repository's merge queue, once."""
    repo = repo_name(root)
    with locked(repo):
        q = read_json(queue_path(repo), {}) or {}
        items = q.get("items") or []
        fresh = not any(i.get("pr") == n for i in items)
        if fresh:
            items.append({"id": f"#{n}", "pr": n, "ts": now()})
        q["items"], q["root"] = items, root
        write_json(queue_path(repo), q)
        if fresh:   # one already in the queue keeps the state it is in (pressing again must not hide the work under way)
            set_state(f"{repo}.pr-{n}", state="queued", position=len(items), error="", pid=None)
        worker_ = q.get("worker")
        if not (worker_ and alive(worker_)):
            log = os.path.join(runs_dir(), f"{repo}.merge-queue.log")
            with open(log, "ab") as fh:
                p = subprocess.Popen([sys.executable, os.path.abspath(__file__), "--worker", "--root", root], stdin=subprocess.DEVNULL,
                                     stdout=fh, stderr=subprocess.STDOUT, start_new_session=True)
            q["worker"] = p.pid
            write_json(queue_path(repo), q)
    return 0


def mergeable_doc_pr(root: str, n: int) -> dict:
    """The non-code PR's row, or {} after recording why the queue refuses it."""
    key = f"{repo_name(root)}.pr-{n}"
    rc, out = gh(root, "pr", "view", str(n), "--json", "number,state,baseRefName,headRefName,isDraft,files")
    try:
        p = json.loads(out) if rc == 0 else {}
    except ValueError:
        p = {}
    if p and p.get("state") == "MERGED":   # it landed before an interruption (a killed worker, a batch that got through)
        set_state(key, state="merged", error="", pid=None, note="merged into " + str(p.get("baseRefName"))); return {}
    if not p or p.get("state") != "OPEN":
        set_state(key, state="failed", error="not an open pull request", pid=None); return {}
    if p.get("isDraft"):
        set_state(key, state="failed", error="still a draft: mark it ready for review first", pid=None); return {}
    code = [f for f in (x.get("path", "") for x in p.get("files") or []) if not DOC_PATH_RE.search(f)]
    if code:
        set_state(key, state="failed", error="changes code (" + ", ".join(code[:3]) + "): code reaches development only through a stub; retarget it to the stub, or drop those files", pid=None); return {}
    return p


# ---------------------------------------------------------------- a batch of non-code PRs, one CI run
# A strict branch rule makes every merge put the rest behind, so N PRs one at a time cost N CI runs. Waiting non-code
# PRs are merged together on one plan/queue-batch-* branch, checked once and merged through one PR; GitHub then marks
# each member merged, because its head is in the base. Anything out of the ordinary (a member that conflicts, a batch
# that stays red) goes back to one PR at a time, where the blame is clear. ASD_MERGE_BATCH=0 turns batching off.
BATCH_PREFIX = "plan/queue-batch-"


def work_batch(root: str, nums: list[int]) -> list[int]:
    """Merge the PRs through one CI run; returns those the queue is finished with (merged or refused)."""
    repo = repo_name(root)
    done: list[int] = []
    rows: list[dict] = []
    for n in nums:
        p = mergeable_doc_pr(root, n)
        if p and (not rows or p["baseRefName"] == rows[0]["baseRefName"]):
            rows.append(p)
        elif not p:
            done.append(n)
    if len(rows) < 2:
        return done
    base = rows[0]["baseRefName"]
    label = ", ".join(f"#{p['number']}" for p in rows)
    def state_all(ps, **kw):
        for x in ps:
            set_state(f"{repo}.pr-{x['number']}", **kw)
    state_all(rows, state="fixing", error="", pid=os.getpid(), note="renumbering any id the batch holds twice")
    git(root, "fetch", "-q", "origin", "--prune")
    kept: list[dict] = []
    for p in rows:   # the PR earlier in the queue keeps its id
        ren, adr = collisions(root, base, p["headRefName"], [x["headRefName"] for x in kept])
        if (ren or adr) and fix_ids(root, base, p["headRefName"], ren, adr):
            continue   # one at a time, afterwards
        git(root, "fetch", "-q", "origin", p["headRefName"])
        kept.append(p)
    wt = os.path.join(tempfile.mkdtemp(prefix="asd-batch-"), "w")
    rc, out = git(root, "worktree", "add", "-q", "--detach", wt, f"origin/{base}")
    if rc != 0:
        return done
    branch = f"{BATCH_PREFIX}{rows[0]['number']}-{rows[-1]['number']}"
    members: list[dict] = []
    try:
        for p in kept:
            rc, sha = git(root, "rev-parse", f"origin/{p['headRefName']}")
            rc2, _ = git(wt, "merge", "--no-edit", "--no-ff", f"origin/{p['headRefName']}")
            if rc != 0 or rc2 != 0:
                git(wt, "merge", "--abort")
                continue
            members.append({**p, "sha": sha.strip()})
        if len(members) < 2:
            return done
        rc, out = git(wt, "push", "-q", "--force", "origin", f"HEAD:refs/heads/{branch}")
        if rc != 0:
            return done
    finally:
        git(root, "worktree", "remove", "--force", wt)
    label = ", ".join(f"#{p['number']}" for p in members)
    rc, out = gh(root, "pr", "create", "--base", base, "--head", branch, "--title", f"plans: batch of {label}",
                 "--body", f"The merge queue checks and merges {label} together, in one CI run. Each of them reads as merged once this lands.")
    m = re.search(r"/pull/(\d+)", out) if rc == 0 else None
    if not m:   # the worker was interrupted after opening it: carry on with the same PR
        rc, out = gh(root, "pr", "view", branch, "--json", "number", "-q", r'"/pull/\(.number)"')
        m = re.search(r"/pull/(\d+)", out) if rc == 0 else None
    if not m:
        git(root, "push", "-q", "origin", "--delete", branch)
        return done
    num = int(m.group(1))
    def note(state, text):
        state_all(members, state="batching", batch=num, error="", pid=os.getpid(), note=f"in batch #{num}: {text}")
    def give_up(why: str) -> list[int]:
        gh(root, "pr", "close", str(num), "--delete-branch", "--comment", f"Back to one at a time: {why}")
        state_all(members, state="queued", batch=None, pid=None, note=f"batch #{num} did not land ({why}); one at a time now")
        return done
    note("batching", "waiting for its checks")
    row = {"number": num, "headRefName": branch, "baseRefName": base}
    for _ in range(3):
        why = settle(root, f"{repo}.pr-{num}", f"PR-{num}", [row], base, [], note, use_agent=False)
        if why:
            return give_up(why)
        rc, out = gh(root, "pr", "merge", str(num), "--merge", "--delete-branch", timeout=300)
        if not (rc != 0 and "not up to date" in out):
            break
    rc2, st = gh(root, "pr", "view", str(num), "--json", "state", "-q", ".state")
    if st.strip() != "MERGED":
        return give_up(out[-200:] if rc != 0 else f"not merged afterwards ({st.strip()})")
    # GitHub marks a member merged by itself, a moment later; one it does not is closed saying where its work went.
    for p in members:
        key = f"{repo}.pr-{p['number']}"
        state = ""
        for _ in range(int(os.environ.get("ASD_MERGE_MERGEABLE_TRIES") or 12)):
            _, state = gh(root, "pr", "view", str(p["number"]), "--json", "state", "-q", ".state")
            if state.strip() == "MERGED":
                break
            time.sleep(float(os.environ.get("ASD_MERGE_POLL") or 15) / 3)
        if state.strip() != "MERGED":
            _, head = gh(root, "pr", "view", str(p["number"]), "--json", "headRefOid", "-q", ".headRefOid")
            if head.strip() and p["sha"] and head.strip() != p["sha"]:   # pushed to during the batch: that push is not in
                set_state(key, state="failed", pid=None, error=f"its branch changed while batch #{num} ran; the new commits are not merged")
                done.append(p["number"])
                continue
            gh(root, "pr", "close", str(p["number"]), "--comment", f"Merged into {base} through #{num}.")
        set_state(key, state="merged", pid=None, error="", note=f"merged into {base} through batch #{num}")
        done.append(p["number"])
    return done


def work_pr(root: str, n: int) -> None:
    """One non-code PR into development through the shared steps, then merged by script (no cleanup run)."""
    repo = repo_name(root); key = f"{repo}.pr-{n}"
    p = mergeable_doc_pr(root, n)
    if not p:
        return
    files = [f.get("path", "") for f in p.get("files") or []]
    ids = [m.group(1) for f in files if (m := ID_FILE_RE.match(f))]
    agent_id = ids[0] if ids else f"PR-{n}"
    def note(state, text):
        set_state(key, state=state, error="", pid=os.getpid(), note=text)
        if ids:
            append_line(os.path.join(runs_dir(), f"{repo}.{agent_id}.jsonl"), {"ts": now(), "kind": "runner", "text": f"merge queue (#{n}): " + text})
    for _ in range(3):
        why = settle(root, key, agent_id, [p], p["baseRefName"], [], note)
        if why:
            set_state(key, state="failed", error=why, pid=None); return
        set_state(key, state="merging", pid=os.getpid())
        rc, out = gh(root, "pr", "merge", str(n), "--merge", timeout=300)
        if not (rc != 0 and "not up to date" in out):   # another merge won the race: settle again
            break
    rc2, st = gh(root, "pr", "view", str(n), "--json", "state", "-q", ".state")
    if st.strip() != "MERGED":   # the state decides: a merge retried after a blip can report an error and still be in
        set_state(key, state="failed", error=(out[-300:] if rc != 0 else f"not merged afterwards ({st.strip()})"), pid=None); return
    set_state(key, state="merged", pid=None, note="merged into " + p["baseRefName"])


def worker(root: str) -> int:
    """Work the repository's merge queue to the end, one dossier at a time."""
    repo = repo_name(root)
    with locked(repo):
        q = read_json(queue_path(repo), {}) or {}
        if q.get("worker") and q["worker"] != os.getpid() and alive(q["worker"]):
            return 0
        q["worker"] = os.getpid(); write_json(queue_path(repo), q)
    single: set[int] = set()   # tried in a batch already: one at a time now
    while True:
        with locked(repo):
            q = read_json(queue_path(repo), {}) or {}
            items = q.get("items") or []
            for i, it in enumerate(items):   # the rest learn their place
                set_state(f"{repo}.pr-{it['pr']}" if it.get("pr") else f"{repo}.{it['id']}", position=i + 1) if i else None
            if not items:
                q["worker"] = None; write_json(queue_path(repo), q)
                return 0
            head = items[0]["id"]; head_pr = items[0].get("pr")
            # The non-code PRs waiting in a row from the head; a dossier stack keeps its place in the line.
            run: list[int] = []
            for it in items:
                if not it.get("pr"):
                    break
                if int(it["pr"]) not in single:
                    run.append(int(it["pr"]))
        finished = [head]
        try:
            if head_pr and len(run) > 1 and run[0] == int(head_pr) and os.environ.get("ASD_MERGE_BATCH") != "0":
                single.update(run)
                finished = [f"#{n}" for n in work_batch(q.get("root") or root, run)]
            elif head_pr:
                work_pr(q.get("root") or root, int(head_pr))
            else:
                work_one(q.get("root") or root, head)
        except Exception as e:  # one dossier's failure never stops the queue
            if head_pr and len(run) > 1 and int(head_pr) in single and finished == [head] and run[0] == int(head_pr):
                finished = []   # the batch failed: each goes through on its own
            else:
                set_state(f"{repo}.pr-{head_pr}" if head_pr else f"{repo}.{head}", state="failed", error=f"{type(e).__name__}: {e}", pid=None)
        with locked(repo):
            q = read_json(queue_path(repo), {}) or {}
            q["items"] = [i for i in (q.get("items") or []) if i["id"] not in finished]
            write_json(queue_path(repo), q)


def revive() -> int:
    """Start a worker for every merge queue with work and no live worker (killed by a logout, a reboot, a crash)."""
    for path in glob.glob(os.path.join(runs_dir(), "*.merge-queue.json")):
        q = read_json(path, {}) or {}
        root = q.get("root")
        if not (q.get("items") and root and os.path.isdir(root)) or (q.get("worker") and alive(q["worker"])):
            continue
        with open(path[:-5] + ".log", "ab") as fh:
            subprocess.Popen([sys.executable, os.path.abspath(__file__), "--worker", "--root", root], stdin=subprocess.DEVNULL,
                             stdout=fh, stderr=subprocess.STDOUT, start_new_session=True)
        print(f"merge_dossier: restarted the merge queue of {os.path.basename(root)}")
    # An integration whose process died while it waited for the queue: picked up again, so /stub still starts after it.
    for path in glob.glob(os.path.join(runs_dir(), "*.integrate.json")):
        st = read_json(path, {}) or {}
        if st.get("state") == "merging" and st.get("root") and st.get("branch") and os.path.isdir(st["root"]) and not alive(st.get("pid")):
            with open(path[:-5] + ".log", "ab") as fh:
                p = subprocess.Popen([sys.executable, os.path.abspath(__file__), "--integrate", st["branch"], "--root", st["root"]], stdin=subprocess.DEVNULL,
                                     stdout=fh, stderr=subprocess.STDOUT, start_new_session=True)
            write_json(path, {**st, "pid": p.pid, "ts": now()})
            print(f"merge_dossier: restarted the integration of {st['branch']}")
    return 0


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
    line(f"start /merge-queue {branch}")
    # Step 1: the stub's pull request into development. Opening it closes the stub: new work goes to the next one.
    into = stub_into_dev(root, branch)
    if into == "merged":
        state(state="failed", error=f"{branch} is already merged into development"); return 1
    if not into:
        date = branch.split("/", 1)[1]
        rc, out = gh(root, "pr", "create", "--base", "development", "--head", branch, "--title", f"Stub {date}: integrating",
                     "--body", "Integration of this stub, started from the dashboard's Integrate.\n\n"
                     "1. The merge queue merges every finished dossier on the stub, one by one.\n"
                     "2. /stub runs the full stack suite, audits each dossier against its criteria, and fixes what fails.\n\n"
                     "## Receipt\n\npending\n\n## Fix rounds\n")
        if rc != 0:
            state(state="failed", error="could not open the stub's PR into development: " + out[-200:]); return 1
        line(f"Opened the stub's pull request into development ({out.strip().splitlines()[-1] if out.strip() else ''}): {branch} is closed, new work goes to the next stub.", "update")
    rc, out = gh(root, "pr", "list", "--state", "open", "--limit", "200", "--json", "number,headRefName,baseRefName,isDraft")
    if rc != 0:
        state(state="failed", error="gh pr list failed: " + out[:200]); return 1
    everything = json.loads(out or "[]")
    prs = sorted([p for p in everything if p.get("baseRefName") == branch and p["headRefName"] != branch], key=lambda p: p["number"])
    ids = []
    others = []   # no dossier's stack: never merged from here, only named, so the owner can decide about it
    for p in prs:
        m = re.search(r"([A-Za-z][A-Za-z0-9]*-\d+)", p["headRefName"])
        if m and not p["headRefName"].startswith(("plan/", "docs/")):
            i = m.group(1).upper()
            if i not in [x[0] for x in ids]:
                ids.append((i, p))
        else:
            others.append(p)
    def ready(i: str, p: dict) -> bool:
        """Every PR of the dossier's stack is out of draft (the queue refuses a stack with a draft in it), and no run is live."""
        chain, _ = chain_of(everything, i)
        return not any(x.get("isDraft") for x in (chain or [p])) and not still_building(f"{repo}.{i}")
    finished = picked_stacks(ids, ready, (read_json(st_path, {}) or {}).get("finished") or [])
    inflight = [i for i, p in ids if i not in finished]
    state(state="merging", finished=finished, inflight=inflight, error="", root=root, branch=branch)
    line(("Integrating " + branch + ". Merging the finished dossiers first, in order: " + (", ".join(finished) or "none") + "."
          + (" Still in flight, so they move to the next stub once this one is in development: " + ", ".join(inflight) + "." if inflight else "")
          + (" Not part of this integration, because no dossier owns them: " + ", ".join(f"#{p['number']} {p['headRefName']}" for p in others) + ". A PR that changes only documents can go straight to development; any other goes to the next stub." if others else "")), "update")
    for i in finished:
        with contextlib.redirect_stdout(io.StringIO()):
            enqueue(root, i)
    poll = float(os.environ.get("ASD_MERGE_POLL") or 15)
    sts: dict = {}
    while True:   # as long as it takes: each stack ends merged or failed, and the queue survives outages and restarts
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
    if "--head" in a and a[a.index("--head") + 1].startswith(("stub/", "release/")):
        print("OPEN" if os.path.exists(os.path.join(d, "stubpr.txt")) else ""); sys.exit(0)
    sel = [p for p in prs if p.get("state", "OPEN") == "OPEN"]
    if "--base" in a: sel = [p for p in sel if p["baseRefName"] == a[a.index("--base") + 1]] if a[a.index("--base") + 1].startswith("stub/") else sel
    print(json.dumps(sel)); sys.exit(0)
if a[:2] == ["pr", "view"] and a[2].startswith("plan/queue-batch-"):
    print("/pull/901"); sys.exit(0)
if a[:2] == ["pr", "create"] and a[a.index("--head") + 1].startswith("plan/queue-batch-") and os.environ.get("FAKE_BATCH_EXISTS"):
    prs.append({"number": 901, "headRefName": a[a.index("--head") + 1], "baseRefName": a[a.index("--base") + 1], "isDraft": False, "title": "b", "url": "u"})
    json.dump(prs, open(os.path.join(d, "prs.json"), "w")); print("a pull request for branch already exists"); sys.exit(1)
if a[:2] == ["pr", "create"] and a[a.index("--head") + 1].startswith("plan/queue-batch-"):
    prs.append({"number": 901, "headRefName": a[a.index("--head") + 1], "baseRefName": a[a.index("--base") + 1], "isDraft": False, "title": "b", "url": "u", "bucket": os.environ.get("FAKE_BATCH_BUCKET", "pass")})
    json.dump(prs, open(os.path.join(d, "prs.json"), "w")); print("https://github.com/o/r/pull/901"); sys.exit(0)
if a[:2] == ["pr", "close"]:
    for p in prs:
        if str(p["number"]) == a[2]: p["state"] = "CLOSED"
    json.dump(prs, open(os.path.join(d, "prs.json"), "w")); sys.exit(0)
if a[:2] == ["pr", "create"]:
    open(os.path.join(d, "stubpr.txt"), "w").write(" ".join(a)); print("https://github.com/o/r/pull/900"); sys.exit(0)
if a[:2] == ["stack", "link"]:
    open(os.path.join(d, "linked.txt"), "w").write(" ".join(a[a.index("--base") + 2:]))
    sys.exit(int(os.environ.get("FAKE_LINK_RC", "0")))
if a[:2] in (["stack", "merge"], ["pr", "merge"]):
    if os.environ.get("FAKE_MERGE_RC", "0") != "0": print("merge blocked by branch protection"); sys.exit(1)
    if a[0] == "pr" and next((p for p in prs if str(p["number"]) == a[2]), {}).get("mss") == "BEHIND": print("the head branch is not up to date with the base branch"); sys.exit(1)
    nums = open(os.path.join(d, "linked.txt")).read().split() if a[0] == "stack" else [a[2]]
    for p in prs:
        if str(p["number"]) in nums: p["state"] = "MERGED"
    if a[0] == "pr" and a[2] == "901" and os.environ.get("FAKE_NO_AUTOMARK") != "1":
        for p in prs:
            if p["headRefName"].startswith(("plan/", "docs/")) and p.get("state", "OPEN") == "OPEN": p["state"] = "MERGED"
    json.dump(prs, open(os.path.join(d, "prs.json"), "w")); sys.exit(0)
if a[:2] == ["pr", "checks"]:
    p = next(p for p in prs if str(p["number"]) == a[2]); print(p.get("bucket", "pass")); sys.exit(0)
if a[:2] == ["run", "list"]: print("[]"); sys.exit(0)
if a[:2] == ["pr", "update-branch"] and os.environ.get("FAKE_NET_BLIP") and not os.path.exists(os.path.join(d, "blipped")):
    open(os.path.join(d, "blipped"), "w").close(); print("error connecting to api.github.com"); sys.exit(1)
if a[:2] == ["pr", "update-branch"] and os.environ.get("FAKE_UPDATE_REFUSED"):
    for p in prs:
        if str(p["number"]) == a[2]: p["mss"] = "CLEAN"
    json.dump(prs, open(os.path.join(d, "prs.json"), "w")); print("Cannot update: the pull request is part of a stack"); sys.exit(1)
if a[:2] == ["pr", "update-branch"]:
    for p in prs:
        if str(p["number"]) == a[2]: p["mss"] = "CLEAN"
    json.dump(prs, open(os.path.join(d, "prs.json"), "w")); sys.exit(0)
if a[:2] == ["pr", "view"] and "mergeStateStatus" in a:
    p = next(p for p in prs if str(p["number"]) == a[2]); print(p.get("mss", "CLEAN")); sys.exit(0)
if a[:2] == ["pr", "view"] and "number,state,baseRefName,headRefName,isDraft,files" in a:
    p = next(p for p in prs if str(p["number"]) == a[2]); print(json.dumps(dict(p, state=p.get("state", "OPEN")))); sys.exit(0)
if a[:2] == ["pr", "view"] and "headRefOid" in a:
    hf = os.path.join(d, "head.txt")
    print(open(hf).read().strip() if os.path.exists(hf) else os.environ.get("FAKE_HEAD", "")); sys.exit(0)
if a[:2] == ["pr", "view"]:
    p = next(p for p in prs if str(p["number"]) == a[2])
    print(p.get("mergeable", "MERGEABLE") if "mergeable" in a else p.get("state", "OPEN")); sys.exit(0)
sys.exit(3)
'''


def selftest() -> int:
    os.environ.setdefault("ASD_MERGE_NET_TRIES", "2")   # a fixture's unreachable remote must not be waited out for ever
    os.environ.setdefault("ASD_MERGE_NET_WAIT", "0.01")
    fails: list[str] = []
    # Leak guard: the questions and runs the owner really sees must not gain a fixture's (repo.*, clone.*) file during this run.
    real_dirs = [os.environ.get("ASD_QUESTIONS_DIR") or os.path.expanduser("~/.agentic/questions"), runs_dir()]
    listing = lambda: {d: {f for f in os.listdir(d) if f.startswith(("repo.", "clone."))} if os.path.isdir(d) else set() for d in real_dirs}
    before_real = listing()

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
        env = dict(os.environ, ASD_GH_BIN=f"{sys.executable} {os.path.join(fdir, 'gh.py')}", FAKE_GH_DIR=fdir, ASD_RUNS_DIR=rdir, ASD_QUESTIONS_DIR=os.path.join(tmp, "questions"))
        os.environ["ASD_QUESTIONS_DIR"] = os.path.join(tmp, "questions")   # the owner questions the queue asks stay in the fixture
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
              rc == 0 and "stack link --base stub/2026-11-03 427 429" in calls and "stack merge 429 --yes --merge" in calls and st.get("state") == "merged")
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
    n = open(os.path.join(d, "runs.txt")).read().count("resolve-merge")
    if not os.environ.get("FAKE_NO_PUSH"): open(os.path.join(d, "head.txt"), "w").write(f"sha{n}")
    if n >= int(os.environ.get("FAKE_RESOLVED_AT_ROUND") or 1):
        prs = json.load(open(os.path.join(d, "prs.json")))
        for p in prs: p["mergeable"] = "MERGEABLE"; p.pop("bucket", None)
        json.dump(prs, open(os.path.join(d, "prs.json"), "w"))
ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
json.dump({"state": "failed" if cmd == "resolve-merge" and os.environ.get("FAKE_RUN_FAILS") else "done", "pid": 2 ** 22 + 7, "started": ts, "command": cmd}, open(os.path.join(rdir, "repo." + dossier + ".runner.json"), "w"))
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
        # ---- migration numbers: a clash goes to the agent before any check is waited for ----
        mo = os.path.join(tmp, "mig.git"); mw = os.path.join(tmp, "mig")
        mg = lambda *x, cwd=mw: subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *x], cwd=cwd, capture_output=True, text=True)
        mg("init", "-q", "--bare", "-b", "main", mo, cwd=tmp); mg("clone", "-q", mo, mw, cwd=tmp)
        def mfile(path):
            os.makedirs(os.path.dirname(os.path.join(mw, path)), exist_ok=True); open(os.path.join(mw, path), "w").write(path)
        mfile("crates/p/migrations/0061_a.sql"); mfile("other/migrations/0062_z.sql"); mg("add", "."); mg("commit", "-q", "-m", "base"); mg("push", "-q", "origin", "HEAD:main")
        def mbranch(name, *paths, rename=None):
            mg("checkout", "-q", "-B", name, "origin/main")
            if rename: mg("mv", *rename)
            for p in paths: mfile(p)
            mg("add", "."); mg("commit", "-q", "-m", name); mg("push", "-q", "-f", "origin", f"HEAD:{name}")
        mbranch("clash", "crates/p/migrations/0061_b.sql")
        found = migration_clashes(mw, "main", "clash")
        check("migrations: a second 0061 in the same folder clashes, and names both files", found == ["crates/p/migrations/0061_b.sql has the number of crates/p/migrations/0061_a.sql"], str(found))
        mbranch("free", "crates/p/migrations/0062_b.sql", "crates/q/migrations/0061_c.sql")
        check("migrations: a free number, and the same number in another folder, do not clash", migration_clashes(mw, "main", "free") == [], str(migration_clashes(mw, "main", "free")))
        mbranch("twice", "crates/p/migrations/0070_x.sql", "crates/p/migrations/0070_y.sql")
        check("migrations: two new files of one stack with one number clash", len(migration_clashes(mw, "main", "twice")) == 2, str(migration_clashes(mw, "main", "twice")))
        mbranch("moved", rename=("crates/p/migrations/0061_a.sql", "crates/p/migrations/0062_a.sql"))
        check("migrations: renumbering onto a free number is no clash", migration_clashes(mw, "main", "moved") == [], str(migration_clashes(mw, "main", "moved")))
        check("migrations: a branch not on origin is left to the merge's checks", migration_clashes(mw, "main", "no-such") == [])
        saved_fns = {n: globals()[n] for n in ("migration_clashes", "conflicting", "wait_checks", "behind", "collisions", "start_run", "wait_run")}
        seen_runs: list = []; waited: list = []; notes: list = []; left = [2]
        def fake_clash(*_a):
            return ["m/0061_b.sql has the number of m/0061_a.sql"] if left[0] > 0 else []
        def fake_start(*_a):
            seen_runs.append(1); left[0] -= 1; return ""
        globals().update(migration_clashes=fake_clash, conflicting=lambda *_a: ([], ""), wait_checks=lambda r, n, t: waited.append(1) or {x: "green" for x in n},
                         behind=lambda *_a: [], collisions=lambda *_a: ([], False), start_run=fake_start, wait_run=lambda *_a: "done")
        one_pr = [P(700, "feat/W-160-x", "stub/s")]
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                why = settle(root, "repo.W-160", "W-160", one_pr, "stub/s", [], lambda s, t: notes.append(t))
            check("queue: a migration clash wakes the agent before any check is waited for, then it merges", why == "" and len(seen_runs) == 2 and len(waited) == 1 and "migration numbers clash" in notes[0], f"{why} {seen_runs} {waited} {notes}")
            left[0] = 99; seen_runs.clear()
            with contextlib.redirect_stdout(io.StringIO()):
                why = settle(root, "repo.W-160", "W-160", one_pr, "stub/s", [], lambda s, t: None)
            check("queue: a clash that stays stops at the round limit and says so", len(seen_runs) == 2 and "migration numbers still clash" in why and "after 2 resolve-merge attempts" in why, why)
        finally:
            globals().update(saved_fns)
        # ---- agent rounds: the queue keeps at it, and stops at the limit ----
        def rounds_scenario(dossier: str, pr: int, resolved_at: int, extra_env: dict | None = None, **fields) -> tuple[list[str], dict]:
            json.dump([dict(P(pr, f"feat/{dossier}-x", "stub/s"), **(fields or {"mergeable": "CONFLICTING"}))], open(os.path.join(fdir, "prs.json"), "w"))
            for f in ("runs.txt", "calls.txt"):
                if os.path.exists(os.path.join(fdir, f)): os.remove(os.path.join(fdir, f))
            open(os.path.join(fdir, "head.txt"), "w").write("sha0")
            saved = dict(os.environ)
            os.environ.update(env, ASD_RUNNER_BIN=fr, ASD_MERGE_POLL="0.2", FAKE_RESOLVED_AT_ROUND=str(resolved_at), **(extra_env or {}))
            try:
                with contextlib.redirect_stdout(io.StringIO()):
                    enqueue(root, dossier)
                for _ in range(600):
                    q = read_json(queue_path("repo"), {}) or {}
                    if not q.get("items") and not (q.get("worker") and alive(q["worker"])):
                        break
                    time.sleep(0.1)
            finally:
                os.environ.clear(); os.environ.update(saved)
            made = open(os.path.join(fdir, "runs.txt")).read().split("\n") if os.path.exists(os.path.join(fdir, "runs.txt")) else []
            return [r for r in made if "resolve-merge" in r], read_json(os.path.join(rdir, f"repo.{dossier}.merge.json"), {}) or {}
        r3, s3 = rounds_scenario("W-141", 610, 2)
        check("queue: a conflict starts resolve-merge by itself; the second attempt may resolve it, and the stack merges", len(r3) == 2 and s3.get("state") == "merged", str(r3) + str(s3))
        r4, s4 = rounds_scenario("W-142", 620, 99)
        check("queue: two resolve-merge attempts per PR is the cap; the stack fails and says so", len(r4) == 2 and s4.get("state") == "failed" and "after 2 resolve-merge attempts" in str(s4.get("error")), str(r4) + str(s4))
        qdir = os.path.join(tmp, "questions")
        asked = [f for f in os.listdir(qdir) if f.endswith(".jsonl")] if os.path.isdir(qdir) else []
        check("queue: the owner is asked once the attempts are spent", any("W-142" in f for f in asked), str(asked))
        check("queue: ... and not before (W-141 resolved on the second attempt, no question)", not any("W-141" in f for f in asked), str(asked))
        r7, s7 = rounds_scenario("W-145", 650, 99, extra_env={"FAKE_NO_PUSH": "1"})
        check("queue: once per PR head: a run that pushed nothing is not repeated on the same head", len(r7) == 1 and s7.get("state") == "failed" and "head did not change" in str(s7.get("error")), str(r7) + str(s7))
        r8, s8 = rounds_scenario("W-146", 660, 99, extra_env={"FAKE_RUN_FAILS": "1"})
        check("queue: resolve-merge failing twice stops after two runs and fails the stack", len(r8) == 2 and s8.get("state") == "failed" and "ended failed twice" in str(s8.get("error")), str(r8) + str(s8))
        r5, s5 = rounds_scenario("W-143", 630, 2, bucket="fail")
        check("queue: a check still red after the agent's push gets another round", len(r5) == 2 and s5.get("state") == "merged", str(r5) + str(s5))
        r6, s6 = rounds_scenario("W-144", 640, 99, bucket="fail")
        check("queue: red checks stop at the cap too", len(r6) == 2 and s6.get("state") == "failed" and "checks still red: #640 after 2 resolve-merge attempts" in str(s6.get("error")), str(r6) + str(s6))
        os.remove(os.path.join(fdir, "head.txt"))
        # ---- integrate: the finished dossiers through the queue, the one in flight left, then /stub ----
        for f in ("runs.txt", "calls.txt"):
            if os.path.exists(os.path.join(fdir, f)): os.remove(os.path.join(fdir, f))
        for n in os.listdir(rdir):
            if n.endswith((".merge.json", ".runner.json")) or n.startswith("repo.merge-queue"): os.remove(os.path.join(rdir, n))
        prs3 = [P(427, "feat/W-128-x", "stub/s"), P(429, "fix/W-128-deferred", "feat/W-128-x"), P(500, "feat/w-130-y", "stub/s"), P(600, "feat/W-131-z", "stub/s", True),
                P(700, "feat/W-140-a", "stub/s"), P(701, "fix/W-140-deferred", "feat/W-140-a", True), P(800, "docs/notes-12", "stub/s")]
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
              rc == 0 and runs[:3] == ["W-128 after-merge", "W-130 after-merge", "stub-s stub"] and ist.get("state") == "stub" and ist.get("inflight") == ["W-131", "W-140"], str(runs) + str(ist))
        calls_i = open(os.path.join(fdir, "calls.txt")).read()
        check("integrate: a stack with a draft on top waits like a draft, instead of failing the integration", "stack merge 701" not in calls_i and "pr merge 700" not in calls_i)
        check("integrate: a PR no dossier owns is named, and left alone", "pr merge 800" not in calls_i and any("#800 docs/notes-12" in t for t in [json.loads(x)["text"] for x in open(os.path.join(rdir, "repo.stub-s.jsonl"))]))
        check("integrate: the stub's timeline gets its own merge-queue section and notes", tl[0] == "start /merge-queue stub/s" and any("W-131" in t for t in tl))
        spr = open(os.path.join(fdir, "stubpr.txt")).read() if os.path.exists(os.path.join(fdir, "stubpr.txt")) else ""
        check("integrate: step 1 opens the stub's PR into development, before any merge", "--base development --head stub/s" in spr and any("is closed" in t for t in tl))
        with contextlib.redirect_stdout(io.StringIO()):
            os.environ.update(env)
            late = plan(root, "W-131")
        saved_env = dict(os.environ)
        os.environ.update(env, ASD_RUNNER_BIN=fr, ASD_MERGE_POLL="0.1")
        try:
            json.dump([], open(os.path.join(fdir, "prs.json"), "w"))
            ip = os.path.join(rdir, "repo.stub-t.integrate.json")
            write_json(ip, {"state": "merging", "pid": 999999, "root": root, "branch": "stub/t", "finished": []})
            with contextlib.redirect_stdout(io.StringIO()):
                revive()
            for _ in range(100):
                if (read_json(ip, {}) or {}).get("state") == "stub": break
                time.sleep(0.1)
            rv = read_json(ip, {})
        finally:
            os.environ.clear(); os.environ.update(saved_env)
            json.dump(prs3, open(os.path.join(fdir, "prs.json"), "w"))
        check("resume: an integration whose process died is picked up again and still starts /stub", rv.get("state") == "stub", str(rv))
        saved_env = dict(os.environ)
        os.environ.update(env)
        try:
            for cmd, want in (("resolve-merge", False), ("after-merge", False), ("work-on", True), ("fix-deferred", True)):
                write_json(os.path.join(rdir, "repo.W-77.runner.json"), {"state": "running", "pid": os.getpid(), "command": cmd})
                check(f"integrate: a live {cmd} run {'keeps a stack in flight' if want else 'does not put a stack in flight'}", still_building("repo.W-77") is want)
            os.remove(os.path.join(rdir, "repo.W-77.runner.json"))
            two = [("W-1", {}), ("W-2", {}), ("W-3", {})]
            check("integrate: a restart keeps the stacks it picked before, even when one is not ready now", picked_stacks(two, lambda i, p: i == "W-3", ["W-1"]) == ["W-1", "W-3"])
            write_json(queue_path("repo"), {"items": [{"id": "W-77", "ts": "t"}], "root": root, "worker": os.getpid()})
            set_state("repo.W-77", state="resolving", position=1, error="", prs=[7], pid=os.getpid())
            with contextlib.redirect_stdout(io.StringIO()):
                enqueue(root, "W-77")
            kept = read_json(os.path.join(rdir, "repo.W-77.merge.json"), {})
            check("enqueue: a stack already in the queue keeps its state", kept.get("state") == "resolving" and kept.get("prs") == [7], str(kept))
            write_json(queue_path("repo"), {})
        finally:
            os.environ.clear(); os.environ.update(saved_env)
        check("integrate: a dossier not picked waits while the stub's PR is open", not late[0] and "being integrated" in late[1], str(late))
        # ---- non-code PRs into development: a colliding dossier id is renumbered by script, then merged; the stub is untouched ----
        g = lambda *x, cwd=None: subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "-c", "commit.gpgsign=false", *x], cwd=cwd, capture_output=True, text=True)
        origin = os.path.join(tmp, "o.git"); clone = os.path.join(tmp, "clone"); pusher = os.path.join(tmp, "pusher")
        g("init", "-q", "--bare", "-b", "development", origin); g("clone", "-q", origin, pusher)
        os.makedirs(os.path.join(pusher, ".discovery", "dossiers"))
        open(os.path.join(pusher, ".discovery", "dossiers", "W-147-first.md"), "w").write("---\nid: W-147\n---\n# first\n")
        g("add", "-A", cwd=pusher); g("commit", "-q", "-m", "base", cwd=pusher); g("push", "-q", "origin", "HEAD:development", cwd=pusher)
        g("checkout", "-q", "-b", "plan/b", cwd=pusher)
        open(os.path.join(pusher, ".discovery", "dossiers", "W-147-second.md"), "w").write("---\nid: W-147\n---\n# second, W-147 here\n")
        g("add", "-A", cwd=pusher); g("commit", "-q", "-m", "plan b", cwd=pusher); g("push", "-q", "origin", "plan/b", cwd=pusher)
        g("clone", "-q", origin, clone); g("config", "commit.gpgsign", "false", cwd=clone); g("config", "user.email", "t@t", cwd=clone); g("config", "user.name", "t", cwd=clone)
        for f in ("calls.txt", "runs.txt", "stubpr.txt"):
            if os.path.exists(os.path.join(fdir, f)): os.remove(os.path.join(fdir, f))
        prs4 = [dict(P(437, "plan/b", "development"), files=[{"path": ".discovery/dossiers/W-147-second.md"}]),
                dict(P(434, "docs/r", "development"), files=[{"path": "docs/r.md"}, {"path": "reading.html"}]),
                dict(P(438, "docs/behind", "development"), files=[{"path": "docs/behind.md"}], mss="BEHIND")]
        json.dump(prs4, open(os.path.join(fdir, "prs.json"), "w"))
        old = dict(os.environ)
        os.environ.update(env, ASD_RUNNER_BIN=fr, ASD_MERGE_POLL="0.1", ASD_MERGE_BATCH="0")
        try:
            enqueue_pr(clone, 437); enqueue_pr(clone, 434); enqueue_pr(clone, 438)
            qp = queue_path(repo_name(clone))
            for _ in range(400):
                q = read_json(qp, {}) or {}
                if not q.get("items") and not (q.get("worker") and alive(q["worker"])): break
                time.sleep(0.1)
        finally:
            os.environ.clear(); os.environ.update(old)
        g("fetch", "-q", "origin", cwd=clone)
        tree = subprocess.run(["git", "-C", clone, "ls-tree", "--name-only", "origin/plan/b", ".discovery/dossiers/"], capture_output=True, text=True).stdout
        body = subprocess.run(["git", "-C", clone, "show", "origin/plan/b:.discovery/dossiers/W-148-second.md"], capture_output=True, text=True).stdout
        rn = repo_name(clone)
        s437 = read_json(os.path.join(rdir, f"{rn}.pr-437.merge.json"), {}); s434 = read_json(os.path.join(rdir, f"{rn}.pr-434.merge.json"), {})
        calls = open(os.path.join(fdir, "calls.txt")).read() if os.path.exists(os.path.join(fdir, "calls.txt")) else ""
        check("non-code PR: a colliding dossier id is renumbered on its branch, with its mentions, then merged",
              "W-148-second.md" in tree and "W-147-second.md" not in tree and "id: W-148" in body and "W-148 here" in body and s437.get("state") == "merged" and "pr merge 437 --merge" in calls, tree + str(s437))
        g("checkout", "-q", "development", cwd=pusher); g("pull", "-q", "origin", "development", cwd=pusher); g("checkout", "-q", "-b", "plan/same-file", cwd=pusher)
        open(os.path.join(pusher, ".discovery", "dossiers", "W-147-first.md"), "w").write("---\nid: W-147\n---\n# first, later\n")
        open(os.path.join(pusher, ".discovery", "dossiers", "W-147-other.md"), "w").write("---\nid: W-147\n---\n# someone else\n")
        g("add", "-A", cwd=pusher); g("commit", "-q", "-m", "same", cwd=pusher); g("push", "-q", "origin", "plan/same-file", cwd=pusher); g("fetch", "-q", "origin", cwd=clone)
        same = collisions(clone, "development", "plan/same-file", [])[0]
        check("ids: a dossier already on the base under the same file is the same dossier; another file with its id is a collision", [f for _, f in same] == [".discovery/dossiers/W-147-other.md"], str(same))
        check("non-code PR: one that changes code is refused with why, never merged", s434.get("state") == "failed" and "changes code" in s434.get("error", "") and "pr merge 434" not in calls)
        s438 = read_json(os.path.join(rdir, f"{rn}.pr-438.merge.json"), {})
        check("non-code PR: a branch behind its base is updated, then merged, not dropped", s438.get("state") == "merged" and "pr update-branch 438" in calls
              and calls.index("pr update-branch 438") < calls.index("pr merge 438 --merge"), str(s438))
        check("non-code PR: the stub, its PR and /stub are never touched", "pr create" not in calls and "stack" not in calls and not os.path.exists(os.path.join(fdir, "runs.txt")))
        # ---- a batch of non-code PRs: one CI run, one merge; each member reads merged; trouble goes back to one at a time ----
        def batch_scenario(tag: str, **extra) -> tuple[dict, dict, str, str]:
            a, b = f"plan/{tag}-a", f"plan/{tag}-b"
            ia, ib = (500, 501) if tag == "ok" else (510, 511) if tag == "red" else (520, 521) if tag == "mark" else (530, 531) if tag == "moved" else (540, 541)
            for br, did in ((a, f"W-{ia}"), (b, f"W-{ia}")):   # the same new id on both: the later one is renumbered
                g("checkout", "-q", "development", cwd=pusher); g("pull", "-q", "origin", "development", cwd=pusher)
                g("checkout", "-q", "-b", br, cwd=pusher)
                open(os.path.join(pusher, ".discovery", "dossiers", f"{did}-{br.split('-')[-1]}.md"), "w").write(f"---\nid: {did}\n---\n# {br}\n")
                g("add", "-A", cwd=pusher); g("commit", "-q", "-m", br, cwd=pusher); g("push", "-q", "origin", br, cwd=pusher)
            if os.path.exists(os.path.join(fdir, "calls.txt")): os.remove(os.path.join(fdir, "calls.txt"))
            json.dump([dict(P(ia, a, "development"), files=[{"path": f".discovery/dossiers/W-{ia}-a.md"}]), dict(P(ib, b, "development"), files=[{"path": f".discovery/dossiers/W-{ia}-b.md"}])], open(os.path.join(fdir, "prs.json"), "w"))
            saved = dict(os.environ)
            os.environ.update(env, ASD_RUNNER_BIN=fr, ASD_MERGE_POLL="0.1", **extra)
            os.environ.pop("ASD_MERGE_BATCH", None)
            try:
                g("fetch", "-q", "origin", cwd=clone)
                enqueue_pr(clone, ia); enqueue_pr(clone, ib)
                for _ in range(400):
                    q = read_json(queue_path(repo_name(clone)), {}) or {}
                    if not q.get("items") and not (q.get("worker") and alive(q["worker"])): break
                    time.sleep(0.1)
            finally:
                os.environ.clear(); os.environ.update(saved)
            rn_ = repo_name(clone)
            cc = open(os.path.join(fdir, "calls.txt")).read()
            return read_json(os.path.join(rdir, f"{rn_}.pr-{ia}.merge.json"), {}), read_json(os.path.join(rdir, f"{rn_}.pr-{ib}.merge.json"), {}), cc, b
        sa, sb, cc, bb = batch_scenario("ok")
        g("fetch", "-q", "origin", "--prune", cwd=clone)
        names = subprocess.run(["git", "-C", clone, "ls-tree", "--name-only", f"origin/{bb}", ".discovery/dossiers/"], capture_output=True, text=True).stdout
        check("batch: the PRs go through one PR and one merge; each reads merged", "pr merge 901 --merge" in cc and "pr merge 500" not in cc and "pr merge 501" not in cc and sa.get("state") == "merged" and sb.get("state") == "merged" and "batch #901" in sa.get("note", ""), cc + str(sa))
        check("batch: an id two members share is renumbered on the later one", "W-500-a.md" not in names and "-b.md" in names and "W-500-b.md" not in names, names)
        check("batch: the batch branch's checks are the ones waited for; no member is merged on its own", "pr checks 901" in cc and "pr checks 500" not in cc, cc)
        sa, sb, cc, _ = batch_scenario("red", FAKE_BATCH_BUCKET="fail")
        check("batch: one that stays red is closed, and each member goes through on its own", "pr close 901" in cc and "pr merge 510 --merge" in cc and "pr merge 511 --merge" in cc and sa.get("state") == "merged" and sb.get("state") == "merged", cc + str(sa) + str(sb))
        sa, sb, cc, _ = batch_scenario("mark", FAKE_NO_AUTOMARK="1")
        check("batch: a member GitHub does not mark merged is closed saying where its work went", "pr close 520" in cc and "through #901" in cc and sa.get("state") == "merged", cc + str(sa))
        # ---- a network blip is waited out; a branch that moved on GitHub is renumbered from where it is ----
        saved_env = dict(os.environ)
        os.environ.update(env, FAKE_NET_BLIP="1", ASD_MERGE_NET_WAIT="0.1", ASD_MERGE_POLL="0.1")
        try:
            json.dump([dict(P(600, "docs/blip", "development"), mss="BEHIND")], open(os.path.join(fdir, "prs.json"), "w"))
            why = settle(clone, "repo.pr-600", "PR-600", [{"number": 600, "headRefName": "docs/blip", "baseRefName": "development"}], "development", [], lambda *a: None)
        finally:
            os.environ.clear(); os.environ.update(saved_env)
        check("network: a blip while updating a branch is retried, not a failure", why == "" and os.path.exists(os.path.join(fdir, "blipped")), why)
        g("checkout", "-q", "development", cwd=pusher); g("pull", "-q", "origin", "development", cwd=pusher); g("checkout", "-q", "-b", "plan/refused", cwd=pusher)
        open(os.path.join(pusher, "refused.md"), "w").write("x\n"); g("add", "-A", cwd=pusher); g("commit", "-q", "-m", "refused", cwd=pusher); g("push", "-q", "origin", "plan/refused", cwd=pusher)
        g("checkout", "-q", "development", cwd=pusher); open(os.path.join(pusher, "base-moved.md"), "w").write("x\n"); g("add", "-A", cwd=pusher); g("commit", "-q", "-m", "base moved", cwd=pusher); g("push", "-q", "origin", "HEAD:development", cwd=pusher)
        saved_env = dict(os.environ)
        os.environ.update(env, FAKE_UPDATE_REFUSED="1", ASD_MERGE_POLL="0.1")
        try:
            json.dump([dict(P(601, "plan/refused", "development"), mss="BEHIND")], open(os.path.join(fdir, "prs.json"), "w"))
            why2 = settle(clone, "repo.pr-601", "PR-601", [{"number": 601, "headRefName": "plan/refused", "baseRefName": "development"}], "development", [], lambda *a: None)
        finally:
            os.environ.clear(); os.environ.update(saved_env)
        g("fetch", "-q", "origin", cwd=clone)
        has = subprocess.run(["git", "-C", clone, "ls-tree", "--name-only", "origin/plan/refused"], capture_output=True, text=True).stdout
        check("update: a refused update-branch merges the base in by hand", why2 == "" and "base-moved.md" in has, why2 + has)
        g("checkout", "-q", "development", cwd=pusher); g("pull", "-q", "origin", "development", cwd=pusher); g("checkout", "-q", "-b", "feat/W-90-low", cwd=pusher)
        open(os.path.join(pusher, "low.md"), "w").write("low\n"); g("add", "-A", cwd=pusher); g("commit", "-q", "-m", "low", cwd=pusher); g("push", "-q", "origin", "feat/W-90-low", cwd=pusher)
        g("checkout", "-q", "-b", "fix/W-90-deferred", cwd=pusher); open(os.path.join(pusher, "top.md"), "w").write("top\n"); g("add", "-A", cwd=pusher); g("commit", "-q", "-m", "top", cwd=pusher); g("push", "-q", "origin", "fix/W-90-deferred", cwd=pusher)
        g("checkout", "-q", "feat/W-90-low", cwd=pusher); open(os.path.join(pusher, "updated.md"), "w").write("from the stub\n"); g("add", "-A", cwd=pusher); g("commit", "-q", "-m", "updated from the stub", cwd=pusher); g("push", "-q", "origin", "feat/W-90-low", cwd=pusher)
        saved_env = dict(os.environ)
        os.environ.update(env, ASD_MERGE_POLL="0.1")
        try:
            json.dump([P(602, "feat/W-90-low", "development"), P(603, "fix/W-90-deferred", "feat/W-90-low")], open(os.path.join(fdir, "prs.json"), "w"))
            why3 = settle(clone, "repo.W-90", "W-90", [{"number": 602, "headRefName": "feat/W-90-low", "baseRefName": "development"}, {"number": 603, "headRefName": "fix/W-90-deferred", "baseRefName": "feat/W-90-low"}], "development", [], lambda *a: None)
        finally:
            os.environ.clear(); os.environ.update(saved_env)
        g("fetch", "-q", "origin", cwd=clone)
        anc = subprocess.run(["git", "-C", clone, "merge-base", "--is-ancestor", "origin/feat/W-90-low", "origin/fix/W-90-deferred"]).returncode
        check("stack: the PR on top is brought up with the one below it, or the stack merge is refused", why3 == "" and anc == 0, why3)
        g("checkout", "-q", "development", cwd=pusher); g("pull", "-q", "origin", "development", cwd=pusher); g("checkout", "-q", "-b", "plan/stale", cwd=pusher)
        open(os.path.join(pusher, ".discovery", "dossiers", "W-147-stale.md"), "w").write("---\nid: W-147\n---\n# stale\n")
        g("add", "-A", cwd=pusher); g("commit", "-q", "-m", "stale", cwd=pusher); g("push", "-q", "origin", "plan/stale", cwd=pusher)
        g("fetch", "-q", "origin", cwd=clone)
        open(os.path.join(pusher, "moved.md"), "w").write("moved\n")
        g("add", "-A", cwd=pusher); g("commit", "-q", "-m", "moved on GitHub", cwd=pusher); g("push", "-q", "origin", "plan/stale", cwd=pusher)
        moved = fix_ids(clone, "development", "plan/stale", [("W-147", ".discovery/dossiers/W-147-stale.md")], False)
        check("ids: a branch that moved on GitHub is renumbered from where it is, not refused", moved == "", moved)
        sa, sb, cc, _ = batch_scenario("again", FAKE_BATCH_EXISTS="1")
        check("resume: a batch PR already open from before an interruption is carried on with", "pr merge 901 --merge" in cc and sa.get("state") == "merged" and sb.get("state") == "merged", cc + str(sa))
        saved_env = dict(os.environ)
        os.environ.update(env, ASD_MERGE_POLL="0.1", ASD_MERGE_BATCH="0", ASD_RUNNER_BIN=fr)
        try:
            rq = repo_name(clone)
            json.dump([dict(P(700, "docs/landed", "development"), files=[{"path": "docs/l.md"}], state="MERGED"), dict(P(701, "docs/next", "development"), files=[{"path": "docs/n.md"}])], open(os.path.join(fdir, "prs.json"), "w"))
            write_json(queue_path(rq), {"items": [{"id": "#700", "pr": 700, "ts": now()}, {"id": "#701", "pr": 701, "ts": now()}], "root": clone, "worker": 999999})
            revive()
            for _ in range(300):
                q = read_json(queue_path(rq), {}) or {}
                if not q.get("items") and not (q.get("worker") and alive(q["worker"])): break
                time.sleep(0.1)
            s700 = read_json(os.path.join(rdir, f"{rq}.pr-700.merge.json"), {}); s701 = read_json(os.path.join(rdir, f"{rq}.pr-701.merge.json"), {})
        finally:
            os.environ.clear(); os.environ.update(saved_env)
        check("resume: a queue whose worker died restarts, reads what landed meanwhile as merged and carries on", s700.get("state") == "merged" and s701.get("state") == "merged", str(s700) + str(s701))
        saved_env = dict(os.environ)
        os.environ.update(env, ASD_MERGE_POLL="0.1", ASD_RUNNER_BIN=fr)
        try:
            rq = repo_name(clone)
            json.dump([dict(P(710, "feat/W-77-x", "stub/2026-11-03"), state="MERGED")], open(os.path.join(fdir, "prs.json"), "w"))
            write_json(os.path.join(rdir, f"{rq}.W-77.merge.json"), {"state": "merging", "prs": [710], "pid": 999999})
            work_one(clone, "W-77")
            s77 = read_json(os.path.join(rdir, f"{rq}.W-77.merge.json"), {})
        finally:
            os.environ.clear(); os.environ.update(saved_env)
        check("resume: a stack that merged before the worker died reads merged, not failed", s77.get("state") == "merged" and not s77.get("error"), str(s77))
        sa, sb, cc, _ = batch_scenario("moved", FAKE_NO_AUTOMARK="1", FAKE_HEAD="pushed-during-the-batch")
        check("batch: a member pushed to during the batch is left open and says so, never closed", "pr close 530" not in cc and sa.get("state") == "failed" and "changed" in sa.get("error", ""), cc + str(sa))
        check("queue: both read merged", read_json(os.path.join(rdir, "repo.W-128.merge.json"), {}).get("state") == "merged" and read_json(os.path.join(rdir, "repo.W-130.merge.json"), {}).get("state") == "merged")
        RUNNER = saved
    leaked = {d: sorted(now_ - before_real[d]) for d, now_ in listing().items() if now_ - before_real[d]}
    check("no file was created in the real questions or runs dir during the selftest", not leaked, str(leaked))
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
    ap.add_argument("--revive", action="store_true", help="restart any merge queue with work whose worker is gone")
    ap.add_argument("--enqueue-pr", metavar="N", type=int, help="add a non-code PR into development to the merge queue")
    ap.add_argument("--integrate", metavar="STUB", help="merge every finished dossier on the stub through the queue, then start /stub")
    ap.add_argument("--migration-clashes", metavar="HEAD", help="print the migrations this branch adds under a number already taken (needs --base)")
    ap.add_argument("--base", help="the branch --migration-clashes compares with")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args(argv)
    if a.selftest:
        return selftest()
    if a.migration_clashes and a.base and a.root:
        found = migration_clashes(os.path.abspath(a.root), a.base, a.migration_clashes)
        print("\n".join(found) if found else "no clash")
        return 1 if found else 0
    if a.revive:
        return revive()
    if a.worker and a.root:
        return worker(os.path.abspath(a.root))
    if a.enqueue_pr and a.root:
        return enqueue_pr(os.path.abspath(a.root), a.enqueue_pr)
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
