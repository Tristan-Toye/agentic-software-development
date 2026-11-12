#!/usr/bin/env python3
"""Phase 9, steps 1-2 and 5 as one call: sync the target branch into the build
branch, settle the ids, check the merged tree, and print the evidence.

The chain, in the order `references/work-on/phase-9.md` lists it:

    1  the worktree X is clean and not mid-operation
    2  git fetch origin <target>; git merge origin/<target>
       (a conflict: the merge is aborted, the conflicted paths are printed,
       exit 2 — resolving code is Opus's, through the implementer)
    3  validate_pipeline.py --finalize-ids --base origin/<target>; every
       `updated dossier` line it prints gets its diff stat in the evidence
    4  validate_pipeline.py (whole tree); git restore docs/adr/index.md; commit
       what finalize changed, by name
    5  the three "git's silence is not agreement" checks: no conflict markers
       in tracked files, no duplicated ADR number, the gate list over the
       merged tree (`--gates FILE`, the compile per gated configuration)
    6  the Phase 3 `CENSUS:` lines the target's side of the merge touched, as
       candidates to re-derive; and whether the sync was bookkeeping-only
    7  optional: `--wait-ci PR` (wait_ci.py) and `--remove-worktree PATH...`
       (remove_worktree.py), after everything above is clean

It prints evidence and decides nothing: whether a bookkeeping-only sync lets
the old suite evidence stand, whether a census line really moved, what goes in
the PR description and the `DEFERRED:` list stay with the orchestrator. It does
not push, open the PR or comment on Jira.

Usage:
    ASD_DRIVER_FINALIZE_PR=on python3 finalize_pr.py --worktree X --target main \\
        --dossier W-014 [--gates FILE] [--wait-ci N] [--log DOSSIER_FILE]

Modes, exit codes and the evidence file: see driver_common.py. In shadow mode
every step runs in a throwaway worktree of X's HEAD, so X is untouched.
"""

from __future__ import annotations

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import driver_common as dc  # noqa: E402

BOOKKEEPING = re.compile(r"^(docs/adr/|docs/learned-rules)")


def census_paths(dossier_file: str | None) -> list[tuple[str, str]]:
    """`CENSUS: <path>:<line> — <owner>` lines from the dossier: (path, whole line)."""
    if not dossier_file or not os.path.isfile(dossier_file):
        return []
    out = []
    for line in open(dossier_file, encoding="utf-8").read().splitlines():
        m = re.search(r"CENSUS:\s*(\S+?)(?::\d+)?\s+—", line)
        if m:
            out.append((m.group(1), line.strip()[:160]))
    return out


def duplicate_adr_numbers(root: str) -> list[str]:
    adr = os.path.join(root, "docs", "adr")
    if not os.path.isdir(adr):
        return []
    seen: dict[str, list[str]] = {}
    for n in sorted(os.listdir(adr)):
        m = re.match(r"^(\d{4})-.*\.md$", n)
        if m:
            seen.setdefault(m.group(1), []).append(n)
    return [f"{k}: {', '.join(v)}" for k, v in seen.items() if len(v) > 1]


