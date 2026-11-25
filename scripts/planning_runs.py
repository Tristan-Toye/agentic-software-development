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
import datetime as dt
import sys

LIVE = ("running", "testing", "waiting", "queued", "paused", "parked")
LEADING_RE = re.compile(r"^\s*(?:Dossier\s+)?([A-Z][A-Z0-9]*-\d+)\b")   # "Dossier W-035 (minted ...", "W-035, local mode": two stage events opening with one id claim it
CLAIMED_RE = re.compile(r"\b([A-Z][A-Z0-9]*-\d+)\s*\(?(?:claimed|minted)")   # "W-218 (claimed, final)", "W-219 claimed (claim_id exit 0)", "Dossier id W-037 minted locally"
FIXTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "shared", "test", "fixtures", "planning-runs")


def _read_json(path: str):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh) or {}
    except (OSError, ValueError):
        return {}


def _question_ids(repo: str, text: str) -> list[str]:
    """Ids of the questions `<repo>.<ID>.Qn` a run asked: once /plan claimed an id, its questions carry it (the run's own
    first question, before the claim, carries the seed slug and so never matches)."""
    return re.findall(rf"\b{re.escape(repo)}\.([A-Z][A-Z0-9]*-\d+)\.Q\d+\b", text)


def planning_runs(repo: str, runs_dir: str) -> dict[str, dict]:
    """{dossier id: {"key": run key, "state": runner state}} for every id a live seed run (`<repo>.seed-*`) or
    plan-from-deferred run (`<repo>.<ID>.plan-D-n`) claimed. Never start a second /plan (or /work-on) on such an id."""
    out: dict[str, dict] = {}
    cands: list[tuple[str, str, dict]] = []
    for path in sorted(glob.glob(os.path.join(runs_dir, f"{glob.escape(repo)}.*.runner.json"))):
        key = os.path.basename(path)[: -len(".runner.json")]
        short = key[len(repo) + 1:]
        if not (short.startswith("seed-") or re.search(r"\.plan-D-\d+$", short)):
            continue
        st = _read_json(path)
        if st.get("command") != "plan" or st.get("state") not in LIVE:
            continue
        for m in _question_ids(repo, " ".join(str(q) for q in st.get("questions") or [])):
            out.setdefault(m.upper(), {"key": key, "state": st.get("state")})
        try:
            with open(os.path.join(runs_dir, f"{key}.jsonl"), encoding="utf-8", errors="replace") as fh:
                leads: dict[str, int] = {}
                for line in fh:
                    if not ("claimed" in line or "Q" in line or re.search(r'"kind":\s*"stage"', line)):
                        continue
                    try:
                        o = json.loads(line)
                    except ValueError:
                        continue
                    if o.get("kind") == "runner":   # "waiting on repo.W-219.Q2", "answered repo.W-219.Q2"
                        for m in _question_ids(repo, str(o.get("text", ""))):
                            out.setdefault(m.upper(), {"key": key, "state": st.get("state")})
                    if o.get("kind") == "stage":
                        for m in CLAIMED_RE.findall(str(o.get("text", "")) + " " + str(o.get("stage", ""))):
                            out.setdefault(m.upper(), {"key": key, "state": st.get("state")})
                        lead = LEADING_RE.match(str(o.get("text", "")))
                        if lead:
                            lid = lead.group(1).upper()
                            leads[lid] = leads.get(lid, 0) + 1
                            if leads[lid] == 2:
                                out.setdefault(lid, {"key": key, "state": st.get("state")})
        except OSError:
            pass
        cands.append((key, short, st))
    # Second pass, after every stronger signal: an empty dossier file a run created goes to the run whose seed slug it
    # carries, else to the one live planning run that could have made it; with several and no slug match, to none.
    for pid, slug in sorted({p for _, _, st in cands for p in _placeholder_ids(st)}):
        if pid in out:
            continue
        fits = [(k, st) for k, short, st in cands if pid in dict(_placeholder_ids(st))]
        # The longest seed slug the file's slug starts with ("audio-studio-frontend-live-api" for seed-audio-studio-frontend).
        seeds = [(k.rsplit(".seed-", 1)[1], k, st) for k, st in fits if ".seed-" in k]
        seeds = [x for x in seeds if slug == x[0] or slug.startswith(x[0] + "-")]
        named = [(k, st) for _, k, st in sorted(seeds, key=lambda x: -len(x[0]))[:1]]
        pick = named or (fits if len(fits) == 1 else [])
        if pick:
            out[pid] = {"key": pick[0][0], "state": pick[0][1].get("state")}
    return out


