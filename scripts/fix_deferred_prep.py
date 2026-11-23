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
   as direct, and skips the checks below. Before that, for every other item:
   - out of scope (build_records.out_of_scope: anchored only in `.discovery/` / `.agent-staging/`,
     or evidence that is only a pipeline check's output) -> `DROPPED`, state wontfix;
   - another dossier (worktree, main checkout, origin/development) cites the item ("D-5 of W-161")
     -> `COVERED`, state dossier, note "covered by W-n";
   - origin/development changed the anchored lines (or removed the file) since the stack base
     forked from it, or another dossier's `anchors:` name the same file (within 15 lines first,
     then anywhere in it) -> `CHECK`:
     the orchestrator reads that before it fixes or asks (fix-deferred.md Step 3).
5. Logs `DEFERRED-FIX: branch ...` (new branches only) and `DEFERRED-TRIAGE: D-n -> direct ...`
   through dossier_edit's log path (a line already there is not added again), sets each direct
   item `fixing` in the record, and commits that log in the worktree so the wave starts clean.
6. Writes the change-request document `<worktree>/.agent-staging/fix-deferred/crs-<ID>.md`
   (one `## CR-n` per direct item), runs check_crs.py on it and compose_payloads.py --crs.
7. Saves its facts in `R/.discovery/deferred/<ID>.fix.json` for fix_deferred_pr.py and prints:

       WORKTREE: <path>
       BRANCH: fix/W-117-deferred base <base> fork <sha> (new|reused); dossier PR #<n> <state>; stack base <b>
       DIRECT: D-1, D-2
       DROPPED: D-6 — out of scope: <why>
       COVERED: D-7 — W-196 (ready) cites W-117 D-7
       CHECK: D-8 — development changed src/a.rs:40 since the stack base (<sha subject>) — otherwise direct
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
import dossier_pr  # noqa: E402

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
    """Every `pr:` shape (URL list, single URL, bare number, number list, null), through the one parser."""
    return dossier_pr.pr_numbers(re.sub(r"^\s*-\s*", "", value or "", flags=re.M).replace("\n", ", "))


def _names_base(fm: dict[str, str]) -> bool:
    branch = fm.get("branch", "").strip().strip("`")
    return bool(pr_numbers(fm.get("pr", ""))) or bool(branch and branch.lower() not in ("null", "none", "~"))


def build_copy(root: str, dossier: str, fm: dict[str, str]) -> tuple[dict[str, str], str]:
    """The front matter to stack on, and where it came from. The main checkout's copy is often the pre-build one
    (no branch, no PR: the build wrote both on its own branch, which merges into a stub, not into the checkout's
    branch); then the newest copy on any ref that names a PR or a branch is the build's own."""
    if _names_base(fm):
        return fm, "checkout"
    rel = os.path.relpath(dossier, root)
    rc, out = git(root, "log", "--all", "--format=%H", "-n", "60", "--", rel)
    for sha in (out.split() if rc == 0 else []):
        rc2, text = git(root, "show", f"{sha}:{rel}")
        if rc2 == 0:
            cand = front(text)
            if _names_base(cand):
                return cand, f"build copy @ {sha[:9]}"
    return fm, "checkout"


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


# -- before anyone is asked: covered elsewhere? -------------------------------

MAINLINE = "origin/development"
NEAR = 15          # an anchor this many lines from the item's line names the same code
SKIP_STATUS = ("dropped", "superseded")   # a dossier with these covers nothing


def mainline_change(wt: str, item: dict, ref: str = MAINLINE) -> str:
    """'' or why the mainline may already have fixed the item: the anchored file changed near the
    anchored line (or is gone) on `ref` since the stack base forked from it."""
    loc = item_loc(item)
    if not loc or git(wt, "rev-parse", "--verify", "-q", ref + "^{commit}")[0] != 0:
        return ""
    path, line = loc
    rc, base = git(wt, "merge-base", "HEAD", ref)
    if rc != 0 or not base:
        return ""
    rc, tip = git(wt, "rev-parse", ref)
    if base == tip:
        return ""
    rc, log = git(wt, "log", "--format=%h %s", f"{base}..{ref}", "--", path)
    commits = [one_line(c, 90) for c in log.splitlines()[:3]]
    if not commits:
        return ""
    if git(wt, "cat-file", "-e", f"{ref}:{path}")[0] != 0:
        return f"{path} is gone on {ref.split('/')[-1]} ({'; '.join(commits)})"
    rc, diff = git(wt, "diff", "-U0", base, ref, "--", path)
    for m in re.finditer(r"^@@ -(\d+)(?:,(\d+))? ", diff, re.M):
        start, count = int(m.group(1)), int(m.group(2) if m.group(2) is not None else 1)
        if start - 3 <= line <= start + max(count, 1) + 3:
            return f"{ref.split('/')[-1]} changed {path}:{line} since the stack base ({'; '.join(commits)})"
    return ""


