#!/usr/bin/env python3
"""Which dossier ids a LIVE /plan run has claimed but whose own run name does not carry them.

A /plan started from a free-text seed runs as `<repo>.seed-<slug>`, and a plan-from-deferred run as
`<repo>.<ID>.plan-D-n`. /plan claims the dossier id in Phase 1 and every later `stage` event of the run's jsonl names it
("W-218 (claimed, final), key ..."); that is the one structured link from such a run to its dossier, and the dossier reads
`planned` while the run is still at work. The dashboard (shared/questions/planning.ts) ports this parsing; both are pinned
against the fixture shared/test/fixtures/planning-runs by their tests. Used by auto_work.py and run_dossier.py.

    planning_runs.py --selftest
"""
from __future__ import annotations

import glob
import json
import os
import re
import sys

LIVE = ("running", "testing", "waiting", "queued", "paused", "parked")
CLAIMED_RE = re.compile(r"\b([A-Z][A-Z0-9]*-\d+)\s*\(claimed")
FIXTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "shared", "test", "fixtures", "planning-runs")


def _read_json(path: str):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh) or {}
    except (OSError, ValueError):
        return {}


def planning_runs(repo: str, runs_dir: str) -> dict[str, dict]:
    """{dossier id: {"key": run key, "state": runner state}} for every id a live seed run (`<repo>.seed-*`) or
    plan-from-deferred run (`<repo>.<ID>.plan-D-n`) claimed. Never start a second /plan (or /work-on) on such an id."""
    out: dict[str, dict] = {}
    for path in sorted(glob.glob(os.path.join(runs_dir, f"{glob.escape(repo)}.*.runner.json"))):
        key = os.path.basename(path)[: -len(".runner.json")]
        short = key[len(repo) + 1:]
        if not (short.startswith("seed-") or re.search(r"\.plan-D-\d+$", short)):
            continue
        st = _read_json(path)
        if st.get("command") != "plan" or st.get("state") not in LIVE:
            continue
        try:
            with open(os.path.join(runs_dir, f"{key}.jsonl"), encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    if '"stage"' not in line or "(claimed" not in line:
                        continue
                    try:
                        o = json.loads(line)
                    except ValueError:
                        continue
                    if o.get("kind") == "stage":
                        for m in CLAIMED_RE.findall(str(o.get("text", "")) + " " + str(o.get("stage", ""))):
                            out.setdefault(m.upper(), {"key": key, "state": st.get("state")})
        except OSError:
            pass
    return out


def planning_ids(repo: str, runs_dir: str) -> set[str]:
    return set(planning_runs(repo, runs_dir))


def claimed_why(repo: str, runs_dir: str, dossier: str, own_key: str = "") -> str:
    """Why a /plan or /work-on of `dossier` must not start: a live planning run already claimed the id. "" when it may."""
    hit = planning_runs(repo, runs_dir).get(dossier.upper())
    if not hit or hit["key"] == own_key:
        return ""
    return f"{dossier} is being planned by the live run {hit['key']} ({hit['state']}); nothing started"


def selftest() -> int:
    bad = 0

    def check(name: str, ok: bool, detail=None) -> None:
        nonlocal bad
        print(("ok   " if ok else "FAIL ") + name + ("" if ok else f" :: {detail}"))
        bad += 0 if ok else 1

    got = planning_runs("r", FIXTURE)
    check("fixture: live seed and plan-D runs name their claimed ids; dead, stopped and non-plan runs do not",
          {k: v["key"] for k, v in got.items()} == {"W-218": "r.seed-webhook-rate-limit", "K-13": "r.K-2.plan-D-1"}, got)
    check("fixture: the run state is carried", got["W-218"]["state"] == "running" and got["K-13"]["state"] == "queued", got)
    check("claimed_why names the run", "r.seed-webhook-rate-limit" in claimed_why("r", FIXTURE, "w-218") and claimed_why("r", FIXTURE, "W-1") == "")
    check("claimed_why: the claiming run itself is not refused", claimed_why("r", FIXTURE, "W-218", "r.seed-webhook-rate-limit") == "")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(selftest() if "--selftest" in sys.argv else 2)
