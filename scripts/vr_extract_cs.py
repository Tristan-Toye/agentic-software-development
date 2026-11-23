#!/usr/bin/env python3
"""Visual review: the C# extractor (a dependency-free declaration parser, no dotnet, no Roslyn).

    vr_extract_cs.py REPO_ROOT [--layers 1,2,3] [--calls f1,f2] [--cfg-test] [-o out.json]
    vr_extract_cs.py --selftest

Normally called through `vr_extract.py csharp ROOT` (same flags). Layers:
  1  projects: one per .csproj (AssemblyName or the file name) and the `depends` edges of its
     `<ProjectReference>` elements. A repository with .cs files outside every .csproj gets one `inferred`
     root project.
  2  namespaces (`module:PROJECT::Ns.Sub`), types (`type:PROJECT::Ns.Type`: class, struct, interface,
     record, enum), the `contains` edges (project > namespace > type > nested type), `uses` edges for base
     types and interfaces (label extends / implements) that resolve inside the repository.
  3  every method, constructor and local top-level-statements body (`fn:PROJECT::Ns.Type::Method`).
  4  (only with --calls) the functions named, their callers and callees, as `calls` edges.

How it reads C#: comments, strings and preprocessor lines are blanked (line numbers kept), then a scanner
walks namespace and type bodies member by member. Method bodies are skipped by brace matching; their text
is read again only for call sites. Test projects (a name ending Tests/Test/Specs, or a reference to xunit,
nunit, MSTest or Microsoft.NET.Test.Sdk) are left out unless --cfg-test.

Call resolution is by name inside the repository, never a guess:
  `M()` (own type, its bases, enclosing types), `this.M()`, `base.M()`, `Type.M()`, `field.M()` /
  `param.M()` / `local.M()` / `this.field.M()` where the receiver's declared type (field, property,
  parameter, primary constructor, `T x`, `var x = new T(..)`) names a repository type, and `new T(..)`
  (the constructor, else the type). Overloads are picked by argument count; two or more candidates, or
  none that fit, are `inferred`. A call on an interface, abstract or virtual member is `dispatch`.
  Extension methods are found by the simple name of the `this` parameter's type. Not derived (no edge):
  receivers that are call results or chains (`a.B().C()`), lambdas and local functions, members reached
  through `var` of a method result, dependency-injection wiring, reflection, and anything outside the
  repository. Property accessors and field initialisers are not scanned for calls.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from bisect import bisect_right

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import vr_lang  # noqa: E402
from vr_lang import Builder, LangError, inferred, line_of, sid, src  # noqa: E402

MODIFIERS = {"public", "private", "protected", "internal", "static", "abstract", "sealed", "partial", "unsafe",
             "new", "readonly", "ref", "file", "required", "async", "virtual", "override", "extern", "volatile",
             "const", "fixed", "implicit", "explicit", "global"}
TYPE_KW = {"class", "struct", "interface", "enum", "record", "delegate"}
KEYWORDS = {"if", "else", "while", "for", "foreach", "switch", "using", "lock", "catch", "nameof", "typeof", "sizeof",
            "default", "checked", "unchecked", "return", "await", "throw", "new", "var", "in", "is", "as", "case", "do",
            "try", "finally", "yield", "when", "where", "fixed", "stackalloc", "base", "this", "out", "ref", "params"}
TEST_NAME = re.compile(r"(\.|^)(Tests?|UnitTests?|IntegrationTests?|Specs?|E2E|AcceptanceTests?)$", re.I)
TEST_REFS = re.compile(r"Microsoft\.NET\.Test\.Sdk|xunit|nunit|MSTest|<IsTestProject>\s*true", re.I)
IDENT = r"[A-Za-z_]\w*"


# ---------------------------------------------------------------- cleaning

def clean(text: str) -> str:
    """Blank comments, string and char literals and preprocessor lines; keep every newline and offset."""
    out = list(text)
    n = len(text)
    i = 0

    def blank(a, b):
        for k in range(a, b):
            if out[k] != "\n":
                out[k] = " "

    line_start = True
    while i < n:
        c = text[i]
        if line_start and c in " \t":
            j = i
            while j < n and text[j] in " \t":
                j += 1
            if j < n and text[j] == "#":
                k = text.find("\n", j)
                k = n if k < 0 else k
                blank(j, k)
                i = k
                continue
        if c == "\n":
            line_start = True
            i += 1
            continue
        if c not in " \t":
            line_start = False
        if c == "/" and text.startswith("//", i):
            k = text.find("\n", i)
            k = n if k < 0 else k
            blank(i, k)
            i = k
            continue
        if c == "/" and text.startswith("/*", i):
            k = text.find("*/", i + 2)
            k = n if k < 0 else k + 2
            blank(i, k)
            i = k
            continue
        if c == "'":
            j = i + 1
            while j < n and text[j] != "'" and text[j] != "\n":
                j += 2 if text[j] == "\\" else 1
            blank(i + 1, min(j, n))
            i = j + 1
            continue
        if c == '"':
            k = i
            while k > 0 and text[k - 1] in "@$":
                k -= 1
            prefix = text[k:i]
            verbatim, interp = "@" in prefix, "$" in prefix
            if text.startswith('"""', i):
                q = 3
                while text.startswith('"' * (q + 1), i):
                    q += 1
                end = text.find('"' * q, i + q)
                end = n if end < 0 else end + q
                blank(i + 1, end - 1)
                i = end
                continue
            j = i + 1
            depth = 0
            while j < n:
                ch = text[j]
                if interp and ch == "{":
                    if text.startswith("{{", j) and depth == 0:
                        j += 2
                        continue
                    depth += 1
                elif interp and ch == "}" and depth > 0:
                    depth -= 1
                elif ch == '"' and depth == 0:
                    if verbatim and text.startswith('""', j):
                        j += 2
                        continue
                    break
                elif ch == '"' and depth > 0:  # a string inside an interpolation hole
                    j = text.find('"', j + 1)
                    j = n if j < 0 else j
                elif ch == "\\" and not verbatim:
                    j += 1
                elif ch == "\n" and not verbatim and depth == 0 and not text.startswith('"""', i):
                    break
                j += 1
            blank(i + 1, min(j, n))
            i = j + 1
            continue
        i += 1
    return "".join(out)


