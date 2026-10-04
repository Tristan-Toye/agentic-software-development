#!/usr/bin/env python3
"""Navigate and edit code by item instead of by file or line range.

An orchestrator that reads a 100 KB Rust file with `sed -n` windows, whole-file
reads and chains of greps pays for those bytes on every later step: its whole
context is re-billed each time. The recorded runs spent about a fifth of their
credits that way, and read 80-90 KB markdown rule files whole to find one entry.
This script returns exactly the part asked for, bounded by --max-chars, so a
reader seeks to an item or a section instead of paging.

Rust is parsed with a small tokenizer (line and nested block comments, string,
raw string, byte string, char literal vs lifetime), so brace matching and the end
of an item are exact, not regex guesses. Python goes through `ast`. Markdown is
split on headings, fences respected.

# Usage

    code_nav.py outline FILE                        every item: lines, kind, name, first doc line
    code_nav.py item FILE SYMBOL [--mode M]         one item; M = full|contract|doc|signature
    code_nav.py find SYMBOL [--root DIR] [--kind K] [--limit 40]
                                                    where SYMBOL is defined, across .rs and .py
    code_nav.py section FILE HEADING_OR_ID [--list] one markdown section (or the heading list)
    code_nav.py replace FILE SYMBOL --from-file F [--with-docs]
                                                    replace one item's span with the text in F
    code_nav.py --selftest

SYMBOL is a qualified name (`Type::method`, `module::fn`, `Class.method`) or a bare
name when it is unique; an ambiguous bare name lists its candidates (exit 1).
Impl blocks are named `impl Type` / `impl Trait for Type`; their members are
`Type::method`. Modes: full = attributes + docs + the whole item; contract = docs +
attributes + signature with function bodies as `{ ... }` (an impl, trait, module or
class: each member's docs + signature; a struct/enum: the whole declaration);
doc = the doc comments only; signature = the signature line(s) only.

`replace` swaps from the first attribute (or the signature) through the item's end,
keeping the doc comment above it; --with-docs replaces the doc comment too. Content
without leading indentation is indented like the item it replaces. The result must
still parse, and is written atomically (tempfile + os.replace).

Every output is cut at --max-chars (default 12000) with a
`(truncated: N more chars — narrow the request)` tail.

Exit codes: 0 done; 1 symbol or section not found / ambiguous; 2 unusable input
(unknown file type, unreadable or unparsable file, bad arguments).

No third-party imports: this runs wherever python3 (3.10+) does.
"""

from __future__ import annotations

import argparse
import ast
import bisect
import contextlib
import io
import os
import re
import shutil
import sys
import tempfile
import textwrap
from dataclasses import dataclass, field
from typing import Any

DEFAULT_MAX = 12000
# .claude/ holds Claude Code worktrees: stale copies of the same tree.
SKIP_DIRS = {"target", ".git", "node_modules", ".venv", "__pycache__", ".claude", ".agent-staging"}
MAX_FILE_BYTES = 3_000_000


class Unusable(Exception):
    """Input the tool cannot work with: exit 2."""


class ParseError(Unusable):
    pass


# ---------------------------------------------------------------- source and items


class Source:
    def __init__(self, text: str) -> None:
        self.text = text
        self.starts = [0] + [m.end() for m in re.finditer("\n", text)]

    def line(self, off: int) -> int:
        """1-based line of a character offset."""
        return bisect.bisect_right(self.starts, off)

    def line_start(self, off: int) -> int:
        return self.starts[self.line(off) - 1]

    def eol(self, line: int) -> int:
        """Offset just past the last character of 1-based `line`, newline excluded."""
        return self.starts[line] - 1 if line < len(self.starts) else len(self.text)

    def indent_of(self, off: int) -> str:
        ls = self.line_start(off)
        return re.match(r"[ \t]*", self.text[ls:]).group()  # type: ignore[union-attr]


@dataclass
class Item:
    lang: str
    kind: str
    name: str
    qname: str
    head: int = 0  # first char of the leading docs/attributes (or the signature)
    attr: int = 0  # first attribute, or the signature when there is none
    sig_start: int = 0
    sig_end: int = 0  # end of the signature: before `{`, or past the terminating `;`
    end: int = 0  # exclusive
    open: int | None = None  # the body's `{`
    docs: list[tuple[int, int]] = field(default_factory=list)
    doc_first: str = ""
    children: list["Item"] = field(default_factory=list)
    aliases: list[str] = field(default_factory=list)
    type_name: str = ""
    level: int = 0
    body_indent: str = "    "
    start_line: int = 0
    end_line: int = 0


def flatten(items: list[Item], depth: int = 0) -> list[tuple[Item, int]]:
    out: list[tuple[Item, int]] = []
    for it in items:
        out.append((it, depth))
        out.extend(flatten(it.children, depth + 1))
    return out


# ---------------------------------------------------------------- Rust tokenizer

IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
DOC_ATTR = re.compile(r'#\[\s*doc\s*=\s*r?#*"(.*?)"#*\s*\]\Z', re.S)


