#!/usr/bin/env python3
"""List and merge a repository's non-code pull requests into development: plans and research documents.

The dashboard's "Non-code PRs" panel on the repo page and its "Merge all" button run this; no agent merges.

# Usage

    merge_docs_prs.py --root R list          # JSON: one row per open plan/* or docs/* PR into development
    merge_docs_prs.py --root R merge [--dry-run]
    merge_docs_prs.py --selftest

A row says whether the PR can merge now, and why not: `docsOnly` (every file under `.discovery/`, `docs/` or
Markdown, the rule the repository's enforce-pr-source-development check applies), `draft`, `mergeable` (GitHub's
CONFLICTING/MERGEABLE/UNKNOWN), `checks` (red, pending or green), and `dossier`: the dossier id the PR adds, with
`duplicate` when another open PR or development already has that id (two concurrent plans can mint the same number;
the later one is renumbered with `validate_pipeline.py --finalize-ids`, never merged as it is).

`merge` merges every row that is ready, oldest PR first, with a merge commit, and re-reads the list after each merge
(a merge can make a later PR conflict or its id a duplicate). It prints one line per PR. Exit 0 when every ready PR
merged, 1 when one failed. `ASD_GH_BIN` replaces `gh` (tests).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys

DOC_RE = re.compile(r"^(\.discovery/|docs/)|\.md$")
ID_RE = re.compile(r"^\.discovery/dossiers/([A-Z][A-Z0-9]*-\d+)-[^/]*\.md$")


def gh(root: str, *args: str, timeout: int = 120) -> tuple[int, str]:
    cmd = shlex.split(os.environ.get("ASD_GH_BIN") or "gh") + list(args)
    try:
        p = subprocess.run(cmd, cwd=root, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired) as e:
        return 1, str(e)
    return p.returncode, (p.stdout if p.returncode == 0 else p.stdout + p.stderr).strip()


def checks_of(rollup: list) -> str:
    states = [str(c.get("conclusion") or c.get("state") or c.get("status") or "").upper() for c in rollup or []]
    if any(s in ("FAILURE", "ERROR", "CANCELLED", "TIMED_OUT", "ACTION_REQUIRED") for s in states):
        return "red"
    if any(s in ("", "PENDING", "QUEUED", "IN_PROGRESS", "EXPECTED", "WAITING") for s in states):
        return "pending"
    return "green"


def dev_ids(root: str) -> set[str]:
    """Dossier ids already on origin/development."""
    subprocess.run(["git", "-C", root, "fetch", "-q", "origin", "development"], capture_output=True, timeout=120)
    out = subprocess.run(["git", "-C", root, "ls-tree", "--name-only", "origin/development", ".discovery/dossiers/"],
                         capture_output=True, text=True, timeout=30).stdout
    return {m.group(1) for line in out.splitlines() if (m := ID_RE.match(line.strip()))}


def rows(root: str, merged_ids: set[str] | None = None) -> tuple[list[dict], str]:
    rc, out = gh(root, "pr", "list", "--base", "development", "--state", "open", "--limit", "100",
                 "--json", "number,headRefName,title,isDraft,mergeable,statusCheckRollup,files,url,createdAt")
    if rc != 0:
        return [], "gh pr list failed: " + out[:200]
    try:
        prs = json.loads(out or "[]")
    except ValueError:
        return [], "gh pr list gave no JSON"
    prs = sorted([p for p in prs if re.match(r"(plan|docs)/", p.get("headRefName", ""))], key=lambda p: p["number"])
    taken = dev_ids(root) | (merged_ids or set())   # ids merged in this run count even before the fetch shows them
    seen: dict[str, int] = {}
    out_rows = []
    for p in prs:
        files = [f.get("path", "") for f in p.get("files") or []]
        code = [f for f in files if not DOC_RE.search(f)]
        ids = sorted({m.group(1) for f in files if (m := ID_RE.match(f))})
        dup = ""
        for i in ids:
            if i in taken:
                dup = f"{i} is already on development"
            elif i in seen:
                dup = f"{i} is also added by #{seen[i]}"
            else:
                seen[i] = p["number"]
        r = {"number": p["number"], "head": p["headRefName"], "title": p.get("title", ""), "url": p.get("url", ""),
             "kind": "plan" if p["headRefName"].startswith("plan/") else "docs", "dossier": ",".join(ids),
             "docsOnly": not code, "codeFiles": code[:5], "draft": bool(p.get("isDraft")), "mergeable": p.get("mergeable") or "UNKNOWN",
             "checks": checks_of(p.get("statusCheckRollup") or []), "duplicate": dup}
        why = ("changes code: " + ", ".join(code[:3])) if code else "draft" if r["draft"] else \
            ("dossier id taken: " + dup + "; renumber it (validate_pipeline.py --finalize-ids) first") if dup else \
            "conflicts with development" if r["mergeable"] == "CONFLICTING" else \
            "checks are red" if r["checks"] == "red" else "checks still running" if r["checks"] == "pending" else \
            "GitHub has not computed mergeability yet" if r["mergeable"] == "UNKNOWN" else ""
        r["ready"], r["blocked"] = not why, why
        out_rows.append(r)
    return out_rows, ""


def merge(root: str, dry: bool) -> int:
    done: set[int] = set()
    merged_ids: set[str] = set()
    failed = False
    while True:
        rs, err = rows(root, merged_ids)
        if err:
            print(f"merge_docs_prs: {err}")
            return 1
        ready = [r for r in rs if r["ready"] and r["number"] not in done]
        if not ready:
            for r in rs:
                if not r["ready"]:
                    print(f"skip #{r['number']} {r['head']}: {r['blocked']}")
            return 1 if failed else 0
        r = ready[0]
        done.add(r["number"])
        if dry:
            print(f"would merge #{r['number']} {r['head']}")
            continue
        rc, out = gh(root, "pr", "merge", str(r["number"]), "--merge", timeout=300)
        print(f"{'merged' if rc == 0 else 'FAILED'} #{r['number']} {r['head']}" + ("" if rc == 0 else f": {out[-200:]}"))
        failed = failed or rc != 0
        if rc == 0 and r["dossier"]:
            merged_ids.update(r["dossier"].split(","))


# ---------------------------------------------------------------- selftest
FAKE = r'''
import json, os, sys
d = os.environ["FAKE_DIR"]; a = sys.argv[1:]
open(os.path.join(d, "calls.txt"), "a").write(" ".join(a) + "\n")
prs = json.load(open(os.path.join(d, "prs.json")))
if a[:2] == ["pr", "list"]: print(json.dumps([p for p in prs if p.get("state", "OPEN") == "OPEN"])); sys.exit(0)
if a[:2] == ["pr", "merge"]:
    for p in prs:
        if str(p["number"]) == a[2]: p["state"] = "MERGED"
    json.dump(prs, open(os.path.join(d, "prs.json"), "w")); sys.exit(0)
sys.exit(3)
'''


def selftest() -> int:
    import tempfile
    fails = []

    def check(name: str, ok: bool, why: str = "") -> None:
        print(("ok   " if ok else "FAIL ") + name + ("" if ok or not why else "  — " + why[:300]))
        if not ok:
            fails.append(name)

    with tempfile.TemporaryDirectory() as tmp:
        root = os.path.join(tmp, "repo"); os.makedirs(root)
        subprocess.run(["git", "init", "-q", root], check=True)
        open(os.path.join(tmp, "gh.py"), "w").write(FAKE)
        green = [{"conclusion": "SUCCESS"}]
        P = lambda n, head, files, **kw: {"number": n, "headRefName": head, "title": f"t{n}", "url": "u", "isDraft": False, "mergeable": "MERGEABLE",
                                          "statusCheckRollup": green, "files": [{"path": f} for f in files], **kw}
        prs = [P(436, "plan/a", [".discovery/dossiers/W-147-a.md"]), P(437, "plan/b", [".discovery/dossiers/W-147-b.md"]),
               P(438, "plan/c", [".discovery/dossiers/W-148-c.md"]), P(434, "docs/r", ["docs/r.md", "reading.html"]),
               P(500, "feat/x", ["src/x.rs"]), P(439, "plan/d", [".discovery/dossiers/W-150-d.md"], statusCheckRollup=[{"status": "IN_PROGRESS"}])]
        json.dump(prs, open(os.path.join(tmp, "prs.json"), "w"))
        os.environ.update(ASD_GH_BIN=f"{sys.executable} {os.path.join(tmp, 'gh.py')}", FAKE_DIR=tmp)
        rs, _ = rows(root)
        by = {r["number"]: r for r in rs}
        check("list: only plan/* and docs/* PRs, oldest first", [r["number"] for r in rs] == [434, 436, 437, 438, 439], str([r["number"] for r in rs]))
        check("list: a docs PR with code is not ready", not by[434]["ready"] and "reading.html" in by[434]["blocked"])
        check("list: the second PR with the same dossier id waits for renumbering", by[436]["ready"] and not by[437]["ready"] and "W-147" in by[437]["blocked"])
        check("list: pending checks wait", not by[439]["ready"] and "running" in by[439]["blocked"])
        import io, contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = merge(root, False)
        calls = open(os.path.join(tmp, "calls.txt")).read()
        check("merge: merges the ready ones in order, skips the rest with why", rc == 0 and "pr merge 436 --merge" in calls and "pr merge 438 --merge" in calls
              and "pr merge 437" not in calls and "pr merge 434" not in calls and "skip #437" in buf.getvalue(), buf.getvalue())
    print("FAILED: " + ", ".join(fails) if fails else "all ok")
    return 1 if fails else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    ap.add_argument("action", nargs="?", choices=["list", "merge"])
    ap.add_argument("--root")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args(argv)
    if a.selftest:
        return selftest()
    if not a.action or not a.root:
        ap.print_usage(sys.stderr)
        return 2
    if a.action == "list":
        rs, err = rows(os.path.abspath(a.root))
        print(json.dumps({"error": err} if err else {"prs": rs}))
        return 1 if err else 0
    return merge(os.path.abspath(a.root), a.dry_run)


if __name__ == "__main__":
    sys.exit(main())
