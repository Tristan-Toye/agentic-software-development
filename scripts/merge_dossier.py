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

**Kanban.** A dossier whose front matter says `delivery: kanban` (absent = stub) may have its bottom PR into `development`:
that chain merges as is (no stub, no carry-over); the queue first waits while development's latest CI (`--dev-ci`) is red
or pending (stop the line). `development` requires `local-stack` today; if GitHub refuses the merge for that, the state
reads "development requires local-stack; the owner must relax it for kanban PRs", with no retry. Never faked, no ruleset
is touched. `--kanban-dossiers --root R` lists the kanban dossiers with an open PR into development (eager merge).

State: `<key>.merge.json` `{state: merging|merged|failed, prs, base, error, ts}`, read by the dashboard. Prints one
line. Exit 0 merged, 1 refused or failed, 2 bad usage. `--dry-run` resolves and checks, and changes nothing.
`ASD_GH_BIN` replaces `gh` (tests).
"""
from __future__ import annotations

import argparse
import datetime
import glob
import json
import os
import re
import shlex
import subprocess
import sys
import contextlib
import io
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
RUNNER = os.environ.get("ASD_RUNNER_BIN") or os.path.join(HERE, "run_dossier.py")


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


# An outage or a GitHub 5xx is not a verdict on the PR: the queue waits it out, however long it lasts, and carries on
# where it was. Backs off to five minutes between tries. ASD_MERGE_NET_TRIES sets a limit; the default, 0, is none.
TRANSIENT_RE = re.compile(r"error connecting to|could not resolve host|temporary failure in name resolution|no such host|dial tcp|server misbehaving"
                          r"|connection (?:reset|refused|timed out)|operation timed out|i/o timeout|tls handshake|network is unreachable|unexpected eof"
                          r"|failed to connect|could not read from remote|http (?:502|503|504)|http 5\d\d:|the remote end hung up|timed out", re.I)
NET_WAITED = 0.0   # seconds this worker spent waiting out outages: not counted against any check's time limit


def retry_network(call) -> tuple[int, str]:
    global NET_WAITED
    tries = int(os.environ.get("ASD_MERGE_NET_TRIES") or 0)
    wait = float(os.environ.get("ASD_MERGE_NET_WAIT") or 30)
    i = 0
    while True:
        rc, out = call()
        i += 1
        if rc == 0 or not TRANSIENT_RE.search(out) or (tries and i >= tries):
            return rc, out
        pause = min(wait * 2 ** min(i - 1, 10), 300.0)
        time.sleep(pause)
        NET_WAITED += pause


def gh(root: str, *args: str, timeout: int = 600) -> tuple[int, str]:
    cmd = shlex.split(os.environ.get("ASD_GH_BIN") or "gh") + list(args)
    def call() -> tuple[int, str]:
        try:
            p = subprocess.run(cmd, cwd=root, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)
        except subprocess.TimeoutExpired:
            return 124, "timed out"
        except OSError as e:
            return 127, str(e)
        return p.returncode, (p.stdout if p.returncode == 0 else (p.stdout + p.stderr)).strip()
    return retry_network(call)


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


FAILURE_TEXT = re.compile(r"^The run of \S+ failed at stage \S+:")


def close_failure_questions(key: str) -> list[str]:
    """Close the still-open "the run failed" questions of the dossier `key` (<repo>.<dossier>) once it is merged: the
    failed run is no longer needed, and its question would sit on the dashboard as if it still blocked something.
    A failure question is one asked with `--topic run-failure`, or (older ones) with the "failed at stage" text. One a
    live run still waits on (runner.json: live pid, the question in its `questions`) is left alone, as is every
    other question and every answered one. Returns the ids closed."""
    qdir = os.environ.get("ASD_QUESTIONS_DIR") or os.path.expanduser("~/.agentic/questions")
    try:
        names = sorted(os.listdir(qdir))
    except OSError:
        return []
    rs = read_json(os.path.join(runs_dir(), key + ".runner.json"), {}) or {}
    waiting = set(rs.get("questions") or []) if alive(rs.get("pid")) else set()
    closed: list[str] = []
    for f in names:
        m = re.fullmatch(re.escape(key) + r"\.Q\d+\.jsonl", f)
        if not m:
            continue
        qid = f[: -len(".jsonl")]
        lines = []
        try:
            with open(os.path.join(qdir, f), encoding="utf-8") as fh:
                for raw in fh:
                    try:
                        lines.append(json.loads(raw))
                    except ValueError:
                        continue
        except OSError:
            continue
        q = next((x for x in lines if isinstance(x, dict) and x.get("kind") == "question"), None)
        if not q or qid in waiting:
            continue
        if any(isinstance(x, dict) and x.get("kind") == "answer" and (x.get("from") == "owner" or x.get("accepted") is True) for x in lines):
            continue
        if q.get("topic") != "run-failure" and not (not q.get("topic") and FAILURE_TEXT.match(str(q.get("text") or ""))):
            continue
        dossier = str(q.get("dossier") or key.rsplit(".", 1)[-1])
        append_line(os.path.join(qdir, f), {"ts": now(), "from": "runner", "kind": "answer", "accepted": True, "option": "B", "via": "merge-queue",
                                            "text": f"Closed automatically: {dossier} was merged; the failed run is no longer needed"})
        closed.append(qid)
    return closed


def is_stub(branch: str) -> bool:
    return bool(re.match(r"(stub|release)/", branch))


def kanban_ids(root: str) -> set[str]:
    """The ids of the dossiers in ROOT whose front matter says `delivery: kanban` (absent = stub). Only these may merge
    straight into development."""
    out: set[str] = set()
    for f in sorted(glob.glob(os.path.join(root, ".discovery", "dossiers", "*.md"))):
        try:
            with open(f, encoding="utf-8") as fh:
                head = fh.read(4000)
        except OSError:
            continue
        m = re.match(r"---\n(.*?)\n---", head, re.S)
        if not m:
            continue
        i = re.search(r"^id:\s*([A-Za-z][A-Za-z0-9]*-\d+)\s*$", m.group(1), re.M)
        if i and re.search(r"^delivery:\s*[\"']?kanban[\"']?\s*(#.*)?$", m.group(1), re.M):
            out.add(i.group(1).upper())
    return out


def dossier_prs(root: str) -> dict[int, str]:
    """{PR number: dossier id} from the `pr:` front matter of the dossiers in ROOT. A dossier with no Jira key builds on a
    branch named after its title (feat/engine-stop-rules), which names no id: its recorded PR is the only link."""
    out: dict[int, str] = {}
    for f in sorted(glob.glob(os.path.join(root, ".discovery", "dossiers", "*.md"))):
        try:
            with open(f, encoding="utf-8") as fh:
                head = fh.read(4000)
        except OSError:
            continue
        m = re.match(r"---\n(.*?)\n---", head, re.S)
        if not m:
            continue
        fm = m.group(1)
        i = re.search(r"^id:\s*([A-Za-z][A-Za-z0-9]*-\d+)\s*$", fm, re.M)
        if i:   # 79, [79, 80], or the PR's URL (…/pull/79), as /work-on Phase 9 records it: dossier_pr reads them all
            sys.path.insert(0, HERE)
            from dossier_pr import pr_field
            for n in pr_field(fm):
                out.setdefault(n, i.group(1).upper())
    return out


def dossier_ids(root: str) -> set[str]:
    """The ids of every dossier file in ROOT (`.discovery/dossiers/*.md` front matter). A branch name names a dossier only
    when its id is in here: a Jira key (feat/LABS-138-control-plane) is a branch's label, not a dossier."""
    out: set[str] = set()
    for f in sorted(glob.glob(os.path.join(root, ".discovery", "dossiers", "*.md"))):
        try:
            with open(f, encoding="utf-8") as fh:
                head = fh.read(4000)
        except OSError:
            continue
        m = re.match(r"---\n(.*?)\n---", head, re.S)
        i = re.search(r"^id:\s*([A-Za-z][A-Za-z0-9]*-\d+)\s*$", m.group(1), re.M) if m else None
        if i:
            out.add(i.group(1).upper())
    return out


def pr_dossier(p: dict, owned: dict | None, known: set[str] | None) -> str | None:
    """The dossier a PR belongs to: the id its `pr:` record gives (OWNED) first; else the id its branch name carries, but
    only a KNOWN dossier id (None = not checked, for callers that have no dossier index). Never a bare Jira key."""
    o = (owned or {}).get(p.get("number"))
    if o:
        return o.upper()
    for m in re.finditer(r"(?<![A-Za-z0-9])([A-Za-z][A-Za-z0-9]*-\d+)(?![0-9])", p.get("headRefName", "")):
        if known is None or m.group(1).upper() in known:
            return m.group(1).upper()
    return None


def chain_of(prs: list[dict], dossier: str, owned: dict | None = None) -> tuple[list[dict], str]:
    """The dossier's open PRs as one bottom-to-top chain, or ([], why not). OWNED ({PR number: dossier id}, from
    `dossier_prs`) adds the PRs a dossier records whose branch names no id."""
    pat = re.compile(r"(^|[^A-Za-z0-9])" + re.escape(dossier) + r"(?![0-9])", re.I)
    owned = owned or {}
    mine = [p for p in prs if (pat.search(p.get("headRefName", "")) or owned.get(p.get("number")) == dossier.upper())
            and not p.get("headRefName", "").startswith("plan/") and not is_stub(p.get("headRefName", ""))]
    if not mine:
        return [], f"no open pull request of {dossier}"
    # A PR stacked on one of them is part of the same stack whatever its branch is called: a repo that names its
    # branches by Jira key (feat/LABS-117-…) gets /fix-deferred's fix/W-007-deferred on top, and GitHub refuses to
    # merge the bottom of a stack alone.
    # And the PR a found one is stacked on is part of it too, whatever its branch is called: /fix-deferred's
    # fix/W-199-deferred sits on a research branch (docs/harness2-…) that names no id and whose pr: lives only on
    # that branch's dossier copy, not the main checkout's, so `owned` misses it. Without it the chain's bottom is
    # the fix PR into a non-stub branch and the queue refuses the dossier.
    while True:
        on_top = [p for p in prs if p not in mine and p.get("baseRefName") in {x["headRefName"] for x in mine}
                  and not p.get("headRefName", "").startswith(("plan/", "docs/"))]
        below = [p for p in prs if p not in mine and p not in on_top
                 and p.get("headRefName") in {x.get("baseRefName") for x in mine}
                 and not p.get("headRefName", "").startswith("plan/") and not is_stub(p.get("headRefName", ""))]   # the stub's own PR into development is nobody's stack
        if not on_top and not below:
            break
        mine += on_top + below
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
    stubs = [p for p in chain if is_stub(p.get("headRefName", ""))]
    if stubs:
        return [], f"{dossier}'s chain would include the stub's own pull request (#{stubs[0]['number']}, head {stubs[0]['headRefName']}); refusing"
    return chain, ""


def plan(root: str, dossier: str) -> tuple[list[dict], str]:
    """Resolve and check: (the chain, "") or ([], why it cannot merge)."""
    key = f"{repo_name(root)}.{dossier}"
    st = read_json(os.path.join(runs_dir(), key + ".runner.json"), {}) or {}
    if st.get("state") in ("running", "testing", "waiting", "paused", "queued") and alive(st.get("pid")):
        return [], f"a {st.get('command') or 'run'} run of {dossier} is still live; merge when it is finished"
    if resolve_runs_live(key):
        return [], f"a resolve-merge run of {dossier} is still live; merge when it is finished"
    rc, out = gh(root, "pr", "list", "--state", "open", "--limit", "200", "--json", "number,headRefName,baseRefName,isDraft,title,url")
    if rc != 0:
        return [], "gh pr list failed: " + out[:200]
    try:
        prs = json.loads(out or "[]")
    except ValueError:
        return [], "gh pr list gave no JSON"
    chain, why = chain_of(prs, dossier, dossier_prs(root))
    if not chain:
        return [], why
    for k in chain_keys(repo_name(root), dossier, chain)[1:]:   # a dossier id another branch of the stack names
        o = read_json(os.path.join(runs_dir(), k + ".runner.json"), {}) or {}
        if o.get("state") in ("running", "testing", "waiting", "paused", "queued") and alive(o.get("pid")):
            return [], f"a {o.get('command') or 'run'} run of {k.split('.', 1)[1]} on this stack is still live; merge when it is finished"
    base = chain[0].get("baseRefName", "")
    kanban = base == "development" and dossier.upper() in kanban_ids(root)   # a kanban dossier goes straight into development
    if kanban:
        drafts = [p for p in chain if p.get("isDraft")]
        return ([], "still a draft: " + ", ".join(f"#{p['number']}" for p in drafts) + "; mark it ready for review first") if drafts else (chain, "")
    if not is_stub(base):
        # Deleting a merged stub's branch (delete-on-merge) makes GitHub move its open PRs to development: such a PR
        # still belongs on a stub and moves to the accepting one below, like a PR whose old stub branch is kept.
        was = moved_off(root, chain[0]["number"]) if base == "development" else ""
        if not was or stub_into_dev(root, was) != "merged":
            return [], f"#{chain[0]['number']} goes into {base}, not a stub; only work on a stub is merged here"
        base = was
    # A dossier still in flight when its stub was integrated moves to the next stub, but only once its old stub is in
    # development: before that, the next stub lacks the old batch and the retargeted PR would carry it along.
    state = stub_into_dev(root, base)
    picked = (read_json(os.path.join(runs_dir(), f"{repo_name(root)}.{base.replace('/', '-')}.integrate.json"), {}) or {}).get("finished") or []
    if state == "open" and dossier not in picked:
        return [], f"its stub {base} is being integrated into development; it moves to the next stub once that merges"
    if state == "merged":
        nxt, why = accepting_stub(root, base)
        if not nxt:
            return [], why
        rc, out = move_base(root, [p["number"] for p in chain], nxt)
        if rc != 0:
            return [], f"could not move #{chain[0]['number']} from {base} to {nxt}: {out[:200]}"
        append_line(os.path.join(runs_dir(), f"{repo_name(root)}.{dossier}.jsonl"), {"ts": now(), "kind": "runner", "text": f"moved #{chain[0]['number']} from {base} (integrated) to the accepting stub {nxt}"})
        chain[0]["baseRefName"] = nxt
    drafts = [p for p in chain if p.get("isDraft")]
    if drafts:
        return [], "still a draft: " + ", ".join(f"#{p['number']}" for p in drafts) + "; mark it ready for review first"
    return chain, ""


def dev_ci(root: str) -> str:
    """green | pending | red | unknown: the checks of development's head commit. Kanban merges stop the line on red."""
    rc, out = gh(root, "api", "repos/{owner}/{repo}/commits/development/check-runs?per_page=100", "--jq", '[.check_runs[] | (.conclusion // "pending")] | join(",")')
    if rc != 0:
        return "unknown"
    b = [x for x in out.split(",") if x]
    return "red" if any(x in ("failure", "timed_out", "cancelled", "action_required") for x in b) else "pending" if "pending" in b else "green"


def wait_development(root: str, note) -> str:
    """Stop the line: "" once development's CI is green (or unreadable), else why a kanban merge gave up waiting."""
    poll = float(os.environ.get("ASD_MERGE_POLL") or 15)
    end = time.time() + float(os.environ.get("ASD_MERGE_CHECK_TIMEOUT") or 3600)
    said = ""
    while True:
        st = dev_ci(root)
        if st in ("green", "unknown"):
            return ""
        if st != said:
            note("waiting", f"development's CI is {st}; kanban merges wait until it is green")
            said = st
        if time.time() > end:
            return f"development's CI stayed {st}; kanban merges wait until it is green (fix development, then merge again)"
        time.sleep(poll)


def stub_into_dev(root: str, stub: str) -> str:
    """The stub's pull request into development: "open", "merged", or "" (none: it still accepts dossiers)."""
    rc, out = gh(root, "pr", "list", "--head", stub, "--base", "development", "--state", "all", "--json", "state", "-q", "[.[].state] | join(\",\")")
    states = out.split(",") if rc == 0 and out else []
    return "open" if "OPEN" in states else "merged" if "MERGED" in states else ""


def accepting_stub(root: str, old: str) -> tuple[str, str]:
    """The stub new work goes to, by the one rule every flow shares (scripts/stub_target.py): the accepting stub, or a
    new one cut from development only once no stub is being integrated. It must already hold the integrated `old`."""
    sys.path.insert(0, HERE)
    from stub_target import target
    try:
        rc, b = target(root, False)
    except RuntimeError as e:
        return "", str(e)
    if rc == 3:
        return "", f"{b.split()[-1]} is being integrated; the next stub is cut once it merges"
    subprocess.run(["git", "-C", root, "fetch", "-q", "origin", "--prune"], capture_output=True, timeout=180)
    tip = f"origin/{old}"
    if subprocess.run(["git", "-C", root, "rev-parse", "-q", "--verify", tip], capture_output=True).returncode != 0:
        # The old stub's branch was deleted on merge: what development took from it is that PR's merge commit.
        rc, tip = gh(root, "pr", "list", "--head", old, "--base", "development", "--state", "merged", "--json", "mergeCommit", "-q", ".[0].mergeCommit.oid")
        if rc != 0 or not tip:
            return "", f"{old} is gone from origin and its merge into development was not found"
    held = subprocess.run(["git", "-C", root, "merge-base", "--is-ancestor", tip, f"origin/{b}"], capture_output=True).returncode == 0
    if not held:
        return "", f"the accepting stub {b} does not hold {old} yet; it takes development first (/stub Step 1), then the dossier moves"
    return b, ""


def moved_off(root: str, num: int) -> str:
    """The stub GitHub moved this PR off onto development, or "". Deleting a merged stub's branch (the repository's
    delete-on-merge setting) moves every open PR into it to the stub PR's base and records an AutomaticBaseChange
    event; a hand retarget records a BaseRefChanged one. Either keeps the old base."""
    q = ("query($owner:String!,$repo:String!,$n:Int!){repository(owner:$owner,name:$repo){pullRequest(number:$n){"
         "timelineItems(last:20,itemTypes:[AUTOMATIC_BASE_CHANGE_SUCCEEDED_EVENT,BASE_REF_CHANGED_EVENT]){nodes{"
         "... on AutomaticBaseChangeSucceededEvent{oldBase newBase} ... on BaseRefChangedEvent{previousRefName currentRefName}}}}}}")
    rc, out = gh(root, "api", "graphql", "-F", "owner={owner}", "-F", "repo={repo}", "-F", f"n={num}", "-f", f"query={q}",
                 "--jq", '[.data.repository.pullRequest.timelineItems.nodes[] | {was: (.oldBase // .previousRefName), now: (.newBase // .currentRefName)}'
                 ' | select(.now == "development")] | last | .was // ""')
    return out.strip() if rc == 0 and is_stub(out.strip()) else ""


def stack_numbers(root: str, num: int) -> list[int]:
    """The GitHub stacks this PR was added to (newest last), from its timeline."""
    q = ("query($owner:String!,$repo:String!,$n:Int!){repository(owner:$owner,name:$repo){pullRequest(number:$n){"
         "timelineItems(last:20,itemTypes:[ADDED_TO_STACK_EVENT]){nodes{... on AddedToStackEvent{stackNumber}}}}}}")
    rc, out = gh(root, "api", "graphql", "-F", "owner={owner}", "-F", "repo={repo}", "-F", f"n={num}", "-f", f"query={q}",
                 "--jq", "[.data.repository.pullRequest.timelineItems.nodes[].stackNumber | numbers] | map(tostring) | join(\" \")")
    return [int(x) for x in out.split()] if rc == 0 else []


def move_base(root: str, chain: list[int], nxt: str) -> tuple[int, str]:
    """Point the bottom PR of `chain` (bottom to top) at `nxt`. GitHub refuses a base change on a PR in a stack
    ("part of a stack"): then every stack the chain's PRs were in is unstacked, the bottom moved, and the chain linked
    again as one stack on `nxt` (merged PRs stay in their old stack; GitHub keeps them there)."""
    rc, out = gh(root, "pr", "edit", str(chain[0]), "--base", nxt)
    if rc == 0 or "part of a stack" not in out:
        return rc, out
    for n in sorted({s for c in chain for s in stack_numbers(root, c)}):
        gh(root, "stack", "unstack", str(n))
    rc, out = gh(root, "pr", "edit", str(chain[0]), "--base", nxt)
    if rc != 0:
        return rc, out
    if len(chain) > 1:
        rc2, out2 = gh(root, "stack", "link", "--base", nxt, *[str(c) for c in chain])
        if rc2 != 0:
            return 0, f"moved, but relinking the stack failed (merge relinks it): {out2[-200:]}"
    return 0, out


def stacked_on(prs: list[dict], num: int) -> list[int]:
    """`num` and the open PRs stacked on it, bottom to top (one PR per level: the first whose base is the head)."""
    by_base = {}
    for p in prs:
        by_base.setdefault(p.get("baseRefName"), p)
    head = next((p.get("headRefName") for p in prs if p["number"] == num), None)
    out, seen = [num], {num}
    while head in by_base and by_base[head]["number"] not in seen:
        nxt = by_base[head]; out.append(nxt["number"]); seen.add(nxt["number"]); head = nxt.get("headRefName")
    return out


def carry_over(root: str, stub: str) -> tuple[bool, list[str]]:
    """After `stub` merged into development: move the open PRs it left behind (still on it, or moved to development by
    GitHub when its branch was deleted) to the accepting stub, and drop the failed merge records that only said "its
    stub is being integrated", so the queue and Integrate take those dossiers on the next stub. (done, lines): done is
    False when it must be tried again (the stub not merged yet, the next stub not ready, a gh failure)."""
    if stub_into_dev(root, stub) != "merged":
        return False, [f"{stub} is not merged into development yet"]
    rc, out = gh(root, "pr", "list", "--state", "open", "--limit", "200", "--json", "number,headRefName,baseRefName")
    if rc != 0:
        return False, [f"gh pr list failed: {out[:200]}"]
    left = [p for p in json.loads(out or "[]") if p.get("baseRefName") == stub
            or p.get("baseRefName") == "development" and moved_off(root, p["number"]) == stub]
    lines, done = [], True
    if left:
        nxt, why = accepting_stub(root, stub)
        if not nxt:
            return False, [why]
        prs = json.loads(out or "[]")
        for p in left:
            rc, out = move_base(root, stacked_on(prs, p["number"]), nxt)
            if rc != 0:
                done = False; lines.append(f"could not move #{p['number']} to {nxt}: {out[:200]}"); continue
            lines.append(f"moved #{p['number']} {p['headRefName']} from {stub} to {nxt}")
    repo, stale = repo_name(root), f"its stub {stub} is being integrated"
    for f in sorted(glob.glob(os.path.join(runs_dir(), f"{repo}.*.merge.json"))):
        st = read_json(f, {}) or {}
        if st.get("state") == "failed" and stale in (st.get("error") or ""):
            os.remove(f); lines.append(f"cleared the stale merge record of {os.path.basename(f)[len(repo) + 1:-len('.merge.json')]}")
    return done, lines


def mark_detector(repo: str, chain: list[dict], dossier: str) -> None:
    """Record the PRs as started in the merge detector's file, so it does not start a second cleanup run."""
    path = os.path.join(runs_dir(), f"{repo}.merges.json")
    seen = read_json(path, {}) or {}
    for p in chain:
        seen[str(p["number"])] = {"id": dossier, "base": chain[0].get("baseRefName", ""), "mergedAt": now(), "state": "started", "at": now()}
    write_json(path, seen)


# Who started a merge, for the dossier's timeline: the owner's Merge button, auto work's eager merge, or /stub's
# integration. Unknown sources read as the button (the CLI default).
SOURCES = {"button": "your Merge button", "eager": "eager merge: finished and on an open stub", "integrate": "the stub's integration",
           "handoff": "a run whose CI cannot start: a parent PR of its stack conflicts"}


def source_text(by: str | None) -> str:
    return SOURCES.get(by or "button", SOURCES["button"])


def merge(root: str, dossier: str, method: str, dry: bool, no_cleanup: bool = False, by: str = "button") -> int:
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
        # Without --base, linking moves the bottom PR to the repository's default branch.
        rc, out = gh(root, "stack", "link", "--base", base, *[str(n) for n in nums])
        if rc != 0:
            return fail(state_path, nums, base, f"gh stack link {' '.join(map(str, nums))} failed: {out[-300:]}")
        rc, out = gh(root, "stack", "merge", str(nums[-1]), "--yes", f"--{method}", timeout=1800)
    else:
        rc, out = gh(root, "pr", "merge", str(nums[0]), f"--{method}", timeout=600)
    if rc != 0:
        if base == "development" and "local-stack" in out:   # the ruleset wants the stub's receipt; never faked, never retried
            return fail(state_path, nums, base, "development requires local-stack; the owner must relax it for kanban PRs")
        return fail(state_path, nums, base, out[-400:] or f"exit {rc}")
    open_left = []
    for n in nums:
        rc, st = gh(root, "pr", "view", str(n), "--json", "state", "-q", ".state")
        if rc != 0 or st.strip() != "MERGED":
            open_left.append(f"#{n} ({st.strip() or 'unknown'})")
    if open_left:
        return fail(state_path, nums, base, "not merged afterwards: " + ", ".join(open_left))
    write_json(state_path, {"state": "merged", "prs": nums, "base": base, "error": "", "ts": now()})
    close_failure_questions(key)
    append_line(os.path.join(d, key + ".jsonl"), {"ts": now(), "kind": "runner", "text": f"merged {label} into {base}" + (" as one stack" if len(nums) > 1 else "") + f" ({source_text(by)})"})
    mark_detector(repo, chain, dossier)
    if no_cleanup:
        print(f"merge_dossier: merged {label} into {base}")
        return 0
    after = start_run(root, dossier_id(root, dossier, chain), "after-merge", nums)
    print(f"merge_dossier: merged {label} into {base}; cleanup: {after[:200]}")
    return 0


def start_run(root: str, dossier: str, command: str, nums: list[int]) -> str:
    """Start the dossier's agent on the runner for `command` with the PR numbers; its first output line."""
    r = subprocess.run([sys.executable, RUNNER, dossier, "--root", root, "--command", command, "--items", ",".join(map(str, nums)), "--model", "sonnet"],
                       capture_output=True, text=True, timeout=60)
    return (r.stdout.strip().splitlines() or [r.stderr.strip()] or [""])[0] if (r.stdout or r.stderr) else ""


# ---------------------------------------------------------------- the merge queue
# Merge presses queue per repository, first in first out; one worker per repository works the queue: for each
# dossier it waits for the dossier's own run, resolves conflicts through the dossier's agent (`/resolve-merge`), merges
# by script, runs `/after-merge` to the end, then takes the next. Nothing merges while another dossier's cleanup runs.

