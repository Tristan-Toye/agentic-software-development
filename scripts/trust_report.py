#!/usr/bin/env python3
"""How far can a dossier PR built by the agents be trusted once it has merged?

For every dossier PR merged into a weekly stub (a PR whose base is `stub/*`), the
report counts what happened to it afterwards:

  (a) fixes attributed to it. /stub audits every dossier on the stub and attributes a
      failure to one dossier (primary-agents/stub.md, Step 8); each fix round ships as a
      PR `fix/<ID>-stub<date>-<round>` into the stub, which is the structured signal and
      the count. Where a stub's record (`.discovery/analysis/stub-<date>.md`, the run log
      `~/.agentic/runs/<repo>.stub-<date>.jsonl`, files under `~/.agentic/post-stub/`)
      attributes a failure to a dossier that no fix PR names, that counts as one fix.
  (b) reverts. Two signals, kept apart:
      - explicit: a commit on origin/development or origin/stub/* that reverts one of the
        PR's commits ("This reverts commit <sha>", or `Revert "<subject>"`);
      - same-lines fix-up (a HEURISTIC, window REVERT_DAYS = 14): a later commit whose
        subject says fix/revert/regress, made within 14 days after the merge, that removes
        at least 3 non-trivial lines the PR added in the same file. It is approximate on
        purpose; commits of other dossier PRs and of the dossier's own fix PRs are skipped.
  (c) post-merge deferred bugs. D-items in `.discovery/deferred/<ID>.yaml` (JSON) and in
      `~/.agentic/runs/<repo>.<ID>.deferred.md` that are bug kinds (bug, latent-bug,
      security, regression, defect) and say they were found after the merge ("after the
      merge", "post-merge", "regression"). Only an explicit marker counts; the open
      bug-kind D-items of a dossier are shown beside it, unscored.

Trust score = 100 - 15 per fix - 40 per revert - 20 per post-merge bug - 10 per same-lines
fix-up, floored at 0. FLAG: a revert, a post-merge bug, or a score under 70. watch: under 100.

# Usage

    trust_report.py --root R [--root R2 ...] [--since DATE] [--out FILE_OR_DIR] [--json]
    trust_report.py record --root R      # same run, prints one line; always writes the record
    trust_report.py --selftest

Every run (except --selftest) writes `<agentic>/trust-report/<repo>.json`
{"last_run","stubs","prs","flagged"}; the dashboard reads it for its "Trust report: last
run N days ago" alarm. `<agentic>` is `$ASD_AGENTIC_DIR` or `~/.agentic`; the runs
directory is `$ASD_RUNS_DIR` or `<agentic>/runs`. `ASD_GH_BIN` replaces `gh` (tests).
The checkout is only read: `git log`, `git diff` and `git show` on origin refs, and
`gh pr list`; nothing is checked out or switched. Run it after each stub lands.

Exit 0 on success, 2 on bad usage or an unreadable repository.
"""
from __future__ import annotations

import argparse
import datetime as dt
import glob
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
from collections import defaultdict

REVERT_DAYS = 14
FIXUP_MIN_LINES = 3
BUG_KINDS = {"bug", "latent-bug", "security", "regression", "defect"}
POST_MERGE = re.compile(r"after the merge|after merge|post-merge|after it merged|after merging|regression", re.I)
FIXLIKE = re.compile(r"\b(fix|fixes|fixed|revert|regress\w*|hotfix|repair)", re.I)
ID_RE = re.compile(r"\b([A-Z]{1,6}-\d+)\b")
HEAD_ID_RE = re.compile(r"(?<![A-Za-z0-9])([A-Za-z]{1,6}-\d+)(?![0-9])")
STUB_FIX_RE = re.compile(r"^fix/([A-Za-z]{1,6}-\d+)-stub\d{4}-\d{2}-\d{2}(?:-\d+)*$")
SKIP_HEAD = re.compile(r"^(chore|docs|sync)/")
STUB_FIX_ANY_RE = re.compile(r"^fix/.+-stub\d{4}-\d{2}-\d{2}(?:-\d+)*$")


# --- environment -----------------------------------------------------------------------