def match(text: str, i: int, open_c: str, close_c: str) -> int:
    """Index of the bracket closing the one at `i`, or len(text) - 1."""
    depth = 0
    n = len(text)
    while i < n:
        c = text[i]
        if c == open_c:
            depth += 1
        elif c == close_c:
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return n - 1


def split_top(s: str) -> list:
    """Split on commas at paren, bracket, brace and angle depth 0."""
    out, depth, cur = [], 0, []
    for ch in s:
        if ch in "([{<":
            depth += 1
        elif ch in ")]}>":
            depth = max(0, depth - 1)
        if ch == "," and depth == 0:
            out.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    tail = "".join(cur)
    if tail.strip():
        out.append(tail)
    return [x.strip() for x in out if x.strip()]


def simple_type(t: str) -> str:
    """`System.Collections.Generic.List<int>?` -> `List`; the dotted name is kept separately by `dotted_type`."""
    return dotted_type(t).rsplit(".", 1)[-1]


def dotted_type(t: str) -> str:
    t = re.sub(r"<.*>", "", t.strip()).replace("?", "").replace("[]", "").replace("global::", "")
    m = re.match(r"\s*(?:\(.*)?([A-Za-z_][\w.]*)", t)
    return m.group(1) if m else ""


# ---------------------------------------------------------------- model

class Type_:
    def __init__(self, name, kind, ns, outer, file, line, project, arity=0):
        self.name, self.kind, self.ns, self.outer, self.file, self.line, self.project = name, kind, ns, outer, file, line, project
        self.arity = arity
        self.bases_raw: list = []
        self.bases: list = []
        self.fields: dict = {}     # member / ctor-param name -> type text
        self.methods: list = []
        self.id = ""
        self.usings: list = []
        self.aliases: dict = {}
        self.nested: dict = {}

    @property
    def full(self) -> str:
        parts = [self.ns] if self.ns else []
        chain, t = [], self
        while t is not None:
            chain.append(t.name)
            t = t.outer
        return ".".join(parts + list(reversed(chain)))


class Method_:
    def __init__(self, name, typ, line, params, body, ret, mods, is_ctor=False, ext=None):
        self.name, self.typ, self.line, self.params, self.body, self.ret, self.mods = name, typ, line, params, body, ret, mods
        self.is_ctor, self.ext = is_ctor, ext
        self.id = ""
        self.sig = ""

    @property
    def virtual(self) -> bool:
        return self.typ.kind == "interface" or bool({"abstract", "virtual", "override"} & set(self.mods))


class File_:
    def __init__(self, rel, project):
        self.rel, self.project = rel, project
        self.usings: list = []
        self.aliases: dict = {}
        self.global_usings: list = []
        self.types: list = []
        self.namespaces: list = []      # (name, line)
        self.top: list = []             # statement spans (start, end)
        self.top_line = 0
        self.clean = ""
        self.starts: list = []

    def line(self, off: int) -> int:
        return bisect_right(self.starts, off)


USING_RE = re.compile(r"^(?:global\s+)?using\s+(static\s+)?(?:(" + IDENT + r")\s*=\s*)?([A-Za-z_][\w.<>,\s?\[\]:]*?)\s*$")


def parse_params(s: str) -> list:
    """[(type text, name, has_default, is_this)] of a parameter list text."""
    out = []
    for p in split_top(s):
        p = re.sub(r"\[[^\]]*\]\s*", "", p)
        has_def = False
        d = p.find("=")
        while d >= 0 and (p[d:d + 2] == "==" or p[d - 1:d] in "!<>="):
            d = p.find("=", d + 2)
        if d >= 0:
            has_def, p = True, p[:d].strip()
        words = p.split()
        is_this = bool(words) and words[0] == "this"
        words = [w for w in words if w not in ("this", "ref", "out", "in", "params", "scoped", "readonly")]
        if len(words) >= 2:
            name = words[-1]
            typ = " ".join(words[:-1])
            out.append((typ, name, has_def, is_this))
        elif words:
            out.append(("", words[-1], has_def, is_this))
    return out


