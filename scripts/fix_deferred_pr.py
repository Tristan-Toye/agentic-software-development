#!/usr/bin/env python3
"""Open /fix-deferred's stacked pull request and settle the records, in one call.

`/fix-deferred` Step 6 is mechanical: push, link the stack, set title and body, close each item
in the record, log, remove the worktree. This script does all of it from the facts
fix_deferred_prep.py saved (`R/.discovery/deferred/<ID>.fix.json`) and the inputs below.

# Usage

    fix_deferred_pr.py --root R --dossier W-117
        [--fixed D-1=<sha>,D-2=<sha>]      # default: every direct item, at the branch head
        [--test 'D-1=tests/test_a.py::test_retry'] ...   # each fixed item's proving test
        [--to-dossier D-3,D-4]             # items ruled their own dossier (/plan runs them)
        [--dropped D-5]                    # items ruled wontfix
        [--suite 'stub mode: 14 passed; CI green'] [--gates 'gates: 6 passed']
        [--dry-run]
    fix_deferred_pr.py --selftest

In order:

1. Refuses a worktree with uncommitted tracked changes or a branch with no commit over its base.
2. `git push -u origin fix/<ID>-deferred` from the worktree.
3. Dossier pull request still open: `gh stack link --open --base <the dossier PR's base>
   <dossier PR number> fix/<ID>-deferred` (always with --base: without it the dossier's PR would
   move to the default branch). Merged or no pull request: `gh pr create --base <base> --head
   fix/<ID>-deferred`.
4. `gh pr edit` sets the title `fix(<ID>): deferred issues D-...` and the body: one row per item
   (D-ID, kind, title, `fixed (<sha>)` or `-> dossier (/plan <ID> D-n)`), each fixed item's
   proving test, the gates and suite summary.
5. `build_records.py deferred set` for every item (`fixed` with the sha, `dossier`, `wontfix`),
   `DEFERRED-FIXED: D-n <sha>` and `DEFERRED-FIX: PR <url> — fixed <n>, to dossier <m>` lines in
   the build log, committed and pushed.
6. `remove_worktree.py --root R <worktree> --save`.

`--dry-run` prints the title, the body and every command, and changes nothing (nothing is
pushed, set or removed). Exit codes: 0 done; 1 a step failed (it names the step; a re-run
repeats only what is idempotent: push, link, edit); 2 unusable input. No third-party imports.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import build_records  # noqa: E402
import dossier_edit  # noqa: E402
import fix_deferred_prep as prep  # noqa: E402


class Failed(Exception):
    pass


def pairs(spec: str | None) -> dict[str, str]:
    out = {}
    for part in [p for p in (spec or "").split(",") if p.strip()]:
        k, sep, v = part.partition("=")
        out[k.strip()] = v.strip() if sep else ""
    return out


def ids(spec: str | None) -> list[str]:
    return [x.strip() for x in (spec or "").split(",") if x.strip()]


def title_for(did: str, order: list[str]) -> str:
    return f"fix({did}): deferred issues {', '.join(order)}"


def body_for(did: str, rows: list[tuple[str, str, str, str]], tests: dict[str, str], gates: str, suite: str) -> str:
    out = [f"Fixes the deferred issues of {did} in one stacked pull request.\n",
           "| Item | Kind | Title | Result |", "|---|---|---|---|"]
    out += [f"| {a} | {b} | {c.replace('|', '/')} | {d} |" for a, b, c, d in rows]
    out += ["", "## Proving tests", ""]
    out += [f"- {k}: `{v}`" for k, v in tests.items()] or ["- none recorded"]
    out += ["", "## Gates and suite", "", gates or "gates: not stated", suite or "suite: not stated", ""]
    return "\n".join(out)


def sh(cmd: list[str], cwd: str, step: str) -> str:
    rc, out = prep.run(cmd, cwd=cwd)
    if rc != 0:
        raise Failed(f"{step}: {prep.one_line(' '.join(cmd), 120)} -> {prep.one_line(out, 300)}")
    return out


def execute(args, say=print) -> int:
    root = os.path.abspath(args.root)
    did = args.dossier
    try:
        with open(prep.state_path(root, did), encoding="utf-8") as fh:
            st = json.load(fh)
    except (OSError, ValueError):
        say(f"fix_deferred_pr: REFUSED — no {prep.state_path(root, did)}: run fix_deferred_prep.py first")
        return 2
    wt, branch = st["worktree"], st["branch"]
    path = build_records.record_path(os.path.join(root, ".discovery"), "deferred", did)
    byid = {i["id"]: i for i in build_records.load(path, {"items": [], "sources": []})["items"]}
    to_dossier, dropped = ids(args.to_dossier), ids(args.dropped)
    unknown = [x for x in to_dossier + dropped + list(pairs(args.fixed)) if x not in byid]
    if unknown:
        say(f"fix_deferred_pr: REFUSED — {', '.join(unknown)} is not in the record")
        return 2
    head = prep.git(wt, "rev-parse", "--short=10", "HEAD")[1] if os.path.isdir(wt) else "HEAD"
    fixed = pairs(args.fixed) or {i: "" for i in st.get("direct", []) if i not in to_dossier + dropped}
    fixed = {k: (v or head) for k, v in fixed.items()}
    if not fixed and not to_dossier and not dropped:
        say("fix_deferred_pr: REFUSED — no item to report: nothing fixed, nothing routed")
        return 2
    tests = dict(t.split("=", 1) for t in (args.test or []) if "=" in t)
    rows = []
    for k in sorted(fixed, key=lambda x: int(x[2:])):
        rows.append((k, byid[k].get("kind") or "-", byid[k].get("title") or prep.one_line(byid[k].get("evidence"), 80), f"fixed ({fixed[k]})"))
    for k in to_dossier:
        rows.append((k, byid[k].get("kind") or "-", byid[k].get("title") or prep.one_line(byid[k].get("evidence"), 80), f"→ dossier (/plan {did} {k})"))
    for k in dropped:
        rows.append((k, byid[k].get("kind") or "-", byid[k].get("title") or prep.one_line(byid[k].get("evidence"), 80), "dropped (won't fix)"))
    title = title_for(did, [r[0] for r in rows])
    body = body_for(did, rows, tests, args.gates, args.suite)
    link = (["gh", "stack", "link", "--open", "--base", st["stack_base"], str(st["dossier_pr"]), branch]
            if st["kind"] == "open"
            else ["gh", "pr", "create", "--base", st["base"], "--head", branch, "--title", title, "--body", body])
    if args.dry_run:
        say("DRY-RUN: nothing is pushed, linked, set or removed")
        say(f"TITLE: {title}\nBODY:\n{body}")
        say(f"push: git -C {wt} push -u origin {branch}")
        say("link: " + " ".join(link[:7] if st["kind"] != "open" else link))
        for k, sha in fixed.items():
            say(f"record: {k} -> fixed ({sha})")
        for k in to_dossier:
            say(f"record: {k} -> dossier")
        for k in dropped:
            say(f"record: {k} -> wontfix")
        say(f"remove: remove_worktree.py --root {root} {wt} --save")
        return 0
    step = "check"
    try:
        if prep.git(wt, "status", "--porcelain", "--untracked-files=no")[1].strip():
            raise Failed(f"check: {wt} has uncommitted tracked changes; commit or revert them first")
        base_ref = f"origin/{st['base']}"
        if prep.git(wt, "rev-list", "--count", f"{base_ref}..HEAD")[1].strip() in ("", "0"):
            raise Failed(f"check: {branch} has no commit over {base_ref}")
        step = "push"
        sh(["git", "-C", wt, "push", "-q", "-u", "origin", branch], wt, step)
        step = "link"
        out = sh(link, wt, step)
        step = "edit"
        url = sh(["gh", "pr", "view", branch, "--json", "url", "-q", ".url"], wt, step).strip()
        sh(["gh", "pr", "edit", url, "--title", title, "--body", body], wt, step)
        say(f"PR: {url}")
        step = "records"
        wt_dossier = prep.find_dossier(wt, did) or st["dossier"]
        log = []
        for k, sha in fixed.items():
            build_records.deferred_set(path, k, "fixed", sha)
            log += [f"- DEFERRED-FIXED: {k} {sha}"]
        for k in to_dossier:
            build_records.deferred_set(path, k, "dossier", None)
        for k in dropped:
            build_records.deferred_set(path, k, "wontfix", None)
        log.append(f"- DEFERRED-FIX: PR {url} — fixed {len(fixed)}, to dossier {len(to_dossier)}")
        say(dossier_edit.log_or_say(wt_dossier, log))
        prep.commit_log(wt, did, "deferred fix log")
        sh(["git", "-C", wt, "push", "-q", "origin", branch], wt, "push")
        step = "remove"
        sh([sys.executable, os.path.join(HERE, "remove_worktree.py"), "--root", root, wt, "--save"], root, step)
        say(f"DONE: fixed {len(fixed)}, to dossier {len(to_dossier)}, dropped {len(dropped)}; worktree removed")
    except (Failed, build_records.Refused) as err:
        say(f"fix_deferred_pr: FAILED at {step} — {err}")
        return 1
    return 0


# -- selftest ---------------------------------------------------------------

def selftest() -> int:
    ok = True

    def check(name: str, cond: bool, detail: str = "") -> None:
        nonlocal ok
        print(("PASS  " if cond else "FAIL  ") + name + ("" if cond else f"  {detail}"))
        ok = ok and cond

    check("title lists the items", title_for("W-9", ["D-1", "D-2"]) == "fix(W-9): deferred issues D-1, D-2")
    body = body_for("W-9", [("D-1", "bug", "t", "fixed (abc)"), ("D-2", "-", "u", "→ dossier (/plan W-9 D-2)")],
                    {"D-1": "tests/t.py::a"}, "gates: 3 passed", "suite: ci green")
    check("body has a row per item, the proving test and the summary",
          "| D-1 | bug | t | fixed (abc) |" in body and "→ dossier (/plan W-9 D-2)" in body
          and "D-1: `tests/t.py::a`" in body and "gates: 3 passed" in body and "suite: ci green" in body, body)
    check("pairs parses D-n=sha lists", pairs("D-1=abc, D-2=def") == {"D-1": "abc", "D-2": "def"})

    import contextlib
    import io
    with tempfile.TemporaryDirectory() as tmp:
        # a fixture repo with a prepared state, driven through prep's own selftest layout
        def sh(cwd, *a):
            return subprocess.run(["git", "-C", cwd, *a], capture_output=True, text=True, check=True).stdout.strip()
        origin, main = os.path.join(tmp, "origin.git"), os.path.join(tmp, "repo")
        subprocess.run(["git", "init", "-q", "--bare", "-b", "stub", origin], check=True)
        subprocess.run(["git", "clone", "-q", origin, main], check=True, capture_output=True)
        for k, v in (("user.email", "t@example.invalid"), ("user.name", "t")):
            sh(main, "config", k, v)
        os.makedirs(os.path.join(main, ".discovery", "dossiers"))
        with open(os.path.join(main, ".discovery", "dossiers", "W-9-x.md"), "w") as fh:
            fh.write("---\nid: W-9\nbranch: feat/W-9\npr: null\n---\n\n## Build log\n\n- start\n")
        with open(os.path.join(main, "a.py"), "w") as fh:
            fh.write("x = 1\n")
        sh(main, "checkout", "-q", "-b", "stub")
        sh(main, "add", "-A")
        sh(main, "commit", "-q", "-m", "init")
        sh(main, "push", "-q", "origin", "stub")
        rec = build_records.record_path(os.path.join(main, ".discovery"), "deferred", "W-9")
        items = [{"id": f"D-{n}", "evidence": f"a.py:1 issue {n}", "fix": "f", "boundary": "none", "blast_radius": "x",
                  "state": "fixing", "kind": "bug", "title": f"issue {n}"} for n in (1, 2, 3)]
        build_records.atomic_write(rec, json.dumps({"items": items, "sources": ["s"]}))
        wt, kind, fork = prep.ensure_branch(main, "W-9", "stub")
        with open(os.path.join(wt, "a.py"), "w") as fh:
            fh.write("x = 2\n")
        sh(wt, "commit", "-q", "-am", "fix D-1")
        st = {"id": "W-9", "root": main, "worktree": wt, "branch": "fix/W-9-deferred", "base": "stub", "kind": "none",
              "stack_base": "stub", "dossier_pr": None, "fork": fork, "dossier": os.path.join(wt, ".discovery", "dossiers", "W-9-x.md"),
              "direct": ["D-1"], "asked": {}}
        with open(prep.state_path(main, "W-9"), "w") as fh:
            json.dump(st, fh)
        ns = argparse.Namespace(root=main, dossier="W-9", fixed=None, test=["D-1=tests/a.py::t"], to_dossier="D-2",
                                dropped="D-3", suite="suite: ok", gates="gates: ok", dry_run=True)
        buf = io.StringIO()
        rc = execute(ns, say=lambda s: buf.write(s + "\n"))
        text = buf.getvalue()
        check("dry-run prints title, rows and the commands, exit 0",
              rc == 0 and "TITLE: fix(W-9): deferred issues D-1, D-2, D-3" in text and "| D-2 | bug | issue 2 | → dossier (/plan W-9 D-2) |" in text
              and "gh pr create --base stub" in text and "remove_worktree.py" in text, text)
        check("dry-run changes nothing: the record, the remote and the worktree stay",
              json.load(open(rec))["items"][0]["state"] == "fixing" and os.path.isdir(wt)
              and prep.git(main, "ls-remote", "--heads", "origin", "fix/W-9-deferred")[1] == "")
        # a fake gh on PATH: pushes are real (local origin), gh calls are recorded
        bindir = os.path.join(tmp, "bin")
        os.makedirs(bindir)
        calls = os.path.join(tmp, "gh.calls")
        with open(os.path.join(bindir, "gh"), "w") as fh:
            fh.write(f"#!/bin/sh\necho \"$@\" >> {calls}\ncase \"$*\" in *\"pr view\"*) echo https://github.com/o/r/pull/12;; esac\n")
        os.chmod(os.path.join(bindir, "gh"), 0o755)
        old = os.environ["PATH"]
        os.environ["PATH"] = bindir + os.pathsep + old
        try:
            ns.dry_run = False
            buf = io.StringIO()
            rc = execute(ns, say=lambda s: buf.write(s + "\n"))
        finally:
            os.environ["PATH"] = old
        text = buf.getvalue()
        gh = open(calls).read()
        recd = {i["id"]: i for i in json.load(open(rec))["items"]}
        check("a real run pushes, creates the PR, sets the body, closes the records and removes the worktree",
              rc == 0 and "pr create --base stub --head fix/W-9-deferred" in gh and "pr edit https://github.com/o/r/pull/12" in gh
              and recd["D-1"]["state"] == "fixed" and recd["D-2"]["state"] == "dossier" and recd["D-3"]["state"] == "wontfix"
              and not os.path.exists(wt) and prep.git(main, "ls-remote", "--heads", "origin", "fix/W-9-deferred")[1] != "", text + gh)
        check("the pushed branch carries the build log lines",
              "DEFERRED-FIX: PR https://github.com/o/r/pull/12 — fixed 1, to dossier 1" in
              prep.git(main, "show", "origin/fix/W-9-deferred:.discovery/dossiers/W-9-x.md")[1])
        stack = dict(st, kind="open", stack_base="stub", dossier_pr=7)
        ns2 = argparse.Namespace(root=main, dossier="W-9", fixed="D-1=abc", test=[], to_dossier=None, dropped=None,
                                 suite="", gates="", dry_run=True)
        with open(prep.state_path(main, "W-9"), "w") as fh:
            json.dump(stack, fh)
        buf = io.StringIO()
        execute(ns2, say=lambda s: buf.write(s + "\n"))
        check("an open dossier PR links the stack with --base", "gh stack link --open --base stub 7 fix/W-9-deferred" in buf.getvalue(), buf.getvalue())
    print("selftest:", "ok" if ok else "FAILED")
    return 0 if ok else 1


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--root")
    ap.add_argument("--dossier")
    ap.add_argument("--fixed", help="D-1=<sha>,D-2=<sha> (default: every direct item at the branch head)")
    ap.add_argument("--test", action="append", metavar="D-n=TEST", help="a fixed item's proving test; repeatable")
    ap.add_argument("--to-dossier", help="D-3,D-4: items ruled their own dossier")
    ap.add_argument("--dropped", help="D-5: items ruled wontfix")
    ap.add_argument("--suite", default="")
    ap.add_argument("--gates", default="")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    if args.selftest:
        return selftest()
    if not args.root or not args.dossier:
        ap.error("--root and --dossier are required unless --selftest")
    return execute(args)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
