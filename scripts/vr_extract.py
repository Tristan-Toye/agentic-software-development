#!/usr/bin/env python3
"""Visual review: extract the "before" change graph from a Cargo workspace.

    vr_extract.py rust REPO_ROOT [--layers 1,2] [--crates a,b] [--calls fn1,fn2] [--timeout 120]
                       [--cfg-test] [-o out.json] [--timings]
    vr_extract.py --selftest
    vr_extract.py --live REPO_ROOT       (opt-in: the real tools, needs cargo)

Layers (OQ-03, taken by recommendation: cargo metadata plus cargo-modules, call
hierarchy only on request):
  1  crates and the normal dependency edges between workspace members
     (`cargo metadata --format-version 1 --no-deps`).
  2  modules per crate (`cargo modules structure --lib|--bin NAME -p CRATE`,
     cargo-modules pinned to 0.27.0). Bin targets are run too (D0 recall miss:
     crates/platform/src/bin/llm_gateway.rs). If cargo-modules is missing or is
     another version, the layer is recorded as `skipped` in meta.layers with a
     reason naming the tool and the pin; the run does not fail.
     With --crates a,b the layer runs only for those crates (layer 1 stays the
     whole workspace): the other crates keep their crate node, and
     meta.layers.modules lists `limited_to` and `skipped_crates`.
  4  (only with --calls) callers and callees of the named functions through a
     rust-analyzer call hierarchy (vr_ra.py), bounded by --timeout seconds.

The output is a graph with view "full": not budgeted (budgets apply to the
views VR-05 picks from it) and valid under `vr_graph.py validate`. Ids follow
the stable id rule of the schema (kind plus qualified name): crate:NAME,
module:CRATE::PATH, bin:CRATE::bin::NAME, fn:CRATE::PATH::NAME. Output is
deterministic: no timestamps, everything sorted. Timings go to stderr/--timings.

Rules for layer 1 (D0 REPORT section 2): only `normal` dependencies count; dev
and build edges, self edges and edges to `workspace-hack` are dropped. The
`workspace-hack` crate itself stays a node (it is a workspace member).

The language seam: `extract(lang, root, ...)` dispatches on `lang`; "rust" is
the only entry in EXTRACTORS. A new language adds one function and one entry.
Exit codes: 0 written; 1 the extractor failed; 2 bad arguments.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import vr_graph  # noqa: E402
from vr_ra import RaClient, RaError  # noqa: E402

VERSION = "1.0.0"
# The one source of truth for how cargo-modules is told what to read (VC-06, rules R1/R2 of vr_c3_agree.py: c3 reads every file, so
# the module layer must see the feature-gated and linux-only modules too). vr_c3_agree.py imports these; vr_refresh keys its module cache on them.
CM_TARGET = "x86_64-unknown-linux-gnu"
CM_ALL_FEATURES = "--all-features"
CM_FLAGS = (CM_ALL_FEATURES, "--target", CM_TARGET)
CARGO_MODULES_PIN = "0.27.0"
# VC-08 limit for layers 1 and 2 with --jobs 4. D0 proposed 60 s from lib-only runs (7 s warm);
# with the --bin targets added (24 processes on config-harness) the live run took 93.5 s (serial: 189 s).
VC08_LIMIT_S = 120
HIDDEN_DEP_TARGETS = {"workspace-hack"}
LIB_KINDS = {"lib", "rlib", "dylib", "cdylib", "staticlib", "proc-macro"}


class ExtractError(Exception):
    """The extraction cannot continue (cargo missing, metadata failed)."""


# ----------------------------------------------------------------- running

def default_runner(cmd: list, cwd: str, timeout: float | None = None):
    """Run a command; returns (returncode, stdout, stderr). rc 127 = not found."""
    try:
        env = dict(os.environ, NO_COLOR="1", CARGO_TERM_COLOR="never")  # cargo-modules colours a pipe otherwise
        p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout, env=env)
    except FileNotFoundError:
        return 127, "", "%s: command not found" % cmd[0]
    except subprocess.TimeoutExpired:
        return 124, "", "%s timed out" % " ".join(cmd[:3])
    return p.returncode, p.stdout, p.stderr


def _shorten(label: str) -> tuple:
    """(label, full): a label over vr_graph.NODE_LABEL_MAX ends with an ellipsis; `full` is the
    original, kept in the evidence note so the inspector still shows it."""
    if len(label) <= vr_graph.NODE_LABEL_MAX:
        return label, None
    return label[:vr_graph.NODE_LABEL_MAX - 1] + "\u2026", label


def _node(nid, label, kind, ev, status="context", **extra):
    label, full = _shorten(label)
    if full:
        ev = dict(ev, note=(ev["note"] + "; " if ev.get("note") else "") + "full name: " + full)
    n = {"id": nid, "label": label, "kind": kind, "status": status, "label_source": "extractor", "evidence": [ev]}
    n.update(extra)
    return n


def _src(path, line, note=None):
    e = {"tier": "source", "path": path, "line": line}
    if note:
        e["note"] = note
    return e


class _Files:
    """Cached line reader for evidence lookups."""

    def __init__(self, root):
        self.root = root
        self._c: dict = {}

    def lines(self, rel):
        if rel not in self._c:
            try:
                with open(os.path.join(self.root, rel), encoding="utf-8", errors="replace") as fh:
                    self._c[rel] = fh.read().split("\n")
            except OSError:
                self._c[rel] = None
        return self._c[rel]

    def exists(self, rel):
        return self.lines(rel) is not None


# ------------------------------------------------------------------ layer 1

def _manifest_line(files: _Files, rel: str, key: str, package: bool = False) -> int:
    """Line of a dependency (or of the package name) in a Cargo.toml; 1 if not found."""
    lines = files.lines(rel) or []
    section = ""
    for i, raw in enumerate(lines, 1):
        s = raw.strip()
        m = re.match(r"^\[\[?([^\]]+)\]\]?", s)
        if m:
            section = m.group(1).strip()
            if not package and re.match(r"^(.*\.)?(dependencies)\.%s$" % re.escape(key), section):
                return i
            continue
        if package and section == "package" and re.match(r"^name\s*=", s):
            return i
        if not package and re.search(r"(^|\.)dependencies$", section) and not section.startswith(("dev-", "build-")) \
                and re.match(r"^%s\s*(=|\.)" % re.escape(key), s):
            return i
    return 1


def layer_crates(meta: dict, files: _Files, ws_root: str):
    members_ids = set(meta["workspace_members"])
    pkgs = sorted((p for p in meta["packages"] if p["id"] in members_ids), key=lambda p: p["name"])
    names = {p["name"] for p in pkgs}
    nodes, edges = [], {}
    for p in pkgs:
        man = os.path.relpath(p["manifest_path"], ws_root)
        nodes.append(_node("crate:" + p["name"], p["name"], "crate",
                           _src(man, _manifest_line(files, man, p["name"], package=True), "package name"),
                           resolution="static"))
    for p in pkgs:
        man = os.path.relpath(p["manifest_path"], ws_root)
        for dep in p.get("dependencies", []):
            if dep.get("kind") is not None or dep.get("source") is not None:
                continue  # dev, build and registry dependencies
            tgt = dep["name"]
            if tgt not in names or tgt == p["name"] or tgt in HIDDEN_DEP_TARGETS:
                continue
            eid = "edge:depends:crate:%s:crate:%s" % (p["name"], tgt)
            if eid in edges:
                continue
            key = dep.get("rename") or tgt
            edges[eid] = {"id": eid, "source": "crate:" + p["name"], "target": "crate:" + tgt, "kind": "depends",
                          "status": "context", "resolution": "static",
                          "evidence": [_src(man, _manifest_line(files, man, key), "dependency")]}
    return pkgs, nodes, [edges[k] for k in sorted(edges)]


# ------------------------------------------------------------------ layer 2

_TREE = re.compile(r"^((?:│   |    )*)(?:├── |└── )(.*)$")
_MOD = re.compile(r"^mod (\w+)\b")
_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def parse_structure(text: str):
    """Parse `cargo modules structure` output to [(depth, name)] of modules, root excluded.

    depth 1 = a top-level module. Non-module items (types, fns) are skipped; a module
    never sits below one.
    """
    out = []
    for raw in _ANSI.sub("", text).splitlines():
        m = _TREE.match(raw)
        if not m:
            continue
        mm = _MOD.match(m.group(2))
        if mm:
            out.append((len(m.group(1)) // 4 + 1, mm.group(1)))
    return out


def _root_dirbase(root_file: str) -> str:
    d, base = os.path.split(root_file)
    return d if base in ("lib.rs", "main.rs") else os.path.join(d, base[:-3])


def _find_decl(files: _Files, parent_file: str, name: str):
    pat = re.compile(r"^\s*(?:pub(?:\([^)]*\))?\s+)?mod\s+%s\s*([;{])" % re.escape(name))
    for i, raw in enumerate(files.lines(parent_file) or [], 1):
        m = pat.match(raw)
        if m:
            return i, m.group(1)
    return None, None


def modules_from_structure(entries, root_id: str, qual_prefix: str, root_file: str, files: _Files):
    """Build module nodes and `contains` edges for one target.

    Evidence is the `mod name;` declaration in the parent's file, which always
    exists; a module the declaration of which cannot be found (macro, #[path])
    gets an inferred note. `root_id` is the crate node (lib) or bin node.
    """
    nodes, edges = [], []
    stack = [{"id": root_id, "qual": qual_prefix, "file": root_file, "dir": _root_dirbase(root_file)}]
    for depth, name in entries:
        del stack[depth:]
        parent = stack[-1] if stack else None
        if parent is None:
            continue
        line, kind = _find_decl(files, parent["file"], name)
        qual = "%s::%s" % (parent["qual"], name)
        nid = "module:" + qual
        if line is None:
            ev = {"tier": "inferred", "note": "module %s is not declared as `mod %s` in %s (macro or #[path])" % (qual, name, parent["file"])}
            child = {"file": parent["file"], "dir": os.path.join(parent["dir"], name)}
        elif kind == "{":
            ev = _src(parent["file"], line, "inline module")
            child = {"file": parent["file"], "dir": os.path.join(parent["dir"], name)}
        else:
            ev = _src(parent["file"], line, "module declaration")
            f1 = os.path.join(parent["dir"], name + ".rs")
            f2 = os.path.join(parent["dir"], name, "mod.rs")
            f = f1 if files.exists(f1) else f2 if files.exists(f2) else parent["file"]
            child = {"file": f, "dir": os.path.join(parent["dir"], name)}
        child.update({"id": nid, "qual": qual})
        stack.append(child)
        nodes.append(_node(nid, name, "module", ev, resolution="static"))
        edges.append({"id": "edge:contains:%s:%s" % (parent["id"], nid), "source": parent["id"], "target": nid,
                      "kind": "contains", "status": "context", "resolution": "static", "evidence": [ev]})
    return nodes, edges


def cargo_modules_version(runner, root):
    rc, out, _ = runner(["cargo", "modules", "--version"], root)
    if rc != 0:
        return None, "cargo-modules is not installed (pinned version %s)" % CARGO_MODULES_PIN
    m = re.search(r"(\d+\.\d+\.\d+)", out)
    if not m or m.group(1) != CARGO_MODULES_PIN:
        return m.group(1) if m else None, "cargo-modules %s found, pinned version is %s" % (m.group(1) if m else "?", CARGO_MODULES_PIN)
    return m.group(1), None


def layer_modules(pkgs, root: str, ws_root: str, files: _Files, runner, cfg_test: bool, jobs: int = 4) -> tuple[list, list, dict, list]:
    """Returns (nodes, edges, layer_meta, tools). Never raises for a missing tool.

    One `cargo modules` process per lib and bin target; `jobs` run at once (D0: 6 to
    15 s each, so serial is 3 minutes on config-harness). Results are consumed in
    target order, so the output does not depend on which process finishes first.
    """
    ver, why = cargo_modules_version(runner, root)
    if why:
        print("vr_extract: module layer skipped: %s" % why, file=sys.stderr)
        return [], [], {"status": "skipped", "reason": why}, []
    nodes, edges, failed = [], [], []
    base = ["cargo", "modules", "structure", "--no-fns", "--no-traits", "--no-types", *CM_FLAGS] + (["--cfg-test"] if cfg_test else [])
    work = []
    for p in pkgs:
        crate_id = "crate:" + p["name"]
        for t in sorted(p["targets"], key=lambda t: (t["kind"][0], t["name"])):
            kinds = set(t["kind"])
            root_file = os.path.relpath(t["src_path"], ws_root)
            if kinds & LIB_KINDS:
                work.append((p, t, base + ["--lib", "-p", p["name"]], t["name"].replace("-", "_"), crate_id, crate_id, root_file))
            elif "bin" in kinds:
                qual = "%s::bin::%s" % (p["name"].replace("-", "_"), t["name"])
                work.append((p, t, base + ["--bin", t["name"], "-p", p["name"]], qual, "bin:" + qual, crate_id, root_file))
    with ThreadPoolExecutor(max_workers=max(1, jobs)) as pool:
        results = list(pool.map(lambda w: runner(w[2], root), work))
    for (p, t, cmd, qual, parent_id, crate_id, root_file), (rc, out, err) in zip(work, results):
        if rc != 0:
            failed.append("%s %s" % (p["name"], t["name"]))
            print("vr_extract: %s failed: %s" % (" ".join(cmd[2:]), err.strip()[:200]), file=sys.stderr)
            continue
        if parent_id != crate_id:
            ev = _src(root_file, 1, "binary target")
            nodes.append(_node(parent_id, t["name"], "bin", ev, resolution="static"))
            edges.append({"id": "edge:contains:%s:%s" % (crate_id, parent_id), "source": crate_id, "target": parent_id,
                          "kind": "contains", "status": "context", "resolution": "static", "evidence": [ev]})
        n, e = modules_from_structure(parse_structure(out), parent_id, qual, root_file, files)
        nodes += n
        edges += e
    meta: dict = {"status": "ran"}
    if failed:
        meta["reason"] = "cargo modules failed for: " + ", ".join(failed)
    return nodes, edges, meta, [{"name": "cargo-modules", "version": ver}]


def _modules_cached(pkgs, root, ws_root, files, runner, cfg_test, jobs, cache, parts):
    """Layer 2 per crate through `cache`: only the crates with no stored result run cargo-modules."""
    nodes, edges, tools = [], [], []
    reused, todo = [], []
    keyed = {}
    for p in pkgs:
        k = cache.key(p)
        keyed[p["name"]] = k
        hit = cache.load(p["name"], k) if k else None
        if hit is not None:
            reused.append(p["name"])
            nodes += hit[0]
            edges += hit[1]
            if parts is not None:
                parts[p["name"]] = (hit[0], hit[1])
        else:
            todo.append(p)
    meta: dict = {"status": "ran"}
    failed_names, ran = [], []
    if todo:
        with ThreadPoolExecutor(max_workers=max(1, jobs)) as pool:
            results = list(pool.map(lambda p: layer_modules([p], root, ws_root, files, runner, cfg_test, 1), todo))
        for p, (n, e, lm, tl) in zip(todo, results):
            if lm.get("status") == "skipped":
                return [], [], lm, []
            if lm.get("reason"):  # some target failed: keep what came back, store nothing
                failed_names.append(p["name"])
                meta["reason"] = "cargo modules failed for: " + ", ".join(failed_names)
            elif keyed[p["name"]]:
                cache.store(p["name"], keyed[p["name"]], n, e)
            ran.append(p["name"])
            nodes += n
            edges += e
            tools = tl
            if parts is not None:
                parts[p["name"]] = (n, e)
    if not tools:
        ver, _ = cargo_modules_version(runner, root)
        tools = [{"name": "cargo-modules", "version": ver}]
    meta["reused"] = sorted(reused)
    meta["recomputed"] = sorted(ran)
    return nodes, edges, meta, tools


# ------------------------------------------------------------------ layer 4

def _qual_for(rel: str, pkgs, ws_root: str) -> str:
    """Module path of a source file: crates/a/src/b/c.rs -> a::b::c (inline modules are not seen)."""
    for p in sorted(pkgs, key=lambda p: -len(os.path.dirname(os.path.relpath(p["manifest_path"], ws_root)))):
        d = os.path.dirname(os.path.relpath(p["manifest_path"], ws_root))
        pre = d + "/" if d else ""
        if rel.startswith(pre + "src/"):
            parts = rel[len(pre) + 4:][:-3].split("/")
            if parts[-1] in ("lib", "main") and len(parts) == 1:
                parts = []
            elif parts[-1] == "mod":
                parts = parts[:-1]
            return "::".join([p["name"].replace("-", "_")] + parts)
    return rel[:-3].replace("/", "::")


def _is_dispatch(abs_path: str, line: int) -> bool:
    """A call resolved to a trait method is a dispatch: the implementation is chosen at run time.

    rust-analyzer answers a trait-object call with the trait's method (D0 section 4).
    Heuristic: the callee's `fn` sits inside a `trait` block (nearest less-indented
    line starts a trait) or has no body (`;` before `{`).
    """
    try:
        with open(abs_path, encoding="utf-8", errors="replace") as fh:
            lines = fh.read().split("\n")
    except OSError:
        return False
    if not 1 <= line <= len(lines):
        return False
    sig = ""
    for raw in lines[line - 1:line + 6]:
        sig += raw
        if "{" in raw or ";" in raw:
            break
    if re.search(r"^[^{;]*;", sig):
        return True
    indent = len(lines[line - 1]) - len(lines[line - 1].lstrip())
    for j in range(line - 2, -1, -1):
        raw = lines[j]
        if not raw.strip() or raw.strip().startswith(("//", "#[", "///")):
            continue
        ind = len(raw) - len(raw.lstrip())
        if ind < indent:
            return bool(re.match(r"^\s*(pub(\([^)]*\))?\s+)?(unsafe\s+)?trait\b", raw))
    return False


def find_functions(specs, pkgs, ws_root: str, files: _Files):
    """Locate `name` or `relpath:name` specs: [(spec, rel, line, col)] sorted; missing specs come back separately."""
    hits, missing = [], []
    srcs = []
    for p in pkgs:
        d = os.path.dirname(p["manifest_path"])
        for dp, dns, fns in os.walk(os.path.join(d, "src")):
            dns.sort()
            srcs += [os.path.relpath(os.path.join(dp, f), ws_root) for f in sorted(fns) if f.endswith(".rs")]
    for spec in specs:
        only, _, name = spec.rpartition(":")
        if not only:
            name = spec
        pat = re.compile(r"\bfn\s+(%s)\b" % re.escape(name))
        found = False
        for rel in sorted(set(srcs)):
            if only and rel != only:
                continue
            for i, raw in enumerate(files.lines(rel) or [], 1):
                m = pat.search(raw)
                if m:
                    hits.append((spec, rel, i, m.start(1) + 1))
                    found = True
        if not found:
            missing.append(spec)
    return hits, missing


def layer_calls(specs, pkgs, root: str, ws_root: str, files: _Files, timeout: float, ra_factory=None):
    """Returns (nodes, edges, layer_meta, tools). Failure of the language server skips the layer."""
    hits, missing = find_functions(specs, pkgs, ws_root, files)
    if not hits:
        return [], [], {"status": "skipped", "reason": "function(s) not found: " + ", ".join(missing)}, []
    factory = ra_factory or (lambda r, t: RaClient(r, timeout=t))
    nodes, edges, ids = {}, {}, {}
    try:
        with factory(ws_root, timeout) as ra:
            version = ra.version
            for _, rel, line, col in hits:
                res = ra.call_hierarchy(rel, line, col)
                focus = _fn_node(res["item"], pkgs, ws_root, nodes, ids)
                for side in ("outgoing", "incoming"):
                    for c in res[side]:
                        it = c["item"]
                        if not _in_workspace(it["path"], ws_root):
                            continue
                        other = _fn_node(it, pkgs, ws_root, nodes, ids)
                        a, b = (focus, other) if side == "outgoing" else (other, focus)
                        site = res["item"] if side == "outgoing" else it
                        callee = it if side == "outgoing" else res["item"]
                        eid = "edge:calls:%s:%s" % (a, b)
                        res_kind = "dispatch" if _is_dispatch(callee["path"], callee["line"]) else "static"
                        rel_site = os.path.relpath(site["path"], ws_root)
                        edges[eid] = {"id": eid, "source": a, "target": b, "kind": "calls", "status": "context",
                                      "resolution": res_kind,
                                      "evidence": [_src(rel_site, (c["ranges"] or [site["line"]])[0], "call site")]}
    except RaError as exc:
        print("vr_extract: call layer skipped: %s" % exc, file=sys.stderr)
        return [], [], {"status": "skipped", "reason": str(exc)}, []
    meta = {"status": "ran"}
    if missing:
        meta["reason"] = "function(s) not found: " + ", ".join(missing)
    return ([nodes[k] for k in sorted(nodes)], [edges[k] for k in sorted(edges)], meta,
            [{"name": "rust-analyzer", "version": version or "unknown"}])


def _in_workspace(path: str, ws_root: str) -> bool:
    rel = os.path.relpath(path, ws_root)
    return not rel.startswith("..") and not rel.startswith("target/") and "/target/" not in rel


def _fn_node(item: dict, pkgs, ws_root: str, nodes: dict, ids: dict) -> str:
    """Node for a function; one id per (file, line), `_dupN` when a qualified name repeats."""
    rel = os.path.relpath(item["path"], ws_root)
    key = (rel, item["line"])
    if key in ids:
        return ids[key]
    base = "fn:%s::%s" % (_qual_for(rel, pkgs, ws_root), item["name"])
    nid, n = base, 1
    while nid in nodes:
        n += 1
        nid = "%s_dup%d" % (base, n)
    ids[key] = nid
    nodes[nid] = _node(nid, item["name"], "fn", _src(rel, item["line"], "definition"), resolution="static")
    return nid


# -------------------------------------------------------------------- rust

def extract_rust(root: str, layers=(1, 2), calls=(), timeout: float = 120.0, cfg_test: bool = False, jobs: int = 4,
                 runner=None, ra_factory=None, timings: dict | None = None, crates=None, module_cache=None,
                 parts: dict | None = None) -> dict:
    """`crates`: names of the workspace crates layer 2 runs for (None: all). The other crates keep
    their crate node; meta.layers.modules records `limited_to` and `skipped_crates`.

    `module_cache` (whole-repo map, vr_refresh.py) makes layer 2 incremental per crate: an object with
    `key(pkg) -> str`, `load(name, key) -> (nodes, edges) | None` and `store(name, key, nodes, edges)`.
    A crate with a stored result is not run again (meta.layers.modules lists `reused` and `recomputed`);
    a crate whose run failed is never stored. `parts`, when given, is filled with
    {crate name: (module nodes, module edges)} for every crate of layer 2, reused or not."""
    runner = runner or default_runner
    timings = timings if timings is not None else {}
    root = os.path.abspath(root)
    t0 = time.time()
    rc, out, err = runner(["cargo", "metadata", "--format-version", "1", "--no-deps"], root)
    if rc != 0:
        raise ExtractError("cargo metadata failed (rc %s): %s" % (rc, err.strip()[:300]))
    try:
        meta_json = json.loads(out)
    except ValueError as exc:
        raise ExtractError("cargo metadata did not print JSON: %s" % exc)
    ws_root = meta_json.get("workspace_root", root)
    files = _Files(ws_root)
    pkgs, nodes, edges = layer_crates(meta_json, files, ws_root)
    timings["crates"] = time.time() - t0
    layers_meta = {"crates": {"status": "ran"}}
    tools = []
    rc, vout, _ = runner(["cargo", "--version"], root)
    tools.append({"name": "cargo", "version": (vout.split() + ["unknown", "unknown"])[1] if rc == 0 else "unknown"})

    if 2 in layers:
        t = time.time()
        mod_pkgs = pkgs
        if crates is not None:
            want = set(crates)
            mod_pkgs = [p for p in pkgs if p["name"] in want]
        if module_cache is None:
            n, e, lm, tl = layer_modules(mod_pkgs, root, ws_root, files, runner, cfg_test, jobs)
        else:
            n, e, lm, tl = _modules_cached(mod_pkgs, root, ws_root, files, runner, cfg_test, jobs, module_cache, parts)
        if crates is not None:
            lm["limited_to"] = [p["name"] for p in mod_pkgs]
            lm["skipped_crates"] = [p["name"] for p in pkgs if p not in mod_pkgs]
        nodes += n
        edges += e
        layers_meta["modules"] = lm
        tools += tl
        timings["modules"] = time.time() - t
    else:
        layers_meta["modules"] = {"status": "skipped", "reason": "not requested"}
    if calls:
        t = time.time()
        n, e, lm, tl = layer_calls(list(calls), pkgs, root, ws_root, files, timeout, ra_factory)
        have = {x["id"] for x in nodes}
        nodes += [x for x in n if x["id"] not in have]
        edges += e
        layers_meta["calls"] = lm
        tools += tl
        timings["calls"] = time.time() - t
    else:
        layers_meta["calls"] = {"status": "skipped", "reason": "not requested"}
    tools.append({"name": "vr_extract", "version": VERSION})

    rc, commit, _ = runner(["git", "rev-parse", "--short=8", "HEAD"], ws_root)
    commit = commit.strip() if rc == 0 and commit.strip() else "unknown"
    nodes.sort(key=lambda x: x["id"])
    edges.sort(key=lambda x: x["id"])
    return {
        "schema_version": "1.0.0", "view": "full",
        "title": "Before graph of %s" % os.path.basename(ws_root),
        "meta": {"repo": os.path.basename(ws_root), "commit": commit, "generated_by": "extractor",
                 "tools": sorted(tools, key=lambda x: x["name"]), "layers": layers_meta},
        "nodes": nodes, "edges": edges,
    }


# The language-neutral seam (OQ-10): one function per language, same signature.
EXTRACTORS = {"rust": extract_rust}


def extract(lang: str, root: str, **opts) -> dict:
    """Extract the before graph of `root` for `lang`. Only "rust" exists."""
    if lang not in EXTRACTORS:
        raise ExtractError("no extractor for %r (known: %s)" % (lang, ", ".join(sorted(EXTRACTORS))))
    return EXTRACTORS[lang](root, **opts)


def dumps(graph: dict) -> str:
    return json.dumps(graph, indent=1, ensure_ascii=False) + "\n"


# ----------------------------------------------------------------- selftest

def _fake_workspace(d: str):
    """A tiny workspace on disk plus the cargo metadata JSON it would give."""
    def w(rel, text):
        p = os.path.join(d, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w") as fh:
            fh.write(text)
    w("Cargo.toml", "[workspace]\nmembers = [\"crates/*\"]\n")
    w("crates/app/Cargo.toml", "[package]\nname = \"app\"\n\n[dependencies]\nserde = \"1\"\ncore-lib.workspace = true\nworkspace-hack = { path = \"../workspace-hack\" }\n\n[dev-dependencies]\ntest-kit = { path = \"../test-kit\" }\n")
    w("crates/core-lib/Cargo.toml", "[package]\nname = \"core-lib\"\n\n[dependencies]\nsys = { path = \"../sys\" }\n")
    w("crates/sys/Cargo.toml", "[package]\nname = \"sys\"\n")
    w("crates/test-kit/Cargo.toml", "[package]\nname = \"test-kit\"\n")
    w("crates/workspace-hack/Cargo.toml", "[package]\nname = \"workspace-hack\"\n")
    w("crates/app/src/lib.rs", "pub mod net;\npub mod util {\n    pub mod inner {}\n}\n#[cfg(test)]\nmod tests;\n")
    w("crates/app/src/net.rs", "pub mod http;\n")
    w("crates/app/src/net/http.rs", "")
    w("crates/app/src/bin/gateway.rs", "mod routes;\nfn main() {}\n")
    w("crates/app/src/bin/gateway/routes.rs", "")
    for c in ("core-lib", "sys", "test-kit", "workspace-hack"):
        w("crates/%s/src/lib.rs" % c, "pub fn f() {}\n")
    w("crates/sys/src/shapes.rs", "")

    def pkg(name, deps, bins=()):
        base = os.path.join(d, "crates", name)
        t = [{"name": name.replace("-", "_"), "kind": ["lib"], "src_path": base + "/src/lib.rs"}]
        t += [{"name": b, "kind": ["bin"], "src_path": base + "/src/bin/%s.rs" % b} for b in bins]
        t.append({"name": "suite", "kind": ["test"], "src_path": base + "/tests/suite.rs"})
        return {"name": name, "id": "path+file://%s#0.1.0" % base, "manifest_path": base + "/Cargo.toml",
                "targets": t, "dependencies": deps}

    def dep(name, kind=None, source=None, rename=None):
        return {"name": name, "kind": kind, "source": source, "rename": rename}
    pk = [pkg("app", [dep("serde", source="registry+crates.io"), dep("core-lib"), dep("workspace-hack"), dep("test-kit", kind="dev"),
                      dep("app")], bins=["gateway"]),
          pkg("core-lib", [dep("sys")]), pkg("sys", []), pkg("test-kit", []), pkg("workspace-hack", [])]
    return {"packages": pk, "workspace_members": [p["id"] for p in pk], "workspace_root": d}


FAKE_TREES = {
    ("--lib", "app"): "crate app\n├── mod net: pub\n│   └── mod http: pub\n├── mod util: pub\n│   └── mod inner: pub\n│   └── struct S: pub\n",
    ("--bin", "gateway"): "crate gateway\n└── mod routes: pub(self)\n",
}


def make_runner(meta, log, cm_version: str | None = "0.27.0", trees=FAKE_TREES, fail_cm=False):
    def runner(cmd, cwd, timeout=None):
        log.append(list(cmd))
        if cmd[:2] == ["cargo", "metadata"]:
            return 0, json.dumps(meta), ""
        if cmd == ["cargo", "--version"]:
            return 0, "cargo 1.97.1 (abc 2026-06-30)\n", ""
        if cmd[:3] == ["cargo", "modules", "--version"]:
            return (0, "cargo-modules %s\n" % cm_version, "") if cm_version else (127, "", "no such command: modules")
        if cmd[:3] == ["cargo", "modules", "structure"]:
            if fail_cm:
                return 101, "", "error: boom"
            tgt = "--bin" if "--bin" in cmd else "--lib"
            key = (tgt, cmd[cmd.index("--bin") + 1] if tgt == "--bin" else cmd[cmd.index("-p") + 1])
            return 0, trees.get(key, "crate x\n"), ""
        if cmd[:2] == ["git", "rev-parse"]:
            return 0, "abc12345\n", ""
        return 127, "", "unexpected " + " ".join(cmd)
    return runner


def _selftest() -> int:
    fails: list = []

    def check(name, cond, detail=""):
        if not cond:
            fails.append("%s %s" % (name, detail))

    me = os.path.dirname(os.path.abspath(__file__))
    with tempfile.TemporaryDirectory() as d:
        d = os.path.realpath(d)
        meta = _fake_workspace(d)
        log: list = []
        g1 = extract("rust", d, runner=make_runner(meta, log))
        issues = vr_graph.validate(g1, root=d)
        # VC-04 (with the fake workspace): every non-inferred anchor resolves; output valid.
        check("vc04:valid-with-root", issues == [], json.dumps(issues)[:400])
        ids = {n["id"] for n in g1["nodes"]}
        # S1 / VC-01: one crate node per workspace member.
        crates = sorted(n["id"][6:] for n in g1["nodes"] if n["kind"] == "crate")
        check("s1+vc01:crate-nodes", crates == sorted(p["name"] for p in meta["packages"]), str(crates))
        # S1 / VC-02: one edge per normal workspace dependency; dev, self, registry and workspace-hack dropped.
        dep_edges = sorted((e["source"], e["target"]) for e in g1["edges"] if e["kind"] == "depends")
        check("s1+vc02:crate-edges", dep_edges == [("crate:app", "crate:core-lib"), ("crate:core-lib", "crate:sys")], str(dep_edges))
        e = [x for x in g1["edges"] if x["kind"] == "depends"][0]
        check("s1:static", all(x["resolution"] == "static" for x in g1["edges"] if x["kind"] == "depends"))
        # S1: evidence points at the Cargo.toml line of the dependency.
        ev = e["evidence"][0]
        lines = open(os.path.join(d, ev["path"])).read().split("\n")
        check("s1:evidence-cargo-toml-line", ev["path"].endswith("Cargo.toml") and lines[ev["line"] - 1].startswith("core-lib"), str(ev))
        # Modules, nested, inline, bin (D0 recall fix).
        for want in ("module:app::net", "module:app::net::http", "module:app::util", "module:app::util::inner",
                     "bin:app::bin::gateway", "module:app::bin::gateway::routes"):
            check("modules:" + want, want in ids)
        check("modules:no-non-modules", not any(i.endswith("::S") for i in ids))
        http = [n for n in g1["nodes"] if n["id"] == "module:app::net::http"][0]
        check("modules:decl-evidence", http["evidence"][0]["path"] == "crates/app/src/net.rs" and http["evidence"][0]["line"] == 1)
        inner = [n for n in g1["nodes"] if n["id"] == "module:app::util::inner"][0]
        check("modules:inline-evidence", inner["evidence"][0]["path"] == "crates/app/src/lib.rs" and inner["evidence"][0]["line"] == 3, str(inner["evidence"]))
        check("modules:bin-cmd", any("--bin" in c and "gateway" in c for c in log))
        # VC-07 / S2: no --cfg-test by default; passed only on request.
        check("vc07:no-cfg-test-default", not any("--cfg-test" in c for c in log))
        log2: list = []
        extract("rust", d, runner=make_runner(meta, log2), cfg_test=True)
        check("vc07:cfg-test-on-request", any("--cfg-test" in c for c in log2 if "structure" in c))
        check("vc06:module-layer-reads-every-file", all(all(f in c for f in CM_FLAGS) for c in log2 if c[:3] == ["cargo", "modules", "structure"])
              and any(c[:3] == ["cargo", "modules", "structure"] for c in log2) and CM_FLAGS == ("--all-features", "--target", "x86_64-unknown-linux-gnu"))
        # VC-09: every tool and the commit are in meta; pinned version.
        tools = {t["name"]: t["version"] for t in g1["meta"]["tools"]}
        check("vc09:tools", tools.get("cargo") == "1.97.1" and tools.get("cargo-modules") == "0.27.0" and "vr_extract" in tools, str(tools))
        check("vc09:commit", g1["meta"]["commit"] == "abc12345")
        # VC-05 / S8: two runs, identical bytes.
        g1b = extract("rust", d, runner=make_runner(meta, []))
        check("vc05+s8:deterministic", dumps(g1) == dumps(g1b))
        # S7: tool missing or wrong version -> layer skipped with a reason, crate layer still there.
        for ver, label in ((None, "missing"), ("0.28.0", "wrong-version")):
            g = extract("rust", d, runner=make_runner(meta, [], cm_version=ver))
            lm = g["meta"]["layers"]["modules"]
            check("s7:%s-skipped" % label, lm["status"] == "skipped" and "cargo-modules" in lm["reason"] and "0.27.0" in lm["reason"], str(lm))
            check("s7:%s-crates-kept" % label, g["meta"]["layers"]["crates"]["status"] == "ran" and len(g["nodes"]) == 5)
            check("s7:%s-valid" % label, vr_graph.validate(g, root=d) == [])
        # Module layer failure for one crate is recorded, not fatal.
        g = extract("rust", d, runner=make_runner(meta, [], fail_cm=True))
        check("modules:failure-recorded", "failed for" in g["meta"]["layers"]["modules"].get("reason", ""))
        # Layers selection.
        g = extract("rust", d, layers=(1,), runner=make_runner(meta, []))
        check("layers:1-only", g["meta"]["layers"]["modules"]["reason"] == "not requested" and all(n["kind"] == "crate" for n in g["nodes"]))
        # --crates: layer 2 only for the listed crates; every crate node stays.
        logc: list = []
        gc = extract("rust", d, crates=["app"], runner=make_runner(meta, logc))
        mods = [c for c in logc if "structure" in c]
        check("crates:only-listed-run", mods and all("app" in c for c in mods), str(mods))
        check("crates:all-crate-nodes-kept", sorted(n["id"][6:] for n in gc["nodes"] if n["kind"] == "crate") == crates, str(crates))
        lmc = gc["meta"]["layers"]["modules"]
        check("crates:meta-limited", lmc["limited_to"] == ["app"] and "sys" in lmc["skipped_crates"] and "app" not in lmc["skipped_crates"], str(lmc))
        check("crates:graph-valid", vr_graph.validate(gc, check_budget=False) == [], str(vr_graph.validate(gc, check_budget=False)))
        check("crates:no-foreign-modules", not any(n["id"].startswith("module:sys") for n in gc["nodes"]))
        check("crates:all-is-default", "limited_to" not in g1["meta"]["layers"]["modules"])
        # Over-budget L2 is still written in full: 'full' view has no budget.
        check("budget:full-writes-all", vr_graph.budget(g1)["over"] is False and len(g1["nodes"]) > 0)
        # Dispatch seam.
        try:
            extract("go", d)
            check("seam:unknown-lang", False)
        except ExtractError as exc:
            check("seam:unknown-lang", "rust" in str(exc))
        # Layer 4: fake rust-analyzer. VC-10 / S3: trait-method callee is a dispatch.
        # the fake server answers for <root>/crates/a/src/lib.rs: point the layer at that layout.
        os.makedirs(os.path.join(d, "crates/a/src"), exist_ok=True)
        with open(os.path.join(d, "crates/a/src/lib.rs"), "w") as fh:
            ls = [""] * 30
            ls[2] = "fn inner() {}"
            ls[10] = "trait Shape {"
            ls[11] = "    fn area(&self) -> f64;"
            ls[12] = "}"
            ls[15] = "fn run_loop() {}"
            ls[7] = "fn target_fn() {}"
            fh.write("\n".join(ls))
        meta_a = json.loads(json.dumps(meta))
        base = os.path.join(d, "crates", "a")
        meta_a["packages"].append({"name": "a", "id": "path+file://%s#0.1.0" % base, "manifest_path": base + "/Cargo.toml",
                                   "targets": [{"name": "a", "kind": ["lib"], "src_path": base + "/src/lib.rs"}], "dependencies": []})
        meta_a["workspace_members"].append(meta_a["packages"][-1]["id"])
        with open(base + "/Cargo.toml", "w") as fh:
            fh.write("[package]\nname = \"a\"\n")
        fake = lambda r, t: RaClient(r, cmd=[sys.executable, os.path.join(me, "vr_ra.py"), "--fake-server"], timeout=t)
        g4 = extract("rust", d, layers=(1,), calls=["target_fn"], runner=make_runner(meta_a, []), ra_factory=fake, timeout=30)
        check("calls:valid", vr_graph.validate(g4, root=d) == [], json.dumps(vr_graph.validate(g4, root=d))[:400])
        res = {(e["source"], e["target"]): e["resolution"] for e in g4["edges"] if e["kind"] == "calls"}
        check("s3+vc10:dispatch", res.get(("fn:a::target_fn", "fn:a::area")) == "dispatch", str(res))
        check("calls:static-callee", res.get(("fn:a::target_fn", "fn:a::inner")) == "static", str(res))
        check("calls:caller-edge", ("fn:a::run_loop", "fn:a::target_fn") in res, str(res))
        check("calls:std-filtered", not any("push" in n["id"] for n in g4["nodes"]))
        check("calls:tool-pinned", "rust-analyzer" in {t["name"] for t in g4["meta"]["tools"]} and g4["meta"]["layers"]["calls"]["status"] == "ran")
        # Timeout: the layer is skipped with a reason, the rest is kept.
        hang = lambda r, t: RaClient(r, cmd=[sys.executable, os.path.join(me, "vr_ra.py"), "--fake-server", "hang"], timeout=t)
        t0 = time.time()
        g5 = extract("rust", d, layers=(1,), calls=["target_fn"], runner=make_runner(meta_a, []), ra_factory=hang, timeout=2)
        check("calls:timeout-skipped", g5["meta"]["layers"]["calls"]["status"] == "skipped" and "timed out" in g5["meta"]["layers"]["calls"]["reason"] and time.time() - t0 < 10)
        g6 = extract("rust", d, layers=(1,), calls=["no_such_fn"], runner=make_runner(meta_a, []), ra_factory=fake)
        check("calls:not-found", g6["meta"]["layers"]["calls"]["status"] == "skipped")
        # cargo metadata failure is fatal and says so.
        try:
            extract("rust", d, runner=lambda c, cwd, timeout=None: (101, "", "boom"))
            check("metadata-failure", False)
        except ExtractError as exc:
            check("metadata-failure", "cargo metadata" in str(exc))
    # The structure parser on the real shape of the output (D0 section 3).
    got = parse_structure("crate harness\n├── mod a: pub\n│   ├── struct X: pub\n│   └── mod b: pub(crate)\n└── mod c: pub\n    └── mod d: pub\n")
    ansi = parse_structure("\x1b[2m\x1b[0m\x1b[38;2;84;142;200mcrate\x1b[0m x\n\x1b[2m├── \x1b[0mmod a: pub\n")
    check("parser:ansi", ansi == [(1, "a")], str(ansi))
    check("parser", got == [(1, "a"), (2, "b"), (1, "c"), (2, "d")], str(got))
    # The CLI writes a file and refuses bad input.
    r = subprocess.run([sys.executable, os.path.join(me, "vr_extract.py"), "rust", "/nonexistent-root-xyz"], capture_output=True, text=True)
    check("cli:bad-root-exit-1", r.returncode == 1, r.stderr[:200])
    long_name = "very_long_module_name_" * 6
    nn = _node("module:x", long_name, "module", _src("a.rs", 1, "definition"))
    check("fu02:shorten", len(nn["label"]) == vr_graph.NODE_LABEL_MAX and nn["label"].endswith("\u2026")
          and long_name in nn["evidence"][0]["note"] and "definition" in nn["evidence"][0]["note"], str(nn))
    check("fu02:short-untouched", _node("m", "abc", "module", _src("a.rs", 1))["label"] == "abc")
    rr = subprocess.run([sys.executable, os.path.join(me, "vr_ra.py"), "--selftest"], capture_output=True, text=True)
    check("vr_ra:selftest", rr.returncode == 0, rr.stderr[:300])
    if fails:
        print("vr_extract selftest FAILED:\n  " + "\n  ".join(fails), file=sys.stderr)
        return 1
    print("vr_extract selftest ok")
    return 0


# --------------------------------------------------------------------- live

def _live(root: str) -> int:
    """Real tools on a real workspace: VC-01, VC-02, VC-04, VC-05, VC-08, VC-09 and a call query."""
    root = os.path.abspath(root)
    fails: list = []
    tm1: dict = {}
    t = time.time()
    g = extract("rust", root, layers=(1, 2), timings=tm1)
    wall = time.time() - t
    out = dumps(g)
    g2 = extract("rust", root, layers=(1, 2))
    same = out == dumps(g2)
    _, mj, _ = default_runner(["cargo", "metadata", "--format-version", "1"], root)
    full = json.loads(mj)
    wm = set(full["workspace_members"])
    names = {p["name"] for p in full["packages"] if p["id"] in wm}
    idn = {p["id"]: p["name"] for p in full["packages"] if p["id"] in wm}
    crate_nodes = {n["id"][6:] for n in g["nodes"] if n["kind"] == "crate"}
    if crate_nodes != names:
        fails.append("VC-01: crate nodes differ: %s" % sorted(crate_nodes ^ names))
    want = set()
    for nd in full["resolve"]["nodes"]:
        if nd["id"] not in wm:
            continue
        for dep in nd["deps"]:
            if dep["pkg"] in wm and any(k.get("kind") is None for k in dep["dep_kinds"]):
                a, b = idn[nd["id"]], idn[dep["pkg"]]
                if a != b and b not in HIDDEN_DEP_TARGETS:
                    want.add(("crate:" + a, "crate:" + b))
    have = {(e["source"], e["target"]) for e in g["edges"] if e["kind"] == "depends"}
    if have != want:
        fails.append("VC-02: edges differ: extra %s missing %s" % (sorted(have - want), sorted(want - have)))
    issues = vr_graph.validate(g, root=root)
    if issues:
        fails.append("VC-04: %d validator issue(s): %s" % (len(issues), json.dumps(issues[:3])))
    if not same:
        fails.append("VC-05: two runs differ")
    if not g["meta"]["commit"] or g["meta"]["commit"] == "unknown":
        fails.append("VC-09: no commit")
    n_mod = sum(1 for n in g["nodes"] if n["kind"] == "module")
    n_bin = sum(1 for n in g["nodes"] if n["kind"] == "bin")
    print(json.dumps({"crates": len(crate_nodes), "crate_edges": len(have), "modules": n_mod, "bins": n_bin,
                      "layers": g["meta"]["layers"], "tools": g["meta"]["tools"], "commit": g["meta"]["commit"],
                      "seconds": {"crates": round(tm1.get("crates", 0), 1), "modules": round(tm1.get("modules", 0), 1), "total": round(wall, 1)},
                      "deterministic": same, "vc08_limit_s": VC08_LIMIT_S, "vc08": "ok" if wall <= VC08_LIMIT_S else "OVER"}, indent=1))
    if wall > VC08_LIMIT_S:
        fails.append("VC-08: %.1f s over the %d s limit" % (wall, VC08_LIMIT_S))
    for f in fails:
        print("FAIL " + f, file=sys.stderr)
    return 1 if fails else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--live", metavar="REPO_ROOT", help="opt-in: run the real tools on REPO_ROOT and check VC-01/02/04/05/08/09")
    sub = ap.add_subparsers(dest="lang")
    r = sub.add_parser("rust", help="extract the before graph of a Cargo workspace")
    r.add_argument("root")
    r.add_argument("--layers", default="1,2", help="comma list of 1 (crates), 2 (modules); default 1,2")
    r.add_argument("--calls", default="", help="comma list of functions (name or relpath:name) for the call hierarchy layer")
    r.add_argument("--timeout", type=float, default=120.0, help="seconds for the whole rust-analyzer session (default 120)")
    r.add_argument("--crates", default=None, help="comma list of crate names: layer 2 runs only for these (layer 1 stays the whole workspace)")
    r.add_argument("--jobs", type=int, default=4, help="parallel cargo-modules processes (default 4)")
    r.add_argument("--cfg-test", action="store_true", help="include #[cfg(test)] modules (default: excluded)")
    r.add_argument("--timings", action="store_true", help="print per-layer seconds to stderr")
    r.add_argument("-o", "--output", help="write here instead of stdout")
    args = ap.parse_args(argv)
    if args.selftest:
        return _selftest()
    if args.live:
        return _live(args.live)
    if args.lang != "rust":
        ap.print_usage(sys.stderr)
        return 2
    try:
        layers = tuple(sorted({int(x) for x in args.layers.split(",") if x.strip()}))
    except ValueError:
        print("--layers takes numbers, e.g. 1,2", file=sys.stderr)
        return 2
    if not set(layers) <= {1, 2}:
        print("--layers: only 1 and 2 (layer 4 is --calls)", file=sys.stderr)
        return 2
    timings: dict = {}
    try:
        g = extract("rust", args.root, layers=layers, calls=[c for c in args.calls.split(",") if c.strip()],
                    timeout=args.timeout, cfg_test=args.cfg_test, jobs=args.jobs, timings=timings,
                    crates=None if args.crates is None else [c.strip() for c in args.crates.split(",") if c.strip()])
    except ExtractError as exc:
        print("vr_extract: %s" % exc, file=sys.stderr)
        return 1
    text = dumps(g)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as fh:
            fh.write(text)
    else:
        sys.stdout.write(text)
    if args.timings:
        print("vr_extract: seconds " + json.dumps({k: round(v, 1) for k, v in timings.items()}), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
