#!/usr/bin/env python3
"""Phase 5's package merges in one call: each implementer branch into X, in
`Depends on` order, with the `OWNED_PATHS` scope check before every merge.

Per package, in order:

    1  `git diff --name-only HEAD...<branch>` lists only paths inside the
       package's owned paths (a file, a directory, or a glob). A path outside
       them that matches an `--expected-hook-output GLOB` is accepted and listed;
       any other path stops the run BEFORE that merge, as a `TOUCHED_BEYOND`
       candidate: exit 2, the paths printed. Accepting or rejecting it, and
       fixing the package table, is Opus's ruling.
    2  `git merge --no-ff --no-edit <branch>`. A conflict stops the run: the
       conflicted paths are printed, the merge is aborted, exit 2. A conflict
       proves the package table was wrong; logging it and fixing
       `## Work packages` is Opus's.
    3  with `--package-worktree PKG=PATH`, the merged package's worktree is
       removed through remove_worktree.py (mode on only).

Packages with no `--branch` (the test rows the authors already committed) are
skipped and listed. Earlier merges stay in place when a later one stops the run;
the evidence names the last merged package and the head.

Usage:
    ASD_DRIVER_MERGE_PACKAGES=on python3 merge_packages.py --worktree X \\
        --dossier W-014 --branch P1=feat/w014-p1 --branch P2=feat/w014-p2 \\
        [--package-worktree P1=/path/to/p1] [--expected-hook-output 'docs/**/anchors.json'] \\
        [--log DOSSIER_FILE]

Shadow mode merges in a throwaway worktree of X's HEAD and removes nothing.
Modes, exit codes and the evidence file: see driver_common.py.
"""

from __future__ import annotations

import fnmatch
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import driver_common as dc  # noqa: E402


def parse_package_table(text: str) -> list[dict]:
    """Rows of `## Work packages`: id (first token of the name), name, owned paths, depends-on ids."""
    m = re.search(r"^## Work packages\s*$(.*?)(?=^## |\Z)", text, re.M | re.S)
    if not m:
        raise dc.Surprise("the dossier has no `## Work packages` section")
    rows = []
    for line in m.group(1).splitlines():
        line = line.strip()
        if not line.startswith("|") or re.fullmatch(r"[|\-:\s]+", line):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) < 3 or cells[0].lower() in ("package", ""):
            continue
        owned = [p.strip().strip("`") for p in cells[1].split(",") if p.strip() and p.strip() != "—"]
        deps = [t.strip() for t in re.split(r"[,\s]+", cells[2]) if t.strip() and t.strip() != "—"]
        rows.append({"id": cells[0].split()[0], "name": cells[0], "owned": owned, "depends": deps})
    if not rows:
        raise dc.Surprise("`## Work packages` has no rows")
    return rows


def merge_order(rows: list[dict]) -> list[dict]:
    """Table order, with every package after the packages it depends on."""
    by_id = {r["id"]: r for r in rows}
    for r in rows:
        for dep in r["depends"]:
            if dep not in by_id:
                raise dc.Surprise(f"package {r['id']} depends on {dep}, which is not a row of the table")
    done: list[dict] = []
    seen: set[str] = set()
    pending = list(rows)
    while pending:
        ready = [r for r in pending if all(x in seen for x in r["depends"])]
        if not ready:
            raise dc.Surprise("`Depends on` has a cycle among: " + ", ".join(r["id"] for r in pending))
        nxt = ready[0]
        done.append(nxt)
        seen.add(nxt["id"])
        pending.remove(nxt)
    return done


def inside(path: str, owned: list[str]) -> bool:
    p = os.path.normpath(path)
    for o in owned:
        n = os.path.normpath(o)
        if p == n or p.startswith(n.rstrip("/") + "/") or fnmatch.fnmatch(p, o):
            return True
    return False