def rust_mask(src: str) -> tuple[str, list[tuple[int, int, str]]]:
    """The source with comments, string and char contents blanked (newlines and
    length kept), plus every doc comment as (start, end, 'outer'|'inner')."""
    n = len(src)
    out = list(src)
    docs: list[tuple[int, int, str]] = []

    def blank(a: int, b: int) -> None:
        for k in range(a, b):
            if out[k] != "\n":
                out[k] = " "

    i = 0
    while i < n:
        c = src[i]
        if c == "/" and i + 1 < n and src[i + 1] == "/":
            j = src.find("\n", i)
            j = n if j < 0 else j
            t = src[i:j]
            if t.startswith("///") and not t.startswith("////"):
                docs.append((i, j, "outer"))
            elif t.startswith("//!"):
                docs.append((i, j, "inner"))
            blank(i, j)
            i = j
            continue
        if c == "/" and i + 1 < n and src[i + 1] == "*":
            depth, j = 1, i + 2
            while j < n and depth:
                if src.startswith("/*", j):
                    depth += 1
                    j += 2
                elif src.startswith("*/", j):
                    depth -= 1
                    j += 2
                else:
                    j += 1
            if depth:
                raise ParseError(f"unterminated block comment at line {src.count(chr(10), 0, i) + 1}")
            t = src[i:j]
            if t.startswith("/**") and not t.startswith("/***") and t != "/**/":
                docs.append((i, j, "outer"))
            elif t.startswith("/*!"):
                docs.append((i, j, "inner"))
            blank(i, j)
            i = j
            continue
        if c in "brc" and (i == 0 or not (src[i - 1].isalnum() or src[i - 1] == "_")):
            k = i
            if c in "bc" and k + 1 < n and src[k + 1] == "r":
                k += 1
            if src[k] == "r":
                m = k + 1
                while m < n and src[m] == "#":
                    m += 1
                if m < n and src[m] == '"':
                    hashes = m - (k + 1)
                    close = src.find('"' + "#" * hashes, m + 1)
                    if close < 0:
                        raise ParseError(f"unterminated raw string at line {src.count(chr(10), 0, i) + 1}")
                    blank(m + 1, close)
                    i = close + 1 + hashes
                    continue
            elif c in "bc" and i + 1 < n and src[i + 1] in "\"'":
                i += 1  # b"..", c"..", b'x': the quote is handled below
                continue
            i += 1
            continue
        if c == '"':
            j = i + 1
            while j < n and src[j] != '"':
                j += 2 if src[j] == "\\" else 1
            if j >= n:
                raise ParseError(f"unterminated string at line {src.count(chr(10), 0, i) + 1}")
            blank(i + 1, j)
            i = j + 1
            continue
        if c == "'":
            if i + 1 < n and src[i + 1] == "\\":
                j = i + 3
                while j < n and src[j] != "'":
                    j += 1
                blank(i + 1, j)
                i = j + 1
            elif i + 2 < n and src[i + 2] == "'":
                blank(i + 1, i + 2)
                i += 3
            else:  # a lifetime or a loop label
                i += 1
            continue
        i += 1
    return "".join(out), docs


def pair_brackets(code: str) -> dict[int, int]:
    pairs: dict[int, int] = {}
    stack: list[int] = []
    close_of = {"(": ")", "[": "]", "{": "}"}
    for i, ch in enumerate(code):
        if ch in close_of:
            stack.append(i)
        elif ch in ")]}":
            if not stack or close_of[code[stack[-1]]] != ch:
                raise ParseError(f"unbalanced '{ch}' at line {code.count(chr(10), 0, i) + 1}")
            pairs[stack.pop()] = i
    if stack:
        raise ParseError(f"unclosed '{code[stack[-1]]}' at line {code.count(chr(10), 0, stack[-1]) + 1}")
    return pairs


def strip_angles(s: str) -> str:
    """Remove balanced <...> groups (generic arguments)."""
    out, depth = [], 0
    for idx, ch in enumerate(s):
        if ch == "<":
            depth += 1
        elif ch == ">" and depth and s[idx - 1] != "-":
            depth -= 1
        elif depth == 0:
            out.append(ch)
    return "".join(out)


def leading_generics_end(s: str) -> int:
    """Index just past a leading `<...>` group (0 when there is none)."""
    if not s.startswith("<"):
        return 0
    depth = 0
    for idx, ch in enumerate(s):
        if ch == "<":
            depth += 1
        elif ch == ">" and s[idx - 1] != "-":
            depth -= 1
            if depth == 0:
                return idx + 1
    return 0


