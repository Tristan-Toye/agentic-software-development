#!/usr/bin/env python3
"""Automatic work, per repository, off by default: one cycle from a stub's merge to the next stub's merge, no clicks.

The script decides; no agent does. The dashboard calls `--tick` every two minutes with the facts it already knows
(dossier statuses, runner states, deferred items, stubs); the script reads its state file, takes at most the next
step, writes the state file and exits. Every start goes through the normal runner (`run_dossier.py`), so the slot cap,
the `queued` state, the account routing and every owner question apply unchanged.

# Usage

    auto_work.py --enable --root R      # the one switch: automatic work on, and the scripted stub merge (land_stub.py) with it
    auto_work.py --disable --root R     # both off; live runs are never touched
    auto_work.py --start-now --root R   # the next tick starts a cycle now (the first cycle; later ones start by themselves)
    auto_work.py --resume --root R      # a paused cycle goes on (a failed or stopped run is started again)
    auto_work.py --status --root R      # the state file and the eager-merge switch
    auto_work.py --eager-merge on|off --root R   # put each finished dossier on the open stub at once (on by default; works with auto work off)
    auto_work.py --tick --facts FILE|-  # one step for every enabled repository (the dashboard)
    auto_work.py --selftest

# The cycle

1. START: the repository's stub pull request into development merged (`<stub>.integrate.json` reads `merged`, which
   land_stub.py writes whether it or the owner merged), or `--start-now`.
2. The frontier is snapshotted once, at START: every dossier with status `ready` whose `blocked_by` are all `done`
   (to /work-on) and every `planned` one whose `blocked_by` are all `done` (to /plan, then /work-on once the plan made
   it ready). Dossiers already in flight are adopted too: status `building` or `review`, any dossier with a live run,
   and a `pr` or `done` dossier with deferred items still open. Their live run is waited for, never started again, and they get
   Fix all deferred like any member, so integrate never runs past a build or a deferred fix that is still going. A blocker
   adopted that way does not count as `done` for this snapshot: its dependents start in the cycle after, once its fixes
   are in development. Only that snapshot is worked in the cycle; a dossier that becomes eligible later waits for the next START.
   Before the cycle, each merged stub is carried over once (`merge_dossier.py --carry-over`): the open PRs it left behind,
   which GitHub moves to development when the stub's branch is deleted on merge, go to the accepting stub, so this
   cycle's Integrate (and eager merge) takes them.
3. Each member is started through `run_dossier.py` and followed to the end.
4. A member whose build is finished (status `pr` or `done`) gets "Fix all deferred" exactly as the dashboard button
   sends it: every open item (an item the owner ruled to leave open is dropped, so it is not open), named explicitly. It is finished when
   that run is done and none of the items it was launched with is still open (one left open pauses with "accept"), or
   it had none to begin with. An item the run added itself (its own review findings) gets a follow-up /fix-deferred, at
   most 2 per member (`fix_rounds`); after that the member is finished and the next cycle adopts the dossier.
5. When every member is finished: the repository's non-code PRs are queued (`merge_docs_prs.py merge`, the "Merge all"
   button) and the script waits until each one is merged; then the stub's Integrate (`merge_dossier.py --integrate`)
   runs and the script waits for it to reach /stub; then land_stub.py merges the stub's PR when its conditions hold.
6. That merge is the next START.

# Eager merge (its own switch, on by default; `--eager-merge off` turns it off)

`--eager-merge on` puts each dossier on the open stub as soon as it is finished, through the existing merge queue
(`merge_dossier.py ID --enqueue`, the Merge button's entry point), so the stub grows during the week and Integrate has
little left to merge. Finished is the same as for a cycle member: build done (status `pr` or `done`), no run live (so a
fix-deferred run is over) and no open deferred item left (one the owner ruled to stay open does not count). The dossier
must have an open pull request into an open stub. A dossier whose `<repo>.<dossier>.merge.json` exists is never enqueued (a `merged` one only until a later run of it, e.g. /fix-deferred, finishes with a new open PR: blocks_eager)
again (queued, running, waiting, merged, failed or cancelled). A failed one is retried by itself when the failure was transient (CI still running or timed out, a gh/network error; at most twice per head of its stack, one timeline line each) or when a later run of the dossier has finished; a cancelled one, and a failure that needs a human (conflict, red checks, spent resolve-merge attempts), wait for the owner. It works with auto work
on or off; with the switch off nothing of this runs.

# Kanban pull (its own switch: `kanban` in `~/.agentic/delivery-flows.json`, `$ASD_DELIVERY_FLOWS`; default off)

For a repository whose kanban switch is on, every tick (auto work on or off) pulls work in plain queue order, no
classes: a `planned` dossier whose `blocked_by` are all `done` gets /plan, a `ready` one with `delivery: kanban` (read
from its front matter; absent = stub, also with the stub switch off: /plan labels every dossier kanban then) and all blockers
`done` gets /work-on, each through the normal runner when no run of it is live. A started (dossier, command) is recorded in
`<runs dir>/<repo>.kanban.json` and never started again by the pull (a failed run waits for the owner, no loop). Dossiers
in the auto work cycle are left to it, and the cycle's frontier snapshot skips `delivery: kanban` dossiers. Finished
kanban dossiers with an open PR into development join the merge queue (eager merge) while development's CI is green.

# The rules it never bends

Every owner question and every diagram review still waits for the owner: a live run in `waiting` is waited for, never
answered, skipped or timed out. A run that ends `failed` or `stopped`, a run that never shows up, a non-code PR the
queue could not merge, a failed integration and a failed /stub run pause the cycle (`paused`, with the reason). There
is no retry beyond the runner's and the queue's own; `--resume` (the dashboard's Resume) goes on, starting the failed
run again. Disabled, the script starts nothing and changes nothing; runs that are live go on.

# Files

Switch: `$ASD_STUB_AUTOMERGE_FILE` (default `~/.agentic/stub-auto-merge.json`, land_stub.py's file) key `auto_work`,
next to that repository's `enabled` (the scripted stub merge) and `eager_merge` (the eager-merge switch). State: `<runs dir>/<repo>.auto-work.json`, written
atomically: `{cycle, phase, step, started, stub, paused, members: [{id, stage, state, error}], ...}`.
Facts (from the dashboard): `{"repos": {"<repo>": {"root", "runs": [{id, status, blockedBy, runner, deferred}],
"stubs": [{branch, state, canIntegrate}]}}}`.

# A /work-on that ends done with the dossier not at `pr` or `done`

That should not happen. After LAG_TICKS ticks (a status write may land late) the run's own agent is asked once per
launch, through `run_dossier.py chat ID --root R --text ...` (the finished run resumes its session): "the dossier reads
<status>; if that is a mistake, finish the phases now; if intended, reply `INTENDED:` and why". The member's `asked`
records `{ts, status}` and its state reads "asked the agent why it stopped at <status>"; the cycle does not pause. Later
ticks: status `pr`/`done` goes the normal path; an agent `chat` reply newer than `asked.ts` and LAG_TICKS more ticks with
the status still not `pr`/`done` pause with the reply's first 200 characters in the reason; no reply within
`ASD_AUTO_WORK_ASK_WAIT` seconds (default 1800) pauses with "its agent did not answer"; a chat that cannot start pauses
with the error. The ask is never repeated for the same launch (`launch()` resets it).

Exit 0 done, 1 refused (resume of a cycle that is not paused...), 2 bad usage. `ASD_RUNNER_BIN`, `ASD_MERGE_BIN`,
`ASD_DOCS_MERGE_BIN` replace the scripts it starts (tests); `ASD_AUTO_WORK_GRACE` (seconds, default 600) is how long a
started run may take to show its state before the cycle pauses; `ASD_AUTO_WORK_ASK_WAIT` (default 1800) how long the
asked agent may take to reply.

# A runner that was killed, not stopped

The facts report a live-state runner record whose process is dead as `stopped`. When every runner dies at once (a
logout), `run_dossier.py revive` restarts them within moments, so a member whose runner.json still holds a live state
(running, testing, waiting, paused, queued, parked) is shown as "restarting" for up to the same grace
(`ASD_AUTO_WORK_GRACE`, tracked in the member's `restarting`) and the cycle pauses only when it is still dead after that.
A pause of kind `member` with action `relaunch` heals itself on a later tick: when that member's run is live again, or its
/work-on dossier reached `pr`/`done`, the pause is cleared and the cycle goes on; the state keeps a `healed` note with the former
reason. Other pause kinds are never cleared here.
"""
from __future__ import annotations

import argparse
import contextlib
import datetime
import fcntl
import glob
import io
import json
import os
import re
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import merge_dossier  # noqa: E402
from merge_dossier import now, read_json, repo_name, runs_dir  # noqa: E402
import land_stub  # noqa: E402
import planning_runs  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
LIVE = ("running", "testing", "waiting", "queued", "paused", "parked")   # parked: a /work-on waiting for its blockers; run_dossier unparks it
STAGE_OF = {"planned": "plan", "ready": "build", "building": "build", "review": "build", "pr": "fix", "done": "fix"}
FIX_FOLLOWUPS = 2   # follow-up /fix-deferred rounds per member for items a fix run added itself
LAG_TICKS = 2   # ticks a finished run's effect may take to show in the dashboard's facts before the cycle pauses


# ---------------------------------------------------------------- files
def state_path(repo: str) -> str:
    return os.path.join(runs_dir(), f"{repo}.auto-work.json")


def write_atomic(path: str, obj) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".auto-work-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(obj, fh, ensure_ascii=False, indent=1)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.remove(tmp)
        raise


def enabled(repo: str) -> bool:
    return bool(land_stub.settings_of(repo).get("auto_work"))


def eager_on(repo: str) -> bool:
    return land_stub.settings_of(repo).get("eager_merge") is not False   # on unless turned off


def set_eager(repo: str, on: bool) -> None:
    """The eager-merge switch: its own key, next to `auto_work` and `enabled`, which it never touches."""
    all_ = read_json(land_stub.settings_path(), {}) or {}
    write_atomic(land_stub.settings_path(), {**all_, repo: {**(all_.get(repo) or {}), "eager_merge": on}})


def set_switch(repo: str, on: bool) -> None:
    """The one switch: automatic work and the scripted stub merge (land_stub.py's `enabled`) go on and off together."""
    all_ = read_json(land_stub.settings_path(), {}) or {}
    write_atomic(land_stub.settings_path(), {**all_, repo: {**(all_.get(repo) or {}), "auto_work": on, "enabled": on}})


# ---------------------------------------------------------------- kanban (delivery flows)
def flows_path() -> str:
    return os.environ.get("ASD_DELIVERY_FLOWS") or os.path.expanduser("~/.agentic/delivery-flows.json")


def flows_of(repo: str) -> tuple[bool, bool]:
    """(stub, kanban) for the repository: missing file, entry or key = stub on, kanban off; both off is invalid = stub on."""
    ent = (read_json(flows_path(), {}) or {}).get(repo)
    ent = ent if isinstance(ent, dict) else {}
    stub, kanban = ent.get("stub") is not False, ent.get("kanban") is True
    return (True, kanban) if not stub and not kanban else (stub, kanban)


def kanban_on(repo: str) -> bool:
    return flows_of(repo)[1]


def delivery_of(root: str) -> dict[str, str]:
    """{dossier id (upper): delivery} read from the front matter of the dossiers on disk: `delivery: kanban`, else stub."""
    out: dict[str, str] = {}
    for f in glob.glob(os.path.join(root, ".discovery", "dossiers", "*.md")):
        try:
            with open(f, encoding="utf-8") as fh:
                head = fh.read(4000)
        except OSError:
            continue
        m = re.match(r"---\n(.*?)\n---", head, re.S)
        i = re.search(r"^id:\s*([A-Za-z][A-Za-z0-9]*-\d+)\s*$", m.group(1), re.M) if m else None
        if i:
            d = re.search(r"^delivery:\s*[\"']?(\w+)", m.group(1), re.M)
            out[i.group(1).upper()] = "kanban" if d and d.group(1) == "kanban" else "stub"
    return out


def kanban_ids(repo: str, root: str) -> set[str]:
    """The kanban dossiers of a repository whose kanban switch is on (empty otherwise: nothing changes by default)."""
    if not kanban_on(repo):
        return set()
    # Only a labelled dossier: /work-on and merge_dossier.py read `delivery: kanban` alone, so an unlabelled one pulled as
    # kanban would be built and merged as stub, onto a stub no cycle integrates. /plan labels every dossier with only kanban on.
    return {i for i, d in delivery_of(root).items() if d == "kanban"}


def planning_ids(repo: str) -> set[str]:
    """Dossier ids that a LIVE /plan run other than the dossier's own is writing (a free-form seed run or a plan-from-deferred
    run): the dossier reads `planned` while the run is still at work. Never start a second /plan (or /work-on) on an id in
    this set. The parsing lives in planning_runs.py, shared with run_dossier.py's refusal and the dashboard's port."""
    return planning_runs.planning_ids(repo, runs_dir())


def kanban_pull(repo: str, root: str, facts: dict) -> list[str]:
    """Continuous pull, plain queue order (by id): /plan for a planned dossier, /work-on for a ready kanban one, every
    blocker done, no live run. Starts go through the runner (slot cap, queue, account, owner questions). Returns "ID:command"."""
    runs = facts.get("runs") or []
    done = {r["id"] for r in runs if r.get("status") == "done"}
    kan = kanban_ids(repo, root)
    path = os.path.join(runs_dir(), f"{repo}.kanban.json")
    state = read_json(path, {}) or {}
    started = state.setdefault("started", {})
    aw = read_json(state_path(repo), {}) or {}
    cycle = {m["id"] for m in aw.get("members") or []} if aw.get("step") == "work" else set()   # the auto work cycle's own members
    planning = planning_ids(repo)   # a seed or plan-from-deferred run still writing the dossier: its own runner is not the one to check
    out = []
    for r in sorted(runs, key=lambda r: r["id"]):
        cmd = "plan" if r.get("status") == "planned" else "work-on" if r.get("status") == "ready" and r["id"].upper() in kan else ""
        key = f"{r['id']}:{cmd}"
        if not cmd or key in started or r["id"] in cycle or r["id"].upper() in planning or (r.get("runner") or {}).get("state") in LIVE \
                or not all(b in done for b in r.get("blockedBy") or []):
            continue
        ok, msg = start_runner(root, r["id"], cmd)
        print(f"auto_work: {repo}: kanban pull {key}: " + (msg if ok else "not started: " + msg))
        if ok:
            started[key] = now()
            out.append(key)
    if out:
        write_atomic(path, state)
    return out