class Scanner:
    def __init__(self, f: File_, text: str, project: str):
        self.f, self.t, self.project = f, text, project

    def ws(self, i, end):
        t = self.t
        while i < end and t[i].isspace():
            i += 1
        return i

    def header(self, i, end):
        """(end index of the header, terminator) scanning from `i` at paren/bracket depth 0."""
        t, depth = self.t, 0
        while i < end:
            c = t[i]
            if c in "([":
                depth += 1
            elif c in ")]":
                depth = max(0, depth - 1)
            elif depth == 0:
                if c == ";" or c == "{":
                    return i, c
                if c == "}":
                    return i, "}"
                if c == "=":
                    nxt = t[i + 1] if i + 1 < end else ""
                    prv = t[i - 1] if i > 0 else ""
                    if nxt == ">":
                        return i, "=>"
                    if nxt == "=" or prv in "=!<>":
                        i += 2 if nxt == "=" else 1
                        continue
                    return i, "="
            i += 1
        return end, ""

    def skip_stmt(self, i, end):
        """From `i`, the index after the next `;` at depth 0 (balancing parens, brackets and braces)."""
        t, depth = self.t, 0
        while i < end:
            c = t[i]
            if c in "([{":
                depth += 1
            elif c in ")]}":
                depth -= 1
                if depth < 0:
                    return i
            elif c == ";" and depth == 0:
                return i + 1
            i += 1
        return end

    def block(self, i, end, ns, outer, usings, aliases, ctx):
        """Scan members in t[i:end]; ctx is "global", "ns" or "type"."""
        t, f = self.t, self.f
        while True:
            i = self.ws(i, end)
            if i >= end:
                return
            while i < end and t[i] == "[":     # attribute lists
                i = self.ws(match(t, i, "[", "]") + 1, end)
            if i >= end:
                return
            hs = i
            he, term = self.header(i, end)
            h = t[hs:he]
            words = h.split()
            core = [w for w in words if w not in MODIFIERS]
            first = core[0] if core else ""
            if term == "}":
                i = he + 1
                continue
            if first == "namespace" or (words and words[0] == "namespace"):
                nm = "".join(h.split()[1:]) if len(words) > 1 else ""
                full = (ns + "." + nm) if ns and nm else (nm or ns)
                f.namespaces.append((full, f.line(hs)))
                if term == "{":
                    close = match(t, he, "{", "}")
                    self.block(he + 1, close, full, outer, list(usings), dict(aliases), "ns")
                    i = close + 1
                    continue
                self.block(he + 1, end, full, outer, usings, aliases, "ns")  # file-scoped
                return
            if (words and words[0] in ("using", "global")) and term == ";":
                m = USING_RE.match(h.strip())
                if m and "(" not in h:
                    static, alias, target = m.group(1), m.group(2), m.group(3)
                    target = re.sub(r"\s+", "", target)
                    if alias:
                        aliases[alias] = target
                    elif not static:
                        usings.append(target)
                        if words[0] == "global":
                            f.global_usings.append(target)
                    i = he + 1
                    continue
            if first == "extern":
                i = he + 1 if term == ";" else he + 1
                continue
            m = re.match(r"^\s*(?:(?:%s)\s+)*(record(?:\s+(?:class|struct))?|class|struct|interface|enum|delegate)\s+(%s)\s*(<[^()]*?>)?" %
                         ("|".join(sorted(MODIFIERS)), IDENT), h) if term in ("{", ";", "=") or True else None
            if m and not (ctx == "global" and first not in TYPE_KW and not any(w in TYPE_KW for w in words[:4])):
                kw = m.group(1).split()[0]
                if kw == "delegate":
                    i = self.skip_after(he, term, end)
                    continue
                tname = m.group(2)
                arity = len(split_top(m.group(3)[1:-1])) if m.group(3) else 0
                typ = Type_(tname, kw, ns, outer, f.rel, f.line(hs + h.find(tname)), self.project, arity)
                typ.usings, typ.aliases = usings, aliases
                rest = h[m.end():]
                prim = ""
                if rest.lstrip().startswith("("):
                    ps = rest.index("(")
                    pe = match(rest, ps, "(", ")")
                    prim = rest[ps + 1:pe]
                    rest = rest[pe + 1:]
                for pt, pn, _d, _t in parse_params(prim):
                    typ.fields.setdefault(pn, pt)
                ci = rest.find(":")
                if ci >= 0:
                    bl = re.split(r"\bwhere\b", rest[ci + 1:], maxsplit=1)[0]
                    for b in split_top(bl):
                        b = re.sub(r"\(.*", "", b.strip())
                        if dotted_type(b):
                            typ.bases_raw.append(dotted_type(b))
                f.types.append(typ)
                if outer is not None:
                    outer.nested[tname] = typ
                if term == "{" and kw != "enum":
                    close = match(t, he, "{", "}")
                    self.block(he + 1, close, ns, typ, usings, aliases, "type")
                    i = close + 1
                elif term == "{":
                    i = match(t, he, "{", "}") + 1
                else:
                    i = he + 1
                continue
            if ctx == "type" and outer is not None:
                i = self.member(h, hs, he, term, end, outer)
                continue
            # a statement (top-level statements, or stray text in a namespace)
            if ctx == "global":
                if not f.top:
                    f.top_line = f.line(hs)
                i = self.skip_after(he, term, end)
                f.top.append((hs, i))
            else:
                i = self.skip_after(he, term, end)

    def skip_after(self, he, term, end):
        t = self.t
        if term == "{":
            close = match(t, he, "{", "}")
            return close + 1
        if term in ("=", "=>"):
            return self.skip_stmt(he + 1, end)
        return he + 1

    def member(self, h, hs, he, term, end, typ):
        t, f = self.t, self.f
        words = h.split()
        mods = [w for w in words if w in MODIFIERS]
        pi = self.top_paren(h)
        if pi >= 0 and "operator" not in h and not h.lstrip().startswith("~"):
            before = h[:pi].rstrip()
            before = re.sub(r"<[^<>]*(?:<[^<>]*>[^<>]*)*>\s*$", "", before).rstrip()
            m = re.search(r"(%s)$" % IDENT, before)
            name = m.group(1) if m else ""
            pe = match(h, pi, "(", ")")
            params = parse_params(h[pi + 1:pe])
            ret = re.sub(r"\b(?:%s)\b" % "|".join(sorted(MODIFIERS)), " ", before[:m.start()] if m else "").strip()
            is_ctor = name == typ.name and not ret
            ext = params[0][0] if params and params[0][3] else None
            body = None
            if term == "{":
                close = match(t, he, "{", "}")
                body = (he + 1, close)
                nxt = close + 1
            elif term == "=>":
                e = self.skip_stmt(he + 1, end)
                body = (he + 2, e)
                nxt = e
            else:
                nxt = he + 1
            if name and name not in KEYWORDS:
                mt = Method_(name, typ, f.line(hs + (m.start() if m else 0)), [(a, b, c) for a, b, c, _ in params], body,
                             ret, mods, is_ctor, ext)
                mt.sig = re.sub(r"\s+", " ", (h.strip() + "")).strip()[:120]
                typ.methods.append(mt)
                for pt, pn, _d, _t in params:
                    pass
            return nxt
        # field, property, event, indexer
        names = h.split()
        nxt = self.skip_after(he, term, end)
        if term in ("{", "=>") and "[" not in h:
            if term == "{":
                nx = self.ws(nxt, end)
                if nx < end and t[nx] == "=" and t[nx + 1:nx + 2] != "=":
                    nxt = self.skip_stmt(nx + 1, end)
        core = [w for w in names if w not in MODIFIERS and w != "event"]
        if len(core) >= 2:
            typ.fields.setdefault(core[-1], " ".join(core[:-1]))
        return nxt

    @staticmethod
    def top_paren(h):
        depth = 0
        for i, c in enumerate(h):
            if c == "<":
                depth += 1
            elif c == ">" and i and h[i - 1] != "=":
                depth = max(0, depth - 1)
            elif c == "(" and depth == 0:
                return i
        return -1


