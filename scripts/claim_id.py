#!/usr/bin/env python3
"""Claim the next dossier (W-NNN) or ADR (NNNN) number from the repository's central ID ledger.

Concurrent /plan runs each minted "the highest number on my base + 1", so ten open plan PRs could hold the same
W-161 (config-harness, 2026-11-13): an open sibling PR cannot be seen from a base tip. The ledger is one branch on
the remote, `asd/ids`, holding one file, `ids.txt`, with one line per claimed number:

    W-171<TAB>plan/feature-key<TAB>2026-11-13T15:20:00Z

A claim fetches that branch, takes the next number, appends its line in a new commit on top of what it fetched, and
pushes WITHOUT force. Git is the lock: when another session claimed first the push is rejected as non-fast-forward,
and the claim fetches again and takes the next number. So two claims never get the same number, on any machine.

The next number is one above the highest of: the ledger, and every number visible in a tree of the remote's
branches (development, stubs, every open plan/* or docs/* branch). The scan seeds an empty ledger and also covers a
number someone minted by hand without claiming it. The working tree, the index and HEAD are never touched: the
commit is built with plumbing (hash-object, mktree, commit-tree).

Usage:
    claim_id.py --kind W   [--repo PATH] [--remote origin] [--for plan/KEY]   -> prints W-171
    claim_id.py --kind ADR [--repo PATH] [--remote origin] [--for KEY]        -> prints ADR-0094
    claim_id.py --kind W --peek                                               -> the number a claim would take, no push
    claim_id.py --selftest

Exit 0 with the id on stdout; 1 when the remote cannot be reached or pushed to after the retries (the caller falls
back to the provisional minting of formats/evidence-and-ids.md §4, which --finalize-ids settles at merge).
"""
from __future__ import annotations

import argparse
import itertools
import datetime as dt
import os
import re
import subprocess
import sys
import tempfile

LEDGER_BRANCH = "asd/ids"
LEDGER_FILE = "ids.txt"
RETRIES = 12
KINDS = {
    # kind: (directory scanned in each branch tree, file-name pattern with the number as group 1, id format)
    "W": (".discovery/dossiers", re.compile(r"^W-(\d{3,})\b"), "W-%03d"),
    "ADR": ("docs/adr", re.compile(r"^(?:ADR-)?(\d{4})\b"), "ADR-%04d"),
}
_SEQ = itertools.count()   # one private ref namespace per claim, also between threads of one process
LEDGER_LINE = {"W": re.compile(r"^W-(\d+)\t"), "ADR": re.compile(r"^ADR-(\d+)\t")}


def git(repo: str, *args: str, input: str | None = None, check: bool = True) -> subprocess.CompletedProcess:
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0")
    r = subprocess.run(["git", "-C", repo, *args], input=input, capture_output=True, text=True, env=env)
    if check and r.returncode != 0:
        raise RuntimeError("git %s: %s" % (" ".join(args[:3]), (r.stderr or r.stdout).strip()[:300]))
    return r


def fetch(repo: str, remote: str, ns: str) -> str | None:
    """Fetch every branch of the remote into the private namespace `ns` (refs/asd-claim/<pid>-<n>/), never into the
    shared remote-tracking refs: concurrent claims from worktrees of one repository share those refs, and two
    fetches updating them at once fail with "cannot lock ref". The ledger's commit, or None when it has none yet."""
    drop(repo, ns)
    git(repo, "fetch", "-q", "--no-write-fetch-head", "--refmap=", remote, "+refs/heads/*:%s*" % ns)
    r = git(repo, "rev-parse", "-q", "--verify", "%s%s^{commit}" % (ns, LEDGER_BRANCH), check=False)
    return r.stdout.strip() or None


def drop(repo: str, ns: str) -> None:
    refs = git(repo, "for-each-ref", "--format=delete %(refname)", ns, check=False).stdout
    if refs:
        git(repo, "update-ref", "--stdin", input=refs, check=False)


def ledger_text(repo: str, tip: str | None) -> str:
    if not tip:
        return ""
    r = git(repo, "show", "%s:%s" % (tip, LEDGER_FILE), check=False)
    return r.stdout if r.returncode == 0 else ""


def scanned_max(repo: str, ns: str, kind: str) -> int:
    """The highest number of this kind in the scanned directory of any remote branch's tree."""
    d, pat, _ = KINDS[kind]
    refs = git(repo, "for-each-ref", "--format=%(objectname)", ns).stdout.split()
    best = 0
    for sha in set(refs):
        r = git(repo, "ls-tree", "--name-only", sha, d + "/", check=False)
        for path in r.stdout.splitlines():
            m = pat.match(path.rsplit("/", 1)[-1])
            if m:
                best = max(best, int(m.group(1)))
    return best


def next_number(repo: str, ns: str, kind: str, tip: str | None) -> int:
    best = scanned_max(repo, ns, kind)
    for line in ledger_text(repo, tip).splitlines():
        m = LEDGER_LINE[kind].match(line)
        if m:
            best = max(best, int(m.group(1)))
    return best + 1


def claim(repo: str, remote: str, kind: str, label: str, peek: bool = False) -> str:
    fmt = KINDS[kind][2]
    ns = "refs/asd-claim/%d-%d/" % (os.getpid(), next(_SEQ))
    try:
        return _claim(repo, remote, kind, label, peek, fmt, ns)
    finally:
        drop(repo, ns)