def agentic_dir() -> str:
    return os.environ.get("ASD_AGENTIC_DIR") or os.path.expanduser("~/.agentic")


def runs_dir() -> str:
    return os.environ.get("ASD_RUNS_DIR") or os.path.join(agentic_dir(), "runs")


def run(cmd: list[str], cwd: str) -> str:
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd[:4])} failed: {r.stderr.strip()[:300]}")
    return r.stdout


def git(root: str, *args: str) -> str:
    return run(["git", "-C", root, *args], root)


def gh(root: str, *args: str) -> str:
    return run([*shlex.split(os.environ.get("ASD_GH_BIN") or "gh"), *args], root)


def repo_name(root: str) -> str:
    return os.path.basename(os.path.realpath(root))


def parse_ts(s: str) -> float:
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


# --- the PRs ---------------------------------------------------------------------------

def list_prs(root: str, since: float | None) -> list[dict]:
    out = gh(root, "pr", "list", "--state", "merged", "--limit", "1000", "--json",
             "number,title,headRefName,baseRefName,mergedAt,mergeCommit,url")
    prs = []
    for p in json.loads(out or "[]"):
        if not str(p.get("baseRefName", "")).startswith("stub/") or not p.get("mergedAt"):
            continue
        p["ts"] = parse_ts(p["mergedAt"])
        if since and p["ts"] < since:
            continue
        p["sha"] = (p.get("mergeCommit") or {}).get("oid") or ""
        head = p.get("headRefName", "")
        m = STUB_FIX_RE.match(head)
        if m:
            p["kind"], p["dossier"] = "fix", m.group(1).upper()
        elif SKIP_HEAD.match(head) or STUB_FIX_ANY_RE.match(head):
            continue  # a sync, a map refresh, or a stub fix naming no dossier
        else:
            m = HEAD_ID_RE.search(head)
            p["kind"], p["dossier"] = "dossier", (m.group(1).upper() if m else head)
        prs.append(p)
    return sorted(prs, key=lambda p: p["ts"])


def pr_commits(root: str, sha: str) -> list[str]:
    if not sha:
        return []
    try:
        parents = git(root, "rev-list", "--parents", "-n", "1", sha).split()[1:]
        if len(parents) >= 2:
            return git(root, "rev-list", f"{parents[0]}..{parents[1]}").split() + [sha]
    except RuntimeError:
        return []
    return [sha]


def pr_added_lines(root: str, sha: str) -> dict[str, set[str]]:
    """File -> the non-trivial lines the PR added (diff against the first parent)."""
    try:
        base = git(root, "rev-list", "--parents", "-n", "1", sha).split()[1]
        text = git(root, "diff", "-U0", "--no-color", base, sha)
    except (RuntimeError, IndexError):
        return {}
    out: dict[str, set[str]] = defaultdict(set)
    cur = ""
    for ln in text.splitlines():
        if ln.startswith("+++ "):
            cur = ln[6:] if ln.startswith("+++ b/") else ""
        elif ln.startswith("+") and cur and len(ln[1:].strip()) >= 12:
            out[cur].add(ln[1:].strip())
    return out


# --- (b) reverts -----------------------------------------------------------------------

def history_refs(root: str) -> list[str]:
    refs = git(root, "for-each-ref", "--format=%(refname)", "refs/remotes/origin/development", "refs/remotes/origin/stub")
    return [r for r in refs.split() if r]


def scan_history(root: str, since_ts: float) -> list[dict]:
    """Every non-merge commit on development/stubs since the date, with its removed lines."""
    refs = history_refs(root)
    if not refs:
        return []
    text = git(root, "log", "-p", "-U0", "--no-color", "--no-merges", f"--since=@{int(since_ts) - 86400}",
               "--format=@@C%x1f%H%x1f%ct%x1f%s%x1f%b%x1e", *refs)
    commits: list[dict] = []
    cur: dict | None = None
    for chunk in text.split("@@C\x1f")[1:]:
        head, _, diff = chunk.partition("\x1e")
        parts = head.split("\x1f", 3)
        if len(parts) < 4:
            continue
        cur = {"sha": parts[0], "ts": int(parts[1]), "subject": parts[2], "body": parts[3], "removed": defaultdict(set)}
        f = ""
        for ln in diff.splitlines():
            if ln.startswith("--- "):
                f = ln[6:] if ln.startswith("--- a/") else ""
            elif ln.startswith("-") and f and len(ln[1:].strip()) >= 12:
                cur["removed"][f].add(ln[1:].strip())
        commits.append(cur)
    return commits