def parse_file(root: str, rel: str, project: str) -> File_:
    text = vr_lang.read_text(root, rel)
    f = File_(rel, project)
    f.clean = clean(text)
    f.starts = [0]
    for m in re.finditer("\n", f.clean):
        f.starts.append(m.end())
    sc = Scanner(f, f.clean, project)
    sc.block(0, len(f.clean), "", None, f.usings, f.aliases, "global")
    return f


# ---------------------------------------------------------------- projects

def find_projects(root: str):
    out = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in vr_lang.SKIP_DIRS and not d.startswith("."))
        for fn in sorted(filenames):
            if fn.endswith(".csproj"):
                rel = os.path.relpath(os.path.join(dirpath, fn), root).replace(os.sep, "/")
                out[os.path.dirname(rel)] = rel
    return out


def project_for(rel: str, projects: dict):
    d = os.path.dirname(rel)
    while True:
        if d in projects:
            return d
        if not d:
            return None
        d = d.rsplit("/", 1)[0] if "/" in d else ""


# ---------------------------------------------------------------- extract

class Index:
    def __init__(self, types: list):
        self.types = types
        self.by_simple: dict = {}
        self.by_full: dict = {}
        for t in types:
            self.by_simple.setdefault(t.name, []).append(t)
            self.by_full.setdefault(t.full, []).append(t)

    def resolve(self, name: str, ctx: Type_ | None, ns: str, usings: list, aliases: dict, project: str):
        """The repository type a (possibly dotted) name denotes from inside `ctx`, or None. Ambiguity gives None."""
        if not name:
            return None
        first, _, rest = name.partition(".")
        if first in aliases:
            name = aliases[first] + ("." + rest if rest else "")
        last = name.rsplit(".", 1)[-1]
        cands = self.by_simple.get(last, [])
        if not cands:
            return None
        levels = []
        if "." not in name:
            c = ctx
            while c is not None:
                if name in c.nested:
                    return c.nested[name]
                for b in c.bases:
                    if name in b.nested:
                        return b.nested[name]
                c = c.outer
        scopes = []
        cur = ns
        while cur:
            scopes.append(cur)
            cur = cur.rpartition(".")[0]
        scopes.append("")
        for sc in scopes:
            q = (sc + "." + name) if sc else name
            if q in self.by_full:
                levels.append(self.by_full[q])
                break
        if not levels and "." not in name:
            for u in usings:
                q = u + "." + name
                if q in self.by_full:
                    levels.append(self.by_full[q])
                    break
        if not levels:
            return None
        hit = levels[0]
        if len(hit) > 1:
            same = [h for h in hit if h.project == project]
            hit = same if same else hit
        return hit[0] if len(hit) == 1 else None


def _arity_of(text: str, open_idx: int) -> int:
    close = match(text, open_idx, "(", ")")
    inner = text[open_idx + 1:close]
    return len(split_top(inner)) if inner.strip() else 0


GENERIC = r"(?:\s*<[^<>;(){}]*(?:<[^<>;(){}]*>[^<>;(){}]*)*>)?"
LOCAL_DECL = re.compile(r"(?<![\w.])(%s(?:\.%s)*%s\??(?:\[\])?)\s+(%s)\s*(?==|;|,|\)|\bin\b)" % (IDENT, IDENT, GENERIC, IDENT))
VAR_NEW = re.compile(r"\bvar\s+(%s)\s*=\s*(?:await\s+)?new\s+(%s(?:\.%s)*)" % (IDENT, IDENT, IDENT))
CALL_RE = re.compile(r"(%s)%s\s*\(" % (IDENT, GENERIC))
NEW_RE = re.compile(r"(?<![\w.])new\s+(%s(?:\.%s)*)%s\s*[({]" % (IDENT, IDENT, GENERIC))