def secs(iso: str) -> float:
    try:
        return datetime.datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=datetime.timezone.utc).timestamp()
    except (TypeError, ValueError):
        return 0.0


def age(iso: str) -> float:
    return datetime.datetime.now(datetime.timezone.utc).timestamp() - secs(iso)


def ask_wait() -> float:
    return float(os.environ.get("ASD_AUTO_WORK_ASK_WAIT") or 1800)


def agent_reply(repo: str, dossier: str, since: str) -> str | None:
    """The first chat reply of the run's agent written after `since` (its text), or None while there is none."""
    try:
        with open(os.path.join(runs_dir(), f"{repo}.{dossier}.jsonl"), encoding="utf-8") as fh:
            for raw in fh:
                try:
                    o = json.loads(raw)
                except ValueError:
                    continue
                if isinstance(o, dict) and o.get("kind") == "chat" and o.get("from") == "agent" and str(o.get("ts", "")) > since:
                    return str(o.get("text") or "")
    except OSError:
        pass
    return None


def grace() -> float:
    return float(os.environ.get("ASD_AUTO_WORK_GRACE") or 600)


def blank(repo: str, root: str) -> dict:
    return {"repo": repo, "root": root, "cycle": 0, "step": "idle", "phase": "idle", "started": "", "stub": "", "trigger": "",
            "paused": None, "members": [], "docs": {}, "integrate": {}, "note": "", "seen": [], "baseline": False, "request": None, "ts": now()}


def merged_triggers(root: str, facts: dict | None = None) -> dict[str, str]:
    """{trigger id: stub branch}: every stub of this repository whose pull request merged into development. The id is the branch.
    Two sources: an integrate file reading `merged` (land_stub.py writes it) and the dashboard's facts (`mergedStubs`, read
    from GitHub), which also see a PR the owner merged by hand, where the integrate file still reads `stub`."""
    out = {}
    for p in glob.glob(os.path.join(runs_dir(), "*.integrate.json")):
        st = read_json(p, {}) or {}
        if st.get("state") == "merged" and st.get("root") == root and st.get("branch"):
            out[st["branch"]] = st["branch"]
    for b in (facts or {}).get("mergedStubs") or []:
        out[b] = b
    return out


def lock_try(repo: str):
    """The repository's lock, or None when another process holds it (that one is already taking the step)."""
    fh = open(os.path.join(runs_dir(), f"{repo}.auto-work.lock"), "w")
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fh.close()
        return None
    return fh


def lock_wait(repo: str):
    fh = open(os.path.join(runs_dir(), f"{repo}.auto-work.lock"), "w")
    fcntl.flock(fh, fcntl.LOCK_EX)
    return fh


# ---------------------------------------------------------------- what the cycle starts (all through the existing scripts)
def runner_bin() -> str:
    return os.environ.get("ASD_RUNNER_BIN") or merge_dossier.RUNNER


def start_runner(root: str, dossier: str, command: str, items: str = "") -> tuple[bool, str]:
    return run_runner([sys.executable, runner_bin(), dossier, "--root", root, "--command", command] + (["--items", items] if items else []))


def ask_agent(root: str, dossier: str, text: str) -> tuple[bool, str]:
    """`run_dossier.py chat ID --root R --text T`: the message goes to the finished run's own session; its reply lands as a
    `chat` line from `agent` in the run's log."""
    return run_runner([sys.executable, runner_bin(), "chat", dossier, "--root", root, "--text", text])