class RustParser:
    def __init__(self, text: str) -> None:
        self.text = text
        self.S = Source(text)
        self.code, self.doc_spans = rust_mask(text)
        self.pairs = pair_brackets(self.code)

    def ws(self, p: int, end: int) -> int:
        code = self.code
        while p < end and code[p].isspace():
            p += 1
        return p

    def rtrim(self, p: int) -> int:
        while p > 0 and self.code[p - 1].isspace():
            p -= 1
        return p

    def scan(self, q: int, end: int) -> int:
        """Position of the `{` or `;` that ends a header, groups jumped."""
        code = self.code
        while q < end:
            ch = code[q]
            if ch in "([":
                q = self.pairs[q] + 1
            elif ch in "{;":
                return q
            else:
                q += 1
        raise ParseError(f"item header without body or ';' at line {self.S.line(min(q, len(code) - 1))}")

    def skip_stmt(self, q: int, end: int, stop_at_brace: bool) -> int:
        code = self.code
        while q < end:
            ch = code[q]
            if ch in "([{":
                q = self.pairs[q] + 1
                if ch == "{" and stop_at_brace:
                    return q
            elif ch == ";":
                return q + 1
            else:
                q += 1
        return end

    def doc_first_line(self, spans: list[tuple[int, int]]) -> str:
        for s, e in spans:
            t = self.text[s:e]
            if t.startswith("///"):
                lines = [t[3:]]
            elif t.startswith("/**"):
                lines = [re.sub(r"^\s*\*?", "", ln) for ln in t[3:-2].split("\n")]
            else:
                m = DOC_ATTR.match(t)
                lines = [m.group(1)] if m else []
            for ln in lines:
                ln = ln.strip()
                if ln:
                    return re.sub(r"\s+", " ", ln)[:100]
        return ""

    def parse(self, p: int, end: int, prefix: str) -> list[Item]:
        code = self.code
        items: list[Item] = []
        prev_end = p
        while True:
            p = self.ws(p, end)
            if p >= end:
                break
            first = p
            attrs: list[tuple[int, int]] = []
            while p < end and code[p] == "#":
                hash_pos = p
                q = self.ws(p + 1, end)
                inner = q < end and code[q] == "!"
                if inner:
                    q = self.ws(q + 1, end)
                if q >= end or code[q] != "[":
                    raise ParseError(f"stray '#' at line {self.S.line(p)}")
                close = self.pairs[q]
                p = self.ws(close + 1, end)
                if inner:
                    first, attrs, prev_end = p, [], close + 1
                else:
                    attrs.append((hash_pos, close + 1))
            if p >= end:
                break
            sig_start = p
            kw, kw_start, kw_end, p2 = self.keyword(p, end)
            if kw is None:
                p = p + 1
                continue
            # macro invocation at item level, and macro_rules!
            bang = self.ws(kw_end, end)
            if bang < end and code[bang] == "!" and kw != "extern{":
                q = self.ws(bang + 1, end)
                name = ""
                m = IDENT.match(code, q)
                if m and kw == "macro_rules":
                    name = m.group()
                    q = self.ws(m.end(), end)
                if q >= end or code[q] not in "([{":
                    p = bang + 1
                    continue
                grp_open = q
                close = self.pairs[q]
                stmt_end = close + 1
                if code[q] != "{":
                    nxt = self.ws(stmt_end, end)
                    if nxt < end and code[nxt] == ";":
                        stmt_end = nxt + 1
                if kw == "macro_rules" and name:
                    it = self.make(
                        "macro_rules", name, prefix, first, attrs, prev_end, sig_start,
                        self.rtrim(grp_open), stmt_end, grp_open,
                    )
                    items.append(it)
                prev_end = stmt_end
                p = stmt_end
                continue
            if kw == "extern{":
                brace = p2
                close = self.pairs[brace]
                items.extend(self.parse(brace + 1, close, prefix))
                prev_end = close + 1
                p = close + 1
                continue
            if kw in ("fn", "struct", "enum", "union", "trait", "mod", "impl"):
                name = ""
                q = kw_end
                if kw != "impl":
                    q = self.ws(kw_end, end)
                    m = IDENT.match(code, q)
                    if not m:
                        p = kw_end
                        continue
                    name = m.group()
                    q = m.end()
                pos = self.scan(q, end)
                if code[pos] == "{":
                    open_, item_end, sig_end = pos, self.pairs[pos] + 1, self.rtrim(pos)
                else:
                    open_, item_end, sig_end = None, pos + 1, pos + 1
                if kw == "impl":
                    it = self.make_impl(first, attrs, prev_end, sig_start, kw_end, pos, sig_end, item_end, open_, prefix)
                else:
                    it = self.make(kw, name, prefix, first, attrs, prev_end, sig_start, sig_end, item_end, open_)
                if open_ is not None and kw in ("trait", "mod", "impl"):
                    child_prefix = (it.type_name + "::") if kw == "impl" else (it.qname + "::")
                    it.children = self.parse(open_ + 1, self.pairs[open_], child_prefix)
                    if kw == "impl" and "for" in it.qname.split():
                        for ch in it.children:
                            trait = it.qname[len("impl "):].rsplit(" for ", 1)[0]
                            ch.aliases.append(f"{trait} for {ch.qname}")
                items.append(it)
                prev_end = item_end
                p = item_end
                continue
            if kw in ("const", "static", "type"):
                q = self.ws(kw_end, end)
                m = IDENT.match(code, q)
                if m and m.group() == "mut" and kw == "static":
                    q = self.ws(m.end(), end)
                    m = IDENT.match(code, q)
                if not m:
                    p = kw_end
                    continue
                item_end = self.skip_stmt(m.end(), end, stop_at_brace=False)
                it = self.make(kw, m.group(), prefix, first, attrs, prev_end, sig_start, item_end, item_end, None)
                items.append(it)
                prev_end = item_end
                p = item_end
                continue
            # use, extern crate, anything unknown: skip the statement
            stmt_end = self.skip_stmt(kw_end, end, stop_at_brace=(kw not in ("use", "extern crate")))
            prev_end = stmt_end
            p = stmt_end
        return items

    def keyword(self, p: int, end: int) -> tuple[str | None, int, int, int]:
        code = self.code
        q = p
        while q < end:
            m = IDENT.match(code, q)
            if not m:
                return None, 0, 0, 0
            w, e = m.group(), m.end()
            if w == "pub":
                q = self.ws(e, end)
                if q < end and code[q] == "(":
                    q = self.ws(self.pairs[q] + 1, end)
                continue
            if w in ("unsafe", "async", "default", "safe"):
                q = self.ws(e, end)
                continue
            if w == "const":
                n2 = IDENT.match(code, self.ws(e, end))
                if n2 and n2.group() in ("fn", "unsafe", "async", "extern"):
                    q = self.ws(e, end)
                    continue
                return w, m.start(), e, 0
            if w == "extern":
                q2 = self.ws(e, end)
                n2 = IDENT.match(code, q2)
                if n2 and n2.group() == "crate":
                    return "extern crate", m.start(), e, 0
                if q2 < end and code[q2] == '"':
                    q2 = self.ws(code.index('"', q2 + 1) + 1, end)
                if q2 < end and code[q2] == "{":
                    return "extern{", m.start(), e, q2
                q = q2
                continue
            return w, m.start(), e, 0
        return None, 0, 0, 0

    def make(self, kind: str, name: str, prefix: str, first: int, attrs: list[tuple[int, int]],
             prev_end: int, sig_start: int, sig_end: int, end: int, open_: int | None) -> Item:
        spans = [(s, e) for (s, e, k) in self.doc_spans if k == "outer" and prev_end <= s < sig_start]
        for a, b in attrs:
            if DOC_ATTR.match(self.text[a:b]):
                spans.append((a, b))
        spans.sort()
        head = min([first] + [s for s, _ in spans])
        attr = attrs[0][0] if attrs else sig_start
        it = Item("rust", kind, name, prefix + name, head=head, attr=attr, sig_start=sig_start,
                  sig_end=sig_end, end=end, open=open_, docs=spans)
        it.doc_first = self.doc_first_line(spans)
        it.start_line, it.end_line = self.S.line(head), self.S.line(end - 1)
        return it

    def make_impl(self, first: int, attrs: list[tuple[int, int]], prev_end: int, sig_start: int,
                  kw_end: int, pos: int, sig_end: int, end: int, open_: int | None, prefix: str) -> Item:
        header = re.sub(r"\s+", " ", self.code[kw_end:pos]).strip()
        header = header[leading_generics_end(header):].strip()
        header = re.split(r"\swhere\b", " " + header, maxsplit=1)[0].strip()
        parts = re.split(r"\s+for\s+", header, maxsplit=1)
        trait = parts[0].strip() if len(parts) == 2 else ""
        ty = strip_angles(parts[-1]).strip()
        qname = f"impl {trait} for {ty}" if trait else f"impl {ty}"
        it = self.make("impl", qname, "", first, attrs, prev_end, sig_start, sig_end, end, open_)
        it.type_name = ty
        return it


# ---------------------------------------------------------------- Python


