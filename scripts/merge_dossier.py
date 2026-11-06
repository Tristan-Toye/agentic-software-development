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
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
RUNNER = os.path.join(HERE, "run_dossier.py")


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
    if st.get("state") in ("running", "waiting", "paused") and alive(st.get("pid")):
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
    drafts = [p for p in chain if p.get("isDraft")]
    if drafts:
        return [], "still a draft: " + ", ".join(f"#{p['number']}" for p in drafts) + "; mark it ready for review first"
    return chain, ""


def mark_detector(repo: str, chain: list[dict], dossier: str) -> None:
    """Record the PRs as started in the merge detector's file, so it does not start a second cleanup run."""
    path = os.path.join(runs_dir(), f"{repo}.merges.json")
    seen = read_json(path, {}) or {}
    for p in chain:
        seen[str(p["number"])] = {"id": dossier, "base": chain[0].get("baseRefName", ""), "mergedAt": now(), "state": "started", "at": now()}
    write_json(path, seen)


def merge(root: str, dossier: str, method: str, dry: bool) -> int:
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
    r = subprocess.run([sys.executable, RUNNER, dossier, "--root", root, "--command", "after-merge", "--items", ",".join(map(str, nums)), "--model", "sonnet"],
                       capture_output=True, text=True, timeout=60)
    after = (r.stdout.strip().splitlines() or [r.stderr.strip()])[0] if (r.stdout or r.stderr) else ""
    print(f"merge_dossier: merged {label} into {base}; cleanup: {after[:200]}")
    return 0


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
    print(json.dumps([p for p in prs if p.get("state", "OPEN") == "OPEN"])); sys.exit(0)
if a[:2] == ["stack", "link"]: sys.exit(int(os.environ.get("FAKE_LINK_RC", "0")))
if a[:2] in (["stack", "merge"], ["pr", "merge"]):
    if os.environ.get("FAKE_MERGE_RC", "0") != "0": print("merge blocked by branch protection"); sys.exit(1)
    for p in prs: p["state"] = "MERGED"
    json.dump(prs, open(os.path.join(d, "prs.json"), "w")); sys.exit(0)
if a[:2] == ["pr", "view"]:
    print(next(p.get("state", "OPEN") for p in prs if str(p["number"]) == a[2])); sys.exit(0)
sys.exit(3)
'''


def selftest() -> int:
    fails: list[str] = []

    def check(name: str, ok: bool) -> None:
        print(("ok   " if ok else "FAIL ") + name)
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
        RUNNER = saved
    print("FAILED: " + ", ".join(fails) if fails else "all ok")
    return 1 if fails else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    ap.add_argument("id", nargs="?")
    ap.add_argument("--root")
    ap.add_argument("--method", choices=["merge", "squash", "rebase"], default="merge")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args(argv)
    if a.selftest:
        return selftest()
    if not a.id or not a.root or not re.fullmatch(r"[A-Za-z][A-Za-z0-9]*-\d+", a.id):
        ap.print_usage(sys.stderr)
        return 2
    return merge(os.path.abspath(a.root), a.id, a.method, a.dry_run)


if __name__ == "__main__":
    sys.exit(main())