def extract_csharp(root: str, layers=(1, 2), calls=(), cfg_test: bool = False, timings=None, **_ignored) -> dict:
    root = os.path.abspath(root)
    if not os.path.isdir(root):
        raise LangError("%s is not a directory" % root)
    b = Builder()
    layers_meta: dict = {}
    projects = find_projects(root)
    pinfo: dict = {}
    for d, rel in sorted(projects.items()):
        text = vr_lang.read_text(root, rel)
        am = re.search(r"<AssemblyName>\s*([^<\s]+)\s*</AssemblyName>", text)
        name = am.group(1) if am and "$(" not in am.group(1) else os.path.splitext(os.path.basename(rel))[0]
        is_test = bool(TEST_NAME.search(name) or TEST_REFS.search(text))
        pinfo[d] = {"id": "project:" + sid(name), "name": name, "rel": rel, "text": text, "lines": text.split("\n"),
                    "test": is_test}
    seen_ids: set = set()
    for d in sorted(pinfo):
        p = pinfo[d]
        n = 2
        base = p["id"]
        while p["id"] in seen_ids:
            p["id"] = "%s_%d" % (base, n)
            n += 1
        seen_ids.add(p["id"])
    allf = vr_lang.walk_files(root, (".cs",))
    files, skipped_tests = [], 0
    for rel in allf:
        d = project_for(rel, projects)
        if d is not None and pinfo[d]["test"] and not cfg_test:
            skipped_tests += 1
            continue
        if rel.endswith((".g.cs", ".Designer.cs", ".AssemblyInfo.cs")):
            continue
        files.append(rel)
    if not files and not pinfo:
        raise LangError("no C# source files under %s" % root)
    keep = {d for d, p in pinfo.items() if cfg_test or not p["test"]}
    root_pid = None
    if any(project_for(f, projects) is None for f in files):
        name = os.path.basename(root)
        root_pid = "project:" + sid(name)
        while root_pid in seen_ids:
            root_pid += "_root"
        b.node(root_pid, name, "project",
               inferred("no .csproj above these .cs files; the repository root is taken as the project"), resolution="inferred")
    for d in sorted(keep):
        p = pinfo[d]
        b.node(p["id"], p["name"], "project", src(p["rel"], line_of(p["lines"], p["name"]) if p["name"] in p["text"] else 1, "project file"))
    by_rel = {p["rel"]: d for d, p in pinfo.items()}
    for d in sorted(keep):
        p = pinfo[d]
        for i, raw in enumerate(p["lines"], 1):
            m = re.search(r"<ProjectReference\s+Include\s*=\s*\"([^\"]+)\"", raw)
            if not m:
                continue
            tgt = os.path.normpath(os.path.join(d, m.group(1).replace("\\", "/"))).replace(os.sep, "/")
            td = by_rel.get(tgt)
            if td is not None and td in keep and td != d:
                b.edge("depends", p["id"], pinfo[td]["id"], src(p["rel"], i, "project reference"))
    layers_meta["projects"] = {"status": "ran"}
    if skipped_tests:
        layers_meta["projects"]["reason"] = "%d test file(s) left out (--cfg-test includes them)" % skipped_tests

    parsed: list = []
    for rel in files:
        d = project_for(rel, projects)
        pid = pinfo[d]["id"] if d is not None else root_pid
        try:
            parsed.append(parse_file(root, rel, pid))
        except (RecursionError, IndexError, ValueError) as exc:
            layers_meta.setdefault("modules", {"status": "ran"})
            layers_meta["modules"]["reason"] = (layers_meta["modules"].get("reason", "") + " not parsed: %s (%s);" % (rel, type(exc).__name__)).strip()
    # global usings are visible in every file of the project
    gl: dict = {}
    for f in parsed:
        gl.setdefault(f.project, []).extend(f.global_usings)
    types = [t for f in parsed for t in f.types]
    # ids
    for t in types:
        suffix = "_g%d" % t.arity if t.arity else ""
        t.id = "type:%s::%s%s" % (sid(t.project.split(":", 1)[1]), sid(t.full), suffix)
    by_id: dict = {}
    for t in types:
        by_id.setdefault(t.id, t)
    idx = Index(types)
    for t in types:
        t.usings = list(t.usings) + gl.get(t.project, [])
        ns_ = t.ns
        for br in t.bases_raw:
            r = idx.resolve(br, t.outer, ns_, t.usings, t.aliases, t.project)
            if r is not None and r is not t:
                t.bases.append(r)
    pfx = lambda t: t.project.split(":", 1)[1]
    mid = lambda proj, ns_: "module:%s::%s" % (sid(proj.split(":", 1)[1]), sid(ns_))

    ns_by_proj: dict = {}
    for f in parsed:
        for nm, ln in f.namespaces:
            ns_by_proj.setdefault(f.project, {}).setdefault(nm, []).append((f.rel, ln))
    if 2 in layers:
        for proj in sorted(ns_by_proj):
            nss = ns_by_proj[proj]
            for nm in sorted(nss):
                nid = mid(proj, nm)
                for k, (rel, ln) in enumerate(nss[nm][:4]):
                    b.node(nid, nm.rsplit(".", 1)[-1], "module", src(rel, ln, "namespace %s" % nm))
                par = nm.rpartition(".")[0]
                while par and par not in nss:
                    par = par.rpartition(".")[0]
                parent = mid(proj, par) if par else proj
                b.edge("contains", parent, nid, src(nss[nm][0][0], nss[nm][0][1], "namespace %s" % nm))
        seen_t: set = set()
        for t in types:
            label = t.name + ("<%s>" % ",".join("T" for _ in range(t.arity)) if t.arity else "")
            ev = src(t.file, t.line, "%s %s" % (t.kind, t.full))
            if t.id in seen_t and t.id in b.nodes:
                b.node(t.id, label, "type", ev)
                continue
            seen_t.add(t.id)
            b.node(t.id, label, "type", ev)
            if t.outer is not None:
                owner = t.outer.id
            elif t.ns and mid(t.project, t.ns) in b.nodes:
                owner = mid(t.project, t.ns)
            else:
                owner = t.project
            b.edge("contains", owner, t.id, ev)
            for bt in t.bases:
                lab = "implements" if bt.kind == "interface" and t.kind != "interface" else "extends"
                b.edge("uses", t.id, bt.id, src(t.file, t.line, "base type"), label=lab)
        layers_meta["modules"] = dict(layers_meta.get("modules", {}), status="ran")
    else:
        layers_meta["modules"] = {"status": "skipped", "reason": "not requested"}

    # methods
    methods = [m for t in types for m in t.methods]
    cnt: dict = {}
    for m in methods:
        qual = sid(m.typ.full) + ("_g%d" % m.typ.arity if m.typ.arity else "")
        key = (sid(pfx(m.typ)), qual, sid(m.name))
        cnt[key] = cnt.get(key, 0) + 1
        # overloads: the counter goes into the qualifier so the last `::` segment stays the member name
        q2 = qual if cnt[key] == 1 else "%s_o%d" % (qual, cnt[key])
        m.id = "fn:%s::%s::%s" % (key[0], q2, key[2])
    tops = []
    for f in parsed:
        if f.top:
            nm = "top_level_statements"
            tid = "fn:%s::%s::%s" % (sid(f.project.split(":", 1)[1]), sid(os.path.splitext(os.path.basename(f.rel))[0]), nm)
            tops.append((tid, f))

    def fn_node(m: Method_):
        ev = src(m.typ.file, m.line, "definition: " + m.sig)
        b.node(m.id, m.name, "fn", ev)
        if b.has(m.typ.id):
            b.edge("contains", m.typ.id, m.id, ev)

    def top_node(tid, f):
        ev = src(f.rel, f.top_line, "top-level statements")
        b.node(tid, "top-level statements (%s)" % os.path.basename(f.rel), "fn", ev)
        if f.project and b.has(f.project):
            b.edge("contains", f.project, tid, ev)

    if 3 in layers:
        for m in methods:
            fn_node(m)
        for tid, f in tops:
            top_node(tid, f)
        layers_meta["functions"] = {"status": "ran"}
    else:
        layers_meta["functions"] = {"status": "skipped", "reason": "not requested"}

    tools = [{"name": "vr_extract_cs", "version": vr_lang.VERSION}]
    if calls:
        focus_m, missing = set(), []
        for spec in calls:
            hit = [m for m in methods if vr_lang.focus_matches(spec, m.name, m.typ.file)]
            hit += [None for tid, f in tops if vr_lang.focus_matches(spec, "top_level_statements", f.rel)]
            if not hit:
                missing.append(spec)
            focus_m.update(m.id for m in hit if m is not None)
            for tid, f in tops:
                if vr_lang.focus_matches(spec, "top_level_statements", f.rel):
                    focus_m.add(tid)
        pending = _all_calls(parsed, types, methods, tops, idx, gl)
        byid = {m.id: m for m in methods}
        topid = {tid: f for tid, f in tops}
        if not focus_m:
            layers_meta["calls"] = {"status": "skipped", "reason": "function(s) not found: " + ", ".join(missing)}
        else:
            kept = [p for p in pending if p[0] in focus_m or p[1] in focus_m]
            used = set(focus_m)
            for p in kept:
                used.add(p[0])
                used.add(p[1])
            for u in sorted(used):
                if u in byid:
                    fn_node(byid[u])
                elif u in topid:
                    top_node(u, topid[u])
            for s_, t_, resn, rel, ln, label in kept:
                if b.has(t_) or t_ in by_id:
                    if t_ in by_id and not b.has(t_):
                        tt = by_id[t_]
                        b.node(t_, tt.name, "type", src(tt.file, tt.line, "%s %s" % (tt.kind, tt.full)))
                    b.edge("calls", s_, t_, src(rel, ln, "call site"), resolution=resn, label=label)
            layers_meta["calls"] = {"status": "ran"}
            if missing:
                layers_meta["calls"]["reason"] = "function(s) not found: " + ", ".join(missing)
    else:
        layers_meta["calls"] = {"status": "skipped", "reason": "not requested"}
    return vr_lang.assemble(root, b, layers_meta, tools)


