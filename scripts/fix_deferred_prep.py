#!/usr/bin/env python3
"""Prepare a /fix-deferred run: triage, stacked branch, change-request document, payloads.

`/fix-deferred` Steps 1 to 3 (and the mechanical half of Step 4) are one script call, so the
orchestrator only judges what a script cannot: the items this call flags `ask`.

# Usage

    fix_deferred_prep.py --root R --dossier W-117 [--items D-1,D-3] [--direct D-4]
                         [--test-command CMD] [--hooks TEXT] [--env-script F] [--path-prepend DIR]
                         [--pr-json FILE] [--no-compose]
    fix_deferred_prep.py --selftest

What it does, in order (every step is safe to run again):

1. Reads the deferred record `R/.discovery/deferred/<ID>.yaml` (build_records.py's loader) and
   the dossier's front matter (`branch`, `pr`). `--items` limits the scope; every other item is
   left alone. An item whose state is already fixed / wontfix / dossier is skipped.
2. Finds the base: the dossier's pull request open -> its head branch is the base and its own
   base is the stack base; merged (or no pull request) -> the branch it merged into.
3. Creates branch `fix/<ID>-deferred` and worktree `../<repo>-<ID>-deferred` from `origin/<base>`
   (running the repository's executable `.worktree-setup.sh <R> <worktree>` once, for a new
   worktree only). A branch or worktree that already exists is reused, never recreated.
4. Triage: an item is `direct` unless its boundary names an external contract, its effort
   (`effort: L` in the record) is L, or its `path:line` no longer exists in the worktree: then it
   is `ask`, with the reason. `--direct D-n[,D-m]` treats an item the owner ruled "fix it anyway"
   as direct.
5. Logs `DEFERRED-FIX: branch ...` (new branches only) and `DEFERRED-TRIAGE: D-n -> direct ...`
   through dossier_edit's log path (a line already there is not added again), sets each direct
   item `fixing` in the record, and commits that log in the worktree so the wave starts clean.
6. Writes the change-request document `<worktree>/.agent-staging/fix-deferred/crs-<ID>.md`
   (one `## CR-n` per direct item), runs check_crs.py on it and compose_payloads.py --crs.
7. Saves its facts in `R/.discovery/deferred/<ID>.fix.json` for fix_deferred_pr.py and prints:

       WORKTREE: <path>
       BRANCH: fix/W-117-deferred base <base> fork <sha> (new|reused); dossier PR #<n> <state>; stack base <b>
       DIRECT: D-1, D-2
       ASK: D-3 — <reason>
       CRS: <document> — <check_crs summary>
       PAYLOADS: <wave args file> — <files>
       UNROUTED: ... (compose_payloads' lines, only when it refused)

`--test-command` is the fix implementers' TEST_COMMAND; leave it out only when the dossier's
`GATES:` line (or `.discovery/analysis/gates-<ID>.txt`) names the gates file, which then is it.

Exit codes: 0 prepared (UNROUTED is exit 1: rule on the lines, re-run with `--assign` through
compose_payloads.py by hand); 2 unusable input (no record, unknown D-id, no dossier). No
third-party imports.
"""

from __future__ import annotations

import argparse
import glob
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

CLOSED = ("fixed", "wontfix", "dossier")
LOC_RE = re.compile(r"([\w./+-]+\.\w+):(\d+)")


class Refused(Exception):
    pass


def run(cmd: list[str], cwd: str | None = None) -> tuple[int, str]:
    p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    return p.returncode, (p.stdout + p.stderr).strip()


def git(cwd: str, *a: str) -> tuple[int, str]:
    return run(["git", "-C", cwd, *a])


def one_line(text: str, cap: int = 200) -> str:
    t = re.sub(r"\s+", " ", str(text or "")).strip()
    return t if len(t) <= cap else t[: cap - 1].rstrip() + "…"


# -- the dossier ------------------------------------------------------------

def find_dossier(root: str, did: str) -> str | None:
    hits = sorted(glob.glob(os.path.join(root, ".discovery", "dossiers", f"{did}-*.md")))
    return hits[0] if hits else None