def revert_targets(commits: list[dict]) -> list[tuple[dict, list[str], str]]:
    out = []
    for c in commits:
        shas = re.findall(r"This reverts commit ([0-9a-f]{7,40})", c["body"])
        m = re.match(r'Revert "(.+)"', c["subject"])
        if shas or m:
            out.append((c, shas, m.group(1) if m else ""))
    return out


# --- (a) attributions in the stub's records --------------------------------------------

def attribution_blocks(text: str) -> list[set[str]]:
    """Each 'attribut...' line plus the bullet lines right under it is one block; the IDs in it."""
    blocks: list[set[str]] = []
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        if "ttribut" in lines[i]:
            ids = set(ID_RE.findall(lines[i]))
            j = i + 1
            while j < len(lines) and lines[j].lstrip().startswith(("-", "*", "|")) and j - i < 12:
                ids |= set(ID_RE.findall(lines[j]))
                j += 1
            if ids:
                blocks.append(ids)
            i = j
        else:
            i += 1
    return blocks


def record_texts(root: str, repo: str, stub: str) -> list[tuple[str, str]]:
    sd = stub[len("stub/"):]
    out: list[tuple[str, str]] = []

    def add(path: str, jsonl: bool = False) -> None:
        try:
            if os.path.getsize(path) > 4_000_000:
                return
            raw = open(path, encoding="utf-8", errors="replace").read()
        except OSError:
            return
        if jsonl:
            parts = []
            for ln in raw.splitlines():
                try:
                    parts.append(str(json.loads(ln).get("text", "")))
                except (ValueError, AttributeError):
                    continue
            raw = "\n\n".join(parts)
        out.append((path, raw))

    add(os.path.join(root, ".discovery", "analysis", f"stub-{sd}.md"))
    add(os.path.join(runs_dir(), f"{repo}.stub-{sd}.jsonl"), jsonl=True)
    for p in glob.glob(os.path.join(agentic_dir(), "post-stub", "**", "*"), recursive=True):
        if os.path.isfile(p) and sd in p and p.endswith((".md", ".txt", ".json", ".jsonl")) and repo in p:
            add(p)
    return out


# --- (c) deferred bugs -----------------------------------------------------------------

def deferred_bugs(root: str, repo: str) -> tuple[dict[str, set[str]], dict[str, int]]:
    """Dossier -> post-merge bug D-ids; dossier -> open bug-kind D-items (informational)."""
    post: dict[str, set[str]] = defaultdict(set)
    open_bugs: dict[str, int] = defaultdict(int)
    for f in glob.glob(os.path.join(root, ".discovery", "deferred", "*.yaml")):
        did = os.path.basename(f)[:-5]
        try:
            items = json.load(open(f, encoding="utf-8")).get("items", [])
        except (OSError, ValueError, AttributeError):
            continue
        for it in items:
            if not isinstance(it, dict) or str(it.get("kind", "")).lower() not in BUG_KINDS:
                continue
            if it.get("state") == "open":
                open_bugs[did] += 1
            if it.get("state") != "wontfix" and POST_MERGE.search(json.dumps(it)):
                post[did].add(str(it.get("id")))
    for f in glob.glob(os.path.join(runs_dir(), f"{repo}.*.deferred.md")):
        did = os.path.basename(f)[len(repo) + 1:-len(".deferred.md")]
        try:
            for ln in open(f, encoding="utf-8", errors="replace"):
                m = re.search(r"\bD-\d+\b", ln)
                if m and POST_MERGE.search(ln) and re.search(r"bug|defect|regress", ln, re.I):
                    post[did].add(m.group(0))
        except OSError:
            continue
    return post, open_bugs