def _all_calls(parsed, types, methods, tops, idx: Index, gl: dict) -> list:
    """[(source fn id, target id, resolution, rel path, line, label)] for every resolvable call site."""
    ext: dict = {}
    for m in methods:
        if m.ext:
            ext.setdefault(m.name, []).append(m)
    out: list = []
    fileof = {f.rel: f for f in parsed}
    for t in types:
        t_methods: dict = {}
        for k in [t] + _ancestors(t):
            for mm in k.methods:
                t_methods.setdefault(mm.name, []).append(mm)
        t._all = t_methods  # type: ignore[attr-defined]
    for m in methods:
        if m.body is None:
            continue
        f = fileof[m.typ.file]
        _scan_body(f, f.clean, m.body, m.typ, m, m.id, idx, ext, gl, out)
    for tid, f in tops:
        _scan_body(f, f.clean, (f.top[0][0], f.top[-1][1]), None, None, tid, idx, ext, gl, out)
    return out


def _ancestors(t: Type_, seen=None) -> list:
    seen = seen if seen is not None else {id(t)}
    out = []
    for b in t.bases:
        if id(b) not in seen:
            seen.add(id(b))
            out.append(b)
            out += _ancestors(b, seen)
    return out


def _scan_body(f: File_, text: str, span, typ, meth, src_id, idx: Index, ext: dict, gl: dict, out: list):
    a, e = span
    body = text[a:e]
    usings = (typ.usings if typ is not None else f.usings) + gl.get(f.project, [])
    aliases = typ.aliases if typ is not None else f.aliases
    ns = typ.ns if typ is not None else ""
    vars_: dict = {}
    conflict: set = set()

    def note(name, tname):
        if name in vars_ and vars_[name] != tname:
            conflict.add(name)
        vars_[name] = tname

    if typ is not None:
        for fname, ftype in typ.fields.items():
            vars_[fname] = ftype
        for k in _ancestors(typ):
            for fname, ftype in k.fields.items():
                vars_.setdefault(fname, ftype)
    if meth is not None:
        for pt, pn, _d in meth.params:
            note(pn, pt)
    for m in LOCAL_DECL.finditer(body):
        tn = m.group(1)
        if tn.split(".")[0].split("<")[0] in KEYWORDS or tn in ("var", "return"):
            if tn != "var":
                continue
        if tn == "var":
            continue
        note(m.group(2), tn)
    for m in VAR_NEW.finditer(body):
        note(m.group(1), m.group(2))

    def vtype(name):
        if name in conflict or name not in vars_:
            return None
        return idx.resolve(dotted_type(vars_[name]), typ, ns, usings, aliases, f.project)

    def emit(cands, argc, ln, recv_virtual_hint=False, label=None):
        if not cands:
            return
        fit = [c for c in cands if _fits(c, argc)]
        use, res = (fit, "static") if len(fit) == 1 else (fit or cands, "inferred")
        for c in use:
            r = res
            if r == "static" and c.virtual:
                r = "dispatch"
            out.append((src_id, c.id, r, f.rel, ln, label))

    for m in NEW_RE.finditer(body):
        t2 = idx.resolve(dotted_type(m.group(1)), typ, ns, usings, aliases, f.project)
        if t2 is None:
            continue
        ln = f.line(a + m.start())
        op = body.find("(", m.end() - 1) if body[m.end() - 1] == "(" else -1
        argc = _arity_of(body, op) if op >= 0 else None
        ctors = [x for x in t2.methods if x.is_ctor]
        if ctors:
            emit(ctors, argc if argc is not None else -1, ln)
        else:
            out.append((src_id, t2.id, "static", f.rel, ln, "constructs"))
    for m in CALL_RE.finditer(body):
        name = m.group(1)
        if name in KEYWORDS:
            continue
        s = m.start()
        before = body[:s].rstrip()
        # a declaration (`void Foo(` / `int Foo(`) is not a call
        pm = re.search(r"([\w>\]?]+)$", before)
        if pm and not before.endswith((".", "?.")) and pm.group(1) not in KEYWORDS and not before.endswith(("(", ",", "=", "{", ";", "}", ":", "!", "&", "|", "+", "-", "*", "/", "<", ">")):
            prev = pm.group(1)
            if prev not in ("await", "return", "else", "throw", "yield", "do", "in", "case"):
                continue
        ln = f.line(a + s)
        op = body.index("(", m.end() - 1)
        argc = _arity_of(body, op)
        recv = None
        if before.endswith("?."):
            before = before[:-2]
            recv_kind = "dot"
        elif before.endswith("."):
            before = before[:-1]
            recv_kind = "dot"
        else:
            recv_kind = "none"
        if recv_kind == "none":
            if typ is not None:
                cands = getattr(typ, "_all", {}).get(name)
                c2 = typ.outer
                while not cands and c2 is not None:
                    cands = getattr(c2, "_all", {}).get(name)
                    c2 = c2.outer
                if cands:
                    emit([x for x in cands if not x.is_ctor], argc, ln)
            continue
        rm = re.search(r"((?:%s\s*(?:\?\s*)?\.\s*)*%s)\s*$" % (IDENT, IDENT), before)
        if not rm or before[:rm.start()].rstrip().endswith((".", ")", "]")):
            continue
        chain = [x.strip() for x in re.split(r"\s*\??\s*\.\s*", rm.group(1))]
        if chain[0] == "this":
            chain = chain[1:]
            if not chain:
                if typ is not None:
                    emit([x for x in getattr(typ, "_all", {}).get(name, []) if not x.is_ctor], argc, ln)
                continue
        if chain == ["base"]:
            if typ is not None:
                cands = [x for k in _ancestors(typ) for x in k.methods if x.name == name and not x.is_ctor]
                emit(cands, argc, ln)
            continue
        rt = None
        if len(chain) == 1:
            rt = vtype(chain[0])
            if rt is None and chain[0] not in vars_:
                rt = idx.resolve(chain[0], typ, ns, usings, aliases, f.project)
        else:
            rt = idx.resolve(".".join(chain), typ, ns, usings, aliases, f.project)
            if rt is None and chain[0] in vars_:
                continue
        if rt is None:
            continue
        cands = [x for x in getattr(rt, "_all", {}).get(name, []) if not x.is_ctor]
        if not cands:
            cands = [x for x in ext.get(name, []) if simple_type(x.ext or "") in {rt.name} | {k.name for k in _ancestors(rt)}]
        emit(cands, argc, ln)