def front(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    m = re.match(r"---\n(.*?)\n---", text, re.S)
    for line in (m.group(1) if m else "").split("\n"):
        k = re.match(r"^([A-Za-z_]+):[ \t]*(.*?)(?:\s+#.*)?$", line)
        if k:
            out[k.group(1)] = k.group(2).strip()
    if m:  # a block-form `pr:` list
        b = re.search(r"^pr:[ \t]*\n((?:[ \t]+-[^\n]*\n?)+)", m.group(1) + "\n", re.M)
        if b:
            out["pr"] = b.group(1)
    return out


def pr_numbers(value: str) -> list[int]:
    return [int(n) for n in re.findall(r"/pull/(\d+)", value or "")]


def pr_state(root: str, number: int, pr_json: str | None) -> dict:
    if pr_json:
        with open(pr_json, encoding="utf-8") as fh:
            return json.load(fh)
    rc, out = run(["gh", "pr", "view", str(number), "--json", "state,baseRefName,headRefName,url"], cwd=root)
    if rc != 0:
        raise Refused(f"gh pr view {number} failed: {one_line(out)}")
    return json.loads(out)


def resolve_base(fm: dict[str, str], root: str, pr_json: str | None) -> dict:
    """{kind: open|merged|none, base, stack_base, pr, pr_state}: where the stacked branch forks and
    what the new pull request's base is."""
    nums = pr_numbers(fm.get("pr", ""))
    if nums:
        st = pr_state(root, nums[-1], pr_json)
        if str(st.get("state", "")).upper() == "OPEN":
            return {"kind": "open", "base": st["headRefName"], "stack_base": st["baseRefName"],
                    "pr": nums[-1], "pr_state": "open"}
        return {"kind": "merged", "base": st["baseRefName"], "stack_base": st["baseRefName"],
                "pr": nums[-1], "pr_state": str(st.get("state", "merged")).lower()}
    branch = fm.get("branch", "").strip().strip("`")
    if not branch or branch.lower() in ("null", "none", "~"):
        raise Refused("the dossier names neither a pull request nor a branch to stack on")
    return {"kind": "none", "base": branch, "stack_base": branch, "pr": None, "pr_state": "none"}


# -- triage -----------------------------------------------------------------

def item_loc(item: dict) -> tuple[str, int] | None:
    for field in (item.get("loc"), item.get("evidence")):
        m = LOC_RE.search(str(field or ""))
        if m:
            return m.group(1), int(m.group(2))
    return None


def effort_of(item: dict) -> str:
    for field in (item.get("effort"), item.get("note"), item.get("fix"), item.get("blast_radius")):
        m = re.search(r"\beffort:\s*([SML])\b", str(field or ""))
        if m:
            return m.group(1)
    return ""


def triage_item(item: dict, worktree: str) -> tuple[str, str]:
    """('direct', '') or ('ask', reason)."""
    boundary = str(item.get("boundary") or "")
    if re.search(r"\bexternal\b", boundary, re.I):
        return "ask", f"external contract — boundary: {one_line(boundary, 120)}"
    if effort_of(item) == "L":
        return "ask", "effort L: days of work, more than one focused test proves"
    loc = item_loc(item)
    if loc:
        path, line = loc
        full = os.path.join(worktree, path)
        if not os.path.isfile(full):
            return "ask", f"no longer applies — {path} does not exist at the stack base"
        with open(full, encoding="utf-8", errors="replace") as fh:
            n = sum(1 for _ in fh)
        if line > n:
            return "ask", f"no longer applies — {path}:{line} is past the end of the file ({n} lines)"
    return "direct", ""


# -- the change-request document -------------------------------------------

def cr_document(did: str, items: list[dict]) -> str:
    parts = [f"# Change requests — deferred issues of {did}\n"]
    for n, it in enumerate(items, 1):
        loc = item_loc(it)
        where = f"{loc[0]}:{loc[1]}" if loc else "unknown:0"
        others = []
        for field in (it.get("evidence"), it.get("fix")):
            for m in LOC_RE.finditer(str(field or "")):
                s = f"{m.group(1)}:{m.group(2)}"
                if s != where and s not in others:
                    others.append(s)
        sib = ", ".join(others) if others else f"none (searched: the deferred record of {did} names no other site)"
        title = one_line(it.get("title") or it.get("evidence"), 100)
        parts.append(
            f"## CR-{n}: {it['id']} — {title}\n"
            f"- Location: {where}\n"
            f"- Observed: {one_line(it.get('evidence'), 400)}\n"
            f"- Required: {one_line(it.get('fix'), 400)}; first a focused test that fails on the defect\n"
            f"- Behaviour held fixed: every behaviour the item does not name; the public signatures\n"
            f"- Rule: {one_line(it.get('kind') or 'deferred issue', 60)}\n"
            f"- Severity: should-fix\n"
            f"- Siblings: {sib}\n")
    return "\n".join(parts)


# -- the worktree -----------------------------------------------------------

def worktree_path(root: str, did: str) -> str:
    root = os.path.realpath(root)
    return os.path.join(os.path.dirname(root), f"{os.path.basename(root)}-{did}-deferred")


def registered(root: str, path: str) -> bool:
    rc, out = git(root, "worktree", "list", "--porcelain")
    want = os.path.realpath(path)
    return rc == 0 and any(l.startswith("worktree ") and os.path.realpath(l[9:]) == want for l in out.splitlines())


def ensure_branch(root: str, did: str, base: str) -> tuple[str, str, str]:
    """(worktree, 'new'|'reused', fork sha). Never recreates an existing branch or worktree."""
    branch = f"fix/{did}-deferred"
    wt = worktree_path(root, did)
    if registered(root, wt):
        rc, out = git(wt, "branch", "--show-current")
        if out != branch:
            raise Refused(f"{wt} is a worktree on {out!r}, not {branch}")
        return wt, "reused", fork_point(wt, base)
    if os.path.exists(wt):
        raise Refused(f"{wt} exists and is not a worktree of this repository")
    git(root, "fetch", "-q", "origin", base)
    have = git(root, "rev-parse", "--verify", "-q", f"refs/heads/{branch}")[0] == 0
    if have:
        rc, out = git(root, "worktree", "add", wt, branch)
        kind = "reused"
    else:
        start = f"origin/{base}" if git(root, "rev-parse", "--verify", "-q", f"origin/{base}")[0] == 0 else base
        rc, out = git(root, "worktree", "add", "-b", branch, wt, start)
        kind = "new"
    if rc != 0:
        raise Refused(f"git worktree add failed: {one_line(out)}")
    setup = os.path.join(root, ".worktree-setup.sh")
    if os.access(setup, os.X_OK):
        rc, out = run([setup, root, wt])
        last = out.splitlines()[-1] if out else ""
        print(f"worktree-setup: exit {rc}" + (f" — {one_line(last, 160)}" if last else ""))
    return wt, kind, fork_point(wt, base)


def fork_point(wt: str, base: str) -> str:
    for ref in (f"origin/{base}", base):
        rc, out = git(wt, "merge-base", "HEAD", ref)
        if rc == 0 and out:
            return out[:12]
    return git(wt, "rev-parse", "--short=12", "HEAD")[1]


def commit_log(wt: str, did: str, what: str) -> None:
    """Commit the dossier log lines just written, so the tree is clean when the wave starts."""
    git(wt, "add", "--", ".discovery/dossiers")
    if git(wt, "diff", "--cached", "--quiet")[0] == 0:
        return
    rc, out = git(wt, "commit", "-q", "--no-verify", "-m", f"chore({did}): {what}")
    if rc != 0:
        print(f"fix_deferred_prep: log commit failed — {one_line(out)}", file=sys.stderr)


# -- main -------------------------------------------------------------------

def state_path(root: str, did: str) -> str:
    return os.path.join(root, ".discovery", "deferred", f"{did}.fix.json")


def prepare(args) -> int:
    root = os.path.abspath(args.root)
    did = args.dossier
    path = build_records.record_path(os.path.join(root, ".discovery"), "deferred", did)
    data = build_records.load(path, {"items": [], "sources": []})
    items = data["items"]
    if not items:
        raise Refused(f"no deferred record at {path} (an older run keeps its ledger in the dossier's ## Build log)")
    scope = [x for x in (args.items or "").split(",") if x]
    ids = {i.get("id") for i in items}
    unknown = [x for x in scope if x not in ids]
    if unknown:
        raise Refused(f"{', '.join(unknown)} is not in the record {path}")
    dossier = find_dossier(root, did)
    if not dossier:
        raise Refused(f"no dossier {did}-*.md under {root}/.discovery/dossiers")
    fm = front(open(dossier, encoding="utf-8").read())
    base = resolve_base(fm, root, args.pr_json)
    wt, kind, fork = ensure_branch(root, did, base["base"])
    branch = f"fix/{did}-deferred"
    wt_dossier = find_dossier(wt, did)
    if not wt_dossier:
        raise Refused(f"the worktree {wt} holds no dossier {did}-*.md: the stack base {base['base']} lacks the build copy")

    forced = {x for x in (args.direct or "").split(",") if x}
    todo = [i for i in items if (not scope or i["id"] in scope) and i.get("state") not in CLOSED]
    direct: list[dict] = []
    asked: dict[str, str] = {}
    for it in todo:
        verdict, why = triage_item(it, wt)
        if verdict == "ask" and it["id"] in forced:
            verdict = "direct"
        if verdict == "direct":
            direct.append(it)
        else:
            asked[it["id"]] = why

    lines = []
    if kind == "new":
        lines.append(f"- DEFERRED-FIX: branch {branch} on {base['base']} @ {fork}")
    lines += [f"- DEFERRED-TRIAGE: {it['id']} → direct — {one_line(it.get('fix'), 140)}" for it in direct]
    if lines:
        print(dossier_edit.log_or_say(wt_dossier, lines))
        commit_log(wt, did, "deferred triage log")
    for it in direct:
        if it.get("state") != "fixing":
            build_records.deferred_set(path, it["id"], "fixing", None)

    out = [f"WORKTREE: {wt}",
           f"BRANCH: {branch} base {base['base']} fork {fork} ({kind}); dossier PR "
           f"{'#%s' % base['pr'] if base['pr'] else 'none'} {base['pr_state']}; stack base {base['stack_base']}",
           "DIRECT: " + (", ".join(i["id"] for i in direct) or "none")]
    out += [f"ASK: {k} — {v}" for k, v in asked.items()]
    if not asked:
        out.append("ASK: none")
    st = {"id": did, "root": root, "worktree": wt, "branch": branch, "base": base["base"], "kind": base["kind"],
          "stack_base": base["stack_base"], "dossier_pr": base["pr"], "fork": fork, "dossier": wt_dossier,
          "direct": sorted({*(i["id"] for i in direct), *_old(state_path(root, did))}), "asked": asked}
    rc = 0
    if direct and not args.no_compose:
        stage = os.path.join(wt, ".agent-staging", "fix-deferred")
        os.makedirs(stage, exist_ok=True)
        crs = os.path.join(stage, f"crs-{did}.md")
        with open(crs, "w", encoding="utf-8") as fh:
            fh.write(cr_document(did, direct))
        rc1, chk = run([sys.executable, os.path.join(HERE, "check_crs.py"), crs])
        summary = chk.splitlines()[-1] if chk else "no output"
        bad = [l for l in chk.splitlines() if re.match(r"\S+ demote \(", l)]
        out.append(f"CRS: {crs} — {summary}" + ("".join("\n  " + b for b in bad)))
        wave = os.path.join(wt, ".agent-staging", "payloads", "fix-wave.json")
        os.makedirs(os.path.dirname(wave), exist_ok=True)
        cmd = [sys.executable, os.path.join(HERE, "compose_payloads.py"), "--kind", "implementer", "--dossier", wt_dossier,
               "--root", wt, "--host", "claude", "--crs", crs, "--out", wave,
               "--hooks", args.hooks or fm.get("hooks") or "none (the dossier records no commit hook)"]
        if args.test_command:
            cmd += ["--test-command", args.test_command]
        if args.env_script:
            cmd += ["--env-script", args.env_script]
        for d in args.path_prepend or []:
            cmd += ["--path-prepend", d]
        rc2, comp = run(cmd)
        if rc2 != 0:
            unrouted = [l for l in comp.splitlines() if l.startswith(("UNROUTED", "CONFLICT"))]
            out += unrouted or ["COMPOSE FAILED:"] + ["  " + l[:300] for l in comp.splitlines()[:8]]
            rc = 1
        else:
            files = [l.split(" -> ")[1].split(" sha ")[0] for l in comp.splitlines() if " -> " in l]
            out.append(f"PAYLOADS: {wave} — " + ", ".join(files))
            st["crs"], st["wave"] = crs, wave
    with open(state_path(root, did), "w", encoding="utf-8") as fh:
        json.dump(st, fh, indent=1)
    print("\n".join(out))
    return rc


def _old(path: str) -> list[str]:
    try:
        with open(path, encoding="utf-8") as fh:
            return list(json.load(fh).get("direct", []))
    except (OSError, ValueError):
        return []


# -- selftest ---------------------------------------------------------------

def selftest() -> int:
    ok = True

    def check(name: str, cond: bool, detail: str = "") -> None:
        nonlocal ok
        print(("PASS  " if cond else "FAIL  ") + name + ("" if cond else f"  {detail}"))
        ok = ok and cond

    def sh(cwd: str, *a: str) -> str:
        return subprocess.run(["git", "-C", cwd, *a], capture_output=True, text=True, check=True).stdout.strip()

    with tempfile.TemporaryDirectory() as tmp:
        origin, main = os.path.join(tmp, "origin.git"), os.path.join(tmp, "repo")
        subprocess.run(["git", "init", "-q", "--bare", "-b", "stub", origin], check=True)
        subprocess.run(["git", "clone", "-q", origin, main], check=True, capture_output=True)
        for k, v in (("user.email", "t@example.invalid"), ("user.name", "t")):
            sh(main, "config", k, v)
        os.makedirs(os.path.join(main, "src"))
        with open(os.path.join(main, "src", "a.py"), "w") as fh:
            fh.write("".join(f"line {i}\n" for i in range(1, 21)))
        os.makedirs(os.path.join(main, ".discovery", "dossiers"))
        dossier = os.path.join(main, ".discovery", "dossiers", "W-9-fix-things.md")
        with open(dossier, "w") as fh:
            fh.write("---\nid: W-9\nstatus: done\nbranch: feat/W-9\npr: [https://github.com/o/r/pull/7]\nhooks: none\njira: null\n---\n\n"
                     "## Contract\n\n### src/a.py\n\n```python\ndef f() -> int: ...\n```\n\n## Work packages\n\n| Package | Owned paths | Depends on |\n|---|---|---|\n| P1 core | src/ | — |\n\n"
                     "## Build log\n\n- start\n")
        sh(main, "checkout", "-q", "-b", "stub")
        sh(main, "add", "-A")
        sh(main, "commit", "-q", "-m", "init")
        sh(main, "push", "-q", "origin", "stub")
        recs = [
            {"id": "D-1", "evidence": "src/a.py:5 retry ignores 429", "fix": "honour 429", "boundary": "contract: none",
             "blast_radius": "one package", "state": "open", "kind": "bug", "note": "effort: S"},
            {"id": "D-2", "evidence": "src/a.py:6 route changes", "fix": "rename route", "boundary": "external: HTTP route",
             "blast_radius": "callers", "state": "open", "note": "effort: M"},
            {"id": "D-3", "evidence": "src/a.py:7 big", "fix": "rewrite", "boundary": "none", "blast_radius": "x",
             "state": "open", "note": "effort: L"},
            {"id": "D-4", "evidence": "src/gone.py:3 old", "fix": "x", "boundary": "none", "blast_radius": "x", "state": "open"},
            {"id": "D-5", "evidence": "src/a.py:99 past end", "fix": "x", "boundary": "none", "blast_radius": "x", "state": "open"},
            {"id": "D-6", "evidence": "src/a.py:8 done", "fix": "x", "boundary": "none", "blast_radius": "x", "state": "fixed"},
        ]
        rec = build_records.record_path(os.path.join(main, ".discovery"), "deferred", "W-9")
        build_records.atomic_write(rec, json.dumps({"items": recs, "sources": ["selftest"]}))
        pj = os.path.join(tmp, "pr.json")
        with open(pj, "w") as fh:
            json.dump({"state": "MERGED", "baseRefName": "stub", "headRefName": "feat/W-9", "url": "u"}, fh)

        verdicts = {i["id"]: triage_item(i, main)[0] for i in recs}
        check("triage: boundary external / effort L / gone file / past end are ask",
              [verdicts[k] for k in ("D-1", "D-2", "D-3", "D-4", "D-5")] == ["direct", "ask", "ask", "ask", "ask"], verdicts)
        check("triage: the reasons name the cause", "external" in triage_item(recs[1], main)[1]
              and "effort L" in triage_item(recs[2], main)[1] and "does not exist" in triage_item(recs[3], main)[1]
              and "past the end" in triage_item(recs[4], main)[1])
        doc = cr_document("W-9", [recs[0]])
        crs = os.path.join(tmp, "crs.md")
        with open(crs, "w") as fh:
            fh.write(doc)
        rc, chk = run([sys.executable, os.path.join(HERE, "check_crs.py"), crs])
        check("the change-request document passes check_crs.py", rc == 0 and "CR-1 ok" in chk, chk)

        ns = argparse.Namespace(root=main, dossier="W-9", items=None, direct=None, test_command="python3 -m pytest -q", hooks=None,
                                env_script=None, path_prepend=None, pr_json=pj, no_compose=False)
        import io
        import contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = prepare(ns)
        text = buf.getvalue()
        wt = worktree_path(main, "W-9")
        check("prep creates the branch and worktree, base from the merged PR",
              os.path.isdir(wt) and sh(wt, "branch", "--show-current") == "fix/W-9-deferred" and "(new)" in text and "base stub" in text, text)
        check("prep names the direct and ask items", "DIRECT: D-1" in text and "ASK: D-2 —" in text and "ASK: D-3 —" in text
              and "D-6" not in text, text)
        check("prep composed one payload", rc == 0 and "PAYLOADS:" in text and os.path.isfile(os.path.join(wt, ".agent-staging", "payloads", "fix-wave.json")), text)
        log = open(find_dossier(wt, "W-9")).read()
        check("triage and branch lines are in the worktree's dossier log",
              "DEFERRED-TRIAGE: D-1 → direct — honour 429" in log and "DEFERRED-FIX: branch fix/W-9-deferred on stub @" in log, log)
        check("the log commit leaves the tree clean", sh(wt, "status", "--porcelain", "--untracked-files=no") == "")
        check("the record has D-1 fixing", json.load(open(rec))["items"][0]["state"] == "fixing")
        head = sh(wt, "rev-parse", "HEAD")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = prepare(ns)
        check("a second run reuses the branch, adds no commit and no duplicate log line",
              "(reused)" in buf.getvalue() and sh(wt, "rev-parse", "HEAD") == head
              and open(find_dossier(wt, "W-9")).read().count("DEFERRED-TRIAGE: D-1") == 1, buf.getvalue())
        ns.items, ns.direct = "D-2", "D-2"
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            prepare(ns)
        check("--direct turns an ask item direct", "DIRECT: D-2" in buf.getvalue(), buf.getvalue())
        ns.items = "D-77"
        try:
            prepare(ns)
            check("an unknown D-id is refused", False)
        except Refused:
            check("an unknown D-id is refused", True)
    print("selftest:", "ok" if ok else "FAILED")
    return 0 if ok else 1


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--root")
    ap.add_argument("--dossier")
    ap.add_argument("--items", help="D-1,D-3: only these items are in scope")
    ap.add_argument("--direct", help="D-n[,D-m]: items the owner ruled 'fix it in this PR anyway'")
    ap.add_argument("--test-command")
    ap.add_argument("--hooks")
    ap.add_argument("--env-script")
    ap.add_argument("--path-prepend", action="append")
    ap.add_argument("--pr-json", help="a JSON file standing in for `gh pr view` (tests)")
    ap.add_argument("--no-compose", action="store_true", help="stop after the branch, triage and log")
    args = ap.parse_args(argv)
    if args.selftest:
        return selftest()
    if not args.root or not args.dossier:
        ap.error("--root and --dossier are required unless --selftest")
    try:
        return prepare(args)
    except (Refused, build_records.Refused) as err:
        print(f"fix_deferred_prep: REFUSED — {err}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
