#!/usr/bin/env python3
"""Visual review: the Python extractor (stdlib `ast`, no third-party import).

    vr_extract_py.py REPO_ROOT [--layers 1,2,3] [--calls f1,f2] [--cfg-test] [-o out.json]
    vr_extract_py.py --selftest

Normally called through `vr_extract.py python ROOT` (same flags). Layers:
  1  projects: one per pyproject.toml / setup.py / setup.cfg (a repository with none gets one
     `inferred` root project) and the `depends` edges between projects of the repository.
  2  modules and packages (`module:PKG.MOD`), classes (`type:PKG.MOD::Class`), the `contains`
     edges, `uses` edges for imports between repository modules (count = import statements) and for
     base classes (label extends).
  3  every function and method (`fn:PKG.MOD.Class::name`).
  4  (only with --calls) the functions named, their callers and callees, as `calls` edges.

Module names come from the `__init__.py` chain of the file; a file outside any package is named by its
relative path (`scripts.tool`). Test code (tests/ dirs, test_*.py, *_test.py, conftest.py) is left out
unless --cfg-test. A file that does not parse (`SyntaxError`) keeps its module node and is listed in
`meta.layers.modules.reason`.

Call resolution is name resolution through imports and class bases, never a guess:
  `f()` / `mod.f()` / `pkg.mod.f()` / `Class.method()` / `self.m()` / `cls.m()` / `super().m()` /
  `self.attr.m()` (attr typed by `self.attr = Class(...)` or an annotation) / `var.m()` (var typed by a
  parameter annotation or `var = Class(...)`). A call on a class is an edge to its `__init__` (or to the
  class when it has none). A method that a repository subclass overrides is `dispatch`. A receiver
  whose type is not known, a call through a callable argument, `getattr`, decorators and calls on
  objects from outside the repository produce no edge.
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import re
import sys
import tempfile
import warnings

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import vr_lang  # noqa: E402
from vr_lang import Builder, LangError, inferred, line_of, sid, src  # noqa: E402

try:
    import tomllib
except ImportError:  # Python < 3.11
    tomllib = None

TEST_DIRS = {"tests", "test", "testing"}
MANIFESTS = ("pyproject.toml", "setup.py", "setup.cfg")


def _is_test(rel: str) -> bool:
    parts = rel.split("/")
    base = parts[-1]
    return (any(p in TEST_DIRS for p in parts[:-1]) or base.startswith("test_") or base.endswith("_test.py")
            or base == "conftest.py")


def _norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


# ---------------------------------------------------------------- layer 1

def _project_info(root: str, rel: str) -> dict:
    """{name, deps(list of normalised names)} of one manifest; the name falls back to the directory."""
    text = vr_lang.read_text(root, rel)
    base = os.path.basename(rel)
    name, deps = None, []
    if base == "pyproject.toml":
        data = {}
        if tomllib:
            try:
                data = tomllib.loads(text)
            except Exception:
                data = {}
        proj = data.get("project") if isinstance(data.get("project"), dict) else {}
        poetry = (data.get("tool") or {}).get("poetry") if isinstance(data.get("tool"), dict) else None
        poetry = poetry if isinstance(poetry, dict) else {}
        name = proj.get("name") or poetry.get("name")
        reqs = list(proj.get("dependencies") or [])
        for group in (proj.get("optional-dependencies") or {}).values():
            reqs += list(group)
        for r in reqs:
            m = re.match(r"\s*([A-Za-z0-9][A-Za-z0-9._-]*)", str(r))
            if m:
                deps.append(_norm(m.group(1)))
        deps += [_norm(k) for k in (poetry.get("dependencies") or {})]
        if not name:
            m = re.search(r'^\[project\]\s*$(.*?)(?=^\[|\Z)', text, re.S | re.M)
            n = re.search(r'^name\s*=\s*"([^"]+)"', m.group(1), re.M) if m else None
            name = n.group(1) if n else None
    elif base == "setup.cfg":
        m = re.search(r"^\[metadata\]\s*$(.*?)(?=^\[|\Z)", text, re.S | re.M)
        n = re.search(r"^name\s*=\s*(\S+)", m.group(1), re.M) if m else None
        name = n.group(1) if n else None
    else:
        n = re.search(r"\bname\s*=\s*[\"']([^\"']+)[\"']", text)
        name = n.group(1) if n else None
    d = os.path.dirname(rel)
    return {"name": name or os.path.basename(d) or os.path.basename(os.path.abspath(root)), "deps": deps,
            "named": bool(name)}


def layer_projects(root: str, files: list, b: Builder):
    """Returns (projects by dir, root_project_id_or_None). `projects` maps a directory to {id, name, rel}."""
    found = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in vr_lang.SKIP_DIRS and not d.startswith(".")
                             and not d.endswith(".egg-info"))
        for m in MANIFESTS:
            if m in filenames:
                d = os.path.relpath(dirpath, root).replace(os.sep, "/")
                d = "" if d == "." else d
                found.setdefault(d, os.path.join(d, m).replace(os.sep, "/"))
                break  # pyproject.toml first, then setup.py, then setup.cfg
    projects: dict = {}
    for d in sorted(found):
        rel = found[d]
        info = _project_info(root, rel)
        pid = "project:" + sid(info["name"])
        n = 2
        while pid in {p["id"] for p in projects.values()}:
            pid = "project:%s_%d" % (sid(info["name"]), n)
            n += 1
        lines = vr_lang.read_text(root, rel).split("\n")
        b.node(pid, info["name"], "project", src(rel, line_of(lines, info["name"]) if info["named"] else 1, "project manifest"))
        projects[d] = {"id": pid, "name": info["name"], "rel": rel, "deps": info["deps"], "lines": lines}
    by_norm = {_norm(p["name"]): p for p in projects.values()}
    for p in projects.values():
        for dep in sorted(set(p["deps"])):
            t = by_norm.get(dep)
            if t is not None and t["id"] != p["id"]:
                b.edge("depends", p["id"], t["id"], src(p["rel"], line_of(p["lines"], dep), "dependency"))
    root_id = None
    if "" not in projects and any(_project_for(f, projects) is None for f in files):
        name = os.path.basename(os.path.abspath(root))
        root_id = "project:" + sid(name)
        while root_id in {p["id"] for p in projects.values()}:
            root_id += "_root"
        b.node(root_id, name, "project", inferred("no pyproject.toml, setup.py or setup.cfg above these files; the repository root is taken as the project"),
               resolution="inferred")
    return projects, root_id


def _project_for(rel: str, projects: dict):
    d = os.path.dirname(rel)
    while True:
        if d in projects:
            return projects[d]
        if not d:
            return None
        d = d.rsplit("/", 1)[0] if "/" in d else ""


# ---------------------------------------------------------------- parsing

class Cls:
    def __init__(self, mod, path, node, tid, outer):
        self.mod, self.path, self.node, self.id, self.outer = mod, path, node, tid, outer
        self.methods: dict = {}      # name -> [fn id]
        self.bases_raw = [_dotted(x) for x in node.bases]
        self.bases: list = []
        self.attr_types: dict | None = None


class Fn:
    def __init__(self, mod, cls, node, fid):
        self.mod, self.cls, self.node, self.id = mod, cls, node, fid
        self.name = node.name


class Mod:
    def __init__(self, qual, rel, is_pkg, tree, lines, project):
        self.qual, self.rel, self.is_pkg, self.tree, self.lines, self.project = qual, rel, is_pkg, tree, lines, project
        self.defs: dict = {}          # name -> ("fn", Fn) | ("type", Cls)
        self.imports: dict = {}       # alias -> ("mod", qual) | ("sym", modqual, name) | ("ext",)
        self.stars: list = []
        self.fns: list = []
        self.classes: list = []


def _dotted(expr):
    """`a.b.c` of a Name/Attribute chain as a list, else None."""
    parts = []
    while isinstance(expr, ast.Attribute):
        parts.append(expr.attr)
        expr = expr.value
    if isinstance(expr, ast.Name):
        parts.append(expr.id)
        return list(reversed(parts))
    return None


def _module_names(files: list, root: str) -> dict:
    """rel -> (qual, is_pkg). The `__init__.py` chain names a file; outside a package the relative path does."""
    fileset = set(files)
    out, used = {}, {}
    for rel in files:
        d = os.path.dirname(rel)
        stem = os.path.splitext(os.path.basename(rel))[0]
        chain = []
        cur = d
        while cur and (cur + "/__init__.py") in fileset:
            chain.append(os.path.basename(cur))
            cur = os.path.dirname(cur)
        if stem == "__init__":
            if chain:
                qual = ".".join(reversed(chain))
            else:
                qual = (d or os.path.basename(os.path.abspath(root))).replace("/", ".")
            is_pkg = True
        elif chain:
            qual = ".".join(list(reversed(chain)) + [stem])
            is_pkg = False
        else:
            qual = ".".join([x for x in d.split("/") if x] + [stem])
            is_pkg = False
        if qual in used:
            n = 2
            while "%s_dup%d" % (qual, n) in used:
                n += 1
            qual = "%s_dup%d" % (qual, n)
        used[qual] = rel
        out[rel] = (qual, is_pkg)
    return out


def _parse_all(root, files, names, projects, root_pid, unparsed):
    mods: dict = {}
    for rel in files:
        qual, is_pkg = names[rel]
        text = vr_lang.read_text(root, rel)
        tree = None
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                tree = ast.parse(text, filename=rel)
        except (SyntaxError, ValueError, RecursionError) as exc:
            unparsed.append("%s (%s)" % (rel, type(exc).__name__))
        p = _project_for(rel, projects)
        mods[qual] = Mod(qual, rel, is_pkg, tree, text.split("\n"), p["id"] if p else root_pid)
    return mods


def _collect_defs(m: Mod, b_ids: set):
    """Top-level and nested classes, functions, methods of one module (functions inside functions are not nodes)."""

    def uniq(base):
        nid, n = base, 1
        while nid in b_ids:
            n += 1
            nid = "%s_dup%d" % (base, n)
        b_ids.add(nid)
        return nid

    def visit(body, cls, path):
        for st in body:
            if isinstance(st, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if cls is None:
                    fid = uniq("fn:%s::%s" % (m.qual, sid(st.name)))
                else:
                    fid = uniq("fn:%s.%s::%s" % (m.qual, sid(".".join(path)), sid(st.name)))
                fn = Fn(m, cls, st, fid)
                m.fns.append(fn)
                if cls is None:
                    m.defs.setdefault(st.name, ("fn", fn))
                else:
                    cls.methods.setdefault(st.name, []).append(fid)
                    cls.fnobjs = getattr(cls, "fnobjs", {})
                    cls.fnobjs[fid] = fn
            elif isinstance(st, ast.ClassDef):
                npath = path + [st.name]
                c = Cls(m, npath, st, uniq("type:%s::%s" % (m.qual, sid(".".join(npath)))), cls)
                m.classes.append(c)
                if cls is None:
                    m.defs.setdefault(st.name, ("type", c))
                else:
                    cls.nested = getattr(cls, "nested", {})
                    cls.nested[st.name] = c
                visit(st.body, c, npath)
            elif isinstance(st, (ast.If, ast.Try, ast.With, ast.For, ast.While)) and cls is None:
                for blk in (getattr(st, "body", []), getattr(st, "orelse", []), getattr(st, "finalbody", [])):
                    visit(blk, cls, path)
                for h in getattr(st, "handlers", []):
                    visit(h.body, cls, path)

    if m.tree is not None:
        visit(m.tree.body, None, [])


def _import_bindings(m: Mod, mods: dict, node, table: dict, stars: list, sink):
    """Bind the names of one import statement into `table`; `sink(target_qual, lineno)` records import edges."""
    if isinstance(node, ast.Import):
        for a in node.names:
            if a.asname:
                table[a.asname] = ("mod", a.name) if a.name in mods else ("ext",)
                if a.name in mods:
                    sink(a.name, node.lineno)
            else:
                top = a.name.split(".")[0]
                table[top] = ("mod", top) if top in mods else ("ext",)
                if a.name in mods:
                    sink(a.name, node.lineno)
                elif top in mods:
                    sink(top, node.lineno)
    elif isinstance(node, ast.ImportFrom):
        pkg = m.qual if m.is_pkg else m.qual.rpartition(".")[0]
        if node.level:
            base = pkg
            for _ in range(node.level - 1):
                base = base.rpartition(".")[0]
            target = (base + "." + node.module) if node.module and base else (node.module or base)
        else:
            target = node.module or ""
        if not target or (target not in mods and not any(q.startswith(target + ".") for q in mods)):
            for a in node.names:
                if a.name != "*":
                    table[a.asname or a.name] = ("ext",)
            return
        if target in mods:
            sink(target, node.lineno)
        for a in node.names:
            if a.name == "*":
                if target in mods:
                    stars.append(target)
                continue
            sub = target + "." + a.name
            if sub in mods:
                table[a.asname or a.name] = ("mod", sub)
                sink(sub, node.lineno)
            else:
                table[a.asname or a.name] = ("sym", target, a.name)


# ---------------------------------------------------------------- resolution

class Resolver:
    def __init__(self, mods: dict):
        self.mods = mods
        self.subs: dict = {}
        self.byid = {}
        for m in mods.values():
            for c in m.classes:
                self.byid[c.id] = c
        for m in mods.values():
            for c in m.classes:
                c.bases = [t for t in (self.chain_type(m, r) for r in c.bases_raw if r) if t is not None]
        for m in mods.values():
            for c in m.classes:
                for base in self.ancestors(c):
                    self.subs.setdefault(base.id, set()).add(c.id)

    def ancestors(self, c: Cls, seen=None) -> list:
        seen = seen if seen is not None else set()
        out = []
        for b in c.bases:
            if b.id not in seen:
                seen.add(b.id)
                out.append(b)
                out += self.ancestors(b, seen)
        return out

    def lookup(self, qual: str, name: str, depth: int = 0):
        m = self.mods.get(qual)
        if m is None or depth > 6:
            return None
        if name in m.defs:
            return m.defs[name]
        bind = m.imports.get(name)
        if bind:
            return self.follow(bind, depth + 1)
        for s in m.stars:
            r = self.lookup(s, name, depth + 1)
            if r:
                return r
        return None

    def follow(self, bind, depth=0):
        if bind[0] == "mod":
            return ("mod", bind[1])
        if bind[0] == "sym":
            return self.lookup(bind[1], bind[2], depth)
        return None

    def bound(self, m: Mod, name: str, table: dict | None = None):
        if table and name in table:
            return self.follow(table[name])
        return self.lookup(m.qual, name)

    def step(self, state, part: str):
        """Move one attribute along: mod -> mod/fn/type, type -> method/nested, inst -> method/attr type."""
        if state is None:
            return None
        k = state[0]
        if k == "mod":
            sub = state[1] + "." + part
            if sub in self.mods:
                return ("mod", sub)
            r = self.lookup(state[1], part)
            return r
        if k in ("type", "inst"):
            c = state[1]
            m = self.find_method(c, part)
            if m:
                return ("fnid", m, c, k)
            nested = getattr(c, "nested", {}).get(part)
            if nested and k == "type":
                return ("type", nested)
            t = self.attr_type(c, part)
            if t is not None:
                return ("inst", t)
        return None

    def find_method(self, c: Cls, name: str):
        for k in [c] + self.ancestors(c):
            if name in k.methods:
                return (k, k.methods[name][0])
        return None

    def overridden(self, c: Cls, name: str) -> bool:
        for sid_ in self.subs.get(c.id, ()):
            sc = self.byid[sid_]
            if name in sc.methods:
                return True
        return False

    def chain_type(self, m: Mod, parts: list, table: dict | None = None):
        st = self.chain(m, parts, table)
        return st[1] if st and st[0] == "type" else None

    def chain(self, m: Mod, parts: list, table: dict | None = None):
        st = self.bound(m, parts[0], table)
        for p in parts[1:]:
            st = self.step(st, p)
        return st

    def attr_type(self, c: Cls, name: str):
        if c.attr_types is None:
            c.attr_types = {}
            for st in c.node.body:
                if isinstance(st, ast.AnnAssign) and isinstance(st.target, ast.Name):
                    t = self.ann(st.annotation, c.mod)
                    if t:
                        c.attr_types[st.target.id] = t
            for fns in getattr(c, "fnobjs", {}).values():
                self._scan_self_attrs(c, fns)
        if name in c.attr_types:
            return c.attr_types[name]
        for a in self.ancestors(c):
            if a.attr_types is None:
                self.attr_type(a, "")
            if name in a.attr_types:
                return a.attr_types[name]
        return None

    def _scan_self_attrs(self, c: Cls, fn: Fn):
        args = fn.node.args.args
        if not args:
            return
        me = args[0].arg
        ptypes = {a.arg: self.ann(a.annotation, c.mod) for a in args + fn.node.args.kwonlyargs if a.annotation is not None}
        for n in ast.walk(fn.node):
            tgt = val = ann = None
            if isinstance(n, ast.Assign) and len(n.targets) == 1:
                tgt, val = n.targets[0], n.value
            elif isinstance(n, ast.AnnAssign):
                tgt, val, ann = n.target, n.value, n.annotation
            if not (isinstance(tgt, ast.Attribute) and isinstance(tgt.value, ast.Name) and tgt.value.id == me):
                continue
            t = self.ann(ann, c.mod) if ann is not None else None
            if t is None and isinstance(val, ast.Call):
                parts = _dotted(val.func)
                t = self.chain_type(c.mod, parts) if parts else None
            if t is None and isinstance(val, ast.Name):
                t = ptypes.get(val.id)
            if t is not None and tgt.attr not in c.attr_types:
                c.attr_types[tgt.attr] = t

    def ann(self, expr, m: Mod):
        """The repository class an annotation names (Optional[X], X | None, "X" unwrapped), else None."""
        if expr is None:
            return None
        if isinstance(expr, ast.Constant) and isinstance(expr.value, str):
            try:
                expr = ast.parse(expr.value, mode="eval").body
            except SyntaxError:
                return None
        if isinstance(expr, ast.BinOp) and isinstance(expr.op, ast.BitOr):
            return self.ann(expr.left, m) or self.ann(expr.right, m)
        if isinstance(expr, ast.Subscript):
            base = _dotted(expr.value)
            if base and base[-1] in ("Optional", "Annotated", "Final", "ClassVar"):
                sl = expr.slice
                if isinstance(sl, ast.Tuple) and sl.elts:
                    sl = sl.elts[0]
                return self.ann(sl, m)
            return None
        parts = _dotted(expr)
        return self.chain_type(m, parts) if parts else None


# ---------------------------------------------------------------- calls

def _local_names(fn: ast.AST) -> set:
    names = set()
    a = fn.args
    for arg in a.posonlyargs + a.args + a.kwonlyargs + ([a.vararg] if a.vararg else []) + ([a.kwarg] if a.kwarg else []):
        names.add(arg.arg)
    for n in ast.walk(fn):
        if isinstance(n, ast.Name) and isinstance(n.ctx, (ast.Store, ast.Del)):
            names.add(n.id)
        elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and n is not fn:
            names.add(n.name)
        elif isinstance(n, ast.ExceptHandler) and n.name:
            names.add(n.name)
        elif isinstance(n, ast.arg):
            names.add(n.arg)
    return names


def _calls_of(res: Resolver, fn: Fn, mod: Mod, mods: dict, emit, node=None):
    """Call `emit(target_id_or_cls, resolution, lineno, label)` for every resolvable call of `fn` (or of a module body)."""
    scope = node if node is not None else fn.node
    cls = fn.cls if fn is not None else None
    table: dict = {}
    stars: list = []
    local: set = set()
    var_types: dict = {}
    is_static = False
    if fn is not None:
        local = _local_names(scope)
        for d in scope.decorator_list:
            parts = _dotted(d)
            if parts and parts[-1] == "staticmethod":
                is_static = True
        args = scope.args.posonlyargs + scope.args.args + scope.args.kwonlyargs
        for a in args:
            t = res.ann(a.annotation, mod) if a.annotation is not None else None
            if t is not None:
                var_types[a.arg] = ("inst", t)
        if cls is not None and not is_static and scope.args.args:
            first = scope.args.args[0].arg
            is_cm = any((_dotted(d) or [""])[-1] == "classmethod" for d in scope.decorator_list)
            var_types[first] = ("type" if is_cm else "inst", cls)
    walker = ast.walk(scope) if node is None else _module_level_nodes(node)
    nodes = list(walker)
    for n in nodes:
        if isinstance(n, (ast.Import, ast.ImportFrom)):
            _import_bindings(mod, mods, n, table, stars, lambda q, ln: None)
    for n in nodes:
        t = None
        if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name):
            c = res.ann(n.annotation, mod)
            t = (n.target.id, ("inst", c)) if c else None
        elif isinstance(n, ast.Assign) and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name) \
                and isinstance(n.value, ast.Call):
            parts = _dotted(n.value.func)
            c = res.chain_type(mod, parts, table) if parts and parts[0] not in local - {n.targets[0].id} else None
            t = (n.targets[0].id, ("inst", c)) if c else None
        elif isinstance(n, ast.withitem) and isinstance(n.optional_vars, ast.Name) and isinstance(n.context_expr, ast.Call):
            parts = _dotted(n.context_expr.func)
            c = res.chain_type(mod, parts, table) if parts else None
            t = (n.optional_vars.id, ("inst", c)) if c else None
        if t and t[0] not in var_types:
            var_types[t[0]] = t[1]
    for n in nodes:
        if not isinstance(n, ast.Call):
            continue
        f = n.func
        st = None
        # super().m()
        if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Call) and isinstance(f.value.func, ast.Name) \
                and f.value.func.id == "super" and cls is not None:
            for base in res.ancestors(cls):
                if f.attr in base.methods:
                    st = ("fnid", (base, base.methods[f.attr][0]), cls, "super")
                    break
        else:
            parts = _dotted(f)
            if not parts:
                continue
            head = parts[0]
            if head in var_types:
                st = var_types[head]
                for p in parts[1:]:
                    st = res.step(st, p)
            elif head in local and head not in table:
                continue
            else:
                st = res.chain(mod, parts, table)
        if st is None:
            continue
        if st[0] == "fn":
            emit(st[1].id, "static", n.lineno, None)
        elif st[0] == "type":
            c = st[1]
            init = res.find_method(c, "__init__")
            if init:
                emit(init[1], "dispatch" if res.overridden(init[0], "__init__") else "static", n.lineno, None)
            else:
                emit(c.id, "static", n.lineno, "constructs")
        elif st[0] == "fnid":
            owner, fid = st[1]
            recv, via = st[2], st[3]
            disp = via == "inst" and res.overridden(recv, f.attr)
            emit(fid, "dispatch" if disp else "static", n.lineno, None)


def _module_level_nodes(tree):
    """AST nodes of a module body outside any def or class."""
    stack = list(tree.body)
    while stack:
        n = stack.pop()
        yield n
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            continue
        stack.extend(ast.iter_child_nodes(n))


# ---------------------------------------------------------------- extract

def extract_python(root: str, layers=(1, 2), calls=(), cfg_test: bool = False, timings=None, **_ignored) -> dict:
    root = os.path.abspath(root)
    if not os.path.isdir(root):
        raise LangError("%s is not a directory" % root)
    allf = vr_lang.walk_files(root, (".py",))
    files = allf if cfg_test else [f for f in allf if not _is_test(f)]
    if not files:
        raise LangError("no Python source files under %s" % root)
    b = Builder()
    layers_meta: dict = {}
    projects, root_pid = layer_projects(root, files, b)
    layers_meta["projects"] = {"status": "ran"}
    names = _module_names(files, root)
    unparsed: list = []
    mods = _parse_all(root, files, names, projects, root_pid, unparsed)
    ids: set = set()
    for q in sorted(mods):
        _collect_defs(mods[q], ids)
    # import tables (module level), needed by every later step
    import_edges: dict = {}
    for q in sorted(mods):
        m = mods[q]
        if m.tree is None:
            continue
        for st in m.tree.body:
            for n in ([st] + (list(ast.walk(st)) if isinstance(st, (ast.If, ast.Try)) else [])):
                if isinstance(n, (ast.Import, ast.ImportFrom)):
                    _import_bindings(m, mods, n, m.imports, m.stars,
                                     lambda tq, ln, m=m: import_edges.setdefault((m.qual, tq), []).append(ln))
    res = Resolver(mods)

    if 2 in layers:
        for q in sorted(mods):
            m = mods[q]
            mid = "module:" + sid(q)
            note = "package" if m.is_pkg else "module file"
            ev = src(m.rel, 1, note)
            if not "".join(m.lines):
                ev = inferred("%s %s: %s exists but is empty, so no line can be cited" % (note, q, m.rel))
            elif m.tree is None:
                ev = src(m.rel, 1, note + "; not parsed (syntax error): its classes and functions are missing")
            b.node(mid, q.rsplit(".", 1)[-1], "module", ev)
            parent = q.rpartition(".")[0]
            while parent and parent not in mods:
                parent = parent.rpartition(".")[0]
            pid = "module:" + sid(parent) if parent else (m.project or None)
            if pid:
                b.edge("contains", pid, mid, ev)
            for c in m.classes:
                cev = src(m.rel, c.node.lineno, "class %s" % ".".join(c.path))
                b.node(c.id, c.path[-1], "type", cev)
                owner = c.outer.id if c.outer is not None else mid
                b.edge("contains", owner, c.id, cev)
        for (a, t), lns in sorted(import_edges.items()):
            if a != t:
                am = mods[a]
                for ln in lns:
                    b.edge("uses", "module:" + sid(a), "module:" + sid(t), src(am.rel, ln, "import"), label="imports")
        for q in sorted(mods):
            for c in mods[q].classes:
                for base in c.bases:
                    if base.id != c.id:
                        b.edge("uses", c.id, base.id, src(c.mod.rel, c.node.lineno, "base class"), label="extends")
        layers_meta["modules"] = {"status": "ran"}
        if unparsed:
            layers_meta["modules"]["reason"] = "not parsed (syntax error), classes and functions missing: " + ", ".join(sorted(unparsed)[:20])
    else:
        layers_meta["modules"] = {"status": "skipped", "reason": "not requested"}

    def fn_node(f: Fn):
        sig = _signature(f)
        b.node(f.id, f.name, "fn", src(f.mod.rel, f.node.lineno, "definition: " + sig))
        parent = f.cls.id if f.cls is not None else "module:" + sid(f.mod.qual)
        if b.has(parent):
            b.edge("contains", parent, f.id, src(f.mod.rel, f.node.lineno, "definition"))

    if 3 in layers:
        for q in sorted(mods):
            for f in mods[q].fns:
                fn_node(f)
        layers_meta["functions"] = {"status": "ran"}
    else:
        layers_meta["functions"] = {"status": "skipped", "reason": "not requested"}

    tools = [{"name": "python-ast", "version": "%d.%d.%d" % sys.version_info[:3]}]
    if calls:
        allfns = [f for q in sorted(mods) for f in mods[q].fns]
        focus = set()
        missing = []
        for spec in calls:
            hit = [f for f in allfns if vr_lang.focus_matches(spec, f.name, f.mod.rel)]
            if not hit:
                missing.append(spec)
            focus.update(f.id for f in hit)
        byid = {f.id: f for f in allfns}
        pending: list = []

        def run_calls(f: Fn, mod: Mod, src_id: str, node=None):
            def emit(target, resolution, lineno, label):
                pending.append((src_id, target, resolution, mod.rel, lineno, label))
            _calls_of(res, f, mod, mods, emit, node)

        for f in allfns:
            run_calls(f, f.mod, f.id)
        for q in sorted(mods):
            m = mods[q]
            if m.tree is not None:
                run_calls(None, m, "fn:%s::__module__" % m.qual, m.tree)
        if not focus:
            layers_meta["calls"] = {"status": "skipped", "reason": "function(s) not found: " + ", ".join(missing)}
        else:
            used = set(focus)
            kept = [p for p in pending if p[0] in focus or p[1] in focus]
            for s_, t_, resn, rel, ln, label in kept:
                used.add(s_)
                used.add(t_)
            for fid in sorted(used):
                if fid in byid:
                    fn_node(byid[fid])
                elif fid.endswith("::__module__"):
                    qual = fid[len("fn:"):-len("::__module__")]
                    b.node(fid, "(module code)", "fn", src(mods[qual].rel, 1, "module-level statements"))
                    if b.has("module:" + sid(qual)):
                        b.edge("contains", "module:" + sid(qual), fid, src(mods[qual].rel, 1, "module-level statements"))
            for s_, t_, resn, rel, ln, label in kept:
                if b.has(t_) or t_ in byid:
                    b.edge("calls", s_, t_, src(rel, ln, "call site"), resolution=resn, label=label)
            layers_meta["calls"] = {"status": "ran"}
            if missing:
                layers_meta["calls"]["reason"] = "function(s) not found: " + ", ".join(missing)
    else:
        layers_meta["calls"] = {"status": "skipped", "reason": "not requested"}
    return vr_lang.assemble(root, b, layers_meta, tools)


def _signature(f: Fn) -> str:
    try:
        a = ast.unparse(f.node.args)
    except Exception:
        a = "..."
    ret = ""
    if f.node.returns is not None:
        try:
            ret = " -> " + ast.unparse(f.node.returns)
        except Exception:
            ret = ""
    kw = "async def" if isinstance(f.node, ast.AsyncFunctionDef) else "def"
    return ("%s %s(%s)%s" % (kw, f.name, a, ret))[:120]


# ---------------------------------------------------------------- selftest

_FIXTURE = {
    "pyproject.toml": '[project]\nname = "shop"\ndependencies = ["lib-core>=1"]\n',
    "shop/__init__.py": "from .service import Service\n",
    "shop/service.py": (
        "from shop import util\nfrom .repo import Repo, BaseRepo\nimport shop.util as u\n\n\n"
        "class Service:\n    def __init__(self, repo: Repo):\n        self.repo = repo\n        self.cache = BaseRepo()\n\n"
        "    def handle(self, x):\n        util.log(x)\n        r = self.repo.load(x)\n        self.cache.save(r)\n"
        "        helper(r)\n        return u.fmt(r)\n\n\n"
        "def helper(r):\n    s = Service(Repo())\n    s.handle(r)\n\n\n"
        "if __name__ == \"__main__\":\n    helper(1)\n"),
    "shop/repo.py": (
        "class BaseRepo:\n    def save(self, r):\n        pass\n\n    def load(self, k):\n        return self.save(k)\n\n\n"
        "class Repo(BaseRepo):\n    def save(self, r):\n        super().save(r)\n"),
    "shop/util.py": "def log(x):\n    print(x)\n\n\ndef fmt(x):\n    return str(x)\n",
    "shop/broken.py": "def oops(:\n",
    "tests/test_service.py": "from shop.service import Service\n\n\ndef test_it():\n    Service(None)\n",
    "libs/core/pyproject.toml": '[project]\nname = "lib_core"\n',
    "libs/core/lib_core/__init__.py": "",
    "libs/core/lib_core/base.py": "def base():\n    pass\n",
}


def _write(root: str, files: dict):
    for rel, text in files.items():
        p = os.path.join(root, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(text)


def _selftest() -> int:
    fails: list = []

    def check(name, cond, detail=""):
        if not cond:
            fails.append("%s %s" % (name, detail))

    with tempfile.TemporaryDirectory() as d:
        d = os.path.realpath(d)
        _write(d, _FIXTURE)
        g = extract_python(d, layers=(1, 2, 3), calls=["handle", "helper"])
        issues = vr_graph_validate(g, d)
        check("valid", issues == [], json.dumps(issues)[:400])
        ids = {n["id"] for n in g["nodes"]}
        for want in ("project:shop", "project:lib_core", "module:shop", "module:shop.service", "module:shop.util",
                     "type:shop.service::Service", "type:shop.repo::Repo", "fn:shop.service.Service::handle",
                     "fn:shop.service::helper", "fn:shop.util::log", "module:lib_core.base", "module:shop.broken"):
            check("node:" + want, want in ids)
        check("tests-excluded", not any("test_service" in i for i in ids))
        edges = {e["id"]: e for e in g["edges"]}
        check("depends", "edge:depends:project:shop:project:lib_core" in edges, str(sorted(edges)[:5]))
        check("contains", "edge:contains:module:shop:module:shop.service" in edges)
        check("imports", "edge:uses:module:shop.service:module:shop.util" in edges)
        check("extends", "edge:uses:type:shop.repo::Repo:type:shop.repo::BaseRepo" in edges)

        def call(a, t):
            return edges.get("edge:calls:%s:%s" % (a, t))
        h = "fn:shop.service.Service::handle"
        check("call:module-attr", call(h, "fn:shop.util::log") is not None)
        check("call:alias-attr", call(h, "fn:shop.util::fmt") is not None)
        check("call:self-attr-typed", call(h, "fn:shop.repo.Repo::load") is None and call(h, "fn:shop.repo.BaseRepo::load") is not None,
              str([e for e in edges if "calls" in e]))
        c = call(h, "fn:shop.repo.BaseRepo::save")
        check("call:dispatch-when-overridden", c is not None and c["resolution"] == "dispatch", str(c))
        check("call:local-fn", call(h, "fn:shop.service::helper") is not None)
        check("call:ctor", call("fn:shop.service::helper", "fn:shop.service.Service::__init__") is not None)
        check("call:var-typed", call("fn:shop.service::helper", h) is not None)
        check("call:module-body", call("fn:shop.service::__module__", "fn:shop.service::helper") is not None)
        check("evidence-call-site", call(h, "fn:shop.util::log")["evidence"][0]["note"] == "call site")
        check("no-guess", call(h, "fn:shop.util::print") is None and not any(e["target"].endswith("::print") for e in g["edges"]))
        check("unparsed-listed", "shop/broken.py (SyntaxError)" in g["meta"]["layers"]["modules"].get("reason", ""))
        check("determinism", json.dumps(g, sort_keys=True) == json.dumps(extract_python(d, layers=(1, 2, 3), calls=["handle", "helper"]), sort_keys=True))
        g2 = extract_python(d, layers=(1, 2))
        check("no-fn-without-layer3", not any(n["kind"] == "fn" for n in g2["nodes"]))
        check("calls-skipped", g2["meta"]["layers"]["calls"]["status"] == "skipped")
        g3 = extract_python(d, layers=(1, 2), calls=["nonesuch"])
        check("calls-missing", g3["meta"]["layers"]["calls"]["status"] == "skipped" and "nonesuch" in g3["meta"]["layers"]["calls"]["reason"])
        g4 = extract_python(d, layers=(1, 2), cfg_test=True)
        check("cfg-test", "module:tests.test_service" in {n["id"] for n in g4["nodes"]})
        g5 = extract_python(d, layers=(2,), calls=["shop/service.py:helper"])
        check("focus-relpath", any(n["id"] == "fn:shop.service::helper" for n in g5["nodes"]))
    with tempfile.TemporaryDirectory() as d:
        d = os.path.realpath(d)
        _write(d, {"a.py": "def f():\n    pass\n"})
        g = extract_python(d, layers=(1, 2, 3))
        p = [n for n in g["nodes"] if n["kind"] == "project"]
        check("inferred-root-project", len(p) == 1 and p[0]["evidence"][0]["tier"] == "inferred" and p[0]["resolution"] == "inferred", str(p))
        check("valid-bare", vr_graph_validate(g, d) == [])
    if fails:
        print("vr_extract_py selftest FAIL:\n  " + "\n  ".join(fails))
        return 1
    print("vr_extract_py selftest ok")
    return 0


def vr_graph_validate(g: dict, root: str) -> list:
    return vr_lang.vr_graph.validate(g, root=root, check_budget=False)


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
        g = extract_python(a.root, layers=tuple(sorted({int(x) for x in a.layers.split(",") if x.strip()})),
                           calls=[c for c in a.calls.split(",") if c.strip()], cfg_test=a.cfg_test)
    except (LangError, ValueError) as exc:
        print("vr_extract_py: %s" % exc, file=sys.stderr)
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