def sync(d: dc.Driver, wt: str, target: str, args) -> None:
    st = dc.require_clean(wt)
    pre = st["head"]
    d.facts.update({"branch": st["branch"], "head_before": pre, "target": target})

    rc, out = dc.git(wt, "fetch", "origin", target)
    if rc != 0:
        raise dc.Surprise(f"git fetch origin {target} failed: {out.strip()[:160]}")
    d.step("fetch", "ok", f"origin/{target}")

    rc, out = dc.git(wt, "merge", "--no-edit", f"origin/{target}")
    if rc != 0:
        _, conflicted = dc.git(wt, "diff", "--name-only", "--diff-filter=U")
        paths = [p for p in conflicted.splitlines() if p.strip()]
        d.facts["conflicted_paths"] = paths[:40]
        dc.git(wt, "merge", "--abort")
        code = [p for p in paths if not BOOKKEEPING.match(p)]
        raise dc.Surprise(
            f"merge of origin/{target} conflicts in {len(paths)} path(s) "
            f"({len(code)} outside docs/adr and docs/learned-rules): {', '.join(paths[:6]) or out.strip()[:120]}. "
            "The merge was aborted; resolve it by hand through the implementer")
    _, head = dc.git(wt, "rev-parse", "HEAD")
    d.facts["head_after_merge"] = head.strip()
    d.step("merge", "ok", "already up to date" if head.strip() == pre else f"merged origin/{target}")

    # What the target's side brought in: classify the sync.
    _, names = dc.git(wt, "diff", "--name-only", f"{pre}..HEAD")
    changed = [n for n in names.splitlines() if n.strip()]
    d.facts["synced_paths"] = len(changed)
    d.facts["bookkeeping_only_sync"] = bool(changed) and all(BOOKKEEPING.match(n) for n in changed)
    d.facts["code_paths_synced"] = [n for n in changed if not BOOKKEEPING.match(n)][:20]

    rc, out = dc.script("validate_pipeline.py", "--root", wt, "--finalize-ids", "--base", f"origin/{target}", cwd=wt)
    if rc != 0:
        raise dc.Surprise(f"validate_pipeline.py --finalize-ids exited {rc}: {' | '.join(dc.cap(out, 3))}")
    updated = re.findall(r"^updated dossier (\S+)", out, re.M)
    notices = re.findall(r"^NOTICE\s+(\S+)", out, re.M)
    d.facts["finalize_output"] = dc.cap(out, 12)
    stats = {}
    for name in updated[:10]:
        _, stat = dc.git(wt, "diff", "--stat", "HEAD", "--", f".discovery/dossiers/{name}")
        stats[name] = dc.cap(stat, 3)
    d.facts["updated_dossiers"] = stats
    d.step("finalize-ids", "ok", f"{len(updated)} updated dossier line(s), {len(notices)} NOTICE (informational)")
    if updated:
        d.finding(f"updated dossier(s): {', '.join(updated[:6])} — read each diff stat in the evidence before you keep it")

    rc, out = dc.script("validate_pipeline.py", "--root", wt, cwd=wt)
    defects = [ln for ln in out.splitlines() if ln.startswith("DEFECT")]
    if rc not in (0, 1):
        raise dc.Surprise(f"validate_pipeline.py exited {rc}: {' | '.join(dc.cap(out, 3))}")
    if defects:
        d.step("validate", "finding", f"{len(defects)} DEFECT")
        for ln in defects[:5]:
            d.finding(ln)
    else:
        d.step("validate", "ok", "no DEFECT")

    rc, _ = dc.git(wt, "cat-file", "-e", "HEAD:docs/adr/index.md")
    if rc == 0:
        dc.git(wt, "restore", "docs/adr/index.md")
    st2 = dc.worktree_state(wt)
    if st2["tracked_changes"]:
        names = [ln[3:].strip().split(" -> ")[-1] for ln in st2["tracked_changes"]]
        rc, out = dc.git(wt, "add", "--", *names)
        if rc != 0:
            raise dc.Surprise(f"could not stage finalize's changes by name: {out.strip()[:120]}")
        rc, out = dc.git(wt, "commit", "-m", f"Finalize ADR and LRN ids after syncing {target}")
        if rc != 0:
            raise dc.Surprise(f"commit of finalize's changes failed: {out.strip()[:160]}")
        d.step("commit", "ok", f"committed {len(names)} path(s) finalize changed")
    else:
        d.step("commit", "skipped", "finalize changed nothing")

    # The three checks Git's silence does not replace.
    rc, out = dc.git(wt, "grep", "-n", "-E", "^(<<<<<<<|>>>>>>>) ", "--", ".", ":!*.md")
    if rc == 0:
        raise dc.Surprise(f"conflict markers in tracked files: {' | '.join(dc.cap(out, 4))}")
    d.step("markers", "ok", "no conflict markers in tracked non-markdown files")
    dups = duplicate_adr_numbers(wt)
    if dups:
        d.step("adr-numbers", "finding", "; ".join(dups[:3]))
        d.finding("duplicated ADR number(s): " + "; ".join(dups[:3]))
    else:
        d.step("adr-numbers", "ok", "no duplicated ADR number")
    if args.gates:
        rc, out = dc.script("run_gates.py", args.gates, "--root", wt, cwd=wt)
        if rc == 0:
            d.step("gates", "ok", f"{args.gates} green over the merged tree")
        elif rc == 1:
            d.step("gates", "finding", "a gate failed")
            for ln in dc.cap(out, 8):
                d.finding("gates: " + ln)
        else:
            raise dc.Surprise(f"run_gates.py exited {rc}: {' | '.join(dc.cap(out, 3))}")
    else:
        d.step("gates", "skipped", "no --gates FILE given; the compile per gated configuration is yours")

    cands = []
    dossier_file = args.log or os.path.join(args.root, ".discovery", "dossiers", f"{args.dossier}.md") if args.dossier or args.log else None
    for path, line in census_paths(dossier_file):
        if any(n == path or n.startswith(path.rstrip("/") + "/") for n in changed):
            cands.append(line)
    d.facts["census_candidates"] = cands[:20]
    d.step("census", "ok" if not cands else "finding", f"{len(cands)} CENSUS pin(s) on paths the sync touched")
    if cands:
        d.finding(f"{len(cands)} CENSUS pin(s) sit on paths the target changed — re-derive each (evidence file lists them)")


