#!/usr/bin/env python3
"""Merge a stub's pull request into development once its /stub run has proved it, for a repository that opted in.

The script decides and merges; no agent does. It is off for every repository until the owner turns it on.

# Usage

    land_stub.py --enable --root R [--receipt-context NAME]
    land_stub.py --disable --root R
    land_stub.py --check STUB --root R     # what would hold it back now; merges nothing
    land_stub.py --selftest

`merge_dossier.py --revive` (the dashboard, every two minutes) calls `land_all()`: every stub whose integration
reached `/stub`, in a repository that opted in, is looked at again until its pull request has merged.

# What must hold, all of it, on the pull request's current head

1. The stub's `/stub` run ended `done`.
2. The pull request is open, not a draft, and GitHub reads it `MERGEABLE` and `CLEAN`.
3. Every check on the head passed (skipped and neutral are fine), and the receipt context (`local-stack`) is
   among them and green. A commit status belongs to one commit, so a later push takes the receipt away.
4. The pull request body carries the block `/stub` writes last (`primary-agents/stub.md`, Step 8):
   `STUB-RESULT: green`, `STUB-HEAD: <this head>`, `STUB-GAPS: 0`, `STUB-UNRESOLVED: 0`, and no `ATTENTION:` line.
5. No open owner question on the stub or on a dossier the integration merged.
6. No open pull request into the stub that belongs to this integration: its own `fix/*-stub*` or a stack it merged.
   A dossier that was still in flight moves to the next stub, so it holds nothing back.
7. The head changes none of the guarded paths (default `scripts/local_stack_status.py`, where the waivers live).

Anything else, or anything unreadable, leaves the pull request for the owner. The merge is
`gh pr merge --merge --match-head-commit <head>`, so a push after these checks makes GitHub refuse it.

# Files

Settings: `$ASD_STUB_AUTOMERGE_FILE` (default `~/.agentic/stub-auto-merge.json`),
`{"<repo>": {"enabled": true, "receipt_context": "local-stack", "guarded_paths": ["..."]}}`.
State: `<key>.land.json` `{state: waiting|merged, pr, head, reasons, ts}`, read by the dashboard. A change of reasons
writes one line on the stub's timeline.

Exit 0 merged or nothing to do, 1 held back, 2 bad usage. `ASD_GH_BIN` replaces `gh` (tests).
"""
from __future__ import annotations

import argparse
import fcntl
import glob
import json
import os
import re
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from merge_dossier import append_line, gh, git, now, read_json, repo_name, run_live, runs_dir, write_json  # noqa: E402

DEFAULT_CONTEXT = "local-stack"
DEFAULT_GUARDED = ["scripts/local_stack_status.py"]
PASSED = ("SUCCESS", "SKIPPED", "NEUTRAL")


def settings_path() -> str:
    return os.environ.get("ASD_STUB_AUTOMERGE_FILE") or os.path.expanduser("~/.agentic/stub-auto-merge.json")


def settings_of(repo: str) -> dict:
    return (read_json(settings_path(), {}) or {}).get(repo) or {}


def set_enabled(repo: str, enabled: bool, context: str = "") -> None:
    all_ = read_json(settings_path(), {}) or {}
    cur = {**(all_.get(repo) or {}), "enabled": enabled}
    if context:
        cur["receipt_context"] = context
    write_json(settings_path(), {**all_, repo: cur})


def check_failures(rollup: list, context: str) -> list[str]:
    """What the head's checks hold back. `context` must be there and green; the rest must have passed."""
    out, seen = [], False
    for c in rollup or []:
        name = c.get("name") or c.get("context") or "?"
        verdict = str(c.get("conclusion") or c.get("state") or c.get("status") or "").upper()
        if name == context:
            seen = True
            if verdict != "SUCCESS":
                out.append(f"the {context} receipt on this head reads {verdict or 'nothing'}")
        elif verdict not in PASSED:
            out.append(f"check {name} reads {verdict or 'nothing'}")
    if not seen:
        out.append(f"this head has no {context} receipt")
    return out