# --- the analysis ----------------------------------------------------------------------

def score_of(fixes: int, reverts: int, post: int, fixups: int) -> int:
    return max(0, 100 - 15 * fixes - 40 * reverts - 20 * post - 10 * fixups)


def flag_of(score: int, reverts: int, post: int) -> str:
    if reverts or post or score < 70:
        return "FLAG"
    return "watch" if score < 100 else "ok"


def analyse(root: str, since: float | None = None) -> dict:
    repo = repo_name(root)
    prs = list_prs(root, since)
    dossier_prs = [p for p in prs if p["kind"] == "dossier"]
    fix_prs = [p for p in prs if p["kind"] == "fix"]
    commit_sets = {p["number"]: set(pr_commits(root, p["sha"])) for p in prs}
    owner: dict[str, int] = {}
    for p in dossier_prs:
        for c in commit_sets[p["number"]]:
            owner.setdefault(c, p["number"])
    fix_commits = {p["number"]: commit_sets[p["number"]] for p in fix_prs}

    history: list[dict] = []
    if dossier_prs:
        try:
            history = scan_history(root, min(p["ts"] for p in dossier_prs))
        except RuntimeError:
            history = []
    explicit: dict[int, set[str]] = defaultdict(set)
    subj_owner: dict[str, int] = {}
    stdin = "".join(c + "\n" for c in owner)
    if stdin:
        r = subprocess.run(["git", "-C", root, "log", "--no-walk=unsorted", "--stdin", "--format=%H%x1f%s"], input=stdin, capture_output=True, text=True)
        for ln in r.stdout.splitlines():
            h, _, subj = ln.partition("\x1f")
            subj_owner.setdefault(subj, owner[h])
    for c, shas, subj in revert_targets(history):
        hit = None
        for s in shas:
            for full, num in owner.items():
                if full.startswith(s):
                    hit = num
        if hit is None and subj:
            hit = subj_owner.get(subj)
            m = re.search(r"#(\d+)", subj)
            if hit is None and m and any(p["number"] == int(m.group(1)) for p in dossier_prs):
                hit = int(m.group(1))
        if hit is not None and c["sha"] not in commit_sets[hit]:
            explicit[hit].add(c["sha"])

    added = {p["number"]: pr_added_lines(root, p["sha"]) for p in dossier_prs if history}
    index: dict[tuple[str, str], list[int]] = defaultdict(list)
    for num, files in added.items():
        for f, lines in files.items():
            for ln in lines:
                index[(f, ln)].append(num)
    pr_by_num = {p["number"]: p for p in dossier_prs}
    fix_dossier = {p["number"]: p["dossier"] for p in fix_prs}
    all_fix_commits = {c: num for num, cs in fix_commits.items() for c in cs}
    fixups: dict[int, set[str]] = defaultdict(set)
    for c in history:
        if c["sha"] in owner or not FIXLIKE.search(c["subject"]) or c["sha"] in explicit_all(explicit):
            continue
        hits: dict[int, int] = defaultdict(int)
        for f, lines in c["removed"].items():
            for ln in lines:
                for num in index.get((f, ln), ()):
                    hits[num] += 1
        for num, n in hits.items():
            p = pr_by_num[num]
            if n < FIXUP_MIN_LINES or not (p["ts"] < c["ts"] <= p["ts"] + REVERT_DAYS * 86400):
                continue
            fnum = all_fix_commits.get(c["sha"])
            if fnum is not None and fix_dossier.get(fnum) == p["dossier"]:
                continue
            fixups[num].add(c["sha"])

    post, open_bugs = deferred_bugs(root, repo)

    stubs: dict[str, dict] = {}
    for p in dossier_prs:
        s = stubs.setdefault(p["baseRefName"], {"stub": p["baseRefName"], "rows": {}})
        r = s["rows"].setdefault(p["dossier"], {"dossier": p["dossier"], "prs": [], "titles": [], "fix_prs": [], "record_attributions": 0,
                                                "reverts": set(), "fixups": set(), "post_merge_bugs": [], "open_bugs": 0, "first_ts": p["ts"]})
        r["prs"].append(p["number"])
        r["titles"].append(p["title"])
        r["reverts"] |= explicit.get(p["number"], set())
        r["fixups"] |= fixups.get(p["number"], set())
    seen_post: set[str] = set()
    for p in fix_prs:
        s = stubs.get(p["baseRefName"])
        if s and p["dossier"] in s["rows"]:
            s["rows"][p["dossier"]]["fix_prs"].append(p["number"])
    for name, s in stubs.items():
        for path, text in record_texts(root, repo, name):
            for ids in attribution_blocks(text):
                for i in ids:
                    if i in s["rows"]:
                        s["rows"][i]["record_attributions"] += 1
    for name in sorted(stubs):
        for d, r in sorted(stubs[name]["rows"].items(), key=lambda kv: kv[1]["first_ts"]):
            if d not in seen_post:
                seen_post.add(d)
                r["post_merge_bugs"] = sorted(post.get(d, ()))
                r["open_bugs"] = open_bugs.get(d, 0)

    out_stubs = []
    for name in sorted(stubs):
        rows = []
        for r in stubs[name]["rows"].values():
            fixes = len(r["fix_prs"]) or (1 if r["record_attributions"] else 0)
            sc = score_of(fixes, len(r["reverts"]), len(r["post_merge_bugs"]), len(r["fixups"]))
            rows.append({"dossier": r["dossier"], "prs": sorted(r["prs"]), "title": r["titles"][0], "fixes": fixes, "fix_prs": sorted(r["fix_prs"]),
                         "record_attributions": r["record_attributions"], "reverts": len(r["reverts"]), "fixups": len(r["fixups"]),
                         "post_merge_bugs": r["post_merge_bugs"], "open_bugs": r["open_bugs"], "score": sc,
                         "flag": flag_of(sc, len(r["reverts"]), len(r["post_merge_bugs"]))})
        rows.sort(key=lambda x: (x["score"], x["dossier"]))
        n = len(rows)
        out_stubs.append({"stub": name, "rows": rows, "dossiers": n, "fixes": sum(x["fixes"] for x in rows), "reverts": sum(x["reverts"] for x in rows),
                          "fixups": sum(x["fixups"] for x in rows), "post_merge_bugs": sum(len(x["post_merge_bugs"]) for x in rows),
                          "flagged": sum(1 for x in rows if x["flag"] == "FLAG"),
                          "avg_score": round(sum(x["score"] for x in rows) / n, 1) if n else None})
    all_rows = [(s["stub"], r) for s in out_stubs for r in s["rows"]]
    flagged = [{"stub": s, "dossier": r["dossier"], "prs": r["prs"], "score": r["score"]} for s, r in all_rows if r["flag"] == "FLAG"]
    return {"repo": repo, "root": os.path.realpath(root), "since": since, "generated": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "revert_days": REVERT_DAYS, "stubs": out_stubs, "pr_count": len(dossier_prs), "fix_pr_count": len(fix_prs), "flagged": flagged,
            "totals": {"dossiers": len(all_rows), "fixes": sum(r["fixes"] for _, r in all_rows), "reverts": sum(r["reverts"] for _, r in all_rows),
                       "fixups": sum(r["fixups"] for _, r in all_rows), "post_merge_bugs": sum(len(r["post_merge_bugs"]) for _, r in all_rows),
                       "flagged": len(flagged),
                       "avg_score": round(sum(r["score"] for _, r in all_rows) / len(all_rows), 1) if all_rows else None}}