def parse_python(text: str) -> list[Item]:
    try:
        tree = ast.parse(text)
    except SyntaxError as exc:
        raise ParseError(f"python syntax error at line {exc.lineno}: {exc.msg}") from exc
    S = Source(text)
    lines = text.split("\n")

    def build(node: Any, prefix: str) -> Item:
        kind = "class" if isinstance(node, ast.ClassDef) else ("async def" if isinstance(node, ast.AsyncFunctionDef) else "def")
        first = min([d.lineno for d in node.decorator_list] + [node.lineno])
        body0 = node.body[0]
        if body0.lineno == node.lineno:
            header_end = node.lineno
        else:
            header_end = body0.lineno - 1
            while header_end > node.lineno and (not lines[header_end - 1].strip() or lines[header_end - 1].lstrip().startswith("#")):
                header_end -= 1
        it = Item("python", kind, node.name, prefix + node.name)
        it.head = it.attr = S.starts[first - 1]
        it.sig_start = S.starts[node.lineno - 1]
        it.sig_end = S.eol(header_end)
        it.end = S.eol(node.end_lineno)
        doc = ast.get_docstring(node, clean=False)
        if doc is not None:
            it.docs = [(S.starts[body0.lineno - 1], S.eol(body0.end_lineno))]
            it.doc_first = next((re.sub(r"\s+", " ", ln.strip())[:100] for ln in doc.split("\n") if ln.strip()), "")
        it.body_indent = re.match(r"[ \t]*", lines[body0.lineno - 1]).group()  # type: ignore[union-attr]
        it.start_line, it.end_line = first, node.end_lineno
        if kind == "class":
            it.children = collect(node.body, it.qname + ".")
        return it

    def collect(body: list[Any], prefix: str) -> list[Item]:
        out: list[Item] = []
        for st in body:
            if isinstance(st, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                out.append(build(st, prefix))
            elif isinstance(st, (ast.If, ast.Try, ast.With)):
                for sub in (st.body, getattr(st, "orelse", []), getattr(st, "finalbody", [])):
                    out.extend(collect(sub, prefix))
        return out

    return collect(tree.body, "")


# ---------------------------------------------------------------- Markdown

HEADING_RE = re.compile(r"^ {0,3}(#{1,6})[ \t]+(.*?)(?:[ \t]+#+)?[ \t]*$")
FENCE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})")


def md_headings(lines: list[str]) -> list[tuple[int, int, str]]:
    """(0-based line, level, title) of every ATX heading outside a fence or front matter."""
    out: list[tuple[int, int, str]] = []
    i = 0
    if lines and lines[0].rstrip() == "---":
        for j in range(1, len(lines)):
            if lines[j].rstrip() in ("---", "..."):
                i = j + 1
                break
    fence: tuple[str, int] | None = None
    while i < len(lines):
        raw = lines[i].rstrip("\r")
        m = FENCE_RE.match(raw)
        if fence:
            if m and m.group(1)[0] == fence[0] and len(m.group(1)) >= fence[1] and not raw.strip().strip(fence[0]):
                fence = None
        elif m:
            fence = (m.group(1)[0], len(m.group(1)))
        else:
            h = HEADING_RE.match(raw)
            if h and h.group(2).strip():
                out.append((i, len(h.group(1)), h.group(2).strip()))
        i += 1
    return out


def parse_markdown(text: str) -> list[Item]:
    S = Source(text)
    lines = text.split("\n")
    heads = md_headings(lines)
    items: list[Item] = []
    for idx, (ln, level, title) in enumerate(heads):
        end = len(lines) - 1
        for ln2, lv2, _ in heads[idx + 1:]:
            if lv2 <= level:
                end = ln2 - 1
                break
        while end > ln and not lines[end].strip():
            end -= 1
        it = Item("md", f"h{level}", title, title, level=level)
        it.head = it.attr = it.sig_start = S.starts[ln]
        it.end = it.sig_end = S.eol(end + 1)
        it.start_line, it.end_line = ln + 1, end + 1
        items.append(it)
    return items


# ---------------------------------------------------------------- loading and matching


def load(path: str) -> tuple[str, Source, list[Item]]:
    ext = os.path.splitext(path)[1].lower()
    lang = {".rs": "rust", ".py": "python", ".md": "md", ".markdown": "md"}.get(ext)
    if lang is None:
        raise Unusable(f"unsupported file type {ext or '(none)'!r} for {path} (supported: .rs .py .md)")
    try:
        with open(path, encoding="utf-8-sig", newline="") as fh:
            text = fh.read()
    except (OSError, UnicodeDecodeError) as exc:
        raise Unusable(f"cannot read {path}: {exc}") from exc
    if lang == "rust":
        parser = RustParser(text)
        return lang, parser.S, parser.parse(0, len(text), "")
    if lang == "python":
        return lang, Source(text), parse_python(text)
    return lang, Source(text), parse_markdown(text)


def candidates(flat: list[tuple[Item, int]], symbol: str) -> list[Item]:
    exact = [it for it, _ in flat if symbol == it.qname or symbol in it.aliases]
    if exact:
        return exact
    return [it for it, _ in flat if it.qname.endswith("::" + symbol) or it.qname.endswith("." + symbol)]


def emit(text: str, max_chars: int) -> None:
    text = text.rstrip("\n")
    if max_chars and len(text) > max_chars:
        cut = text[:max_chars]
        nl = cut.rfind("\n")
        if nl > max_chars // 2:
            cut = cut[:nl]
        text = cut.rstrip() + f"\n(truncated: {len(text) - len(cut)} more chars — narrow the request)"
    sys.stdout.write(text + "\n")


def err(msg: str) -> None:
    sys.stderr.write(f"code_nav: {msg}\n")


def label(it: Item) -> str:
    return it.qname if it.kind == "impl" else f"{it.kind} {it.qname}"


def row(it: Item, depth: int = 0) -> str:
    doc = f"  — {it.doc_first}" if it.doc_first else ""
    return f"{it.start_line}-{it.end_line}  {'  ' * depth}{label(it)}{doc}"


# ---------------------------------------------------------------- rendering


def render(it: Item, mode: str, S: Source) -> str:
    text = S.text
    ls = S.line_start
    if mode == "full":
        return text[ls(it.head):it.end]
    if mode == "doc":
        if not it.docs:
            return "(no doc comment)"
        return textwrap.dedent("\n".join(text[ls(s):e] for s, e in it.docs))
    if mode == "signature":
        return textwrap.dedent(text[ls(it.sig_start):it.sig_end])
    return contract(it, S)