def dossier_texts(dirs: list[str], wt: str, ref: str = MAINLINE) -> dict[str, str]:
    """Every dossier by file name: the worktree's, the main checkout's, then the mainline's (first wins)."""
    out: dict[str, str] = {}
    for d in dirs:
        for f in sorted(glob.glob(os.path.join(d, ".discovery", "dossiers", "W-*.md"))):
            name = os.path.basename(f)
            if name not in out:
                with open(f, encoding="utf-8", errors="replace") as fh:
                    out[name] = fh.read()
    rc, names = git(wt, "ls-tree", "--name-only", ref, ".discovery/dossiers/")
    if rc == 0:
        for p in names.splitlines():
            name = os.path.basename(p)
            if re.match(r"W-\d+.*\.md$", name) and name not in out:
                rc2, text = git(wt, "show", f"{ref}:{p}")
                if rc2 == 0:
                    out[name] = text
    return out


def _words(text: str) -> set[str]:
    """Content words (4+ letters, a plural `s` dropped) for a rough overlap score."""
    return {w[:-1] if w.endswith("s") and len(w) > 4 else w for w in re.findall(r"[a-z]{4,}", text.lower())}


def other_dossiers(did: str, item: dict, texts: dict[str, str]) -> tuple[str, list[str]]:
    """(covered, related) for one item across the other dossiers.

    covered: a dossier that cites this very item ("D-5 of W-161", "W-161 D-5"), e.g. one /plan
    wrote from it. related: dossiers whose `anchors:` name the item's file, those within NEAR lines
    first; one that anchors the file only elsewhere still names it (another line of one file is
    often the same defect seen from another caller), ranked by the words their title shares with
    the item's title and evidence, so a file many dossiers anchor still shows the likely one."""
    iid = item["id"]
    cite = re.compile(rf"\b{re.escape(iid)}\s+(?:of|in|from)\s+{re.escape(did)}\b"
                      rf"|\b{re.escape(did)}(?:'s)?[\s:/,.-]{{1,3}}(?:deferred\s+(?:item\s+)?)?{re.escape(iid)}\b")
    loc = item_loc(item)
    mine = _words(f"{item.get('title', '')} {item.get('evidence', '')}")
    covered, related, same_file = "", [], []
    for name, text in sorted(texts.items()):
        fm = front(text)
        oid = fm.get("id") or name.split("-", 2)[0] + "-" + name.split("-", 2)[1]
        if oid == did:
            continue
        status = fm.get("status", "?").strip()
        if status in SKIP_STATUS:
            continue
        if not covered and cite.search(text):
            covered = f"{oid} ({status}) cites {did} {iid}"
            continue
        if loc:
            lines = [int(m.group(1)) for m in re.finditer(
                rf"^\s*-\s*{re.escape(loc[0])}:(\d+)\s*$", text.split("\n---", 1)[0], re.M)]
            near = [n for n in lines if abs(n - loc[1]) <= NEAR]
            if near:
                related.append(f"{oid} ({status}) anchors {loc[0]}:{near[0]}")
            elif lines:
                nearest = min(lines, key=lambda n: abs(n - loc[1]))
                score = len(mine & _words(fm.get("title", "")))
                same_file.append((-score, abs(nearest - loc[1]),
                                  f"{oid} ({status}) anchors the same file, {loc[0]}:{nearest}"))
    return covered, (related + [t for *_, t in sorted(same_file)])[:3]


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


