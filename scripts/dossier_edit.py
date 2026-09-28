#!/usr/bin/env python3
"""Change a dossier without reading it into the orchestrator's context.

The orchestrator's two routine dossier writes are an appended `## Build log`
line and a changed front matter field. Done with an editor tool, each one first
reads the whole dossier — the recorded runs read one 45 KB dossier ten to
fifteen times, and every read stayed in context for the rest of the run. This
script does both writes on disk and prints one line per change, so the file's
bytes never enter the conversation.

It also prints one section on request, so a reader seeks to a heading instead
of reading the file (references/formats/dossier.md, "Body — fixed headings,
fixed order").

# Usage

    dossier_edit.py log DOSSIER 'LINE' ['LINE' ...]    append to ## Build log
    dossier_edit.py log DOSSIER -  < lines.txt          append stdin's lines
    dossier_edit.py set DOSSIER KEY=VALUE [...]          set front matter scalars
    dossier_edit.py add DOSSIER KEY=ITEM [...]           append to a list field
    dossier_edit.py section DOSSIER 'HEADING'            print one section
    dossier_edit.py --selftest

`log` requires `## Build log` to be the last section (the fixed body order puts
it there), so an append at end of file is an append to the log. A line that
does not start with `- ` gets the `- ` bullet. `set` changes top-level scalar
keys that already exist (`status`, `updated`, `branch`, `worktree`, `pr`,
`jira`, `baseline_commit`, ...); a list key (`adrs`, `blocked_by`, `anchors`)
goes through `add`, in either the inline `[a, b]` or the block `- a` form.
`section` prints the heading line through the line before the next heading of
the same or a higher level, fences respected, with 1-based line numbers.

Exit codes: 0 done; 1 refused (file untouched); 2 unusable input.

No third-party imports: this runs wherever python3 does.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import tempfile

HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*$")
KEY_RE = re.compile(r"^(?P<key>[A-Za-z_][\w-]*):(?P<rest>.*)$")


class Refused(Exception):
    pass


def read(path: str) -> str:
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def write(path: str, text: str) -> None:
    # Write beside the target, then rename: a crash mid-write never leaves a
    # half dossier behind.
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(os.path.abspath(path)), prefix=".dossier-edit-")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.replace(tmp, path)


def headings(lines: list[str]) -> list[tuple[int, int, str]]:
    """(0-based line index, level, title) for every heading outside a fence."""
    out: list[tuple[int, int, str]] = []
    fence = False
    for i, line in enumerate(lines):
        if line.strip().startswith("```"):
            fence = not fence
            continue
        if fence:
            continue
        m = HEADING_RE.match(line)
        if m:
            out.append((i, len(m.group(1)), m.group(2)))
    return out


def split_front_matter(text: str) -> tuple[list[str], list[str]]:
    lines = text.split("\n")
    if not lines or lines[0] != "---":
        raise Refused("no front matter: the first line is not ---")
    for i in range(1, len(lines)):
        if lines[i] == "---":
            return lines[1:i], lines[i + 1 :]
    raise Refused("front matter is not closed by ---")


def join_front_matter(fm: list[str], body: list[str]) -> str:
    return "\n".join(["---", *fm, "---", *body])


def append_log(text: str, entries: list[str]) -> tuple[str, int]:
    lines = text.split("\n")
    level2 = [h for h in headings(lines) if h[1] == 2]
    if not level2 or not level2[-1][2].startswith("Build log"):
        last = level2[-1][2] if level2 else "none"
        raise Refused(f"the last section is `## {last}`, not `## Build log`; an append at end of file would land in the wrong section")
    entries = [e if e.startswith("- ") else f"- {e}" for e in (x.rstrip() for x in entries) if e]
    if not entries:
        raise Refused("nothing to append")
    body = text.rstrip("\n")
    return body + "\n" + "\n".join(entries) + "\n", len(entries)


def set_fields(text: str, pairs: list[tuple[str, str]]) -> tuple[str, list[str]]:
    fm, body = split_front_matter(text)
    changes: list[str] = []
    for key, value in pairs:
        index = next((i for i, l in enumerate(fm) if (m := KEY_RE.match(l)) and m.group("key") == key), None)
        if index is None:
            raise Refused(f"front matter has no `{key}:`; set changes an existing key, it never invents one")
        rest = KEY_RE.match(fm[index]).group("rest")  # type: ignore[union-attr]
        old, _, comment = rest.partition(" #")
        old = old.strip()
        is_block_list = old == "" and index + 1 < len(fm) and fm[index + 1].lstrip().startswith("- ")
        if old.startswith("[") or is_block_list:
            raise Refused(f"`{key}` is a list; use add")
        fm[index] = f"{key}: {value}" + (f"  #{comment}" if comment else "")
        changes.append(f"{key}: {old or '(empty)'} -> {value}")
    return join_front_matter(fm, body), changes


def add_items(text: str, pairs: list[tuple[str, str]]) -> tuple[str, list[str]]:
    fm, body = split_front_matter(text)
    changes: list[str] = []
    for key, item in pairs:
        index = next((i for i, l in enumerate(fm) if (m := KEY_RE.match(l)) and m.group("key") == key), None)
        if index is None:
            raise Refused(f"front matter has no `{key}:`")
        rest = KEY_RE.match(fm[index]).group("rest")  # type: ignore[union-attr]
        value, _, comment = rest.partition(" #")
        value = value.strip()
        tail = f"  #{comment}" if comment else ""
        if value.startswith("["):
            inner = value.strip("[]").strip()
            items = [x.strip() for x in inner.split(",") if x.strip()]
            if item in items:
                changes.append(f"{key}: already holds {item}")
                continue
            fm[index] = f"{key}: [{', '.join([*items, item])}]{tail}"
        elif value == "":
            end = index + 1
            while end < len(fm) and fm[end].startswith(("  ", "- ")):
                end += 1
            existing = [l.strip()[2:].strip() for l in fm[index + 1 : end]]
            if item in existing:
                changes.append(f"{key}: already holds {item}")
                continue
            fm.insert(end, f"  - {item}")
        else:
            raise Refused(f"`{key}` is a scalar; use set")
        changes.append(f"{key}: + {item}")
    return join_front_matter(fm, body), changes


def section(text: str, heading: str) -> str:
    lines = text.split("\n")
    want = heading.lstrip("#").strip().strip("`").lower()
    hs = headings(lines)
    for n, (i, level, title) in enumerate(hs):
        if title.strip("`").lower() == want:
            end = next((j for j, lv, _ in hs[n + 1 :] if lv <= level), len(lines))
            while end > i + 1 and not lines[end - 1].strip():
                end -= 1
            return "\n".join(f"{k + 1}\t{lines[k]}" for k in range(i, end))
    raise Refused(f"no heading `{heading}` outside a code fence")


def pairs_of(items: list[str]) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for item in items:
        key, sep, value = item.partition("=")
        if not sep or not key:
            raise ValueError(f"expected KEY=VALUE, got {item!r}")
        out.append((key.strip(), value.strip()))
    return out


def run(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--selftest", action="store_true", help="check the checker")
    sub = ap.add_subparsers(dest="cmd")
    p_log = sub.add_parser("log", help="append lines to ## Build log")
    p_log.add_argument("dossier")
    p_log.add_argument("lines", nargs="+", help="lines to append, or - for stdin")
    for name in ("set", "add"):
        p = sub.add_parser(name)
        p.add_argument("dossier")
        p.add_argument("pairs", nargs="+", help="KEY=VALUE")
    p_sec = sub.add_parser("section", help="print one section with line numbers")
    p_sec.add_argument("dossier")
    p_sec.add_argument("heading")
    args = ap.parse_args(argv)
    if args.selftest:
        return selftest()
    if not args.cmd:
        ap.error("a subcommand is required unless --selftest")
    try:
        text = read(args.dossier)
    except OSError as err:
        print(f"dossier_edit: {err}", file=sys.stderr)
        return 2
    try:
        if args.cmd == "log":
            entries = sys.stdin.read().splitlines() if args.lines == ["-"] else args.lines
            new, n = append_log(text, entries)
            write(args.dossier, new)
            print(f"dossier_edit: appended {n} line(s) to ## Build log")
        elif args.cmd in ("set", "add"):
            fn = set_fields if args.cmd == "set" else add_items
            new, changes = fn(text, pairs_of(args.pairs))
            write(args.dossier, new)
            for change in changes:
                print(f"dossier_edit: {change}")
        else:
            print(section(text, args.heading))
    except Refused as err:
        print(f"dossier_edit: REFUSED — {err}", file=sys.stderr)
        return 1
    except ValueError as err:
        print(f"dossier_edit: {err}", file=sys.stderr)
        return 2
    return 0


SAMPLE = """---
id: W-001
status: ready          # planned | ready
updated: 2026-08-10
adrs: []
anchors:
  - src/a.py:1