def explicit_all(explicit: dict[int, set[str]]) -> set[str]:
    return set().union(*explicit.values()) if explicit else set()


# --- output ----------------------------------------------------------------------------

def trend(stubs: list[dict]) -> list[str]:
    lines = []
    prev = None
    for s in stubs:
        if s["avg_score"] is None:
            continue
        arrow = "" if prev is None else (" (up)" if s["avg_score"] > prev else " (down)" if s["avg_score"] < prev else " (flat)")
        lines.append(f"- {s['stub']}: average score {s['avg_score']}{arrow}, {s['flagged']} of {s['dossiers']} flagged, {s['fixes']} fixes, {s['reverts']} reverts")
        prev = s["avg_score"]
    return lines


def markdown(res: dict) -> str:
    t = res["totals"]
    o = [f"# Trust report: {res['repo']}", "",
         f"Generated {res['generated']}. {res['pr_count']} dossier PRs merged into stubs, {res['fix_pr_count']} stub fix PRs. "
         f"Score = 100 - 15 per fix - 40 per revert - 20 per post-merge bug - 10 per same-lines fix-up (heuristic, {res['revert_days']} days, "
         f"at least {FIXUP_MIN_LINES} lines). FLAG = a revert, a post-merge bug or a score under 70.", ""]
    for s in res["stubs"]:
        o += [f"## {s['stub']}", "", "| PR | Dossier | Fixes attributed | Reverts | Same-lines fix-ups | Post-merge bugs | Score | Flag |", "|---|---|---|---|---|---|---|---|"]
        for r in s["rows"]:
            fx = str(r["fixes"]) + (f" ({', '.join('#' + str(n) for n in r['fix_prs'])})" if r["fix_prs"] else (" (record)" if r["fixes"] else ""))
            pm = ", ".join(r["post_merge_bugs"]) or "0"
            o.append(f"| {', '.join('#' + str(n) for n in r['prs'])} | {r['dossier']} | {fx} | {r['reverts']} | {r['fixups']} | {pm} | {r['score']} | {r['flag']} |")
        o += ["", f"Stub total: {s['dossiers']} dossiers, {s['fixes']} fixes, {s['reverts']} reverts, {s['fixups']} fix-ups, {s['post_merge_bugs']} post-merge bugs, {s['flagged']} flagged.", ""]
    o += ["## Totals", "", f"- dossiers {t['dossiers']}, fixes {t['fixes']}, reverts {t['reverts']}, same-lines fix-ups {t['fixups']}, post-merge bugs {t['post_merge_bugs']}",
          f"- flagged {t['flagged']}, average score {t['avg_score']}", "", "## Trend", ""]
    o += trend(res["stubs"]) or ["- no stubs"]
    if res["flagged"]:
        o += ["", "## Most flagged", ""]
        for f in sorted(res["flagged"], key=lambda x: x["score"])[:15]:
            o.append(f"- {f['dossier']} ({f['stub']}, {', '.join('#' + str(n) for n in f['prs'])}): score {f['score']}")
    return "\n".join(o) + "\n"