def _fits(m: Method_, argc) -> bool:
    if argc is None or argc < 0:
        return True
    total = len(m.params) - (1 if m.ext else 0)
    req = sum(1 for p in m.params[(1 if m.ext else 0):] if not p[2])
    return req <= argc <= total or any("params" in p[0] for p in m.params)


# ---------------------------------------------------------------- selftest

_CS = {
    "Shop.sln": "",
    "src/Shop.Core/Shop.Core.csproj": '<Project Sdk="Microsoft.NET.Sdk">\n  <PropertyGroup><TargetFramework>net9.0</TargetFramework></PropertyGroup>\n</Project>\n',
    "src/Shop.Core/Model.cs": (
        "using System;\nnamespace Shop.Core;\n\npublic interface IRepo\n{\n    Order Load(int id);\n    void Save(Order o);\n}\n\n"
        "public record Order(int Id, string Name);\n\npublic abstract class BaseService : IDisposable\n{\n"
        "    public virtual void Run() { }\n    public void Dispose() { }\n}\n\npublic static class Ext\n{\n"
        "    public static string Show(this Order o) => $\"{o.Id}\" + \"}{\";\n}\n"),
    "src/Shop.Api/Shop.Api.csproj": '<Project Sdk="Microsoft.NET.Sdk.Web">\n  <ItemGroup>\n    <ProjectReference Include="..\\Shop.Core\\Shop.Core.csproj" />\n  </ItemGroup>\n</Project>\n',
    "src/Shop.Api/Service.cs": (
        "using System.Collections.Generic;\nusing Shop.Core;\n\nnamespace Shop.Api\n{\n    // class Fake { void No() {} }\n"
        "    public class OrderService : BaseService\n    {\n        private readonly IRepo _repo;\n        private int _n = Compute(3);\n"
        "        public OrderService(IRepo repo) { _repo = repo; }\n\n        public override void Run()\n        {\n"
        "            _repo.Load(1);\n            var o = new Order(1, \"x\");\n            Handle(o);\n            Helper(1, 2);\n            Helper(1);\n"
        "            var s = new Inner();\n            base.Run();\n            Console.WriteLine(\"Handle(x) {\");\n"
        "            var shown = o.Show();\n        }\n\n        private void Handle(Order o) { _repo.Save(o); }\n"
        "        private int Helper(int a, int b = 0) => a + b;\n        private static int Compute(int x) => x;\n"
        "        public int Total { get; set; } = 4;\n        class Inner { public void Go() { } }\n    }\n}\n"),
    "src/Shop.Api/Program.cs": (
        "using Shop.Api;\nusing Shop.Core;\n\nvar svc = new OrderService(null);\nsvc.Run();\n"
        "if (args.Length > 0) { svc.Run(); }\n"),
    "tests/Shop.Tests/Shop.Tests.csproj": '<Project><ItemGroup><PackageReference Include="xunit" Version="2" /></ItemGroup></Project>\n',
    "tests/Shop.Tests/T.cs": "namespace Shop.Tests { public class T { public void A() {} } }\n",
}