def queue_path(repo: str) -> str:
    return os.path.join(runs_dir(), f"{repo}.merge-queue.json")


def locked(repo: str):
    import fcntl
    os.makedirs(runs_dir(), exist_ok=True)
    fh = open(os.path.join(runs_dir(), f"{repo}.merge-queue.lock"), "a+")
    fcntl.flock(fh, fcntl.LOCK_EX)
    return fh


def set_state(key: str, **kw) -> None:
    p = os.path.join(runs_dir(), key + ".merge.json")
    write_json(p, {**(read_json(p, {}) or {}), "ts": now(), **kw})
    if kw.get("state") == "merged":
        close_failure_questions(key)


def enqueue(root: str, dossier: str, by: str = "button") -> int:
    """Add the dossier to its repository's merge queue (once) and make sure a worker runs it. `by` names who asked
    (`SOURCES`); it is kept on the queue item and written to the dossier's timeline, and the merge line repeats it."""
    repo = repo_name(root)
    with locked(repo):
        q = read_json(queue_path(repo), {}) or {}
        items = q.get("items") or []
        waiting = any(i["id"] == dossier for i in items)
        if not waiting:
            items.append({"id": dossier, "ts": now(), "by": by})
        q["items"], q["root"] = items, root
        write_json(queue_path(repo), q)
        pos = [i["id"] for i in items].index(dossier) + 1
        if not waiting:   # one already in the queue keeps its state: the worker may be busy with it
            # a new press is the owner's retry: it starts with fresh resolve-merge attempts (a restart keeps them)
            prior = read_json(os.path.join(runs_dir(), f"{repo}.{dossier}.merge.json"), {}) or {}
            again = f"; earlier merge of {', '.join('#%s' % n for n in prior['prs'])} done, this is a new pull request" if prior.get("state") == "merged" and prior.get("prs") else ""
            # the old record's note ("merged; /after-merge runs") and prs must not read as this merge's
            set_state(f"{repo}.{dossier}", state="queued", position=pos, error="", prs=[], pid=None, resolve_attempts=None, note=None, by=by)
            append_line(os.path.join(runs_dir(), f"{repo}.{dossier}.jsonl"),
                        {"ts": now(), "kind": "runner", "text": f"merge queue: queued at position {pos} ({source_text(by)}){again}"})
        worker = q.get("worker")
        if not (worker and alive(worker)):
            log = os.path.join(runs_dir(), f"{repo}.merge-queue.log")
            with open(log, "ab") as fh:
                p = subprocess.Popen([sys.executable, os.path.abspath(__file__), "--worker", "--root", root], stdin=subprocess.DEVNULL,
                                     stdout=fh, stderr=subprocess.STDOUT, start_new_session=True)
            q["worker"] = p.pid
            write_json(queue_path(repo), q)
    print(f"merge_dossier: {dossier} is number {pos} in the merge queue")
    return 0


def run_live(key: str) -> bool:
    st = read_json(os.path.join(runs_dir(), key + ".runner.json"), {}) or {}
    return st.get("state") in ("running", "testing", "waiting", "paused", "queued") and alive(st.get("pid"))


def resolve_key(key: str, nums: list[int]) -> str:
    """The run key run_dossier gives a queue-started /resolve-merge of these PRs: `<repo>.<ID>.resolve-<pr>-<pr>`."""
    return f"{key}.resolve-" + "-".join(str(n) for n in nums)


def resolve_runs_live(key: str) -> list[str]:
    """The live `<key>.resolve-*` runs: the queue's own resolve-merge runs and the runner's parent-PR ones."""
    try:
        names = os.listdir(runs_dir())
    except OSError:
        return []
    return [n[: -len(".runner.json")] for n in sorted(names) if n.startswith(key + ".resolve-") and n.endswith(".runner.json")
            and run_live(n[: -len(".runner.json")])]


def runner_lock_held(key: str) -> bool:
    """Whether a runner holds the run's `<key>.runner.lock` right now (run_dossier.try_lock's flock, probed and released at
    once, without touching the pid inside). A failed run keeps it while it waits on its failure question."""
    try:
        import fcntl
        fh = open(os.path.join(runs_dir(), key + ".runner.lock"), "a+")
    except (OSError, ImportError):
        return False
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return True
    finally:
        fh.close()
    return False


PARENT_CONFLICT_RE = re.compile(r"parent PR[^.|]{0,60}\bconflict", re.I)   # a run failed only because CI cannot start under a conflicting parent


def open_failure_questions(key: str, conflicts: bool = True) -> list[str]:
    """The unanswered run-failure questions of the dossier `key` (<repo>.<dossier>): ones asked with `--topic run-failure`
    or (older ones) with the "failed at stage" text. The dossier's own failed run asked them and waits on the owner."""
    qdir = os.environ.get("ASD_QUESTIONS_DIR") or os.path.expanduser("~/.agentic/questions")
    try:
        names = sorted(os.listdir(qdir))
    except OSError:
        return []
    out: list[str] = []
    for f in names:
        if not re.fullmatch(re.escape(key) + r"\.Q\d+\.jsonl", f):
            continue
        lines = []
        try:
            with open(os.path.join(qdir, f), encoding="utf-8") as fh:
                for raw in fh:
                    try:
                        lines.append(json.loads(raw))
                    except ValueError:
                        continue
        except OSError:
            continue
        q = next((x for x in lines if isinstance(x, dict) and x.get("kind") == "question"), None)
        if not q or any(isinstance(x, dict) and x.get("kind") == "answer" and (x.get("from") == "owner" or x.get("accepted") is True) for x in lines):
            continue
        if not conflicts and PARENT_CONFLICT_RE.search(str(q.get("text") or "")):
            continue   # the stack's own conflict: the queue resolves it, so that question must not hold the queue (W-182)
        if q.get("topic") == "run-failure" or (not q.get("topic") and FAILURE_TEXT.match(str(q.get("text") or ""))):
            out.append(f[: -len(".jsonl")])
    return out


def wait_runner_free(key, timeout_s: float, while_question: bool = True) -> bool:
    """Wait until no runner holds the dossier's lock and (while_question) it has no open run-failure question of its own.
    The question has no deadline: the owner answers it. A lock alone gives up after `timeout_s`. True when free."""
    poll = float(os.environ.get("ASD_MERGE_POLL") or 15)
    keys = [key] if isinstance(key, str) else list(key)
    end = time.time() + timeout_s
    while True:
        asking = while_question and any(open_failure_questions(k, conflicts=False) for k in keys)
        held = any(runner_lock_held(k) for k in keys)
        if not asking and not held:
            return True
        if held and any(run_live(k) for k in keys):
            end = time.time() + timeout_s   # a live run is waited for, however long: waiting is not failing
        if not asking and time.time() >= end:
            return False
        idle_tick()
        time.sleep(poll)


QUEUE_RUNS = ("resolve-merge", "after-merge")


def chain_keys(repo: str, dossier: str, chain: list[dict]) -> list[str]:
    """The run keys of a stack: its dossier's, and every dossier id a branch in it names (fix/W-007-deferred on feat/LABS-117-…)."""
    ids = [dossier] + [m.upper() for p in chain for m in re.findall(r"(?<![A-Za-z0-9])([A-Za-z][A-Za-z0-9]*-\d+)(?![0-9])", p.get("headRefName", ""))]
    return [f"{repo}.{i}" for i in dict.fromkeys(i.upper() for i in ids)]


def still_building(key: str) -> bool:
    """A live run that is the dossier's own work. A run the merge queue started (`QUEUE_RUNS`) means the stack is already being merged."""
    st = read_json(os.path.join(runs_dir(), key + ".runner.json"), {}) or {}
    return run_live(key) and st.get("command") not in QUEUE_RUNS


def picked_stacks(ids: list, ready, before: list) -> list:
    """The stacks an integration merges: those ready now, and those an earlier start of the same integration picked.
    A restart never drops a stack, whatever it is doing at that moment."""
    return [i for i, p in ids if i in before or ready(i, p)]


WAIT_HOOK = None   # set by the worker while its head waits on an agent: called on every poll, so the queue goes on behind it
_IN_HOOK = False


def idle_tick() -> None:
    """One chance for the worker to use a wait on an agent (`advance`); never nested, and never a reason to stop the wait."""
    global _IN_HOOK
    if WAIT_HOOK is None or _IN_HOOK:
        return
    _IN_HOOK = True
    try:
        WAIT_HOOK()
    except Exception:
        pass
    finally:
        _IN_HOOK = False


def wait_run(key, since: str, timeout_s: float) -> str:
    """Wait for the run started at `since` (or later) to end; its end state ("done", "failed", "stopped") or "timeout".
    `key` is one run key or several (the dossier's own and the `.resolve-<prs>` one a queue-started /resolve-merge has).
    Only the run's own working time counts against `timeout_s`: an outage, a usage limit, a slot it queued for and an owner
    question each extend the deadline by the real time they took (W-161 queued for hours and was timed out at 4 h). A run
    that failed but still holds its lock or has an open run-failure question is alive: its owner may answer Retry (W-197)."""
    poll = float(os.environ.get("ASD_MERGE_POLL") or 15)
    keys = [key] if isinstance(key, str) else list(key)
    last = time.time()
    end = last + timeout_s
    idle_prev = False
    while True:
        t = time.time()
        if idle_prev:
            end += t - last
        last = t
        if t >= end:
            return "timeout"
        sts = {k: read_json(os.path.join(runs_dir(), k + ".runner.json"), {}) or {} for k in keys}
        mine = [k for k in keys if str(sts[k].get("started") or "") >= since]
        mine.sort(key=lambda k: not run_live(k))
        cur = sts[mine[0]] if mine else {}
        idle_prev = bool(mine) and cur.get("state") in ("paused", "queued", "waiting")
        if mine and not any(run_live(k) for k in mine):
            if cur.get("state") not in ("done", "stopped") and any(runner_lock_held(k) or open_failure_questions(k, conflicts=False) for k in mine):
                idle_prev = True   # it failed and asks the owner: the same run goes on after an answer
            else:
                return str(cur.get("state") or "stopped") if alive(cur.get("pid")) or cur.get("state") in ("done", "failed", "stopped") else "stopped"
        idle_tick()
        time.sleep(poll)


def conflicting(root: str, nums: list[int]) -> tuple[list[int], str]:
    """The PRs GitHub reports as conflicting with their base. GitHub computes this lazily: UNKNOWN is asked again."""
    tries = int(os.environ.get("ASD_MERGE_MERGEABLE_TRIES") or 12)
    bad = []
    for n in nums:
        for _ in range(tries):
            rc, out = gh(root, "pr", "view", str(n), "--json", "mergeable", "-q", ".mergeable")
            if rc != 0:
                return [], f"gh pr view {n} failed: {out[:160]}"
            if out.strip() != "UNKNOWN":
                break
            time.sleep(float(os.environ.get("ASD_MERGE_POLL") or 15) / 3)
        if out.strip() == "CONFLICTING" and not git_merges_clean(root, n):
            bad.append(n)
    return bad, ""


def git_merges_clean(root: str, n: int) -> bool:
    """True when git itself proves PR n merges into its base without a conflict, whatever GitHub's flag says.

    GitHub's `mergeable` can stay CONFLICTING for a head that already holds its whole base (seen on W-137 #659: the
    head descended from the base, yet GitHub said DIRTY for hours), and the queue then sent resolve-merge after a
    conflict git cannot find, twice, and asked the owner. Only a positive proof overrides GitHub: the refs fetched,
    and the base an ancestor of the head or `git merge-tree` clean. Anything that cannot be proven keeps the flag."""
    rc, out = gh(root, "pr", "view", str(n), "--json", "baseRefName,headRefName")
    try:
        refs = json.loads(out) if rc == 0 else {}
        base, head = refs["baseRefName"], refs["headRefName"]
    except (ValueError, KeyError, TypeError):
        return False
    if git(root, "fetch", "-q", "origin", base, head)[0] != 0:
        return False
    b, h = f"refs/remotes/origin/{base}", f"refs/remotes/origin/{head}"
    if git(root, "rev-parse", "-q", "--verify", b)[0] != 0 or git(root, "rev-parse", "-q", "--verify", h)[0] != 0:
        return False
    clean = git(root, "merge-base", "--is-ancestor", b, h)[0] == 0 or git(root, "merge-tree", "--write-tree", b, h)[0] == 0
    if clean:
        print(f"[merge_dossier] #{n}: GitHub says CONFLICTING but git merges {head} into {base} cleanly; "
              "treated as mergeable (GitHub's flag is stale)", flush=True)
    return clean


def behind(root: str, nums: list[int]) -> list[int]:
    """The PRs a strict branch rule refuses until they hold the latest base. UNKNOWN (right after a merge) is asked again."""
    tries = int(os.environ.get("ASD_MERGE_MERGEABLE_TRIES") or 12)
    out = []
    for n in nums:
        st = ""
        for _ in range(tries):
            rc, st = gh(root, "pr", "view", str(n), "--json", "mergeStateStatus", "-q", ".mergeStateStatus")
            if rc != 0 or st.strip() != "UNKNOWN":
                break
            time.sleep(float(os.environ.get("ASD_MERGE_POLL") or 15) / 3)
        if rc == 0 and st.strip() == "BEHIND":
            out.append(n)
    return out


# ---------------------------------------------------------------- shared steps: what blocks a merge, fixed
# The same steps settle a dossier's stack (into its stub) and a non-code PR (into development), cheapest first:
#   1. GitHub's mergeability: UNKNOWN right after another merge, so it is asked again (conflicting()).
#   2. Id collisions, mechanical: a dossier id or an ADR/LRN number the PR adds that the base (or another open PR)
#      already has is renumbered on the PR's branch, in a scratch worktree, then pushed (no agent).
#   3. A branch behind its base: every merge puts the rest of the queue behind, and a strict branch rule refuses them,
#      so the base is merged into the branch (gh pr update-branch) and its checks run again.
#   4. Red checks: failed jobs are re-run once (flaky checks), then waited for.
#   5. What is left (a content conflict, a check still red): the dossier's own agent, a fresh /resolve-merge run under
#      the dossier's key (a live run of it gets a message instead), then the steps again, at most AGENT_ROUNDS times.

ID_FILE_RE = re.compile(r"^\.discovery/dossiers/([A-Z][A-Z0-9]*-\d+)-[^/]+\.md$")
ADR_FILE_RE = re.compile(r"^docs/adr/(\d{4})-[^/]+\.md$")


def dossier_id(root: str, fallback: str, chain: list[dict]) -> str:
    """The dossier a stack belongs to, for the runs the queue starts on it (/resolve-merge, /after-merge), so they land on
    the dossier's own timeline. The queue names a stack by the id in its bottom branch, which in a repo that names
    branches by Jira key (feat/LABS-117-…) is not the dossier's: take an id a branch of the stack names that has a dossier
    file, else the dossier whose `pr:` names one of the stack's PRs, else `fallback`."""
    if not chain:
        return fallback
    cands = [k.split(".", 1)[1] for k in chain_keys("r", fallback, chain)]
    nums = {str(p["number"]) for p in chain}
    for ref in [f"origin/{p['headRefName']}" for p in reversed(chain)] + [f"origin/{chain[0].get('baseRefName', '')}"]:
        rc, tree = git(root, "ls-tree", "--name-only", ref, ".discovery/dossiers/")
        files = {m.group(1): l.strip() for l in (tree.splitlines() if rc == 0 else []) if (m := ID_FILE_RE.match(l.strip()))}
        if not files:
            continue
        for c in cands:
            if c in files:
                return c
        for i, f in files.items():
            _, body = git(root, "show", f"{ref}:{f}")
            head = body.split("\n---", 1)[0] if body.startswith("---") else ""
            if set(re.findall(r"/pull/(\d+)", head)) & nums:
                return i
        return fallback
    return fallback