def result_failures(body: str, head: str) -> list[str]:
    """What the result block in the pull request body holds back. No block, no merge."""
    def field(name: str) -> str:
        m = re.search(rf"^{name}:[ \t]*(\S*)[ \t]*$", body or "", re.M)
        return m.group(1) if m else ""
    out = []
    if field("STUB-RESULT").lower() != "green":
        out.append("the pull request has no STUB-RESULT: green line")
    if field("STUB-HEAD") != head:
        out.append(f"STUB-HEAD does not name this head ({head[:8]})")
    if field("STUB-GAPS") != "0":
        out.append("the audit does not report STUB-GAPS: 0")
    if field("STUB-UNRESOLVED") != "0":
        out.append("the run does not report STUB-UNRESOLVED: 0")
    for m in re.finditer(r"^ATTENTION:[ \t]*(.*)$", body or "", re.M):
        out.append("attention: " + m.group(1).strip()[:200])
    return out


def open_questions(repo: str, owners: list[str]) -> list[str]:
    """Open owner questions of these run names: no line that answers them."""
    store = os.environ.get("ASD_QUESTIONS_DIR") or os.path.expanduser("~/.agentic/questions")
    out = []
    for owner in owners:
        for path in glob.glob(os.path.join(store, glob.escape(f"{repo}.{owner}.") + "Q*.jsonl")):
            answered = False
            try:
                with open(path, encoding="utf-8") as fh:
                    for raw in fh:
                        try:
                            ln = json.loads(raw)
                        except ValueError:
                            continue
                        if ln.get("kind") == "answer" and (ln.get("from") == "owner" or ln.get("accepted") is True):
                            answered = True
            except OSError:
                continue
            if not answered:
                out.append(os.path.basename(path)[:-6])
    return sorted(out)


def own_open_prs(prs: list[dict], finished: list[str]) -> list[str]:
    """Open PRs into the stub that belong to this integration: /stub's fix branches and the stacks it merged."""
    out = []
    for p in prs or []:
        head = p.get("headRefName", "")
        names_finished = any(re.search(rf"(?<![A-Za-z0-9]){re.escape(i)}(?!\d)", head, re.I) for i in finished)
        if re.match(r"fix/.*-stub", head, re.I) or (names_finished and not head.startswith(("plan/", "docs/"))):
            out.append(f"#{p.get('number')} {head}")
    return out


def assess(root: str, repo: str, branch: str, finished: list[str], cfg: dict) -> tuple[str, dict, list[str]]:
    """("merged"|"ready"|"waiting", the pull request or {}, reasons)."""
    slug = branch.replace("/", "-"); key = f"{repo}.{slug}"
    run = read_json(os.path.join(runs_dir(), key + ".runner.json"), {}) or {}
    if run_live(key):
        return "waiting", {}, ["the /stub run is still going"]
    if run.get("command") != "stub" or run.get("state") != "done":
        return "waiting", {}, [f"the /stub run ended {run.get('state') or 'never started'}, not done"]
    rc, out = gh(root, "pr", "list", "--head", branch, "--base", "development", "--state", "all", "--limit", "5",
                 "--json", "number,state,isDraft,mergeable,mergeStateStatus,headRefOid,body,statusCheckRollup")
    if rc != 0:
        return "waiting", {}, ["gh pr list failed: " + out[:160]]
    try:
        prs = json.loads(out or "[]")
    except ValueError:
        return "waiting", {}, ["gh pr list gave no JSON"]
    if any(p.get("state") == "MERGED" for p in prs):
        return "merged", next(p for p in prs if p.get("state") == "MERGED"), []
    pr = next((p for p in prs if p.get("state") == "OPEN"), None)
    if not pr:
        return "waiting", {}, ["the stub has no open pull request into development"]
    head = pr.get("headRefOid") or ""
    reasons = []
    if pr.get("isDraft"):
        reasons.append("the pull request is a draft")
    if pr.get("mergeable") != "MERGEABLE":
        reasons.append(f"GitHub reads it {pr.get('mergeable') or 'UNKNOWN'}")
    if pr.get("mergeStateStatus") != "CLEAN":
        reasons.append(f"its merge state is {pr.get('mergeStateStatus') or 'UNKNOWN'}, not CLEAN")
    reasons += check_failures(pr.get("statusCheckRollup") or [], cfg.get("receipt_context") or DEFAULT_CONTEXT)
    reasons += result_failures(pr.get("body") or "", head)
    q = open_questions(repo, [slug] + finished)
    if q:
        reasons.append("open questions: " + ", ".join(q))
    rc, out = gh(root, "pr", "list", "--base", branch, "--state", "open", "--limit", "200", "--json", "number,headRefName")
    if rc != 0:
        reasons.append("gh could not list the pull requests into the stub: " + out[:160])
    else:
        own = own_open_prs(json.loads(out or "[]"), finished)
        if own:
            reasons.append("still open into the stub: " + ", ".join(own))
    git(root, "fetch", "-q", "origin", "development", branch)
    rc, out = git(root, "diff", "--name-only", f"origin/development...{head}")
    if rc != 0:
        reasons.append("could not read what the head changes: " + out[:160])
    else:
        guarded = cfg.get("guarded_paths") or DEFAULT_GUARDED
        hit = [f for f in out.splitlines() if f.strip() in guarded]
        if hit:
            reasons.append("the stub changes a guarded path: " + ", ".join(hit))
    return ("waiting" if reasons else "ready"), pr, reasons