def _claim(repo: str, remote: str, kind: str, label: str, peek: bool, fmt: str, ns: str) -> str:
    for _ in range(RETRIES):
        tip = fetch(repo, remote, ns)
        n = next_number(repo, ns, kind, tip)
        ident = fmt % n
        if peek:
            return ident
        now = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        text = ledger_text(repo, tip)
        if text and not text.endswith("\n"):
            text += "\n"
        text += "%s\t%s\t%s\n" % (ident, label.replace("\t", " ") or "-", now)
        blob = git(repo, "hash-object", "-w", "--stdin", input=text).stdout.strip()
        tree = git(repo, "mktree", input="100644 blob %s\t%s\n" % (blob, LEDGER_FILE)).stdout.strip()
        args = ["commit-tree", tree, "-m", "claim %s for %s" % (ident, label or "-")]
        if tip:
            args[2:2] = ["-p", tip]
        commit = git(repo, *args).stdout.strip()
        # No force: a push that is not a fast-forward of what we fetched means someone else claimed first.
        r = git(repo, "push", "-q", remote, "%s:refs/heads/%s" % (commit, LEDGER_BRANCH), check=False)
        if r.returncode == 0:
            return ident
        err = (r.stderr or "").lower()
        if not any(w in err for w in ("non-fast-forward", "rejected", "fetch first", "cannot lock ref", "stale info")):
            raise RuntimeError("push to %s failed: %s" % (remote, r.stderr.strip()[:300]))
    raise RuntimeError("no claim after %d tries (the ledger keeps moving)" % RETRIES)


def selftest() -> int:
    import concurrent.futures as cf

    fails = []

    def check(name: str, ok: bool, info: str = "") -> None:
        print(("ok   " if ok else "FAIL ") + name + ("" if ok else "  " + info))
        if not ok:
            fails.append(name)

    with tempfile.TemporaryDirectory() as t:
        bare = os.path.join(t, "remote.git")
        subprocess.run(["git", "init", "-q", "--bare", bare], check=True)
        seed = os.path.join(t, "seed")
        subprocess.run(["git", "init", "-q", "-b", "development", seed], check=True)
        os.makedirs(os.path.join(seed, ".discovery/dossiers"))
        os.makedirs(os.path.join(seed, "docs/adr"))
        for f in (".discovery/dossiers/W-007-a.md", "docs/adr/0003-x.md", "docs/adr/index.md"):
            open(os.path.join(seed, f), "w").write("x\n")
        for c in (["add", "-A"], ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "seed"],
                  ["remote", "add", "origin", bare], ["push", "-q", "origin", "development"]):
            subprocess.run(["git", "-C", seed, *c], check=True)
        # an open sibling plan branch holds W-009: the base tip alone would say 8
        subprocess.run(["git", "-C", seed, "checkout", "-qb", "plan/sib"], check=True)
        open(os.path.join(seed, ".discovery/dossiers/W-009-sib.md"), "w").write("x\n")
        for c in (["add", "-A"], ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "sib"],
                  ["push", "-q", "origin", "plan/sib"]):
            subprocess.run(["git", "-C", seed, *c], check=True)

        clones = []
        for i in range(6):
            c = os.path.join(t, "c%d" % i)
            subprocess.run(["git", "clone", "-q", bare, c], check=True)
            for k, v in (("user.name", "t"), ("user.email", "t@t")):
                subprocess.run(["git", "-C", c, "config", k, v], check=True)
            clones.append(c)
        head0 = subprocess.run(["git", "-C", clones[0], "rev-parse", "HEAD"], capture_output=True, text=True).stdout

        check("peek sees the open sibling branch", claim(clones[0], "origin", "W", "x", peek=True) == "W-010")
        check("ADR peek", claim(clones[0], "origin", "ADR", "x", peek=True) == "ADR-0004")
        first = claim(clones[0], "origin", "W", "plan/one")
        check("first claim", first == "W-010", first)
        with cf.ThreadPoolExecutor(6) as ex:
            got = list(ex.map(lambda c: claim(c, "origin", "W", "plan/" + os.path.basename(c)), clones * 2))
        check("12 concurrent claims are distinct", len(set(got)) == 12, str(sorted(got)))
        check("and contiguous after W-010", sorted(got) == ["W-%03d" % n for n in range(11, 23)], str(sorted(got)))
        adr = claim(clones[1], "origin", "ADR", "W-011")
        check("ADR claim is its own sequence", adr == "ADR-0004", adr)
        txt = subprocess.run(["git", "--git-dir", bare, "show", "%s:%s" % (LEDGER_BRANCH, LEDGER_FILE)],
                             capture_output=True, text=True).stdout
        check("ledger has one line per claim", len(txt.splitlines()) == 14, txt)
        check("ledger line shape", re.match(r"^W-010\tplan/one\t\d{4}-\d\d-\d\dT", txt) is not None, txt[:80])
        head1 = subprocess.run(["git", "-C", clones[0], "rev-parse", "HEAD"], capture_output=True, text=True).stdout
        st = subprocess.run(["git", "-C", clones[0], "status", "--porcelain"], capture_output=True, text=True).stdout
        check("HEAD and working tree untouched", head0 == head1 and st == "", st)
        try:
            claim(clones[0], "nowhere", "W", "x")
            check("unreachable remote fails", False)
        except RuntimeError:
            check("unreachable remote fails", True)
    print("claim_id selftest: %s" % ("FAIL " + ", ".join(fails) if fails else "OK"))
    return 1 if fails else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    ap.add_argument("--kind", choices=sorted(KINDS))
    ap.add_argument("--repo", default=".")
    ap.add_argument("--remote", default="origin")
    ap.add_argument("--for", dest="label", default="", help="what the number is for: a plan branch, a dossier id")
    ap.add_argument("--peek", action="store_true", help="print the number a claim would take; claim nothing")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args(argv)
    if a.selftest:
        return selftest()
    if not a.kind:
        ap.error("--kind is required")
    try:
        print(claim(a.repo, a.remote, a.kind, a.label, peek=a.peek))
    except RuntimeError as e:
        print("claim_id: %s" % e, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