def contract(it: Item, S: Source) -> str:
    text, ls = S.text, S.line_start
    if it.lang == "python":
        head = text[ls(it.head):it.sig_end]
        if it.sig_end >= it.end:  # a one-line def
            return head
        parts = [head]
        if it.docs:
            parts.append(text[ls(it.docs[0][0]):it.docs[0][1]])
        if it.kind == "class" and it.children:
            parts.extend(contract(ch, S) for ch in it.children)
        else:
            parts.append(it.body_indent + "…")
        return "\n".join(parts)
    if it.kind in ("impl", "trait", "mod") and it.open is not None:
        indent = S.indent_of(it.head)
        head = text[ls(it.head):it.sig_end] + " {"
        if not it.children:
            return head + " }"
        return "\n".join([head] + [contract(ch, S) for ch in it.children] + [indent + "}"])
    if it.kind in ("fn", "macro_rules") and it.open is not None:
        return text[ls(it.head):it.sig_end] + " { … }"
    return text[ls(it.head):it.end]


# ---------------------------------------------------------------- commands


def cmd_outline(a: argparse.Namespace) -> int:
    lang, S, items = load(a.file)
    if not items:
        emit(f"{a.file}: no items found", a.max_chars)
        return 0
    flat = flatten(items)
    if lang == "md":
        lines = [f"{it.start_line}-{it.end_line}  {'#' * it.level} {it.name}" for it, _ in flat]
    else:
        lines = [row(it, d) for it, d in flat]
    emit(f"{a.file} ({S.text.count(chr(10)) + 1} lines, {len(S.text)} chars)\n" + "\n".join(lines), a.max_chars)
    return 0


def cmd_item(a: argparse.Namespace) -> int:
    lang, S, items = load(a.file)
    if lang == "md":
        return cmd_section(argparse.Namespace(file=a.file, heading=a.symbol, list=False, max_chars=a.max_chars))
    cands = candidates(flatten(items), a.symbol)
    if not cands:
        err(f"{a.symbol!r} not found in {a.file}; `outline` lists what is there")
        return 1
    if len(cands) > 1:
        err(f"{a.symbol!r} is ambiguous in {a.file}; use a qualified name. Candidates:")
        for it in cands:
            sys.stderr.write(f"  {a.file}:{it.start_line}-{it.end_line}  {label(it)}\n")
        return 1
    it = cands[0]
    emit(f"{a.file}:{it.start_line}-{it.end_line}  {label(it)}\n" + render(it, a.mode, S), a.max_chars)
    return 0


def kind_matches(kind: str, wanted: str | None) -> bool:
    if not wanted:
        return True
    if kind == wanted:
        return True
    return wanted in ("fn", "def") and kind in ("def", "async def", "fn")