def signing(wt: str, flag: bool) -> bool:
    """work-on states no signing rule of its own: the repository's `commit.gpgsign` decides. `-S` is passed
    explicitly when the flag asks for it or that config is true (the same commit either way)."""
    return flag or git(wt, "config", "--get", "commit.gpgsign")[1].strip().lower() == "true"


def commit_log(wt: str, did: str, what: str, sign: bool = False) -> tuple[bool, str]:
    """Commit the dossier log lines just written, through gate_commit.py (the one commit path of the flow),
    so the tree is clean when the wave starts. Hooks are never bypassed: a log-only commit touches one
    markdown file on a finished build, not a tree mid-migration (the only case Phase 2 lets a bypass for),
    and the dossier's `hooks:` policy is about implementer commits. A refusal is returned, never worked
    around: the lines stay uncommitted in the worktree and the caller reports it."""
    rc, out = git(wt, "status", "--porcelain", "--", ".discovery/dossiers")
    paths = [l.split(None, 1)[1].strip() for l in out.splitlines() if len(l.split(None, 1)) == 2]
    if not paths:
        return True, "nothing to commit"
    jira = ""
    d = find_dossier(wt, did)
    if d:
        jira = front(open(d, encoding="utf-8").read()).get("jira", "")
    prefix = f"{jira} " if jira and jira.lower() not in ("null", "none", "~") else ""
    cmd = [sys.executable, os.path.join(HERE, "gate_commit.py"), "--root", wt, "--paths", ",".join(paths),
           "-m", f"{prefix}chore({did}): {what}"]
    if signing(wt, sign):
        cmd.append("--sign")
    rc, out = run(cmd)
    return rc == 0, one_line(out, 400)


def load_report(path: str | None) -> dict[str, dict[str, str]]:
    """Per D-item fields of the `/deferred` report (`### D-n — title` blocks with `- **Field** — value`
    bullets): boundary, blast_radius, confidence, effort. {} when the file is absent or unreadable."""
    if not path or not os.path.isfile(path):
        return {}
    try:
        text = open(path, encoding="utf-8").read()
    except OSError:
        return {}
    out: dict[str, dict[str, str]] = {}
    for m in re.finditer(r"^### (D-\d+)\b.*?(?=^### |^## |\Z)", text, re.M | re.S):
        got: dict[str, str] = {}
        for f in re.finditer(r"^- \*\*([A-Za-z ]+)\*\*\s*[—-]\s*(.+)$", m.group(0), re.M):
            key = {"contract boundary": "boundary", "blast radius": "blast_radius", "confidence": "confidence",
                   "effort": "effort"}.get(f.group(1).strip().lower())
            if key:
                got[key] = f.group(2).strip()
        if "effort" in got:
            e = re.match(r"`?([SML])\b", got["effort"])
            got["effort"] = e.group(1) if e else ""
        out[m.group(1)] = got
    return out