def _selftest() -> int:
    fails: list = []

    def check(name, cond, detail=""):
        if not cond:
            fails.append("%s %s" % (name, detail))

    c = clean('a // x\n"s { }" /* y\n z */ \'}\' $"{a}{{" @"q""}" """raw }"""\n#if X\nb')
    check("clean:newlines", c.count("\n") == 4 and "}" not in c and "x" not in c.replace("a", "").replace("b", "").replace("X", ""), repr(c))
    with tempfile.TemporaryDirectory() as d:
        d = os.path.realpath(d)
        for rel, text in _CS.items():
            p = os.path.join(d, rel)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w", encoding="utf-8") as fh:
                fh.write(text)
        g = extract_csharp(d, layers=(1, 2, 3), calls=["Run", "Handle", "top_level_statements"])
        issues = vr_lang.vr_graph.validate(g, root=d, check_budget=False)
        check("valid", issues == [], json.dumps(issues)[:500])
        ids = {n["id"] for n in g["nodes"]}
        for want in ("project:Shop.Core", "project:Shop.Api", "module:Shop.Core::Shop.Core", "module:Shop.Api::Shop.Api",
                     "type:Shop.Core::Shop.Core.IRepo", "type:Shop.Core::Shop.Core.Order", "type:Shop.Api::Shop.Api.OrderService",
                     "type:Shop.Api::Shop.Api.OrderService.Inner", "fn:Shop.Api::Shop.Api.OrderService::Run",
                     "fn:Shop.Api::Shop.Api.OrderService::Handle", "fn:Shop.Core::Shop.Core.Ext::Show",
                     "fn:Shop.Api::Shop.Api.OrderService::OrderService"):
            check("node:" + want, want in ids, "")
        check("tests-excluded", not any("Shop.Tests" in i for i in ids))
        check("fake-class-in-comment", not any("Fake" in i for i in ids))
        check("property-not-method", not any(i.endswith("::Total") for i in ids))
        edges = {e["id"]: e for e in g["edges"]}
        check("depends", "edge:depends:project:Shop.Api:project:Shop.Core" in edges)
        check("contains", "edge:contains:module:Shop.Api::Shop.Api:type:Shop.Api::Shop.Api.OrderService" in edges)
        check("extends", "edge:uses:type:Shop.Api::Shop.Api.OrderService:type:Shop.Core::Shop.Core.BaseService" in edges)
        check("implements-external-dropped", not any("IDisposable" in e for e in edges))

        def call(a, t):
            return edges.get("edge:calls:%s:%s" % (a, t))
        run = "fn:Shop.Api::Shop.Api.OrderService::Run"
        c1 = call(run, "fn:Shop.Api::Shop.Api.OrderService::Handle")
        check("call:own-method", c1 is not None and c1["resolution"] == "static", str(c1))
        c2 = call(run, "fn:Shop.Core::Shop.Core.IRepo::Load")
        check("call:interface-dispatch", c2 is not None and c2["resolution"] == "dispatch", str(sorted(edges)[:40]))
        check("call:extension", call(run, "fn:Shop.Core::Shop.Core.Ext::Show") is not None)
        check("call:base", call(run, "fn:Shop.Core::Shop.Core.BaseService::Run") is not None)
        check("call:ctor-fallback-type", call(run, "type:Shop.Api::Shop.Api.OrderService.Inner") is not None)
        h = call(run, "fn:Shop.Api::Shop.Api.OrderService::Helper")
        check("call:overload-by-arity", h is not None and h["resolution"] == "static" and h.get("count") == 2, str(h))
        hd = call("fn:Shop.Api::Shop.Api.OrderService::Handle", "fn:Shop.Core::Shop.Core.IRepo::Save")
        check("call:field-typed", hd is not None)
        check("string-not-a-call", not any(e["target"].endswith("::WriteLine") for e in g["edges"]))
        top = "fn:Shop.Api::Program::top_level_statements"
        check("call:top-level-ctor", call(top, "fn:Shop.Api::Shop.Api.OrderService::OrderService") is not None, str(sorted(e for e in edges if "calls" in e)))
        check("call:top-level-var", call(top, run) is not None)
        check("determinism", json.dumps(g, sort_keys=True) == json.dumps(
            extract_csharp(d, layers=(1, 2, 3), calls=["Run", "Handle", "top_level_statements"]), sort_keys=True))
        g2 = extract_csharp(d, layers=(1, 2))
        check("no-fn-without-layer3", not any(n["kind"] == "fn" for n in g2["nodes"]))
        check("calls-skipped", g2["meta"]["layers"]["calls"]["status"] == "skipped")
        g3 = extract_csharp(d, layers=(1, 2), cfg_test=True)
        check("cfg-test", "project:Shop.Tests" in {n["id"] for n in g3["nodes"]})
        g4 = extract_csharp(d, layers=(1,), calls=["Nonesuch"])
        check("calls-missing", g4["meta"]["layers"]["calls"]["status"] == "skipped")
    with tempfile.TemporaryDirectory() as d:
        d = os.path.realpath(d)
        with open(os.path.join(d, "A.cs"), "w") as fh:
            fh.write("namespace X { class A { void F() {} } }\n")
        g = extract_csharp(d, layers=(1, 2, 3))
        p = [n for n in g["nodes"] if n["kind"] == "project"]
        check("inferred-root-project", len(p) == 1 and p[0]["evidence"][0]["tier"] == "inferred", str(p))
        check("valid-bare", vr_lang.vr_graph.validate(g, root=d, check_budget=False) == [])
    if fails:
        print("vr_extract_cs selftest FAIL:\n  " + "\n  ".join(fails))
        return 1
    print("vr_extract_cs selftest ok")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("root", nargs="?")
    ap.add_argument("--layers", default="1,2")
    ap.add_argument("--calls", default="")
    ap.add_argument("--cfg-test", action="store_true")
    ap.add_argument("-o", "--output")
    a = ap.parse_args(argv)
    if a.selftest:
        return _selftest()
    if not a.root:
        ap.print_usage(sys.stderr)
        return 2
    try:
        g = extract_csharp(a.root, layers=tuple(sorted({int(x) for x in a.layers.split(",") if x.strip()})),
                           calls=[c for c in a.calls.split(",") if c.strip()], cfg_test=a.cfg_test)
    except (LangError, ValueError) as exc:
        print("vr_extract_cs: %s" % exc, file=sys.stderr)
        return 1
    text = json.dumps(g, indent=1, ensure_ascii=False) + "\n"
    if a.output:
        with open(a.output, "w", encoding="utf-8") as fh:
            fh.write(text)
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