def land(root: str, branch: str, finished: list[str] | None = None, dry: bool = False) -> int:
    repo = repo_name(root); slug = branch.replace("/", "-"); key = f"{repo}.{slug}"
    cfg = settings_of(repo)
    state_path = os.path.join(runs_dir(), key + ".land.json")
    integrate_path = os.path.join(runs_dir(), key + ".integrate.json")
    if finished is None:
        finished = (read_json(integrate_path, {}) or {}).get("finished") or []
    verdict, pr, reasons = assess(root, repo, branch, finished, cfg)
    if dry:
        print(f"land_stub: {verdict}" + ("".join("\n  - " + r for r in reasons)))
        return 0 if verdict != "waiting" else 1
    number, head = pr.get("number"), pr.get("headRefOid") or ""
    log = os.path.join(runs_dir(), key + ".jsonl")
    if verdict == "ready":
        rc, out = gh(root, "pr", "merge", str(number), "--merge", "--match-head-commit", head)
        if rc == 0:
            verdict = "merged"
            append_line(log, {"ts": now(), "kind": "update", "text": f"Every check held on {head[:8]}, so the script merged the stub's pull request #{number} into development."})
        else:
            verdict, reasons = "waiting", ["GitHub refused the merge: " + out[-200:]]
    before = read_json(state_path, {}) or {}
    if verdict == "merged":
        write_json(state_path, {"state": "merged", "pr": number, "head": head, "reasons": [], "ts": now()})
        cur = read_json(integrate_path, {}) or {}
        if cur:
            write_json(integrate_path, {**cur, "state": "merged", "note": "merged into development", "ts": now()})
        print(f"land_stub: {branch} is in development (#{number})")
        return 0
    write_json(state_path, {"state": "waiting", "pr": number, "head": head, "reasons": reasons, "ts": now()})
    if reasons != before.get("reasons"):
        append_line(log, {"ts": now(), "kind": "update", "text": "Auto-merge is holding the stub's pull request, which stays yours to merge:\n" + "\n".join("- " + r for r in reasons)})
    print(f"land_stub: {branch} waits: " + "; ".join(reasons))
    return 1


def land_all() -> int:
    """Every stub that reached /stub in a repository that opted in. One look per stub at a time, so a look that waits out an outage never stacks up."""
    for path in glob.glob(os.path.join(runs_dir(), "*.integrate.json")):
        st = read_json(path, {}) or {}
        root, branch = st.get("root"), st.get("branch")
        if st.get("state") != "stub" or not root or not branch or not os.path.isdir(root) or not settings_of(repo_name(root)).get("enabled"):
            continue
        with open(path[:-len(".integrate.json")] + ".land.lock", "w") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                continue
            try:
                land(root, branch, st.get("finished") or [])
            except (OSError, ValueError, subprocess.SubprocessError) as e:   # one stub never stops the others; the next look tries again
                print(f"land_stub: {branch}: {e}")
    return 0