def write_record(res: dict) -> str:
    d = os.path.join(agentic_dir(), "trust-report")
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, res["repo"] + ".json")
    rec = {"last_run": res["generated"], "stubs": len(res["stubs"]), "prs": res["pr_count"], "flagged": len(res["flagged"])}
    tmp = f"{path}.{os.getpid()}.tmp"
    json.dump(rec, open(tmp, "w"), indent=1)
    os.replace(tmp, path)
    return path


def main(argv: list[str]) -> int:
    if "--selftest" in argv:
        return selftest()
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("cmd", nargs="?", choices=["report", "record"], default="report")
    ap.add_argument("--root", action="append", required=True)
    ap.add_argument("--since")
    ap.add_argument("--out")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    since = None
    if a.since:
        try:
            since = dt.datetime.fromisoformat(a.since).replace(tzinfo=dt.timezone.utc).timestamp()
        except ValueError:
            print(f"bad --since {a.since!r}", file=sys.stderr)
            return 2
    for root in a.root:
        if not os.path.isdir(root):
            print(f"no such directory: {root}", file=sys.stderr)
            return 2
        try:
            res = analyse(root, since)
        except (RuntimeError, ValueError) as e:
            print(f"{root}: {e}", file=sys.stderr)
            return 2
        rec = write_record(res)
        if a.cmd == "record":
            print(f"{res['repo']}: {res['pr_count']} PRs, {len(res['flagged'])} flagged -> {rec}")
            continue
        body = json.dumps(res, indent=1) + "\n" if a.json else markdown(res)
        if a.out:
            multi = len(a.root) > 1 or os.path.isdir(a.out) or a.out.endswith("/")
            path = os.path.join(a.out, res["repo"] + (".json" if a.json else ".md")) if multi else a.out
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
            open(path, "w").write(body)
            print(path)
        else:
            sys.stdout.write(body)
    return 0


