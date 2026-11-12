#!/usr/bin/env python3
"""No-lost-rule check for text moved between the /work-on documents.

Splits the /work-on prompt (the generated claude/commands/work-on.md) and
every references/work-on/*.md file into sentences, once at a base git ref and
once in the working tree, and compares the two multisets:

- LOST: a sentence of the base that no longer exists anywhere, and is not in
  the allow file (text deliberately rewritten or dropped).
- DUPLICATED: a sentence that exists more often now than at the base.

A move is clean when both lists are empty. The allow file holds one
substring per line (`#` comments); a lost sentence containing one is
accounted for, and listed under ALLOWED so the owner can audit each.

    python3 scripts/check_rule_moves.py --base 2c39cba [--allow FILE] [--show]
    python3 scripts/check_rule_moves.py --selftest
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
MIN_LEN = 25  # shorter fragments (headings, pointers) carry no rule
FILES_GLOB = "references/work-on/*.md"
COMMAND = "claude/commands/work-on.md"
UNIT_START = re.compile(r"^(\s*[-*] |\s*\d+\. |\s*\||#)")
SENT_SPLIT = re.compile(r"(?<=[.!?:;])\s+(?=[A-Z`*(\[\"“])")


def sentences(text: str) -> list[str]:
    """Sentences of a markdown text, whitespace-normalised, fences kept."""
    units: list[str] = []
    for block in re.split(r"\n\s*\n", text):
        buf: list[str] = []
        for line in block.split("\n"):
            if UNIT_START.match(line) and buf:
                units.append(" ".join(buf))
                buf = []
            buf.append(line.strip())
        if buf:
            units.append(" ".join(buf))
    out: list[str] = []
    for unit in units:
        unit = re.sub(r"\s+", " ", unit).strip()
        unit = re.sub(r"^([-*]|\d+\.|#+) ", "", unit)
        for sent in SENT_SPLIT.split(unit):
            sent = sent.strip()
            if len(sent) >= MIN_LEN:
                out.append(sent)
    return out


def git_show(ref: str, path: str) -> str:
    proc = subprocess.run(
        ["/usr/bin/git", "show", f"{ref}:{path}"],
        cwd=REPO, capture_output=True, text=True,
    )
    return proc.stdout if proc.returncode == 0 else ""


def base_files(ref: str) -> dict[str, str]:
    names = subprocess.run(
        ["/usr/bin/git", "ls-tree", "--name-only", ref, "references/work-on/"],
        cwd=REPO, capture_output=True, text=True, check=True,
    ).stdout.split()
    files = {n: git_show(ref, n) for n in names if n.endswith(".md")}
    files[COMMAND] = git_show(ref, COMMAND)
    return files


def tree_files() -> dict[str, str]:
    files = {p.relative_to(REPO).as_posix(): p.read_text() for p in sorted(REPO.glob(FILES_GLOB))}
    files[COMMAND] = (REPO / COMMAND).read_text()
    return files


def count(files: dict[str, str]) -> Counter[str]:
    c: Counter[str] = Counter()
    for text in files.values():
        c.update(sentences(text))
    return c


def compare(before: Counter[str], after: Counter[str], allow: list[str]):
    lost, allowed, dup = [], [], []
    for sent, n in before.items():
        if after[sent] < n:
            (allowed if any(a in sent for a in allow) else lost).append(sent)
    for sent, n in after.items():
        if n > before[sent] and before[sent] > 0:
            dup.append(sent)
    return lost, allowed, dup


def load_allow(path: str | None) -> list[str]:
    if not path:
        return []
    return [
        ln.strip() for ln in Path(path).read_text().splitlines()
        if ln.strip() and not ln.lstrip().startswith("#")
    ]


def selftest() -> int:
    a = "Never edit a test yourself. You wrote the code.\n\n- one list item that is long enough to count\n"
    assert len(sentences(a)) == 2, sentences(a)
    before, after = count({"x": a}), count({"y": a + "\nNever edit a test yourself.\n"})
    lost, _, dup = compare(before, after, [])
    assert not lost and len(dup) == 1, (lost, dup)
    lost, allowed, _ = compare(before, count({"y": "nothing relevant here at all, really"}), ["Never edit"])
    assert len(lost) == 1 and len(allowed) == 1, (lost, allowed)
    print("check_rule_moves selftest: 3 passed")
    return 0


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    ap.add_argument("--base")
    ap.add_argument("--allow")
    ap.add_argument("--show", action="store_true", help="print the sentences, not just counts")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)
    if args.selftest:
        return selftest()
    if not args.base:
        ap.error("--base REF is required")
    before, after = count(base_files(args.base)), count(tree_files())
    lost, allowed, dup = compare(before, after, load_allow(args.allow))
    print(f"base sentences {sum(before.values())} ({len(before)} distinct); now {sum(after.values())}")
    print(f"LOST {len(lost)}  ALLOWED {len(allowed)}  DUPLICATED {len(dup)}")
    for title, rows in (("LOST", lost), ("DUPLICATED", dup), ("ALLOWED", allowed if args.show else [])):
        for sent in rows:
            print(f"  {title}: {sent[:300]}")
    return 1 if lost or dup else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