def _placeholder_ids(st: dict) -> list[tuple[str, str]]:
    """(id, slug) of the empty dossier files created after the run started: /plan reserves its number with an empty
    `<ID>-<slug>.md` before it writes anything, and a run may say nothing about the id until much later (podcast W-038,
    2026-11-25: the kanban pull read the empty file as a planned dossier and queued a second /plan of it)."""
    root, started = str(st.get("root") or ""), str(st.get("started") or "")
    if not root or not started:
        return []
    try:
        t0 = dt.datetime.strptime(started[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=dt.timezone.utc).timestamp()
    except ValueError:
        return []
    d = os.path.join(root, ".discovery", "dossiers")
    out = []
    try:
        for name in os.listdir(d):
            m = re.match(r"([A-Z][A-Z0-9]*-\d+)(?:-(.*))?\.md$", name)
            if not m:
                continue
            s = os.stat(os.path.join(d, name))
            if s.st_size == 0 and s.st_mtime >= t0 - 1:
                out.append((m.group(1).upper(), m.group(2) or ""))
    except OSError:
        return []
    return sorted(out)


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
          {k: v["key"] for k, v in got.items()} == {"W-218": "r.seed-webhook-rate-limit", "K-13": "r.K-2.plan-D-1", "W-219": "r.seed-plain-claim", "W-220": "r.seed-asked", "W-035": "r.seed-minted"}, got)
    check("fixture: the run state is carried", got["W-218"]["state"] == "running" and got["K-13"]["state"] == "queued", got)
    check("claimed_why names the run", "r.seed-webhook-rate-limit" in claimed_why("r", FIXTURE, "w-218") and claimed_why("r", FIXTURE, "W-1") == "")
    check("claim read from a plain 'W-219 claimed' stage and from the run's questions (the seed-single-migration-owner shape)",
          planning_runs("r", FIXTURE).get("W-219", {}).get("key") == "r.seed-plain-claim" and planning_runs("r", FIXTURE).get("W-220", {}).get("key") == "r.seed-asked",
          planning_runs("r", FIXTURE))
    check("claim read from two stage events opening with the id (the seed-audio-codec-library shape); one alone is no claim",
          planning_runs("r", FIXTURE).get("W-035", {}).get("key") == "r.seed-minted" and "W-026" not in planning_runs("r", FIXTURE),
          planning_runs("r", FIXTURE))
    check("claimed_why: the claiming run itself is not refused", claimed_why("r", FIXTURE, "W-218", "r.seed-webhook-rate-limit") == "")
    check("a 'Dossier id W-037 minted locally' stage claims the id (podcast W-037)",
          CLAIMED_RE.findall("Frame approved. Dossier id W-037 minted locally (claim_id gave W-014)") == ["W-037"])
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        runs, root = os.path.join(tmp, "runs"), os.path.join(tmp, "repo")
        dd = os.path.join(root, ".discovery", "dossiers")
        os.makedirs(runs); os.makedirs(dd)

        def seed(slug: str, state: str = "running") -> None:
            with open(os.path.join(runs, f"q.seed-{slug}.runner.json"), "w") as fh:
                json.dump({"command": "plan", "state": state, "root": root, "started": "2020-01-01T00:00:00Z"}, fh)
        for slug in ("alb-edge-hardening", "audio-studio-frontend", "cognito-oidc-provider"):
            seed(slug)
        for name, body in (("W-037-cognito-oidc-provider.md", ""), ("W-038-audio-studio-frontend-live-api.md", ""),
                           ("W-039-unrelated-thing.md", ""), ("W-036-done.md", "---\nstatus: ready\n---\n")):
            with open(os.path.join(dd, name), "w") as fh:
                fh.write(body)
        got = {k: v["key"] for k, v in planning_runs("q", runs).items()}
        check("an empty dossier file goes to the seed whose slug it carries (podcast W-037/W-038); one no seed names, with "
              "several live seeds, to none; a written dossier is no claim",
              got == {"W-037": "q.seed-cognito-oidc-provider", "W-038": "q.seed-audio-studio-frontend"}, got)
        for slug in ("alb-edge-hardening", "audio-studio-frontend"):
            seed(slug, "done")
        got = {k: v["key"] for k, v in planning_runs("q", runs).items()}
        check("with one live planning run, an empty dossier file it could have made is its claim",
              got.get("W-039") == "q.seed-cognito-oidc-provider" and "W-038" in got, got)
        os.utime(os.path.join(dd, "W-039-unrelated-thing.md"), (1e9, 1e9))
        check("an empty file older than the run is not its claim", "W-039" not in planning_runs("q", runs))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(selftest() if "--selftest" in sys.argv else 2)