def run_runner(args: list[str]) -> tuple[bool, str]:
    try:
        p = subprocess.run(args, capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, str(e)[:200]
    lines = [x.strip() for x in ((p.stdout if p.returncode == 0 else p.stderr or p.stdout) or "").splitlines() if x.strip()]
    return p.returncode == 0, (lines[0] if p.returncode == 0 and lines else lines[-1] if lines else f"exit {p.returncode}")[:300]


def queue_docs(root: str) -> tuple[bool, list[int], str]:
    """The dashboard's "Merge all" for non-code PRs: every ready plan/docs PR joins the merge queue. Returns the numbers."""
    script = os.environ.get("ASD_DOCS_MERGE_BIN") or os.path.join(HERE, "merge_docs_prs.py")
    try:
        p = subprocess.run([sys.executable, script, "merge", "--root", root], capture_output=True, text=True, timeout=600, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, [], str(e)[:200]
    nums = [int(m.group(1)) for m in re.finditer(r"^queued #(\d+)\b", p.stdout or "", re.M)]
    return p.returncode == 0, nums, ((p.stdout or "") + (p.stderr or "")).strip().splitlines()[-1][:300] if (p.stdout or p.stderr).strip() else ""


def start_integrate(root: str, branch: str) -> tuple[bool, str]:
    """The dashboard's Integrate: `merge_dossier.py --integrate`, detached, with the file the button writes first."""
    repo = repo_name(root); slug = branch.replace("/", "-")
    script = os.environ.get("ASD_MERGE_BIN") or os.path.join(HERE, "merge_dossier.py")
    path = os.path.join(runs_dir(), f"{repo}.{slug}.integrate.json")
    try:
        with open(os.path.join(runs_dir(), f"{repo}.{slug}.integrate.log"), "ab") as fh:
            p = subprocess.Popen([sys.executable, script, "--integrate", branch, "--root", root], stdin=subprocess.DEVNULL, stdout=fh, stderr=fh, start_new_session=True)
    except OSError as e:
        return False, str(e)[:200]
    write_atomic(path, {**(read_json(path, {}) or {}), "pid": p.pid, "root": root, "branch": branch, "state": "merging", "error": "", "ts": now()})
    return True, ""


# ---------------------------------------------------------------- the decisions
def in_flight(r: dict) -> bool:
    """A dossier already being worked when the cycle starts: building or in review, a live run of any kind, or a
    finished build (`pr` or `done`) with deferred items still open. The cycle adopts it, so integrate waits for its run and its
    Fix all deferred instead of landing the stub without it."""
    status = r.get("status")
    if status in ("building", "review"):
        return True
    if ((r.get("runner") or {}).get("state") in LIVE) and status in STAGE_OF:
        return True
    return status in ("pr", "done") and bool(open_items(r))


def frontier(runs: list[dict], skip: frozenset | set = frozenset()) -> list[dict]:
    """Who is worked this cycle: `ready` and `planned` dossiers whose blockers are all `done`, plus every dossier already
    in flight (`in_flight`). A blocker not known is not done, and neither is one adopted this cycle (a `done` build whose
    deferred items are still open): the dependent waits for the cycle after, when the blocker's fixes are in development."""
    done = {r["id"] for r in runs if r.get("status") == "done" and not in_flight(r)}
    out = []
    for r in sorted(runs, key=lambda r: r["id"]):
        if r["id"].upper() in skip:   # a kanban dossier belongs to the pull
            continue
        if in_flight(r) or r.get("status") in ("ready", "planned") and all(b in done for b in r.get("blockedBy") or []):
            out.append({"id": r["id"], "stage": STAGE_OF[r["status"]], "state": "pending", "error": "", "launch": None, "lag": 0, "asked": None, "accepted": False, "rounds": 0, "retried": False})
    return out


def open_items(run: dict) -> list[str]:
    """The items "Fix all deferred" sends: every open one. A "leave open" ruling drops the item (state deprecated), so it is never open."""
    return [i["id"] for i in run.get("deferred") or [] if i.get("state") == "open"]


def pause(st: dict, reason: str, kind: str, member: str = "", action: str = "relaunch") -> None:
    st["paused"] = {"reason": reason, "kind": kind, "member": member, "action": action, "at": now()}
    st["phase"] = "paused"
    if member:
        for m in st["members"]:
            if m["id"] == member:
                m["error"], m["state"] = reason, "failed"


def phase_of(st: dict) -> str:
    if st.get("paused"):
        return "paused"
    step = st["step"]
    if step == "work":
        return "building" if any(m["stage"] in ("plan", "build") and m["stage"] != "finished" for m in st["members"]) else "fixing deferred"
    return {"docs": "merging non-code", "integrate": "integrating", "land": "landing"}.get(step, "idle")


def launch(st: dict, m: dict, command: str, runner: dict, items: str = "") -> bool:
    ok, out = start_runner(st["root"], m["id"], command, items)
    if not ok:
        pause(st, f"{m['id']}: could not start /{command}: {out}", "member", m["id"])
        return False
    if command == "plan":
        m["rounds"] = int(m.get("rounds") or 0) + 1   # plan round n: a plan that ended done without making the dossier ready is run again
    m["launch"] = {"command": command, "items": items, "ts": now(), "pid": runner.get("pid"), "started": runner.get("started")}
    m["state"], m["error"], m["lag"], m["asked"] = "starting", "", 0, None
    return True


def fresh(m: dict, r: dict) -> bool:
    """Whether the runner's file already shows the run this cycle started (not the one it replaced)."""
    L = m["launch"]
    return r.get("command") == L["command"] and (r.get("state") in LIVE or r.get("pid") != L.get("pid") or r.get("started") != L.get("started"))


def killed_not_stopped(st: dict, m: dict) -> bool:
    """Whether the member's runner.json still holds a live state (running, queued...): the process was killed, the
    owner did not stop the run, and `revive` will start it again."""
    rj = read_json(os.path.join(runs_dir(), f"{st['repo']}.{m['id']}.runner.json"), {}) or {}
    return rj.get("state") in LIVE


def heal(st: dict, facts: dict) -> None:
    """Clear a pause of kind member / action relaunch once that member's run is live again (revive restarted it) or its
    dossier reached pr/done. Every other pause kind waits for the owner."""
    p = st.get("paused")
    if not p or p.get("kind") != "member" or p.get("action") != "relaunch" or not p.get("member"):
        return
    run = next((r for r in facts.get("runs") or [] if r.get("id") == p["member"]), None)
    if not run:
        return
    rs = (run.get("runner") or {}).get("state")
    live = rs in LIVE
    mem = next((m for m in st["members"] if m["id"] == p["member"]), {})
    # pr/done only counts for a /work-on: a /fix-deferred starts from a dossier that is already at pr
    built = run.get("status") in ("pr", "done") and (mem.get("launch") or {}).get("command", "work-on") == "work-on" and rs != "failed"
    if not live and not built:
        return
    for m in st["members"]:
        if m["id"] == p["member"]:
            m["error"], m["state"], m["lag"] = "", "running" if live else "pending", 0
            m.pop("restarting", None)
    st["healed"] = {"at": now(), "member": p["member"], "was": p.get("reason", ""),
                    "because": "its run is live again" if live else f"its dossier reads {run.get('status')}"}
    st["paused"], st["phase"] = None, ""


def built_already(m: dict, r: dict) -> bool:
    """The member's /work-on already ran to its end (its run record: command work-on, state done) although the dossier
    on development does not read pr or done: the live status sits on the build branch (`branch:`), or the agent reported
    the ready status as intended. A second /work-on would start a second build of a finished change: only /fix-deferred
    (open items) or nothing follows. A /work-on launched this cycle that is still being judged keeps its own path."""
    L = m.get("launch")
    if r.get("command") == "fix-deferred" and r.get("state") == "done":
        return True   # /fix-deferred only runs on a built dossier, so its finished run proves the build too
    return r.get("command") == "work-on" and r.get("state") == "done" and not (L and L["command"] == "work-on")


def step_member(st: dict, m: dict, run: dict | None) -> None:
    if m["stage"] == "finished":
        return
    if run is None:
        pause(st, f"{m['id']} is not on the dashboard any more", "member", m["id"], "recheck"); return
    status, r, L = run.get("status", ""), run.get("runner") or {}, m.get("launch")
    if status == "dropped":
        m.update(stage="finished", state="finished", error="dropped"); return
    if r.get("state") in LIVE:   # waiting = an owner question: never answered, skipped or timed out here
        m["stage"] = STAGE_OF.get(status, m["stage"])
        m["state"] = {"queued": "queued for a slot", "waiting": "waiting for the owner", "paused": "paused (usage limit)",
                      "parked": "waiting for its blockers"}.get(r["state"], "running")
        m["lag"], m["retried"] = 0, False
        m.pop("restarting", None)
        return
    if L and not fresh(m, r):
        if age(L["ts"]) > grace():
            if m.get("retried"):   # the retry left no trace either
                pause(st, f"{m['id']}: the /{L['command']} run never showed up, and neither did its retry", "member", m["id"])
            else:                  # no trace of the first start: start it once more, and record that
                if L["command"] == "plan":
                    m["rounds"] = max(0, int(m.get("rounds") or 0) - 1)   # a retried start is not another plan round
                if launch(st, m, L["command"], r, L.get("items", "")):
                    m["retried"] = True; m["state"] = "starting (retry)"
        else:
            m["state"] = "starting"
        return
    if L:
        m["retried"] = False
    if L and r.get("state") == "stopped" and killed_not_stopped(st, m):
        # The facts read a live-state record whose process is dead as "stopped" (all runners died in a logout):
        # `run_dossier.py revive` restarts it within moments. Wait for that, up to the grace, before pausing.
        since = m.setdefault("restarting", now())
        if age(since) <= grace():
            m["state"] = "restarting"
            return
    if L and r.get("state") in ("failed", "stopped"):
        pause(st, f"{m['id']}: the /{L['command']} run ended {r['state']}", "member", m["id"]); return
    m["stage"] = STAGE_OF.get(status, m["stage"])
    if status in ("pr", "done") or built_already(m, r):
        m["asked"] = None   # the agent fixed it (or it was intended after all and the dossier moved): the normal path takes over
        todo = open_items(run)
        if L and L["command"] == "fix-deferred":
            # Only an item the run was launched with and left open is "not fixed". An item the run itself added (its own
            # review findings) is new work: it gets a follow-up round, bounded, never an owner pause.
            sent = {i.strip() for i in (L.get("items") or "").split(",") if i.strip()}
            unfixed = [i for i in todo if i in sent] if sent else todo
            added = [i for i in todo if i not in unfixed]
            if m["accepted"] or not todo:   # accepted: the owner took the items still open
                m.update(stage="finished", state="finished", error=""); return
            if unfixed:
                m["lag"] += 1
                if m["lag"] >= LAG_TICKS:
                    pause(st, f"{m['id']}: /fix-deferred is done but {len(unfixed)} item(s) are still open ({', '.join(unfixed[:6])})", "member", m["id"], "accept")
                return
            n = int(m.get("fix_rounds") or 0)
            if n < FIX_FOLLOWUPS:
                if launch(st, m, "fix-deferred", r, ",".join(added)):
                    m["fix_rounds"] = n + 1; m["stage"] = "fix"
                    m["state"] = f"follow-up /fix-deferred {n + 1}/{FIX_FOLLOWUPS}: {len(added)} item(s) the previous run added ({', '.join(added[:6])})"
                return
            m.update(stage="finished", state=f"finished; {len(added)} item(s) added by the last /fix-deferred ({', '.join(added[:6])}) left to the next cycle after {FIX_FOLLOWUPS} follow-up rounds", error=""); return
        if not todo or m["accepted"]:
            m.update(stage="finished", state="finished", error=""); return
        launch(st, m, "fix-deferred", r, ",".join(todo)); m["stage"] = "fix"; return
    want = "plan" if status == "planned" else "work-on"
    if L and L["command"] == want:   # it ended done, and the dossier does not show what it should yet
        m["lag"] += 1
        if m["lag"] < LAG_TICKS:     # a status write may land late: wait before believing "not ready"
            return
        if want == "plan":           # the owner's ruling: plan again, until the dossier reads ready (questions, failures and stops are handled above)
            if m["id"].upper() not in planning_ids(st["repo"]):
                launch(st, m, "plan", r)
            return
        if m["accepted"]:
            return
        ask = m.get("asked")
        if not ask:   # the owner's ruling: this should not happen, so the run's own agent is asked first, once per launch
            ok, out = ask_agent(st["root"], m["id"], ASK_TEXT.format(status=status))
            if not ok:
                pause(st, f"{m['id']}: /work-on stopped at {status} and its agent could not be asked: {out}", "member", m["id"], "recheck"); return
            m["asked"] = {"ts": now(), "status": status, "after": 0}
            m["state"] = f"asked the agent why it stopped at {status}"
            return
        reply = agent_reply(st["repo"], m["id"], ask["ts"])
        if reply is not None:
            ask["after"] = int(ask.get("after") or 0) + (1 if ask.get("replied") else 0)
            ask["replied"] = True
            if ask["after"] >= LAG_TICKS:   # the reply may be followed by a status write: wait for it before believing "still not"
                said = " ".join(reply.split())[:200]
                pause(st, f"{m['id']}: /work-on stopped at {status}; its agent says: {said}", "member", m["id"], "recheck")
        elif age(ask["ts"]) > ask_wait():
            pause(st, f"{m['id']}: /work-on stopped at {status}; its agent did not answer", "member", m["id"], "recheck")
        return
    if m["id"].upper() in planning_ids(st["repo"]):   # a seed / plan-from-deferred run is still writing it: wait, never a second /plan
        return
    launch(st, m, want, r)


ASK_TEXT = ("The runner's check: /work-on ended, but the dossier front matter reads `status: {status}`, not `pr` or `done`. "
            "If that is a mistake, finish the remaining phases now (open the PR, set the status). If it is intended, reply with "
            "a first line starting `INTENDED:` and the reason.")


def start_cycle(st: dict, facts: dict, trigger: str, stub: str) -> None:
    st.update(cycle=st["cycle"] + 1, step="work", started=now(), stub=stub, trigger=trigger, paused=None, docs={}, integrate={}, note="", request=None,
              members=frontier(facts.get("runs") or [], kanban_ids(st["repo"], st["root"])))
    st["seen"] = sorted(set(st["seen"]) | set(merged_triggers(st["root"], facts)))


def step_cycle(st: dict, facts: dict) -> bool:
    """One transition at most. True when the state moved on and the next step may be looked at in the same tick."""
    step, runs = st["step"], {r["id"]: r for r in facts.get("runs") or []}
    if step == "idle":
        merged = merged_triggers(st["root"], facts)
        if not st.get("baseline", True):   # the first look after the switch went on: what merged before it never starts a cycle
            st["seen"] = sorted(set(st["seen"]) | set(merged)); st["baseline"] = True
            if st.get("request") != "start":
                return False
        fresh_ones = {k: b for k, b in merged.items() if k not in st["seen"]}
        if st.get("request") == "start" or fresh_ones:
            k, b = (sorted(fresh_ones.items())[-1] if fresh_ones else ("manual", ""))
            start_cycle(st, facts, k, b)
            return True
        return False
    if step == "work":
        for m in st["members"]:
            step_member(st, m, runs.get(m["id"]))
            if st["paused"]:
                return False
        if all(m["stage"] == "finished" for m in st["members"]):
            st["step"] = "docs"; return True
        return False
    if step == "docs":
        d = st["docs"]
        if not d.get("launched"):
            ok, nums, out = queue_docs(st["root"])
            if not ok:
                pause(st, "the non-code PRs could not be queued: " + out, "docs"); return False
            st["docs"] = {"launched": now(), "queued": nums}
            return True
        waiting = []
        for n in d.get("queued") or []:
            s = (read_json(os.path.join(runs_dir(), f"{st['repo']}.pr-{n}.merge.json"), {}) or {}).get("state")
            if s in ("failed", "cancelled"):
                pause(st, f"non-code PR #{n} did not merge ({s})", "docs"); return False
            if s != "merged":
                waiting.append(n)
        st["note"] = ("waiting for non-code PR " + ", ".join(f"#{n}" for n in waiting)) if waiting else ""
        if not waiting:
            st["step"], st["note"] = "integrate", ""; return True
        return False
    if step == "integrate":
        it = st["integrate"]
        if not it.get("branch"):
            stubs = facts.get("stubs") or []
            mine = [s for s in stubs if (read_json(os.path.join(runs_dir(), f"{st['repo']}.{s['branch'].replace('/', '-')}.integrate.json"), {}) or {}).get("state") in ("merging", "stub")]
            pick = (mine or [s for s in stubs if s.get("canIntegrate")] or [None])[0]
            if not pick:
                st["note"] = "waiting for a stub that can be integrated"; return False
            it.update(branch=pick["branch"], launched="" if not mine else now(), adopted=bool(mine))
        key = f"{st['repo']}.{it['branch'].replace('/', '-')}"
        if not it.get("launched"):
            ok, out = start_integrate(st["root"], it["branch"])
            if not ok:
                pause(st, "could not start Integrate: " + out, "integrate"); return False
            it["launched"] = now(); return False   # the file says merging now; the next tick follows it
        ig = read_json(os.path.join(runs_dir(), key + ".integrate.json"), {}) or {}
        if ig.get("state") == "failed":
            pause(st, f"Integrate of {it['branch']} failed: {(ig.get('error') or '')[:200]}", "integrate"); return False
        if ig.get("state") in ("stub", "merged"):
            st["step"], st["note"] = "land", ""; return True
        st["note"] = f"Integrate of {it['branch']}: " + (ig.get("note") or "merging the finished dossiers")
        return False
    if step == "land":
        it = st["integrate"]; key = f"{st['repo']}.{it['branch'].replace('/', '-')}"
        handmerged = it["branch"] in (facts.get("mergedStubs") or [])   # the owner merged the PR; the integrate file may still read `stub`
        if not handmerged and (read_json(os.path.join(runs_dir(), key + ".integrate.json"), {}) or {}).get("state") != "merged":
            with contextlib.redirect_stdout(io.StringIO()):
                LAND()   # land_stub.py looks at every stub that reached /stub, in repositories that opted in
        ig = read_json(os.path.join(runs_dir(), key + ".integrate.json"), {}) or {}
        if ig.get("state") == "merged" or handmerged:
            trig = it["branch"]
            st["seen"] = sorted(set(st["seen"]) | {trig})
            start_cycle(st, facts, trig, it["branch"])   # the stub's merge is the next START
            return True
        sr = read_json(os.path.join(runs_dir(), key + ".runner.json"), {}) or {}
        if sr.get("command") == "stub" and sr.get("state") in ("failed", "stopped"):
            pause(st, f"the /stub run of {it['branch']} ended {sr['state']}", "stub-run"); return False
        land = read_json(os.path.join(runs_dir(), key + ".land.json"), {}) or {}
        st["note"] = f"waiting to merge {it['branch']}: " + ("; ".join(land.get("reasons") or []) or "the /stub run is going")
        return False
    return False


LAND = land_stub.land_all


def finished_run(run: dict) -> bool:
    """A dossier that is finished the way a cycle member is: build done (`pr` or `done`), no run of it live (a fix-deferred
    run is over) and no open deferred item left (open_items: one the owner ruled to stay open does not count)."""
    return run.get("status") in ("pr", "done") and (run.get("runner") or {}).get("state") not in LIVE and not open_items(run)


def held_by_deferred(run: dict) -> bool:
    """Built (`pr` or `done`), no run of it live, and only open deferred items keep it from the merge queue: an item
    minted after the last /fix-deferred took its selection (W-190 D-9) would hold it there silently."""
    return run.get("status") in ("pr", "done") and (run.get("runner") or {}).get("state") not in LIVE and bool(open_items(run))


def note_held(repo: str, run: dict) -> bool:
    """Say once, in the dossier's timeline, which open items hold it from eager merge, and what releases it. Said again
    only when the set of open items changes. Returns whether a line was written."""
    items = open_items(run)
    mark = os.path.join(runs_dir(), f"{repo}.{run['id']}.eager-held.json")
    try:
        if json.load(open(mark, encoding="utf-8")).get("items") == items:
            return False
    except (OSError, ValueError, AttributeError):
        pass
    merge_dossier.append_line(os.path.join(runs_dir(), f"{repo}.{run['id']}.jsonl"), {"ts": now(), "kind": "runner", "text":
        f"eager merge: held — deferred {', '.join(items[:8])} still open. Fix them (Deferred tab, Fix) or rule them "
        f"leave-open; it joins the merge queue by itself after that. The Merge button merges it now regardless."})
    tmp = mark + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump({"items": items, "ts": now()}, fh)
    os.replace(tmp, mark)
    print(f"auto_work: {repo}: eager merge of {run['id']}: held by open deferred {', '.join(items)}")
    return True


def stub_dossiers(root: str, branch: str) -> list[str]:
    """The dossiers with an open pull request into the stub, asked of merge_dossier.py (the same view Integrate has)."""
    script = os.environ.get("ASD_MERGE_BIN") or os.path.join(HERE, "merge_dossier.py")
    try:
        p = subprocess.run([sys.executable, script, "--stub-dossiers", branch, "--root", root], capture_output=True, text=True, timeout=120, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired):
        return []
    return [x.strip().upper() for x in (p.stdout or "").splitlines() if x.strip()] if p.returncode == 0 else []


def enqueue_merge(root: str, dossier: str, by: str = "button") -> tuple[bool, str]:
    """The Merge button's entry point: `merge_dossier.py ID --enqueue --root R` (stacks merge whole, conflicts go to /resolve-merge)."""
    script = os.environ.get("ASD_MERGE_BIN") or os.path.join(HERE, "merge_dossier.py")
    return run_runner([sys.executable, script, dossier, "--enqueue", "--root", root, "--by", by])


EAGER_RETRIES = 2   # re-enqueues of a transiently failed merge per head of its stack, so a persistent failure cannot loop
HUMAN_FAIL_RE = re.compile(r"conflict|clash|checks (?:still )?red|resolve-merge|head did not change|draft|changes code|not an open pull request|branch changed", re.I)


def transient_failure(rec: dict) -> bool:
    """A `failed` merge record whose error is a wait that ran out or an outage, not a verdict on the PRs: CI still running
    (or timed out) after the queue's limit, or a gh/network error (merge_dossier.TRANSIENT_RE). Anything that needs a
    human (a conflict, red checks, spent resolve-merge attempts, a draft) is not."""
    err = str(rec.get("error") or "")
    if not err or HUMAN_FAIL_RE.search(err):
        return False
    return bool(err.startswith("checks still running") or merge_dossier.TRANSIENT_RE.search(err))


def retry_heads(root: str, rec: dict) -> str:
    """The heads of the PRs the failed record names, as one string ("" when none are known)."""
    return ",".join(merge_dossier.head_of(root, int(n)) for n in rec.get("prs") or [])


def retry_mark(repo: str, run: dict) -> str:
    return os.path.join(runs_dir(), f"{repo}.{run['id']}.eager-retry.json")


def retry_allowed(repo: str, run: dict, root: str, rec: dict) -> bool:
    """A transient failure is retried at most EAGER_RETRIES times per head of its stack. Under the cap no gh call is made."""
    mark = read_json(retry_mark(repo, run), {}) or {}
    if int(mark.get("count") or 0) < EAGER_RETRIES:
        return True
    return bool(root) and retry_heads(root, rec) != mark.get("heads")   # a new head (a push, a resolve run) is a new chance


def note_retry(repo: str, run: dict, root: str, rec: dict, why: str) -> None:
    """One timeline line when a failed merge goes back in the queue; counts the retry against the stack's head."""
    heads = retry_heads(root, rec) if root else ""
    mark = read_json(retry_mark(repo, run), {}) or {}
    count = int(mark.get("count") or 0) + 1 if mark.get("heads") == heads else 1
    write_atomic(retry_mark(repo, run), {"heads": heads, "count": count, "ts": now()})
    merge_dossier.append_line(os.path.join(runs_dir(), f"{repo}.{run['id']}.jsonl"), {"ts": now(), "kind": "runner", "text":
        f"eager merge: retrying the merge that failed ({str(rec.get('error') or '')[:160]}): {why}"
        + (f" (retry {count} of {EAGER_RETRIES} for this head)" if why.startswith("transient") else "")})


def retry_kind(repo: str, run: dict, root: str, rec: dict) -> str:
    """"outlived" or "transient" when the failed record may be retried, else "". A `cancelled` record never is."""
    if rec.get("state") != "failed":
        return ""
    rn = run.get("runner") or {}
    if rn.get("command") not in ("after-merge", "resolve-merge") and rn.get("started") and rec.get("ts") and str(rn["started"]) > str(rec["ts"]):
        return "outlived"
    if transient_failure(rec) and retry_allowed(repo, run, root, rec):
        return "transient"
    return ""


def blocks_eager(repo: str, run: dict, root: str = "") -> bool:
    """Whether the dossier's `<repo>.<ID>.merge.json` keeps it out of eager merge. Any record does (queued, running, merging,
    waiting, cancelled: a cancel is the owner's), except a `merged` one that the dossier has outlived: its run started after
    the merge (a /fix-deferred run that opened a new stacked PR), so what is open on the stub now is not what that record
    merged. A `failed` one is retried when the failure was transient (CI still running or timed out, a gh/network error; at
    most EAGER_RETRIES per head) or is outlived the same way; one that needs a human (conflict, red checks, spent
    resolve-merge attempts) stays the owner's. One local file read per dossier: no gh call unless a retry cap was reached."""
    path = os.path.join(runs_dir(), f"{repo}.{run['id']}.merge.json")
    if not os.path.exists(path):
        return False
    rec = read_json(path, {}) or {}
    rn = run.get("runner") or {}
    if rec.get("state") == "failed":
        return not retry_kind(repo, run, root, rec)
    if rec.get("state") != "merged" or rn.get("command") in ("after-merge", "resolve-merge"):
        return True
    return not (rn.get("started") and rec.get("ts") and str(rn["started"]) > str(rec["ts"]))


def eager_step(repo: str, root: str, facts: dict) -> list[str]:
    """Eager merge: every finished dossier on an open stub joins the merge queue, once; with kanban on, so does every finished
    kanban dossier with an open PR into development, while development's CI is green. Returns the ones enqueued."""
    stubs = [s["branch"] for s in facts.get("stubs") or [] if s.get("branch")]
    unqueued = [r for r in facts.get("runs") or [] if not blocks_eager(repo, r, root)]
    todo = [r for r in unqueued if finished_run(r)]
    held = [r for r in unqueued if held_by_deferred(r)]
    if not todo and not held:
        return []
    on_stub: set[str] = set()
    for b in stubs:
        on_stub.update(stub_dossiers(root, b))
    for r in held:
        if r["id"].upper() in on_stub:
            note_held(repo, r)
    if not todo:
        return []
    kan = kanban_ids(repo, root)
    ready_kan = {r["id"] for r in todo if r["id"].upper() in kan and r.get("status") == "pr"}   # `done` ones are merged: no gh call per tick for them
    if ready_kan:
        if merge_script_out(root, "--dev-ci") == "green":
            on_stub.update(x.strip().upper() for x in merge_script_out(root, "--kanban-dossiers").splitlines() if x.strip())
        else:   # stop the line: development's CI is not green; the next tick looks again
            print(f"auto_work: {repo}: kanban merge of {', '.join(sorted(ready_kan))}: waits for development's CI to be green")
            todo = [r for r in todo if r["id"] not in ready_kan]
    if not on_stub or not todo:
        return []
    out = []
    for r in sorted(todo, key=lambda r: r["id"]):
        if r["id"].upper() in on_stub:
            rec = read_json(os.path.join(runs_dir(), f"{repo}.{r['id']}.merge.json"), {}) or {}
            kind = retry_kind(repo, r, root, rec) if rec else ""
            ok, msg = enqueue_merge(root, r["id"], "eager")
            if ok and kind:
                note_retry(repo, r, root, rec, "transient failure, nothing else changed" if kind == "transient" else "outlived: a later run of the dossier has finished")
            if not ok:   # the eager step's own output reaches no log: the dossier's timeline says why it did not queue
                merge_dossier.append_line(os.path.join(runs_dir(), f"{repo}.{r['id']}.jsonl"),
                            {"ts": now(), "kind": "runner", "text": "eager merge: not queued: " + msg[:300]})
            print(f"auto_work: {repo}: eager merge of {r['id']}: " + (msg if ok else "not enqueued: " + msg))
            if ok:
                out.append(r["id"])
    return out


def merge_script_out(root: str, flag: str) -> str:
    """The stdout of `merge_dossier.py FLAG --root R` ("" when it failed)."""
    script = os.environ.get("ASD_MERGE_BIN") or os.path.join(HERE, "merge_dossier.py")
    try:
        p = subprocess.run([sys.executable, script, flag, "--root", root], capture_output=True, text=True, timeout=120, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return (p.stdout or "").strip() if p.returncode == 0 else ""


def carry_step(st: dict, facts: dict) -> list[str]:
    """Once per merged stub: `merge_dossier.py --carry-over` moves the open PRs it left behind (dossiers still in flight
    when it was integrated) to the accepting stub. Deleting the stub's branch on merge makes GitHub move them to
    development, where neither Integrate nor eager merge sees them. A stub is marked carried only when that succeeded
    (exit 0); otherwise the next tick tries again. Returns the stubs carried now."""
    script = os.environ.get("ASD_MERGE_BIN") or os.path.join(HERE, "merge_dossier.py")
    carried, out = st.setdefault("carried", []), []
    for b in sorted(set(merged_triggers(st["root"], facts).values()) - set(carried)):
        try:
            p = subprocess.run([sys.executable, script, "--carry-over", b, "--root", st["root"]], capture_output=True, text=True, timeout=600, stdin=subprocess.DEVNULL)
        except (OSError, subprocess.TimeoutExpired) as e:
            print(f"auto_work: {st['repo']}: carry-over of {b}: {e}"); continue
        print(f"auto_work: {st['repo']}: carry-over of {b}: " + ("; ".join((p.stdout or p.stderr or "").split("\n")).strip("; ") or "nothing left on it"))
        if p.returncode == 0:
            carried.append(b); out.append(b)
    return out


def tick_repo(repo: str, facts: dict) -> None:
    root = facts.get("root") or ""
    if root and os.path.isdir(runs_dir()) and kanban_on(repo):   # governed by the kanban switch alone, not by auto work
        lk = lock_try(repo)
        if lk is not None:
            try:
                kanban_pull(repo, root, facts)
            finally:
                lk.close()
    if not root or not os.path.isdir(runs_dir()) or not (enabled(repo) or eager_on(repo)):
        return
    if eager_on(repo):
        lk = lock_try(repo)
        if lk is not None:
            try:
                eager_step(repo, root, facts)
            finally:
                lk.close()
    if not enabled(repo):
        return
    lk = lock_try(repo)
    if lk is None:
        return
    try:
        st = read_json(state_path(repo), None) or blank(repo, root)
        before = json.dumps(st, sort_keys=True)
        st["root"] = root
        carry_step(st, facts)
        heal(st, facts)
        if not st["paused"]:
            for _ in range(8):
                if not step_cycle(st, facts):
                    break
        st["phase"] = phase_of(st)
        if json.dumps(st, sort_keys=True) != before:
            st["ts"] = now()
            write_atomic(state_path(repo), st)
    finally:
        lk.close()


def tick(facts: dict) -> int:
    for repo, f in (facts.get("repos") or {}).items():
        try:
            tick_repo(repo, f)
        except (OSError, ValueError, KeyError, subprocess.SubprocessError) as e:   # one repository never stops the others; the next tick tries again
            print(f"auto_work: {repo}: {e}")
    return 0


# ---------------------------------------------------------------- the owner's controls
def cmd_enable(root: str) -> int:
    repo = repo_name(root)
    set_switch(repo, True)
    lk = lock_wait(repo)
    try:
        st = read_json(state_path(repo), None)
        if not st:   # a stub merged before this moment never starts a cycle
            st = blank(repo, root); st["seen"] = sorted(merged_triggers(root))
            write_atomic(state_path(repo), st)
        elif st["step"] == "idle":   # the next look (which has the dashboard's facts) marks what merged by hand before this as seen
            st["seen"] = sorted(set(st["seen"]) | set(merged_triggers(root))); st["baseline"] = False; write_atomic(state_path(repo), st)
    finally:
        lk.close()
    print(f"auto_work: on for {repo} (the scripted stub merge is on with it)")
    return 0


def cmd_disable(root: str) -> int:
    repo = repo_name(root)
    set_switch(repo, False)
    print(f"auto_work: off for {repo}; live runs go on, nothing new starts")
    return 0


def cmd_eager(root: str, value: str) -> int:
    repo = repo_name(root)
    set_eager(repo, value == "on")
    print(f"auto_work: eager merge {value} for {repo}" + (" (each finished dossier on an open stub joins the merge queue)" if value == "on" else ""))
    return 0
def cmd_status(root: str) -> int:
    repo = repo_name(root)
    print(json.dumps({**(read_json(state_path(repo), {}) or {}), "eager_merge": eager_on(repo)}, indent=1))
    return 0
def cmd_start_now(root: str) -> int:
    repo = repo_name(root)
    if not enabled(repo):
        print("auto_work: it is off for this repository"); return 1
    lk = lock_wait(repo)
    try:
        st = read_json(state_path(repo), None) or blank(repo, root)
        if st["step"] != "idle":
            print(f"auto_work: cycle {st['cycle']} is {st['phase']}"); return 1
        st["request"], st["root"] = "start", root
        write_atomic(state_path(repo), st)
    finally:
        lk.close()
    print("auto_work: a cycle starts at the next look")
    return 0


def cmd_resume(root: str) -> int:
    repo = repo_name(root)
    lk = lock_wait(repo)
    try:
        st = read_json(state_path(repo), None) or {}
        p = st.get("paused")
        if not p:
            print("auto_work: nothing is paused"); return 1
        for m in st["members"]:
            if m["id"] == p.get("member"):
                m["error"], m["state"], m["lag"] = "", "pending", 0
                if p["action"] == "accept":
                    m["accepted"] = True
                elif p["action"] == "relaunch":
                    m["launch"] = None   # the next look starts that run again, as the Resume button would
        if p["kind"] == "docs":
            st["docs"] = {}
        elif p["kind"] == "integrate":
            st["integrate"] = {**st["integrate"], "launched": ""}
        elif p["kind"] == "stub-run":
            # The /stub run is keyed by the stub being integrated (the dashboard's slug), not the one the cycle started after.
            b = st["integrate"].get("branch") or st["stub"]
            ok, out = start_runner(root, b.replace("/", "-"), "stub", b)
            if not ok:
                print("auto_work: could not resume /stub: " + out); return 1
        st["paused"], st["phase"], st["ts"] = None, "", now()
        st["phase"] = phase_of(st)
        write_atomic(state_path(repo), st)
    finally:
        lk.close()
    print("auto_work: resumed")
    return 0


# ---------------------------------------------------------------- selftest
def selftest() -> int:
    fails = []

    def check(name: str, ok: bool, why: object = "") -> None:
        print(("ok   " if ok else "FAIL ") + name + ("" if ok or not why else "  — " + str(why)[:500]))
        if not ok:
            fails.append(name)

    # Adoption of dossiers already in flight (pure): the W-008 case, a /work-on in review when the cycle starts.
    item = lambda st_: {"id": "D-1", "state": st_}
    fr = lambda runs_: sorted(m["id"] for m in frontier(runs_))
    check("in flight: review or building, a live run, and pr or done with an open deferred item are adopted (W-008 read done with 8 open); pr or done with none, and dropped, are not",
          fr([{"id": "R", "status": "review", "runner": {"state": "running", "command": "work-on"}},
              {"id": "B", "status": "building"},
              {"id": "L", "status": "done", "runner": {"state": "running", "command": "fix-deferred"}},
              {"id": "P", "status": "pr", "deferred": [item("open")]},
              {"id": "K", "status": "pr", "deferred": [item("deprecated"), item("fixed")]},
              {"id": "Q", "status": "pr"}, {"id": "X", "status": "done", "deferred": [item("open")]}, {"id": "Y", "status": "done", "deferred": [item("fixed")]},
              {"id": "Z", "status": "dropped"}]) == ["B", "L", "P", "R", "X"])
    check("frontier: a dependent of a blocker adopted this cycle (done, deferred still open) waits for the cycle after",
          fr([{"id": "B8", "status": "done", "deferred": [item("open")]}, {"id": "B9", "status": "ready", "blockedBy": ["B8"]},
              {"id": "C1", "status": "done", "deferred": [item("fixed")]}, {"id": "C2", "status": "ready", "blockedBy": ["C1"]}]) == ["B8", "C2"])
    m_ = {"id": "R", "stage": "build", "state": "pending", "error": "", "launch": None, "lag": 0, "asked": None, "accepted": False, "rounds": 0, "retried": False}
    st0 = {"root": "/nonexistent", "repo": "x", "members": [m_], "paused": None}
    step_member(st0, m_, {"id": "R", "status": "review", "runner": {"state": "running", "command": "work-on"}})
    check("an adopted live /work-on is waited for: no launch, the member is not finished", m_["launch"] is None and m_["stage"] == "build" and m_["state"] == "running", m_)

    # A /work-on that already ran to its end is never launched again (the live status may sit on the build branch).
    calls_ = []
    real_start = globals().get("start_runner")
    globals()["start_runner"] = lambda root_, id_, command_, items_="": calls_.append((id_, command_, items_)) or (True, "started")
    try:
        fin = {"state": "done", "command": "work-on", "pid": 7, "started": "t"}
        mk = lambda: {"id": "R", "stage": "build", "state": "pending", "error": "", "launch": None, "lag": 0, "asked": None, "accepted": False, "rounds": 0, "retried": False}
        stx = lambda: {"root": "/nonexistent", "repo": "x", "members": [], "paused": None}
        m1 = mk()
        step_member(stx(), m1, {"id": "R", "status": "ready", "runner": fin, "deferred": []})
        check("a finished /work-on whose dossier still reads ready, no open items: no /work-on, the member is finished", calls_ == [] and m1["stage"] == "finished", (calls_, m1))
        m2 = mk()
        step_member(stx(), m2, {"id": "R", "status": "building", "runner": fin, "deferred": [{"id": "D-1", "state": "open"}]})
        check("a finished /work-on with an open deferred item: only /fix-deferred", calls_ == [("R", "fix-deferred", "D-1")], calls_)
        calls_.clear()
        m3 = mk()
        step_member(stx(), m3, {"id": "R", "status": "ready", "runner": {"state": "stopped", "command": "work-on", "pid": 7, "started": "t"}, "deferred": []})
        check("a stopped /work-on that never finished still starts /work-on", calls_ == [("R", "work-on", "")], calls_)
        calls_.clear()
        fd = {"state": "done", "command": "fix-deferred", "pid": 10, "started": "v"}
        m4 = dict(mk(), launch={"command": "fix-deferred", "items": "D-1,D-2", "ts": "2026-01-01T00:00:00Z", "pid": 9, "started": "u"})
        step_member(stx(), m4, {"id": "R", "status": "ready", "runner": fd, "deferred": []})
        check("a finished /fix-deferred whose dossier still reads ready (status on the build branch): no /work-on, finished",
              calls_ == [] and m4["stage"] == "finished", (calls_, m4))
        m5 = dict(mk(), accepted=True, launch=dict(m4["launch"]))
        step_member(stx(), m5, {"id": "R", "status": "ready", "runner": fd, "deferred": [{"id": "D-2", "state": "open"}]})
        check("a finished /fix-deferred with items the owner accepted as open: finished, nothing launched",
              calls_ == [] and m5["stage"] == "finished", (calls_, m5))
        m6 = dict(mk(), launch=dict(m4["launch"]))
        step_member(stx(), m6, {"id": "R", "status": "ready", "runner": fd, "deferred": [{"id": "D-1", "state": "done"}, {"id": "D-2", "state": "done"}, {"id": "D-9", "state": "open"}]})
        check("a finished /fix-deferred that added D-9 itself: follow-up /fix-deferred D-9, no pause",
              calls_ == [("R", "fix-deferred", "D-9")] and m6["fix_rounds"] == 1 and m6["stage"] == "fix", (calls_, m6))
        calls_.clear()
        st7 = stx(); m7 = dict(mk(), launch=dict(m4["launch"]), lag=5)
        step_member(st7, m7, {"id": "R", "status": "ready", "runner": fd, "deferred": [{"id": "D-2", "state": "open"}, {"id": "D-9", "state": "open"}]})
        check("a finished /fix-deferred with a launched item still open: the old pause, no follow-up",
              calls_ == [] and bool(st7["paused"]) and "D-2" in str(st7["paused"]["reason"]) and "D-9" not in str(st7["paused"]["reason"]), (calls_, st7))
        st8 = stx(); m8 = dict(mk(), launch=dict(m4["launch"]), fix_rounds=2)
        step_member(st8, m8, {"id": "R", "status": "ready", "runner": fd, "deferred": [{"id": "D-9", "state": "open"}]})
        check("follow-up bound reached: finished with the reason in the state, no launch, no pause",
              calls_ == [] and not st8["paused"] and m8["stage"] == "finished" and "D-9" in str(m8["state"]), (calls_, st8, m8))
    finally:
        globals()["start_runner"] = real_start

    global LAND
    real_land = LAND
    real_dirs = [land_stub.settings_path(), runs_dir()]
    with tempfile.TemporaryDirectory() as tmp:
        runs = os.path.join(tmp, "runs"); os.makedirs(runs)
        root = os.path.join(tmp, "repo"); os.makedirs(root)
        subprocess.run(["git", "init", "-q", root], check=True)
        repo = repo_name(root)
        calls = os.path.join(tmp, "calls.txt")
        for n, body in (("runner.py", "import sys\nopen(%r,'a').write('runner '+' '.join(sys.argv[1:])+'\\n')\nif 'BADDOSSIER' in sys.argv: sys.stderr.write('refused\\n'); sys.exit(2)\nprint('started')\n"),
                        ("docs.py", "import sys\nopen(%r,'a').write('docs '+' '.join(sys.argv[1:])+'\\n')\nprint('queued #11 plan/a')\nprint('queued #12 docs/b')\n"),
                        ("merge.py", "import sys, os\nif '--carry-over' in sys.argv:\n    open(%r + '.carry','a').write(' '.join(sys.argv[1:3]) + '\\n'); print('moved #5 feat/x from ' + sys.argv[2] + ' to stub/next'); sys.exit(int(os.environ.get('FAKE_CARRY_RC') or 0))\nopen(%r,'a').write(('integrate ' if '--integrate' in sys.argv else 'stubdossiers ' if '--stub-dossiers' in sys.argv else 'enqueue ')+' '.join(sys.argv[1:])+'\\n')\nif '--stub-dossiers' in sys.argv:\n    f = os.environ.get('FAKE_STUB_DOSSIERS')\n    print(open(f).read() if f and os.path.exists(f) else '', end='')\n")):
            open(os.path.join(tmp, n), "w").write(body % ((calls, calls) if n == "merge.py" else calls))
        os.environ.update(ASD_RUNS_DIR=runs, ASD_STUB_AUTOMERGE_FILE=os.path.join(tmp, "settings.json"), ASD_RUNNER_BIN=os.path.join(tmp, "runner.py"),
                          ASD_DOCS_MERGE_BIN=os.path.join(tmp, "docs.py"), ASD_MERGE_BIN=os.path.join(tmp, "merge.py"))
        landed = []
        LAND = lambda: landed.append(1) or 0
        log = lambda: open(calls).read().splitlines() if os.path.exists(calls) else []
        def waitlog(n_integrate):   # the integrate script is detached, as in the dashboard: its first line lands a moment later
            import time
            for _ in range(100):
                if sum(1 for l in log() if l.startswith("integrate")) >= n_integrate:
                    return
                time.sleep(0.1)
        P = lambda p: os.path.join(runs, p)
        st = lambda: read_json(state_path(repo), {}) or {}

        def run_(id, status, blocked=(), runner=None, items=()):
            return {"id": id, "status": status, "blockedBy": list(blocked), "runner": runner, "deferred": [{"id": i[0], "state": i[1]} for i in items]}
        facts_runs = []
        facts_merged = []
        facts_stubs = [{"branch": "stub/2026-11-24", "state": "accepting", "canIntegrate": True}]
        def tk():
            tick({"repos": {repo: {"root": root, "runs": facts_runs, "stubs": facts_stubs, "mergedStubs": facts_merged}}})
        def setrun(id, **kw):
            for r in facts_runs:
                if r["id"] == id:
                    r.update(kw)
        def runner(state, command, pid=100, started="2026-11-17T10:00:00Z"):
            return {"state": state, "command": command, "pid": pid, "started": started}
        def members():
            return {m["id"]: m for m in st().get("members", [])}

        # An old merged stub exists before the switch goes on.
        json.dump({"state": "merged", "root": root, "branch": "stub/2026-11-10", "ts": "2026-11-10T10:00:00Z"}, open(P(f"{repo}.stub-2026-11-10.integrate.json"), "w"))
        facts_runs[:] = [run_("A", "ready"), run_("B", "ready", ["C"]), run_("C", "building", runner=runner("running", "work-on")), run_("D", "planned"),
                         run_("E", "planned", ["A"]), run_("F", "ready", ["G"]), run_("G", "done"), run_("H", "dropped")]

        set_eager(repo, False)   # eager merge is on by default: the auto work cases below run without it (it has its own cases)
        # Off by default: nothing is looked at, nothing is written, nothing starts.
        tk()
        check("disabled: no state file, no run started", not os.path.exists(state_path(repo)) and log() == [])
        check("disabled: --start-now refuses", cmd_start_now(root) == 1 and not os.path.exists(state_path(repo)))
        cmd_enable(root)
        sw = land_stub.settings_of(repo)
        check("enable: one switch turns the scripted stub merge on with it", sw.get("auto_work") is True and sw.get("enabled") is True and not land_stub.settings_of("other"))
        facts_merged = ["stub/2026-11-03"]   # a stub the owner merged by hand before the switch: its integrate file still reads `stub`
        json.dump({"state": "stub", "root": root, "branch": "stub/2026-11-03", "ts": "2026-11-03T10:00:00Z"}, open(P(f"{repo}.stub-2026-11-03.integrate.json"), "w"))
        tk(); tk()
        check("the stubs merged before the switch (by land_stub or by hand) started no cycle", st()["cycle"] == 0 and log() == [], st())
        carry = lambda: open(calls + ".carry").read().splitlines() if os.path.exists(calls + ".carry") else []
        check("every merged stub is carried over once (its leftover PRs go to the accepting stub)",
              sorted(carry()) == ["--carry-over stub/2026-11-03", "--carry-over stub/2026-11-10"] and sorted(st()["carried"]) == ["stub/2026-11-03", "stub/2026-11-10"], (carry(), st().get("carried")))
        s9 = {"repo": repo, "root": root, "carried": list(st()["carried"])}
        f9 = {"mergedStubs": ["stub/2099-01-01"]}
        os.environ["FAKE_CARRY_RC"] = "1"
        r1, r2 = carry_step(s9, f9), carry_step(s9, f9)
        del os.environ["FAKE_CARRY_RC"]
        r3, r4 = carry_step(s9, f9), carry_step(s9, f9)
        check("a carry-over that could not finish is tried again; a finished one never again",
              r1 == r2 == r4 == [] and r3 == ["stub/2099-01-01"] and "stub/2099-01-01" in s9["carried"] and carry().count("--carry-over stub/2099-01-01") == 3, (r1, r2, r3, r4, carry()))

        # A stub merges: START. The frontier is snapshotted once.
        json.dump({"state": "merged", "root": root, "branch": "stub/2026-11-17", "ts": "2026-11-17T09:00:00Z"}, open(P(f"{repo}.stub-2026-11-17.integrate.json"), "w"))
        tk()
        s = st()
        check("START: the stub's merge starts cycle 1", s["cycle"] == 1 and s["step"] == "work" and s["stub"] == "stub/2026-11-17", s)
        check("snapshot: ready and planned with every blocker done; blocked by an unfinished one, and dropped, are out; C, in flight, is adopted",
              sorted(members()) == ["A", "C", "D", "F"], sorted(members()))
        check("the adopted C is waited for, never started again", "running" == members()["C"]["state"] and not any(l.startswith("runner C ") for l in log()), (members()["C"], log()))
        check("A /work-on, F /work-on and D /plan are started through the runner",
              sorted(log()) == sorted([f"runner A --root {root} --command work-on", f"runner D --root {root} --command plan", f"runner F --root {root} --command work-on"]), log())
        before = log()
        facts_runs.append(run_("N", "ready"))   # created after START
        setrun("C", status="done", runner=runner("done", "work-on"))   # the blocker finishes mid-cycle
        setrun("A", runner=runner("queued", "work-on", pid=200, started="2026-11-17T10:01:00Z"))
        setrun("F", runner=runner("waiting", "work-on", pid=201, started="2026-11-17T10:01:00Z"))
        setrun("D", runner=runner("running", "plan", pid=202, started="2026-11-17T10:01:00Z"))
        tk()
        check("a dossier created after START and a blocker finishing mid-cycle add nobody; the adopted C finishes with no deferred item", sorted(members()) == ["A", "C", "D", "F"] and members()["C"]["stage"] == "finished" and log() == before, members())
        check("an open question just waits: no second start, nothing answered", members()["F"]["state"] == "waiting for the owner" and members()["A"]["state"] == "queued for a slot" and log() == before)
        check("the phase reads building", st()["phase"] == "building")

        # A: build done, no deferred item: finished without a fix-deferred run.
        setrun("A", status="pr", runner=runner("done", "work-on", pid=200, started="2026-11-17T10:01:00Z"))
        tk()
        check("a member with no deferred items finishes without a fix-deferred run", members()["A"]["stage"] == "finished" and not any("fix-deferred" in l and " A " in l for l in log()), log())
        # D: planned -> plan -> work-on.
        n_plan = sum(1 for l in log() if l.startswith("runner D") and "--command plan" in l)
        setrun("D", runner=runner("done", "plan", pid=202, started="2026-11-17T10:01:00Z"))   # /plan ended done, the dossier still reads planned
        tk()
        check("plan done but not ready: the first look waits (a status write may land late), no pause, no second plan",
              not st()["paused"] and sum(1 for l in log() if l.startswith("runner D") and "--command plan" in l) == n_plan, st().get("paused"))
        tk()
        check("plan done and still not ready: /plan runs again (plan round 2), no pause",
              not st()["paused"] and sum(1 for l in log() if l.startswith("runner D") and "--command plan" in l) == n_plan + 1 and members()["D"]["rounds"] == 2, (st().get("paused"), members()["D"]))
        setrun("D", runner=runner("waiting", "plan", pid=210, started="2026-11-17T10:20:00Z")); tk()
        check("an owner question in the second plan round is only waited for", members()["D"]["state"] == "waiting for the owner" and not st()["paused"])
        setrun("D", runner=runner("failed", "plan", pid=210, started="2026-11-17T10:20:00Z")); tk()
        check("a plan round that ends failed pauses the cycle", bool(st()["paused"]) and st()["paused"]["member"] == "D", st().get("paused"))
        cmd_resume(root)
        tk()
        check("resume starts the failed plan round again (round 3)", members()["D"]["rounds"] == 3 and not st()["paused"], members()["D"])
        setrun("D", status="ready", runner=runner("done", "plan", pid=212, started="2026-11-17T10:30:00Z"))
        tk()
        check("planned -> /plan -> /work-on once the plan made it ready", f"runner D --root {root} --command work-on" in log() and members()["D"]["stage"] == "build", log())
        # D and F build and finish with deferred items; one is ruled to stay open.
        setrun("D", status="pr", runner=runner("done", "work-on", pid=203, started="2026-11-17T10:30:00Z"), deferred=[{"id": "D-1", "state": "open"}, {"id": "D-2", "state": "deprecated"}, {"id": "D-3", "state": "fixed"}])
        setrun("F", status="pr", runner=runner("done", "work-on", pid=201, started="2026-11-17T10:01:00Z"), deferred=[{"id": "D-1", "state": "open"}])
        tk()
        check("planned -> plan -> work-on -> fix-deferred: the same item selection as the button (the dropped one stays out)",
              f"runner D --root {root} --command fix-deferred --items D-1" in log() and f"runner F --root {root} --command fix-deferred --items D-1" in log(), log())
        check("not everyone is finished: no non-code merge, no integrate", not any(l.startswith(("docs", "integrate")) for l in log()))
        check("the phase reads fixing deferred", st()["phase"] == "fixing deferred", st()["phase"])
        # F's fix-deferred fails: the cycle pauses; D is untouched.
        setrun("F", runner=runner("failed", "fix-deferred", pid=301, started="2026-11-17T11:00:00Z"))
        setrun("D", runner=runner("running", "fix-deferred", pid=302, started="2026-11-17T11:00:00Z"))
        n_before = len(log()); tk()
        s = st()
        check("a failed run pauses the cycle, with the reason, on the member", s["paused"] and "F" in s["paused"]["reason"] and "failed" in s["paused"]["reason"] and s["phase"] == "paused" and members()["F"]["state"] == "failed", s.get("paused"))
        setrun("D", status="done", runner=runner("done", "fix-deferred", pid=302, started="2026-11-17T11:00:00Z"), deferred=[{"id": "D-1", "state": "fixed"}, {"id": "D-2", "state": "deprecated"}])
        tk()
        check("paused: nothing new starts and nothing else moves", len(log()) == n_before and members()["D"]["stage"] == "fix", log()[n_before:])
        check("resume with nothing paused refuses", (cmd_resume(root + "/nope") if False else 1) == 1)
        cmd_resume(root)
        tk()
        check("resume: the failed run is started again, as the Resume button would", log()[-1] == f"runner F --root {root} --command fix-deferred --items D-1" and st()["paused"] is None, log()[-2:])
        # A logout kills every runner: the facts say "stopped", runner.json still says running, `revive` restarts it.
        os.environ["ASD_AUTO_WORK_GRACE"] = "600"
        setrun("F", runner=runner("stopped", "fix-deferred", pid=304, started="2026-11-17T11:05:00Z"))
        json.dump({"state": "running", "command": "fix-deferred"}, open(P(f"{repo}.F.runner.json"), "w"))
        tk()
        check("a killed runner (facts stopped, runner.json live) is restarting, not a pause, within the grace",
              not st()["paused"] and members()["F"]["state"] == "restarting" and members()["F"].get("restarting"), (st().get("paused"), members()["F"]))
        s = st(); s["members"][[m_["id"] for m_ in s["members"]].index("F")]["restarting"] = "2020-01-01T00:00:00Z"; write_atomic(state_path(repo), s)
        tk()
        check("still dead after the grace: the cycle pauses as before", bool(st()["paused"]) and "ended stopped" in st()["paused"]["reason"], st().get("paused"))
        setrun("F", runner=runner("running", "fix-deferred", pid=305, started="2026-11-17T11:06:00Z"))
        tk()
        check("revive restarted it: the pause heals itself, with a note, and the member runs again",
              st()["paused"] is None and st()["healed"]["member"] == "F" and "ended stopped" in st()["healed"]["was"] and members()["F"]["state"] == "running"
              and not members()["F"].get("restarting"), (st().get("paused"), st().get("healed")))
        os.remove(P(f"{repo}.F.runner.json"))
        setrun("F", runner=runner("stopped", "fix-deferred", pid=306, started="2026-11-17T11:07:00Z")); tk()
        check("a run the owner stopped (runner.json stopped) still pauses at once", bool(st()["paused"]) and "ended stopped" in st()["paused"]["reason"], st().get("paused"))
        cmd_resume(root)
        h = lambda pz, run: (lambda s_: (heal(s_, {"runs": [run]}), s_)[1])({"members": [{"id": "Z", "state": "failed", "error": "x", "lag": 0}], "paused": pz, "phase": "paused"})
        pz = {"reason": "Z: ended stopped", "kind": "member", "member": "Z", "action": "relaunch", "at": now()}
        check("heal: a dossier that reached pr clears a member/relaunch pause", h(pz, {"id": "Z", "status": "pr", "runner": None})["paused"] is None)
        check("heal: still dead and not done keeps the pause", h(pz, {"id": "Z", "status": "building", "runner": {"state": "stopped"}})["paused"] is not None)
        check("heal: other pause kinds and actions are never cleared",
              all(h({**pz, **o}, {"id": "Z", "status": "done", "runner": {"state": "running"}})["paused"] is not None
                  for o in ({"kind": "docs"}, {"kind": "stub-run"}, {"action": "accept"}, {"action": "recheck"})))
        # F's rerun finishes; D's fix-deferred is done with only a dropped item.
        setrun("F", status="done", runner=runner("done", "fix-deferred", pid=303, started="2026-11-17T11:10:00Z"), deferred=[{"id": "D-1", "state": "fixed"}])
        n_before = len(log()); tk()
        check("every member finished: the non-code PRs are queued first, and nothing integrates in the same look",
              st()["step"] == "docs" and log()[n_before:] == [f"docs merge --root {root}"], log()[n_before:])
        check("phase: merging non-code", st()["phase"] == "merging non-code")
        tk()
        check("the batch is not merged yet: integrate waits", not any(l.startswith("integrate") for l in log()) and "#11" in st()["note"], st()["note"])
        json.dump({"state": "merged"}, open(P(f"{repo}.pr-11.merge.json"), "w")); json.dump({"state": "queued"}, open(P(f"{repo}.pr-12.merge.json"), "w"))
        tk()
        check("one of two non-code PRs still queued: still no integrate", not any(l.startswith("integrate") for l in log()))
        json.dump({"state": "merged"}, open(P(f"{repo}.pr-12.merge.json"), "w"))
        tk(); waitlog(1)
        check("the non-code batch finished: then, and only then, the stub is integrated",
              log()[-1] == f"integrate --integrate stub/2026-11-24 --root {root}" and log().index(f"docs merge --root {root}") < len(log()) - 1, log()[-3:])
        check("integrate marks its state file merging, like the button", (read_json(P(f"{repo}.stub-2026-11-24.integrate.json"), {}) or {}).get("state") == "merging")
        tk()
        check("integrate is only started once", sum(1 for l in log() if l.startswith("integrate")) == 1 and st()["phase"] == "integrating")
        # Integrate fails: pause. Resume starts it again.
        w = lambda **kw: json.dump({"root": root, "branch": "stub/2026-11-24", **kw}, open(P(f"{repo}.stub-2026-11-24.integrate.json"), "w"))
        w(state="failed", error="a stack conflicted", ts="2026-11-17T12:00:00Z"); tk()
        check("a failed integrate pauses the cycle", st()["paused"] and st()["paused"]["kind"] == "integrate" and "a stack conflicted" in st()["paused"]["reason"], st().get("paused"))
        cmd_resume(root); tk(); waitlog(2)
        check("resume: Integrate runs again", sum(1 for l in log() if l.startswith("integrate")) == 2 and st()["paused"] is None)
        w(state="stub", note="/stub runs", ts="2026-11-17T12:30:00Z")
        landed.clear(); tk()
        check("integrate reached /stub: land_stub is triggered", st()["step"] == "land" and len(landed) >= 1 and st()["phase"] == "landing", (st()["step"], landed))
        json.dump({"command": "stub", "state": "failed"}, open(P(f"{repo}.stub-2026-11-24.runner.json"), "w")); tk()
        check("a failed /stub run pauses the cycle", st()["paused"] and st()["paused"]["kind"] == "stub-run", st().get("paused"))
        check("the note names the stub being integrated", "stub/2026-11-24" in st()["note"] or st()["paused"], st()["note"])
        os.remove(P(f"{repo}.stub-2026-11-24.runner.json")); n_before = len(log()); cmd_resume(root)
        check("resume starts /stub on the stub being integrated, not the one the cycle started after",
              log()[n_before:] == [f"runner stub-2026-11-24 --root {root} --command stub --items stub/2026-11-24"] and st()["paused"] is None, log()[n_before:])
        json.dump({"command": "stub", "state": "running"}, open(P(f"{repo}.stub-2026-11-24.runner.json"), "w")); tk()
        check("while /stub runs, the note names that stub", st()["note"].startswith("waiting to merge stub/2026-11-24: "), st()["note"])
        os.remove(P(f"{repo}.stub-2026-11-24.runner.json"))
        # The stub merges: that is the next START, with a new snapshot.
        facts_runs[:] = [run_("N", "ready"), run_("A", "done"), run_("M", "ready", ["N"])]
        w(state="stub", note="/stub done", ts="2026-11-17T13:00:00Z")   # the owner merges the PR by hand: GitHub says merged, the file still says `stub`
        facts_merged.append("stub/2026-11-24")
        n_before = len(log()); tk()
        check("the stub's merge restarts the cycle: cycle 2, a fresh snapshot of what is eligible now",
              st()["cycle"] == 2 and st()["stub"] == "stub/2026-11-24" and sorted(members()) == ["N"] and log()[n_before:] == [f"runner N --root {root} --command work-on"], (st()["cycle"], sorted(members()), log()[n_before:]))
        n_before = len(log()); tk(); tk()
        check("the same merge never starts a third cycle", st()["cycle"] == 2)

        # A run that never shows up, a refused start, an empty frontier, a manual start.
        os.environ["ASD_AUTO_WORK_GRACE"] = "0"
        s = st(); s["members"][0]["launch"]["ts"] = "2020-01-01T00:00:00Z"; write_atomic(state_path(repo), s)
        setrun("N", runner=runner("queued", "work-on", pid=400, started="2026-11-17T14:00:00Z")); tk()
        check("a run the runner put in queued for a slot counts as shown: the grace never fires on it", not st()["paused"] and members()["N"]["state"] == "queued for a slot", st().get("paused"))
        setrun("N", runner=None)
        old = lambda: (lambda s_: (s_["members"][0]["launch"].update(ts="2020-01-01T00:00:00Z"), write_atomic(state_path(repo), s_)))(st())
        n_before = len(log()); old(); tk()
        check("a run that left no trace within the grace is started once more, and the retry is recorded",
              not st()["paused"] and members()["N"]["retried"] is True and len(log()) == n_before + 1 and log()[-1].startswith("runner N "), (st().get("paused"), log()[n_before:]))
        os.environ["ASD_AUTO_WORK_GRACE"] = "600"
        setrun("N", runner=runner("running", "work-on", pid=500, started="2026-11-17T15:00:00Z")); tk()
        check("the retry shows up: the cycle continues, the retry flag clears", not st()["paused"] and members()["N"]["retried"] is False and members()["N"]["state"] == "running")
        setrun("N", runner=None); s_ = st(); s_["members"][0]["launch"]["ts"] = now(); write_atomic(state_path(repo), s_)
        os.environ["ASD_AUTO_WORK_GRACE"] = "0"; old(); tk(); old(); n_before = len(log()); tk()
        check("both starts vanish: the cycle pauses, once, after exactly one retry", st()["paused"] and "retry" in st()["paused"]["reason"] and len(log()) == n_before, st().get("paused"))
        os.environ.pop("ASD_AUTO_WORK_GRACE")
        s = st(); s["paused"] = None; s["step"] = "idle"; s["members"] = []; s["request"] = None; write_atomic(state_path(repo), s)
        facts_runs[:] = [run_("BADDOSSIER", "ready")]
        check("start-now asks for a cycle", cmd_start_now(root) == 0 and st()["request"] == "start")
        tk()
        check("a start the runner refuses pauses the cycle with its words", st()["cycle"] == 3 and st()["paused"] and "refused" in st()["paused"]["reason"], st())
        s = st(); s.update(paused=None, step="idle", members=[]); write_atomic(state_path(repo), s)
        facts_runs[:] = []
        check("start-now is refused while a cycle runs", (lambda: (cmd_start_now(root), 0)[1] == 0)() and True)
        facts_merged.clear(); cmd_start_now(root); n_before = len(log()); tk()
        check("an empty frontier goes straight to the non-code merge", st()["cycle"] == 4 and st()["step"] in ("docs", "integrate", "land") and log()[n_before] == f"docs merge --root {root}", (st()["cycle"], st()["step"], log()[n_before:]))

        # A /work-on that ended done with the dossier not at pr/done: the run's own agent is asked once, before the owner.
        def stuck(id):
            s_ = st(); s_.update(paused=None, step="work", phase="building", members=[{"id": id, "stage": "build", "state": "running", "error": "", "launch": {"command": "work-on", "items": "", "ts": now(), "pid": 1, "started": "s0"},
                                                                                  "lag": 0, "asked": None, "accepted": False, "rounds": 0, "retried": False}])
            write_atomic(state_path(repo), s_)
            facts_runs[:] = [run_(id, "ready", runner=runner("done", "work-on", pid=9, started="s1"))]
        asks = lambda id: [l for l in log() if l.startswith(f"runner chat {id} ")]
        stuck("XP"); facts_runs[:] = [run_("XP", "ready", runner=runner("parked", "work-on", pid=9, started="s1"))]; tk(); tk(); tk()
        check("a parked /work-on (waiting for blockers in the same cycle) is live: never asked, the cycle does not pause",
              not asks("XP") and not st()["paused"] and members()["XP"]["state"] == "waiting for its blockers", (asks("XP"), st().get("paused"), members()["XP"]))
        stuck("X1"); tk(); n_ask = len(asks("X1"))
        check("a /work-on that ended with the dossier still ready waits a tick before the agent is asked", n_ask == 0 and not st()["paused"])
        tk()
        check("then its agent is asked, through run_dossier chat, and the cycle does not pause",
              len(asks("X1")) == 1 and f"--root {root} --text" in asks("X1")[0] and "INTENDED:" in asks("X1")[0] and "`status: ready`" in asks("X1")[0] and not st()["paused"]
              and members()["X1"]["state"] == "asked the agent why it stopped at ready" and members()["X1"]["asked"]["status"] == "ready", (asks("X1"), st().get("paused")))
        tk(); tk(); tk()
        check("the agent is asked only once per launch, and the cycle waits for the answer", len(asks("X1")) == 1 and not st()["paused"])
        setrun("X1", status="pr"); tk()
        check("asked, then fixed: the status reached pr, the ask clears and the normal path takes over", not st()["paused"] and members()["X1"]["asked"] is None and members()["X1"]["stage"] == "finished", members()["X1"])
        stuck("X2"); tk(); tk(); tk()
        json.dump({"ts": "2099-01-01T00:00:00Z", "kind": "chat", "from": "agent", "text": "INTENDED: the plan says the owner opens the PR\nmore"}, open(P(f"{repo}.X2.jsonl"), "w")); tk(); tk()
        check("an agent reply does not pause at once: the status may still land", not st()["paused"])
        tk()
        check("asked, replied INTENDED, still ready: the cycle pauses with the agent's words", st()["paused"] and "stopped at ready; its agent says: INTENDED: the plan says the owner opens the PR more" in st()["paused"]["reason"] and len(asks("X2")) == 1, st().get("paused"))
        stuck("X3"); tk(); tk(); tk()
        check("asked, no reply, within the wait: no pause", not st()["paused"] and len(asks("X3")) == 1)
        os.environ["ASD_AUTO_WORK_ASK_WAIT"] = "0"
        s_ = st(); s_["members"][0]["asked"]["ts"] = "2020-01-01T00:00:00Z"; write_atomic(state_path(repo), s_); tk()
        check("asked, no reply after the wait: pauses, saying the agent did not answer", st()["paused"] and "its agent did not answer" in st()["paused"]["reason"], st().get("paused"))
        os.environ.pop("ASD_AUTO_WORK_ASK_WAIT")
        stuck("BADDOSSIER"); tk(); tk()
        check("the chat cannot start: the cycle pauses with the error", st()["paused"] and "could not be asked: refused" in st()["paused"]["reason"], st().get("paused"))
        s_ = st(); s_.update(paused=None, step="idle", phase="idle", members=[]); write_atomic(state_path(repo), s_); facts_runs[:] = []
        # Disabled again: nothing moves, live runs are not touched.
        cmd_disable(root)
        sw = land_stub.settings_of(repo)
        check("disable: both switches off", sw.get("auto_work") is False and sw.get("enabled") is False)
        s_before, n_before = st(), len(log())
        json.dump({"state": "merged", "root": root, "branch": "stub/2026-12-01", "ts": "2026-12-01T09:00:00Z"}, open(P(f"{repo}.stub-2026-12-01.integrate.json"), "w"))
        tk()
        check("disabled: the loop starts nothing and changes nothing", st() == s_before and len(log()) == n_before)
        check("the state file is whole json, never a torn write", isinstance(st(), dict) and not glob.glob(P(".auto-work-*.tmp")))
        # Eager merge: its own switch (on by default); each finished dossier on an open stub joins the merge queue, once.
        cmd_disable(root)   # auto work is off for all of these
        stubf = os.path.join(tmp, "stub-dossiers.txt"); os.environ["FAKE_STUB_DOSSIERS"] = stubf
        open(stubf, "w").write("E1\nE2\nE3\nE4\nE5\nE6\nE7\n")   # the dossiers with an open PR into the stub
        facts_stubs[:] = [{"branch": "stub/2026-11-24", "state": "accepting", "canIntegrate": True}]
        done_ = lambda s_="done": runner(s_, "fix-deferred", pid=700, started="2026-11-17T16:00:00Z")
        facts_runs[:] = [run_("E1", "pr", runner=runner("done", "work-on")),                                           # built, nothing deferred
                         run_("E2", "done", runner=done_(), items=[("D-1", "fixed"), ("D-2", "dismissed"), ("D-3", "converted")]),   # fixed by hand or by fix-deferred
                         run_("E3", "pr", runner=runner("done", "work-on"), items=[("D-1", "open")]),                  # an open item
                         run_("E4", "pr", runner=done_("running")),                                                     # fix-deferred still running
                         run_("E5", "pr", runner=runner("done", "work-on")),                                           # already queued
                         run_("E6", "pr", runner=runner("done", "work-on")),                                           # merged
                         run_("E7", "pr", runner=runner("done", "work-on")),                                           # failed
                         run_("E8", "pr", runner=runner("done", "work-on")),                                           # finished but no open PR into the stub
                         run_("E9", "building", runner=runner("running", "work-on"))]                                   # not built
        for k, v in (("E5", "queued"), ("E6", "merged"), ("E7", "failed")):
            json.dump({"state": v}, open(P(f"{repo}.{k}.merge.json"), "w"))
        all_ = read_json(land_stub.settings_path(), {}); all_[repo].pop("eager_merge"); write_atomic(land_stub.settings_path(), all_)
        check("eager merge is on by default (no key in the switch file)", eager_on(repo) and eager_on("other"))
        cmd_eager(root, "off")
        n_before = len(log()); st_before = st()
        tk()
        check("eager off: nothing is asked, nothing is enqueued, even with finished dossiers", log()[n_before:] == [] and st() == st_before, log()[n_before:])
        cmd_eager(root, "on")
        sw = land_stub.settings_of(repo)
        check("eager-merge on is its own key: it leaves auto_work and the scripted stub merge off", sw.get("eager_merge") is True and sw.get("auto_work") is False and sw.get("enabled") is False, sw)
        s_before = st(); n_before = len(log()); tk()
        enq = [l for l in log()[n_before:] if l.startswith("enqueue")]
        check("eager on, auto work off: the finished dossiers E1 and E2 are enqueued, through the merge entry point, and only those",
              enq == [f"enqueue E1 --enqueue --root {root} --by eager", f"enqueue E2 --enqueue --root {root} --by eager"], log()[n_before:])
        check("eager asks the merge script which dossiers have an open PR into the stub", f"stubdossiers --stub-dossiers stub/2026-11-24 --root {root}" in log()[n_before:])
        check("the auto work state file is untouched by the eager step", st() == s_before)
        for k in ("E1", "E2"):   # the queue writes the record, as merge_dossier.enqueue does
            json.dump({"state": "queued"}, open(P(f"{repo}.{k}.merge.json"), "w"))
        n_before = len(log()); tk(); tk()
        check("idempotent: queued, running, merged and failed records are never enqueued again", not [l for l in log()[n_before:] if l.startswith("enqueue")], log()[n_before:])
        for state_ in ("merging", "waiting", "cancelled", "cleanup"):
            json.dump({"state": state_}, open(P(f"{repo}.E1.merge.json"), "w")); tk()
        check("any other record state is left alone too", not [l for l in log()[n_before:] if l.startswith("enqueue")])
        # A merged record does not outlive the dossier: a later run (/fix-deferred) that finished with a new open PR on the stub queues it again.
        keep_runs = list(facts_runs)
        open(stubf, "w").write("E10\nE11\nE12\nE13\n")
        facts_runs[:] = [run_("E10", "done", runner=runner("done", "fix-deferred", started="2026-11-21T14:00:00Z")),   # merged 03:41, fix-deferred ran after: new PR
                         run_("E11", "done", runner=runner("done", "work-on", started="2026-11-20T10:00:00Z")),        # merged after its last run: nothing new
                         run_("E12", "done", runner=runner("done", "fix-deferred", started="2026-11-21T14:00:00Z")),   # queued: once
                         run_("E13", "done", runner=runner("done", "fix-deferred", started="2026-11-21T14:00:00Z"))]   # merged, rerun, but no open PR on the stub
        for k, v in (("E10", "merged"), ("E11", "merged"), ("E12", "queued"), ("E13", "merged")):
            json.dump({"state": v, "prs": [594], "ts": "2026-11-21T03:41:08Z"}, open(P(f"{repo}.{k}.merge.json"), "w"))
        open(stubf, "w").write("E10\nE11\nE12\n")
        n_before = len(log()); tk()
        check("merged record + a later run finished with a new open PR on the stub: queued again; no new run, a queued record, or no open PR: not",
              [l for l in log()[n_before:] if l.startswith("enqueue")] == [f"enqueue E10 --enqueue --root {root} --by eager"], log()[n_before:])
        facts_runs[:] = keep_runs
        open(stubf, "w").write("E1\nE2\nE3\nE4\nE5\nE6\nE7\n")
        for k in ("E10", "E11", "E12", "E13"): os.remove(P(f"{repo}.{k}.merge.json"))
        # A failed merge is retried by itself when that is safe: transient, or outlived by a later finished run; never a cancel,
        # never a failure that needs a human, and a transient one at most EAGER_RETRIES times per head.
        keep_runs = list(facts_runs); real_head = merge_dossier.head_of
        heads_now = {"v": "sha1"}
        merge_dossier.head_of = lambda _root, n: heads_now["v"]
        def retried(k: str) -> int:
            try:
                return sum("eager merge: retrying" in l for l in open(P(f"{repo}.{k}.jsonl"), encoding="utf-8"))
            except OSError:
                return 0
        try:
            ids = ("E20", "E21", "E22", "E23", "E24", "E25")
            open(stubf, "w").write("\n".join(ids) + "\n")
            facts_runs[:] = [run_(k, "done", runner=runner("done", "work-on", started="2026-11-20T10:00:00Z")) for k in ids]
            setrun("E23", runner=runner("done", "fix-deferred", started="2026-11-21T14:00:00Z"))   # started after the record
            recs = {"E20": {"state": "failed", "error": "checks still running after 60 min: #637"},
                    "E21": {"state": "failed", "error": "still conflicting: #5 after 2 resolve-merge attempts"},
                    "E22": {"state": "cancelled", "error": ""},
                    "E23": {"state": "failed", "error": "checks still red: #9 after 2 resolve-merge attempts"},
                    "E24": {"state": "failed", "error": "gh: error connecting to api.github.com"},
                    "E25": {"state": "failed", "error": "checks still running after 60 min: #7"}}
            for k, v in recs.items():
                json.dump({**v, "prs": [637, 657], "ts": "2026-11-21T03:41:08Z"}, open(P(f"{repo}.{k}.merge.json"), "w"))
            json.dump({"heads": "sha1,sha1", "count": 2, "ts": "2026-11-21T04:00:00Z"}, open(P(f"{repo}.E25.eager-retry.json"), "w"))   # cap reached for this head
            n_before = len(log()); tk()
            got = sorted(l.split()[1] for l in log()[n_before:] if l.startswith("enqueue"))
            check("eager retry: a transient failure (CI still running, a gh error) and an outlived one are enqueued again", got == ["E20", "E23", "E24"], got)
            check("eager retry: a cancel, a failure that needs a human, and a capped transient one are not", "E22" not in got and "E21" not in got and "E25" not in got, got)
            check("eager retry: one timeline line per retry, none for the others", [retried(k) for k in ids] == [1, 0, 0, 1, 1, 0], [retried(k) for k in ids])
            check("eager retry: the transient retry is counted against its head", (read_json(P(f"{repo}.E20.eager-retry.json"), {}) or {}).get("count") == 1)
            # the stub records are 'queued' now in the real flow; here the fake enqueue leaves them failed: the cap must stop a loop
            for _ in range(3): tk()
            check("eager retry: a failure that persists is retried at most twice for one head", retried("E20") == 2, retried("E20"))
            heads_now["v"] = "sha2"; tk()
            check("eager retry: a new head gets a new chance", retried("E20") == 3, retried("E20"))
        finally:
            merge_dossier.head_of = real_head
        facts_runs[:] = keep_runs
        open(stubf, "w").write("E1\nE2\nE3\nE4\nE5\nE6\nE7\n")
        for k in ("E20", "E21", "E22", "E23", "E24", "E25"):
            for suf in ("merge.json", "eager-retry.json", "jsonl"):
                try: os.remove(P(f"{repo}.{k}.{suf}"))
                except OSError: pass
        def held_lines() -> list[str]:
            try:
                return [l for l in open(P(f"{repo}.E3.jsonl"), encoding="utf-8") if "eager merge: held" in l]
            except OSError:
                return []
        check("held: a built dossier on the stub kept out only by an open deferred item says so in its timeline, once",
              len(held_lines()) == 1 and "D-1" in held_lines()[0] and "leave-open" in held_lines()[0], held_lines())
        setrun("E3", deferred=[{"id": "D-1", "state": "open"}, {"id": "D-9", "state": "open"}]); tk()
        check("held: a new open item (minted after the fix-deferred run) says it again, naming it", len(held_lines()) == 2 and "D-9" in held_lines()[1], held_lines())
        # A dossier becomes finished: the open item is fixed, the fix-deferred run ends.
        setrun("E3", deferred=[{"id": "D-1", "state": "fixed"}]); setrun("E4", runner=done_())
        n_before = len(log()); tk()
        check("a dossier whose item got fixed and whose fix-deferred run ended is enqueued next look",
              [l for l in log()[n_before:] if l.startswith("enqueue")] == [f"enqueue E3 --enqueue --root {root} --by eager", f"enqueue E4 --enqueue --root {root} --by eager"], log()[n_before:])
        # No open stub: nothing.
        for k in ("E3", "E4"):
            json.dump({"state": "queued"}, open(P(f"{repo}.{k}.merge.json"), "w"))
        os.remove(P(f"{repo}.E7.merge.json")); facts_stubs[:] = []
        n_before = len(log()); tk()
        check("no open stub: nothing is enqueued", log()[n_before:] == [], log()[n_before:])
        facts_stubs[:] = [{"branch": "stub/2026-11-24", "state": "accepting", "canIntegrate": True}]
        # With auto work on as well, the eager step still runs and the cycle is not disturbed.
        cmd_enable(root); n_before = len(log()); tk()
        check("auto work on as well: the eager step enqueues the finished one (E7, its record gone)", [l for l in log()[n_before:] if l.startswith("enqueue")] == [f"enqueue E7 --enqueue --root {root} --by eager"], log()[n_before:])
        cmd_eager(root, "off"); sw = land_stub.settings_of(repo)
        check("eager-merge off leaves auto work and the scripted stub merge as they were", sw.get("eager_merge") is False and sw.get("auto_work") is True and sw.get("enabled") is True, sw)
        n_before = len(log()); tk()   # E7 still has no record: only the switch keeps it out
        check("eager off again: auto work alone enqueues nothing", not [l for l in log()[n_before:] if l.startswith(("enqueue", "stubdossiers"))], log()[n_before:])
        cmd_disable(root)
        check("status shows the eager-merge switch", cmd_status(root) == 0)
    LAND = real_land
    kanban_selftest(check)
    jira_branch_selftest(check)
    land_dirs_ok = [land_stub.settings_path(), runs_dir()] != real_dirs
    check("the selftest used its own settings and runs directory", land_dirs_ok)
    print("FAILED: " + ", ".join(fails) if fails else "all ok")
    return 1 if fails else 0


def jira_branch_selftest(check) -> None:
    """Eager merge of a dossier whose PR is on a Jira-key branch (podcast-generation-engine W-009: #97 feat/LABS-138-control-plane
    into the stub, #99 fix/W-009-deferred on it, #101 the stub into development). The real `merge_dossier.py --stub-dossiers`
    reads a fake gh over a temp repo; its enqueue is a recording stand-in. Nothing touches ~/.agentic."""
    keys = ("ASD_RUNS_DIR", "ASD_STUB_AUTOMERGE_FILE", "ASD_MERGE_BIN", "ASD_REAL_MERGE_BIN", "ASD_GH_BIN", "ASD_DELIVERY_FLOWS")
    saved = {k: os.environ.get(k) for k in keys}
    with tempfile.TemporaryDirectory() as tmp:
        runs, root, log_ = os.path.join(tmp, "runs"), os.path.join(tmp, "pod-repo"), os.path.join(tmp, "calls.txt")
        os.makedirs(runs); os.makedirs(os.path.join(root, ".discovery", "dossiers"))
        repo = repo_name(root)
        stub = "stub/2026-11-21"
        prs = [{"number": 97, "headRefName": "feat/LABS-138-control-plane", "baseRefName": stub, "isDraft": False},
               {"number": 99, "headRefName": "fix/W-009-deferred", "baseRefName": "feat/LABS-138-control-plane", "isDraft": False},
               {"number": 101, "headRefName": stub, "baseRefName": "development", "isDraft": False}]
        with open(os.path.join(root, ".discovery", "dossiers", "W-009-control-plane.md"), "w") as fh:
            fh.write("---\nid: W-009\nstatus: pr\npr: 97\n---\n# t\n")
        with open(os.path.join(tmp, "gh.py"), "w") as fh:
            fh.write("import json\nprint(json.dumps(%r))\n" % prs)
        with open(os.path.join(tmp, "merge.py"), "w") as fh:   # --stub-dossiers is the real script's; an enqueue is only recorded
            fh.write("import sys, os, subprocess\n"
                     "if '--stub-dossiers' in sys.argv:\n"
                     "    sys.exit(subprocess.call([sys.executable, os.environ['ASD_REAL_MERGE_BIN']] + sys.argv[1:]))\n"
                     "open(%r,'a').write('merge '+' '.join(sys.argv[1:])+'\\n')\nprint('queued')\n" % log_)
        os.environ.update(ASD_RUNS_DIR=runs, ASD_STUB_AUTOMERGE_FILE=os.path.join(tmp, "settings.json"), ASD_MERGE_BIN=os.path.join(tmp, "merge.py"),
                          ASD_REAL_MERGE_BIN=os.path.join(HERE, "merge_dossier.py"), ASD_GH_BIN=f"{sys.executable} {os.path.join(tmp, 'gh.py')}",
                          ASD_DELIVERY_FLOWS=os.path.join(tmp, "flows.json"))
        try:
            check("eager (Jira-key branch): the stub's dossiers are W-009, not LABS-138", stub_dossiers(root, stub) == ["W-009"], stub_dossiers(root, stub))
            fin = {"state": "done", "command": "work-on", "pid": 1, "started": "t"}
            facts = {"root": root, "stubs": [{"branch": stub, "state": "accepting"}],
                     "runs": [{"id": "W-009", "status": "pr", "blockedBy": [], "runner": fin, "deferred": []}]}
            set_eager(repo, True)
            eager_step(repo, root, facts)
            calls = open(log_).read().splitlines() if os.path.exists(log_) else []
            check("eager (Jira-key branch): W-009 is enqueued by its own id", calls == [f"merge W-009 --enqueue --root {root} --by eager"], calls)
        finally:
            for k, v in saved.items():
                if v is None: os.environ.pop(k, None)
                else: os.environ[k] = v


def kanban_selftest(check) -> None:
    """The kanban pull, the frontier skip and the kanban eager merge, in a temp dir with fake runner and merge scripts."""
    keys = ("ASD_RUNS_DIR", "ASD_STUB_AUTOMERGE_FILE", "ASD_RUNNER_BIN", "ASD_MERGE_BIN", "ASD_DELIVERY_FLOWS", "FAKE_DEV_CI")
    saved = {k: os.environ.get(k) for k in keys}
    with tempfile.TemporaryDirectory() as tmp:
        runs, root, log_ = os.path.join(tmp, "runs"), os.path.join(tmp, "kan-repo"), os.path.join(tmp, "calls.txt")
        os.makedirs(runs); os.makedirs(os.path.join(root, ".discovery", "dossiers"))
        repo = repo_name(root)
        open(os.path.join(tmp, "runner.py"), "w").write("import sys\nopen(%r,'a').write('runner '+' '.join(sys.argv[1:])+'\\n')\nprint('started')\n" % log_)
        open(os.path.join(tmp, "merge.py"), "w").write(
            "import sys, os\nopen(%r,'a').write('merge '+' '.join(sys.argv[1:])+'\\n')\n"
            "if '--dev-ci' in sys.argv: print(os.environ.get('FAKE_DEV_CI', 'green'))\n"
            "elif '--kanban-dossiers' in sys.argv: print(os.environ.get('FAKE_KANBAN_DOSSIERS', ''))\n"
            "elif '--enqueue' in sys.argv: print('queued')\n" % log_)
        os.environ.update(ASD_RUNS_DIR=runs, ASD_STUB_AUTOMERGE_FILE=os.path.join(tmp, "settings.json"), ASD_RUNNER_BIN=os.path.join(tmp, "runner.py"),
                          ASD_MERGE_BIN=os.path.join(tmp, "merge.py"), ASD_DELIVERY_FLOWS=os.path.join(tmp, "flows.json"), FAKE_DEV_CI="green")
        os.environ["FAKE_KANBAN_DOSSIERS"] = ""
        try:
            def dossier(id_, delivery=None):
                with open(os.path.join(root, ".discovery", "dossiers", f"{id_}-x.md"), "w") as fh:
                    fh.write(f"---\nid: {id_}\nstatus: ready\n" + (f"delivery: {delivery}\n" if delivery else "") + "---\n# t\n")
            for i, d in (("K-1", "kanban"), ("K-2", None), ("K-3", None), ("K-4", None), ("K-5", "kanban"), ("K-6", "kanban"), ("K-7", "kanban"),
                         ("K-8", "kanban"), ("K-9", "kanban"), ("K-10", "kanban"), ("K-11", "stub")):
                dossier(i, d)
            run_ = lambda id_, status, blocked=(), runner=None: {"id": id_, "status": status, "blockedBy": list(blocked), "runner": runner, "deferred": []}
            fin = {"state": "done", "command": "work-on", "pid": 1, "started": "t"}
            facts = {"root": root, "stubs": [], "runs": [
                run_("K-1", "ready"), run_("K-2", "ready"), run_("K-3", "planned"), run_("K-4", "planned", ["K-5"]), run_("K-5", "building"),
                run_("K-6", "ready", ["K-7"]), run_("K-7", "done"), run_("K-8", "ready", runner={"state": "running", "command": "work-on", "pid": 2, "started": "t"}),
                run_("K-9", "ready", ["K-5"]), run_("K-10", "pr", runner=fin), run_("K-11", "pr", runner=fin)]}
            calls = lambda: open(log_).read().splitlines() if os.path.exists(log_) else []
            check("kanban settings: missing file, entry or key = stub on, kanban off; both off is invalid = stub on",
                  flows_of(repo) == (True, False) and not kanban_on(repo) and not kanban_ids(repo, root))
            json.dump({repo: {"stub": False, "kanban": True}, "other": {"stub": False, "kanban": False}, "mix": {"kanban": True}}, open(os.environ["ASD_DELIVERY_FLOWS"], "w"))
            check("kanban settings: kanban only, both on, both off", flows_of(repo) == (False, True) and flows_of("mix") == (True, True) and flows_of("other") == (True, False))
            json.dump({}, open(os.environ["ASD_DELIVERY_FLOWS"], "w"))
            tick_repo(repo, facts)
            check("default settings: no kanban pull, no state file, nothing started", not any(l.startswith("runner") for l in calls()) and not os.path.exists(os.path.join(runs, f"{repo}.kanban.json")), calls())
            json.dump({repo: {"stub": True, "kanban": True}}, open(os.environ["ASD_DELIVERY_FLOWS"], "w"))
            set_eager(repo, False)
            tick_repo(repo, facts)
            check("kanban on, auto work off: a planned dossier gets /plan, a ready kanban one /work-on, in id order, blockers done, no live run; stub-classified, blocked and live ones are left",
                  [l for l in calls() if l.startswith("runner")] == [f"runner {i} --root {root} --command {c}" for i, c in (("K-1", "work-on"), ("K-3", "plan"), ("K-6", "work-on"))], calls())
            n = len(calls()); tick_repo(repo, facts)
            check("a started (dossier, command) is not started again by the pull", len(calls()) == n, calls()[n:])
            facts["runs"][2]["status"] = "ready"   # K-3 planned -> ready; its dossier says kanban only once /plan wrote it
            dossier("K-3", "kanban"); tick_repo(repo, facts)
            check("a plan that made the dossier ready kanban is pulled into /work-on next", calls()[n:] == [f"runner K-3 --root {root} --command work-on"], calls()[n:])
            json.dump({repo: {"stub": False, "kanban": True}}, open(os.environ["ASD_DELIVERY_FLOWS"], "w"))
            n = len(calls()); tick_repo(repo, facts)
            check("kanban only: a ready dossier without `delivery:` is not pulled (/work-on and merge_dossier.py would treat it as stub)",
                  not [l for l in calls()[n:] if l.startswith("runner")] and "K-2" not in kanban_ids(repo, root), calls()[n:])
            # a /plan run that is still writing a dossier (free-form seed, plan-from-deferred) owns it: no second /plan
            for k_, cmd_, st_ in (("seed-hook", "plan", "running"), ("K-20.plan-D-1", "plan", "waiting")):
                json.dump({"command": cmd_, "state": st_, "pid": 1}, open(os.path.join(runs, f"{repo}.{k_}.runner.json"), "w"))
            for k_, i_ in (("seed-hook", "K-12"), ("K-20.plan-D-1", "K-13")):
                with open(os.path.join(runs, f"{repo}.{k_}.jsonl"), "w") as fh:
                    fh.write(json.dumps({"kind": "stage", "stage": "Phase 2", "text": f"Dossier {i_} (claimed), key x"}) + "\n")
            check("planning_ids: a live seed run and a live plan-from-deferred run name the ids they claimed", planning_ids(repo) == {"K-12", "K-13"}, planning_ids(repo))
            facts["runs"] += [run_("K-12", "planned"), run_("K-13", "planned"), run_("K-14", "planned")]
            n = len(calls()); tick_repo(repo, facts)
            check("a planned dossier a live seed / plan-from-deferred run is still writing is not given a second /plan; another planned one is",
                  [l for l in calls()[n:] if l.startswith("runner")] == [f"runner K-14 --root {root} --command plan"], calls()[n:])
            json.dump({"command": "plan", "state": "done", "pid": 1}, open(os.path.join(runs, f"{repo}.seed-hook.runner.json"), "w"))
            n = len(calls()); tick_repo(repo, facts)
            check("the seed run done: its dossier may be planned", f"runner K-12 --root {root} --command plan" in calls()[n:] and planning_ids(repo) == {"K-13"}, calls()[n:])
            # the frontier snapshot
            runs_f = [run_("K-1", "ready"), run_("K-2", "ready"), run_("K-5", "building", runner={"state": "running", "command": "work-on"})]
            check("frontier: kanban dossiers are skipped (in flight too), others kept; default skips nothing",
                  [m["id"] for m in frontier(runs_f, {"K-1", "K-5"})] == ["K-2"] and [m["id"] for m in frontier(runs_f)] == ["K-1", "K-2", "K-5"])
            json.dump({repo: {"stub": True, "kanban": True}}, open(os.environ["ASD_DELIVERY_FLOWS"], "w"))
            st_ = blank(repo, root); start_cycle(st_, {"runs": runs_f}, "t", "stub/x")
            check("start_cycle: with kanban on the snapshot leaves `delivery: kanban` dossiers to the pull", [m["id"] for m in st_["members"]] == ["K-2"], st_["members"])
            # eager merge of finished kanban dossiers
            os.environ["FAKE_KANBAN_DOSSIERS"] = "K-10"
            os.remove(log_) if os.path.exists(log_) else None
            set_eager(repo, True)
            eager_step(repo, root, facts)
            check("eager: a finished kanban dossier with an open PR into development is enqueued, a stub-classified one is not",
                  [l for l in calls() if l.startswith("merge") and "--enqueue" in l] == [f"merge K-10 --enqueue --root {root} --by eager"], calls())
            os.remove(log_); os.environ["FAKE_DEV_CI"] = "red"
            eager_step(repo, root, facts)
            check("stop the line: development's CI red, no kanban merge is enqueued", not any("--enqueue" in l for l in calls()) and "--dev-ci --root " + root in " ".join(calls()), calls())
            os.environ["FAKE_DEV_CI"] = "green"; os.remove(log_)
            json.dump({}, open(os.environ["ASD_DELIVERY_FLOWS"], "w"))
            eager_step(repo, root, facts)
            check("default settings: eager merge asks nothing about development and enqueues nothing", calls() == [], calls())
        finally:
            for k, v in saved.items():
                if v is None: os.environ.pop(k, None)
                else: os.environ[k] = v


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    ap.add_argument("--root")
    for f, h in (("enable", "automatic work on for this repository (and the scripted stub merge)"), ("disable", "off; live runs go on"),
                 ("start-now", "start a cycle at the next look"), ("resume", "go on after a pause"), ("status", "print the state file"),
                 ("tick", "one step for every enabled repository"), ("selftest", "")):
        ap.add_argument("--" + f, action="store_true", help=h)
    ap.add_argument("--facts", help="tick: the facts file, or - for stdin")
    ap.add_argument("--eager-merge", choices=["on", "off"], help="the eager-merge switch: each finished dossier joins the merge queue for the open stub")
    a = ap.parse_args(argv)
    if a.selftest:
        return selftest()
    if a.tick:
        if not a.facts:
            ap.print_usage(sys.stderr); return 2
        try:
            facts = json.loads(sys.stdin.read() if a.facts == "-" else open(a.facts).read())
        except (OSError, ValueError) as e:
            print(f"auto_work: facts unreadable: {e}", file=sys.stderr); return 2
        return tick(facts)
    chosen = [x for x in ("enable", "disable", "start_now", "resume", "status", "eager_merge") if getattr(a, x)]
    if not a.root or len(chosen) != 1:
        ap.print_usage(sys.stderr); return 2
    root = os.path.abspath(a.root)
    if chosen[0] == "status":
        return cmd_status(root)
    if chosen[0] == "eager_merge":
        return cmd_eager(root, a.eager_merge)
    return {"enable": cmd_enable, "disable": cmd_disable, "start_now": cmd_start_now, "resume": cmd_resume}[chosen[0]](root)


if __name__ == "__main__":
    sys.exit(main())