def run(d: dc.Driver, args) -> int:
    if not args.worktree or not args.target:
        raise dc.Surprise("--worktree and --target are required")
    wt = os.path.abspath(args.worktree)
    if d.shadow:
        dc.require_clean(wt)
        with dc.throwaway_worktree(wt, "HEAD") as tmp:
            d.facts["shadow_worktree"] = "throwaway; X untouched"
            sync(d, tmp, args.target, args)
    else:
        sync(d, wt, args.target, args)
    if not d.findings:
        if args.wait_ci and not d.shadow:
            rc, out = dc.script("wait_ci.py", "--pr", str(args.wait_ci), cwd=wt, timeout=3600)
            d.facts["wait_ci"] = dc.cap(out, 12)
            if rc == 0:
                d.step("wait-ci", "ok", f"PR {args.wait_ci} green")
            elif rc == 1:
                d.step("wait-ci", "finding", f"PR {args.wait_ci} red")
                d.finding(f"CI on PR {args.wait_ci} is red: " + (dc.cap(out, 1) or [""])[0])
            else:
                raise dc.Surprise(f"wait_ci.py exited {rc}: {' | '.join(dc.cap(out, 3))}")
        if args.remove_worktree and not d.shadow:
            rc, out = dc.script("remove_worktree.py", "--root", args.root, *args.remove_worktree, "--branch", cwd=args.root)
            if rc != 0:
                raise dc.Surprise(f"remove_worktree.py exited {rc}: {' | '.join(dc.cap(out, 3))}")
            d.step("remove-worktree", "ok", f"{len(args.remove_worktree)} path(s)")
    bk = "bookkeeping-only" if d.facts.get("bookkeeping_only_sync") else "code synced"
    d.summary = (f"sync of origin/{args.target} {'clean' if not d.findings else str(len(d.findings)) + ' finding(s)'} "
                 f"({bk}; {d.facts.get('synced_paths', 0)} path(s))")
    return 1 if d.findings else 0


def build_parser(ap) -> None:
    ap.add_argument("--worktree", help="the build worktree X (committed mode: dossier already committed)")
    ap.add_argument("--target", help="the branch the PR merges into")
    ap.add_argument("--gates", help="gates file for run_gates.py over the merged tree")
    ap.add_argument("--wait-ci", type=int, metavar="PR", help="after a clean sync, wait_ci.py --pr PR (mode on only)")
    ap.add_argument("--remove-worktree", nargs="+", metavar="PATH", help="after everything is clean, remove these fan-out worktrees (mode on only)")