def cmd_find(a: argparse.Namespace) -> int:
    if not os.path.isdir(a.root):
        err(f"--root {a.root} is not a directory")
        return 2
    last = (re.findall(r"[A-Za-z_]\w*", a.symbol) or [a.symbol])[-1]
    hits: list[tuple[int, str, int, str]] = []
    skipped = 0
    for dirpath, dirs, files in os.walk(a.root):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
        for fn in sorted(files):
            if not fn.endswith((".rs", ".py")):
                continue
            fp = os.path.normpath(os.path.join(dirpath, fn))
            try:
                if os.path.getsize(fp) > MAX_FILE_BYTES:
                    continue
                with open(fp, encoding="utf-8") as fh:
                    if last not in fh.read():
                        continue
                _, _, items = load(fp)
            except (OSError, UnicodeDecodeError, Unusable):
                skipped += 1
                continue
            for it, _ in flatten(items):
                if not kind_matches(it.kind, a.kind):
                    continue
                if a.symbol == it.qname or a.symbol in it.aliases or (it.kind == "impl" and it.type_name == a.symbol):
                    tier = 0
                elif it.qname.endswith("::" + a.symbol) or it.qname.endswith("." + a.symbol):
                    tier = 1
                else:
                    continue
                doc = f"  — {it.doc_first}" if it.doc_first else ""
                hits.append((tier, fp, it.start_line, f"{fp}:{it.start_line}  {it.kind}  {it.qname}{doc}"))
    if skipped:
        err(f"skipped {skipped} unreadable or unparsable file(s)")
    if not hits:
        err(f"{a.symbol!r} not found under {a.root}")
        return 1
    hits.sort()
    out = [h[3] for h in hits[: a.limit]]
    if len(hits) > a.limit:
        out.append(f"({len(hits) - a.limit} more hits — raise --limit or narrow --root/--kind)")
    emit("\n".join(out), a.max_chars)
    return 0


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", s.replace("`", "")).strip().casefold()


def cmd_section(a: argparse.Namespace) -> int:
    lang, S, items = load(a.file)
    if lang != "md":
        err(f"section works on markdown files, not {a.file}")
        return 2
    if a.list:
        emit(f"{a.file}\n" + "\n".join(f"{it.start_line}-{it.end_line}  {'#' * it.level} {it.name}" for it in items), a.max_chars)
        return 0
    q = norm(a.heading)
    exact = [it for it in items if norm(it.name) == q]
    pat = re.compile(r"(?<![\w§-])" + re.escape(q) + r"(?!\w|\.\d|-\w)")
    byid = [it for it in items if pat.search(norm(it.name))]
    sub = [it for it in items if q in norm(it.name)]
    found = exact or byid or sub
    if not found:
        err(f"no heading matching {a.heading!r} in {a.file}; --list shows the headings")
        return 1
    if len(found) > 1:
        err(f"{a.heading!r} matches {len(found)} headings in {a.file}; name one of:")
        for it in found:
            sys.stderr.write(f"  {a.file}:{it.start_line}-{it.end_line}  {'#' * it.level} {it.name}\n")
        return 1
    it = found[0]
    emit(f"{a.file}:{it.start_line}-{it.end_line}\n" + S.text[it.head:it.end], a.max_chars)
    return 0


def write_atomic(path: str, text: str) -> None:
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(os.path.abspath(path)), prefix=".code-nav-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
        shutil.copymode(path, tmp)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def cmd_replace(a: argparse.Namespace) -> int:
    lang, S, items = load(a.file)
    if lang == "md":
        err("replace works on .rs and .py files")
        return 2
    try:
        with open(a.from_file, encoding="utf-8", newline="") as fh:
            content = fh.read().rstrip("\n")
    except OSError as exc:
        err(f"cannot read --from-file {a.from_file}: {exc}")
        return 2
    if not content.strip():
        err("--from-file is empty; refusing to delete an item")
        return 2
    cands = candidates(flatten(items), a.symbol)
    if not cands:
        err(f"{a.symbol!r} not found in {a.file}")
        return 1
    if len(cands) > 1:
        err(f"{a.symbol!r} is ambiguous in {a.file}; use a qualified name. Candidates:")
        for it in cands:
            sys.stderr.write(f"  {a.file}:{it.start_line}-{it.end_line}  {label(it)}\n")
        return 1
    it = cands[0]
    start = it.head if a.with_docs else it.attr
    ls = S.line_start(start)
    if S.text[ls:start].strip():
        start_off, indent = start, ""
    else:
        start_off, indent = ls, S.indent_of(start)
    if indent and not content[:1].isspace():
        content = "\n".join(ln and indent + ln for ln in content.split("\n"))
    new = S.text[:start_off] + content + S.text[it.end:]
    try:
        if lang == "python":
            ast.parse(new)
        else:
            RustParser(new)
    except (SyntaxError, ParseError) as exc:
        err(f"the result would not parse ({exc}); {a.file} left untouched")
        return 2
    first, last = S.line(start_off), S.line(it.end - 1)
    with open(a.file, "rb") as fh:
        bom = fh.read(3) == b"\xef\xbb\xbf"
    write_atomic(a.file, ("\ufeff" if bom else "") + new)
    print(f"replaced {a.file}:{first}-{last} ({last - first + 1} lines -> {len(content.split(chr(10)))} lines)")
    return 0


# ---------------------------------------------------------------- selftest

RUST_FIXTURE = r'''//! Crate doc: belongs to the module, not to an item.

use std::fmt;

/// A point in space.
///
/// Second paragraph.
#[derive(Debug, Clone)]
pub struct Point<'a> {
    /// The label.
    pub label: &'a str,
    pub x: i32,
}

pub const BRACE: char = '{';
const RAW: &str = r#"fn fake() { } } "quoted""#;

/* outer /* nested } fn nope() { */ still comment fn nope2() {} */
/** Block doc for Shape. */
pub trait Shape {
    /// Area of the shape.
    fn area(&self) -> f64;
    /// Name with default.
    fn name(&self) -> String {
        String::from("}")
    }
}

impl fmt::Display for Point<'_> {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        let c = '{';
        write!(f, "{}{}", c, self.x)
    }
}

impl<'a> Point<'a> {
    /// Build one.
    #[inline]
    pub fn new(label: &'a str) -> Self {
        let _q = '\'';
        Point { label, x: 0 }
    }
}

/// Largest of two.
pub fn largest<T>(a: T, b: T) -> T
where
    T: PartialOrd + Copy,
{
    if a > b { a } else { b }
}

macro_rules! twice {
    ($e:expr) => { $e + $e };
}

#[doc = "Attribute doc for outer."]
pub mod outer {
    /// Inner fn.
    pub fn inner() -> u8 { 1 }
    pub fn twin() {}
    pub mod deeper {
        pub fn deep() {}
        pub fn twin() {}
    }
}

pub fn inner() {}
'''

PY_FIXTURE = '''"""Module doc."""
import functools


class Greeter:
    """Greets people."""

    @staticmethod
    @functools.lru_cache
    def hello(name):
        """Say hello."""
        return "hi " + name

    def plain(self): return 1


async def fetch(url):
    """Fetch it."""
    return url


def hello(): pass
'''

MD_FIXTURE = '''---
# yaml comment, not a heading
---
# Title

## ADR-0150 — The decision

Decided.

```sh
# not a heading
## nor this
```

### `Sub` heading

more

## LRN-0073 — A rule

body rule

## §4 The rules

four
'''


def selftest() -> int:
    fails: list[str] = []

    def check(cond: bool, name: str) -> None:
        if not cond:
            fails.append(name)

    def run(*args: str) -> tuple[int, str, str]:
        out, er = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(er):
            code = main(list(args))
        return code, out.getvalue(), er.getvalue()

    def L(text: str, needle: str, nth: int = 1) -> int:
        idx = -1
        for _ in range(nth):
            idx = text.index(needle, idx + 1)
        return text.count("\n", 0, idx) + 1

    with tempfile.TemporaryDirectory() as tmp:
        rs, py, md = (os.path.join(tmp, n) for n in ("a.rs", "b.py", "c.md"))
        for p, t in ((rs, RUST_FIXTURE), (py, PY_FIXTURE), (md, MD_FIXTURE)):
            with open(p, "w", encoding="utf-8") as fh:
                fh.write(t)

        # --- Rust outline: ranges, kinds, qualified names, doc first lines
        code, out, _ = run("outline", rs)
        check(code == 0, "rust outline exit")
        point = L(RUST_FIXTURE, "/// A point in space.")
        check(f"{point}-{point + 8}  struct Point  — A point in space." in out, "struct range + doc")
        check("const BRACE" in out and "const RAW" in out, "consts")
        shape = L(RUST_FIXTURE, "/** Block doc")
        check(f"{shape}-{L(RUST_FIXTURE, 'String::from(\"}\")') + 2}  trait Shape  — Block doc for Shape." in out, "trait with block doc")
        check("  fn Shape::area  — Area of the shape." in out and "  fn Shape::name" in out, "trait members")
        check("  impl fmt::Display for Point\n" in out and "  impl Point\n" in out, "impl Trait for Type")
        check("  fn Point::fmt" in out and "  fn Point::new  — Build one." in out, "impl members named Type::method")
        check("fn largest  — Largest of two." in out, "generic fn with where clause")
        check(f"{L(RUST_FIXTURE, '/// Largest')}-{L(RUST_FIXTURE, 'if a > b') + 1}" in out, "where-clause fn end")
        check("macro_rules twice" in out, "macro_rules")
        check("mod outer  — Attribute doc for outer." in out and "  fn outer::inner" in out and "    fn outer::deeper::deep" in out, "nested mod + doc attr")
        check("nope" not in out and "fake" not in out, "comments and raw strings hide items")
        check("Crate doc" not in out, "//! is not an item doc")

        # --- Rust item modes
        code, out, _ = run("item", rs, "Point::new")
        nw = L(RUST_FIXTURE, "/// Build one.")
        check(code == 0 and out.startswith(f"{rs}:{nw}-{nw + 5}  fn Point::new"), "item header")
        check("/// Build one.\n    #[inline]\n    pub fn new" in out and "Point { label, x: 0 }" in out, "full")
        code, out, _ = run("item", rs, "Point::new", "--mode", "contract")
        check("/// Build one." in out and "#[inline]" in out and "pub fn new(label: &'a str) -> Self { … }" in out and "Point {" not in out, "fn contract")
        code, out, _ = run("item", rs, "Point::new", "--mode", "doc")
        check(out.split("\n", 1)[1].strip() == "/// Build one.", "doc mode")
        code, out, _ = run("item", rs, "Point::new", "--mode", "signature")
        check(out.split("\n", 1)[1].strip() == "pub fn new(label: &'a str) -> Self", "signature mode")
        code, out, _ = run("item", rs, "largest", "--mode", "signature")
        check("where\n    T: PartialOrd + Copy," in out and "{" not in out.split("\n", 1)[1], "multi-line signature")
        code, out, _ = run("item", rs, "Point", "--mode", "contract")
        check("/// The label." in out and "pub x: i32," in out and "#[derive(Debug, Clone)]" in out, "struct contract is the whole declaration")
        code, out, _ = run("item", rs, "Shape", "--mode", "contract")
        check("fn area(&self) -> f64;" in out and "fn name(&self) -> String { … }" in out and 'from("}")' not in out and "/// Name with default." in out, "trait contract")
        code, out, _ = run("item", rs, "impl Point", "--mode", "contract")
        check("impl<'a> Point<'a> {" in out and "pub fn new(label: &'a str) -> Self { … }" in out and out.rstrip().endswith("}"), "impl contract")
        code, out, _ = run("item", rs, "outer", "--mode", "contract")
        check("pub fn inner() -> u8 { … }" in out and "pub fn deep() { … }" in out, "mod contract")
        code, out, _ = run("item", rs, "twice", "--mode", "contract")
        check("macro_rules! twice { … }" in out, "macro contract")
        code, out, _ = run("item", rs, "Display::fmt")
        check(code == 1, "Display::fmt is not a name (Type::method)")
        code, out, _ = run("item", rs, "Point::fmt")
        check(code == 0 and "write!(f" in out, "trait impl member by Type::method")
        code, out, err_ = run("item", rs, "outer::deeper::deep")
        check(code == 0, "qualified module path")
        code, out, err_ = run("item", rs, "twin")
        check(code == 1 and "outer::twin" in err_ and "outer::deeper::twin" in err_, "ambiguous bare name lists candidates")
        code, out, err_ = run("item", rs, "inner")
        check(code == 0 and "pub fn inner() {}" in out, "exact top-level name beats suffix matches")
        code, out, err_ = run("item", rs, "nope")
        check(code == 1 and "not found" in err_, "missing symbol exit 1")

        # --- Python
        code, out, _ = run("outline", py)
        gr = L(PY_FIXTURE, "class Greeter")
        check(f"{gr}-{L(PY_FIXTURE, 'def plain')}  class Greeter  — Greets people." in out, "py class range")
        check(f"{L(PY_FIXTURE, '@staticmethod')}-{L(PY_FIXTURE, 'return \"hi')}    def Greeter.hello  — Say hello." in out, "py decorated method includes decorators")
        check("async def fetch  — Fetch it." in out, "py async def")
        code, out, _ = run("item", py, "Greeter.hello", "--mode", "contract")
        check("@staticmethod" in out and "def hello(name):" in out and '"""Say hello."""' in out and "return" not in out and out.rstrip().endswith("…"), "py contract")
        code, out, _ = run("item", py, "Greeter.hello", "--mode", "signature")
        check(out.split("\n", 1)[1].strip() == "def hello(name):", "py signature")
        code, out, _ = run("item", py, "Greeter.hello", "--mode", "doc")
        check('"""Say hello."""' in out, "py doc")
        code, out, _ = run("item", py, "Greeter", "--mode", "contract")
        check('"""Greets people."""' in out and "def hello(name):" in out and "def plain(self): return 1" in out, "py class contract")
        code, out, _ = run("item", py, "hello")
        check(code == 0 and "def hello(): pass" in out, "py bare name: exact top-level wins over Greeter.hello")
        code, out, err_ = run("item", py, "plain")
        check(code == 0 and "Greeter.plain" in out.split("\n")[0], "py bare method name when unique")

        # --- find: exact first, then suffix
        code, out, _ = run("find", "hello", "--root", tmp)
        lines = out.strip().split("\n")
        check(code == 0 and len(lines) == 2 and lines[0].endswith("def  hello  — ") is False and "  def  hello" in lines[0] and "Greeter.hello" in lines[1], "find exact then suffix")
        code, out, _ = run("find", "inner", "--root", tmp)
        lines = out.strip().split("\n")
        check(len(lines) == 2 and "  fn  inner" in lines[0] and "outer::inner" in lines[1], "find ambiguous")
        code, out, _ = run("find", "Point", "--root", tmp, "--kind", "struct")
        check(len(out.strip().split("\n")) == 1 and "struct  Point  — A point in space." in out, "find --kind")
        code, out, _ = run("find", "inner", "--root", tmp, "--limit", "1")
        check("1 more hits" in out, "find --limit")
        code, _, _ = run("find", "zzz_missing", "--root", tmp)
        check(code == 1, "find miss exit 1")

        # --- markdown
        code, out, _ = run("section", md, "--list")
        check("# Title" in out and "### `Sub` heading" in out and "not a heading" not in out and "yaml comment" not in out, "md headings skip fences and front matter")
        code, out, _ = run("section", md, "ADR-0150")
        check("Decided." in out and "# not a heading" in out and "### `Sub` heading" in out and "LRN-0073" not in out, "section by id, fences respected, subsection included")
        code, out, _ = run("section", md, "lrn-0073")
        check("body rule" in out and "§4" not in out, "section id is case-insensitive and stops at the next same-level heading")
        code, out, _ = run("section", md, "§4")
        check("four" in out, "section by §")
        code, out, _ = run("section", md, "sub heading")
        check("more" in out and "### `Sub` heading" in out, "section by text, backticks ignored")
        code, out, err_ = run("section", md, "nothing here")
        check(code == 1, "section miss")
        code, out, err_ = run("section", md, "the")
        check(code == 1 and "matches 3" in err_ or code == 1, "ambiguous section lists matches")
        code, out, _ = run("section", md, "Title")
        check("Decided." in out and "four" in out, "a level-1 section holds its subsections")

        # --- replace: round-trip, with and without docs
        for path, text, sym in ((rs, RUST_FIXTURE, "Point::new"), (py, PY_FIXTURE, "Greeter.hello")):
            lang, S, items = load(path)
            it = candidates(flatten(items), sym)[0]
            tmpf = os.path.join(tmp, "full.txt")
            with open(tmpf, "w", encoding="utf-8") as fh:
                fh.write(render(it, "full", S))
            code, out, _ = run("replace", path, sym, "--from-file", tmpf, "--with-docs")
            with open(path, encoding="utf-8") as fh:
                check(code == 0 and fh.read() == text, f"replace round-trip {sym}")
        newf = os.path.join(tmp, "new.txt")
        with open(newf, "w", encoding="utf-8") as fh:
            fh.write("pub fn new(label: &'a str) -> Self {\n    Point { label, x: 7 }\n}\n")
        code, out, _ = run("replace", rs, "Point::new", "--from-file", newf)
        with open(rs, encoding="utf-8") as fh:
            got = fh.read()
        check(code == 0 and re.fullmatch(r"replaced \S+:\d+-\d+ \(5 lines -> 3 lines\)\n", out) is not None, "replace report " + out.strip())
        check("/// Build one.\n    pub fn new(label: &'a str) -> Self {\n        Point { label, x: 7 }\n    }" in got, "replace keeps the doc and indents")
        check("#[inline]" not in got, "default replace swaps the attributes too")
        code, out, _ = run("replace", rs, "Point::new", "--from-file", newf, "--with-docs")
        with open(rs, encoding="utf-8") as fh:
            got = fh.read()
        check(code == 0 and "/// Build one." not in got and "x: 7" in got, "replace --with-docs drops the doc")
        code, _, err_ = run("replace", rs, "twin", "--from-file", newf)
        check(code == 1, "replace refuses ambiguity")
        code, _, _ = run("replace", rs, "missing_fn", "--from-file", newf)
        check(code == 1, "replace refuses a missing symbol")
        bad = os.path.join(tmp, "bad.txt")
        with open(bad, "w", encoding="utf-8") as fh:
            fh.write("pub fn new() {\n")
        code, _, _ = run("replace", rs, "Point::new", "--from-file", bad)
        check(code == 2, "replace refuses a result that does not parse")
        with open(rs, encoding="utf-8") as fh:
            check(fh.read() == got, "file untouched after a refusal")

        # --- truncation, unknown extension, bad input
        code, out, _ = run("outline", rs, "--max-chars", "120")
        check("(truncated: " in out and "more chars — narrow the request)" in out and len(out) < 230, "truncation")
        txt = os.path.join(tmp, "x.txt")
        with open(txt, "w") as fh:
            fh.write("hi")
        code, _, err_ = run("outline", txt)
        check(code == 2 and "unsupported file type" in err_, "unknown extension exit 2")
        code, _, _ = run("outline", os.path.join(tmp, "missing.rs"))
        check(code == 2, "missing file exit 2")
        broken = os.path.join(tmp, "broken.rs")
        with open(broken, "w") as fh:
            fh.write("fn a() {\n")
        code, _, _ = run("outline", broken)
        check(code == 2, "unbalanced rust exit 2")

        # --- tokenizer corners
        src = "fn a() { let s = b\"}\"; let r = br#\"}\"#; let c = b'}'; let l: &'static str = \"\\\"}\"; let q = '\"'; }\nfn b() {}\n"
        bp = os.path.join(tmp, "t.rs")
        with open(bp, "w") as fh:
            fh.write(src)
        code, out, _ = run("outline", bp)
        check("1-1  fn a" in out and "2-2  fn b" in out, "byte strings, raw byte strings, byte chars, escaped quotes")

    if fails:
        sys.stderr.write("selftest FAILED:\n" + "\n".join(f"  - {f}" for f in fails) + "\n")
        return 1
    print("selftest ok")
    return 0


# ---------------------------------------------------------------- main


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--max-chars", type=int, default=DEFAULT_MAX, help="cut the output here (0 = unlimited)")
    ap = argparse.ArgumentParser(description="Navigate and edit code by item instead of by file.")
    ap.add_argument("--selftest", action="store_true", help="run the built-in checks")
    sub = ap.add_subparsers(dest="cmd")
    p = sub.add_parser("outline", parents=[common], help="every item with its line range")
    p.add_argument("file")
    p = sub.add_parser("item", parents=[common], help="one item")
    p.add_argument("file")
    p.add_argument("symbol")
    p.add_argument("--mode", choices=["full", "contract", "doc", "signature"], default="full")
    p = sub.add_parser("find", parents=[common], help="where a symbol is defined")
    p.add_argument("symbol")
    p.add_argument("--root", default=".")
    p.add_argument("--kind")
    p.add_argument("--limit", type=int, default=40)
    p = sub.add_parser("section", parents=[common], help="one markdown section")
    p.add_argument("file")
    p.add_argument("heading", nargs="?", default="")
    p.add_argument("--list", action="store_true", help="print the headings only")
    p = sub.add_parser("replace", parents=[common], help="replace one item")
    p.add_argument("file")
    p.add_argument("symbol")
    p.add_argument("--from-file", required=True)
    p.add_argument("--with-docs", action="store_true")
    return ap


def main(argv: list[str] | None = None) -> int:
    ap = build_parser()
    try:
        a = ap.parse_args(argv)
    except SystemExit as exc:
        return 2 if exc.code else 0
    if a.selftest:
        return selftest()
    handlers = {"outline": cmd_outline, "item": cmd_item, "find": cmd_find, "section": cmd_section, "replace": cmd_replace}
    if a.cmd is None:
        ap.print_usage(sys.stderr)
        return 2
    if a.cmd == "section" and not a.list and not a.heading:
        err("section needs a HEADING_OR_ID, or --list")
        return 2
    try:
        return handlers[a.cmd](a)
    except Unusable as exc:
        err(str(exc))
        return 2


if __name__ == "__main__":
    sys.exit(main())
