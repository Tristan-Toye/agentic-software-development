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
   it ready). Only that snapshot is worked in the cycle; a dossier that becomes eligible later waits for the next START.
3. Each member is started through `run_dossier.py` and followed to the end.
4. A member whose build is finished (status `pr` or `done`) gets "Fix all deferred" exactly as the dashboard button
   sends it: every open item except the ones the owner ruled to leave open, named explicitly. It is finished when
   that run is done and it has no open item left, or had none to begin with.
5. When every member is finished: the repository's non-code PRs are queued (`merge_docs_prs.py merge`, the "Merge all"
   button) and the script waits until each one is merged; then the stub's Integrate (`merge_dossier.py --integrate`)
   runs and the script waits for it to reach /stub; then land_stub.py merges the stub's PR when its conditions hold.
6. That merge is the next START.

# Eager merge (its own switch, on by default; `--eager-merge off` turns it off)

`--eager-merge on` puts each dossier on the open stub as soon as it is finished, through the existing merge queue
(`merge_dossier.py ID --enqueue`, the Merge button's entry point), so the stub grows during the week and Integrate has
little left to merge. Finished is the same as for a cycle member: build done (status `pr` or `done`), no run live (so a
fix-deferred run is over) and no open deferred item left (one the owner ruled to stay open does not count). The dossier
must have an open pull request into an open stub. A dossier whose `<repo>.<dossier>.merge.json` exists is never enqueued
again (queued, running, waiting, merged, failed or cancelled: a failed one waits for the owner). It works with auto work
on or off; with the switch off nothing of this runs.

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

HERE = os.path.dirname(os.path.abspath(__file__))
LIVE = ("running", "testing", "waiting", "queued", "paused")
STAGE_OF = {"planned": "plan", "ready": "build", "building": "build", "review": "build", "pr": "fix", "done": "fix"}
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
def frontier(runs: list[dict]) -> list[dict]:
    """Who is worked this cycle: `ready` and `planned` dossiers whose blockers are all `done`. A blocker not known is not done."""
    status = {r["id"]: r.get("status") for r in runs}
    out = []
    for r in sorted(runs, key=lambda r: r["id"]):
        if r.get("status") in ("ready", "planned") and all(status.get(b) == "done" for b in r.get("blockedBy") or []):
            out.append({"id": r["id"], "stage": STAGE_OF[r["status"]], "state": "pending", "error": "", "launch": None, "lag": 0, "asked": None, "accepted": False, "rounds": 0, "retried": False})
    return out


def open_items(run: dict) -> list[str]:
    """The items "Fix all deferred" sends: every open one except those the owner ruled to leave open."""
    return [i["id"] for i in run.get("deferred") or [] if i.get("state") == "open" and not i.get("keptOpen")]


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
        m["state"] = {"queued": "queued for a slot", "waiting": "waiting for the owner", "paused": "paused (usage limit)"}.get(r["state"], "running")
        m["lag"], m["retried"] = 0, False
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
    if L and r.get("state") in ("failed", "stopped"):
        pause(st, f"{m['id']}: the /{L['command']} run ended {r['state']}", "member", m["id"]); return
    m["stage"] = STAGE_OF.get(status, m["stage"])
    if status in ("pr", "done"):
        m["asked"] = None   # the agent fixed it (or it was intended after all and the dossier moved): the normal path takes over
        todo = open_items(run)
        if L and L["command"] == "fix-deferred":
            if not todo:
                m.update(stage="finished", state="finished", error=""); return
            m["lag"] += 1
            if m["lag"] >= LAG_TICKS and not m["accepted"]:
                pause(st, f"{m['id']}: /fix-deferred is done but {len(todo)} item(s) are still open ({', '.join(todo[:6])})", "member", m["id"], "accept")
            return
        if not todo or m["accepted"]:
            m.update(stage="finished", state="finished", error=""); return
        launch(st, m, "fix-deferred", r, ",".join(todo)); m["stage"] = "fix"; return
    want = "plan" if status == "planned" else "work-on"
    if L and L["command"] == want:   # it ended done, and the dossier does not show what it should yet
        m["lag"] += 1
        if m["lag"] < LAG_TICKS:     # a status write may land late: wait before believing "not ready"
            return
        if want == "plan":           # the owner's ruling: plan again, until the dossier reads ready (questions, failures and stops are handled above)
            launch(st, m, "plan", r); return
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
    launch(st, m, want, r)


ASK_TEXT = ("The runner's check: /work-on ended, but the dossier front matter reads `status: {status}`, not `pr` or `done`. "
            "If that is a mistake, finish the remaining phases now (open the PR, set the status). If it is intended, reply with "
            "a first line starting `INTENDED:` and the reason.")


def start_cycle(st: dict, facts: dict, trigger: str, stub: str) -> None:
    st.update(cycle=st["cycle"] + 1, step="work", started=now(), stub=stub, trigger=trigger, paused=None, docs={}, integrate={}, note="", request=None,
              members=frontier(facts.get("runs") or []))
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
        st["note"] = "waiting to merge the stub: " + ("; ".join(land.get("reasons") or []) or "the /stub run is going")
        return False
    return False


LAND = land_stub.land_all


def finished_run(run: dict) -> bool:
    """A dossier that is finished the way a cycle member is: build done (`pr` or `done`), no run of it live (a fix-deferred
    run is over) and no open deferred item left (open_items: one the owner ruled to stay open does not count)."""
    return run.get("status") in ("pr", "done") and (run.get("runner") or {}).get("state") not in LIVE and not open_items(run)


def stub_dossiers(root: str, branch: str) -> list[str]:
    """The dossiers with an open pull request into the stub, asked of merge_dossier.py (the same view Integrate has)."""
    script = os.environ.get("ASD_MERGE_BIN") or os.path.join(HERE, "merge_dossier.py")
    try:
        p = subprocess.run([sys.executable, script, "--stub-dossiers", branch, "--root", root], capture_output=True, text=True, timeout=120, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired):
        return []
    return [x.strip().upper() for x in (p.stdout or "").splitlines() if x.strip()] if p.returncode == 0 else []


def enqueue_merge(root: str, dossier: str) -> tuple[bool, str]:
    """The Merge button's entry point: `merge_dossier.py ID --enqueue --root R` (stacks merge whole, conflicts go to /resolve-merge)."""
    script = os.environ.get("ASD_MERGE_BIN") or os.path.join(HERE, "merge_dossier.py")
    return run_runner([sys.executable, script, dossier, "--enqueue", "--root", root])


def eager_step(repo: str, root: str, facts: dict) -> list[str]:
    """Eager merge: every finished dossier on an open stub joins the merge queue, once. Returns the ones enqueued."""
    stubs = [s["branch"] for s in facts.get("stubs") or [] if s.get("branch")]
    todo = [r for r in facts.get("runs") or [] if finished_run(r)
            and not os.path.exists(os.path.join(runs_dir(), f"{repo}.{r['id']}.merge.json"))]   # any record: queued, running, merged, waiting, failed, cancelled
    if not stubs or not todo:
        return []
    on_stub: set[str] = set()
    for b in stubs:
        on_stub.update(stub_dossiers(root, b))
    out = []
    for r in sorted(todo, key=lambda r: r["id"]):
        if r["id"].upper() in on_stub:
            ok, msg = enqueue_merge(root, r["id"])
            print(f"auto_work: {repo}: eager merge of {r['id']}: " + (msg if ok else "not enqueued: " + msg))
            if ok:
                out.append(r["id"])
    return out


def tick_repo(repo: str, facts: dict) -> None:
    root = facts.get("root") or ""
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
            ok, out = start_runner(root, st["stub"] or st["integrate"].get("branch", ""), "stub", st["integrate"].get("branch", ""))
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
                        ("merge.py", "import sys, os\nopen(%r,'a').write(('integrate ' if '--integrate' in sys.argv else 'stubdossiers ' if '--stub-dossiers' in sys.argv else 'enqueue ')+' '.join(sys.argv[1:])+'\\n')\nif '--stub-dossiers' in sys.argv:\n    f = os.environ.get('FAKE_STUB_DOSSIERS')\n    print(open(f).read() if f and os.path.exists(f) else '', end='')\n")):
            open(os.path.join(tmp, n), "w").write(body % calls)
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
            return {"id": id, "status": status, "blockedBy": list(blocked), "runner": runner, "deferred": [{"id": i[0], "state": i[1], "keptOpen": len(i) > 2} for i in items]}
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

        # A stub merges: START. The frontier is snapshotted once.
        json.dump({"state": "merged", "root": root, "branch": "stub/2026-11-17", "ts": "2026-11-17T09:00:00Z"}, open(P(f"{repo}.stub-2026-11-17.integrate.json"), "w"))
        tk()
        s = st()
        check("START: the stub's merge starts cycle 1", s["cycle"] == 1 and s["step"] == "work" and s["stub"] == "stub/2026-11-17", s)
        check("snapshot: ready and planned with every blocker done; blocked by an unfinished one, and dropped, are out",
              sorted(members()) == ["A", "D", "F"], sorted(members()))
        check("A /work-on, F /work-on and D /plan are started through the runner",
              sorted(log()) == sorted([f"runner A --root {root} --command work-on", f"runner D --root {root} --command plan", f"runner F --root {root} --command work-on"]), log())
        before = log()
        facts_runs.append(run_("N", "ready"))   # created after START
        setrun("C", status="done", runner=runner("done", "work-on"))   # the blocker finishes mid-cycle
        setrun("A", runner=runner("queued", "work-on", pid=200, started="2026-11-17T10:01:00Z"))
        setrun("F", runner=runner("waiting", "work-on", pid=201, started="2026-11-17T10:01:00Z"))
        setrun("D", runner=runner("running", "plan", pid=202, started="2026-11-17T10:01:00Z"))
        tk()
        check("a dossier created after START and a blocker finishing mid-cycle add nobody", sorted(members()) == ["A", "D", "F"] and log() == before, members())
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
        setrun("D", status="pr", runner=runner("done", "work-on", pid=203, started="2026-11-17T10:30:00Z"), deferred=[{"id": "D-1", "state": "open", "keptOpen": False}, {"id": "D-2", "state": "open", "keptOpen": True}, {"id": "D-3", "state": "fixed"}])
        setrun("F", status="pr", runner=runner("done", "work-on", pid=201, started="2026-11-17T10:01:00Z"), deferred=[{"id": "D-1", "state": "open", "keptOpen": False}])
        tk()
        check("planned -> plan -> work-on -> fix-deferred: the same item selection as the button (the kept-open one stays out)",
              f"runner D --root {root} --command fix-deferred --items D-1" in log() and f"runner F --root {root} --command fix-deferred --items D-1" in log(), log())
        check("not everyone is finished: no non-code merge, no integrate", not any(l.startswith(("docs", "integrate")) for l in log()))
        check("the phase reads fixing deferred", st()["phase"] == "fixing deferred", st()["phase"])
        # F's fix-deferred fails: the cycle pauses; D is untouched.
        setrun("F", runner=runner("failed", "fix-deferred", pid=301, started="2026-11-17T11:00:00Z"))
        setrun("D", runner=runner("running", "fix-deferred", pid=302, started="2026-11-17T11:00:00Z"))
        n_before = len(log()); tk()
        s = st()
        check("a failed run pauses the cycle, with the reason, on the member", s["paused"] and "F" in s["paused"]["reason"] and "failed" in s["paused"]["reason"] and s["phase"] == "paused" and members()["F"]["state"] == "failed", s.get("paused"))
        setrun("D", status="done", runner=runner("done", "fix-deferred", pid=302, started="2026-11-17T11:00:00Z"), deferred=[{"id": "D-1", "state": "fixed"}, {"id": "D-2", "state": "open", "keptOpen": True}])
        tk()
        check("paused: nothing new starts and nothing else moves", len(log()) == n_before and members()["D"]["stage"] == "fix", log()[n_before:])
        check("resume with nothing paused refuses", (cmd_resume(root + "/nope") if False else 1) == 1)
        cmd_resume(root)
        tk()
        check("resume: the failed run is started again, as the Resume button would", log()[-1] == f"runner F --root {root} --command fix-deferred --items D-1" and st()["paused"] is None, log()[-2:])
        # F's rerun finishes; D's fix-deferred is done with only a kept-open item.
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
        os.remove(P(f"{repo}.stub-2026-11-24.runner.json")); st_ = st(); st_["paused"] = None; write_atomic(state_path(repo), st_)
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
              enq == [f"enqueue E1 --enqueue --root {root}", f"enqueue E2 --enqueue --root {root}"], log()[n_before:])
        check("eager asks the merge script which dossiers have an open PR into the stub", f"stubdossiers --stub-dossiers stub/2026-11-24 --root {root}" in log()[n_before:])
        check("the auto work state file is untouched by the eager step", st() == s_before)
        for k in ("E1", "E2"):   # the queue writes the record, as merge_dossier.enqueue does
            json.dump({"state": "queued"}, open(P(f"{repo}.{k}.merge.json"), "w"))
        n_before = len(log()); tk(); tk()
        check("idempotent: queued, running, merged and failed records are never enqueued again", not [l for l in log()[n_before:] if l.startswith("enqueue")], log()[n_before:])
        for state_ in ("merging", "waiting", "cancelled", "cleanup"):
            json.dump({"state": state_}, open(P(f"{repo}.E1.merge.json"), "w")); tk()
        check("any other record state is left alone too", not [l for l in log()[n_before:] if l.startswith("enqueue")])
        # A dossier becomes finished: the open item is fixed, the fix-deferred run ends.
        setrun("E3", deferred=[{"id": "D-1", "state": "fixed", "keptOpen": False}]); setrun("E4", runner=done_())
        n_before = len(log()); tk()
        check("a dossier whose item got fixed and whose fix-deferred run ended is enqueued next look",
              [l for l in log()[n_before:] if l.startswith("enqueue")] == [f"enqueue E3 --enqueue --root {root}", f"enqueue E4 --enqueue --root {root}"], log()[n_before:])
        # No open stub: nothing.
        for k in ("E3", "E4"):
            json.dump({"state": "queued"}, open(P(f"{repo}.{k}.merge.json"), "w"))
        os.remove(P(f"{repo}.E7.merge.json")); facts_stubs[:] = []
        n_before = len(log()); tk()
        check("no open stub: nothing is enqueued", log()[n_before:] == [], log()[n_before:])
        facts_stubs[:] = [{"branch": "stub/2026-11-24", "state": "accepting", "canIntegrate": True}]
        # With auto work on as well, the eager step still runs and the cycle is not disturbed.
        cmd_enable(root); n_before = len(log()); tk()
        check("auto work on as well: the eager step enqueues the finished one (E7, its record gone)", [l for l in log()[n_before:] if l.startswith("enqueue")] == [f"enqueue E7 --enqueue --root {root}"], log()[n_before:])
        cmd_eager(root, "off"); sw = land_stub.settings_of(repo)
        check("eager-merge off leaves auto work and the scripted stub merge as they were", sw.get("eager_merge") is False and sw.get("auto_work") is True and sw.get("enabled") is True, sw)
        n_before = len(log()); tk()   # E7 still has no record: only the switch keeps it out
        check("eager off again: auto work alone enqueues nothing", not [l for l in log()[n_before:] if l.startswith(("enqueue", "stubdossiers"))], log()[n_before:])
        cmd_disable(root)
        check("status shows the eager-merge switch", cmd_status(root) == 0)
    LAND = real_land
    land_dirs_ok = [land_stub.settings_path(), runs_dir()] != real_dirs
    check("the selftest used its own settings and runs directory", land_dirs_ok)
    print("FAILED: " + ", ".join(fails) if fails else "all ok")
    return 1 if fails else 0


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
