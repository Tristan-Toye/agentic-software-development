#!/usr/bin/env python3
"""Count how many runs a Phase 7 finding category appeared in.

Phase 8 step 8c ("add a checker gate") asks whether a review finding category recurs:
this run plus two or more earlier runs. The only durable record of a review round is the
one-line-per-CR ledger in each dossier's `## Build log`; the verbatim reviewer documents
live in `.agent-staging/reviews` and are removed with the worktree. A ledger line names its
category as `[cat: <slug>]` (`phase-7.md`), and this script counts the dossiers that carry it.

# Usage

    review_categories.py count --root R --category C [--exclude DOSSIER]
    review_categories.py --selftest

`R` is the repo root; every `R/.discovery/dossiers/*.md` is one run. `--exclude` names the
current dossier (file stem or path), so the count is of EARLIER runs only. Prints the number
of earlier dossiers with at least one `[cat: C]` ledger line, a CR counted once per run
however many lines it has. Exit 0; 2 on unusable input.

No third-party imports.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import tempfile


def _line_re(category: str) -> re.Pattern[str]:
    return re.compile(r"^\s*-\s+r\d+\s+\S+:\s+CR-.*\[cat:\s*" + re.escape(category.strip().lower()) + r"\s*\]", re.I | re.M)


def count(root: str, category: str, exclude: str | None = None) -> int:
    folder = os.path.join(root, ".discovery", "dossiers")
    if not os.path.isdir(folder):
        return 0
    skip = os.path.splitext(os.path.basename(exclude))[0] if exclude else None
    pat = _line_re(category)
    n = 0
    for name in sorted(os.listdir(folder)):
        stem, ext = os.path.splitext(name)
        if ext != ".md" or stem == skip:
            continue
        with open(os.path.join(folder, name), encoding="utf-8", errors="replace") as fh:
            if pat.search(fh.read()):
                n += 1
    return n


def selftest() -> int:
    failures = []
    line = "- r1 style: CR-style-1 no bare except — a.py:3 [cat: bare-except] — accepted\n"
    with tempfile.TemporaryDirectory() as root:
        d = os.path.join(root, ".discovery", "dossiers")
        os.makedirs(d)
        for stem, body in {
            "W-001": line + line,  # twice in one run: counts once
            "W-002": line,
            "W-003": line.replace("bare-except", "other"),
            "W-004": "no ledger\n",
            "W-005": line,  # the current run
        }.items():
            with open(os.path.join(d, stem + ".md"), "w", encoding="utf-8") as fh:
                fh.write(body)
        cases = [
            (count(root, "bare-except"), 3),
            (count(root, "bare-except", "W-005"), 2),
            (count(root, "BARE-EXCEPT", os.path.join(d, "W-005.md")), 2),
            (count(root, "other"), 1),
            (count(root, "missing"), 0),
            (count(os.path.join(root, "nowhere"), "x"), 0),
        ]
    for i, (got, want) in enumerate(cases):
        if got != want:
            failures.append(f"case {i}: got {got}, want {want}")
    for f in failures:
        print("FAIL", f)
    print(f"selftest: {len(failures)} failure(s)")
    return 1 if failures else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("cmd", nargs="?", choices=["count"])
    ap.add_argument("--root")
    ap.add_argument("--category")
    ap.add_argument("--exclude")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)
    if args.selftest:
        return selftest()
    if args.cmd != "count" or not args.root or not args.category:
        print("review_categories: count needs --root and --category", file=sys.stderr)
        return 2
    print(count(args.root, args.category, args.exclude))
    return 0


if __name__ == "__main__":
    sys.exit(main())
