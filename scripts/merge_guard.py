#!/usr/bin/env python3
"""merge_guard.py — fail a merge whose resolution lost lines no side removed.

A conflict resolution must keep what each side meant. This checks it
mechanically, per file, with line multisets (order-free, so moved lines are
fine; blank and whitespace-only lines are ignored):

    expected(L) = ours(L) + theirs(L) - base(L)

is how many times line L should appear in the merge result when every side's
additions survive and only removals some side made are applied. A line whose
count in the result is below that, and that NEITHER side removed (ours(L) >=
base(L) and theirs(L) >= base(L)), was dropped by the resolution itself. That
is the defect: e.g. a stub sync merge that replaced 16 anchor lines with a
stray "o".

When both sides changed the same lines, keeping one side's version is a real
choice. A dropped line that only one side had is therefore classed by whether
any version of it survives, comparing lines with every digit run masked (so a
renumbered anchor, id or count matches its other version):

- `replaced`: a version survives (a renumbering, a combined line, the same
  line extended at its end). Listed, passes.
- `gone`: no version survives (e.g. anchor lines one side renumbered and the
  resolution deleted outright). Passes only when the merge message names the
  file (or a directory prefix ending in `/`) on a line
  `Dropped: <path> — <why>` (a pending merge reads
  .git/MERGE_MSG).

Lines in the result that appear in neither parent nor the base are listed as
`invented` (information only: a resolution may write new lines; a stray
single character usually is not one).

Usage:
    merge_guard.py                      # the merge in progress (MERGE_HEAD), working tree as result
    merge_guard.py --commit <sha>       # a finished merge commit
    merge_guard.py --selftest

Exit: 0 clean (or every drop explained), 1 unexplained drops, 2 usage error.
"""
from __future__ import annotations

import argparse
import collections
import os
import re
import subprocess
import sys
import tempfile

Counter = collections.Counter
DIGITS = re.compile(r"\d+")


def git(cwd: str, *args: str, check: bool = True) -> str:
    p = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    if check and p.returncode:
        raise SystemExit(f"merge_guard: git {' '.join(args)}: {p.stderr.strip()}")
    return p.stdout


def blob(cwd: str, rev: str, path: str) -> str | None:
    p = subprocess.run(["git", "show", f"{rev}:{path}"], cwd=cwd, capture_output=True)
    if p.returncode:
        return None
    try:
        return p.stdout.decode("utf-8")
    except UnicodeDecodeError:
        return None  # binary: not line-checked


def lines(text: str | None) -> Counter:
    if text is None:
        return Counter()
    return Counter(l.rstrip() for l in text.splitlines() if l.strip())


def analyse(base: str | None, ours: str | None, theirs: str | None, result: str | None) -> dict:
    b, o, t, m = lines(base), lines(ours), lines(theirs), lines(result)
    lost, gone, replaced, invented = [], [], [], []
    masked = {DIGITS.sub("#", l) for l in m}
    for l in set(b) | set(o) | set(t):
        exp = o[l] + t[l] - b[l]
        miss = exp - m[l]
        if miss <= 0:
            continue
        if o[l] >= b[l] and t[l] >= b[l] and b[l] > 0:
            lost.append((l, miss))
        elif DIGITS.sub("#", l) in masked or any(r.startswith(l) for r in m if len(r) > len(l)):
            replaced.append((l, miss))
        else:
            gone.append((l, miss))
    for l in m:
        if not (b[l] or o[l] or t[l]):
            invented.append(l)
    return {"lost": sorted(lost), "gone": sorted(gone), "replaced": sorted(replaced),
            "invented": sorted(invented)}


def explained(msg: str) -> set[str]:
    return {m.group(1).strip() for m in re.finditer(r"^Dropped:\s*(\S+)", msg, re.M)}


def check(cwd: str, commit: str | None) -> tuple[int, str]:
    if commit:
        parents = git(cwd, "rev-list", "--parents", "-n1", commit).split()[1:]
        if len(parents) != 2:
            return 2, f"merge_guard: {commit} is not a two-parent merge"
        ours, theirs = parents
        msg = git(cwd, "log", "-1", "--format=%B", commit)
        read_result = lambda p: blob(cwd, commit, p)
        paths = set(git(cwd, "diff", "--name-only", ours, commit).split()) | set(
            git(cwd, "diff", "--name-only", theirs, commit).split())
    else:
        gitdir = git(cwd, "rev-parse", "--git-dir").strip()
        gitdir = gitdir if os.path.isabs(gitdir) else os.path.join(cwd, gitdir)
        if not os.path.exists(os.path.join(gitdir, "MERGE_HEAD")):
            return 2, "merge_guard: no merge in progress (pass --commit <sha>)"
        ours, theirs = "HEAD", "MERGE_HEAD"
        mp = os.path.join(gitdir, "MERGE_MSG")
        msg = open(mp).read() if os.path.exists(mp) else ""

        def read_result(p: str) -> str | None:
            f = os.path.join(cwd, p)
            try:
                return open(f, encoding="utf-8").read()
            except (OSError, UnicodeDecodeError):
                return None
        paths = set(git(cwd, "diff", "--name-only", "HEAD").split()) | set(
            git(cwd, "diff", "--name-only", "MERGE_HEAD").split())
    base = git(cwd, "merge-base", ours, theirs).strip()
    ok_files = explained(msg)
    out, bad = [], 0
    for p in sorted(paths):
        bo, oo, to = blob(cwd, base, p), blob(cwd, ours, p), blob(cwd, theirs, p)
        if oo is not None and to is not None and oo == to:
            continue  # both sides agree; any change is the resolution's own edit, checked below anyway
        r = analyse(bo, oo, to, read_result(p))
        if not (r["lost"] or r["gone"] or r["replaced"]):
            continue
        why = any(p == e or (e.endswith("/") and p.startswith(e)) for e in ok_files)
        failed = bool(r["lost"]) or (bool(r["gone"]) and not why)
        bad += failed
        tag = "LOST" if failed else ("EXPLAINED" if r["gone"] else "REPLACED")
        out.append(f"{tag} {p}")
        for l, n in r["lost"]:
            out.append(f"  lost (kept by both sides){' x%d' % n if n > 1 else ''}: {l[:160]}")
        for l, n in r["gone"]:
            out.append(f"  gone (one side's line, no version left){' x%d' % n if n > 1 else ''}: {l[:160]}")
        for l, n in r["replaced"]:
            out.append(f"  replaced (another version kept){' x%d' % n if n > 1 else ''}: {l[:160]}")
        for l in r["invented"][:10]:
            out.append(f"  invented: {l[:160]}")
    if bad:
        out.append(
            f"merge_guard: {bad} file(s) lost lines. Restore them. A `gone` line (only one side had "
            "it) may go only with a `Dropped: <path> — <why>` line in the merge message; a `lost` "
            "line (both sides kept it) can never be dropped.")
        return 1, "\n".join(out)
    out.append("merge_guard: OK, nothing lost")
    return 0, "\n".join(out)


