#!/usr/bin/env python3
"""Visual review: shared helpers of the Python and C# extractors (vr_extract_py.py, vr_extract_cs.py).

Not run on its own except for `--selftest`. The two extractors emit the same graph as the
Rust path of `vr_extract.py` (schema `references/visual-review/graph.schema.json`, view "full"):

  project  (kind "project")  the build unit: a pyproject/setup manifest or a .csproj
  module   (kind "module")   a Python module or package, or a C# namespace
  type     (kind "type")     a Python class or a C# class/struct/interface/record/enum
  fn       (kind "fn")       a Python function or method, a C# method or constructor
  edges    contains, depends (project to project), uses (module imports, extends/implements), calls

Ids follow the stable id rule (kind plus qualified name, never a counter) and the last `::`
segment of an `fn:` id is the function name, as in the Rust path (vr_prepare.candidates reads it).
Everything is deterministic: sorted, no timestamps.

Honesty rule (the same as the Rust path): an edge or node is `static` when the source proves it,
`dispatch` when the callee is a virtual, abstract or interface member (the implementation is chosen
at run time), `inferred` when a name matched more than one candidate or the arity did not fit. A
name that cannot be resolved inside the repository is dropped, never guessed.

No third-party imports.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import vr_graph  # noqa: E402

VERSION = "1.0.0"
SCHEMA_VERSION = "1.0.0"
SKIP_DIRS = {".git", ".hg", ".svn", "node_modules", "__pycache__", "venv", ".venv", "env", "site-packages", "build",
             "dist", ".tox", ".mypy_cache", ".pytest_cache", ".ruff_cache", "target", "bin", "obj", ".vs", ".idea", ".terraform"}
ID_BAD = re.compile(r"[^A-Za-z0-9_.:-]")


class LangError(Exception):
    """The extraction cannot continue (vr_extract turns it into its own ExtractError)."""


def sid(text: str) -> str:
    """A string made valid for the id pattern of the schema."""
    return ID_BAD.sub("_", text)


def shorten(label: str) -> tuple:
    if len(label) <= vr_graph.NODE_LABEL_MAX:
        return label, None
    return label[:vr_graph.NODE_LABEL_MAX - 1] + "…", label


def src(path: str, line: int, note: str | None = None) -> dict:
    e = {"tier": "source", "path": path, "line": max(1, int(line))}
    if note:
        e["note"] = note[:300]
    return e


def inferred(note: str) -> dict:
    return {"tier": "inferred", "note": note}


class Builder:
    """Nodes and edges by id; a repeated node merges its evidence, a repeated edge counts."""

    def __init__(self):
        self.nodes: dict = {}
        self.edges: dict = {}

    def node(self, nid: str, label: str, kind: str, ev: dict, resolution: str = "static", **extra) -> str:
        if nid in self.nodes:
            have = self.nodes[nid]["evidence"]
            if ev not in have and len(have) < 8:
                have.append(ev)
            return nid
        label, full = shorten(label)
        if full:
            ev = dict(ev, note=((ev.get("note") + "; ") if ev.get("note") else "") + "full name: " + full)
        n = {"id": nid, "label": label, "kind": kind, "status": "context", "resolution": resolution,
             "label_source": "extractor", "evidence": [ev]}
        n.update(extra)
        self.nodes[nid] = n
        return nid

    def edge(self, kind: str, s: str, t: str, ev: dict, resolution: str = "static", label: str | None = None) -> str:
        eid = "edge:%s:%s:%s" % (kind, s, t)
        if eid in self.edges:
            e = self.edges[eid]
            e["count"] = e.get("count", 1) + 1
            if ev not in e["evidence"] and len(e["evidence"]) < 3:
                e["evidence"].append(ev)
            return eid
        e = {"id": eid, "source": s, "target": t, "kind": kind, "status": "context", "resolution": resolution,
             "evidence": [ev]}
        if label:
            e["label"] = label[:40]
        self.edges[eid] = e
        return eid

    def has(self, nid: str) -> bool:
        return nid in self.nodes


def walk_files(root: str, exts: tuple, extra_skip=()) -> list:
    """Repository-relative paths (forward slashes), sorted, of the files with one of `exts`."""
    out = []
    skip = SKIP_DIRS | set(extra_skip)
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in skip and not d.startswith(".") and not d.endswith(".egg-info"))
        for f in sorted(filenames):
            if f.endswith(exts):
                out.append(os.path.relpath(os.path.join(dirpath, f), root).replace(os.sep, "/"))
    return sorted(out)


def read_text(root: str, rel: str) -> str:
    with open(os.path.join(root, rel), encoding="utf-8-sig", errors="replace") as fh:
        return fh.read()


def line_of(lines: list, needle: str, default: int = 1) -> int:
    """First 1-based line containing `needle` (case-insensitive), else `default`."""
    low = needle.lower()
    for i, raw in enumerate(lines, 1):
        if low in raw.lower():
            return i
    return default


def git_commit(root: str) -> str:
    try:
        r = subprocess.run(["git", "rev-parse", "--short=8", "HEAD"], cwd=root, capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        return "unknown"
    return r.stdout.strip() if r.returncode == 0 and r.stdout.strip() else "unknown"


def assemble(root: str, b: Builder, layers_meta: dict, tools: list) -> dict:
    nodes = sorted(b.nodes.values(), key=lambda x: x["id"])
    edges = sorted(b.edges.values(), key=lambda x: x["id"])
    tools = sorted(tools + [{"name": "vr_extract", "version": VERSION}], key=lambda x: x["name"])
    name = os.path.basename(os.path.abspath(root))
    return {"schema_version": SCHEMA_VERSION, "view": "full", "title": "Before graph of %s" % name,
            "meta": {"repo": name, "commit": git_commit(root), "generated_by": "extractor", "tools": tools,
                     "layers": layers_meta},
            "nodes": nodes, "edges": edges}


def focus_matches(spec: str, name: str, rel: str) -> bool:
    """A --calls entry is `name` or `relpath:name`."""
    if ":" in spec:
        p, n = spec.rsplit(":", 1)
        return n == name and p.replace(os.sep, "/") == rel
    return spec == name


def _selftest() -> int:
    fails = []
    b = Builder()
    ev = src("a.py", 3, "x")
    b.node("fn:a::f", "f", "fn", ev)
    b.node("fn:a::f", "f", "fn", src("a.py", 9))
    b.edge("calls", "fn:a::f", "fn:a::g", ev)
    b.edge("calls", "fn:a::f", "fn:a::g", src("a.py", 4))
    if len(b.nodes["fn:a::f"]["evidence"]) != 2:
        fails.append("node evidence merge")
    if b.edges["edge:calls:fn:a::f:fn:a::g"].get("count") != 2:
        fails.append("edge count")
    if sid("a<b>/c") != "a_b__c":
        fails.append("sid")
    if not focus_matches("x.py:f", "f", "x.py") or focus_matches("y.py:f", "f", "x.py") or not focus_matches("f", "f", "q"):
        fails.append("focus_matches")
    if fails:
        print("vr_lang selftest FAIL: " + "; ".join(fails))
        return 1
    print("vr_lang selftest ok")
    return 0


if __name__ == "__main__":
    sys.exit(_selftest() if "--selftest" in sys.argv else 2)