def selftest() -> int:
    import tempfile

    c = dc.Checker("finalize_pr")
    with tempfile.TemporaryDirectory() as tmp:
        origin = os.path.join(tmp, "origin.git")
        dc.fixture_git(tmp, "init", "-q", "--bare", "-b", "main", origin)
        main = os.path.join(tmp, "main")
        dc.fixture_git(tmp, "clone", "-q", origin, main)
        dc.fixture_git(main, "config", "user.email", "t@example.com")
        dc.fixture_git(main, "config", "user.name", "t")
        dc.fixture_commit(main, "src/a.py", "a = 1\n", "init")
        dc.fixture_git(main, "push", "-q", "origin", "main")
        xwt = os.path.join(tmp, "x")
        dc.fixture_git(main, "worktree", "add", "-q", "-b", "feat/x", xwt)
        dc.fixture_commit(xwt, "src/feature.py", "f = 1\n", "feature")
        # the target moves: a file outside the feature's paths
        dc.fixture_commit(main, "src/other.py", "o = 1\n", "target advance")
        dc.fixture_git(main, "push", "-q", "origin", "main")
        env = {"ASD_DRIVER_FINALIZE_PR": "on"}
        shadow_env = {"ASD_DRIVER_FINALIZE_PR": "shadow"}

        rc, out = dc.run_self("finalize_pr.py", "--worktree", xwt, "--target", "main", env={})
        c("off refuses with exit 3", rc == 3 and "manual steps" in out)

        head_x = dc.fixture_git(xwt, "rev-parse", "HEAD").strip()
        rc, out = dc.run_self("finalize_pr.py", "--worktree", xwt, "--target", "main", "--dossier", "W-1", "--root", main, env=shadow_env)
        c("shadow runs clean on a clean sync", rc == 0 and "verdict=clean" in out)
        c("shadow leaves X untouched", dc.fixture_git(xwt, "rev-parse", "HEAD").strip() == head_x)
        shadow_dir = os.path.join(main, ".git", "asd-drivers", "driver-shadow", "W-1")
        c("shadow writes evidence under driver-shadow/<dossier>/", os.path.isdir(shadow_dir) and len(os.listdir(shadow_dir)) == 1)

        rc, out = dc.run_self("finalize_pr.py", "--worktree", xwt, "--target", "main", "--dossier", "W-1", "--root", main, env=env)
        c("on merges the target and exits 0", rc == 0 and "verdict=clean" in out)
        c("the merge landed", os.path.isfile(os.path.join(xwt, "src", "other.py")))
        c("bookkeeping_only is false for a code sync", '"bookkeeping_only_sync": false' in open(
            os.path.join(main, ".git", "asd-drivers", "driver-evidence", "W-1", sorted(os.listdir(os.path.join(main, ".git", "asd-drivers", "driver-evidence", "W-1")))[0])).read())

        # a conflict
        dc.fixture_commit(xwt, "src/a.py", "a = 2\n", "x edits a")
        dc.fixture_commit(main, "src/a.py", "a = 3\n", "target edits a")
        dc.fixture_git(main, "push", "-q", "origin", "main")
        before = dc.fixture_git(xwt, "rev-parse", "HEAD").strip()
        rc, out = dc.run_self("finalize_pr.py", "--worktree", xwt, "--target", "main", "--dossier", "W-1", "--root", main, env=env)
        c("a conflict exits 2 naming the path", rc == 2 and "src/a.py" in out and "STOPPED AT" in out)
        c("the merge it started was aborted", not os.path.exists(os.path.join(xwt, ".git")) or dc.worktree_state(xwt)["operation"] == [])
        c("X is where it was", dc.fixture_git(xwt, "rev-parse", "HEAD").strip() == before)

        # dirty tree
        dc.fixture_write(xwt, "src/feature.py", "f = 99\n")
        rc, out = dc.run_self("finalize_pr.py", "--worktree", xwt, "--target", "main", "--dossier", "W-1", "--root", main, env=env)
        c("a dirty tree exits 2", rc == 2 and "uncommitted" in out)
        os.makedirs(os.path.join(xwt, "docs", "adr"), exist_ok=True)
        for n in ("0001-a.md", "0001-b.md"):
            dc.fixture_write(xwt, "docs/adr/" + n, "x\n")
        c("duplicate_adr_numbers reports a clash", duplicate_adr_numbers(xwt) == ["0001: 0001-a.md, 0001-b.md"])
        dc.fixture_write(main, ".discovery/dossiers/W-1.md", "CENSUS: src/other.py:3 — P1\nCENSUS: none — x\n")
        c("census_paths parses pins", census_paths(os.path.join(main, ".discovery/dossiers/W-1.md"))[0][0] == "src/other.py")
    return c.report()


if __name__ == "__main__":
    sys.exit(dc.drive("finalize_pr", None, build_parser, run, selftest))
