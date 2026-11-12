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
    dossier_edit.py sync-contract DOSSIER --root X      rebuild every ## Contract block from X's files
    dossier_edit.py --selftest

`log` requires `## Build log` to be the last section (the fixed body order puts
it there), so an append at end of file is an append to the log. A line that
does not start with `- ` gets the `- ` bullet. `set` changes top-level scalar
keys that already exist (`status`, `updated`, `branch`, `worktree`, `pr`,
`jira`, `baseline_commit`, ...); a list key (`adrs`, `blocked_by`, `anchors`)
goes through `add`, `remove`, or `set KEY=[a, b]` (the whole list, written in
the inline form), in either the inline `[a, b]` or the block `- a` form.
`section` prints the heading line through the line before the next heading of
the same or a higher level, fences respected, with 1-based line numbers. The
default output is capped at 120 lines (the head; for `## Build log` the last
120) with a last line naming how many were left out: `--all` prints the whole
section, `--lines N` sets the cap.

`blocked_by` is guarded the way validate_pipeline.py guards it: `remove` and a
`set` that drops an id are refused unless `## Build log` already holds
`BLOCKER-REMOVED: <id> — <reason>` for that id (log it first with `log`). A
done blocker records what the dossier builds on, so it leaves on purpose or not
at all. `replace-section` swaps one section's body — everything under its
heading up to the next heading of the same or a higher level, sub-sections
included — for the file's text, keeping the heading line. It refuses `## Build
log` (append-only, `log` only) and any section that contains it, and a new body
that holds a heading of the section's own level or higher.

`sync-contract` rebuilds each `### <path>` code block under `## Contract` from
that file under `--root` (signatures, doc comments and constants, bodies as
`…`; the extraction is code_nav.py's, .rs, .py and .cs), writes the dossier, then
re-extracts both sides by symbol and compares each member's whole text. It
never checks that a chunk is "in" the file: a truncated member is a different
text. Any missing, truncated or extra member is listed and exits 1.
With a `baseline_commit` in the front matter a block holds what the dossier is
about, not the whole file: the members it already names, plus (in the first
block of a path) the members the file added or changed since that commit,
test modules aside. Without one, or when that commit is not in the repository,
a block holds the whole file.

Exit codes: 0 done; 1 refused (file untouched); 2 unusable input.

No third-party imports: this runs wherever python3 does.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
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


def write_verified(path: str, new: str, what: str) -> None:
    """Write, then read the file back and refuse a silently-lost change: the
    Build log is the run's only durable record, and a batch that lands as
    nothing must exit nonzero, never pass quietly (#154). Exits 1 (via
    SystemExit) naming what was lost."""
    write(path, new)
    try:
        back = read(path)
    except OSError as err:
        print(f"dossier_edit: wrote {path} but cannot read it back: {err}", file=sys.stderr)
        raise SystemExit(1)
    if back != new:
        print(f"dossier_edit: {what} — the file does not carry what was written "
              f"({len(back.encode('utf-8'))} bytes on disk, {len(new.encode('utf-8'))} meant); "
              "nothing was silently kept", file=sys.stderr)
        raise SystemExit(1)


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


SECTION_CAP = 120


def section(text: str, heading: str, cap: int | None = None) -> str:
    """One section with line numbers. `cap` bounds the lines printed (None: all): the
    head of the section, except `## Build log`, which keeps its tail (newest lines);
    a last line says how many were left out and how to get them."""
    out = _section_all(text, heading)
    rows = out.split("\n")
    if cap is None or len(rows) <= cap:
        return out
    if heading.lstrip("#").strip().strip("`").lower() == "build log":
        return "\n".join([rows[0], f"... ({len(rows) - cap} earlier line(s) omitted; --all prints the whole section)"] + rows[-(cap - 1):])
    return "\n".join(rows[:cap] + [f"... ({len(rows) - cap} more line(s); --all prints the whole section, --lines N a longer head)"])


def _section_all(text: str, heading: str) -> str:
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


# ---------------------------------------------------------------- sync-contract


def _nav():
    """code_nav's parsers (Rust tokenizer, Python ast, C# scanner) and its `contract`
    renderer: the one extraction every contract view already shares."""
    here = os.path.dirname(os.path.abspath(__file__))
    if here not in sys.path:
        sys.path.insert(0, here)
    import code_nav
    return code_nav


def _py_constants(nav, text: str, S) -> list:
    """Module-level `NAME = ...` / `NAME: T = ...` as pseudo items, with the
    `#` comment lines directly above (a constant's documentation form)."""
    import ast
    lines = text.split("\n")
    out = []
    for st in ast.parse(text).body:
        if isinstance(st, ast.Assign):
            names = [t.id for t in st.targets if isinstance(t, ast.Name)]
        elif isinstance(st, ast.AnnAssign) and isinstance(st.target, ast.Name):
            names = [st.target.id]
        else:
            continue
        if not names:
            continue
        first = st.lineno
        while first > 1 and lines[first - 2].lstrip().startswith("#"):
            first -= 1
        it = nav.Item("python", "const", names[0], names[0])
        it.head = it.attr = it.sig_start = S.starts[first - 1]
        it.end = it.sig_end = S.eol(st.end_lineno)
        it.start_line, it.end_line = first, st.end_lineno
        out.append(it)
    return out


def contract_members(path: str, text: str, is_block: bool = False):
    """(top-level items in file order, Source, nav) for a .rs/.py/.cs text; raises
    Refused for any other language, naming the supported ones."""
    nav = _nav()
    ext = os.path.splitext(path)[1].lower()
    try:
        if ext == ".rs":
            parser = nav.RustParser(text)
            return parser.parse(0, len(text), ""), parser.S, nav
        if ext == ".cs":
            parser = nav.CSharpParser(text)
            return parser.parse(0, len(text), ""), parser.S, nav
        if ext == ".py":
            if is_block:  # the contract form's body marker is not valid Python
                text = "\n".join(ln.replace("…", "pass") if ln.strip() == "…" else ln for ln in text.split("\n"))
            S = nav.Source(text)
            items = nav.parse_python(text) + _py_constants(nav, text, S)
            return sorted(items, key=lambda i: i.head), S, nav
    except nav.Unusable as err:
        raise Refused(f"{path}: cannot parse: {err}")
    raise Refused(f"{path}: sync-contract reads .rs, .py and .cs (code_nav's parsers); "
                  f"{ext or 'this file type'} is not supported — hand-write that block")


def _render(it, S, nav) -> str:
    return nav.contract(it, S) if it.kind != "const" or it.lang != "python" else S.text[S.line_start(it.head):it.end]


def rebuild_block(path: str, file_text: str) -> str:
    """The contract block for one file: every top-level member in contract form
    (signature + docs/doc comments + constants, bodies dropped), in file order,
    adjacent members staying adjacent and separated ones a blank line apart."""
    items, S, nav = contract_members(path, file_text)
    out: list[str] = []
    prev_end = None
    for it in items:
        if prev_end is not None:
            out.append("\n" if it.start_line <= prev_end + 1 else "\n\n")
        out.append(_render(it, S, nav))
        prev_end = it.end_line
    return "".join(out)


def member_map(path: str, text: str, is_block: bool = False) -> dict[str, str]:
    """qualified name -> contract text, for every member including nested ones,
    extracted by symbol. A repeated name (two `impl Foo`) gets `#2`, `#3`."""
    items, S, nav = contract_members(path, text, is_block)
    out: dict[str, str] = {}
    for it, _ in nav.flatten(items):
        key = f"{it.kind} {it.qname}"
        n = 2
        while key in out:
            key = f"{it.kind} {it.qname}#{n}"
            n += 1
        out[key] = _render(it, S, nav)
    return out


def verify_block(path: str, block: str, file_text: str) -> list[str]:
    """Whole-text equality per member, by symbol. Never substring presence: a
    truncated member is a different text and is reported as such."""
    want = member_map(path, file_text)
    have = member_map(path, block, True)
    bad = []
    for key, text in want.items():
        if key not in have:
            bad.append(f"MISSING   {key}")
        elif have[key] != text:
            bad.append(f"MISMATCH  {key}  (dossier {len(have[key])} chars, file {len(text)})")
    bad += [f"EXTRA     {key}  (in the dossier, not in the file)" for key in have if key not in want]
    return bad


# A contract holds what the dossier is about, not all of each file it touches. With a `baseline_commit` a block keeps
# the members it already names, the members the file added or changed since that commit, and the members the dossier's
# own text mentions (work packages, criteria, approach, checklist): everything a blind author, a reviewer or a payload
# needs, and new stubs join by themselves.


def _own(it, S, nav) -> str:
    """A member's contract text; a container's without its members, so a member's change is not the container's."""
    kids = it.children
    it.children = []
    try:
        return _render(it, S, nav)
    finally:
        it.children = kids


def scoped_members(path: str, text: str, is_block: bool = False):
    """(top-level items, Source, nav, {id(item): key}, {key: own contract text}). Repeated containers (two `impl Foo`)
    share one key: what tells them apart is their members, and a block holding only some would number them differently."""
    items, S, nav = contract_members(path, text, is_block)
    keys: dict[int, str] = {}
    own: dict[str, str] = {}
    for it, _ in nav.flatten(items):
        key = f"{it.kind} {it.qname}"
        if not it.children:
            n = 2
            while key in own:
                key = f"{it.kind} {it.qname}#{n}"
                n += 1
        keys[id(it)] = key
        own.setdefault(key, _own(it, S, nav))
    return items, S, nav, keys, own


def _is_test_item(it, S) -> bool:
    return "cfg(test)" in S.text[S.line_start(it.head):it.sig_end]


def changed_keys(path: str, file_text: str, base_text: str | None) -> set[str]:
    """The members the file added or changed since the baseline (signature, docs, constants), never its test modules."""
    items, S, nav, keys, own = scoped_members(path, file_text)
    if base_text is None:
        base = {}
    else:
        try:
            base = scoped_members(path, base_text)[4]
        except Refused:
            base = {}
    out: set[str] = set()

    def walk(its) -> None:
        for it in its:
            if _is_test_item(it, S):
                continue
            k = keys[id(it)]
            if base.get(k) != own[k]:
                out.add(k)
            walk(it.children)
    walk(items)
    return out


def scoped_block(path: str, file_text: str, scope: set[str]) -> str:
    """The contract block holding only `scope`; a container stays when it or one of its members is in scope."""
    items, S, nav, keys, _ = scoped_members(path, file_text)

    def keep(it) -> bool:
        it.children = [c for c in it.children if keep(c)]
        return keys[id(it)] in scope or bool(it.children)
    out: list[str] = []
    prev_end = None
    for it in items:
        if not keep(it):
            continue
        if prev_end is not None:
            out.append("\n" if it.start_line <= prev_end + 1 else "\n\n")
        out.append(_render(it, S, nav))
        prev_end = it.end_line
    return "".join(out)


def verify_scoped(path: str, block: str, file_text: str, scope: set[str]) -> list[str]:
    """Whole-text equality per in-scope member, by symbol, against the file."""
    want = scoped_members(path, file_text)[4]
    have = scoped_members(path, block, True)[4]
    bad = []
    for key in sorted(scope):
        if key not in have:
            bad.append(f"MISSING   {key}")
        elif have[key] != want.get(key):
            bad.append(f"MISMATCH  {key}  (dossier {len(have[key])} chars, file {len(want.get(key) or '')})")
    return bad


MENTION_SECTIONS = re.compile(r"^#{2,3} +(?:Approach|Work packages|Acceptance criteria|PROMISE_CHECKLIST)\b.*?(?=^#{2,3} |\Z)", re.M | re.S)


def mentioned_names(text: str) -> set[str]:
    """The identifiers in backticks in the sections that tell a builder what to do (four letters up: not `new`, `run`)."""
    names: set[str] = set()
    for sec in MENTION_SECTIONS.finditer(text):
        for tok in re.findall(r"`([^`\n]+)`", sec.group(0)):
            names.update(re.findall(r"[A-Za-z_][A-Za-z0-9_]{3,}", tok))
    return names


def mentioned_keys(path: str, file_text: str, names: set[str]) -> set[str]:
    items, S, nav, keys, _ = scoped_members(path, file_text)
    return {keys[id(it)] for it, _ in nav.flatten(items) if it.name in names and not _is_test_item(it, S)}


def baseline_of(text: str) -> str:
    m = re.match(r"---\n(.*?)\n---\n", text, re.S)
    b = re.search(r"^baseline_commit:\s*([0-9a-fA-F]{7,40})\s*$", m.group(1), re.M) if m else None
    return b.group(1) if b else ""


def git_show(root: str, commit: str, path: str) -> str | None:
    """The file at `commit`; None when it did not exist there. Raises OSError when the commit is not there at all."""
    if subprocess.run(["git", "-C", root, "cat-file", "-e", f"{commit}^{{commit}}"], capture_output=True).returncode != 0:
        raise OSError(f"baseline {commit} not in {root}")
    p = subprocess.run(["git", "-C", root, "show", f"{commit}:{path}"], capture_output=True)
    return p.stdout.decode("utf-8", "replace") if p.returncode == 0 else None


def contract_blocks(lines: list[str]) -> list[tuple[str, int, int]]:
    """(path, first body line, closing fence line) of each `### <path>` code
    block under `## Contract`; `### Definitions` is skipped."""
    hs = headings(lines)
    n = next((k for k, (_, lv, t) in enumerate(hs) if lv == 2 and t.strip("`").lower() == "contract"), None)
    if n is None:
        raise Refused("no `## Contract` section")
    end = next((j for j, lv, _ in hs[n + 1:] if lv <= 2), len(lines))
    out = []
    for k in range(n + 1, len(hs)):
        i, lv, title = hs[k]
        if i >= end:
            break
        if lv != 3 or title.strip("`").lower().startswith("definitions"):
            continue
        parts = title.strip().strip("`").split()
        path = parts[0].strip("`") if parts else ""
        nxt = min(hs[k + 1][0] if k + 1 < len(hs) else len(lines), end)
        open_i = next((j for j in range(i + 1, nxt) if lines[j].strip().startswith("```")), None)
        if open_i is None:
            raise Refused(f"`### {title}` has no code block to rebuild")
        close_i = next((j for j in range(open_i + 1, end) if lines[j].strip().startswith("```")), None)
        if close_i is None:
            raise Refused(f"`### {title}` has an unclosed code block")
        out.append((path, open_i + 1, close_i))
    return out


def sync_contract(text: str, root: str) -> tuple[str, list[str], list[str]]:
    """(new dossier text, one note per block, per-member problems found after
    rebuilding — empty when every block equals its file's members)."""
    lines = text.split("\n")
    blocks = contract_blocks(lines)
    if not blocks:
        raise Refused("`## Contract` holds no `### <path>` code block")
    notes: list[str] = []
    baseline = baseline_of(text)
    if baseline:
        scoped = sync_scoped(lines, blocks, root, baseline)
        if scoped is not None:
            return scoped
        notes.append(f"baseline {baseline[:8]} is not in {root}: every block holds its whole file")
    for path, a, b in reversed(blocks):
        fp = path if os.path.isabs(path) else os.path.join(root, path)
        try:
            file_text = read(fp)
        except OSError as err:
            raise Refused(f"{path}: cannot read {fp}: {err}")
        lines[a:b] = rebuild_block(fp, file_text).split("\n")
        notes.append(f"rebuilt `{path}`")
    new = "\n".join(lines)
    problems: list[str] = []
    new_lines = new.split("\n")
    for path, a, b in contract_blocks(new_lines):
        fp = path if os.path.isabs(path) else os.path.join(root, path)
        for p in verify_block(fp, "\n".join(new_lines[a:b]), read(fp)):
            problems.append(f"{path}: {p}")
    return new, notes[:1 if baseline else 0] + list(reversed(notes[1 if baseline else 0:])), problems


def sync_scoped(lines: list[str], blocks: list[tuple[str, int, int]], root: str, baseline: str):
    """sync_contract under a baseline: each block keeps the members it names, and the first block of a path also takes
    the path's added or changed members no block names. None when the baseline commit is not available."""
    mentions = mentioned_names("\n".join(lines))
    named: list[set[str]] = []
    unread: list[str] = []
    files: dict[str, tuple[str, set[str]]] = {}
    try:
        for path, a, b in blocks:
            fp = path if os.path.isabs(path) else os.path.join(root, path)
            try:
                block_keys = set(scoped_members(fp, "\n".join(lines[a:b]), True)[4])
            except Refused as err:
                if "is not supported" in str(err):
                    raise
                block_keys = set()
                unread.append(f"{path}: the block could not be parsed, so it names nothing: {err}")
            named.append(block_keys)
            if path not in files:
                try:
                    file_text = read(fp)
                except OSError as err:
                    raise Refused(f"{path}: cannot read {fp}: {err}")
                rel = os.path.relpath(fp, root)
                files[path] = (file_text, changed_keys(fp, file_text, git_show(root, baseline, rel)))
    except OSError:
        return None
    all_named = {}
    for (path, _, _), keys in zip(blocks, named):
        all_named.setdefault(path, set()).update(keys)
    notes: list[str] = []
    problems: list[str] = []
    out = list(lines)
    seen: set[str] = set()
    plans = []
    for (path, a, b), keys in zip(blocks, named):
        file_text, changed = files[path]
        fp = path if os.path.isabs(path) else os.path.join(root, path)
        present = set(scoped_members(fp, file_text)[4])
        gone = sorted(keys - present)
        scope = (keys & present) | ((changed | mentioned_keys(fp, file_text, mentions)) - all_named[path] if path not in seen else set())
        seen.add(path)
        block = scoped_block(fp, file_text, scope)
        if not scope:
            problems.append(f"{path}: EMPTY     the block names no member of the file and nothing changed since {baseline[:8]}")
        new_keys = len(scope - keys)
        notes.append(f"rebuilt `{path}`: {len(scope)} member(s), {new_keys} added (changed since the baseline or named in the dossier)"
                     + (f"; no longer in the file: {', '.join(gone)}" if gone else ""))
        plans.append((a, b, block, fp, file_text, scope, path))
    for a, b, block, *_ in reversed(plans):
        out[a:b] = block.split("\n")
    new = "\n".join(out)
    new_lines = new.split("\n")
    for (path, a, b), plan in zip(contract_blocks(new_lines), plans):
        for p in verify_scoped(plan[3], "\n".join(new_lines[a:b]), plan[4], plan[5]):
            problems.append(f"{path}: {p}")
    return new, notes + unread, problems


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
    p_sec.add_argument("--all", action="store_true", help="print the whole section (default: the first %d lines; `Build log`: the last)" % SECTION_CAP)
    p_sec.add_argument("--lines", type=int, metavar="N", help="cap at N lines instead of %d" % SECTION_CAP)
    p_rep = sub.add_parser("replace-section", help="replace one section's body from a file")
    p_rep.add_argument("dossier")
    p_rep.add_argument("heading")
    p_rep.add_argument("--from-file", required=True, metavar="F", help="the new body; - reads stdin")
    p_syn = sub.add_parser("sync-contract", help="rebuild each ## Contract block from its file on disk, then verify")
    p_syn.add_argument("dossier")
    p_syn.add_argument("--root", required=True, help="the tree holding the materialised contract files")
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
            write_verified(args.dossier, new, f"appending {n} line(s) to ## Build log")
            tail = read(args.dossier).rsplit("## Build log", 1)[1]
            lost = [e for e in entries if e and e not in tail]
            if lost:
                print(f"dossier_edit: appended, but the re-read Build log is missing "
                      f"{len(lost)} of the line(s) handed in: {lost[0][:80]}", file=sys.stderr)
                return 1
            print(f"dossier_edit: appended {n} line(s) to ## Build log")
        elif args.cmd in ("set", "add", "remove"):
            fn = {"set": set_fields, "add": add_items, "remove": remove_items}[args.cmd]
            new, changes = fn(text, pairs_of(args.pairs))
            write_verified(args.dossier, new, f"{args.cmd} {args.pairs}")
            for change in changes:
                print(f"dossier_edit: {change}")
        elif args.cmd == "replace-section":
            try:
                new_body = sys.stdin.read() if args.from_file == "-" else read(args.from_file)
            except OSError as err:
                print(f"dossier_edit: {err}", file=sys.stderr)
                return 2
            new, note = replace_section(text, args.heading, new_body)
            write_verified(args.dossier, new, f"replacing `{args.heading}`")
            print(f"dossier_edit: {note}")
        elif args.cmd == "sync-contract":
            new, notes, problems = sync_contract(text, args.root)
            if new != text:
                write_verified(args.dossier, new, "sync-contract")
            for note in notes:
                print(f"dossier_edit: {note}")
            if problems:
                for p in problems:
                    print(f"dossier_edit: {p}", file=sys.stderr)
                print(f"dossier_edit: {len(problems)} contract member(s) differ from the file", file=sys.stderr)
                return 1
            print(f"dossier_edit: {len(notes)} contract block(s) equal their files, member by member")
        else:
            print(section(text, args.heading, None if args.all else (args.lines or SECTION_CAP)))
    except Refused as err:
        print(f"dossier_edit: REFUSED — {err}", file=sys.stderr)
        return 1
    except ValueError as err:
        print(f"dossier_edit: {err}", file=sys.stderr)
        return 2
    except OSError as err:
        print(f"dossier_edit: cannot write {args.dossier}: {err}", file=sys.stderr)
        return 1
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
    big = SAMPLE + "\n".join(f"- line {k}" for k in range(300)) + "\n"
    capped = section(big, "Build log", 20).split("\n")
    check("section caps a long Build log to its tail and says so", len(capped) == 21 and "earlier line(s) omitted" in capped[1] and capped[-1].endswith("line 299"))
    check("section with no cap prints everything", len(section(big, "Build log").split("\n")) > 300)
    check("section caps a long other section to its head", "more line(s)" in section(big.replace("## Build log", "## Long"), "Long", 5).split("\n")[-1])

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

        # --- #154: every write is read back; a lost one exits nonzero ---
        ro_dir = os.path.join(tmp, "readonly")
        os.makedirs(ro_dir)
        ro = os.path.join(ro_dir, "W-001-x.md")
        with open(ro, "w", encoding="utf-8") as fh:
            fh.write(SAMPLE)
        os.chmod(ro_dir, 0o555)  # write() replaces atomically (temp + rename), so a read-only FILE is bypassed by design; a read-only directory is the forced failure
        try:
            rc = run(["log", ro, "- a read-only batch"])
            forced = rc != 0
        finally:
            os.chmod(ro_dir, 0o755)
        check("a forced-failure append (a directory the writer cannot write) exits nonzero and names it", forced)
        check("the failed append left the file untouched", read(ro) == SAMPLE)
        real_write = globals()["write"]
        try:
            globals()["write"] = lambda p, t: real_write(p, t[: max(len(t) // 2, 1)])  # a write that lands half
            try:
                run(["log", path, "- half a write"])
                mismatch = False
            except SystemExit as err:
                mismatch = err.code == 1
        finally:
            globals()["write"] = real_write
        check("a silently-lost write is refused, never passed quietly", mismatch)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(SAMPLE)
        import contextlib, io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = run(["log", path, "- counted"])
        check("a successful append prints the count it added",
              rc == 0 and "appended 1 line(s)" in buf.getvalue() and read(path).endswith("- counted\n"))

    import contextlib, io
    # sync-contract: rebuilt from the file, verified by whole-member equality.
    PY = ('"""mod doc."""\n\nA = 1\nB = 2\nC = 3\nD = 4\n\n\n'
          'def f(x):\n    """Return x."""\n    return x\n\n\n'
          'class K:\n    """A K."""\n\n    def m(self):\n        """Do m."""\n        return 1\n')
    RS = ('/// Max.\npub const MAX: u32 = 3;\n\n/// Run it.\npub fn run(a: u32) -> u32 {\n    a + 1\n}\n')
    CS = ('namespace Demo\n{\n    /// <summary>A shape.</summary>\n    public class Shape\n    {\n'
          '        /// <summary>Area.</summary>\n        public double Area(int w)\n        {\n            return w;\n        }\n\n'
          '        /// <summary>Area by h.</summary>\n        public double Area(int w, int h) => w * h;\n\n'
          '        /// <summary>Nested.</summary>\n        public struct Inner\n        {\n            public int V;\n        }\n    }\n\n'
          '    /// <summary>A point.</summary>\n    public record Point(int X, int Y);\n}\n')
    DOS = ("## Contract\n\n### Definitions\n\n- **t** — x\n\n### `src/m.py`\n\n```python\nstale\n```\n\n"
           "### src/lib.rs\n\n```rust\nstale\n```\n\n### src/Shape.cs\n\n```csharp\nstale\n```\n\n## Build log\n\n- first\n")
    with tempfile.TemporaryDirectory() as td:
        os.makedirs(os.path.join(td, "src"))
        for name, body in (("m.py", PY), ("lib.rs", RS), ("Shape.cs", CS)):
            with open(os.path.join(td, "src", name), "w", encoding="utf-8") as fh:
                fh.write(body)
        new, notes, problems = sync_contract(DOS, td)
        check("sync-contract rebuilds all three blocks and verifies clean", len(notes) == 3 and problems == [])
        check("sync rebuilds a C# block: overloads, nested type, record; bodies dropped",
              "public double Area(int w) { … }" in new and "public double Area(int w, int h) => …;" in new
              and "public struct Inner {\n            public int V;\n        }" in new and "public record Point(int X, int Y);" in new
              and "/// <summary>Area.</summary>" in new and "return w;" not in new)
        cs_block = new.split("```csharp\n")[1].split("```")[0]
        check("a truncated C# doc comment is a MISMATCH", any(q.startswith("MISMATCH") for q in
              verify_block("Shape.cs", cs_block.replace("A point.", "A"), CS)))
        check("a dropped C# overload is MISSING", any(q.startswith("MISSING") and "Area(int, int)" in q for q in
              verify_block("Shape.cs", cs_block.replace("        public double Area(int w, int h) => …;\n", ""), CS)))
        check("sync keeps all four consecutive constants",
              "A = 1\nB = 2\nC = 3\nD = 4" in new and "pub const MAX: u32 = 3;" in new)
        check("sync drops bodies, keeps docstrings", "return x" not in new and '"""Return x."""' in new and "a + 1" not in new)
        check("sync leaves Definitions and Build log alone", "- **t** — x" in new and new.endswith("- first\n"))
        _, again, p2 = sync_contract(new, td)
        check("sync is idempotent", p2 == [] and sync_contract(new, td)[0] == new)
        trunc = new.replace("A = 1\nB = 2\nC = 3\nD = 4", "A = 1")
        probs = verify_block("m.py", trunc.split("```python\n")[1].split("```")[0], PY)
        check("a truncated constant chunk fails verification (substring would pass)",
              "A = 1" in PY and any("const B" in q and q.startswith("MISSING") for q in probs))
        cut = new.replace("Return x.", "Return")
        check("a truncated docstring is a MISMATCH", any(q.startswith("MISMATCH  def f") for q in
              verify_block("m.py", cut.split("```python\n")[1].split("```")[0], PY)))
        path = os.path.join(td, "d.md")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(DOS)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = run(["sync-contract", path, "--root", td])
        check("CLI sync-contract writes the blocks and exits 0", rc == 0 and "pub fn run(a: u32) -> u32 { … }" in read(path))
        with open(os.path.join(td, "src", "m.py"), "a", encoding="utf-8") as fh:
            fh.write("\n\ndef g():\n    return 2\n")
        with open(os.path.join(td, "src", "lib.txt"), "w", encoding="utf-8") as fh:
            fh.write("x")
        errbuf = io.StringIO()
        with contextlib.redirect_stderr(errbuf), contextlib.redirect_stdout(io.StringIO()):
            rc2 = run(["sync-contract", path, "--root", td])
        check("a later-added member is picked up on re-sync", rc2 == 0 and "def g():" in read(path))
        bad = new.replace("### src/lib.rs", "### src/lib.txt")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(bad)
        with contextlib.redirect_stderr(errbuf), contextlib.redirect_stdout(io.StringIO()):
            rc3 = run(["sync-contract", path, "--root", td])
        check("an unsupported language refuses (exit 1, file untouched)", rc3 == 1 and read(path) == bad)

    # sync-contract under a baseline: named + changed only, nothing a reader needs lost.
    with tempfile.TemporaryDirectory() as td:
        g = lambda *x: subprocess.run(["git", "-C", td, "-c", "user.email=t@t", "-c", "user.name=t", "-c", "commit.gpgsign=false", *x], capture_output=True, text=True)
        g("init", "-q")
        os.makedirs(os.path.join(td, "src"))
        lib = os.path.join(td, "src", "lib.rs")
        BASE = ("/// Old pub.\npub fn untouched() -> u8 {\n    1\n}\n\n/// Named.\npub fn named() -> u8 {\n    2\n}\n\n"
                "/// Will change.\npub fn changes() -> u8 {\n    3\n}\n\nimpl K {\n    /// Old method.\n    pub fn old(&self) {\n        a();\n    }\n"
                "    /// Second.\n    pub fn second(&self) {\n        b();\n    }\n}\n")
        open(lib, "w", encoding="utf-8").write(BASE)
        g("add", "-A"); g("commit", "-q", "-m", "base")
        sha = g("rev-parse", "HEAD").stdout.strip()
        NOW = BASE.replace("/// Will change.\npub fn changes() -> u8", "/// Will change.\npub fn changes(a: u8) -> u8") \
            .replace("    /// Second.", "    /// Added.\n    pub fn added(&self) {\n        c();\n    }\n    /// Second.") \
            + "\n#[cfg(test)]\nmod tests {\n    fn t() {\n        x();\n    }\n}\n"
        open(lib, "w", encoding="utf-8").write(NOW)
        SD = (f"---\nid: W-1\nbaseline_commit: {sha}\n---\n\n## Contract\n\n### `src/lib.rs` — one\n\n```rust\n/// Named.\npub fn named() -> u8 {{ … }}\n```\n\n"
              "### `src/lib.rs` — two\n\n```rust\nimpl K {\n    /// Second.\n    pub fn second(&self) { … }\n}\n```\n\n## Build log\n\n- x\n")
        new, notes, probs = sync_contract(SD, td)
        one = new.split("— one")[1].split("### ")[0]; two = new.split("— two")[1].split("## Build")[0]
        check("scoped sync: a member no block names and nothing changed is left out", "untouched" not in new and "pub fn old" not in new)
        check("scoped sync: a named member stays, in its own block", "pub fn named" in one and "pub fn second" in two and "pub fn second" not in one)
        check("scoped sync: a changed or new member joins the first block of its path, once", "changes(a: u8)" in one and "pub fn added" in one and new.count("pub fn added") == 1 and "changes" not in two)
        cited = SD.replace("## Build log", "## Work packages\n\n- WP-1: calls `untouched`\n\n## Build log")
        check("scoped sync: an unchanged member the dossier's work packages mention joins", "pub fn untouched" in sync_contract(cited, td)[0])
        check("scoped sync: a method keeps its impl; bodies and test modules never appear", "impl K {" in two and "a();" not in new and "cfg(test)" not in new and "x();" not in new)
        check("scoped sync verifies clean and is idempotent", probs == [] and sync_contract(new, td)[0] == new)
        cut = new.replace("/// Named.", "/// Nam")
        check("scoped sync: a stale doc comment in a block is rebuilt from the file", sync_contract(cut, td)[0] == new)
        gone = SD.replace("pub fn named() -> u8 { … }", "pub fn renamed() -> u8 { … }")
        n2 = sync_contract(gone, td)[1]
        check("scoped sync: a named member gone from the file is reported", any("renamed" in x for x in n2))
        whole = SD.replace(sha, "0" * 40)
        n3, notes3, _ = sync_contract(whole, td)
        check("scoped sync: a baseline not in the repository keeps every member, and says so", "pub fn untouched" in n3 and "not in" in notes3[0])
        fresh = os.path.join(td, "src", "new.rs")
        open(fresh, "w", encoding="utf-8").write("/// New.\npub fn brand_new() {\n    y();\n}\n")
        n4 = sync_contract(SD.replace("### `src/lib.rs` — two", "### `src/new.rs` — two").replace("impl K {\n    /// Second.\n    pub fn second(&self) { … }\n}", "stale"), td)[0]
        check("scoped sync: a file new since the baseline is whole", "pub fn brand_new() { … }" in n4)

    for item in failures:
        print("SELFTEST FAIL  " + item)
    print(f"selftest: {len(failures)} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(run(sys.argv[1:]))
