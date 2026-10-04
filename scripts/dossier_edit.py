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
    dossier_edit.py set DOSSIER 'KEY=[a, b]' [...]       set a whole list field
    dossier_edit.py add DOSSIER KEY=ITEM [...]           append to a list field
    dossier_edit.py remove DOSSIER KEY=ITEM [...]        remove an item from a list field
    dossier_edit.py section DOSSIER 'HEADING'            print one section
    dossier_edit.py replace-section DOSSIER 'HEADING' --from-file F   (F or - for stdin)
    dossier_edit.py --selftest

`log` requires `## Build log` to be the last section (the fixed body order puts
it there), so an append at end of file is an append to the log. A line that
does not start with `- ` gets the `- ` bullet. `set` changes top-level scalar
keys that already exist (`status`, `updated`, `branch`, `worktree`, `pr`,
`jira`, `baseline_commit`, ...); a list key (`adrs`, `blocked_by`, `anchors`)
goes through `add`, `remove`, or `set KEY=[a, b]` (the whole list, written in
the inline form), in either the inline `[a, b]` or the block `- a` form.
`section` prints the heading line through the line before the next heading of
the same or a higher level, fences respected, with 1-based line numbers.

`blocked_by` is guarded the way validate_pipeline.py guards it: `remove` and a
`set` that drops an id are refused unless `## Build log` already holds
`BLOCKER-REMOVED: <id> — <reason>` for that id (log it first with `log`). A
done blocker records what the dossier builds on, so it leaves on purpose or not
at all. `replace-section` swaps one section's body — everything under its
heading up to the next heading of the same or a higher level, sub-sections
included — for the file's text, keeping the heading line. It refuses `## Build
log` (append-only, `log` only) and any section that contains it, and a new body
that holds a heading of the section's own level or higher.

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


BLOCKER_KEY = "blocked_by"


def key_index(fm: list[str], key: str) -> int | None:
    return next((i for i, l in enumerate(fm) if (m := KEY_RE.match(l)) and m.group("key") == key), None)


def read_list(fm: list[str], index: int) -> tuple[str, list[str], int, str]:
    """(kind, items, end, tail) of the front matter key at `index`. kind is
    `inline` (`[a, b]`), `block` (`- a` lines; `end` is one past the last) or
    `scalar`; `tail` is the inline form's trailing comment."""
    rest = KEY_RE.match(fm[index]).group("rest")  # type: ignore[union-attr]
    value, _, comment = rest.partition(" #")
    value = value.strip()
    tail = f"  #{comment}" if comment else ""
    if value.startswith("["):
        inner = value.strip("[]").strip()
        return "inline", [x.strip() for x in inner.split(",") if x.strip()], index + 1, tail
    if value == "":
        end = index + 1
        while end < len(fm) and fm[end].startswith(("  ", "- ")):
            end += 1
        return "block", [l.strip()[2:].strip() for l in fm[index + 1 : end]], end, tail
    return "scalar", [], index + 1, tail


def build_log_text(body: list[str]) -> str:
    level2 = [h for h in headings(body) if h[1] == 2 and h[2].startswith("Build log")]
    return "\n".join(body[level2[0][0] :]) if level2 else ""


def check_blockers_removed(key: str, removed: list[str], body: list[str]) -> None:
    """validate_pipeline.py's rule: an id leaving `blocked_by` needs a
    `BLOCKER-REMOVED: <id> — <reason>` line in ## Build log first."""
    if key != BLOCKER_KEY or not removed:
        return
    log = build_log_text(body)
    for wid in removed:
        if not re.search(r"BLOCKER-REMOVED:\s*" + re.escape(wid) + r"\s*[—-]+\s*\S", log):
            raise Refused(
                f"`blocked_by` would drop {wid}. A blocker stays unless ## Build log first holds "
                f"`BLOCKER-REMOVED: {wid} — <reason>` (log it with `dossier_edit.py log`, then retry)"
            )


def set_fields(text: str, pairs: list[tuple[str, str]]) -> tuple[str, list[str]]:
    fm, body = split_front_matter(text)
    changes: list[str] = []
    for key, value in pairs:
        index = key_index(fm, key)
        if index is None:
            raise Refused(f"front matter has no `{key}:`; set changes an existing key, it never invents one")
        rest = KEY_RE.match(fm[index]).group("rest")  # type: ignore[union-attr]
        old, _, comment = rest.partition(" #")
        old = old.strip()
        kind, old_items, end, tail = read_list(fm, index)
        if value.startswith("["):  # a whole list
            if kind == "scalar":
                raise Refused(f"`{key}` is a scalar; set it with a plain value")
            if not value.endswith("]"):
                raise Refused(f"a list value looks like [a, b], got {value!r}")
            new_items = [x.strip() for x in value[1:-1].split(",") if x.strip()]
            check_blockers_removed(key, [i for i in old_items if i not in new_items], body)
            fm[index:end] = [f"{key}: [{', '.join(new_items)}]{tail}"]
            changes.append(f"{key}: [{', '.join(old_items)}] -> [{', '.join(new_items)}]")
            continue
        if kind != "scalar":
            raise Refused(f"`{key}` is a list; use add, remove, or set {key}=[a, b]")
        fm[index] = f"{key}: {value}" + (f"  #{comment}" if comment else "")
        changes.append(f"{key}: {old or '(empty)'} -> {value}")
    return join_front_matter(fm, body), changes


def add_items(text: str, pairs: list[tuple[str, str]]) -> tuple[str, list[str]]:
    fm, body = split_front_matter(text)
    changes: list[str] = []
    for key, item in pairs:
        index = key_index(fm, key)
        if index is None:
            raise Refused(f"front matter has no `{key}:`")
        kind, items, end, tail = read_list(fm, index)
        if kind == "scalar":
            raise Refused(f"`{key}` is a scalar; use set")
        if item in items:
            changes.append(f"{key}: already holds {item}")
            continue
        if kind == "inline":
            fm[index] = f"{key}: [{', '.join([*items, item])}]{tail}"
        else:
            fm.insert(end, f"  - {item}")
        changes.append(f"{key}: + {item}")
    return join_front_matter(fm, body), changes


def remove_items(text: str, pairs: list[tuple[str, str]]) -> tuple[str, list[str]]:
    """Take items out of list fields. `blocked_by` is guarded: see check_blockers_removed."""
    fm, body = split_front_matter(text)
    changes: list[str] = []
    for key, item in pairs:
        index = key_index(fm, key)
        if index is None:
            raise Refused(f"front matter has no `{key}:`")
        kind, items, end, tail = read_list(fm, index)
        if kind == "scalar":
            raise Refused(f"`{key}` is a scalar; use set")
        if item not in items:
            changes.append(f"{key}: does not hold {item}")
            continue
        check_blockers_removed(key, [item], body)
        if kind == "inline":
            fm[index] = f"{key}: [{', '.join(i for i in items if i != item)}]{tail}"
        else:
            del fm[index + 1 + items.index(item)]
        changes.append(f"{key}: - {item}")
    return join_front_matter(fm, body), changes


def replace_section(text: str, heading: str, new_body: str) -> tuple[str, str]:
    """Replace one section's body (everything under its heading, sub-sections
    included) with `new_body`; the heading line stays. `## Build log` is
    append-only, so it is refused, and so is any heading whose span contains it."""
    lines = text.split("\n")
    want = heading.lstrip("#").strip().strip("`").lower()
    hs = headings(lines)
    if want.startswith("build log"):
        raise Refused("## Build log is append-only; add to it with `log`")
    for n, (i, level, title) in enumerate(hs):
        if title.strip("`").lower() != want:
            continue
        end = next((j for j, lv, _ in hs[n + 1 :] if lv <= level), len(lines))
        if any(i < j < end and t.startswith("Build log") for j, _, t in hs):
            raise Refused(f"`{title}` contains ## Build log, which stays append-only")
        body = new_body.strip("\n").split("\n") if new_body.strip() else []
        if any(lv <= level for _, lv, _ in headings(body)):
            raise Refused("the new body holds a heading at the section's own level or higher; it would add a section")
        old_lines = end - i - 1
        lines[i + 1 : end] = ["", *body, ""] if body else [""]
        return "\n".join(lines), f"replaced {old_lines} line(s) under `{title}` with {len(body)}"
    raise Refused(f"no heading `{heading}` outside a code fence")


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
    for name in ("set", "add", "remove"):
        p = sub.add_parser(name)
        p.add_argument("dossier")
        p.add_argument("pairs", nargs="+", help="KEY=VALUE")
    p_sec = sub.add_parser("section", help="print one section with line numbers")
    p_sec.add_argument("dossier")
    p_sec.add_argument("heading")
    p_rep = sub.add_parser("replace-section", help="replace one section's body from a file")
    p_rep.add_argument("dossier")
    p_rep.add_argument("heading")
    p_rep.add_argument("--from-file", required=True, metavar="F", help="the new body; - reads stdin")
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
        elif args.cmd in ("set", "add", "remove"):
            fn = {"set": set_fields, "add": add_items, "remove": remove_items}[args.cmd]
            new, changes = fn(text, pairs_of(args.pairs))
            write(args.dossier, new)
            for change in changes:
                print(f"dossier_edit: {change}")
        elif args.cmd == "replace-section":
            try:
                new_body = sys.stdin.read() if args.from_file == "-" else read(args.from_file)
            except OSError as err:
                print(f"dossier_edit: {err}", file=sys.stderr)
                return 2
            new, note = replace_section(text, args.heading, new_body)
            write(args.dossier, new)
            print(f"dossier_edit: {note}")
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

    # --- lists: remove, whole-list set, and the blocked_by guard ---
    blk = SAMPLE.replace("pr: null\n---", "pr: null\nblocked_by: [W-0001, W-0002]  # why\nrelated:\n  - a\n  - b\n---")
    logged = blk + "- BLOCKER-REMOVED: W-0001 — the dependency was wrong\n"

    def refused(fn, text, pairs) -> bool:
        try:
            fn(text, pairs)
            return False
        except Refused:
            return True

    check("remove refuses a blocker with no BLOCKER-REMOVED line", refused(remove_items, blk, [("blocked_by", "W-0001")]))
    check("remove refuses a reasonless BLOCKER-REMOVED", refused(remove_items, blk + "- BLOCKER-REMOVED: W-0001 —\n", [("blocked_by", "W-0001")]))
    check("remove refuses when the line names another id", refused(remove_items, blk + "- BLOCKER-REMOVED: W-0002 — x\n", [("blocked_by", "W-0001")]))
    new, changes = remove_items(logged, [("blocked_by", "W-0001")])
    check("remove takes a justified blocker out of an inline list", "blocked_by: [W-0002]  # why" in new and changes == ["blocked_by: - W-0001"])
    new, changes = remove_items(blk, [("related", "a")])
    check("remove takes an item out of a block list", "related:\n  - b\n---" in new and "  - a" not in new)
    _, changes = remove_items(blk, [("blocked_by", "W-0099")])
    check("remove of an item not held changes nothing", changes == ["blocked_by: does not hold W-0099"])
    check("remove refuses a scalar", refused(remove_items, blk, [("pr", "x")]))

    check("set list refuses to drop a blocker unjustified", refused(set_fields, blk, [("blocked_by", "[W-0001]")]))
    check("set list refuses an emptied blocker list", refused(set_fields, blk, [("blocked_by", "[]")]))
    new, changes = set_fields(blk, [("blocked_by", "[W-0001, W-0002, W-0003]")])
    check("set list may add a blocker", "blocked_by: [W-0001, W-0002, W-0003]  # why" in new)
    new, _ = set_fields(logged, [("blocked_by", "[W-0002]")])
    check("set list may drop a justified blocker", "blocked_by: [W-0002]  # why" in new)
    new, changes = set_fields(blk, [("related", "[x, y]")])
    check("set list turns a block list into the inline form", "related: [x, y]\n---" in new and "  - a" not in new and changes == ["related: [a, b] -> [x, y]"])
    check("set list refuses a scalar key", refused(set_fields, blk, [("pr", "[a]")]))
    check("set list refuses an unclosed list", refused(set_fields, blk, [("related", "[a")]))
    check("a plain set on a list key is still refused", refused(set_fields, blk, [("related", "x")]))

    # --- replace-section ---
    new, note = replace_section(SAMPLE, "Contract", "new contract line\n\n### Sub\n\nmore\n")
    check("replace-section keeps the heading and writes the body", "## Contract\n\nnew contract line\n\n### Sub\n\nmore\n\n## Build log" in new)
    check("replace-section drops the old body, fence and sub-sections", "def f()" not in new and "### Detail" not in new)
    check("replace-section leaves the Build log and the front matter alone", new.endswith("## Build log\n\n- first\n") and new.startswith("---\nid: W-001"))
    new, _ = replace_section(SAMPLE, "Detail", "only this")
    check("replace-section on a level-3 section stops at the next heading", "### Detail\n\nonly this\n\n## Build log" in new and "```python" in new)
    check("replace-section refuses ## Build log", refused(lambda t, a: replace_section(t, a[0], "x"), SAMPLE, ["Build log"]))
    check("replace-section refuses a section that contains the Build log", refused(lambda t, a: replace_section(t, a[0], "x"), SAMPLE, ["Title"]))
    check("replace-section refuses a body that adds a section", refused(lambda t, a: replace_section(t, a[0], "x\n## Extra\n"), SAMPLE, ["Contract"]))
    check("replace-section refuses an unknown heading", refused(lambda t, a: replace_section(t, a[0], "x"), SAMPLE, ["Nope"]))
    new, _ = replace_section(SAMPLE, "Contract", "see\n```\n## fenced is fine\n```\n")
    check("replace-section allows a fenced heading in the body", "## fenced is fine" in new)

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
        body_file = os.path.join(tmp, "body.md")
        with open(body_file, "w", encoding="utf-8") as fh:
            fh.write("from a file\n")
        rc = run(["replace-section", path, "Contract", "--from-file", body_file])
        check("run replace-section writes the file", rc == 0 and "## Contract\n\nfrom a file\n\n## Build log" in read(path))
        before = read(path)
        rc = run(["replace-section", path, "Build log", "--from-file", body_file])
        check("run replace-section refuses the Build log, file untouched", rc == 1 and read(path) == before)
        rc = run(["replace-section", path, "Contract", "--from-file", os.path.join(tmp, "absent.md")])
        check("run replace-section with an unreadable file is unusable input", rc == 2)
        rc = run(["remove", path, "adrs=none"])
        check("run remove of an absent item exits 0", rc == 0)

    for item in failures:
        print("SELFTEST FAIL  " + item)
    print(f"selftest: {len(failures)} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(run(sys.argv[1:]))