def do_merges(d: dc.Driver, wt: str, order: list[dict], branches: dict, args) -> None:
    st = dc.require_clean(wt)
    d.facts.update({"x_branch": st["branch"], "head_before": st["head"]})
    merged: list[str] = []
    skipped: list[str] = []
    accepted_hook: dict[str, list[str]] = {}
    for r in order:
        pid = r["id"]
        br = branches.get(pid)
        if not br:
            skipped.append(pid)
            continue
        rc, _ = dc.git(wt, "rev-parse", "--verify", "-q", br + "^{commit}")
        if rc != 0:
            raise dc.Surprise(f"{pid}: branch {br} does not exist")
        rc, out = dc.git(wt, "diff", "--name-only", f"HEAD...{br}")
        if rc != 0:
            raise dc.Surprise(f"{pid}: git diff HEAD...{br} failed: {out.strip()[:120]}")
        files = [f for f in out.splitlines() if f.strip()]
        beyond = [f for f in files if not inside(f, r["owned"])]
        hook = [f for f in beyond if any(fnmatch.fnmatch(f, g) for g in args.expected_hook_output)]
        beyond = [f for f in beyond if f not in hook]
        if hook:
            accepted_hook[pid] = hook
        if beyond:
            d.facts["touched_beyond"] = {"package": pid, "paths": beyond[:30], "owned": r["owned"]}
            d.facts["merged_before_stop"] = merged
            raise dc.Surprise(
                f"{pid} ({br}) changes {len(beyond)} path(s) outside its OWNED_PATHS, not merged: "
                f"{', '.join(beyond[:6])}. TOUCHED_BEYOND is your ruling: accept (update the package table) or reject")
        rc, out = dc.git(wt, "merge", "--no-ff", "--no-edit", br)
        if rc != 0:
            _, cf = dc.git(wt, "diff", "--name-only", "--diff-filter=U")
            paths = [p for p in cf.splitlines() if p.strip()]
            dc.git(wt, "merge", "--abort")
            d.facts["conflicted_paths"] = paths[:40]
            d.facts["merged_before_stop"] = merged
            raise dc.Surprise(
                f"{pid} ({br}) conflicts in {len(paths)} path(s): {', '.join(paths[:6]) or out.strip()[:120]}. "
                "Merge aborted. A conflict means the package table was wrong: resolve it, log it, fix ## Work packages")
        _, head = dc.git(wt, "rev-parse", "--short", "HEAD")
        merged.append(pid)
        d.step(f"merge {pid}", "ok", f"{br} -> {head.strip()} ({len(files)} file(s), all owned)")
    d.facts["merged"] = merged
    d.facts["skipped_no_branch"] = skipped
    d.facts["accepted_hook_outputs"] = accepted_hook
    _, head = dc.git(wt, "rev-parse", "HEAD")
    d.facts["head_after"] = head.strip()
    if skipped:
        d.step("skipped", "ok", "no --branch for: " + ", ".join(skipped))
    if accepted_hook:
        d.finding("hook-output paths accepted by --expected-hook-output (run the repo's checker over them): " +
                  "; ".join(f"{k}: {', '.join(v[:3])}" for k, v in accepted_hook.items()))


def run(d: dc.Driver, args) -> int:
    if not args.worktree or not args.branch:
        raise dc.Surprise("--worktree and at least one --branch PKG=BRANCH are required")
    wt = os.path.abspath(args.worktree)
    dossier_file = args.log or os.path.join(args.root, ".discovery", "dossiers", f"{args.dossier}.md")
    if not os.path.isfile(dossier_file):
        raise dc.Surprise(f"dossier file not found: {dossier_file} (give --dossier or --log)")
    order = merge_order(parse_package_table(open(dossier_file, encoding="utf-8").read()))
    branches = {}
    for spec in args.branch:
        pid, _, br = spec.partition("=")
        if not br:
            raise dc.Surprise(f"--branch wants PKG=BRANCH, got {spec!r}")
        if pid not in {r["id"] for r in order}:
            raise dc.Surprise(f"--branch names {pid}, which is not a row of ## Work packages")
        branches[pid] = br
    d.facts["order"] = [r["id"] for r in order]
    if d.shadow:
        dc.require_clean(wt)
        with dc.throwaway_worktree(wt, "HEAD") as tmp:
            d.facts["shadow_worktree"] = "throwaway; X untouched"
            do_merges(d, tmp, order, branches, args)
    else:
        do_merges(d, wt, order, branches, args)
        removed = []
        for spec in args.package_worktree or []:
            pid, _, path = spec.partition("=")
            if pid not in d.facts["merged"]:
                continue
            rc, out = dc.script("remove_worktree.py", "--root", args.root, path, "--branch", cwd=args.root)
            if rc != 0:
                raise dc.Surprise(f"remove_worktree.py {path} exited {rc}: {' | '.join(dc.cap(out, 3))}")
            removed.append(pid)
        if removed:
            d.step("remove worktrees", "ok", ", ".join(removed))
    d.summary = f"merged {len(d.facts['merged'])} of {len(branches)} package branch(es) in Depends-on order: {', '.join(d.facts['merged'])}"
    return 1 if d.findings else 0


def build_parser(ap) -> None:
    ap.add_argument("--worktree", help="the build worktree X")
    ap.add_argument("--branch", action="append", default=[], metavar="PKG=BRANCH", help="a package's implementer branch (repeatable)")
    ap.add_argument("--package-worktree", action="append", default=[], metavar="PKG=PATH", help="remove this package's worktree after its merge (mode on)")
    ap.add_argument("--expected-hook-output", action="append", default=[], metavar="GLOB", help="EXPECTED_HOOK_OUTPUTS glob accepted outside OWNED_PATHS")