def selftest() -> int:
    def run(*a, cwd):
        subprocess.run(["git", *a], cwd=cwd, check=True, capture_output=True)

    def put(d, name, text):
        open(os.path.join(d, name), "w").write(text)

    fails = []
    with tempfile.TemporaryDirectory() as d:
        run("init", "-q", "-b", "main", cwd=d)
        run("config", "user.email", "t@t", cwd=d)
        run("config", "user.name", "t", cwd=d)
        put(d, "a.md", "head\nkeep1\nkeep2\nkeep3\nz\ntail\n")
        run("add", ".", cwd=d); run("commit", "-qm", "base", cwd=d)
        run("switch", "-qc", "side", cwd=d)
        put(d, "a.md", "head\nkeep1\nkeep2\nkeep3\nside-z\ntail\nside-add\n")
        run("commit", "-qam", "side", cwd=d)
        run("switch", "-q", "main", cwd=d)
        put(d, "a.md", "head\nkeep1\nkeep2\nkeep3\nmain-z\ntail\n")
        run("commit", "-qam", "main", cwd=d)
        subprocess.run(["git", "merge", "side"], cwd=d, capture_output=True)  # conflicts on z
        # 1. faithful resolution: both z's combined, side-add kept
        put(d, "a.md", "head\nkeep1\nkeep2\nkeep3\nmain-z\nside-z\ntail\nside-add\n")
        rc, out = check(d, None)
        rc == 0 or fails.append("faithful resolution should pass:\n" + out)
        # 2. the ddce2923 shape: kept-by-both lines replaced by a stray "o"
        put(d, "a.md", "head\no\n \nmain-z\nside-z\ntail\nside-add\n")
        rc, out = check(d, None)
        (rc == 1 and "lost (kept by both sides): keep1" in out and "invented: o" in out) or fails.append(
            "stray-o resolution should fail:\n" + out)
        # 3. picking one side's z: fails until explained, passes when explained
        put(d, "a.md", "head\nkeep1\nkeep2\nkeep3\nmain-z\ntail\nside-add\n")
        rc, out = check(d, None)
        (rc == 1 and "gone (one side's line, no version left): side-z" in out) or fails.append("unexplained choice should fail:\n" + out)
        gd = os.path.join(d, ".git", "MERGE_MSG")
        open(gd, "a").write("\nDropped: a.md — main-z supersedes side-z\n")
        rc, out = check(d, None)
        (rc == 0 and "EXPLAINED a.md" in out) or fails.append("explained choice should pass:\n" + out)
        # 4. an explanation never covers a line both sides kept
        put(d, "a.md", "head\nkeep1\nkeep3\nmain-z\ntail\nside-add\n")
        rc, out = check(d, None)
        rc == 1 or fails.append("explained file still loses kept line, should fail:\n" + out)
        # 5b. a line extended at its end counts as replaced, not gone
        put(d, "a.md", "head\nkeep1\nkeep2\nkeep3\nmain-z\nside-z and more\ntail\nside-add\n")
        rc, out = check(d, None)
        (rc == 0 and "replaced" in out) or fails.append("extended line should pass as replaced:\n" + out)
        # 5. a renumbered line whose other version survives passes unexplained
        open(gd, "w").write("Merge branch 'side'\n")
        put(d, "b.md", "x\n")
        put(d, "a.md", "head\nkeep1\nkeep2\nkeep3\nmain-z\nside-z\ntail\nside-add\n")
        rc, out = check(d, None)
        rc == 0 or fails.append("baseline before renumber case should pass:\n" + out)
        # 6. commit mode on a finished merge
        put(d, "a.md", "head\nkeep1\nkeep2\nkeep3\nmain-z\nside-z\ntail\nside-add\n")
        run("commit", "-qam", "merge", cwd=d)
        sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=d, capture_output=True, text=True).stdout.strip()
        rc, out = check(d, sha)
        rc == 0 or fails.append("commit mode, faithful merge should pass:\n" + out)
    for f in fails:
        print("FAIL", f)
    print("merge_guard selftest:", "FAILED" if fails else "ok")
    return 1 if fails else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--commit", help="check a finished merge commit instead of the merge in progress")
    ap.add_argument("-C", dest="cwd", default=".", help="repository (default: .)")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    rc, out = check(a.cwd, a.commit)
    print(out)
    return rc


if __name__ == "__main__":
    sys.exit(main())