def git(root: str, *args: str, timeout: int = 300) -> tuple[int, str]:
    def call() -> tuple[int, str]:
        try:
            p = subprocess.run(["git", "-C", root, *args], capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            return 124, "timed out"
        return p.returncode, (p.stdout if p.returncode == 0 else p.stdout + p.stderr).strip()
    return retry_network(call) if args[0] in ("fetch", "push") else call()


def ids_on(root: str, ref: str) -> tuple[set[str], set[str]]:
    """(dossier ids, ADR numbers) in a tree."""
    _, d = git(root, "ls-tree", "--name-only", ref, ".discovery/dossiers/")
    _, a = git(root, "ls-tree", "--name-only", ref, "docs/adr/")
    return ({m.group(1) for l in d.splitlines() if (m := ID_FILE_RE.match(l.strip()))},
            {m.group(1) for l in a.splitlines() if (m := ADR_FILE_RE.match(l.strip()))})


def added_ids(root: str, base: str, head: str) -> tuple[list[tuple[str, str]], list[str]]:
    """What the branch adds over its base: [(dossier id, file)], [ADR number]."""
    _, out = git(root, "diff", "--name-only", "--diff-filter=A", f"origin/{base}...origin/{head}")
    dos = [(m.group(1), l) for l in out.splitlines() if (m := ID_FILE_RE.match(l))]
    adrs = [m.group(1) for l in out.splitlines() if (m := ADR_FILE_RE.match(l))]
    return dos, adrs


def collisions(root: str, base: str, head: str, others: list[str]) -> tuple[list[tuple[str, str]], bool]:
    """([(dossier id, file)] to renumber, whether an ADR number collides). A dossier id collides with the base or with
    an id another open PR's branch adds (the PR earlier in the queue keeps it)."""
    taken_d, taken_a = ids_on(root, f"origin/{base}")
    _, tree = git(root, "ls-tree", "--name-only", f"origin/{base}", ".discovery/dossiers/")
    on_base = set(tree.split("\n"))
    theirs: set[str] = set()
    for o in others:
        d, _ = added_ids(root, base, o)
        taken_d |= {i for i, _ in d}
        theirs |= {f for _, f in d}
    dos, adrs = added_ids(root, base, head)
    # The same file is the same dossier, not a collision: a branch cut after a plan landed carries that plan's dossier too.
    return [(i, f) for i, f in dos if i in taken_d and f not in on_base and f not in theirs], any(a in taken_a for a in adrs)


MIGRATION_RE = re.compile(r"^(.*/)?migrations/(\d+)[_-][^/]+\.sql$")


def bare_ref(ref: str) -> str:
    """A branch name without the remote: `origin/fix/W-1`, `refs/remotes/origin/fix/W-1` and `fix/W-1` all give `fix/W-1`.
    The migration helpers add `origin/` themselves; a caller's `origin/` would look up origin/origin/... and read nothing."""
    for pre in ("refs/remotes/origin/", "refs/heads/", "origin/"):
        if ref.startswith(pre):
            return ref[len(pre):]
    return ref


def migration_scan(root: str, base: str, head: str) -> tuple[list[str], str]:
    """(clashes, "") or ([], why the refs could not be read). Migrations the head adds under a number another migration
    in the same folder already has, on the base or in the head. A fresh database cannot migrate then, and CI without a
    database cannot see it. Numbers the base already repeats on its own are not this head's."""
    base, head = bare_ref(base), bare_ref(head)
    git(root, "fetch", "-q", "origin", base, head)
    rc, added = git(root, "diff", "--no-renames", "--diff-filter=A", "--name-only", f"origin/{base}...origin/{head}")
    rc2, tree = git(root, "ls-tree", "-r", "--name-only", f"origin/{base}")
    if rc != 0 or rc2 != 0:
        return [], f"refs unreadable: origin/{base} or origin/{head} is not on origin (pass bare branch names, e.g. fix/W-1, not origin/fix/W-1)"
    def keyed(paths):
        return {p: (m.group(1) or "", m.group(2)) for p in paths if (m := MIGRATION_RE.match(p.strip()))}
    new, old = keyed(added.splitlines()), keyed(tree.splitlines())
    out = []
    for path, key in sorted(new.items()):
        others = sorted(p for p, k in {**old, **new}.items() if k == key and p != path)
        if others:
            out.append(f"{path} has the number of {others[0]}")
    return out, ""


def migration_clashes(root: str, base: str, head: str) -> list[str]:
    """The queue's own check: unreadable refs (a branch not on origin) are left to the merge's checks, explicitly: []."""
    found, unreadable = migration_scan(root, base, head)
    return [] if unreadable else found


def next_free_migration(root: str, base: str, folder_of: str) -> str:
    """The next migration number free in the folder of the migration file `folder_of`, across the base and the files every
    open pull request head adds (one clash renumbered onto the base's next number would clash with another open branch).
    Keeps the zero padding of the number in that file name. "" when nothing can be read."""
    base = bare_ref(base)
    m = MIGRATION_RE.match(folder_of)
    if not m:
        return ""
    folder, width = m.group(1) or "", len(m.group(2))
    rc, rows = gh(root, "pr", "list", "--state", "open", "--limit", "200", "--json", "headRefName")
    try:
        heads = [r["headRefName"] for r in json.loads(rows)] if rc == 0 else None
    except (ValueError, TypeError, KeyError):
        heads = None
    if heads is None:
        return ""
    git(root, "fetch", "-q", "origin", base, *heads)
    nums: list[int] = []
    rc, tree = git(root, "ls-tree", "-r", "--name-only", f"origin/{base}")
    if rc != 0:
        return ""
    paths = tree.splitlines()
    for h in heads:
        rc, added = git(root, "diff", "--no-renames", "--diff-filter=A", "--name-only", f"origin/{base}...origin/{h}")
        if rc == 0:
            paths += added.splitlines()
    for p in paths:
        mm = MIGRATION_RE.match(p.strip())
        if mm and (mm.group(1) or "") == folder:
            nums.append(int(mm.group(2)))
    return str(max(nums or [0]) + 1).zfill(width)


def next_free_id(root: str, prefix: str) -> str:
    """The next dossier id no branch on origin holds: base, stubs, plans and every open PR's head."""
    git(root, "fetch", "-q", "origin", "--prune")
    _, refs = git(root, "for-each-ref", "--format=%(refname:short)", "refs/remotes/origin/")
    nums = []
    for r in refs.split():
        d, _ = ids_on(root, r)
        nums += [int(i.split("-")[1]) for i in d if i.startswith(prefix + "-")]
    return f"{prefix}-{max(nums or [0]) + 1}"


def new_id_for(root: str, head: str, old: str, handed: set[str]) -> str:
    """A fresh id for `old`, never one already handed out in this call: from the id ledger (scripts/claim_id.py, the
    asd/ids branch /plan claims from) for W ids, else, or when the ledger cannot be reached, the next free id on origin
    above everything handed out so far."""
    prefix = old.split("-")[0]
    if prefix == "W":
        try:
            sys.path.insert(0, HERE)
            import claim_id
            for _ in range(5):
                new = claim_id.claim(root, "origin", "W", f"{head} (renumbered from {old})")
                if new not in handed:
                    handed.add(new)
                    return new
        except Exception:   # the ledger is unreachable: fall back to scanning origin
            pass
    new = next_free_id(root, prefix)
    while new in handed:
        new = f"{prefix}-{int(new.split('-')[1]) + 1}"
    handed.add(new)
    return new


def remap_ids(text: str, idmap: dict[str, str]) -> str:
    """Replace every old id by its new one in one pass, so a chain (A to B, B to C) cannot be applied twice."""
    if not idmap:
        return text
    alt = "|".join(re.escape(o) for o in sorted(idmap, key=len, reverse=True))
    return re.sub(rf"(?<![A-Za-z0-9])(?:{alt})(?![0-9])", lambda m: idmap[m.group(0)], text)


def fix_ids(root: str, base: str, head: str, renumber: list[tuple[str, str]], adr: bool) -> str:
    """Renumber colliding ids on the branch, mechanically; "" or why it could not."""
    wt = os.path.join(tempfile.mkdtemp(prefix="asd-ids-"), "w")
    # The branch may have moved on GitHub (an update from its base): build on what is there, or the push is refused.
    rc, out = git(root, "fetch", "-q", "origin", head, base)
    if rc != 0:
        return "could not fetch " + head + ": " + out[:160]
    rc, out = git(root, "worktree", "add", "-q", "--detach", wt, f"origin/{head}")
    if rc != 0:
        return "could not check out " + head + ": " + out[:160]
    try:
        rc, out = git(wt, "merge", "--no-edit", "--no-ff", f"origin/{base}")
        if rc != 0:
            git(wt, "merge", "--abort")
            return "conflict"   # a content conflict: the agent's step
        notes = []
        # Every new id first: from the central ledger (the same one /plan claims from), else the highest id on origin plus
        # the ones this call already handed out. Then one simultaneous rewrite of the files the branch adds or changes.
        handed: set[str] = set()
        idmap: dict[str, str] = {}
        for old, f in renumber:
            idmap[old] = new_id_for(root, head, old, handed)
        rc, changed = git(wt, "diff", "--name-only", "--diff-filter=AMR", f"origin/{base}", "HEAD")
        files = [l for l in changed.splitlines() if ID_FILE_RE.match(l)]
        renamed = {}
        for old, f in renumber:
            nf = f.replace(f"/{old}-", f"/{idmap[old]}-", 1)
            git(wt, "mv", f, nf)
            renamed[f] = nf
        for f in files:
            nf = renamed.get(f, f)
            path = os.path.join(wt, nf)
            if not os.path.isfile(path):
                continue
            text = open(path, encoding="utf-8").read()
            out_text = remap_ids(text, idmap)
            if out_text != text:
                open(path, "w", encoding="utf-8").write(out_text)
            git(wt, "add", nf)
        notes = [f"{o} -> {n}" for o, n in idmap.items()]
        if adr:
            vp = os.path.join(HERE, "validate_pipeline.py")
            r = subprocess.run([sys.executable, vp, "--finalize-ids", "--base", f"origin/{base}"], cwd=wt, capture_output=True, text=True)
            if r.returncode != 0:
                return "renumbering ADRs failed: " + (r.stdout + r.stderr).strip()[-200:]
            git(wt, "add", "-A")
            notes.append("ADR numbers finalized")
        rc, _ = git(wt, "diff", "--cached", "--quiet")
        if rc != 0:
            rc, out = git(wt, "commit", "-q", "-m", "renumber ids the base already holds: " + ", ".join(notes))
            if rc != 0:
                return "commit failed: " + out[-200:]
        rc, out = git(wt, "push", "-q", "origin", f"HEAD:{head}")
        return "" if rc == 0 else "push failed: " + out[-200:]
    finally:
        git(root, "worktree", "remove", "--force", wt)


def stack_clash(root: str, base: str, head: str) -> bool:
    """Whether the top of a stack (`head`) conflicts with `base` when the stack merges. GitHub reports each PR of a stack
    only against the PR below it, so a top that clashes with a stub that moved after the bottom took it in reads CLEAN
    everywhere, and only `gh stack merge` finds it ("Merge conflict detected", nothing merged)."""
    git(root, "fetch", "-q", "origin", base, head)
    rc, out = git(root, "merge-tree", "--write-tree", "--name-only", f"origin/{base}", f"origin/{head}")
    # A conflict is exit 1 with the tree id first and the conflicted paths after it; anything else (a ref not on origin, an
    # old git) is no evidence, and gh stack merge keeps the last word.
    lines = out.split("\n")
    return rc == 1 and bool(re.fullmatch(r"[0-9a-f]{40,64}", lines[0].strip())) and len([x for x in lines[1:] if x.strip()]) > 0


def merge_base_in(root: str, head: str, base: str) -> str:
    """Merge the base into the branch and push; "" or why not. A conflict is left for the next round to find."""
    wt = os.path.join(tempfile.mkdtemp(prefix="asd-update-"), "w")
    rc, out = git(root, "fetch", "-q", "origin", head, base)
    if rc != 0:
        return "fetch failed: " + out[:160]
    rc, out = git(root, "worktree", "add", "-q", "--detach", wt, f"origin/{head}")
    if rc != 0:
        return "could not check out " + head + ": " + out[:160]
    try:
        rc, out = git(wt, "merge", "--no-edit", "--no-ff", f"origin/{base}")
        if rc != 0:
            git(wt, "merge", "--abort")
            return "conflict"
        rc, out = git(wt, "push", "-q", "origin", f"HEAD:{head}")
        return "" if rc == 0 else "push failed: " + out[-160:]
    finally:
        git(root, "worktree", "remove", "--force", wt)


def checks_state(root: str, n: int) -> str:
    """green | pending | red, from the PR's checks."""
    rc, out = gh(root, "pr", "checks", str(n), "--json", "bucket", "-q", "[.[].bucket] | join(\",\")")
    if rc not in (0, 1, 8):
        return "pending"
    b = out.split(",") if out else []
    return "red" if any(x in ("fail", "cancel") for x in b) else "pending" if "pending" in b else "green"


def rerun_failed(root: str, head: str) -> bool:
    """Re-run the failed jobs of the branch's newest runs, once; whether any was re-run."""
    rc, out = gh(root, "run", "list", "--branch", head, "--limit", "20", "--json", "databaseId,conclusion,headSha")
    try:
        runs = json.loads(out or "[]") if rc == 0 else []
    except ValueError:
        runs = []
    if not runs:
        return False
    tip = runs[0].get("headSha")
    did = False
    for r in runs:
        if r.get("headSha") == tip and r.get("conclusion") in ("failure", "cancelled", "timed_out"):
            did = gh(root, "run", "rerun", str(r["databaseId"]), "--failed")[0] == 0 or did
    return did


def wait_checks(root: str, nums: list[int], timeout_s: float) -> dict[int, str]:
    poll = float(os.environ.get("ASD_MERGE_POLL") or 15)
    end = time.time() + timeout_s
    waited = NET_WAITED
    while True:
        st = {n: checks_state(root, n) for n in nums}
        end += NET_WAITED - waited
        waited = NET_WAITED
        if "pending" not in st.values() or time.time() > end:
            return st
        time.sleep(poll)


AGENT_ATTEMPTS = 2   # /resolve-merge runs one PR may spend in one settle: once per PR head, two at most; then the owner is asked


def head_of(root: str, n: int) -> str:
    """The PR's current head commit ("" when unknown): a resolve run that pushed changed it."""
    rc, out = gh(root, "pr", "view", str(n), "--json", "headRefOid", "-q", ".headRefOid")
    return out.strip() if rc == 0 else ""


def load_attempts(key: str, heads: dict[int, str]) -> tuple[dict[int, int], dict[int, set], dict[int, set]]:
    """The resolve-merge attempts this stack already spent, read back from `<key>.merge.json` (`resolve_attempts`), so a
    worker restart does not hand a red stack fresh ones. Per PR: {count, tried (heads a run ended done on), heads (every
    head the queue started or saw a resolve-merge end on)}. A PR whose head is not among its known heads moved for some
    other reason (the owner's push, the dossier's own run, a base update): its attempts are reset."""
    saved = (read_json(os.path.join(runs_dir(), key + ".merge.json"), {}) or {}).get("resolve_attempts") or {}
    attempts: dict[int, int] = {}; tried: dict[int, set] = {}; known: dict[int, set] = {}
    for n, h in heads.items():
        e = saved.get(str(n)) or {}
        if h and h in (e.get("heads") or []):
            attempts[n] = int(e.get("count") or 0); tried[n] = set(e.get("tried") or []); known[n] = set(e.get("heads") or [])
    return attempts, tried, known


def save_attempts(key: str, attempts: dict[int, int], tried: dict[int, set], known: dict[int, set]) -> None:
    set_state(key, resolve_attempts={str(n): {"count": attempts.get(n, 0), "tried": sorted(h for h in tried.get(n, set()) if h), "heads": sorted(h for h in known.get(n, set()) if h)}
                                     for n in set(attempts) | set(known)})


def ask_resolve_failed(root: str, dossier: str, why: str) -> None:
    """The one place the queue asks the owner about a conflict: /resolve-merge itself failed twice. Best effort."""
    ask = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ask_owner.py")
    try:
        subprocess.run([sys.executable, ask, "ask", "--root", root, "--dossier", dossier, "--question",
                        f"How should the merge queue go on with {dossier}? It could not bring the dossier's pull requests up to date: {why}. The dossier's agent tried twice.",
                        "--option", "A|Resolve by hand|You resolve the conflict on the branch; the queue merges it on the next press of Merge",
                        "--option", "B|Leave it|The pull requests stay open and conflicting", "--recommend", "A",
                        "--blocks", "the merge of " + dossier], capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        pass


def wait_live(key, since: str, timeout_s: float) -> str:
    """`wait_run`, but a run that is still alive when its working time is up is waited for on: waiting is not failing.
    A hung run is the runner's own stall clock's to end; "timeout" is only for a run that is gone."""
    keys = [key] if isinstance(key, str) else list(key)
    while True:
        end = wait_run(keys, since, timeout_s)
        if end != "timeout" or not any(run_live(k) for k in keys):
            return end


def settle(root: str, key: str, agent_id: str, prs: list[dict], base: str, others: list[str], note, use_agent: bool = True) -> str:
    """Make the PRs mergeable and green, cheapest step first; "" when ready to merge, else why not. Without use_agent
    it stops before waking an agent: a batch that stays red goes back to one PR at a time, where the blame is clear."""
    nums = [p["number"] for p in prs]
    run_timeout = float(os.environ.get("ASD_MERGE_RUN_TIMEOUT") or 4 * 3600)
    check_timeout = float(os.environ.get("ASD_MERGE_CHECK_TIMEOUT") or 3600)
    # per PR: resolve-merge runs started, the heads a run ended done on, every head the queue knows as its own. Kept in
    # the stack's merge.json (load_attempts) so they outlive this settle and a worker restart.
    attempts, tried, known = load_attempts(key, {n: head_of(root, n) for n in nums})
    rerun_used = False
    updates = 0
    link_conflicts: list[int] = []

    def link_chain() -> tuple[bool, str]:
        """Each PR of a stack holds the one below it, or `gh stack merge` refuses the stack: updating the bottom from the
        stub is not enough. (merged any in, why it could not); a conflict goes to the dossier's agent."""
        del link_conflicts[:]
        moved = False
        for lower, upper in zip(prs, prs[1:]):
            git(root, "fetch", "-q", "origin", lower["headRefName"], upper["headRefName"])
            if any(git(root, "rev-parse", "--verify", "-q", f"origin/{x['headRefName']}")[0] != 0 for x in (lower, upper)):
                continue   # not on origin: nothing to compare, and gh stack merge has the last word
            if git(root, "merge-base", "--is-ancestor", f"origin/{lower['headRefName']}", f"origin/{upper['headRefName']}")[0] == 0:
                continue
            note("fixing", f"updating #{upper['number']} with #{lower['number']}, the pull request below it")
            why = merge_base_in(root, upper["headRefName"], lower["headRefName"])
            if why == "conflict":
                link_conflicts.append(upper["number"])
            elif why:
                return False, f"could not update #{upper['number']} with #{lower['number']}: {why}"
            else:
                moved = True
        return moved, ""

    def top_clashes() -> bool:
        """A stack whose top conflicts with the base goes to the dossier's agent, every PR of it, bottom first."""
        if len(prs) < 2 or not stack_clash(root, prs[0]["baseRefName"], prs[-1]["headRefName"]):
            return False
        note("resolving", f"#{prs[-1]['number']}, the top of the stack, conflicts with {prs[0]['baseRefName']} when the stack merges")
        return True

    def update_behind() -> tuple[bool, str]:
        """(updated any, why it could not). A conflict from the update comes back as CONFLICTING on the next round."""
        nonlocal updates
        stale = behind(root, nums)
        if not stale:
            return False, ""
        updates += 1
        if updates > 5:
            return False, "still behind its base after 5 updates: " + ", ".join(f"#{n}" for n in stale)
        note("fixing", "updating " + ", ".join(f"#{n}" for n in stale) + " with the latest " + base)
        for n in stale:
            rc, out = gh(root, "pr", "update-branch", str(n), timeout=300)
            if rc != 0 and "conflict" not in out.lower():
                # GitHub can refuse this on a stacked PR: the same merge, by hand.
                p = next(x for x in prs if x["number"] == n)
                why = merge_base_in(root, p["headRefName"], p["baseRefName"])
                if why and why != "conflict":   # a conflict comes back as CONFLICTING on the next round
                    return False, f"could not update #{n}: {out[-120:]}; {why}"
        time.sleep(float(os.environ.get("ASD_MERGE_POLL") or 15))   # let GitHub see the push and start the checks
        return True, ""

    while True:
        # 2. ids, mechanical (the bottom PR carries the dossier; stacked ones are fixes)
        bottom = prs[0]
        ren, adr = collisions(root, bottom["baseRefName"], bottom["headRefName"], others)
        if ren or adr:
            note("fixing", "renumbering " + ", ".join(i for i, _ in ren) + (" and ADR numbers" if adr else "") + " the base already holds")
            why = fix_ids(root, bottom["baseRefName"], bottom["headRefName"], ren, adr)
            if why and why != "conflict":
                return why
            if not why:
                time.sleep(float(os.environ.get("ASD_MERGE_POLL") or 15))   # let GitHub see the push
        # 1. mergeability
        bad, err = conflicting(root, nums)
        if err:
            return err
        if not bad and top_clashes():
            bad = list(nums)
        # A clash is the stack's to fix, so no check is worth waiting for until it is gone.
        clashes = migration_clashes(root, prs[0]["baseRefName"], prs[-1]["headRefName"]) if not bad else []
        # 3. up to date with the base, and each PR of a stack holding the one below it, before spending a wait on checks
        # that would have to run again
        if not bad and not clashes:
            updated, why = update_behind()
            if why:
                return why
            if updated:
                continue
            linked, why = link_chain()
            if why:
                return why
            if linked:
                time.sleep(float(os.environ.get("ASD_MERGE_POLL") or 15))
                continue
            bad = bad + link_conflicts
        # 3. checks (re-run red ones once)
        st = wait_checks(root, nums, check_timeout) if not bad and not clashes else {}
        red = [n for n, v in st.items() if v == "red"]
        if red and not rerun_used:
            rerun_used = True
            heads = [p["headRefName"] for p in prs if p["number"] in red]
            if any(rerun_failed(root, h) for h in heads):
                note("rerunning", "checks red on " + ", ".join(f"#{n}" for n in red) + "; re-running the failed jobs once")
                time.sleep(float(os.environ.get("ASD_MERGE_POLL") or 15))
                st = wait_checks(root, nums, check_timeout)
                red = [n for n, v in st.items() if v == "red"]
        pending = [n for n, v in st.items() if v == "pending"]
        if not bad and not clashes and not red and not pending:
            updated, why = update_behind()   # a merge from outside the queue can land while the checks ran
            if why:
                return why
            if updated:
                continue
            linked, why = link_chain()
            if why:
                return why
            if linked:
                continue
            if link_conflicts:
                bad = list(link_conflicts)
            elif top_clashes():   # the base moved while the checks ran
                bad = list(nums)
            else:
                if attempts:
                    set_state(key, resolve_attempts=None)   # settled: the next red stack starts with fresh attempts
                return ""
        if pending and not bad and not clashes and not red:
            return "checks still running after " + str(int(check_timeout / 60)) + " min: " + ", ".join(f"#{n}" for n in pending)
        # 4. the dossier's agent
        if not use_agent:
            return ("conflicting: " + ", ".join(f"#{n}" for n in bad)) if bad else ("migration numbers clash: " + "; ".join(clashes)) if clashes else ("checks red: " + ", ".join(f"#{n}" for n in red))
        rkey = f"{repo_name(root)}.{agent_id}"
        qk = resolve_key(rkey, nums)   # the key run_dossier gives the run this settle starts
        # The dossier's own failed run still holds its runner lock while it waits on its failure question: a resolve-merge
        # started now exits at once ("already has a runner"), and asking the owner a second time would repeat that
        # question. Wait for the question to be answered / the lock to be released; that is no attempt.
        # The queue's resolve-merge has a key of its own (`.resolve-<prs>`), so the dossier's own run does not block it; a
        # run-failure question about the stack's own parent conflict is the queue's to resolve, never to wait on (W-182).
        if open_failure_questions(rkey, conflicts=False) or runner_lock_held(qk) or open_failure_questions(qk, conflicts=False):
            note("resolving", f"{agent_id} has a run holding its lock or an open run-failure question; waiting for it instead of starting or asking again")
            if not wait_runner_free([rkey, qk], run_timeout):
                return f"the runner of {agent_id} still holds the dossier's lock after {int(run_timeout / 60)} min; no resolve-merge attempt was spent"
            continue
        targets = bad or ([n for n in red] if red else list(nums))
        heads = {n: head_of(root, n) for n in targets}
        spent = [n for n in targets if attempts.get(n, 0) >= AGENT_ATTEMPTS]
        unchanged = all(heads[n] and heads[n] in tried.get(n, set()) for n in targets)
        if spent or unchanged:
            rounds = f" after {max(attempts.get(n, 0) for n in targets)} resolve-merge attempt{'s' if max(attempts.get(n, 0) for n in targets) != 1 else ''}" + ("" if spent else "; its head did not change")
            why = ("still conflicting: " + ", ".join(f"#{n}" for n in bad) + rounds) if bad else ("migration numbers still clash: " + "; ".join(clashes) + rounds) if clashes else ("checks still red: " + ", ".join(f"#{n}" for n in red) + rounds)
            ask_resolve_failed(root, agent_id, why)
            return why
        for n in targets:
            attempts[n] = attempts.get(n, 0) + 1
            known.setdefault(n, set()).add(heads[n])
        save_attempts(key, attempts, tried, known)
        rerun_used = False   # the agent's push is a new head: a flaky job there gets its own re-run before it costs an attempt
        what = ("conflicts in " + ", ".join(f"#{n}" for n in bad)) if bad else ("migration numbers clash: " + "; ".join(clashes)) if clashes else ("red checks on " + ", ".join(f"#{n}" for n in red))
        note("resolving", what + "; the dossier's agent resolves it (resolve-merge, attempt " + str(max(attempts[n] for n in targets)) + f" of {AGENT_ATTEMPTS})")
        since = now()
        started = start_run(root, agent_id, "resolve-merge", nums)
        if "already has a runner" in started:
            # The lock was taken between the check above and the start: the run never started, so it is not an attempt.
            for n in targets:
                attempts[n] -= 1
            save_attempts(key, attempts, tried, known)
            if not wait_runner_free([rkey, qk], run_timeout):
                return f"the runner of {agent_id} still holds the dossier's lock after {int(run_timeout / 60)} min; no resolve-merge attempt was spent"
            continue
        # a live run of this dossier older than this start (its own work, or an earlier resolve-merge of these PRs): adopted
        live_key = next((k for k in (qk, rkey) if run_live(k) and str((read_json(os.path.join(runs_dir(), k + ".runner.json"), {}) or {}).get("started") or "") < since), "")
        live = (read_json(os.path.join(runs_dir(), live_key + ".runner.json"), {}) or {}) if live_key else {}
        adopted = bool(live_key)
        if adopted:
            # The runner refused (or ignored) the start because a run of this dossier is still live. Wait for that run by its
            # own start, not for a new one that never comes; it is not a new attempt.
            for n in targets:
                attempts[n] -= 1
            note("resolving", f"a run of {agent_id} ({live.get('command') or 'run'}) is still live; waiting for it instead of starting another")
            end = wait_live(live_key, str(live.get("started") or ""), run_timeout)
            if end == "done":
                if live.get("command") == "resolve-merge":
                    for n in targets:
                        tried.setdefault(n, set()).add(heads[n])
                for n in targets:
                    known.setdefault(n, set()).add(head_of(root, n))
                save_attempts(key, attempts, tried, known)
                continue   # the run ended done: look at the stack again straight away
            if end == "timeout":
                for n in targets:
                    attempts[n] += 1   # its own time ran out: that is a spent attempt after all
            save_attempts(key, attempts, tried, known)
            if end != "timeout":
                continue
        else:
            end = wait_live([qk, rkey], since, run_timeout)
        for n in targets:
            known.setdefault(n, set()).add(head_of(root, n))   # a push of the agent is the queue's own head
        if end == "done":
            for n in targets:
                tried.setdefault(n, set()).add(heads[n])
        save_attempts(key, attempts, tried, known)
        if end != "done" and all(attempts[n] >= AGENT_ATTEMPTS for n in targets):
            why = f"resolve-merge ended {end} twice"
            ask_resolve_failed(root, agent_id, why)
            return why


def work_one(root: str, dossier: str, ahead=None) -> str:
    """Merge one dossier's stack. `ahead` (a guard over the chain, see `ahead_guard`) means the worker is not at this item yet: its head
    waits on an agent. Then nothing is waited for and no agent woken: a stack that is clean, up to date and green now is merged,
    anything else is "skip" and untouched. Returns "merged", "failed" or "skip" there."""
    repo = repo_name(root); key = f"{repo}.{dossier}"
    run_timeout = float(os.environ.get("ASD_MERGE_RUN_TIMEOUT") or 4 * 3600)
    if run_live(key) and ahead is not None:
        return "skip"
    if run_live(key) or resolve_runs_live(key):
        set_state(key, state="waiting", error="", pid=os.getpid(), note="waiting for the dossier's own run to finish")
        while run_live(key) or resolve_runs_live(key):
            idle_tick()
            time.sleep(float(os.environ.get("ASD_MERGE_POLL") or 15))
    # A worker killed around the merge (a logout, a reboot) comes back to PRs GitHub has already merged. The merge
    # detector starts their /after-merge, so all that is left here is to say so.
    prior = read_json(os.path.join(runs_dir(), key + ".merge.json"), {}) or {}
    if prior.get("prs") and prior.get("state") in ("merging", "cleanup"):
        merged = [gh(root, "pr", "view", str(n), "--json", "state", "-q", ".state") for n in prior["prs"]]
        if all(rc == 0 and st.strip() == "MERGED" for rc, st in merged):
            set_state(key, state="merged", error="", pid=None, note="merged; found so after the merge queue restarted")
            return "merged"
    chain, why = plan(root, dossier)
    while not chain and ahead is None and "is still live" in why:
        # a run started between the wait above and the check: waiting is not failing
        set_state(key, state="waiting", error="", pid=os.getpid(), note="waiting: " + why)
        idle_tick()
        time.sleep(float(os.environ.get("ASD_MERGE_POLL") or 15))
        chain, why = plan(root, dossier)
    if not chain:
        if ahead is not None:
            return "skip"
        set_state(key, state="failed", error=why, pid=None); return "failed"
    nums = [p["number"] for p in chain]
    if ahead is not None:
        # Only what is mergeable right now: no waiting (the head's wait is going on), no agent, nothing that stacks on an item still ahead.
        if chain[0]["baseRefName"] == "development" or not ahead(chain):
            return "skip"
        bad, err = conflicting(root, nums)
        if bad or err:
            return "skip"
        stale = behind(root, nums)
        if stale:   # an earlier merge put it behind: bring it up to date now, its checks run meanwhile, a later pass merges it
            for n in stale:
                gh(root, "pr", "update-branch", str(n), timeout=300)
            return "skip"
        if any(checks_state(root, n) != "green" for n in nums):
            return "skip"
    def note(state, text):
        if ahead is not None:
            return
        set_state(key, state=state, prs=nums, error="", pid=os.getpid(), note=text)
        append_line(os.path.join(runs_dir(), key + ".jsonl"), {"ts": now(), "kind": "runner", "text": "merge queue: " + text})
    agent = dossier_id(root, dossier, chain)   # the dossier's own id: its agent resolves, its timeline gets the cleanup
    if chain[0]["baseRefName"] == "development":   # only a kanban dossier gets here (plan)
        why = wait_development(root, note)
        if why:
            set_state(key, state="failed", prs=nums, error=why, pid=None); return "failed"
    why = settle(root, key, agent, chain, chain[0]["baseRefName"], [], note, use_agent=ahead is None)
    if why:
        if ahead is not None:
            return "skip"
        set_state(key, state="failed", prs=nums, error=why, pid=None); return "failed"
    set_state(key, state="merging", pid=os.getpid())
    import io, contextlib
    buf = io.StringIO()
    for _ in range(100):
        with contextlib.redirect_stdout(buf):
            rc = merge(root, dossier, "merge", False, no_cleanup=True, by=(read_json(os.path.join(runs_dir(), key + ".merge.json"), {}) or {}).get("by") or "button")
        # a run started after the check above makes the merge refuse "still live": that is a wait, not a failure
        if rc != 0 and "is still live" in str((read_json(os.path.join(runs_dir(), key + ".merge.json"), {}) or {}).get("error") or ""):
            set_state(key, state="waiting", error="", pid=os.getpid(), note="waiting for a run of the dossier to finish")
            while run_live(key) or resolve_runs_live(key):
                idle_tick()
                time.sleep(float(os.environ.get("ASD_MERGE_POLL") or 15))
            continue
        break
    if rc != 0:
        return "failed"  # merge() recorded "failed" and why
    st = read_json(os.path.join(runs_dir(), key + ".merge.json"), {}) or {}
    nums = st.get("prs") or []
    # /after-merge runs beside the queue: it never pushes to the stub, and a question it asks must not hold up the next merge.
    start_run(root, agent, "after-merge", nums)
    set_state(key, state="merged", pid=None, note="merged; /after-merge runs")
    return "merged"


DOC_PATH_RE = re.compile(r"^(\.discovery/|docs/)|\.md$")


def enqueue_pr(root: str, n: int) -> int:
    """Add a non-code PR into development (a plan or research PR) to the repository's merge queue, once."""
    repo = repo_name(root)
    with locked(repo):
        q = read_json(queue_path(repo), {}) or {}
        items = q.get("items") or []
        fresh = not any(i.get("pr") == n for i in items)
        if fresh:
            items.append({"id": f"#{n}", "pr": n, "ts": now()})
        q["items"], q["root"] = items, root
        write_json(queue_path(repo), q)
        if fresh:   # one already in the queue keeps the state it is in (pressing again must not hide the work under way)
            set_state(f"{repo}.pr-{n}", state="queued", position=len(items), error="", pid=None)
        worker_ = q.get("worker")
        if not (worker_ and alive(worker_)):
            log = os.path.join(runs_dir(), f"{repo}.merge-queue.log")
            with open(log, "ab") as fh:
                p = subprocess.Popen([sys.executable, os.path.abspath(__file__), "--worker", "--root", root], stdin=subprocess.DEVNULL,
                                     stdout=fh, stderr=subprocess.STDOUT, start_new_session=True)
            q["worker"] = p.pid
            write_json(queue_path(repo), q)
    return 0


def mergeable_doc_pr(root: str, n: int) -> dict:
    """The non-code PR's row, or {} after recording why the queue refuses it."""
    key = f"{repo_name(root)}.pr-{n}"
    rc, out = gh(root, "pr", "view", str(n), "--json", "number,state,baseRefName,headRefName,isDraft,files")
    try:
        p = json.loads(out) if rc == 0 else {}
    except ValueError:
        p = {}
    if p and p.get("state") == "MERGED":   # it landed before an interruption (a killed worker, a batch that got through)
        set_state(key, state="merged", error="", pid=None, note="merged into " + str(p.get("baseRefName"))); return {}
    if not p or p.get("state") != "OPEN":
        set_state(key, state="failed", error="not an open pull request", pid=None); return {}
    if p.get("isDraft"):
        set_state(key, state="failed", error="still a draft: mark it ready for review first", pid=None); return {}
    code = [f for f in (x.get("path", "") for x in p.get("files") or []) if not DOC_PATH_RE.search(f)]
    if code:
        set_state(key, state="failed", error="changes code (" + ", ".join(code[:3]) + "): code reaches development only through a stub; retarget it to the stub, or drop those files", pid=None); return {}
    return p


# ---------------------------------------------------------------- a batch of non-code PRs, one CI run
# A strict branch rule makes every merge put the rest behind, so N PRs one at a time cost N CI runs. Waiting non-code
# PRs are merged together on one plan/queue-batch-* branch, checked once and merged through one PR; GitHub then marks
# each member merged, because its head is in the base. Anything out of the ordinary (a member that conflicts, a batch
# that stays red) goes back to one PR at a time, where the blame is clear. ASD_MERGE_BATCH=0 turns batching off.
BATCH_PREFIX = "plan/queue-batch-"


def work_batch(root: str, nums: list[int]) -> list[int]:
    """Merge the PRs through one CI run; returns those the queue is finished with (merged or refused)."""
    repo = repo_name(root)
    done: list[int] = []
    rows: list[dict] = []
    for n in nums:
        p = mergeable_doc_pr(root, n)
        if p and (not rows or p["baseRefName"] == rows[0]["baseRefName"]):
            rows.append(p)
        elif not p:
            done.append(n)
    if len(rows) < 2:
        return done
    base = rows[0]["baseRefName"]
    label = ", ".join(f"#{p['number']}" for p in rows)
    def state_all(ps, **kw):
        for x in ps:
            set_state(f"{repo}.pr-{x['number']}", **kw)
    state_all(rows, state="fixing", error="", pid=os.getpid(), note="renumbering any id the batch holds twice")
    git(root, "fetch", "-q", "origin", "--prune")
    kept: list[dict] = []
    for p in rows:   # the PR earlier in the queue keeps its id
        ren, adr = collisions(root, base, p["headRefName"], [x["headRefName"] for x in kept])
        if (ren or adr) and fix_ids(root, base, p["headRefName"], ren, adr):
            continue   # one at a time, afterwards
        git(root, "fetch", "-q", "origin", p["headRefName"])
        kept.append(p)
    wt = os.path.join(tempfile.mkdtemp(prefix="asd-batch-"), "w")
    rc, out = git(root, "worktree", "add", "-q", "--detach", wt, f"origin/{base}")
    if rc != 0:
        return done
    branch = f"{BATCH_PREFIX}{rows[0]['number']}-{rows[-1]['number']}"
    members: list[dict] = []
    try:
        for p in kept:
            rc, sha = git(root, "rev-parse", f"origin/{p['headRefName']}")
            rc2, _ = git(wt, "merge", "--no-edit", "--no-ff", f"origin/{p['headRefName']}")
            if rc != 0 or rc2 != 0:
                git(wt, "merge", "--abort")
                continue
            members.append({**p, "sha": sha.strip()})
        if len(members) < 2:
            return done
        rc, out = git(wt, "push", "-q", "--force", "origin", f"HEAD:refs/heads/{branch}")
        if rc != 0:
            return done
    finally:
        git(root, "worktree", "remove", "--force", wt)
    label = ", ".join(f"#{p['number']}" for p in members)
    rc, out = gh(root, "pr", "create", "--base", base, "--head", branch, "--title", f"plans: batch of {label}",
                 "--body", f"The merge queue checks and merges {label} together, in one CI run. Each of them reads as merged once this lands.")
    m = re.search(r"/pull/(\d+)", out) if rc == 0 else None
    if not m:   # the worker was interrupted after opening it: carry on with the same PR
        rc, out = gh(root, "pr", "view", branch, "--json", "number", "-q", r'"/pull/\(.number)"')
        m = re.search(r"/pull/(\d+)", out) if rc == 0 else None
    if not m:
        git(root, "push", "-q", "origin", "--delete", branch)
        return done
    num = int(m.group(1))
    def note(state, text):
        state_all(members, state="batching", batch=num, error="", pid=os.getpid(), note=f"in batch #{num}: {text}")
    def give_up(why: str) -> list[int]:
        gh(root, "pr", "close", str(num), "--delete-branch", "--comment", f"Back to one at a time: {why}")
        state_all(members, state="queued", batch=None, pid=None, note=f"batch #{num} did not land ({why}); one at a time now")
        return done
    note("batching", "waiting for its checks")
    row = {"number": num, "headRefName": branch, "baseRefName": base}
    for _ in range(3):
        why = settle(root, f"{repo}.pr-{num}", f"PR-{num}", [row], base, [], note, use_agent=False)
        if why:
            return give_up(why)
        rc, out = gh(root, "pr", "merge", str(num), "--merge", "--delete-branch", timeout=300)
        if not (rc != 0 and "not up to date" in out):
            break
    rc2, st = gh(root, "pr", "view", str(num), "--json", "state", "-q", ".state")
    if st.strip() != "MERGED":
        return give_up(out[-200:] if rc != 0 else f"not merged afterwards ({st.strip()})")
    # GitHub marks a member merged by itself, a moment later; one it does not is closed saying where its work went.
    for p in members:
        key = f"{repo}.pr-{p['number']}"
        state = ""
        for _ in range(int(os.environ.get("ASD_MERGE_MERGEABLE_TRIES") or 12)):
            _, state = gh(root, "pr", "view", str(p["number"]), "--json", "state", "-q", ".state")
            if state.strip() == "MERGED":
                break
            time.sleep(float(os.environ.get("ASD_MERGE_POLL") or 15) / 3)
        if state.strip() != "MERGED":
            _, head = gh(root, "pr", "view", str(p["number"]), "--json", "headRefOid", "-q", ".headRefOid")
            if head.strip() and p["sha"] and head.strip() != p["sha"]:   # pushed to during the batch: that push is not in
                set_state(key, state="failed", pid=None, error=f"its branch changed while batch #{num} ran; the new commits are not merged")
                done.append(p["number"])
                continue
            gh(root, "pr", "close", str(p["number"]), "--comment", f"Merged into {base} through #{num}.")
        set_state(key, state="merged", pid=None, error="", note=f"merged into {base} through batch #{num}")
        done.append(p["number"])
    return done


def work_pr(root: str, n: int) -> None:
    """One non-code PR into development through the shared steps, then merged by script (no cleanup run)."""
    repo = repo_name(root); key = f"{repo}.pr-{n}"
    p = mergeable_doc_pr(root, n)
    if not p:
        return
    files = [f.get("path", "") for f in p.get("files") or []]
    ids = [m.group(1) for f in files if (m := ID_FILE_RE.match(f))]
    agent_id = ids[0] if ids else f"PR-{n}"
    def note(state, text):
        set_state(key, state=state, error="", pid=os.getpid(), note=text)
        if ids:
            append_line(os.path.join(runs_dir(), f"{repo}.{agent_id}.jsonl"), {"ts": now(), "kind": "runner", "text": f"merge queue (#{n}): " + text})
    for _ in range(3):
        why = settle(root, key, agent_id, [p], p["baseRefName"], [], note)
        if why:
            set_state(key, state="failed", error=why, pid=None); return
        set_state(key, state="merging", pid=os.getpid())
        rc, out = gh(root, "pr", "merge", str(n), "--merge", timeout=300)
        if not (rc != 0 and "not up to date" in out):   # another merge won the race: settle again
            break
    rc2, st = gh(root, "pr", "view", str(n), "--json", "state", "-q", ".state")
    if st.strip() != "MERGED":   # the state decides: a merge retried after a blip can report an error and still be in
        set_state(key, state="failed", error=(out[-300:] if rc != 0 else f"not merged afterwards ({st.strip()})"), pid=None); return
    set_state(key, state="merged", pid=None, note="merged into " + p["baseRefName"])


# ---------------------------------------------------------------- going on behind a head that waits on an agent
# One conflicting dossier must not hold ten clean ones: while the head waits on its agent (a resolve-merge run, or the
# dossier's own run) the worker merges the later dossiers that are mergeable right now, without an agent and without
# waiting. The head keeps position 1 and is retried when its run ends; an item that needs an agent too (or waits for
# checks) stays where it is until its turn. Only dossiers go ahead: a non-code PR (batched, one CI run) keeps its order.
_AHEAD = {"at": 0.0}


def ahead_guard(root: str, earlier: list[dict]):
    """A guard over a candidate's chain: False when it stacks on, or shares a PR with, an item still ahead of it in the queue
    (a PR is never merged before the PR it is based on), or when that cannot be told."""
    rc, out = gh(root, "pr", "list", "--state", "open", "--limit", "200", "--json", "number,headRefName,baseRefName,isDraft,title,url")
    try:
        prs = json.loads(out or "[]") if rc == 0 else None
    except ValueError:
        prs = None
    repo = repo_name(root)
    nums: set[int] = set()
    for e in earlier:
        if e.get("pr"):
            nums.add(int(e["pr"]))
            continue
        nums.update(int(n) for n in (read_json(os.path.join(runs_dir(), f"{repo}.{e['id']}.merge.json"), {}) or {}).get("prs") or [])
        if prs is not None:
            nums.update(p["number"] for p in chain_of(prs, e["id"], dossier_prs(root))[0])
    heads = {p["headRefName"] for p in prs or [] if p["number"] in nums}

    def guard(chain: list[dict]) -> bool:
        if prs is None:
            return False
        return not ({p["number"] for p in chain} & nums) and not any(p["baseRefName"] in heads for p in chain)
    return guard


def advance(root: str, head: str) -> list[str]:
    """One pass over the dossiers behind `head` while it waits: each that merges now is merged and leaves the queue. The merged ids."""
    if os.environ.get("ASD_MERGE_AHEAD") == "0":
        return []
    t = time.time()
    if t - _AHEAD["at"] < float(os.environ.get("ASD_MERGE_AHEAD_EVERY") or 60):
        return []
    _AHEAD["at"] = t
    repo = repo_name(root)
    merged: list[str] = []
    with locked(repo):
        items = list((read_json(queue_path(repo), {}) or {}).get("items") or [])
    ids = [i["id"] for i in items]
    if head not in ids:
        return []
    for idx in range(ids.index(head) + 1, len(items)):
        it = items[idx]
        if it.get("pr"):
            continue
        earlier = [e for e in items[:idx] if e["id"] not in merged]
        key = f"{repo}.{it['id']}"
        before = read_json(os.path.join(runs_dir(), key + ".merge.json"), {}) or {}
        try:
            res = work_one(root, it["id"], ahead_guard(root, earlier))
        except Exception:
            res = "skip"
        if res == "skip":
            if before:   # whatever a look at it touched: it is still just waiting its turn
                write_json(os.path.join(runs_dir(), key + ".merge.json"), before)
            continue
        merged.append(it["id"])
        with locked(repo):   # merged or failed, as when the worker reaches it: out of the line, the rest move up
            q = read_json(queue_path(repo), {}) or {}
            q["items"] = [i for i in (q.get("items") or []) if i["id"] != it["id"]]
            write_json(queue_path(repo), q)
            for n, i in enumerate(q["items"]):
                if n:
                    set_state(f"{repo}.pr-{i['pr']}" if i.get("pr") else f"{repo}.{i['id']}", position=n + 1)
    return merged


def worker(root: str) -> int:
    """Work the repository's merge queue to the end, one dossier at a time."""
    repo = repo_name(root)
    with locked(repo):
        q = read_json(queue_path(repo), {}) or {}
        if q.get("worker") and q["worker"] != os.getpid() and alive(q["worker"]):
            return 0
        q["worker"] = os.getpid(); write_json(queue_path(repo), q)
    single: set[int] = set()   # tried in a batch already: one at a time now
    while True:
        with locked(repo):
            q = read_json(queue_path(repo), {}) or {}
            items = q.get("items") or []
            for i, it in enumerate(items):   # the rest learn their place
                set_state(f"{repo}.pr-{it['pr']}" if it.get("pr") else f"{repo}.{it['id']}", position=i + 1) if i else None
            if not items:
                q["worker"] = None; write_json(queue_path(repo), q)
                return 0
            head = items[0]["id"]; head_pr = items[0].get("pr")
            # The non-code PRs waiting in a row from the head; a dossier stack keeps its place in the line.
            run: list[int] = []
            for it in items:
                if not it.get("pr"):
                    break
                if int(it["pr"]) not in single:
                    run.append(int(it["pr"]))
        finished = [head]
        global WAIT_HOOK
        _AHEAD["at"] = 0.0
        WAIT_HOOK = lambda h=head, r=q.get("root") or root: advance(r, h)
        try:
            if head_pr and len(run) > 1 and run[0] == int(head_pr) and os.environ.get("ASD_MERGE_BATCH") != "0":
                single.update(run)
                finished = [f"#{n}" for n in work_batch(q.get("root") or root, run)]
            elif head_pr:
                work_pr(q.get("root") or root, int(head_pr))
            else:
                work_one(q.get("root") or root, head)
        except Exception as e:  # one dossier's failure never stops the queue
            if head_pr and len(run) > 1 and int(head_pr) in single and finished == [head] and run[0] == int(head_pr):
                finished = []   # the batch failed: each goes through on its own
            else:
                set_state(f"{repo}.pr-{head_pr}" if head_pr else f"{repo}.{head}", state="failed", error=f"{type(e).__name__}: {e}", pid=None)
        finally:
            WAIT_HOOK = None
        with locked(repo):
            q = read_json(queue_path(repo), {}) or {}
            q["items"] = [i for i in (q.get("items") or []) if i["id"] not in finished]
            write_json(queue_path(repo), q)


def revive() -> int:
    """Start a worker for every merge queue with work and no live worker (killed by a logout, a reboot, a crash)."""
    for path in glob.glob(os.path.join(runs_dir(), "*.merge-queue.json")):
        q = read_json(path, {}) or {}
        root = q.get("root")
        if not (q.get("items") and root and os.path.isdir(root)) or (q.get("worker") and alive(q["worker"])):
            continue
        with open(path[:-5] + ".log", "ab") as fh:
            subprocess.Popen([sys.executable, os.path.abspath(__file__), "--worker", "--root", root], stdin=subprocess.DEVNULL,
                             stdout=fh, stderr=subprocess.STDOUT, start_new_session=True)
        print(f"merge_dossier: restarted the merge queue of {os.path.basename(root)}")
    # An integration whose process died while it waited for the queue: picked up again, so /stub still starts after it.
    for path in glob.glob(os.path.join(runs_dir(), "*.integrate.json")):
        st = read_json(path, {}) or {}
        if st.get("state") == "merging" and st.get("root") and st.get("branch") and os.path.isdir(st["root"]) and not alive(st.get("pid")):
            held = integrate_held(os.path.basename(path)[:-len(".integrate.json")])
            if held:   # an integrate of this stub is running (a press recorded a pid that already exited): never a second one
                if held > 0:
                    write_json(path, {**st, "pid": held})
                continue
            with open(path[:-5] + ".log", "ab") as fh:
                p = subprocess.Popen([sys.executable, os.path.abspath(__file__), "--integrate", st["branch"], "--root", st["root"]], stdin=subprocess.DEVNULL,
                                     stdout=fh, stderr=subprocess.STDOUT, start_new_session=True)
            write_json(path, {**st, "pid": p.pid, "ts": now()})
            print(f"merge_dossier: restarted the integration of {st['branch']}")
    # A stub whose /stub run may be finished, in a repository that opted in to the scripted merge.
    from land_stub import land_all
    land_all()
    return 0


def unqueue(root: str, dossier: str) -> int:
    """Take a dossier out of the queue, unless it is the one being worked on."""
    repo = repo_name(root)
    with locked(repo):
        q = read_json(queue_path(repo), {}) or {}
        items = q.get("items") or []
        if items and items[0]["id"] == dossier and q.get("worker") and alive(q["worker"]):
            print(f"merge_dossier: {dossier} is being merged now; it cannot leave the queue"); return 1
        q["items"] = [i for i in items if i["id"] != dossier]
        write_json(queue_path(repo), q)
        set_state(f"{repo}.{dossier}", state="cancelled", pid=None, error="")
    print(f"merge_dossier: {dossier} left the merge queue")
    return 0


# ---------------------------------------------------------------- integrate a stub
# Part 1: every finished dossier on the stub goes through the merge queue, first in first out (PR number order).
# Part 2: the /stub run on the runner (key <repo>.<stub slug>) proves the batch and opens the stub's PR. Dossiers still
# in flight (a draft PR, or a live run) stay out: their stub closes, and the merge queue moves them to the next stub
# once this one is in development.

def integrate_lock(key: str):
    """(file handle, 0) when this process now holds the stub's integrate lock `<key>.integrate.lock` (held until the handle
    closes or the process ends), else (None, pid of the holder). One integrate per stub: a dashboard press and a revive
    seen 20 min apart ran two on stub/2026-11-08."""
    import fcntl
    os.makedirs(runs_dir(), exist_ok=True)
    fh = open(os.path.join(runs_dir(), key + ".integrate.lock"), "a+")
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fh.seek(0)
        try:
            holder = int(fh.read().strip() or 0)
        except ValueError:
            holder = 0
        fh.close()
        return None, holder
    fh.seek(0); fh.truncate(); fh.write(str(os.getpid())); fh.flush()
    return fh, 0


def integrate_held(key: str) -> int:
    """The pid of the process integrating the stub `key` (0 when none does); the lock is only probed."""
    fh, holder = integrate_lock(key)
    if fh:
        fh.truncate(0); fh.close()
        return 0
    return holder or -1


def stacked_id(everything: list[dict], bottom: dict, owned: dict | None = None, known: set[str] | None = None) -> str | None:
    """The one dossier id the branches stacked on BOTTOM belong to (fix/W-199-deferred on docs/harness2-…), or None when
    they name none or more than one. A PR's `pr:` record (OWNED) wins over its branch name, which counts only for a KNOWN
    dossier id. Plan branches never count."""
    found: set[str] = set()
    heads, seen = {bottom["headRefName"]}, {bottom.get("number")}
    while heads:
        up = [p for p in everything if p.get("baseRefName") in heads and p.get("number") not in seen
              and not p.get("headRefName", "").startswith("plan/")]
        for p in up:
            seen.add(p.get("number"))
            i = pr_dossier(p, owned, known)
            if i:
                found.add(i)
        heads = {p["headRefName"] for p in up}
    return found.pop() if len(found) == 1 else None


def stub_stacks(everything: list[dict], branch: str, owned: dict | None = None, known: set[str] | None = None) -> tuple[list[tuple[str, dict]], list[dict]]:
    """The open PRs straight into the stub, as ([(dossier id, its bottom PR)], [PRs no dossier owns]). Only PRs still open
    count, so a dossier already merged into the stub is not in it. The dossier is the one whose `pr:` records the PR
    (OWNED, from `dossier_prs`); only without a record does the branch name count, and then only a KNOWN dossier id
    (KNOWN, from `dossier_ids`): feat/LABS-138-control-plane is W-009's PR, LABS-138 is no dossier. A PR neither gives
    is the dossier of the PRs stacked on it."""
    prs = sorted([p for p in everything if p.get("baseRefName") == branch and p["headRefName"] != branch], key=lambda p: p["number"])
    ids: list[tuple[str, dict]] = []
    others: list[dict] = []
    for p in prs:
        h = p["headRefName"]
        i = None if h.startswith("plan/") or (h.startswith("docs/") and not (owned or {}).get(p["number"])) else pr_dossier(p, owned, known)
        if not i and not h.startswith("plan/"):
            i = stacked_id(everything, p, owned, known)   # an id-less branch (docs/…) under a dossier's stacked PR is that dossier's
        if not i:
            others.append(p)
            continue
        if i not in [x[0] for x in ids]:
            ids.append((i, p))
    return ids, others


def stub_dossiers(root: str, branch: str) -> list[str]:
    """The dossiers with an open pull request into the stub (the ones eager merging may put on it)."""
    rc, out = gh(root, "pr", "list", "--state", "open", "--limit", "200", "--json", "number,headRefName,baseRefName,isDraft")
    if rc != 0:
        return []
    return [i for i, _ in stub_stacks(json.loads(out or "[]"), branch, dossier_prs(root), dossier_ids(root))[0]]
def kanban_dossiers(root: str) -> list[str]:
    """The kanban dossiers with an open pull request into development (the ones eager merging may queue)."""
    return [i for i in stub_dossiers(root, "development") if i in kanban_ids(root)]


def integrate(root: str, branch: str) -> int:
    repo = repo_name(root); slug = branch.replace("/", "-"); key = f"{repo}.{slug}"
    log = os.path.join(runs_dir(), key + ".jsonl")
    st_path = os.path.join(runs_dir(), key + ".integrate.json")
    lock, holder = integrate_lock(key)
    if not lock:
        append_line(log, {"ts": now(), "kind": "runner", "text": f"another integrate of {branch} is running (pid {holder or 'unknown'}); this one exits"})
        cur = read_json(st_path, {}) or {}
        if holder > 0 and cur.get("state") == "merging" and cur.get("pid") != holder:
            write_json(st_path, {**cur, "pid": holder})   # a press recorded the pid of this process that exits now: the holder's is the real one
        print(f"merge_dossier: another integrate of {branch} is running; nothing to do")
        return 0
    def state(**kw):
        write_json(st_path, {**(read_json(st_path, {}) or {}), "ts": now(), "pid": os.getpid(), **kw})
    def line(text, kind="runner"):
        append_line(log, {"ts": now(), "kind": kind, "text": text})
    line(f"start /merge-queue {branch}")
    # Step 1: the stub's pull request into development. Opening it closes the stub: new work goes to the next one.
    # A stub nothing was merged into yet has no commits beyond development, and GitHub refuses that PR: it then
    # opens right after the merge queue has merged the first finished dossiers, before /stub starts.
    def open_pr() -> tuple[bool, str]:
        date = branch.split("/", 1)[1]
        rc, out = gh(root, "pr", "create", "--base", "development", "--head", branch, "--title", f"Stub {date}: integrating",
                     "--body", "Integration of this stub, started from the dashboard's Integrate.\n\n"
                     "1. The merge queue merges every finished dossier on the stub, one by one.\n"
                     "2. /stub runs the full stack suite, audits each dossier against its criteria, and fixes what fails.\n\n"
                     "## Receipt\n\npending\n\n## Fix rounds\n")
        if rc == 0:
            line(f"Opened the stub's pull request into development ({out.strip().splitlines()[-1] if out.strip() else ''}): {branch} is closed, new work goes to the next stub.", "update")
        return rc == 0, out
    into = stub_into_dev(root, branch)
    if into == "merged":
        state(state="failed", error=f"{branch} is already merged into development"); return 1
    pr_later = False
    if not into:
        ok, out = open_pr()
        if not ok and "no commits between" in out.lower():
            pr_later = True
            line(f"{branch} has nothing merged into it yet, so its pull request into development opens once the first finished dossiers are merged.", "update")
        elif not ok:
            state(state="failed", error="could not open the stub's PR into development: " + out[-200:]); return 1
    rc, out = gh(root, "pr", "list", "--state", "open", "--limit", "200", "--json", "number,headRefName,baseRefName,isDraft")
    if rc != 0:
        state(state="failed", error="gh pr list failed: " + out[:200]); return 1
    everything = json.loads(out or "[]")
    owned = dossier_prs(root)
    ids, others = stub_stacks(everything, branch, owned, dossier_ids(root))   # others: no dossier's stack, never merged from here, only named, so the owner can decide about it
    def ready(i: str, p: dict) -> bool:
        """Every PR of the dossier's stack is out of draft (the queue refuses a stack with a draft in it), and no run is live."""
        chain, _ = chain_of(everything, i, owned)
        return not any(x.get("isDraft") for x in (chain or [p])) and not any(still_building(k) for k in chain_keys(repo, i, chain or [p]))
    finished = picked_stacks(ids, ready, (read_json(st_path, {}) or {}).get("finished") or [])
    inflight = [i for i, p in ids if i not in finished]
    if pr_later and not finished:
        why = (f"nothing to integrate: no dossier on {branch} is finished (no PR out of draft without a live run)"
               + (f"; still in flight: {', '.join(inflight)}" if inflight else ""))
        line(why[0].upper() + why[1:] + ".", "update")
        state(state="failed", error=why); return 1
    state(state="merging", finished=finished, inflight=inflight, error="", root=root, branch=branch)
    line(("Integrating " + branch + ". Merging the finished dossiers first, in order: " + (", ".join(finished) or "none") + "."
          + (" Still in flight, so they move to the next stub once this one is in development: " + ", ".join(inflight) + "." if inflight else "")
          + (" Not part of this integration, because no dossier owns them: " + ", ".join(f"#{p['number']} {p['headRefName']}" for p in others) + ". A PR that changes only documents can go straight to development; any other goes to the next stub." if others else "")), "update")
    for i in finished:
        with contextlib.redirect_stdout(io.StringIO()):
            enqueue(root, i, by="integrate")
    poll = float(os.environ.get("ASD_MERGE_POLL") or 15)
    sts: dict = {}
    while True:   # as long as it takes: each stack ends merged or failed, and the queue survives outages and restarts
        sts = {i: (read_json(os.path.join(runs_dir(), f"{repo}.{i}.merge.json"), {}) or {}) for i in finished}
        if all(v.get("state") in ("merged", "failed", "cancelled") for v in sts.values()):
            break
        time.sleep(poll)
    bad = {i: v for i, v in sts.items() if v.get("state") != "merged"}
    if bad:
        why = "; ".join(f"{i}: {v.get('state')} {v.get('error', '')[:120]}" for i, v in bad.items())
        line("The merge queue did not merge every finished dossier, so /stub did not start: " + why, "update")
        state(state="failed", error=why); return 1
    if pr_later:
        ok, out = open_pr()
        if not ok:
            state(state="failed", error="could not open the stub's PR into development: " + out[-200:]); return 1
    line("All finished dossiers are merged into " + branch + ". Starting /stub: the full stack run, the audit and the stub's PR into development.", "update")
    start_stub(root, branch, slug)
    state(state="stub", note="/stub runs")
    return 0


def start_stub(root: str, branch: str, slug: str) -> str:
    r = subprocess.run([sys.executable, RUNNER, slug, "--root", root, "--command", "stub", "--items", branch], capture_output=True, text=True, timeout=60)
    return (r.stdout or r.stderr).strip()[:200]


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
    if "--head" in a and a[a.index("--head") + 1].startswith(("stub/", "release/")):
        print("OPEN" if os.path.exists(os.path.join(d, "stubpr.txt")) else ""); sys.exit(0)
    sel = [p for p in prs if p.get("state", "OPEN") == "OPEN"]
    if "--base" in a: sel = [p for p in sel if p["baseRefName"] == a[a.index("--base") + 1]] if a[a.index("--base") + 1].startswith("stub/") else sel
    print(json.dumps(sel)); sys.exit(0)
if a[:2] == ["pr", "view"] and a[2].startswith("plan/queue-batch-"):
    print("/pull/901"); sys.exit(0)
if a[:2] == ["pr", "create"] and a[a.index("--head") + 1].startswith("plan/queue-batch-") and os.environ.get("FAKE_BATCH_EXISTS"):
    prs.append({"number": 901, "headRefName": a[a.index("--head") + 1], "baseRefName": a[a.index("--base") + 1], "isDraft": False, "title": "b", "url": "u"})
    json.dump(prs, open(os.path.join(d, "prs.json"), "w")); print("a pull request for branch already exists"); sys.exit(1)
if a[:2] == ["pr", "create"] and a[a.index("--head") + 1].startswith("plan/queue-batch-"):
    prs.append({"number": 901, "headRefName": a[a.index("--head") + 1], "baseRefName": a[a.index("--base") + 1], "isDraft": False, "title": "b", "url": "u", "bucket": os.environ.get("FAKE_BATCH_BUCKET", "pass")})
    json.dump(prs, open(os.path.join(d, "prs.json"), "w")); print("https://github.com/o/r/pull/901"); sys.exit(0)
if a[:2] == ["pr", "close"]:
    for p in prs:
        if str(p["number"]) == a[2]: p["state"] = "CLOSED"
    json.dump(prs, open(os.path.join(d, "prs.json"), "w")); sys.exit(0)
if a[:2] == ["pr", "create"] and os.environ.get("FAKE_NO_COMMITS") and not os.path.exists(os.path.join(d, "merged_any.txt")):
    print("pull request create failed: GraphQL: No commits between development and " + a[a.index("--head") + 1] + " (createPullRequest)"); sys.exit(1)
if a[:2] == ["pr", "create"]:
    open(os.path.join(d, "stubpr.txt"), "w").write(" ".join(a)); print("https://github.com/o/r/pull/900"); sys.exit(0)
if a[:2] == ["stack", "link"]:
    open(os.path.join(d, "linked.txt"), "w").write(" ".join(a[a.index("--base") + 2:]))
    sys.exit(int(os.environ.get("FAKE_LINK_RC", "0")))
if a[:2] in (["stack", "merge"], ["pr", "merge"]):
    if os.environ.get("FAKE_MERGE_RC", "0") != "0": print(os.environ.get("FAKE_MERGE_MSG") or "merge blocked by branch protection"); sys.exit(1)
    if a[0] == "pr" and next((p for p in prs if str(p["number"]) == a[2]), {}).get("mss") == "BEHIND": print("the head branch is not up to date with the base branch"); sys.exit(1)
    nums = open(os.path.join(d, "linked.txt")).read().split() if a[0] == "stack" else [a[2]]
    for p in prs:
        if str(p["number"]) in nums: p["state"] = "MERGED"
    open(os.path.join(d, "merged_any.txt"), "w").write("1")
    if a[0] == "pr" and a[2] == "901" and os.environ.get("FAKE_NO_AUTOMARK") != "1":
        for p in prs:
            if p["headRefName"].startswith(("plan/", "docs/")) and p.get("state", "OPEN") == "OPEN": p["state"] = "MERGED"
    json.dump(prs, open(os.path.join(d, "prs.json"), "w")); sys.exit(0)
if a[:1] == ["api"] and "check-runs" in a[1]:
    if os.environ.get("FAKE_DEV_RC"): print("api failed"); sys.exit(1)
    print(os.environ.get("FAKE_DEV_CHECKS", "success")); sys.exit(0)
if a[:2] == ["pr", "checks"]:
    p = next(p for p in prs if str(p["number"]) == a[2]); print(p.get("bucket", "pass")); sys.exit(0)
if a[:2] == ["run", "list"]: print("[]"); sys.exit(0)
if a[:2] == ["pr", "update-branch"] and os.environ.get("FAKE_NET_BLIP") and not os.path.exists(os.path.join(d, "blipped")):
    open(os.path.join(d, "blipped"), "w").close(); print("error connecting to api.github.com"); sys.exit(1)
if a[:2] == ["pr", "update-branch"] and os.environ.get("FAKE_UPDATE_REFUSED"):
    for p in prs:
        if str(p["number"]) == a[2]: p["mss"] = "CLEAN"
    json.dump(prs, open(os.path.join(d, "prs.json"), "w")); print("Cannot update: the pull request is part of a stack"); sys.exit(1)
if a[:2] == ["pr", "update-branch"]:
    for p in prs:
        if str(p["number"]) == a[2]: p["mss"] = "CLEAN"
    json.dump(prs, open(os.path.join(d, "prs.json"), "w")); sys.exit(0)
if a[:2] == ["pr", "view"] and "mergeStateStatus" in a:
    p = next(p for p in prs if str(p["number"]) == a[2]); print(p.get("mss", "CLEAN")); sys.exit(0)
if a[:2] == ["pr", "view"] and "number,state,baseRefName,headRefName,isDraft,files" in a:
    p = next(p for p in prs if str(p["number"]) == a[2]); print(json.dumps(dict(p, state=p.get("state", "OPEN")))); sys.exit(0)
if a[:2] == ["pr", "view"] and "baseRefName,headRefName" in a:
    p = next(p for p in prs if str(p["number"]) == a[2]); print(json.dumps(p)); sys.exit(0)
if a[:2] == ["pr", "view"] and "headRefOid" in a:
    hf = os.path.join(d, "head.txt")
    print(open(hf).read().strip() if os.path.exists(hf) else os.environ.get("FAKE_HEAD", "")); sys.exit(0)
if a[:2] == ["pr", "view"]:
    p = next(p for p in prs if str(p["number"]) == a[2])
    print(p.get("mergeable", "MERGEABLE") if "mergeable" in a else p.get("state", "OPEN")); sys.exit(0)
sys.exit(3)
'''


def selftest() -> int:
    os.environ.setdefault("ASD_MERGE_NET_TRIES", "2")   # a fixture's unreachable remote must not be waited out for ever
    os.environ.setdefault("ASD_MERGE_NET_WAIT", "0.01")
    fails: list[str] = []
    # Leak guard: the questions and runs the owner really sees must not gain a fixture's (repo.*, clone.*) file during this run.
    real_dirs = [os.environ.get("ASD_QUESTIONS_DIR") or os.path.expanduser("~/.agentic/questions"), runs_dir()]
    listing = lambda: {d: {f for f in os.listdir(d) if f.startswith(("repo.", "clone."))} if os.path.isdir(d) else set() for d in real_dirs}
    before_real = listing()

    def check(name: str, ok: bool, why: str = "") -> None:
        print(("ok   " if ok else "FAIL ") + name + ("" if ok or not why else "  — " + why[:400]))
        if not ok:
            fails.append(name)

    P = lambda n, head, base, draft=False: {"number": n, "headRefName": head, "baseRefName": base, "isDraft": draft, "title": "t", "url": "u"}
    ids_, others_ = stub_stacks([P(5, "feat/W-9-b", "stub/s"), P(3, "feat/W-8-a", "stub/s"), P(4, "fix/W-8-deferred", "feat/W-8-a"), P(6, "plan/x", "stub/s"), P(7, "feat/W-10-c", "development"), P(8, "feat/W-8-again", "stub/s")], "stub/s")
    check("stub members: the dossiers with an open PR straight into the stub, once each, in PR order; stacked, plan and other-base PRs are not extra members",
          [i for i, _ in ids_] == ["W-8", "W-9"] and [p["number"] for p in others_] == [6], f"{ids_} {others_}")
    # GitHub's CONFLICTING flag is cross-checked by git: a head that holds its whole base is not conflicting (W-137 #659),
    # a real conflict still is, and refs git cannot see keep GitHub's word.
    import tempfile as _tf
    with _tf.TemporaryDirectory() as _t:
        _g = lambda *a, cwd: subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *a], cwd=cwd, capture_output=True, text=True)
        _o, _w = os.path.join(_t, "o.git"), os.path.join(_t, "w")
        _g("init", "-q", "--bare", "-b", "feat", _o, cwd=_t); _g("clone", "-q", _o, _w, cwd=_t)
        open(os.path.join(_w, "f"), "w").write("a\n"); _g("add", "f", cwd=_w); _g("commit", "-qm", "a", cwd=_w)
        _g("push", "-q", "origin", "HEAD:feat", cwd=_w)
        _g("checkout", "-qb", "fix", cwd=_w); open(os.path.join(_w, "f"), "w").write("b\n"); _g("commit", "-qam", "b", cwd=_w)
        _g("push", "-q", "origin", "fix", cwd=_w)
        _g("checkout", "-qb", "clash", "feat", cwd=_w); open(os.path.join(_w, "f"), "w").write("c\n"); _g("commit", "-qam", "c", cwd=_w)
        _g("push", "-q", "origin", "clash", cwd=_w)
        _g("checkout", "-q", "feat", cwd=_w); open(os.path.join(_w, "f"), "w").write("d\n"); _g("commit", "-qam", "d", cwd=_w)
        _g("push", "-q", "origin", "feat", cwd=_w)   # feat moved on: fix holds old feat only, clash conflicts with it
        _g("checkout", "-qb", "desc", cwd=_w); open(os.path.join(_w, "g"), "w").write("e\n"); _g("add", "g", cwd=_w); _g("commit", "-qm", "e", cwd=_w)
        _g("push", "-q", "origin", "desc", cwd=_w)
        _refs = {1: ("feat", "desc"), 2: ("feat", "clash"), 3: ("feat", "gone")}
        _saved = globals()["gh"]
        def _fake(root, *a, **k):
            n = int(a[2])
            if "mergeable" in a:
                return 0, "CONFLICTING"
            return 0, json.dumps({"baseRefName": _refs[n][0], "headRefName": _refs[n][1]})
        globals()["gh"] = _fake
        try:
            _bad, _err = conflicting(_w, [1, 2, 3])
        finally:
            globals()["gh"] = _saved
        check("conflicting: a head holding its whole base is mergeable whatever GitHub says; a real clash and an unknown ref stay conflicting",
              _bad == [2, 3] and not _err, f"{_bad} {_err}")
    own_ = {79: "W-029"}
    ids_, others_ = stub_stacks([P(79, "feat/engine-stop-rules", "stub/s"), P(80, "fix/W-029-deferred", "feat/engine-stop-rules"), P(81, "chore/x", "stub/s")], "stub/s", own_)
    check("stub members: a branch with no id belongs to the dossier whose pr: records it", [i for i, _ in ids_] == ["W-029"] and [p["number"] for p in others_] == [81], f"{ids_} {others_}")
    c, why = chain_of([P(79, "feat/engine-stop-rules", "stub/s"), P(80, "fix/W-029-deferred", "feat/engine-stop-rules")], "W-029", own_)
    check("chain: the recorded PR is the bottom, the fix-deferred PR on top", [p["number"] for p in c] == [79, 80], why)
    check("chain: without the record the id-less branch is not found", chain_of([P(79, "feat/engine-stop-rules", "stub/s")], "W-029")[0] == [])
    with tempfile.TemporaryDirectory() as td_:
        os.makedirs(os.path.join(td_, ".discovery", "dossiers"))
        for name, body in (("W-029-a.md", "---\nid: W-029\npr: 79\n---\n"), ("W-030-b.md", "---\nid: W-030\npr: [90, 91]\n---\n"), ("W-031-c.md", "---\nid: W-031\npr: null\n---\n"), ("W-032-d.md", "---\nid: W-032\npr: https://github.com/o/r/pull/83\n---\n")):
            with open(os.path.join(td_, ".discovery", "dossiers", name), "w") as fh_:
                fh_.write(body)
        check("dossier_prs: pr numbers from the front matter, a list too, a PR URL too, null skipped", dossier_prs(td_) == {79: "W-029", 90: "W-030", 91: "W-030", 83: "W-032"}, str(dossier_prs(td_)))
    check("stub members: a dossier already merged into the stub has no open PR, so integrate never meets it", stub_stacks([P(6, "plan/x", "stub/s")], "stub/s")[0] == [])
    c, why = chain_of([P(427, "feat/W-128-x", "stub/2026-11-03"), P(429, "fix/W-128-deferred", "feat/W-128-x"), P(500, "feat/w-130-y", "stub/2026-11-03")], "W-128")
    check("chain: the build PR then the stacked one, bottom to top; another dossier's PR is not in it", [p["number"] for p in c] == [427, 429])
    c, why = chain_of([P(1, "feat/w-7-a", "stub/x"), P(2, "fix/W-7-b", "stub/x")], "W-7")
    check("chain: two PRs both on the stub are not one chain", not c and "one chain" in why)
    c, why = chain_of([P(1, "plan/W-7", "development")], "W-7")
    check("chain: a plan branch is never a dossier's PR", not c and "no open pull request" in why)
    c, why = chain_of([P(40, "feat/LABS-117-qc", "stub/x"), P(50, "fix/W-007-deferred", "feat/LABS-117-qc"), P(41, "fix/LABS-139-a", "stub/x"),
                       P(60, "plan/W-9", "feat/LABS-117-qc")], "LABS-117")
    check("chain: a PR stacked on the dossier's branch joins its stack whatever its branch is called", [p["number"] for p in c] == [40, 50], why)
    # The live layout (podcast-generation-engine W-009): its PR is a Jira-key branch whose id is no dossier, a fix PR stacked on it
    # names the dossier, and the stub's own PR into development sits under both.
    stub9 = "stub/2026-11-21"
    lay = [P(97, "feat/LABS-138-control-plane", stub9), P(99, "fix/W-009-deferred", "feat/LABS-138-control-plane"), P(101, stub9, "development"),
           P(102, "feat/LABS-140-other", stub9)]
    with tempfile.TemporaryDirectory() as td9, tempfile.TemporaryDirectory() as rd9:
        os.makedirs(os.path.join(td9, ".discovery", "dossiers"))
        for name, body in (("W-009-a.md", "---\nid: W-009\npr: 97\n---\n"), ("W-010-b.md", "---\nid: W-010\npr: null\n---\n")):
            with open(os.path.join(td9, ".discovery", "dossiers", name), "w") as fh_:
                fh_.write(body)
        own9, known9 = dossier_prs(td9), dossier_ids(td9)
        check("dossier_ids: every dossier file's id, with or without a pr:", known9 == {"W-009", "W-010"}, str(known9))
        ids9, oth9 = stub_stacks(lay, stub9, own9, known9)
        check("integrate selects W-009 from the pr: record, never the Jira key LABS-138", [i for i, _ in ids9][0] == "W-009" and "LABS-138" not in [i for i, _ in ids9], str(ids9))
        check("integrate: an unknown Jira key with no record and no stacked dossier is nobody's PR", [p["number"] for p in oth9] == [102] and len(ids9) == 1, f"{ids9} {oth9}")
        ids9b, _ = stub_stacks(lay, stub9, {}, known9)
        check("integrate: without the pr: record the stacked fix/W-009-deferred still gives W-009", [i for i, _ in ids9b] == ["W-009"], str(ids9b))
        ids9c, _ = stub_stacks([P(97, "feat/LABS-138-control-plane", stub9)], stub9, {}, {"LABS-138"})
        check("integrate: a Jira key that IS a dossier id still names its dossier", [i for i, _ in ids9c] == ["LABS-138"], str(ids9c))
        c9, why9 = chain_of(lay, "W-009", own9)
        check("chain: W-009 is #97 then #99 and never the stub's PR #101", [p["number"] for p in c9] == [97, 99], f"{c9} {why9}")
        c9, why9 = chain_of(lay, "LABS-138", own9)
        check("chain: even asked for by the Jira key, the stub's PR #101 is not in it", 101 not in [p["number"] for p in c9], f"{c9} {why9}")
        c9, why9 = chain_of([P(101, stub9, "development")], "W-009", {101: "W-009"})
        check("chain: a PR whose head is the stub is never a dossier's, and the failure names the dossier", not c9 and "W-009" in why9, why9)
        _sv = {k: globals()[k] for k in ("gh", "stub_into_dev")}
        _env = os.environ.get("ASD_RUNS_DIR")
        os.environ["ASD_RUNS_DIR"] = rd9
        try:
            globals()["gh"] = lambda root, *a, **k: (0, json.dumps(lay))
            globals()["stub_into_dev"] = lambda root, stub: "open" if stub == stub9 else ""
            ch, why = plan(td9, "W-009")
            check("queue: W-009 resolves to #97 + #99 on the stub, not to #101 into development", [p["number"] for p in ch] == [97, 99] or "being integrated" in why, f"{ch} {why}")
            check("queue: the refusal, if any, is the stub's, not '#101 goes into development'", "#101" not in why, why)
            with open(os.path.join(rd9, f"{repo_name(td9)}.{stub9.replace('/', '-')}.integrate.json"), "w") as fh_:
                json.dump({"finished": ["W-009"]}, fh_)
            ch, why = plan(td9, "W-009")
            check("queue: picked by the integration, W-009's chain is #97, #99 with base the stub", [p["number"] for p in ch] == [97, 99] and ch[0]["baseRefName"] == stub9, f"{ch} {why}")
            check("eager: stub_dossiers lists W-009 (so the eager step sees it on the stub), not LABS-138", stub_dossiers(td9, stub9) == ["W-009"], str(stub_dossiers(td9, stub9)))
        finally:
            globals().update(_sv)
            if _env is None: os.environ.pop("ASD_RUNS_DIR", None)
            else: os.environ["ASD_RUNS_DIR"] = _env
    check("chain: the stack's run keys include every dossier id its branches name",
          chain_keys("repo", "LABS-117", c) == ["repo.LABS-117", "repo.W-007"], str(chain_keys("repo", "LABS-117", c)))
    w199 = [P(597, "docs/harness2-ingest-and-h14-plan", "stub/2026-11-18"), P(624, "fix/W-199-deferred", "docs/harness2-ingest-and-h14-plan"),
            P(600, "docs/other-research", "stub/2026-11-18")]
    c199, why199 = chain_of(w199, "W-199")
    check("chain: the id-less PR a dossier's fix PR is stacked on is the chain's bottom", [p["number"] for p in c199] == [597, 624], why199)
    ids_, others_ = stub_stacks(w199, "stub/2026-11-18")
    check("stub members: a docs branch under a dossier's stacked PR belongs to that dossier",
          [(i, p["number"]) for i, p in ids_] == [("W-199", 597)] and [p["number"] for p in others_] == [600], f"{ids_} {others_}")
    two = [P(1, "docs/x", "stub/s"), P(2, "fix/W-1-deferred", "docs/x"), P(3, "fix/W-2-deferred", "docs/x")]
    check("stub members: an id-less PR under two dossiers' PRs belongs to neither", stub_stacks(two, "stub/s")[0] == [])
    check("chain: a plan PR below is never pulled into the chain",
          [p["number"] for p in chain_of([P(1, "plan/W-5", "development"), P(2, "fix/W-5-deferred", "plan/W-5")], "W-5")[0]] == [2])
    real_git = globals()["git"]
    trees = {"origin/fix/W-007-deferred": {".discovery/dossiers/W-007-qc.md": "---\nid: W-007\npr: https://x/pull/40\n---\nbody"},
             "origin/feat/LABS-139-a": {".discovery/dossiers/W-018-order.md": "---\nid: W-018\npr:\n  - https://x/pull/41\n---\n",
                                        ".discovery/dossiers/W-019-other.md": "---\nid: W-019\npr: https://x/pull/42\n---\n"}}
    def fake_git(root, *a, timeout=300):
        if a[0] == "ls-tree":
            t = trees.get(a[2])
            return (0, "\n".join(t)) if t is not None else (128, "not a tree")
        if a[0] == "show":
            ref, f = a[1].split(":", 1)
            return 0, trees.get(ref, {}).get(f, "")
        return 0, ""
    globals()["git"] = fake_git
    try:
        check("dossier id: a stack's fix branch names the dossier that has a file", dossier_id("/r", "LABS-117", c) == "W-007", dossier_id("/r", "LABS-117", c))
        one = [P(41, "feat/LABS-139-a", "stub/x")]
        check("dossier id: without one, the dossier whose pr: block list names the stack's PR", dossier_id("/r", "LABS-139", one) == "W-018", dossier_id("/r", "LABS-139", one))
        check("dossier id: nothing found keeps the queue's id", dossier_id("/r", "W-55", [P(9, "feat/W-55-z", "stub/x")]) == "W-55")
    finally:
        globals()["git"] = real_git
    check("chain: W-12 is not W-128", chain_of([P(1, "feat/W-128-x", "stub/x")], "W-12")[0] == [])

    # carry-over: a merged stub's leftover PRs, moved to development by GitHub when its branch was deleted on merge
    with tempfile.TemporaryDirectory() as tmp:
        root = os.path.join(tmp, "repo"); rdir = os.path.join(tmp, "runs")
        os.makedirs(rdir); subprocess.run(["git", "init", "-q", root], check=True)
        saved_env, saved = os.environ.get("ASD_RUNS_DIR"), {k: globals()[k] for k in ("gh", "stub_into_dev", "accepting_stub")}
        os.environ["ASD_RUNS_DIR"] = rdir
        edits: list[str] = []
        timeline = {7: [{"oldBase": "stub/old", "newBase": "development"}], 8: [], 9: [{"previousRefName": "stub/old", "currentRefName": "development"}]}
        open_prs = [{"number": 7, "headRefName": "feat/W-1-a", "baseRefName": "development"}, {"number": 8, "headRefName": "docs/x", "baseRefName": "development"},
                    {"number": 9, "headRefName": "fix/W-2-deferred", "baseRefName": "development"}, {"number": 10, "headRefName": "feat/W-3", "baseRefName": "stub/old"},
                    {"number": 11, "headRefName": "feat/W-1-b", "baseRefName": "feat/W-1-a"},
                    {"number": 12, "headRefName": "fix/W-3-b", "baseRefName": "feat/W-3"}]
        stacked = {10: [5]}   # #10 is in GitHub stack 5: a plain base change is refused until it is unstacked
        def fake(_root, *a, **_k):
            if a[:2] == ("api", "graphql") and any("ADDED_TO_STACK" in x for x in a):
                n = int(next(x for x in a if x.startswith("n=")).split("=")[1])
                return 0, " ".join(map(str, stacked.get(n, [])))
            if a[:2] == ("stack", "unstack"):
                edits.append(f"unstack {a[2]}"); [v.remove(int(a[2])) for v in stacked.values() if int(a[2]) in v]; return 0, ""
            if a[:2] == ("stack", "link"):
                edits.append("link " + " ".join(a[2:])); return 0, ""
            if a[:2] == ("api", "graphql"):
                n = int(next(x for x in a if x.startswith("n=")).split("=")[1])
                nodes = timeline.get(n, [])
                was = [x.get("oldBase") or x.get("previousRefName") for x in nodes if (x.get("newBase") or x.get("currentRefName")) == "development"]
                return 0, (was[-1] if was else "")
            if a[:2] == ("pr", "list"):
                return 0, json.dumps(open_prs)
            if a[:2] == ("pr", "edit"):
                if stacked.get(int(a[2])):
                    return 1, "GraphQL: Cannot change the base branch because the pull request is part of a stack. (updatePullRequest)"
                edits.append(f"{a[2]}>{a[4]}"); return 0, ""
            return 1, "unexpected"
        try:
            globals().update(gh=fake, stub_into_dev=lambda _r, b: "merged" if b == "stub/old" else "", accepting_stub=lambda _r, _o: ("stub/new", ""))
            check("moved_off: the automatic retarget names the old stub", moved_off(root, 7) == "stub/old")
            check("moved_off: never moved, nothing", moved_off(root, 8) == "")
            name = repo_name(root)
            write_json(os.path.join(rdir, f"{name}.W-1.merge.json"), {"state": "failed", "error": "its stub stub/old is being integrated into development; it moves to the next stub once that merges"})
            write_json(os.path.join(rdir, f"{name}.W-4.merge.json"), {"state": "failed", "error": "merge blocked by branch protection"})
            done, lines = carry_over(root, "stub/old")
            check("carry-over: every PR left on the merged stub (moved to development or still on it) goes to the accepting stub; a docs PR and a stacked one stay",
                  done and sorted(e for e in edits if ">" in e) == ["10>stub/new", "7>stub/new", "9>stub/new"], str((done, edits, lines)))
            check("carry-over: a PR in a GitHub stack is unstacked, moved, and its chain linked again on the new stub",
                  "unstack 5" in edits and edits.index("unstack 5") < edits.index("10>stub/new") and "link --base stub/new 10 12" in edits, str(edits))
            check("carry-over: the stale 'being integrated' record is cleared, any other failure kept",
                  not os.path.exists(os.path.join(rdir, f"{name}.W-1.merge.json")) and os.path.exists(os.path.join(rdir, f"{name}.W-4.merge.json")), str(lines))
            edits.clear()
            globals().update(accepting_stub=lambda _r, _o: ("", "stub/x is being integrated; the next stub is cut once it merges"))
            done, lines = carry_over(root, "stub/old")
            check("carry-over: no accepting stub yet, nothing moved and it is tried again", not done and edits == [] and "being integrated" in lines[0], str((done, lines)))
            done, lines = carry_over(root, "stub/other")
            check("carry-over: a stub not merged yet is tried again", not done and "not merged" in lines[0], str(lines))
            globals().update(accepting_stub=lambda _r, _o: ("stub/new", ""))
            edits.clear()
            chain, why = plan(root, "W-1")
            check("plan: a dossier GitHub moved to development is taken onto the accepting stub, not refused", why == "" and bool(chain) and chain[0]["baseRefName"] == "stub/new" and "7>stub/new" in edits, str((why, edits)))
        finally:
            globals().update(saved)
            if saved_env is None: os.environ.pop("ASD_RUNS_DIR", None)
            else: os.environ["ASD_RUNS_DIR"] = saved_env

    with tempfile.TemporaryDirectory() as tmp:
        fdir, rdir, root = (os.path.join(tmp, x) for x in ("gh", "runs", "repo"))
        for x in (fdir, rdir, root):
            os.makedirs(x)
        subprocess.run(["git", "init", "-q", root], check=True)
        open(os.path.join(fdir, "gh.py"), "w").write(FAKE_GH)
        fake_runner = os.path.join(tmp, "runner.py")
        open(fake_runner, "w").write("import sys\nopen(sys.argv[0] + '.args', 'w').write(' '.join(sys.argv[1:]))\nprint('started')\n")
        env = dict(os.environ, ASD_GH_BIN=f"{sys.executable} {os.path.join(fdir, 'gh.py')}", FAKE_GH_DIR=fdir, ASD_RUNS_DIR=rdir, ASD_QUESTIONS_DIR=os.path.join(tmp, "questions"))
        os.environ["ASD_QUESTIONS_DIR"] = os.path.join(tmp, "questions")   # the owner questions the queue asks stay in the fixture
        os.environ["ASD_RUNS_DIR"] = rdir   # ... and so does the state the queue keeps (resolve_attempts)
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
              rc == 0 and "stack link --base stub/2026-11-03 427 429" in calls and "stack merge 429 --yes --merge" in calls and st.get("state") == "merged")
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
        # ---- kanban: a dossier whose front matter says `delivery: kanban` may merge straight into development ----
        kfile = os.path.join(root, ".discovery", "dossiers", "W-128-x.md")
        os.makedirs(os.path.dirname(kfile), exist_ok=True)
        open(kfile, "w").write("---\nid: W-128\nstatus: pr\ndelivery: kanban\n---\n# k\n")
        check("kanban_ids: only `delivery: kanban` counts (absent and stub do not)", kanban_ids(root) == {"W-128"})
        rc, out, calls = go([P(427, "feat/W-128-x", "development")])
        check("kanban: a PR into development merges as is, no stub is looked up",
              rc == 0 and "pr merge 427 --merge" in calls and not any("--head" in c for c in calls)
              and read_json(os.path.join(rdir, key + ".merge.json"), {}).get("base") == "development", str((rc, out, calls)))
        rc, out, calls = go([P(427, "feat/W-128-x", "development", True)])
        check("kanban: a draft is still refused", rc == 1 and "draft" in out and not any(c.startswith("pr merge") for c in calls), out)
        rc, out, calls = go([P(427, "feat/W-128-x", "development")], FAKE_MERGE_RC="1", FAKE_MERGE_MSG="Repository rule violations found: Required status check local-stack is expected")
        st_ = read_json(os.path.join(rdir, key + ".merge.json"), {})
        check("kanban: GitHub refusing for local-stack records the clear note, one try, no retry",
              rc == 1 and st_.get("state") == "failed" and st_.get("error") == "development requires local-stack; the owner must relax it for kanban PRs"
              and sum(c.startswith("pr merge") for c in calls) == 1, str((st_, calls)))
        rc, out, calls = go([P(427, "feat/W-128-x", "stub/2026-11-03")])
        check("kanban: a PR into a stub goes the stub way unchanged", rc == 0 and "pr merge 427 --merge" in calls and any("--head" in c for c in calls), str(calls))
        os.remove(kfile)
        rc, out, calls = go([P(427, "feat/W-128-x", "development")])
        check("a dossier without `delivery: kanban` into development is refused as before", rc == 1 and "not a stub" in out)
        # stop the line: development's red CI holds kanban merges
        saved_e = {k: os.environ.get(k) for k in ("ASD_GH_BIN", "FAKE_GH_DIR", "FAKE_DEV_CHECKS", "FAKE_DEV_RC", "ASD_MERGE_POLL", "ASD_MERGE_CHECK_TIMEOUT")}
        os.environ.update(env, ASD_MERGE_POLL="0.05", ASD_MERGE_CHECK_TIMEOUT="0.3")
        try:
            notes: list = []
            got = {}
            for name, checks_, rcx in (("green", "success,skipped,neutral", ""), ("pending", "success,pending", ""), ("red", "success,failure", ""), ("unknown", "", "1")):
                os.environ["FAKE_DEV_CHECKS"] = checks_
                if rcx: os.environ["FAKE_DEV_RC"] = rcx
                else: os.environ.pop("FAKE_DEV_RC", None)
                got[name] = dev_ci(root)
            check("dev_ci: green, pending, red, unknown (unreadable)", got == {"green": "green", "pending": "pending", "red": "red", "unknown": "unknown"}, str(got))
            os.environ.pop("FAKE_DEV_RC", None)
            os.environ["FAKE_DEV_CHECKS"] = "success,failure"
            why = wait_development(root, lambda s_, t_: notes.append((s_, t_)))
            check("stop the line: a red development holds the merge, the note says why, then it gives up with the reason",
                  "stayed red" in why and notes == [("waiting", "development's CI is red; kanban merges wait until it is green")], str((why, notes)))
            os.environ["FAKE_DEV_CHECKS"] = "success"
            check("stop the line: green development lets it through", wait_development(root, lambda *a_: None) == "")
        finally:
            for k, v in saved_e.items():
                if v is None: os.environ.pop(k, None)
                else: os.environ[k] = v
        rc, out, calls = go([P(427, "feat/W-128-x", "stub/x", True)])
        check("refuse: a draft", rc == 1 and "draft" in out)
        write_json(os.path.join(rdir, key + ".runner.json"), {"pid": os.getpid(), "state": "running", "command": "fix-deferred"})
        rc, out, calls = go(stack)
        check("refuse: while a run of the dossier is live", rc == 1 and "still live" in out and not calls)

        # ---- the merge queue: FIFO; a conflict wakes the dossier's agent, then the script merges, then cleanup ----
        FAKE_RUN = r"""
import json, os, sys, datetime
a = sys.argv[1:]; d = os.environ["FAKE_GH_DIR"]; rdir = os.environ["ASD_RUNS_DIR"]
dossier, cmd = a[0], a[a.index("--command") + 1]
open(os.path.join(d, "runs.txt"), "a").write(f"{dossier} {cmd}\n")
if cmd == "resolve-merge":
    n = open(os.path.join(d, "runs.txt")).read().count("resolve-merge")
    if not os.environ.get("FAKE_NO_PUSH"): open(os.path.join(d, "head.txt"), "w").write(f"sha{n}")
    if n >= int(os.environ.get("FAKE_RESOLVED_AT_ROUND") or 1):
        prs = json.load(open(os.path.join(d, "prs.json")))
        for p in prs: p["mergeable"] = "MERGEABLE"; p.pop("bucket", None)
        json.dump(prs, open(os.path.join(d, "prs.json"), "w"))
ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
json.dump({"state": "failed" if cmd == "resolve-merge" and os.environ.get("FAKE_RUN_FAILS") else "done", "pid": 2 ** 22 + 7, "started": ts, "command": cmd}, open(os.path.join(rdir, "repo." + dossier + ".runner.json"), "w"))
print("started")
"""
        fr = os.path.join(tmp, "fakerun.py"); open(fr, "w").write(FAKE_RUN)
        os.remove(os.path.join(rdir, key + ".runner.json"))
        prs2 = [dict(P(427, "feat/W-128-x", "stub/s"), mergeable="CONFLICTING"), P(429, "fix/W-128-deferred", "feat/W-128-x"), P(500, "feat/w-130-y", "stub/s")]
        json.dump(prs2, open(os.path.join(fdir, "prs.json"), "w"))
        for f in ("calls.txt", "runs.txt"):
            if os.path.exists(os.path.join(fdir, f)): os.remove(os.path.join(fdir, f))
        old = dict(os.environ)
        os.environ.update(env, ASD_RUNNER_BIN=fr, ASD_MERGE_POLL="0.2")
        try:
            import io, contextlib
            with contextlib.redirect_stdout(io.StringIO()):
                enqueue(root, "W-128"); enqueue(root, "W-130", by="eager")
            qp = queue_path("repo")
            done = False
            for _ in range(300):
                q = read_json(qp, {}) or {}
                if not q.get("items") and not (q.get("worker") and alive(q["worker"])):
                    done = True; break
                time.sleep(0.1)
        finally:
            os.environ.clear(); os.environ.update(old)
        runs = open(os.path.join(fdir, "runs.txt")).read().split("\n") if os.path.exists(os.path.join(fdir, "runs.txt")) else []
        calls = open(os.path.join(fdir, "calls.txt")).read().split("\n")
        check("queue: worked to the end", done)
        check("queue: a conflict wakes the dossier's agent first, then its cleanup; FIFO order", runs[:3] == ["W-128 resolve-merge", "W-128 after-merge", "W-130 after-merge"], str(runs))
        m128 = [i for i, c in enumerate(calls) if c.startswith("stack merge 429")]
        m130 = [i for i, c in enumerate(calls) if c.startswith("pr merge 500")]
        check("queue: the script merges each, W-128 before W-130", bool(m128) and bool(m130) and m128[0] < m130[0], str(calls))
        def queue_timeline(d):
            f = os.path.join(rdir, f"repo.{d}.jsonl")
            return [json.loads(l).get("text", "") for l in open(f)] if os.path.exists(f) else []
        check("queue: the timeline names who queued it (the button for W-128, eager merge for W-130)",
              any(t.startswith("merge queue: queued") and "(your Merge button)" in t for t in queue_timeline("W-128"))
              and any(t.startswith("merge queue: queued") and "(eager merge" in t for t in queue_timeline("W-130")), str(queue_timeline("W-130"))[:400])
        check("queue: the merge line repeats the source", any(t.startswith("merged ") and "(eager merge" in t for t in queue_timeline("W-130"))
              and any(t.startswith("merged ") and "(your Merge button)" in t for t in queue_timeline("W-128")), str(queue_timeline("W-130"))[:400])
        # ---- migration numbers: a clash goes to the agent before any check is waited for ----
        mo = os.path.join(tmp, "mig.git"); mw = os.path.join(tmp, "mig")
        mg = lambda *x, cwd=mw: subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *x], cwd=cwd, capture_output=True, text=True)
        mg("init", "-q", "--bare", "-b", "main", mo, cwd=tmp); mg("clone", "-q", mo, mw, cwd=tmp)
        def mfile(path):
            os.makedirs(os.path.dirname(os.path.join(mw, path)), exist_ok=True); open(os.path.join(mw, path), "w").write(path)
        mfile("crates/p/migrations/0061_a.sql"); mfile("other/migrations/0062_z.sql"); mg("add", "."); mg("commit", "-q", "-m", "base"); mg("push", "-q", "origin", "HEAD:main")
        def mbranch(name, *paths, rename=None):
            mg("checkout", "-q", "-B", name, "origin/main")
            if rename: mg("mv", *rename)
            for p in paths: mfile(p)
            mg("add", "."); mg("commit", "-q", "-m", name); mg("push", "-q", "-f", "origin", f"HEAD:{name}")
        mbranch("clash", "crates/p/migrations/0061_b.sql")
        found = migration_clashes(mw, "main", "clash")
        check("migrations: a second 0061 in the same folder clashes, and names both files", found == ["crates/p/migrations/0061_b.sql has the number of crates/p/migrations/0061_a.sql"], str(found))
        mbranch("free", "crates/p/migrations/0062_b.sql", "crates/q/migrations/0061_c.sql")
        check("migrations: a free number, and the same number in another folder, do not clash", migration_clashes(mw, "main", "free") == [], str(migration_clashes(mw, "main", "free")))
        mbranch("twice", "crates/p/migrations/0070_x.sql", "crates/p/migrations/0070_y.sql")
        check("migrations: two new files of one stack with one number clash", len(migration_clashes(mw, "main", "twice")) == 2, str(migration_clashes(mw, "main", "twice")))
        mbranch("moved", rename=("crates/p/migrations/0061_a.sql", "crates/p/migrations/0062_a.sql"))
        check("migrations: renumbering onto a free number is no clash", migration_clashes(mw, "main", "moved") == [], str(migration_clashes(mw, "main", "moved")))
        check("migrations: a branch not on origin is left to the merge's checks", migration_clashes(mw, "main", "no-such") == [])
        # origin/-prefixed names (what the resolve-merge agent passed) read the same refs as bare ones
        bare_found = migration_clashes(mw, "main", "clash")
        check("migrations: origin/-prefixed and refs/remotes/origin/ names find the same clash as bare names",
              bool(bare_found) and migration_clashes(mw, "origin/main", "origin/clash") == bare_found
              and migration_clashes(mw, "refs/remotes/origin/main", "refs/remotes/origin/clash") == bare_found, str(migration_clashes(mw, "origin/main", "origin/clash")))
        check("migrations: bare_ref strips only a leading remote prefix", bare_ref("origin/fix/W-1") == "fix/W-1" and bare_ref("fix/origin/x") == "fix/origin/x")
        _o, _e = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(_o), contextlib.redirect_stderr(_e):
            rc_bad = main(["--migration-clashes", "no-such", "--base", "main", "--root", mw])
        check("migrations: the CLI on unreadable refs exits non-zero with refs unreadable, never no clash",
              rc_bad != 0 and "refs unreadable" in _e.getvalue() and "no clash" not in _o.getvalue() + _e.getvalue(), _e.getvalue())
        # next free number: across the base and every open PR head, not the base alone
        mbranch("other-open", "crates/p/migrations/0063_open.sql", "crates/p/migrations/0064_open2.sql")
        mbranch("clash2", "crates/p/migrations/0061_b.sql")
        _sv_gh = globals()["gh"]
        globals()["gh"] = lambda root_, *a, **k: (0, json.dumps([{"headRefName": "other-open"}, {"headRefName": "clash2"}]))
        try:
            free = next_free_migration(mw, "main", "crates/p/migrations/0061_b.sql")
            _o2 = io.StringIO()
            with contextlib.redirect_stdout(_o2):
                rc_c = main(["--migration-clashes", "origin/clash2", "--base", "origin/main", "--root", mw])
        finally:
            globals()["gh"] = _sv_gh
        check("migrations: the next free number counts every open PR head (0064 on another branch -> 0065), keeps the padding", free == "0065", free)
        check("migrations: the CLI prints the clash and that free number", rc_c == 1 and "0061_b.sql has the number of" in _o2.getvalue() and "free on the base and every open pull request head: 0065" in _o2.getvalue(), _o2.getvalue())
        saved_fns = {n: globals()[n] for n in ("migration_clashes", "conflicting", "wait_checks", "behind", "collisions", "start_run", "wait_run")}
        seen_runs: list = []; waited: list = []; notes: list = []; left = [2]
        def fake_clash(*_a):
            return ["m/0061_b.sql has the number of m/0061_a.sql"] if left[0] > 0 else []
        def fake_start(*_a):
            seen_runs.append(1); left[0] -= 1; return ""
        globals().update(migration_clashes=fake_clash, conflicting=lambda *_a: ([], ""), wait_checks=lambda r, n, t: waited.append(1) or {x: "green" for x in n},
                         behind=lambda *_a: [], collisions=lambda *_a: ([], False), start_run=fake_start, wait_run=lambda *_a: "done")
        one_pr = [P(700, "feat/W-160-x", "stub/s")]
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                why = settle(root, "repo.W-160", "W-160", one_pr, "stub/s", [], lambda s, t: notes.append(t))
            check("queue: a migration clash wakes the agent before any check is waited for, then it merges", why == "" and len(seen_runs) == 2 and len(waited) == 1 and "migration numbers clash" in notes[0], f"{why} {seen_runs} {waited} {notes}")
            left[0] = 99; seen_runs.clear()
            with contextlib.redirect_stdout(io.StringIO()):
                why = settle(root, "repo.W-160", "W-160", one_pr, "stub/s", [], lambda s, t: None)
            check("queue: a clash that stays stops at the round limit and says so", len(seen_runs) == 2 and "migration numbers still clash" in why and "after 2 resolve-merge attempts" in why, why)
        finally:
            globals().update(saved_fns)
        # ---- agent rounds: the queue keeps at it, and stops at the limit ----
        def rounds_scenario(dossier: str, pr: int, resolved_at: int, extra_env: dict | None = None, **fields) -> tuple[list[str], dict]:
            json.dump([dict(P(pr, f"feat/{dossier}-x", "stub/s"), **(fields or {"mergeable": "CONFLICTING"}))], open(os.path.join(fdir, "prs.json"), "w"))
            for f in ("runs.txt", "calls.txt"):
                if os.path.exists(os.path.join(fdir, f)): os.remove(os.path.join(fdir, f))
            open(os.path.join(fdir, "head.txt"), "w").write("sha0")
            saved = dict(os.environ)
            os.environ.update(env, ASD_RUNNER_BIN=fr, ASD_MERGE_POLL="0.2", FAKE_RESOLVED_AT_ROUND=str(resolved_at), **(extra_env or {}))
            try:
                with contextlib.redirect_stdout(io.StringIO()):
                    enqueue(root, dossier)
                for _ in range(600):
                    q = read_json(queue_path("repo"), {}) or {}
                    if not q.get("items") and not (q.get("worker") and alive(q["worker"])):
                        break
                    time.sleep(0.1)
            finally:
                os.environ.clear(); os.environ.update(saved)
            made = open(os.path.join(fdir, "runs.txt")).read().split("\n") if os.path.exists(os.path.join(fdir, "runs.txt")) else []
            return [r for r in made if "resolve-merge" in r], read_json(os.path.join(rdir, f"repo.{dossier}.merge.json"), {}) or {}
        r3, s3 = rounds_scenario("W-141", 610, 2)
        check("queue: a conflict starts resolve-merge by itself; the second attempt may resolve it, and the stack merges", len(r3) == 2 and s3.get("state") == "merged", str(r3) + str(s3))
        r4, s4 = rounds_scenario("W-142", 620, 99)
        check("queue: two resolve-merge attempts per PR is the cap; the stack fails and says so", len(r4) == 2 and s4.get("state") == "failed" and "after 2 resolve-merge attempts" in str(s4.get("error")), str(r4) + str(s4))
        qdir = os.path.join(tmp, "questions")
        asked = [f for f in os.listdir(qdir) if f.endswith(".jsonl")] if os.path.isdir(qdir) else []
        check("queue: the owner is asked once the attempts are spent", any("W-142" in f for f in asked), str(asked))
        check("queue: ... and not before (W-141 resolved on the second attempt, no question)", not any("W-141" in f for f in asked), str(asked))
        r7, s7 = rounds_scenario("W-145", 650, 99, extra_env={"FAKE_NO_PUSH": "1"})
        check("queue: once per PR head: a run that pushed nothing is not repeated on the same head", len(r7) == 1 and s7.get("state") == "failed" and "head did not change" in str(s7.get("error")), str(r7) + str(s7))
        r8, s8 = rounds_scenario("W-146", 660, 99, extra_env={"FAKE_RUN_FAILS": "1"})
        check("queue: resolve-merge failing twice stops after two runs and fails the stack", len(r8) == 2 and s8.get("state") == "failed" and "ended failed twice" in str(s8.get("error")), str(r8) + str(s8))
        r5, s5 = rounds_scenario("W-143", 630, 2, bucket="fail")
        check("queue: a check still red after the agent's push gets another round", len(r5) == 2 and s5.get("state") == "merged", str(r5) + str(s5))
        r6, s6 = rounds_scenario("W-144", 640, 99, bucket="fail")
        check("queue: red checks stop at the cap too", len(r6) == 2 and s6.get("state") == "failed" and "checks still red: #640 after 2 resolve-merge attempts" in str(s6.get("error")), str(r6) + str(s6))
        os.remove(os.path.join(fdir, "head.txt"))
        # ---- defects seen on W-134 (2026-11-17): owner questions, a refused start, restarts, two integrates ----
        os.environ["ASD_MERGE_POLL"] = "0.05"
        import threading
        later = lambda secs, fn: threading.Timer(secs, fn).start()
        rk = "repo.W-134"; rpath = os.path.join(rdir, rk + ".runner.json")
        def runner_state(state, started, command="resolve-merge"):
            write_json(rpath, {"state": state, "pid": os.getpid(), "started": started, "command": command})
        # 1. a run waiting on an owner question does not use up the attempt's time
        runner_state("waiting", "2026-11-17T01:44:00Z")
        later(1.0, lambda: runner_state("done", "2026-11-17T01:44:00Z"))
        got = wait_run(rk, "2026-11-17T01:44:00Z", 0.4)
        check("wait: a run waiting on an owner question is not timed out", got == "done", got)
        runner_state("running", "2026-11-17T01:44:00Z")
        later(1.0, lambda: runner_state("done", "2026-11-17T01:44:00Z"))
        got = wait_run(rk, "2026-11-17T01:44:00Z", 0.4)
        check("wait: a run that works longer than the limit still times out", got == "timeout", got)
        runner_state("done", "2026-11-17T01:44:00Z")
        # B: waiting is not failing. Time queued for a slot is real time, even when the worker's own hook is slow.
        global WAIT_HOOK
        saved_hook = WAIT_HOOK
        WAIT_HOOK = lambda: time.sleep(0.3)
        runner_state("queued", "2026-11-17T01:44:00Z")
        later(1.0, lambda: runner_state("done", "2026-11-17T01:44:00Z"))
        got = wait_run(rk, "2026-11-17T01:44:00Z", 0.4)
        WAIT_HOOK = saved_hook
        check("wait: time a queue-started run spends queued for a slot does not count toward the timeout, however slow the poll", got == "done", got)
        # a run keyed `<key>.resolve-<prs>` (run_dossier's key for a queue-started resolve-merge) is the one waited for
        rk2 = resolve_key(rk, [5, 6]); rpath2 = os.path.join(rdir, rk2 + ".runner.json")
        check("queue: the resolve-merge run key is <repo>.<ID>.resolve-<prs>", rk2 == "repo.W-134.resolve-5-6")
        write_json(rpath2, {"state": "running", "pid": os.getpid(), "started": "2026-11-17T09:00:00Z", "command": "resolve-merge"})
        later(0.5, lambda: write_json(rpath2, {"state": "done", "pid": os.getpid(), "started": "2026-11-17T09:00:00Z", "command": "resolve-merge"}))
        got = wait_run([rk2, rk], "2026-11-17T09:00:00Z", 5)
        check("wait: the queue waits on the resolve-merge run's own key, not the dossier's stale record", got == "done", got)
        check("queue: a live resolve run of the dossier is seen by the precheck keys", resolve_runs_live(rk) == [] and not os.remove(rpath2))
        # W-197: a run that failed but holds an open run-failure question is alive: Retry resumes the same run, no new attempt
        time.sleep(1.2)   # let the timers of the tests above fire before this one writes the same runner.json
        qd_ = os.environ["ASD_QUESTIONS_DIR"]; os.makedirs(qd_, exist_ok=True)
        append_line(os.path.join(qd_, rk + ".Q91.jsonl"), {"kind": "question", "topic": "run-failure", "text": "The run of W-134 failed at stage 1: another process changed its merge worktree"})
        write_json(rpath, {"state": "failed", "pid": 1, "started": "2026-11-17T10:00:00Z", "command": "resolve-merge"})
        later(0.6, lambda: runner_state("running", "2026-11-17T10:00:00Z"))
        later(0.8, lambda: runner_state("done", "2026-11-17T10:00:00Z"))
        got = wait_run(rk, "2026-11-17T10:00:00Z", 0.5)
        check("wait: a failed run with its own open run-failure question is waited on; answered Retry, the same run ends done (no second attempt)", got == "done", got)
        # W-182: a question about the stack's own parent conflict must not hold the queue
        append_line(os.path.join(qd_, "repo.W-182.Q92.jsonl"), {"kind": "question", "topic": "run-failure", "text": "failed at stage 4: CI not started on PR #688: its parent PR still conflicts after the runner's resolve-merge"})
        check("queue: an open run-failure question about a conflicting parent PR is not waited on; any other one is",
              open_failure_questions("repo.W-182", conflicts=False) == [] and open_failure_questions("repo.W-182") == ["repo.W-182.Q92"]
              and open_failure_questions(rk, conflicts=False) == [rk + ".Q91"], str(open_failure_questions(rk, conflicts=False)))
        for f_ in (rk + ".Q91.jsonl", "repo.W-182.Q92.jsonl"):
            os.remove(os.path.join(qd_, f_))
        time.sleep(1.2)
        runner_state("done", "2026-11-17T01:44:00Z")
        # 2. the runner refused the start, since the dossier's run is live: the queue waits for that run, no new attempt
        saved_fns = {n: globals()[n] for n in ("conflicting", "wait_checks", "behind", "collisions", "start_run", "head_of", "ask_resolve_failed", "migration_clashes")}
        state = {"live": True, "starts": 0}
        def refused_start(*_a):
            state["starts"] += 1
            return "repo.W-134 is already run by pid 1"   # what the runner says; its runner.json stays the live run's
        def end_old_run():
            runner_state("done", "2026-11-17T01:44:00Z"); state["live"] = False
        globals().update(conflicting=lambda r, n: (list(n) if state["live"] else [], ""), wait_checks=lambda r, n, t: {x: "green" for x in n},
                         behind=lambda *_a: [], collisions=lambda *_a: ([], False), start_run=refused_start, head_of=lambda *_a: "h1",
                         ask_resolve_failed=lambda *_a: None, migration_clashes=lambda *_a: [])
        os.environ["ASD_MERGE_RUN_TIMEOUT"] = "1.5"
        one_w134 = [P(537, "feat/W-134-x", "stub/s")]
        runner_state("running", "2026-11-17T01:44:00Z")
        later(0.5, end_old_run)
        t0 = time.time()
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                why = settle(root, rk, "W-134", one_w134, "stub/s", [], lambda s_, t_: None)
            took = time.time() - t0
            check("queue: a start the runner refuses for a live run adopts that run; it ends done and the stack is re-checked at once",
                  why == "" and took < 1.4 and state["starts"] == 1, f"why={why!r} took={took:.2f} starts={state['starts']}")
            check("queue: ... and the adopted run is no new attempt", ((read_json(os.path.join(rdir, rk + ".merge.json"), {}) or {}).get("resolve_attempts") in (None, {})) or why == "", str(read_json(os.path.join(rdir, rk + ".merge.json"), {})))
            # 3. attempts survive a worker restart; a head moved by someone else resets them
            for f in (rk + ".merge.json", rk + ".runner.json"):
                if os.path.exists(os.path.join(rdir, f)): os.remove(os.path.join(rdir, f))
            hd = {"h": "sha0", "runs": 0}
            def pushing_start(*_a):
                hd["runs"] += 1; hd["h"] = f"sha{hd['runs']}"   # the resolve-merge run pushes
                return "started"
            globals().update(start_run=pushing_start, head_of=lambda *_a: hd["h"], conflicting=lambda r, n: (list(n), ""), wait_run=lambda *_a: "done")
            def one_settle():
                with contextlib.redirect_stdout(io.StringIO()):
                    return settle(root, rk, "W-134", one_w134, "stub/s", [], lambda s_, t_: None)
            w1 = one_settle(); runs1 = hd["runs"]
            w2 = one_settle()   # the worker restarted: a new settle in a new process
            check("queue: resolve-merge attempts persist: a restarted worker does not give a red stack fresh ones", runs1 == 2 and hd["runs"] == 2 and "still conflicting" in w2,
                  f"{w1!r} {w2!r} runs={hd['runs']}")
            check("queue: ... they are kept in the stack's merge.json", "resolve_attempts" in (read_json(os.path.join(rdir, rk + ".merge.json"), {}) or {}))
            hd["h"] = "owner-pushed"   # the owner (or the dossier's own run) moved the head
            one_settle()
            check("queue: a head moved for another reason resets the attempts", hd["runs"] == 4, str(hd["runs"]))
        finally:
            globals().update(saved_fns)
            del os.environ["ASD_MERGE_RUN_TIMEOUT"]
            for f in (rk + ".merge.json", rk + ".runner.json"):
                if os.path.exists(os.path.join(rdir, f)): os.remove(os.path.join(rdir, f))
        # 3b. W-197 (2026-11-24): the failed run of the dossier still held its runner lock and its failure question. The queue
        # started "attempt 2 of 2", the runner exited at once on the lock, that counted as spent, and a second question was asked.
        import fcntl
        wk = "repo.W-197"; wq = os.path.join(tmp, "questions")
        os.makedirs(wq, exist_ok=True)
        qf = os.path.join(wq, wk + ".Q9.jsonl")
        write_line_q = lambda o: open(qf, "a").write(json.dumps(o) + "\n")
        write_line_q({"kind": "question", "dossier": "W-197", "topic": "run-failure", "text": "The run of W-197 failed"})
        check("lock: an unanswered run-failure question is open", open_failure_questions(wk) == [wk + ".Q9"], str(open_failure_questions(wk)))
        lockfh = open(os.path.join(rdir, wk + ".runner.lock"), "a+"); fcntl.flock(lockfh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        check("lock: a held runner lock is seen", runner_lock_held(wk))
        w197 = [P(599, "feat/W-197-x", "stub/s")]
        wst = {"starts": 0, "asks": 0}
        def release():   # the owner answers the failure question, the failed runner ends and drops its lock
            write_line_q({"kind": "answer", "from": "owner", "option": "B"}); fcntl.flock(lockfh, fcntl.LOCK_UN)
        def counted_start(*_a):
            wst["starts"] += 1
            return "[run_dossier] repo.W-197 already has a runner (it holds the lock); this one exits" if wst.get("race") and wst["starts"] == 1 else "started"
        hd2 = {"n": 0}
        def ask_count(*_a): wst["asks"] += 1
        def fixing_wait(*_a):
            hd2["n"] += 1; return "done"
        conf = {"bad": True}
        globals().update(conflicting=lambda r, n: (list(n) if conf["bad"] else [], ""), start_run=counted_start, head_of=lambda *_a: f"x{hd2['n']}",
                         ask_resolve_failed=ask_count, wait_run=lambda *_a: (conf.update(bad=False) or "done"))
        os.environ["ASD_MERGE_RUN_TIMEOUT"] = "5"
        for f in (wk + ".merge.json", wk + ".runner.json"):
            if os.path.exists(os.path.join(rdir, f)): os.remove(os.path.join(rdir, f))
        saves: list = []
        real_save = save_attempts
        globals()["save_attempts"] = lambda k, a_, t_, kn: (saves.append(dict(a_)), real_save(k, a_, t_, kn))[1]
        later(0.4, release)
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                why = settle(root, wk, "W-197", w197, "stub/s", [], lambda s_, t_: None)
            check("queue: an open run-failure question and a held lock make the queue wait, with no start and no second question",
                  wst["asks"] == 0 and why == "" and wst["starts"] == 1, f"why={why!r} {wst}")
            check("queue: ... and only the one real start after the release is an attempt", max(x.get(599, 0) for x in saves) == 1, str(saves))
            # race: the lock is taken between the check and the start; the run never started, so it is no attempt
            os.remove(qf); conf["bad"] = True; wst.update(starts=0, asks=0, race=True); saves.clear()
            for f in (wk + ".merge.json",):
                if os.path.exists(os.path.join(rdir, f)): os.remove(os.path.join(rdir, f))
            seq = iter([False, True, False, False, False, False])   # free at the check, then held at the start, then free again
            real_held = runner_lock_held
            globals()["runner_lock_held"] = lambda k: next(seq, False)
            try:
                with contextlib.redirect_stdout(io.StringIO()):
                    why = settle(root, wk, "W-197", w197, "stub/s", [], lambda s_, t_: None)
            finally:
                globals()["runner_lock_held"] = real_held
            check("queue: a start that exits on the held lock is not a spent attempt", wst["starts"] == 2 and any(x.get(599) == 0 for x in saves) and max(x.get(599, 0) for x in saves) == 1 and wst["asks"] == 0 and why == "", f"why={why!r} {wst} {saves}")
        finally:
            globals().update(saved_fns)
            lockfh.close(); globals()["save_attempts"] = real_save
            del os.environ["ASD_MERGE_RUN_TIMEOUT"]
            for f in (wk + ".merge.json", wk + ".runner.json", wk + ".runner.lock"):
                if os.path.exists(os.path.join(rdir, f)): os.remove(os.path.join(rdir, f))
            if os.path.exists(qf): os.remove(qf)
        # 4. one integrate per stub: a second exits at once; the revive leaves a held stub alone
        ikey = repo_name(root) + ".stub-stub-lock"; held, _ = integrate_lock(ikey)
        for f in ("calls.txt",):
            if os.path.exists(os.path.join(fdir, f)): os.remove(os.path.join(fdir, f))
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc2 = integrate(root, "stub/stub-lock")
        said = open(os.path.join(rdir, ikey + ".jsonl")).read() if os.path.exists(os.path.join(rdir, ikey + ".jsonl")) else ""
        check("integrate: a second one for the same stub exits 0 at once and says another is running", rc2 == 0 and "another integrate" in said and not os.path.exists(os.path.join(fdir, "calls.txt")), f"{rc2} {said} {buf.getvalue()}")
        ipath = os.path.join(rdir, ikey + ".integrate.json")
        write_json(ipath, {"state": "merging", "root": root, "branch": "stub/stub-lock", "pid": 2 ** 22 + 9})
        with contextlib.redirect_stdout(io.StringIO()):
            revive()
        check("integrate: a revive does not start a second integrate while one holds the lock, and records the holder's pid", (read_json(ipath, {}) or {}).get("pid") == os.getpid(), str(read_json(ipath, {})))
        if held: held.close()
        os.remove(ipath)
        free, _ = integrate_lock(ikey); check("integrate: the lock is free once the holder ends", free is not None)
        if free: free.close()
        for f in (ikey + ".jsonl", ikey + ".integrate.lock"):
            if os.path.exists(os.path.join(rdir, f)): os.remove(os.path.join(rdir, f))
        os.environ["ASD_MERGE_POLL"] = "0.2"
        # ---- integrate: the finished dossiers through the queue, the one in flight left, then /stub ----
        for did_ in ("W-128", "W-130", "W-131", "W-140", "W-160", "W-161", "W-162"):   # a branch name names a dossier only when its file exists
            f_ = os.path.join(root, ".discovery", "dossiers", did_ + "-t.md")
            if not any(x.startswith(did_) for x in os.listdir(os.path.dirname(f_))):
                open(f_, "w").write(f"---\nid: {did_}\nstatus: pr\n---\n")
        for f in ("runs.txt", "calls.txt"):
            if os.path.exists(os.path.join(fdir, f)): os.remove(os.path.join(fdir, f))
        for n in os.listdir(rdir):
            if n.endswith((".merge.json", ".runner.json")) or n.startswith("repo.merge-queue"): os.remove(os.path.join(rdir, n))
        prs3 = [P(427, "feat/W-128-x", "stub/s"), P(429, "fix/W-128-deferred", "feat/W-128-x"), P(500, "feat/w-130-y", "stub/s"), P(600, "feat/W-131-z", "stub/s", True),
                P(700, "feat/W-140-a", "stub/s"), P(701, "fix/W-140-deferred", "feat/W-140-a", True), P(800, "docs/notes-12", "stub/s")]
        json.dump(prs3, open(os.path.join(fdir, "prs.json"), "w"))
        old = dict(os.environ)
        os.environ.update(env, ASD_RUNNER_BIN=fr, ASD_MERGE_POLL="0.2")
        RUNNER = fr   # integrate starts /stub in this process; the queue's worker reads ASD_RUNNER_BIN
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                rc = integrate(root, "stub/s")
        finally:
            os.environ.clear(); os.environ.update(old)
        runs = open(os.path.join(fdir, "runs.txt")).read().split("\n")
        ist = read_json(os.path.join(rdir, "repo.stub-s.integrate.json"), {})
        tl = [json.loads(x)["text"] for x in open(os.path.join(rdir, "repo.stub-s.jsonl"))]
        check("integrate: finished dossiers merge first, FIFO, then /stub starts; the draft stays in flight",
              rc == 0 and runs[:3] == ["W-128 after-merge", "W-130 after-merge", "stub-s stub"] and ist.get("state") == "stub" and ist.get("inflight") == ["W-131", "W-140"], str(runs) + str(ist))
        calls_i = open(os.path.join(fdir, "calls.txt")).read()
        check("integrate: a stack with a draft on top waits like a draft, instead of failing the integration", "stack merge 701" not in calls_i and "pr merge 700" not in calls_i)
        check("integrate: a PR no dossier owns is named, and left alone", "pr merge 800" not in calls_i and any("#800 docs/notes-12" in t for t in [json.loads(x)["text"] for x in open(os.path.join(rdir, "repo.stub-s.jsonl"))]))
        check("integrate: the stub's timeline gets its own merge-queue section and notes", tl[0] == "start /merge-queue stub/s" and any("W-131" in t for t in tl))
        spr = open(os.path.join(fdir, "stubpr.txt")).read() if os.path.exists(os.path.join(fdir, "stubpr.txt")) else ""
        check("integrate: step 1 opens the stub's PR into development, before any merge", "--base development --head stub/s" in spr and any("is closed" in t for t in tl))
        with contextlib.redirect_stdout(io.StringIO()):
            os.environ.update(env)
            late = plan(root, "W-131")
        saved_env = dict(os.environ)
        os.environ.update(env, ASD_RUNNER_BIN=fr, ASD_MERGE_POLL="0.1")
        try:
            json.dump([], open(os.path.join(fdir, "prs.json"), "w"))
            ip = os.path.join(rdir, "repo.stub-t.integrate.json")
            write_json(ip, {"state": "merging", "pid": 999999, "root": root, "branch": "stub/t", "finished": []})
            with contextlib.redirect_stdout(io.StringIO()):
                revive()
            for _ in range(100):
                if (read_json(ip, {}) or {}).get("state") == "stub": break
                time.sleep(0.1)
            rv = read_json(ip, {})
        finally:
            os.environ.clear(); os.environ.update(saved_env)
            json.dump(prs3, open(os.path.join(fdir, "prs.json"), "w"))
        check("resume: an integration whose process died is picked up again and still starts /stub", rv.get("state") == "stub", str(rv))
        saved_env = dict(os.environ)
        os.environ.update(env)
        try:
            for cmd, want in (("resolve-merge", False), ("after-merge", False), ("work-on", True), ("fix-deferred", True)):
                write_json(os.path.join(rdir, "repo.W-77.runner.json"), {"state": "running", "pid": os.getpid(), "command": cmd})
                check(f"integrate: a live {cmd} run {'keeps a stack in flight' if want else 'does not put a stack in flight'}", still_building("repo.W-77") is want)
            os.remove(os.path.join(rdir, "repo.W-77.runner.json"))
            two = [("W-1", {}), ("W-2", {}), ("W-3", {})]
            check("integrate: a restart keeps the stacks it picked before, even when one is not ready now", picked_stacks(two, lambda i, p: i == "W-3", ["W-1"]) == ["W-1", "W-3"])
            write_json(queue_path("repo"), {"items": [{"id": "W-77", "ts": "t"}], "root": root, "worker": os.getpid()})
            set_state("repo.W-77", state="resolving", position=1, error="", prs=[7], pid=os.getpid())
            with contextlib.redirect_stdout(io.StringIO()):
                enqueue(root, "W-77")
            kept = read_json(os.path.join(rdir, "repo.W-77.merge.json"), {})
            check("enqueue: a stack already in the queue keeps its state", kept.get("state") == "resolving" and kept.get("prs") == [7], str(kept))
            write_json(queue_path("repo"), {})
            # A merged record is replaced by a new press: fresh queued state, no stale prs or note, the old PRs named in the timeline.
            write_json(os.path.join(rdir, "repo.W-78.merge.json"), {"state": "merged", "prs": [595, 614], "ts": "2026-11-21T03:41:08Z", "note": "merged; /after-merge runs"})
            write_json(queue_path("repo"), {"items": [], "root": root, "worker": os.getpid()})
            with contextlib.redirect_stdout(io.StringIO()):
                enqueue(root, "W-78", by="eager")
            again = read_json(os.path.join(rdir, "repo.W-78.merge.json"), {})
            tl = open(os.path.join(rdir, "repo.W-78.jsonl"), encoding="utf-8").read()
            check("enqueue: over a merged record the dossier is queued afresh (no old prs or note) and the timeline names the earlier merge",
                  again.get("state") == "queued" and again.get("prs") == [] and not again.get("note") and "#595, #614" in tl and "new pull request" in tl, f"{again} {tl}")
            write_json(queue_path("repo"), {})
        finally:
            os.environ.clear(); os.environ.update(saved_env)
        check("integrate: a dossier not picked waits while the stub's PR is open", not late[0] and "being integrated" in late[1], str(late))
        # ---- integrate a stub nothing was merged into yet: GitHub refuses its PR, so it opens after the first merges ----
        def empty_stub(stub, prs_e):
            for f in ("runs.txt", "calls.txt", "stubpr.txt", "merged_any.txt"):
                if os.path.exists(os.path.join(fdir, f)): os.remove(os.path.join(fdir, f))
            for n in os.listdir(rdir):   # only this scenario's dossiers: later checks read W-128's and W-130's merge state
                if n.startswith(("repo.W-16", "repo.merge-queue")): os.remove(os.path.join(rdir, n))
            json.dump(prs_e, open(os.path.join(fdir, "prs.json"), "w"))
            saved = dict(os.environ)
            os.environ.update(env, ASD_RUNNER_BIN=fr, ASD_MERGE_POLL="0.2", FAKE_NO_COMMITS="1")
            try:
                with contextlib.redirect_stdout(io.StringIO()):
                    rc_e = integrate(root, stub)
            finally:
                os.environ.clear(); os.environ.update(saved)
            slug = stub.replace("/", "-")
            runs_e = open(os.path.join(fdir, "runs.txt")).read().split("\n") if os.path.exists(os.path.join(fdir, "runs.txt")) else []
            spr_e = open(os.path.join(fdir, "stubpr.txt")).read() if os.path.exists(os.path.join(fdir, "stubpr.txt")) else ""
            tl_e = [json.loads(x)["text"] for x in open(os.path.join(rdir, f"repo.{slug}.jsonl"))]
            return rc_e, runs_e, spr_e, tl_e, read_json(os.path.join(rdir, f"repo.{slug}.integrate.json"), {})
        rc_e, runs_e, spr_e, tl_e, st_e = empty_stub("stub/e", [P(910, "feat/W-160-a", "stub/e"), P(911, "feat/W-161-b", "stub/e", True)])
        check("integrate: an empty stub merges its finished dossiers first, then opens its PR and starts /stub",
              rc_e == 0 and runs_e[:2] == ["W-160 after-merge", "stub-e stub"] and "--head stub/e" in spr_e and st_e.get("state") == "stub"
              and any("nothing merged into it yet" in t for t in tl_e) and any("is closed" in t for t in tl_e), str((rc_e, runs_e, spr_e, st_e)))
        rc_n, runs_n, spr_n, tl_n, st_n = empty_stub("stub/n", [P(920, "feat/W-162-a", "stub/n", True)])
        check("integrate: an empty stub with nothing finished fails plainly, starts nothing and opens no PR",
              rc_n == 1 and "stub-n stub" not in runs_n and not spr_n and "nothing to integrate" in str(st_n.get("error")) and "W-162" in str(st_n.get("error")), str((rc_n, runs_n, st_n)))
        # ---- non-code PRs into development: a colliding dossier id is renumbered by script, then merged; the stub is untouched ----
        g = lambda *x, cwd=None: subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "-c", "commit.gpgsign=false", *x], cwd=cwd, capture_output=True, text=True)
        origin = os.path.join(tmp, "o.git"); clone = os.path.join(tmp, "clone"); pusher = os.path.join(tmp, "pusher")
        g("init", "-q", "--bare", "-b", "development", origin); g("clone", "-q", origin, pusher)
        os.makedirs(os.path.join(pusher, ".discovery", "dossiers"))
        open(os.path.join(pusher, ".discovery", "dossiers", "W-147-first.md"), "w").write("---\nid: W-147\n---\n# first\n")
        g("add", "-A", cwd=pusher); g("commit", "-q", "-m", "base", cwd=pusher); g("push", "-q", "origin", "HEAD:development", cwd=pusher)
        g("checkout", "-q", "-b", "plan/b", cwd=pusher)
        open(os.path.join(pusher, ".discovery", "dossiers", "W-147-second.md"), "w").write("---\nid: W-147\n---\n# second, W-147 here\n")
        g("add", "-A", cwd=pusher); g("commit", "-q", "-m", "plan b", cwd=pusher); g("push", "-q", "origin", "plan/b", cwd=pusher)
        g("clone", "-q", origin, clone); g("config", "commit.gpgsign", "false", cwd=clone); g("config", "user.email", "t@t", cwd=clone); g("config", "user.name", "t", cwd=clone)
        for f in ("calls.txt", "runs.txt", "stubpr.txt"):
            if os.path.exists(os.path.join(fdir, f)): os.remove(os.path.join(fdir, f))
        prs4 = [dict(P(437, "plan/b", "development"), files=[{"path": ".discovery/dossiers/W-147-second.md"}]),
                dict(P(434, "docs/r", "development"), files=[{"path": "docs/r.md"}, {"path": "reading.html"}]),
                dict(P(438, "docs/behind", "development"), files=[{"path": "docs/behind.md"}], mss="BEHIND")]
        json.dump(prs4, open(os.path.join(fdir, "prs.json"), "w"))
        old = dict(os.environ)
        os.environ.update(env, ASD_RUNNER_BIN=fr, ASD_MERGE_POLL="0.1", ASD_MERGE_BATCH="0")
        try:
            enqueue_pr(clone, 437); enqueue_pr(clone, 434); enqueue_pr(clone, 438)
            qp = queue_path(repo_name(clone))
            for _ in range(400):
                q = read_json(qp, {}) or {}
                if not q.get("items") and not (q.get("worker") and alive(q["worker"])): break
                time.sleep(0.1)
        finally:
            os.environ.clear(); os.environ.update(old)
        g("fetch", "-q", "origin", cwd=clone)
        tree = subprocess.run(["git", "-C", clone, "ls-tree", "--name-only", "origin/plan/b", ".discovery/dossiers/"], capture_output=True, text=True).stdout
        body = subprocess.run(["git", "-C", clone, "show", "origin/plan/b:.discovery/dossiers/W-148-second.md"], capture_output=True, text=True).stdout
        rn = repo_name(clone)
        s437 = read_json(os.path.join(rdir, f"{rn}.pr-437.merge.json"), {}); s434 = read_json(os.path.join(rdir, f"{rn}.pr-434.merge.json"), {})
        calls = open(os.path.join(fdir, "calls.txt")).read() if os.path.exists(os.path.join(fdir, "calls.txt")) else ""
        check("non-code PR: a colliding dossier id is renumbered on its branch, with its mentions, then merged",
              "W-148-second.md" in tree and "W-147-second.md" not in tree and "id: W-148" in body and "W-148 here" in body and s437.get("state") == "merged" and "pr merge 437 --merge" in calls, tree + str(s437))
        g("checkout", "-q", "development", cwd=pusher); g("pull", "-q", "origin", "development", cwd=pusher); g("checkout", "-q", "-b", "plan/same-file", cwd=pusher)
        open(os.path.join(pusher, ".discovery", "dossiers", "W-147-first.md"), "w").write("---\nid: W-147\n---\n# first, later\n")
        open(os.path.join(pusher, ".discovery", "dossiers", "W-147-other.md"), "w").write("---\nid: W-147\n---\n# someone else\n")
        g("add", "-A", cwd=pusher); g("commit", "-q", "-m", "same", cwd=pusher); g("push", "-q", "origin", "plan/same-file", cwd=pusher); g("fetch", "-q", "origin", cwd=clone)
        same = collisions(clone, "development", "plan/same-file", [])[0]
        check("ids: a dossier already on the base under the same file is the same dossier; another file with its id is a collision", [f for _, f in same] == [".discovery/dossiers/W-147-other.md"], str(same))
        g("checkout", "-q", "development", cwd=pusher); g("checkout", "-q", "-b", "plan/edit-only", cwd=pusher)
        open(os.path.join(pusher, ".discovery", "dossiers", "W-147-first.md"), "w").write("---\nid: W-147\nblocked_by: [W-211]\n---\n# first, edited\n")
        g("add", "-A", cwd=pusher); g("commit", "-q", "-m", "edit only", cwd=pusher); g("push", "-q", "origin", "plan/edit-only", cwd=pusher); g("fetch", "-q", "origin", cwd=clone)
        check("ids: a branch that only edits a dossier already on the base adds no id and is never renumbered", collisions(clone, "development", "plan/edit-only", [])[0] == [] and added_ids(clone, "development", "plan/edit-only")[0] == [], str(collisions(clone, "development", "plan/edit-only", [])))
        check("non-code PR: one that changes code is refused with why, never merged", s434.get("state") == "failed" and "changes code" in s434.get("error", "") and "pr merge 434" not in calls)
        s438 = read_json(os.path.join(rdir, f"{rn}.pr-438.merge.json"), {})
        check("non-code PR: a branch behind its base is updated, then merged, not dropped", s438.get("state") == "merged" and "pr update-branch 438" in calls
              and calls.index("pr update-branch 438") < calls.index("pr merge 438 --merge"), str(s438))
        check("non-code PR: the stub, its PR and /stub are never touched", "pr create" not in calls and "stack" not in calls and not os.path.exists(os.path.join(fdir, "runs.txt")))
        # ---- a batch of non-code PRs: one CI run, one merge; each member reads merged; trouble goes back to one at a time ----
        def batch_scenario(tag: str, **extra) -> tuple[dict, dict, str, str]:
            a, b = f"plan/{tag}-a", f"plan/{tag}-b"
            ia, ib = (500, 501) if tag == "ok" else (510, 511) if tag == "red" else (520, 521) if tag == "mark" else (530, 531) if tag == "moved" else (540, 541)
            for br, did in ((a, f"W-{ia}"), (b, f"W-{ia}")):   # the same new id on both: the later one is renumbered
                g("checkout", "-q", "development", cwd=pusher); g("pull", "-q", "origin", "development", cwd=pusher)
                g("checkout", "-q", "-b", br, cwd=pusher)
                open(os.path.join(pusher, ".discovery", "dossiers", f"{did}-{br.split('-')[-1]}.md"), "w").write(f"---\nid: {did}\n---\n# {br}\n")
                g("add", "-A", cwd=pusher); g("commit", "-q", "-m", br, cwd=pusher); g("push", "-q", "origin", br, cwd=pusher)
            if os.path.exists(os.path.join(fdir, "calls.txt")): os.remove(os.path.join(fdir, "calls.txt"))
            json.dump([dict(P(ia, a, "development"), files=[{"path": f".discovery/dossiers/W-{ia}-a.md"}]), dict(P(ib, b, "development"), files=[{"path": f".discovery/dossiers/W-{ia}-b.md"}])], open(os.path.join(fdir, "prs.json"), "w"))
            saved = dict(os.environ)
            os.environ.update(env, ASD_RUNNER_BIN=fr, ASD_MERGE_POLL="0.1", **extra)
            os.environ.pop("ASD_MERGE_BATCH", None)
            try:
                g("fetch", "-q", "origin", cwd=clone)
                enqueue_pr(clone, ia); enqueue_pr(clone, ib)
                for _ in range(400):
                    q = read_json(queue_path(repo_name(clone)), {}) or {}
                    if not q.get("items") and not (q.get("worker") and alive(q["worker"])): break
                    time.sleep(0.1)
            finally:
                os.environ.clear(); os.environ.update(saved)
            rn_ = repo_name(clone)
            cc = open(os.path.join(fdir, "calls.txt")).read()
            return read_json(os.path.join(rdir, f"{rn_}.pr-{ia}.merge.json"), {}), read_json(os.path.join(rdir, f"{rn_}.pr-{ib}.merge.json"), {}), cc, b
        sa, sb, cc, bb = batch_scenario("ok")
        g("fetch", "-q", "origin", "--prune", cwd=clone)
        names = subprocess.run(["git", "-C", clone, "ls-tree", "--name-only", f"origin/{bb}", ".discovery/dossiers/"], capture_output=True, text=True).stdout
        check("batch: the PRs go through one PR and one merge; each reads merged", "pr merge 901 --merge" in cc and "pr merge 500" not in cc and "pr merge 501" not in cc and sa.get("state") == "merged" and sb.get("state") == "merged" and "batch #901" in sa.get("note", ""), cc + str(sa))
        check("batch: an id two members share is renumbered on the later one", "W-500-a.md" not in names and "-b.md" in names and "W-500-b.md" not in names, names)
        check("batch: the batch branch's checks are the ones waited for; no member is merged on its own", "pr checks 901" in cc and "pr checks 500" not in cc, cc)
        sa, sb, cc, _ = batch_scenario("red", FAKE_BATCH_BUCKET="fail")
        check("batch: one that stays red is closed, and each member goes through on its own", "pr close 901" in cc and "pr merge 510 --merge" in cc and "pr merge 511 --merge" in cc and sa.get("state") == "merged" and sb.get("state") == "merged", cc + str(sa) + str(sb))
        sa, sb, cc, _ = batch_scenario("mark", FAKE_NO_AUTOMARK="1")
        check("batch: a member GitHub does not mark merged is closed saying where its work went", "pr close 520" in cc and "through #901" in cc and sa.get("state") == "merged", cc + str(sa))
        # ---- a network blip is waited out; a branch that moved on GitHub is renumbered from where it is ----
        saved_env = dict(os.environ)
        os.environ.update(env, FAKE_NET_BLIP="1", ASD_MERGE_NET_WAIT="0.1", ASD_MERGE_POLL="0.1")
        try:
            json.dump([dict(P(600, "docs/blip", "development"), mss="BEHIND")], open(os.path.join(fdir, "prs.json"), "w"))
            why = settle(clone, "repo.pr-600", "PR-600", [{"number": 600, "headRefName": "docs/blip", "baseRefName": "development"}], "development", [], lambda *a: None)
        finally:
            os.environ.clear(); os.environ.update(saved_env)
        check("network: a blip while updating a branch is retried, not a failure", why == "" and os.path.exists(os.path.join(fdir, "blipped")), why)
        g("checkout", "-q", "development", cwd=pusher); g("pull", "-q", "origin", "development", cwd=pusher); g("checkout", "-q", "-b", "plan/refused", cwd=pusher)
        open(os.path.join(pusher, "refused.md"), "w").write("x\n"); g("add", "-A", cwd=pusher); g("commit", "-q", "-m", "refused", cwd=pusher); g("push", "-q", "origin", "plan/refused", cwd=pusher)
        g("checkout", "-q", "development", cwd=pusher); open(os.path.join(pusher, "base-moved.md"), "w").write("x\n"); g("add", "-A", cwd=pusher); g("commit", "-q", "-m", "base moved", cwd=pusher); g("push", "-q", "origin", "HEAD:development", cwd=pusher)
        saved_env = dict(os.environ)
        os.environ.update(env, FAKE_UPDATE_REFUSED="1", ASD_MERGE_POLL="0.1")
        try:
            json.dump([dict(P(601, "plan/refused", "development"), mss="BEHIND")], open(os.path.join(fdir, "prs.json"), "w"))
            why2 = settle(clone, "repo.pr-601", "PR-601", [{"number": 601, "headRefName": "plan/refused", "baseRefName": "development"}], "development", [], lambda *a: None)
        finally:
            os.environ.clear(); os.environ.update(saved_env)
        g("fetch", "-q", "origin", cwd=clone)
        has = subprocess.run(["git", "-C", clone, "ls-tree", "--name-only", "origin/plan/refused"], capture_output=True, text=True).stdout
        check("update: a refused update-branch merges the base in by hand", why2 == "" and "base-moved.md" in has, why2 + has)
        g("checkout", "-q", "development", cwd=pusher); g("pull", "-q", "origin", "development", cwd=pusher); g("checkout", "-q", "-b", "feat/W-90-low", cwd=pusher)
        open(os.path.join(pusher, "low.md"), "w").write("low\n"); g("add", "-A", cwd=pusher); g("commit", "-q", "-m", "low", cwd=pusher); g("push", "-q", "origin", "feat/W-90-low", cwd=pusher)
        g("checkout", "-q", "-b", "fix/W-90-deferred", cwd=pusher); open(os.path.join(pusher, "top.md"), "w").write("top\n"); g("add", "-A", cwd=pusher); g("commit", "-q", "-m", "top", cwd=pusher); g("push", "-q", "origin", "fix/W-90-deferred", cwd=pusher)
        g("checkout", "-q", "feat/W-90-low", cwd=pusher); open(os.path.join(pusher, "updated.md"), "w").write("from the stub\n"); g("add", "-A", cwd=pusher); g("commit", "-q", "-m", "updated from the stub", cwd=pusher); g("push", "-q", "origin", "feat/W-90-low", cwd=pusher)
        saved_env = dict(os.environ)
        os.environ.update(env, ASD_MERGE_POLL="0.1")
        try:
            json.dump([P(602, "feat/W-90-low", "development"), P(603, "fix/W-90-deferred", "feat/W-90-low")], open(os.path.join(fdir, "prs.json"), "w"))
            why3 = settle(clone, "repo.W-90", "W-90", [{"number": 602, "headRefName": "feat/W-90-low", "baseRefName": "development"}, {"number": 603, "headRefName": "fix/W-90-deferred", "baseRefName": "feat/W-90-low"}], "development", [], lambda *a: None)
        finally:
            os.environ.clear(); os.environ.update(saved_env)
        g("fetch", "-q", "origin", cwd=clone)
        anc = subprocess.run(["git", "-C", clone, "merge-base", "--is-ancestor", "origin/feat/W-90-low", "origin/fix/W-90-deferred"]).returncode
        check("stack: the PR on top is brought up with the one below it, or the stack merge is refused", why3 == "" and anc == 0, why3)
        g("checkout", "-q", "development", cwd=pusher); g("pull", "-q", "origin", "development", cwd=pusher); g("checkout", "-q", "-b", "feat/W-91-low", cwd=pusher)
        open(os.path.join(pusher, "low91.md"), "w").write("low\n"); g("add", "-A", cwd=pusher); g("commit", "-q", "-m", "low", cwd=pusher); g("push", "-q", "origin", "feat/W-91-low", cwd=pusher)
        g("checkout", "-q", "-b", "fix/W-91-deferred", cwd=pusher); open(os.path.join(pusher, "clash91.md"), "w").write("top\n"); g("add", "-A", cwd=pusher); g("commit", "-q", "-m", "top", cwd=pusher); g("push", "-q", "origin", "fix/W-91-deferred", cwd=pusher)
        g("checkout", "-q", "development", cwd=pusher); open(os.path.join(pusher, "clash91.md"), "w").write("base\n"); g("add", "-A", cwd=pusher); g("commit", "-q", "-m", "base took clash91", cwd=pusher); g("push", "-q", "origin", "development", cwd=pusher)
        check("stack: a top that conflicts with the base reads as a clash, though each PR is clean against the one below",
              stack_clash(clone, "development", "fix/W-91-deferred") and not stack_clash(clone, "development", "fix/W-90-deferred"))
        g("checkout", "-q", "development", cwd=pusher); g("pull", "-q", "origin", "development", cwd=pusher); g("checkout", "-q", "-b", "plan/stale", cwd=pusher)
        open(os.path.join(pusher, ".discovery", "dossiers", "W-147-stale.md"), "w").write("---\nid: W-147\n---\n# stale\n")
        g("add", "-A", cwd=pusher); g("commit", "-q", "-m", "stale", cwd=pusher); g("push", "-q", "origin", "plan/stale", cwd=pusher)
        g("fetch", "-q", "origin", cwd=clone)
        open(os.path.join(pusher, "moved.md"), "w").write("moved\n")
        g("add", "-A", cwd=pusher); g("commit", "-q", "-m", "moved on GitHub", cwd=pusher); g("push", "-q", "origin", "plan/stale", cwd=pusher)
        moved = fix_ids(clone, "development", "plan/stale", [("W-147", ".discovery/dossiers/W-147-stale.md")], False)
        check("ids: a branch that moved on GitHub is renumbered from where it is, not refused", moved == "", moved)
        # ---- several collisions in one branch: distinct ids, the branch's own cross-references follow, the base's do not ----
        dd = os.path.join(pusher, ".discovery", "dossiers")
        g("checkout", "-q", "development", cwd=pusher); g("pull", "-q", "origin", "development", cwd=pusher)
        open(os.path.join(dd, "W-149-held.md"), "w").write("---\nid: W-149\nblocked_by: [W-147]\n---\n# held, W-149\n")
        g("add", "-A", cwd=pusher); g("commit", "-q", "-m", "base holds W-149", cwd=pusher); g("push", "-q", "origin", "development", cwd=pusher)
        g("checkout", "-q", "-b", "plan/multi", cwd=pusher)
        open(os.path.join(dd, "W-147-mx.md"), "w").write("---\nid: W-147\n---\n# mx W-147\n")
        open(os.path.join(dd, "W-149-my.md"), "w").write("---\nid: W-149\nblocked_by: [W-147]\n---\n# my\n")
        open(os.path.join(dd, "W-171-sib.md"), "w").write("---\nid: W-171\nblocked_by: [W-147, W-149, W-1470]\n---\n# sib\n")
        g("add", "-A", cwd=pusher); g("commit", "-q", "-m", "plan multi", cwd=pusher); g("push", "-q", "origin", "plan/multi", cwd=pusher)
        g("fetch", "-q", "origin", cwd=clone)
        multi = fix_ids(clone, "development", "plan/multi", [("W-147", ".discovery/dossiers/W-147-mx.md"), ("W-149", ".discovery/dossiers/W-149-my.md")], False)
        g("fetch", "-q", "origin", cwd=clone)
        show = lambda f: subprocess.run(["git", "-C", clone, "show", f"origin/plan/multi:.discovery/dossiers/{f}"], capture_output=True, text=True).stdout
        mtree = subprocess.run(["git", "-C", clone, "ls-tree", "--name-only", "origin/plan/multi", ".discovery/dossiers/"], capture_output=True, text=True).stdout
        newids = re.findall(r"W-(\d+)-m[xy]\.md", mtree)
        check("ids: two collisions in one branch get two distinct new ids, none the base holds", multi == "" and len(newids) == 2 and len(set(newids)) == 2 and not {"147", "149"} & set(newids), multi + mtree)
        nx, ny = (re.search(r"W-(\d+)-mx", mtree), re.search(r"W-(\d+)-my", mtree))
        sib = show("W-171-sib.md")
        check("ids: a blocked_by in a sibling dossier on the branch follows the renumbering; a longer id is left alone",
              nx and ny and f"blocked_by: [W-{nx.group(1)}, W-{ny.group(1)}, W-1470]" in sib, sib)
        check("ids: a reference inside the renamed file follows too", ny and f"blocked_by: [W-{nx.group(1)}]" in show(f"W-{ny.group(1)}-my.md"), show("W-149-my.md"))
        held = show("W-149-held.md")
        check("ids: a file the branch leaves unchanged keeps its references to the base's dossiers", "id: W-149" in held and "blocked_by: [W-147]" in held and "# held, W-149" in held, held)
        check("ids: a chain of renumberings is applied simultaneously", remap_ids("W-1 W-2 W-3", {"W-1": "W-2", "W-2": "W-3"}) == "W-2 W-3 W-3")
        sa, sb, cc, _ = batch_scenario("again", FAKE_BATCH_EXISTS="1")
        check("resume: a batch PR already open from before an interruption is carried on with", "pr merge 901 --merge" in cc and sa.get("state") == "merged" and sb.get("state") == "merged", cc + str(sa))
        saved_env = dict(os.environ)
        os.environ.update(env, ASD_MERGE_POLL="0.1", ASD_MERGE_BATCH="0", ASD_RUNNER_BIN=fr)
        try:
            rq = repo_name(clone)
            json.dump([dict(P(700, "docs/landed", "development"), files=[{"path": "docs/l.md"}], state="MERGED"), dict(P(701, "docs/next", "development"), files=[{"path": "docs/n.md"}])], open(os.path.join(fdir, "prs.json"), "w"))
            write_json(queue_path(rq), {"items": [{"id": "#700", "pr": 700, "ts": now()}, {"id": "#701", "pr": 701, "ts": now()}], "root": clone, "worker": 999999})
            revive()
            for _ in range(300):
                q = read_json(queue_path(rq), {}) or {}
                if not q.get("items") and not (q.get("worker") and alive(q["worker"])): break
                time.sleep(0.1)
            s700 = read_json(os.path.join(rdir, f"{rq}.pr-700.merge.json"), {}); s701 = read_json(os.path.join(rdir, f"{rq}.pr-701.merge.json"), {})
        finally:
            os.environ.clear(); os.environ.update(saved_env)
        check("resume: a queue whose worker died restarts, reads what landed meanwhile as merged and carries on", s700.get("state") == "merged" and s701.get("state") == "merged", str(s700) + str(s701))
        saved_env = dict(os.environ)
        os.environ.update(env, ASD_MERGE_POLL="0.1", ASD_RUNNER_BIN=fr)
        try:
            rq = repo_name(clone)
            json.dump([dict(P(710, "feat/W-77-x", "stub/2026-11-03"), state="MERGED")], open(os.path.join(fdir, "prs.json"), "w"))
            write_json(os.path.join(rdir, f"{rq}.W-77.merge.json"), {"state": "merging", "prs": [710], "pid": 999999})
            work_one(clone, "W-77")
            s77 = read_json(os.path.join(rdir, f"{rq}.W-77.merge.json"), {})
        finally:
            os.environ.clear(); os.environ.update(saved_env)
        check("resume: a stack that merged before the worker died reads merged, not failed", s77.get("state") == "merged" and not s77.get("error"), str(s77))
        sa, sb, cc, _ = batch_scenario("moved", FAKE_NO_AUTOMARK="1", FAKE_HEAD="pushed-during-the-batch")
        check("batch: a member pushed to during the batch is left open and says so, never closed", "pr close 530" not in cc and sa.get("state") == "failed" and "changed" in sa.get("error", ""), cc + str(sa))
        check("queue: both read merged", read_json(os.path.join(rdir, "repo.W-128.merge.json"), {}).get("state") == "merged" and read_json(os.path.join(rdir, "repo.W-130.merge.json"), {}).get("state") == "merged", str(read_json(os.path.join(rdir, "repo.W-128.merge.json"), {})) + str(read_json(os.path.join(rdir, "repo.W-130.merge.json"), {})))
        RUNNER = saved
    # ---- head-of-line: a head that waits on its agent does not hold the dossiers behind it ----
    with tempfile.TemporaryDirectory() as t3:
        PP = lambda n, head, base: {"number": n, "headRefName": head, "baseRefName": base, "isDraft": False, "title": "t", "url": "u"}
        rd3, root3 = os.path.join(t3, "r"), os.path.join(t3, "repo")
        os.makedirs(rd3); os.makedirs(root3)
        saved_env = {k: os.environ.get(k) for k in ("ASD_RUNS_DIR", "ASD_MERGE_AHEAD_EVERY", "ASD_MERGE_AHEAD", "ASD_MERGE_POLL")}
        saved_fns = {n: globals()[n] for n in ("work_one", "gh", "dossier_prs", "plan", "conflicting", "behind", "checks_state", "settle", "merge", "start_run", "dossier_id")}
        os.environ.update(ASD_RUNS_DIR=rd3, ASD_MERGE_AHEAD_EVERY="0", ASD_MERGE_POLL="0.01")
        os.environ.pop("ASD_MERGE_AHEAD", None)
        try:
            def run_queue(ids, clean, ticks=6, stacks=None):
                """The worker over `ids`; the head waits `ticks` polls on its run. (order merged, queue seen mid-wait, states mid-wait)"""
                order, seen = [], {}
                prs_ = [PP(1, "feat/W-1-x", "stub/s")] + [PP(n, f"feat/W-{n}-x", "stub/s") for n in (2, 4, 5)] + [PP(3, "feat/W-3-x", "feat/W-1-x")]
                for f in os.listdir(rd3): os.remove(os.path.join(rd3, f))
                write_json(queue_path("repo"), {"root": root3, "items": [{"id": i, "ts": now()} for i in ids]})
                for n, i in enumerate(ids, 1): set_state(f"repo.{i}", state="queued", position=n, prs=[])
                globals()["gh"] = lambda _r, *a, **_k: (0, json.dumps(prs_))
                globals()["dossier_prs"] = lambda _r: {}
                def fake_work_one(r, d, ahead=None):
                    if ahead is None and d == ids[0]:   # the head: its resolve-merge run is queued for a slot, polled like wait_run does
                        set_state(f"repo.{d}", state="resolving", prs=[1], pid=os.getpid())
                        for _ in range(ticks):
                            idle_tick(); time.sleep(0.005)
                        seen["items"] = [i["id"] for i in (read_json(queue_path("repo"), {}) or {}).get("items") or []]
                        seen["states"] = {i: read_json(os.path.join(rd3, f"repo.{i}.merge.json"), {}) for i in ids}
                    elif ahead is not None:
                        n = int(d.split("-")[1]); chain = [p for p in prs_ if p["number"] in ((n, 1) if d == "W-3" else (n,))]
                        if not ahead(chain) or d not in clean:
                            return "skip"
                    order.append(d + ("*" if ahead is not None else ""))
                    set_state(f"repo.{d}", state="merged", pid=None)
                    return "merged"
                globals()["work_one"] = fake_work_one
                rc = worker(root3)
                return rc, order, seen
            rc, order, seen = run_queue(["W-1", "W-2", "W-3", "W-4", "W-5"], clean={"W-2", "W-3", "W-4"})
            check("head of line: the clean dossiers behind a head waiting on its agent merge meanwhile, the one that needs an agent too stays",
                  rc == 0 and order[:2] == ["W-2*", "W-4*"] and "W-5*" not in order, str(order))
            check("head of line: the head keeps position 1 and the queue its order while the others leave",
                  seen.get("items") == ["W-1", "W-3", "W-5"] and seen["states"]["W-1"].get("state") == "resolving" and seen["states"]["W-3"].get("position") == 2
                  and seen["states"]["W-5"].get("position") == 3, str(seen))
            check("head of line: a stacked child behind its resolving parent is not merged ahead of it (it shares the parent's PR)",
                  "W-3*" not in order and order.index("W-1") < order.index("W-3"), str(order))
            check("head of line: the head finishing its resolve is merged next, then the child, then the rest in place",
                  order == ["W-2*", "W-4*", "W-1", "W-3", "W-5"], str(order))
            rc, order, seen = run_queue(["W-1", "W-2", "W-4"], clean={"W-2", "W-4"}, ticks=3)
            os.environ["ASD_MERGE_AHEAD"] = "0"
            rc0, order0, _ = run_queue(["W-1", "W-2", "W-4"], clean={"W-2", "W-4"}, ticks=3)
            os.environ.pop("ASD_MERGE_AHEAD")
            check("head of line: ASD_MERGE_AHEAD=0 restores the strict order", order == ["W-2*", "W-4*", "W-1"] and order0 == ["W-1", "W-2", "W-4"], f"{order} {order0}")
            # a PR based on a PR still ahead in the queue (different dossier, its own branch name) is not merged either
            globals()["gh"] = lambda _r, *a, **_k: (0, json.dumps([PP(1, "feat/W-1-x", "stub/s"), PP(9, "feat/W-9-x", "feat/W-1-x")]))
            write_json(os.path.join(rd3, "repo.W-1.merge.json"), {"prs": [1]})
            guard_ = ahead_guard(root3, [{"id": "W-1"}])
            check("head of line: the guard refuses a chain whose base is a branch of an item ahead, allows an independent one",
                  not guard_([PP(9, "feat/W-9-x", "feat/W-1-x")]) and guard_([PP(8, "feat/W-8-x", "stub/s")]) and not ahead_guard(root3, [{"id": "W-1"}, {"id": "#7", "pr": 7}])([PP(7, "docs/x", "stub/s")]))
            # work_one in ahead mode: only what is mergeable right now, never an agent, never a failed record
            seen_calls = []
            def settle_(*a, **k): seen_calls.append(("settle", k.get("use_agent"))); return ""
            def merge_(r, d, *a, **k): write_json(os.path.join(rd3, f"repo.{d}.merge.json"), {"state": "merged", "prs": [11]}); return 0
            globals().update(plan=lambda r, d: ([PP(11, "feat/W-6-x", "stub/s")], ""), conflicting=lambda r, n: ([], ""), behind=lambda r, n: [],
                             checks_state=lambda r, n: "green", settle=settle_, merge=merge_, dossier_id=lambda r, d, c: d,
                             start_run=lambda r, d, c, n: seen_calls.append(("run", d, c)) or "", work_one=saved_fns["work_one"])
            globals()["gh"] = lambda _r, *a, **_k: seen_calls.append(("gh",) + a) or (0, "")
            for f in os.listdir(rd3): os.remove(os.path.join(rd3, f))
            res = work_one(root3, "W-6", lambda c: True)
            check("head of line: a clean, up to date, green dossier merges ahead without an agent and /after-merge starts",
                  res == "merged" and ("settle", False) in seen_calls and ("run", "W-6", "after-merge") in seen_calls, f"{res} {seen_calls}")
            outcomes = {}
            for name, patch in (("pending", {"checks_state": lambda r, n: "pending"}), ("red", {"checks_state": lambda r, n: "red"}),
                                ("conflict", {"conflicting": lambda r, n: ([11], "")}), ("behind", {"behind": lambda r, n: [11]}),
                                ("guard", {}), ("settle", {"settle": lambda *a, **k: "checks red"})):
                for f in os.listdir(rd3): os.remove(os.path.join(rd3, f))
                seen_calls.clear(); globals().update(conflicting=lambda r, n: ([], ""), behind=lambda r, n: [], checks_state=lambda r, n: "green", settle=settle_); globals().update(patch)
                outcomes[name] = (work_one(root3, "W-6", (lambda c: False) if name == "guard" else (lambda c: True)), (read_json(os.path.join(rd3, "repo.W-6.merge.json"), {}) or {}).get("state"),
                                  ("run", "W-6", "resolve-merge") in seen_calls, any(c[0] == "gh" and "update-branch" in c for c in seen_calls))
            check("head of line: pending or red checks, a conflict, a guard refusal or a failed settle leave it untouched (no failed record, no agent)",
                  all(v[:3] == ("skip", None, False) for v in outcomes.values()), str(outcomes))
            check("head of line: a stack behind its base is only brought up to date; its checks run, a later pass merges it", outcomes["behind"][3] and not outcomes["pending"][3], str(outcomes))
        finally:
            globals().update(saved_fns)
            for k, v in saved_env.items():
                if v is None: os.environ.pop(k, None)
                else: os.environ[k] = v
    # ---- a merged dossier's failure questions close by themselves ----
    with tempfile.TemporaryDirectory() as t2:
        qd, rd = os.path.join(t2, "q"), os.path.join(t2, "r")
        os.makedirs(qd); os.makedirs(rd)
        saved_env = {k: os.environ.get(k) for k in ("ASD_QUESTIONS_DIR", "ASD_RUNS_DIR")}
        os.environ["ASD_QUESTIONS_DIR"], os.environ["ASD_RUNS_DIR"] = qd, rd
        try:
            def mkq(n: int, text: str, dossier: str = "W-150", **extra) -> str:
                qid = f"repo.{dossier}.Q{n}"
                append_line(os.path.join(qd, qid + ".jsonl"), {"ts": now(), "from": "agent", "kind": "question", "text": text, "dossier": dossier, "repo": "repo", **extra})
                return qid
            def lines_of(qid: str) -> list[dict]:
                return [json.loads(x) for x in open(os.path.join(qd, qid + ".jsonl")) if x.strip()]
            fail_txt = "The run of W-150 failed at stage 4: PR shows CONFLICTING"
            a = mkq(1, fail_txt, topic="run-failure")
            b = mkq(2, fail_txt)   # an old one: no marker, the text only
            c = mkq(3, "Two designs for the cache: keep one map or two?", qkind="design")
            d_ = mkq(4, fail_txt, topic="run-failure")
            append_line(os.path.join(qd, d_ + ".jsonl"), {"ts": now(), "from": "owner", "kind": "answer", "option": "A", "text": "Retry"})
            e = mkq(5, fail_txt, topic="run-failure")   # a live run waits on this one
            write_json(os.path.join(rd, "repo.W-150.runner.json"), {"state": "failed", "pid": os.getpid(), "questions": [e]})
            o = mkq(1, "The run of W-151 failed at stage 2: x", dossier="W-151", topic="run-failure")   # W-151 is not merged
            set_state("repo.W-151", state="failed")
            check("failure questions: not merged, nothing closes", lines_of(o)[-1]["kind"] == "question")
            set_state("repo.W-150", state="merged")
            la, lb = lines_of(a), lines_of(b)
            check("failure questions: a merged dossier's open failure question is closed with an automatic answer",
                  len(la) == 2 and la[-1]["from"] == "runner" and la[-1]["accepted"] is True and la[-1]["option"] == "B" and la[-1]["text"].startswith("Closed automatically: W-150 was merged"), str(la))
            check("failure questions: ... also an old one found by its text", len(lb) == 2 and lb[-1]["from"] == "runner", str(lb))
            check("failure questions: a non-failure question stays open", len(lines_of(c)) == 1)
            check("failure questions: an answered one is untouched", len(lines_of(d_)) == 2 and lines_of(d_)[-1]["from"] == "owner")
            check("failure questions: one a live run waits on stays open", len(lines_of(e)) == 1)
            check("failure questions: a different dossier's stays open", len(lines_of(o)) == 1)
            set_state("repo.W-150", state="merged")
            check("failure questions: closing happens once", len(lines_of(a)) == 2 and len(lines_of(b)) == 2)
            write_json(os.path.join(rd, "repo.W-150.runner.json"), {"state": "running", "pid": 2 ** 22 + 5, "questions": [e]})
            set_state("repo.W-150", state="merged")
            check("failure questions: a dead run no longer holds its question", len(lines_of(e)) == 2 and lines_of(e)[-1]["from"] == "runner")
        finally:
            for k, v in saved_env.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
    leaked = {d: sorted(now_ - before_real[d]) for d, now_ in listing().items() if now_ - before_real[d]}
    check("no file was created in the real questions or runs dir during the selftest", not leaked, str(leaked))
    print("FAILED: " + ", ".join(fails) if fails else "all ok")
    return 1 if fails else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    ap.add_argument("id", nargs="?")
    ap.add_argument("--root")
    ap.add_argument("--method", choices=["merge", "squash", "rebase"], default="merge")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--enqueue", action="store_true", help="add to the repository's merge queue (FIFO) and start its worker")
    ap.add_argument("--by", choices=sorted(SOURCES), default="button", help="with --enqueue: who asked for the merge, for the dossier's timeline")
    ap.add_argument("--unqueue", action="store_true", help="take out of the merge queue")
    ap.add_argument("--worker", action="store_true", help="work the repository's merge queue to the end")
    ap.add_argument("--revive", action="store_true", help="restart any merge queue with work whose worker is gone")
    ap.add_argument("--enqueue-pr", metavar="N", type=int, help="add a non-code PR into development to the merge queue")
    ap.add_argument("--integrate", metavar="STUB", help="merge every finished dossier on the stub through the queue, then start /stub")
    ap.add_argument("--stub-dossiers", metavar="STUB", help="print the dossiers with an open pull request into the stub, one per line")
    ap.add_argument("--kanban-dossiers", action="store_true", help="print the kanban dossiers with an open pull request into development, one per line")
    ap.add_argument("--dev-ci", action="store_true", help="print the state of development's CI (green|pending|red|unknown)")
    ap.add_argument("--carry-over", metavar="STUB", help="after STUB merged: move its leftover open PRs to the accepting stub (exit 1: try again later)")
    ap.add_argument("--migration-clashes", metavar="HEAD", help="print the migrations this branch adds under a number already taken, with the next free number (needs --base; bare branch names)")
    ap.add_argument("--base", help="the branch --migration-clashes compares with")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args(argv)
    if a.selftest:
        return selftest()
    if a.migration_clashes and a.base and a.root:
        root_ = os.path.abspath(a.root)
        found, unreadable = migration_scan(root_, a.base, a.migration_clashes)
        if unreadable:   # never "no clash" for a ref that was not read
            print(unreadable, file=sys.stderr)
            return 2
        taken: dict[str, int] = {}   # two clashes in one folder get two different numbers
        for f in found:
            path = f.split(" has the number of ")[0]
            free = next_free_migration(root_, a.base, path)
            if free and path.rsplit("/", 1)[0] in taken:
                free = str(int(free) + taken[path.rsplit("/", 1)[0]]).zfill(len(free))
            if free:
                taken[path.rsplit("/", 1)[0]] = taken.get(path.rsplit("/", 1)[0], 0) + 1
            print(f + (f"; next number free on the base and every open pull request head: {free}" if free else ""))
        if not found:
            print("no clash")
        return 1 if found else 0
    if a.revive:
        return revive()
    if a.worker and a.root:
        return worker(os.path.abspath(a.root))
    if a.enqueue_pr and a.root:
        return enqueue_pr(os.path.abspath(a.root), a.enqueue_pr)
    if a.stub_dossiers and a.root:
        print("\n".join(stub_dossiers(os.path.abspath(a.root), a.stub_dossiers)))
        return 0
    if a.kanban_dossiers and a.root:
        print("\n".join(kanban_dossiers(os.path.abspath(a.root))))
        return 0
    if a.dev_ci and a.root:
        print(dev_ci(os.path.abspath(a.root)))
        return 0
    if a.carry_over and a.root:
        done, lines = carry_over(os.path.abspath(a.root), a.carry_over)
        print("\n".join(lines))
        return 0 if done else 1
    if a.integrate and a.root:
        if not re.fullmatch(r"(?:stub|release)/[\w.-]+", a.integrate):
            print("merge_dossier: --integrate takes a stub branch, e.g. stub/2026-11-03", file=sys.stderr); return 2
        return integrate(os.path.abspath(a.root), a.integrate)
    if not a.id or not a.root or not re.fullmatch(r"[A-Za-z][A-Za-z0-9]*-\d+", a.id):
        ap.print_usage(sys.stderr)
        return 2
    if a.enqueue:
        return enqueue(os.path.abspath(a.root), a.id, a.by)
    if a.unqueue:
        return unqueue(os.path.abspath(a.root), a.id)
    return merge(os.path.abspath(a.root), a.id, a.method, a.dry_run)


if __name__ == "__main__":
    sys.exit(main())