# --- selftest --------------------------------------------------------------------------

FAKE_GH = '''import json, os, sys
d = os.environ["FAKE_DIR"]
if sys.argv[1:3] == ["pr", "list"]:
    print(open(os.path.join(d, "prs.json")).read())
else:
    sys.exit(1)
'''


def selftest() -> int:
    fails: list[str] = []

    def check(name: str, ok: bool, why: object = "") -> None:
        print(("ok   " if ok else "FAIL ") + name + ("" if ok or not why else "  - " + str(why)[:300]))
        if not ok:
            fails.append(name)

    with tempfile.TemporaryDirectory() as tmp:
        ag = os.path.join(tmp, "agentic")
        os.makedirs(os.path.join(ag, "runs"))
        saved = {k: os.environ.get(k) for k in ("ASD_AGENTIC_DIR", "ASD_RUNS_DIR", "ASD_GH_BIN", "FAKE_DIR")}
        os.environ.update(ASD_AGENTIC_DIR=ag, ASD_GH_BIN=f"{sys.executable} {os.path.join(tmp, 'gh.py')}", FAKE_DIR=tmp)
        os.environ.pop("ASD_RUNS_DIR", None)
        open(os.path.join(tmp, "gh.py"), "w").write(FAKE_GH)
        g = lambda *x, cwd=None: subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *x], cwd=cwd, capture_output=True, text=True)
        ow = os.path.join(tmp, "origin.git")
        g("init", "-q", "--bare", "-b", "development", ow)
        w = os.path.join(tmp, "repo")
        g("clone", "-q", ow, w)
        W = lambda name, text: (os.makedirs(os.path.dirname(os.path.join(w, name)), exist_ok=True), open(os.path.join(w, name), "w").write(text))
        sha = lambda cwd=w: g("rev-parse", "HEAD", cwd=cwd).stdout.strip()
        W("base.txt", "base\n")
        g("add", ".", cwd=w); g("commit", "-q", "-m", "base", cwd=w); g("push", "-q", "origin", "HEAD:development", cwd=w)
        g("checkout", "-q", "-b", "stub/2026-01-05", cwd=w); g("push", "-q", "origin", "HEAD:stub/2026-01-05", cwd=w)

        def dossier(branch: str, fname: str, body: str, num: int) -> dict:
            g("checkout", "-q", "-b", branch, "stub/2026-01-05", cwd=w)
            W(fname, body); g("add", ".", cwd=w); g("commit", "-q", "-m", f"{branch}: add {fname}", cwd=w)
            g("checkout", "-q", "stub/2026-01-05", cwd=w)
            g("merge", "-q", "--no-ff", "-m", f"Merge pull request #{num} from {branch}", branch, cwd=w)
            return {"number": num, "title": branch, "headRefName": branch, "baseRefName": "stub/2026-01-05", "mergedAt": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "mergeCommit": {"oid": sha()}, "url": f"u/{num}"}

        code = "".join(f"let value_number_{i} = compute_something({i});\n" for i in range(6))
        p1 = dossier("feat/W-1-alpha", "alpha.rs", code, 1)
        p2 = dossier("feat/W-2-beta", "beta.rs", code.replace("value", "other"), 2)
        p3 = dossier("feat/W-3-gamma", "gamma.rs", code.replace("value", "third"), 3)
        # W-1 is reverted, W-2 gets a same-lines fix-up, W-3 stays clean apart from a stub fix.
        g("revert", "--no-edit", "-m", "1", p1["mergeCommit"]["oid"], cwd=w)
        W("beta.rs", "fixed\n"); g("add", ".", cwd=w); g("commit", "-q", "-m", "fix beta after the stub audit", cwd=w)
        g("checkout", "-q", "-b", "fix/W-3-stub2026-01-05-1", cwd=w)
        W("gamma_fix.rs", "small fix here\n"); g("add", ".", cwd=w); g("commit", "-q", "-m", "fix gamma", cwd=w)
        g("checkout", "-q", "stub/2026-01-05", cwd=w)
        g("merge", "-q", "--no-ff", "-m", "Merge pull request #4 from fix/W-3-stub2026-01-05-1", "fix/W-3-stub2026-01-05-1", cwd=w)
        p4 = {"number": 4, "title": "fix W-3", "headRefName": "fix/W-3-stub2026-01-05-1", "baseRefName": "stub/2026-01-05", "mergedAt": p1["mergedAt"], "mergeCommit": {"oid": sha()}, "url": "u/4"}
        g("push", "-q", "origin", "stub/2026-01-05", cwd=w)
        g("fetch", "-q", "origin", cwd=w)
        W(".discovery/deferred/W-3.yaml", json.dumps({"items": [{"id": "D-1", "kind": "latent-bug", "state": "open", "title": "found after the merge in the stub"}]}))
        W(".discovery/analysis/stub-2026-01-05.md", "## Step 8\nAttribution: W-2 owns the failing beta test\n")
        # a PR into development and a map-refresh PR must not count
        other = {"number": 9, "title": "x", "headRefName": "feat/W-9", "baseRefName": "development", "mergedAt": p1["mergedAt"], "mergeCommit": {"oid": sha()}, "url": "u/9"}
        refresh = {"number": 8, "title": "m", "headRefName": "chore/map-refresh", "baseRefName": "stub/2026-01-05", "mergedAt": p1["mergedAt"], "mergeCommit": {"oid": sha()}, "url": "u/8"}
        json.dump([p1, p2, p3, p4, other, refresh], open(os.path.join(tmp, "prs.json"), "w"))
        try:
            res = analyse(w)
            rows = {r["dossier"]: r for s in res["stubs"] for r in s["rows"]}
            check("only dossier PRs of stubs are rows", sorted(rows) == ["W-1", "W-2", "W-3"], sorted(rows))
            check("explicit revert found", rows["W-1"]["reverts"] == 1 and rows["W-1"]["flag"] == "FLAG", rows["W-1"])
            check("same-lines fix-up found", rows["W-2"]["fixups"] == 1 and rows["W-2"]["reverts"] == 0, rows["W-2"])
            check("record attribution counts as a fix", rows["W-2"]["fixes"] == 1, rows["W-2"])
            check("stub fix PR attributed", rows["W-3"]["fixes"] == 1 and rows["W-3"]["fix_prs"] == [4], rows["W-3"])
            check("post-merge deferred bug found", rows["W-3"]["post_merge_bugs"] == ["D-1"] and rows["W-3"]["flag"] == "FLAG", rows["W-3"])
            check("totals and flagged", res["totals"]["flagged"] == 2 and len(res["flagged"]) == 2 and res["pr_count"] == 3, res["totals"])
            check("score arithmetic", rows["W-1"]["score"] == 60 and rows["W-3"]["score"] == 65, (rows["W-1"]["score"], rows["W-3"]["score"]))
            md = markdown(res)
            check("markdown has the table and the trend", "| W-1 |" in md and "## Trend" in md and "## Totals" in md)
            # the CLI: report to a file, JSON, record
            outf = os.path.join(tmp, "out", "r.md")
            rc = main(["--root", w, "--out", outf])
            rec = os.path.join(ag, "trust-report", "repo.json")
            check("--out writes the report and the record", rc == 0 and os.path.exists(outf) and os.path.exists(rec))
            r = json.load(open(rec))
            check("record keys", set(r) == {"last_run", "stubs", "prs", "flagged"} and r["flagged"] == 2 and r["prs"] == 3 and r["stubs"] == 1, r)
            os.remove(rec)
            rc = main(["record", "--root", w])
            check("record subcommand writes only the record", rc == 0 and os.path.exists(rec))
            jf = os.path.join(tmp, "out", "r.json")
            main(["--root", w, "--json", "--out", jf])
            check("--json is valid JSON", json.load(open(jf))["repo"] == "repo")
            check("--since drops older PRs", analyse(w, since=dt.datetime(2100, 1, 1).timestamp())["pr_count"] == 0)
        finally:
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
    print("selftest:", "FAIL" if fails else "ok")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
