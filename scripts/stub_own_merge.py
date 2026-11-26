#!/usr/bin/env python3
"""Merge one of a /stub run's own pull requests into that run's stub, and nothing else.

# Usage
    stub_own_merge.py <pr> [--root R]
    stub_own_merge.py --selftest

# Why it exists

Every runner session is refused `gh pr merge` (run_dossier.py RULING_DENY): agents never merge a pull request. A /stub
run is the one exception its command file names. The owner's go-ahead for the integration covers merging the run's
own fix, map-refresh and development-sync pull requests into the stub under integration (primary-agents/stub.md, Steps
1, 4 and 8). Until this script, that yes could not be carried out: the refusal outranks any allow rule, so each merge
became an owner question that an answer could never unlock. The runner allows this script, and only this script, in a
/stub session (`ASD_STUB_BRANCH` is the stub it integrates). Everything else stays refused.

# What it checks, all of it, before it merges

1. `ASD_STUB_BRANCH` is set and reads `stub/<slug>`. Without it this is not a /stub session: refused.
2. The pull request's base is exactly that stub.
3. Its head branch is one of the run's own: `fix/<ID>-stub<slug>-<round>` (Step 8),
   `chore/map-refresh-<slug>` or `chore/map-refresh-<slug>-integrate` (Step 4, map_refresh_pr.py), or
   `chore/stub-<slug>-sync-dev` (Step 1), each optionally with a `-<n>` retry suffix.
4. It is open, not a draft, not `CONFLICTING`, has no `CHANGES_REQUESTED` review, and its checks on the head are all
   in SUCCESS, SKIPPED or NEUTRAL (at least one check).
5. The merge is `gh pr merge <pr> --merge --match-head-commit <head>`, so a push after the check is never merged.

A pull request that is already merged into the stub reads `already merged` (exit 0).

# Output and exit

One line: `MERGED #<pr> <head> into <stub>`, `ALREADY-MERGED #<pr>`, or `REFUSED #<pr>: <reasons>`.
Exit 0 merged or already merged, 1 refused or the merge failed, 2 bad usage. A refusal is not an owner question: red
or pending checks mean wait (`wait_ci.py`) or fix; a branch that is not the run's own was never this run's to merge.
`ASD_GH_BIN` replaces `gh` (tests).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from merge_dossier import gh  # noqa: E402

PASSED = ("SUCCESS", "SKIPPED", "NEUTRAL")
STUB_RE = re.compile(r"^stub/(\d{4}-\d{2}-\d{2}(?:-\d+)?)$")
FIELDS = "number,state,isDraft,baseRefName,headRefName,headRefOid,mergeable,reviewDecision,statusCheckRollup"


def own_head(head: str, slug: str) -> bool:
    """True when `head` is a branch the /stub run for stub/<slug> opens itself."""
    s = re.escape(slug)
    pats = (rf"fix/[A-Za-z]+-\d+-stub{s}-\d+", rf"chore/map-refresh-{s}(?:-integrate)?(?:-\d+)?",
            rf"chore/stub-{s}-sync-dev(?:-\d+)?")
    return any(re.fullmatch(p, head or "") for p in pats)


def refusals(pr: dict, stub: str) -> list[str]:
    """Why `pr` may not be merged into `stub` by the run ([] when it may)."""
    m = STUB_RE.match(stub)
    slug = m.group(1) if m else ""
    out = []
    if pr.get("baseRefName") != stub:
        out.append(f"base is {pr.get('baseRefName') or '?'}, not {stub}")
    if not own_head(pr.get("headRefName", ""), slug):
        out.append(f"head {pr.get('headRefName') or '?'} is not one of this run's own branches")
    if pr.get("state") != "OPEN":
        out.append(f"state is {pr.get('state')}")
    if pr.get("isDraft"):
        out.append("it is a draft")
    if pr.get("mergeable") == "CONFLICTING":
        out.append("it conflicts with the stub")
    if pr.get("reviewDecision") == "CHANGES_REQUESTED":
        out.append("a review requests changes")
    rollup = pr.get("statusCheckRollup") or []
    if not rollup:
        out.append("no checks on the head yet (wait_ci.py --pr first)")
    for c in rollup:
        verdict = str(c.get("conclusion") or c.get("state") or c.get("status") or "").upper()
        if verdict not in PASSED:
            out.append(f"check {c.get('name') or c.get('context') or '?'} reads {verdict or 'nothing'}")
    return out


def merge_own(root: str, number: str) -> int:
    stub = (os.environ.get("ASD_STUB_BRANCH") or "").strip()
    if not STUB_RE.match(stub):
        print(f"REFUSED #{number}: not a /stub run session (ASD_STUB_BRANCH is {stub or 'unset'})")
        return 1
    rc, out = gh(root, "pr", "view", number, "--json", FIELDS)
    if rc != 0:
        print(f"REFUSED #{number}: gh pr view failed: {out[:300]}")
        return 1
    pr = json.loads(out)
    if pr.get("state") == "MERGED" and pr.get("baseRefName") == stub:
        print(f"ALREADY-MERGED #{number}")
        return 0
    why = refusals(pr, stub)
    if why:
        print(f"REFUSED #{number}: " + "; ".join(why))
        return 1
    head = pr.get("headRefOid", "")
    rc, out = gh(root, "pr", "merge", number, "--merge", "--match-head-commit", head)
    if rc != 0:
        print(f"REFUSED #{number}: the merge failed: {out[:300]}")
        return 1
    rc, out = gh(root, "pr", "view", number, "--json", "state")
    state = json.loads(out).get("state") if rc == 0 else "?"
    if state != "MERGED":
        print(f"REFUSED #{number}: merge returned but the pull request reads {state}")
        return 1
    print(f"MERGED #{number} {head[:12]} into {stub}")
    return 0


# ---------------------------------------------------------------- selftest
FAKE = r'''
import json, os, sys
d = os.environ["FAKE_DIR"]; a = sys.argv[1:]
open(os.path.join(d, "calls.txt"), "a").write(" ".join(a) + "\n")
pr = json.load(open(os.path.join(d, "pr.json")))
if a[:2] == ["pr", "view"]:
    print(json.dumps(pr)); sys.exit(0)
if a[:2] == ["pr", "merge"]:
    if a[a.index("--match-head-commit") + 1] != pr["headRefOid"]:
        print("head moved"); sys.exit(1)
    pr["state"] = "MERGED"; json.dump(pr, open(os.path.join(d, "pr.json"), "w")); sys.exit(0)
sys.exit(3)
'''


def selftest() -> int:
    fails = []

    def check(name: str, ok: bool, why: object = "") -> None:
        print(("ok   " if ok else "FAIL ") + name + ("" if ok or not why else "  — " + str(why)[:300]))
        if not ok:
            fails.append(name)

    with tempfile.TemporaryDirectory() as tmp:
        open(os.path.join(tmp, "gh.py"), "w").write(FAKE)
        os.environ.update(ASD_GH_BIN=f"{sys.executable} {os.path.join(tmp, 'gh.py')}", FAKE_DIR=tmp,
                          ASD_STUB_BRANCH="stub/2026-11-18")
        green = [{"name": "Rust", "conclusion": "SUCCESS"}, {"name": "Opt", "conclusion": "SKIPPED"}]

        def run(**over) -> tuple[int, str, str]:
            pr = {"number": 7, "state": "OPEN", "isDraft": False, "baseRefName": "stub/2026-11-18",
                  "headRefName": "fix/W-185-stub2026-11-18-1", "headRefOid": "abc123", "mergeable": "MERGEABLE",
                  "reviewDecision": "", "statusCheckRollup": green}
            json.dump({**pr, **over}, open(os.path.join(tmp, "pr.json"), "w"))
            if os.path.exists(os.path.join(tmp, "calls.txt")):
                os.remove(os.path.join(tmp, "calls.txt"))
            p = subprocess.run([sys.executable, os.path.abspath(__file__), "7", "--root", tmp], capture_output=True, text=True)
            calls = open(os.path.join(tmp, "calls.txt")).read() if os.path.exists(os.path.join(tmp, "calls.txt")) else ""
            return p.returncode, p.stdout.strip(), calls

        rc, out, calls = run()
        check("a green own fix PR merges", rc == 0 and out.startswith("MERGED #7") and "--match-head-commit abc123" in calls, out)
        for head in ("chore/map-refresh-2026-11-18", "chore/map-refresh-2026-11-18-integrate", "chore/stub-2026-11-18-sync-dev"):
            rc, out, _ = run(headRefName=head)
            check(f"own branch {head} merges", rc == 0, out)
        for name, over, want in [
            ("another dossier's PR", {"headRefName": "feat/W-185-thing"}, "not one of this run's own"),
            ("another stub's fix branch", {"headRefName": "fix/W-185-stub2026-11-24-1"}, "not one of this run's own"),
            ("base development", {"baseRefName": "development"}, "base is development"),
            ("base another stub", {"baseRefName": "stub/2026-11-24"}, "base is stub/2026-11-24"),
            ("a red check", {"statusCheckRollup": [{"name": "Rust", "conclusion": "FAILURE"}]}, "check Rust reads FAILURE"),
            ("a pending check", {"statusCheckRollup": [{"name": "Rust", "status": "IN_PROGRESS"}]}, "reads IN_PROGRESS"),
            ("no checks", {"statusCheckRollup": []}, "no checks"),
            ("a draft", {"isDraft": True}, "draft"),
            ("a conflict", {"mergeable": "CONFLICTING"}, "conflicts"),
            ("changes requested", {"reviewDecision": "CHANGES_REQUESTED"}, "requests changes"),
        ]:
            rc, out, calls = run(**over)
            check(f"refused: {name}", rc == 1 and want in out and "pr merge" not in calls, out)
        rc, out, calls = run(state="MERGED")
        check("already merged reads 0, no merge call", rc == 0 and out.startswith("ALREADY-MERGED") and "pr merge" not in calls, out)
        os.environ["ASD_STUB_BRANCH"] = "stub/2026-11-24-2"
        rc, out, _ = run(baseRefName="stub/2026-11-24-2", headRefName="fix/W-024-stub2026-11-24-2-1")
        check("a suffixed stub's own fix merges", rc == 0, out)
        rc, out, _ = run(baseRefName="stub/2026-11-24-2", headRefName="chore/map-refresh-2026-11-24-2-integrate")
        check("a suffixed stub's map refresh merges", rc == 0, out)
        for bad in ("", "development", "production", "stub/x"):
            os.environ["ASD_STUB_BRANCH"] = bad
            rc, out, calls = run(baseRefName=bad or "stub/2026-11-18")
            check(f"refused outside a /stub session (ASD_STUB_BRANCH={bad or 'unset'})", rc == 1 and "not a /stub run" in out and not calls, out)
    print(f"{'FAILED' if fails else 'passed'}: {len(fails)} failing")
    return 1 if fails else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    ap.add_argument("pr", nargs="?")
    ap.add_argument("--root", default=os.getcwd())
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if not a.pr or not a.pr.isdigit():
        ap.print_usage(sys.stderr)
        return 2
    return merge_own(a.root, a.pr)


if __name__ == "__main__":
    sys.exit(main())