def selftest() -> int:
    import tempfile

    c = dc.Checker("merge_packages")
    table = ("## Work packages\n\n| Package | Owned paths | Depends on |\n|---|---|---|\n"
             "| P2 reload | src/reload.py | P1 |\n| P1 flush | src/flush.py, src/flush/ | — |\n| UT unit | tests/test_a.py | — |\n\n## Acceptance criteria\n")
    rows = parse_package_table(table)
    c("table parses three rows", [r["id"] for r in rows] == ["P2", "P1", "UT"])
    ids = [r["id"] for r in merge_order(rows)]
    c("depends-on order puts P1 before P2", ids.index("P1") < ids.index("P2"))
    try:
        merge_order([{"id": "A", "depends": ["B"], "owned": []}, {"id": "B", "depends": ["A"], "owned": []}])
        c("a cycle is a surprise", False)
    except dc.Surprise:
        c("a cycle is a surprise", True)
    c("inside: dir and file", inside("src/flush/a.py", ["src/flush/"]) and inside("src/flush.py", ["src/flush.py"]) and not inside("src/other.py", ["src/flush.py"]))

    with tempfile.TemporaryDirectory() as tmp:
        main = os.path.join(tmp, "main")
        dc.fixture_repo(main)
        dc.fixture_commit(main, "README", "r\n", "init")
        dc.fixture_write(main, ".discovery/dossiers/W-1.md", "---\nid: W-1\n---\n" + table + "\n## Build log\n")
        x = os.path.join(tmp, "x")
        dc.fixture_git(main, "worktree", "add", "-q", "-b", "feat/x", x)
        # branches forked from X's head
        dc.fixture_git(x, "branch", "pkg-p1")
        dc.fixture_git(x, "branch", "pkg-p2")
        dc.fixture_git(x, "branch", "pkg-p3")
        for br, rel, extra in (("pkg-p1", "src/flush.py", None), ("pkg-p2", "src/reload.py", "src/stray.py")):
            w = os.path.join(tmp, br)
            dc.fixture_git(main, "worktree", "add", "-q", w, br)
            dc.fixture_commit(w, rel, "x = 1\n", br)
            if extra:
                dc.fixture_commit(w, extra, "s = 1\n", br + " stray")
        env = {"ASD_DRIVER_MERGE_PACKAGES": "on"}
        shadow = {"ASD_DRIVER_MERGE_PACKAGES": "shadow"}
        base = ["--worktree", x, "--dossier", "W-1", "--root", main, "--branch", "P1=pkg-p1", "--branch", "P2=pkg-p2"]
        head0 = dc.fixture_git(x, "rev-parse", "HEAD").strip()

        rc, out = dc.run_self("merge_packages.py", *base, env={})
        c("off refuses", rc == 3)
        rc, out = dc.run_self("merge_packages.py", *base, env=shadow)
        c("shadow stops at the TOUCHED_BEYOND path", rc == 2 and "src/stray.py" in out and "TOUCHED_BEYOND" in out)
        c("shadow leaves X untouched", dc.fixture_git(x, "rev-parse", "HEAD").strip() == head0)
        rc, out = dc.run_self("merge_packages.py", *base, "--expected-hook-output", "src/stray.py", env=env)
        c("an expected hook output is accepted and listed, exit 1", rc == 1 and "hook-output" in out)
        c("both packages merged in X", os.path.isfile(os.path.join(x, "src", "flush.py")) and os.path.isfile(os.path.join(x, "src", "reload.py")))
        merges = dc.fixture_git(x, "log", "--merges", "--format=%s").strip().splitlines()
        c("merge order P1 then P2 in the log", len(merges) == 2 and "pkg-p1" in merges[1] and "pkg-p2" in merges[0])

        # a conflict: p3 edits a file P1 owns, X has also changed it
        w3 = os.path.join(tmp, "p3")
        dc.fixture_git(main, "worktree", "add", "-q", w3, "pkg-p3")
        dc.fixture_commit(w3, "src/flush.py", "x = 3\n", "p3 edit")
        dc.fixture_commit(x, "src/flush.py", "x = 4\n", "x edit")
        rows3 = ("## Work packages\n\n| Package | Owned paths | Depends on |\n|---|---|---|\n| P3 third | src/flush.py | — |\n\n## Build log\n")
        dc.fixture_write(main, ".discovery/dossiers/W-3.md", "---\nid: W-3\n---\n" + rows3)
        before = dc.fixture_git(x, "rev-parse", "HEAD").strip()
        rc, out = dc.run_self("merge_packages.py", "--worktree", x, "--dossier", "W-3", "--root", main, "--branch", "P3=pkg-p3", env=env)
        c("a conflict exits 2, names the path, aborts", rc == 2 and "src/flush.py" in out and dc.worktree_state(x)["operation"] == [])
        c("X unchanged after the aborted merge", dc.fixture_git(x, "rev-parse", "HEAD").strip() == before)
        rc, out = dc.run_self("merge_packages.py", "--worktree", x, "--dossier", "W-3", "--root", main, "--branch", "ZZ=pkg-p3", env=env)
        c("an unknown package is a surprise", rc == 2 and "not a row" in out)
    return c.report()


if __name__ == "__main__":
    sys.exit(dc.drive("merge_packages", None, build_parser, run, selftest))