pr: null
---

# Title

## Contract

```python
## not a heading
def f(): ...
```

### Detail

text

## Build log

- first
"""


def selftest() -> int:
    failures: list[str] = []

    def check(name: str, ok: bool) -> None:
        if not ok:
            failures.append(name)

    new, n = append_log(SAMPLE, ["second", "- third", ""])
    check("log appends two bulleted lines", n == 2 and new.endswith("- first\n- second\n- third\n"))
    try:
        append_log(SAMPLE.replace("## Build log", "## Acceptance criteria"), ["x"])
        check("log refuses when Build log is not last", False)
    except Refused:
        pass

    new, changes = set_fields(SAMPLE, [("status", "building"), ("pr", "https://x/1")])
    check("set keeps the comment", "status: building  # planned | ready" in new)
    check("set reports old and new", changes[0] == "status: ready -> building")
    check("set leaves the body alone", new.split("\n---\n", 1)[1] == SAMPLE.split("\n---\n", 1)[1])
    for bad in (("missing", "x"), ("adrs", "x"), ("anchors", "x")):
        try:
            set_fields(SAMPLE, [bad])
            check(f"set refuses {bad[0]}", False)
        except Refused:
            pass

    new, _ = add_items(SAMPLE, [("adrs", "ADR-0001"), ("adrs", "ADR-0002"), ("anchors", "src/b.py:2")])
    check("add to inline list", "adrs: [ADR-0001, ADR-0002]" in new)
    check("add to block list", "  - src/a.py:1\n  - src/b.py:2\npr: null" in new)
    _, changes = add_items(new, [("adrs", "ADR-0001")])
    check("add is idempotent", changes == ["adrs: already holds ADR-0001"])

    out = section(SAMPLE, "Contract")
    check("section stops at the next level-2 heading", out.splitlines()[-1].endswith("text") and "## Build log" not in out)
    check("section ignores a fenced ##", "## not a heading" in out)
    check("section numbers lines", out.startswith("13\t## Contract"))

    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "W-001-x.md")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(SAMPLE)
        rc = run(["log", path, "on disk"])
        check("run log writes the file", rc == 0 and read(path).endswith("- on disk\n"))
        rc = run(["set", path, "status=review"])
        check("run set writes the file", rc == 0 and "status: review" in read(path))
        before = read(path)
        rc = run(["set", path, "nope=1"])
        check("a refusal leaves the file untouched", rc == 1 and read(path) == before)

    for item in failures:
        print("SELFTEST FAIL  " + item)
    print(f"selftest: {len(failures)} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(run(sys.argv[1:]))