# ---------------------------------------------------------------- selftest
FAKE = r'''
import json, os, sys
d = os.environ["FAKE_DIR"]; a = sys.argv[1:]
open(os.path.join(d, "calls.txt"), "a").write(" ".join(a) + "\n")
pr = json.load(open(os.path.join(d, "pr.json")))
if a[:2] == ["pr", "list"] and "--head" in a:
    print(json.dumps([pr])); sys.exit(0)
if a[:2] == ["pr", "list"]:
    print(open(os.path.join(d, "into.json")).read()); sys.exit(0)
if a[:2] == ["pr", "merge"]:
    if os.path.exists(os.path.join(d, "refuse")):
        print("head moved"); sys.exit(1)
    pr["state"] = "MERGED"; json.dump(pr, open(os.path.join(d, "pr.json"), "w")); sys.exit(0)
sys.exit(3)
'''


def selftest() -> int:
    fails = []

    def check(name: str, ok: bool, why: object = "") -> None:
        print(("ok   " if ok else "FAIL ") + name + ("" if ok or not why else "  — " + str(why)[:400]))
        if not ok:
            fails.append(name)

    with tempfile.TemporaryDirectory() as tmp:
        g = lambda *x, cwd=None: subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *x], cwd=cwd, capture_output=True, text=True)
        g("init", "-q", "--bare", "-b", "development", os.path.join(tmp, "o.git"))
        w = os.path.join(tmp, "repo"); g("clone", "-q", os.path.join(tmp, "o.git"), w)
        g("commit", "-q", "--allow-empty", "-m", "base", cwd=w); g("push", "-q", "origin", "HEAD:development", cwd=w)
        g("checkout", "-q", "-b", "stub/x", cwd=w)
        open(os.path.join(w, "a.txt"), "w").write("a"); g("add", ".", cwd=w); g("commit", "-q", "-m", "work", cwd=w); g("push", "-q", "origin", "HEAD:stub/x", cwd=w)
        head = g("rev-parse", "HEAD", cwd=w).stdout.strip()
        repo = repo_name(w)
        runs = os.path.join(tmp, "runs"); os.makedirs(runs); qs = os.path.join(tmp, "q"); os.makedirs(qs)
        open(os.path.join(tmp, "gh.py"), "w").write(FAKE)
        os.environ.update(ASD_GH_BIN=f"{sys.executable} {os.path.join(tmp, 'gh.py')}", FAKE_DIR=tmp, ASD_RUNS_DIR=runs,
                          ASD_QUESTIONS_DIR=qs, ASD_STUB_AUTOMERGE_FILE=os.path.join(tmp, "settings.json"))
        key = f"{repo}.stub-x"
        calls = lambda: open(os.path.join(tmp, "calls.txt")).read() if os.path.exists(os.path.join(tmp, "calls.txt")) else ""
        block = f"STUB-RESULT: green\nSTUB-HEAD: {head}\nSTUB-GAPS: 0\nSTUB-UNRESOLVED: 0\n"
        green = [{"name": "Rust", "conclusion": "SUCCESS"}, {"name": "Optional", "conclusion": "SKIPPED"}, {"context": "local-stack", "state": "SUCCESS"}]

        def setup(**pr):
            base = {"number": 9, "state": "OPEN", "isDraft": False, "mergeable": "MERGEABLE", "mergeStateStatus": "CLEAN",
                    "headRefOid": head, "body": "Report.\n" + block, "statusCheckRollup": green}
            json.dump({**base, **pr}, open(os.path.join(tmp, "pr.json"), "w"))
            json.dump([], open(os.path.join(tmp, "into.json"), "w"))
            write_json(os.path.join(runs, key + ".runner.json"), {"command": "stub", "state": "done", "pid": 0})
            write_json(os.path.join(runs, key + ".integrate.json"), {"state": "stub", "root": w, "branch": "stub/x", "finished": ["W-1"]})
            for f in ("calls.txt", "refuse"):
                if os.path.exists(os.path.join(tmp, f)):
                    os.remove(os.path.join(tmp, f))
            for f in glob.glob(os.path.join(runs, "*.land.json")) + glob.glob(os.path.join(runs, "*.jsonl")) + glob.glob(os.path.join(qs, "*")):
                os.remove(f)

        def run_land() -> tuple[int, dict]:
            import contextlib, io
            with contextlib.redirect_stdout(io.StringIO()):
                rc = land(w, "stub/x", ["W-1"])
            return rc, read_json(os.path.join(runs, key + ".land.json"), {}) or {}

        def held(name: str, needle: str, **pr) -> None:
            setup(**pr)
            rc, st = run_land()
            check("held: " + name, rc == 1 and st.get("state") == "waiting" and "pr merge" not in calls() and needle in " ".join(st.get("reasons", [])), st)

        # Off by default: nothing is looked at.
        setup(); land_all()
        check("off by default: no gh call, no state", calls() == "" and not os.path.exists(os.path.join(runs, key + ".land.json")), calls())
        set_enabled(repo, True)
        check("settings: enable is per repository", settings_of(repo).get("enabled") is True and not settings_of("other"))

        # Everything holds.
        setup(); land_all()
        st = read_json(os.path.join(runs, key + ".land.json"), {})
        check("merges with the head pinned when all of it holds", f"pr merge 9 --merge --match-head-commit {head}" in calls() and st.get("state") == "merged", calls())
        check("the integration reads merged, so it is not looked at again", (read_json(os.path.join(runs, key + ".integrate.json"), {}) or {}).get("state") == "merged")
        before = calls(); land_all()
        check("a merged stub makes no further call", calls() == before)
        check("the timeline says the script merged it", "script merged" in open(os.path.join(runs, key + ".jsonl")).read())

        # Each condition alone holds it back.
        held("draft", "draft", isDraft=True)
        held("conflicting", "CONFLICTING", mergeable="CONFLICTING")
        held("behind development", "BEHIND", mergeStateStatus="BEHIND")
        held("a red check", "check Rust", statusCheckRollup=[{"name": "Rust", "conclusion": "FAILURE"}, {"context": "local-stack", "state": "SUCCESS"}])
        held("a pending check", "check Rust", statusCheckRollup=[{"name": "Rust", "status": "IN_PROGRESS"}, {"context": "local-stack", "state": "SUCCESS"}])
        held("no receipt on this head", "no local-stack receipt", statusCheckRollup=[{"name": "Rust", "conclusion": "SUCCESS"}])
        held("a red receipt", "receipt on this head reads FAILURE", statusCheckRollup=[{"context": "local-stack", "state": "FAILURE"}])
        held("no result block", "STUB-RESULT", body="Report only.")
        held("a result for an older head", "STUB-HEAD", body=block.replace(head, "0" * 40))
        held("an audit gap", "STUB-GAPS", body=block.replace("STUB-GAPS: 0", "STUB-GAPS: 2"))
        held("an unresolved attribution", "STUB-UNRESOLVED", body=block.replace("STUB-UNRESOLVED: 0", "STUB-UNRESOLVED: 1"))
        held("an attention line", "attention: a waiver was carried", body=block + "ATTENTION: a waiver was carried\n")

        setup(); open(os.path.join(qs, f"{repo}.stub-x.Q1.jsonl"), "w").write('{"from": "agent", "kind": "question"}\n')
        rc, st = run_land(); check("held: an open question on the stub", rc == 1 and "stub-x.Q1" in " ".join(st["reasons"]), st)
        setup(); open(os.path.join(qs, f"{repo}.W-1.Q2.jsonl"), "w").write('{"from": "agent", "kind": "question"}\n')
        rc, st = run_land(); check("held: an open question on a merged dossier", rc == 1 and "W-1.Q2" in " ".join(st["reasons"]), st)
        setup(); open(os.path.join(qs, f"{repo}.W-1.Q2.jsonl"), "w").write('{"from": "agent", "kind": "question"}\n{"from": "owner", "kind": "answer"}\n')
        rc, st = run_land(); check("an answered question holds nothing", rc == 0 and st["state"] == "merged", st)

        setup(); json.dump([{"number": 4, "headRefName": "fix/W-1-stubx-1"}], open(os.path.join(tmp, "into.json"), "w"))
        rc, st = run_land(); check("held: /stub's own fix PR still open", rc == 1 and "fix/W-1-stubx-1" in " ".join(st["reasons"]), st)
        setup(); json.dump([{"number": 5, "headRefName": "feat/w-1-x"}], open(os.path.join(tmp, "into.json"), "w"))
        rc, st = run_land(); check("held: a stack of this integration still open", rc == 1 and "feat/w-1-x" in " ".join(st["reasons"]), st)
        setup(); json.dump([{"number": 6, "headRefName": "feat/w-10-x"}, {"number": 7, "headRefName": "docs/w-1-note"}], open(os.path.join(tmp, "into.json"), "w"))
        rc, st = run_land(); check("a dossier in flight and a docs PR hold nothing back", rc == 0 and st["state"] == "merged", st)

        setup(); write_json(os.path.join(runs, key + ".runner.json"), {"command": "stub", "state": "failed", "pid": 0})
        rc, st = run_land(); check("held: the /stub run failed, and GitHub is not asked", rc == 1 and "failed" in st["reasons"][0] and calls() == "", st)
        setup(); write_json(os.path.join(runs, key + ".runner.json"), {"command": "work-on", "state": "done", "pid": 0})
        rc, st = run_land(); check("held: a done run that is not /stub", rc == 1 and "pr merge" not in calls(), st)

        all_ = read_json(settings_path(), {}); all_[repo]["guarded_paths"] = ["a.txt"]; write_json(settings_path(), all_)
        setup(); rc, st = run_land(); check("held: the stub changes a guarded path", rc == 1 and "a.txt" in " ".join(st["reasons"]), st)
        all_[repo].pop("guarded_paths"); write_json(settings_path(), all_)

        setup(state="MERGED"); rc, st = run_land()
        check("merged by the owner: recorded, no merge call", rc == 0 and st["state"] == "merged" and "pr merge" not in calls(), st)
        setup(); open(os.path.join(tmp, "refuse"), "w").write("1"); rc, st = run_land()
        check("GitHub refuses (the head moved): it waits, and says why", rc == 1 and "refused" in st["reasons"][0] and (read_json(os.path.join(runs, key + ".integrate.json"), {}) or {}).get("state") == "stub", st)
        setup(isDraft=True); run_land(); run_land()
        check("the timeline gets one line per change of reasons", open(os.path.join(runs, key + ".jsonl")).read().count("Auto-merge is holding") == 1)
        setup(isDraft=True); import contextlib, io
        with contextlib.redirect_stdout(io.StringIO()):
            rc = land(w, "stub/x", ["W-1"], dry=True)
        check("--check changes nothing", rc == 1 and not os.path.exists(os.path.join(runs, key + ".land.json")))

        import merge_dossier
        setup();
        with contextlib.redirect_stdout(io.StringIO()):
            merge_dossier.revive()
        check("merge_dossier --revive looks at it", "pr merge 9" in calls(), calls())
    print("FAILED: " + ", ".join(fails) if fails else "all ok")
    return 1 if fails else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    ap.add_argument("--root")
    ap.add_argument("--enable", action="store_true", help="turn it on for this repository")
    ap.add_argument("--disable", action="store_true", help="turn it off for this repository")
    ap.add_argument("--receipt-context", default="", help="the commit status that carries the full run's receipt (default local-stack)")
    ap.add_argument("--check", metavar="STUB", help="print what would hold this stub back; merges nothing")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args(argv)
    if a.selftest:
        return selftest()
    if not a.root or (a.enable + a.disable + bool(a.check)) != 1:
        ap.print_usage(sys.stderr)
        return 2
    root = os.path.abspath(a.root)
    if a.check:
        return land(root, a.check, dry=True)
    set_enabled(repo_name(root), a.enable, a.receipt_context)
    print(f"land_stub: {'on' if a.enable else 'off'} for {repo_name(root)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