def default_report(root: str, did: str) -> str:
    base = os.environ.get("ASD_RUNS_DIR") or os.path.expanduser("~/.agentic/runs")
    return os.path.join(base, f"{os.path.basename(os.path.realpath(root))}.{did}.deferred.md")


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
    fm, source = build_copy(root, dossier, front(open(dossier, encoding="utf-8").read()))
    if source != "checkout":
        print(f"fix_deferred_prep: {did}: the checkout's dossier is the pre-build copy; stacking from the {source}")
    base = resolve_base(fm, root, args.pr_json)
    wt, kind, fork = ensure_branch(root, did, base["base"])
    branch = f"fix/{did}-deferred"
    wt_dossier = find_dossier(wt, did)
    if not wt_dossier:
        raise Refused(f"the worktree {wt} holds no dossier {did}-*.md: the stack base {base['base']} lacks the build copy")

    # The /deferred report (read-only) is the richer source of boundary, blast radius, confidence and effort;
    # the record's own fields are the fallback for an item the report does not cover.
    report_path = args.report or default_report(root, did)
    report = load_report(report_path)
    items = [dict(i, **{k: v for k, v in report.get(i.get("id"), {}).items() if v}) for i in items]
    log_failed = ""
    forced = {x for x in (args.direct or "").split(",") if x}
    todo = [i for i in items if (not scope or i["id"] in scope) and i.get("state") not in CLOSED]
    direct: list[dict] = []
    asked: dict[str, str] = {}
    dropped: dict[str, str] = {}   # not product code: the /deferred footer's, closed wontfix
    covered: dict[str, str] = {}   # another dossier cites the item: closed `dossier`
    checks: dict[str, str] = {}    # the mainline or another dossier may already cover it: read before fixing or asking
    texts = dossier_texts([wt, root], wt) if any(i["id"] not in forced for i in todo) else {}
    for it in todo:
        verdict, why = triage_item(it, wt)
        if it["id"] in forced:
            direct.append(it)
            continue
        scope = build_records.out_of_scope(it)
        if scope:
            dropped[it["id"]] = f"out of scope: {scope}"
            continue
        cov, related = other_dossiers(did, it, texts)
        if cov:
            covered[it["id"]] = cov
            continue
        dev = mainline_change(wt, it)
        signals = ([dev] if dev else []) + related
        if signals:
            checks[it["id"]] = "; ".join(signals) + f" — otherwise {verdict}" + (f": {why}" if why else "")
        elif verdict == "direct":
            direct.append(it)
        else:
            asked[it["id"]] = why

    lines = []
    if kind == "new":
        lines.append(f"- DEFERRED-FIX: branch {branch} on {base['base']} @ {fork}")
    lines += [f"- DEFERRED-TRIAGE: {it['id']} → direct — {one_line(it.get('fix'), 140)}" for it in direct]
    lines += [f"- DEFERRED-TRIAGE: {k} → wontfix — {v}" for k, v in dropped.items()]
    lines += [f"- DEFERRED-TRIAGE: {k} → dossier — covered: {v}" for k, v in covered.items()]
    if lines:
        print(dossier_edit.log_or_say(wt_dossier, lines))
        okc, msg = commit_log(wt, did, "deferred triage log", args.sign)
        if not okc:
            log_failed = msg
    for it in direct:
        if it.get("state") != "fixing":
            build_records.deferred_set(path, it["id"], "fixing", None)
    for k, v in dropped.items():
        build_records.deferred_set(path, k, "wontfix", v)
    for k, v in covered.items():
        build_records.deferred_set(path, k, "dossier", f"covered by {v}")

    out = [f"REPORT: {report_path if report else 'none — the record fields decide'}",
           f"WORKTREE: {wt}",
           f"BRANCH: {branch} base {base['base']} fork {fork} ({kind}); dossier PR "
           f"{'#%s' % base['pr'] if base['pr'] else 'none'} {base['pr_state']}; stack base {base['stack_base']}",
           "DIRECT: " + (", ".join(i["id"] for i in direct) or "none")]
    out += [f"DROPPED: {k} — {v}" for k, v in dropped.items()]
    out += [f"COVERED: {k} — {v}" for k, v in covered.items()]
    out += [f"CHECK: {k} — {v}" for k, v in checks.items()]
    out += [f"ASK: {k} — {v}" for k, v in asked.items()]
    if not asked:
        out.append("ASK: none")
    if log_failed:
        out.append(f"LOG-COMMIT REFUSED (not bypassed; the lines stay uncommitted in the worktree): {log_failed}")
    st = {"id": did, "root": root, "worktree": wt, "branch": branch, "base": base["base"], "kind": base["kind"],
          "stack_base": base["stack_base"], "dossier_pr": base["pr"], "fork": fork, "dossier": wt_dossier,
          "direct": sorted({*(i["id"] for i in direct), *_old(state_path(root, did))}), "asked": asked,
          "checks": checks, "dropped": dropped, "covered": covered}
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
        sh(main, "config", "commit.gpgsign", "false")
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
                                env_script=None, path_prepend=None, pr_json=pj, no_compose=False,
                                report=os.path.join(tmp, "absent.deferred.md"), sign=False)
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
        pdir = os.path.join(wt, ".agent-staging", "payloads")
        ptext = "".join(open(os.path.join(pdir, f)).read() for f in os.listdir(pdir) if not f.endswith(".json"))
        check("the payload names the stacked branch, not the dossier's `branch:`",
              "BRANCH: fix/W-9-deferred" in ptext and "BRANCH: feat/W-9" not in ptext, ptext[:400])
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
        ns.items = ns.direct = None
        ns.no_compose = True
        # source 1: the record only (the report file is absent) — the checks above already ran on it
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            prepare(ns)
        check("record-only source: no report, the record's boundary and effort decide",
              "REPORT: none" in buf.getvalue() and "ASK: D-2 — external contract" in buf.getvalue()
              and "DIRECT: D-1" in buf.getvalue(), buf.getvalue())
        # source 2: the /deferred report overrides the record per item
        report = os.path.join(tmp, "W-9.deferred.md")
        with open(report, "w") as fh:
            fh.write("# Deferred issues of W-9\n\n| ID | Kind | Title | Risk | Origin | Contract | Effort |\n|---|---|---|---|---|---|---|\n"
                     "| D-1 | bug | a | low | s | external | S |\n\n"
                     "### D-1 — retry ignores 429\n- **Contract boundary** — external: the vendor SDK signature\n"
                     "- **Blast radius** — one package\n- **Confidence** — high\n- **Effort** — S\n\n"
                     "### D-2 — route changes\n- **Contract boundary** — none: internal helper\n"
                     "- **Blast radius** — callers\n- **Confidence** — medium\n- **Effort** — `M` (half a day)\n\n"
                     "### D-3 — big\n- **Contract boundary** — none\n- **Blast radius** — x\n- **Confidence** — low\n- **Effort** — L\n")
        got = load_report(report)
        check("load_report parses boundary, blast radius, confidence and effort per D-item",
              got["D-1"]["boundary"].startswith("external") and got["D-2"]["effort"] == "M" and got["D-3"]["effort"] == "L"
              and got["D-2"]["confidence"] == "medium" and got["D-1"]["blast_radius"] == "one package", got)
        check("load_report is {} for an absent file", load_report(os.path.join(tmp, "nope.md")) == {} and load_report(None) == {})
        ns.report = report
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            prepare(ns)
        out2 = buf.getvalue()
        check("report source: its boundary and effort win over the record (D-1 external, D-2 internal, D-3 still L)",
              f"REPORT: {report}" in out2 and "ASK: D-1 — external contract" in out2 and "DIRECT: D-2" in out2
              and "ASK: D-3 — effort L" in out2, out2)
        before = open(report).read()
        check("the report is read-only", before == open(report).read() and os.path.getmtime(report) <= os.path.getmtime(rec) + 5)
        # a hook that refuses is never bypassed: the log commit reports it and leaves the lines uncommitted
        hook = os.path.join(main, ".git", "hooks", "pre-commit")
        with open(hook, "w") as fh:
            fh.write("#!/bin/sh\necho hook says no >&2\nexit 1\n")
        os.chmod(hook, 0o755)
        head2 = sh(wt, "rev-parse", "HEAD")
        ns.direct = "D-3"
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            prepare(ns)
        out3 = buf.getvalue()
        check("a refusing hook is not bypassed: LOG-COMMIT REFUSED, no new commit, lines left in the worktree",
              "LOG-COMMIT REFUSED" in out3 and sh(wt, "rev-parse", "HEAD") == head2
              and "DEFERRED-TRIAGE: D-3" in open(find_dossier(wt, "W-9")).read()
              and sh(wt, "status", "--porcelain", "--untracked-files=no") != "", out3)
        os.remove(hook)
        sh(wt, "checkout", "--", ".discovery")
        ns.direct = None
        ns.no_compose = False
        ns.items, ns.direct = "D-2", "D-2"
        ns.report = os.path.join(tmp, "absent.deferred.md")
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
        # a kanban dossier merged into development: the fix forks from and targets development, not a stub
        sh(main, "checkout", "-q", "-b", "development")
        with open(os.path.join(main, ".discovery", "dossiers", "W-10-small-fix.md"), "w") as fh:
            fh.write("---\nid: W-10\nstatus: done\ndelivery: kanban\nbranch: fix/W-10\npr: [https://github.com/o/r/pull/8]\n---\n\n## Build log\n\n- start\n")
        sh(main, "add", "-A"); sh(main, "commit", "-q", "-m", "kanban dossier"); sh(main, "push", "-q", "origin", "development")
        sh(main, "checkout", "-q", "stub")
        with open(os.path.join(main, ".discovery", "dossiers", "W-10-small-fix.md"), "w") as fh:   # the owner's checkout holds the dossier file
            fh.write(sh(main, "show", "development:.discovery/dossiers/W-10-small-fix.md") + "\n")
        build_records.atomic_write(build_records.record_path(os.path.join(main, ".discovery"), "deferred", "W-10"),
                                   json.dumps({"items": [recs[0]], "sources": ["selftest"]}))
        pj2 = os.path.join(tmp, "pr2.json")
        with open(pj2, "w") as fh:
            json.dump({"state": "MERGED", "baseRefName": "development", "headRefName": "fix/W-10", "url": "u"}, fh)
        ns2 = argparse.Namespace(**{**vars(ns), "dossier": "W-10", "items": None, "direct": None, "pr_json": pj2, "no_compose": True})
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = prepare(ns2)
        wt2 = worktree_path(main, "W-10")
        check("kanban dossier merged into development: fix branch forks from and targets development",
              rc == 0 and "base development" in buf.getvalue() and "stack base development" in buf.getvalue() and sh(wt2, "branch", "--show-current") == "fix/W-10-deferred"
              and "delivery: kanban" in open(find_dossier(wt2, "W-10")).read(), buf.getvalue())
        check("pr: every shape gives its numbers (bare, list, URL, block list, null)",
              pr_numbers("621") == [621] and pr_numbers("[79, 80]") == [79, 80] and pr_numbers("[https://github.com/o/r/pull/7]") == [7]
              and pr_numbers("  - https://github.com/o/r/pull/5\n  - https://github.com/o/r/pull/6\n") == [5, 6] and pr_numbers("null") == [])
        # the checkout holds the pre-build copy (no branch, no PR); the build branch's copy names its PR as a bare number
        sh(main, "checkout", "-q", "stub")
        pre = "---\nid: W-11\nstatus: ready\nbranch: null\npr: null\n---\n\n## Build log\n\n- planned\n"
        with open(os.path.join(main, ".discovery", "dossiers", "W-11-pre.md"), "w") as fh:
            fh.write(pre)
        sh(main, "add", "-A"); sh(main, "commit", "-q", "-m", "plan W-11"); sh(main, "push", "-q", "origin", "stub")
        sh(main, "checkout", "-q", "-b", "feat/W-11")
        with open(os.path.join(main, ".discovery", "dossiers", "W-11-pre.md"), "w") as fh:
            fh.write(pre.replace("status: ready", "status: done").replace("branch: null", "branch: feat/W-11").replace("pr: null", "pr: 11"))
        sh(main, "add", "-A"); sh(main, "commit", "-q", "-m", "build W-11"); sh(main, "push", "-q", "origin", "feat/W-11")
        sh(main, "checkout", "-q", "stub")
        build_records.atomic_write(build_records.record_path(os.path.join(main, ".discovery"), "deferred", "W-11"),
                                   json.dumps({"items": [recs[0]], "sources": ["selftest"]}))
        pj3 = os.path.join(tmp, "pr3.json")
        with open(pj3, "w") as fh:
            json.dump({"state": "OPEN", "baseRefName": "stub", "headRefName": "feat/W-11", "url": "u"}, fh)
        ns3 = argparse.Namespace(**{**vars(ns), "dossier": "W-11", "items": None, "direct": None, "pr_json": pj3, "no_compose": True})
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = prepare(ns3)
        wt3 = worktree_path(main, "W-11")
        check("pre-build copy in the checkout: the build branch's copy (bare PR number) gives the stack base",
              rc == 0 and "pre-build copy" in buf.getvalue() and sh(wt3, "branch", "--show-current") == "fix/W-11-deferred"
              and "pr: 11" in open(find_dossier(wt3, "W-11")).read(), buf.getvalue())
    with tempfile.TemporaryDirectory() as tmp:  # covered elsewhere: out of scope, another dossier, the mainline
        import io
        import contextlib
        origin, main = os.path.join(tmp, "origin.git"), os.path.join(tmp, "repo")
        subprocess.run(["git", "init", "-q", "--bare", "-b", "development", origin], check=True)
        subprocess.run(["git", "clone", "-q", origin, main], check=True, capture_output=True)
        for k, v in (("user.email", "t@example.invalid"), ("user.name", "t"), ("commit.gpgsign", "false")):
            sh(main, "config", k, v)

        def put(rel: str, text: str) -> None:
            os.makedirs(os.path.dirname(os.path.join(main, rel)), exist_ok=True)
            with open(os.path.join(main, rel), "w") as fh:
                fh.write(text)
        put("src/a.py", "".join(f"line {i}\n" for i in range(1, 46)))
        put("src/b.py", "".join(f"line {i}\n" for i in range(1, 10)))
        put("src/c.py", "x\n")
        put(".discovery/dossiers/W-9-fix-things.md",
            "---\nid: W-9\nstatus: done\nbranch: feat/W-9\npr: [https://github.com/o/r/pull/7]\nhooks: none\n---\n\n"
            "## Contract\n\n### src/a.py\n\n```python\ndef f() -> int: ...\n```\n\n## Work packages\n\n"
            "| Package | Owned paths | Depends on |\n|---|---|---|\n| P1 core | src/ | — |\n\n## Build log\n\n- start\n")
        put(".discovery/dossiers/W-20-sibling.md", "---\nid: W-20\nstatus: ready\n---\n\n## Problem\n\nSource: deferred item D-3 of W-9.\n")
        put(".discovery/dossiers/W-21-near.md", "---\nid: W-21\nstatus: ready\nanchors:\n  - src/a.py:40\n---\n\n## Problem\n\nx\n")
        put(".discovery/dossiers/W-22-gone.md", "---\nid: W-22\nstatus: dropped\n---\n\nW-9 D-6 was here.\n")
        sh(main, "add", "-A")
        sh(main, "commit", "-q", "-m", "init")
        sh(main, "push", "-q", "origin", "development")
        sh(main, "push", "-q", "origin", "development:stub")
        put("src/a.py", "".join(("fixed 5\n" if i == 5 else f"line {i}\n") for i in range(1, 46)))
        os.remove(os.path.join(main, "src", "c.py"))
        sh(main, "commit", "-q", "-am", "Merge pull request #577 from o/fix-5")
        sh(main, "push", "-q", "origin", "development")
        sh(main, "fetch", "-q", "origin")
        sh(main, "checkout", "-q", "-b", "stub", "origin/stub")
        recs = [
            {"id": "D-1", "evidence": ".discovery/dossiers/W-9-fix-things.md:3 duplicate id", "fix": "renumber",
             "boundary": "none", "blast_radius": "x", "state": "open"},
            {"id": "D-2", "evidence": "src/b.py:3 slow", "fix": "cache", "boundary": "none", "blast_radius": "x", "state": "open"},
            {"id": "D-3", "evidence": "src/a.py:12 race", "fix": "lock", "boundary": "none", "blast_radius": "x", "state": "open"},
            {"id": "D-4", "evidence": "src/a.py:5 off by one", "fix": "+1", "boundary": "none", "blast_radius": "x", "state": "open"},
            {"id": "D-5", "evidence": "src/a.py:28 leak", "fix": "close", "boundary": "none", "blast_radius": "x", "state": "open"},
            {"id": "D-6", "evidence": "src/c.py:1 dead", "fix": "x", "boundary": "none", "blast_radius": "x", "state": "open"},
        ]
        texts = dossier_texts([main], main)
        check("other dossiers: a dossier citing 'D-3 of W-9' covers it; a dropped one covers nothing",
              other_dossiers("W-9", recs[2], texts)[0].startswith("W-20 (ready)")
              and other_dossiers("W-9", recs[5], texts)[0] == "", other_dossiers("W-9", recs[2], texts))
        check("other dossiers: an anchor on the same file within 15 lines is related, not covered",
              other_dossiers("W-9", recs[4], texts) == ("", ["W-21 (ready) anchors src/a.py:40"]), other_dossiers("W-9", recs[4], texts))
        check("other dossiers: an anchor on the same file but far from the line still names that dossier",
              other_dossiers("W-9", recs[3], texts) == ("", ["W-21 (ready) anchors the same file, src/a.py:40"])
              and other_dossiers("W-9", recs[1], texts) == ("", []), other_dossiers("W-9", recs[3], texts))
        far = {"W-30-far.md": "---\nid: W-30\nstatus: ready\nanchors:\n  - src/a.py:200\n  - src/a.py:90\n---\n",
               "W-31-near.md": "---\nid: W-31\nstatus: done\nanchors:\n  - src/a.py:30\n---\n"}
        check("other dossiers: a near anchor comes before a same-file one, which names its nearest line",
              other_dossiers("W-9", recs[4], far)[1] == ["W-31 (done) anchors src/a.py:30",
                                                         "W-30 (ready) anchors the same file, src/a.py:90"],
              other_dossiers("W-9", recs[4], far))
        ranked = {"W-40-a.md": "---\nid: W-40\nstatus: done\ntitle: Unrelated cache work\nanchors:\n  - src/a.py:27\n---\n",
                  "W-41-b.md": "---\nid: W-41\nstatus: ready\ntitle: Close the file handle leak\nanchors:\n  - src/a.py:2\n---\n"}
        check("other dossiers: same-file matches rank by title words shared with the item, then distance",
              other_dossiers("W-9", dict(recs[4], evidence="src/a.py:60 handle leak"), ranked)[1]
              == ["W-41 (ready) anchors the same file, src/a.py:2", "W-40 (done) anchors the same file, src/a.py:27"],
              other_dossiers("W-9", dict(recs[4], evidence="src/a.py:60 handle leak"), ranked))
        check("mainline: development changed the anchored line since the fork; elsewhere in the file is no signal",
              "changed src/a.py:5" in mainline_change(main, recs[3]) and "#577" in mainline_change(main, recs[3])
              and mainline_change(main, recs[1]) == "", mainline_change(main, recs[3]))
        check("mainline: a file removed on development is named", "src/c.py is gone on development" in mainline_change(main, recs[5]))
        rec = build_records.record_path(os.path.join(main, ".discovery"), "deferred", "W-9")
        build_records.atomic_write(rec, json.dumps({"items": recs, "sources": ["selftest"]}))
        pj = os.path.join(tmp, "pr.json")
        with open(pj, "w") as fh:
            json.dump({"state": "MERGED", "baseRefName": "stub", "headRefName": "feat/W-9", "url": "u"}, fh)
        sh(main, "push", "-q", "origin", "stub")
        ns = argparse.Namespace(root=main, dossier="W-9", items=None, direct=None, test_command="true", hooks=None,
                                env_script=None, path_prepend=None, pr_json=pj, no_compose=True,
                                report=os.path.join(tmp, "absent.deferred.md"), sign=False)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            prepare(ns)
        text = buf.getvalue()
        state = {i["id"]: (i["state"], i.get("note", "")) for i in json.load(open(rec))["items"]}
        check("prep: out of scope is DROPPED (wontfix), a cited item COVERED (dossier), never asked",
              "DROPPED: D-1 — out of scope" in text and "COVERED: D-3 — W-20" in text
              and state["D-1"][0] == "wontfix" and state["D-3"] == ("dossier", "covered by W-20 (ready) cites W-9 D-3")
              and "ASK: D-1" not in text and "ASK: D-3" not in text, text)
        check("prep: development's change and a related dossier are CHECK lines with the fallback verdict; the rest is direct",
              "CHECK: D-4 — development changed src/a.py:5" in text and "otherwise direct" in text
              and "CHECK: D-5 — W-21" in text and "CHECK: D-6 — src/c.py is gone" in text and "CHECK: D-2" not in text
              and "DIRECT: D-2" in text and state["D-4"][0] == "open", text)
        ns.items, ns.direct = "D-4", "D-4"
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            prepare(ns)
        check("prep: --direct skips the checks (the owner ruled fix it anyway)", "DIRECT: D-4" in buf.getvalue()
              and "CHECK: D-4" not in buf.getvalue(), buf.getvalue())

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
    ap.add_argument("--report", help="the /deferred report (default ~/.agentic/runs/<repo>.<ID>.deferred.md), read-only")
    ap.add_argument("--sign", action="store_true", help="sign the log commit (also on when commit.gpgsign is true)")
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
