#!/usr/bin/env python3
"""Visual review: the whole-repo map of a Rust workspace, with a cached, incremental refresh.

    vr_refresh.py REPO [--since REV] [--dir docs/architecture] [--cache DIR] [--full]
                       [--no-calls] [--calls-limit SEC] [--file-limit SEC] [--index-limit SEC]
                       [--no-product-map] [--jobs N] [--json] [--no-metrics]
    vr_refresh.py --selftest
    (also reachable as `vr_map.py refresh REPO ...`)

What it writes into DIR (default docs/architecture, OQ-05), every file a graph that `vr_graph.py validate`
accepts (view `full`, so no L0/L1/L2 budget), and one manifest:

    crate-map.json          the crates and their dependencies (layer 1)
    modules-<crate>.json    one per crate: its binaries and module tree (layer 2)
    calls-<crate>.json      one per crate: which modules call which modules (layer 4, aggregated)
    crate-calls.json        which crates call which crates (layer 4, aggregated)
    product-map.json        the whole-repo feature map, view "map": areas, "builds on" edges, build stages (vr_map.py), grown by `build --touched` from the previous
                            extraction commit instead of re-read whole
    map-manifest.json       the extraction commit, every view with its node and edge counts, and what the
                            run reused and recomputed

A view whose content did not change is not rewritten, so a refresh commit holds only what moved; the
manifest carries the commit the whole map was last verified at (`extraction_commit`), the views' `meta.commit`
the commit each was last extracted at.

Cache (never committed): `<git common dir>/asd-visual-review/cache/`, shared by every worktree of the
repository. `.discovery/` is tracked in some repositories (config-harness), so a cache there would be swept
into commits; the git common dir is never tracked. `--cache DIR` moves it. Keys are content hashes:
  modules   one entry per crate, keyed by the sha256 of the git blob ids of the crate's Cargo.toml, build.rs and
            src/ files (a crate's src tree hash), plus the cargo-modules version and the cfg flags. Equal key:
            the crate's modules are not extracted again. Any change in the crate: that crate only.
  calls     one entry per source file, keyed by the file's git blob id (a working-tree edit is hashed too):
            the file's functions and their OUTGOING calls. Incoming calls are never stored: they are the inverse
            of every other file's outgoing calls. A file whose blob id is unchanged keeps its entry. Beyond that,
            a file is recomputed when a function it calls, in a file that changed, is no longer at the cached
            name and line (the callee moved or went away): the "dependents" of a change. Left out on purpose: a
            new impl that changes what a trait call resolves to in an unchanged file (`--full` recomputes).
  crates    layer 1 (cargo metadata, about a second) always runs.
The first run computes everything. Every file is stored as soon as it is done, so a run that hits
`--calls-limit` (default 8 h; the dossier runs keep the 120 s VC-08 limit of vr_extract.py) stops cleanly,
reports `partial` and the next run continues where it stopped.

Call layer settings: rust-analyzer is started with build scripts, proc macros, check-on-save, cache priming
and diagnostics OFF (see vr_ra.FAST_OPTIONS). Functions are listed by one documentSymbol request per file and
each gets prepareCallHierarchy plus outgoingCalls: no polling per function. Calls made only through
macro-expanded code are therefore not seen. If rust-analyzer cannot start, the call layer is `skipped`
with the reason and crates and modules are still written.

Exit codes: 0 fine (a partial or skipped call layer is still 0); 1 a view failed validation or extraction
failed; 2 bad arguments.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import vr_extract  # noqa: E402
import vr_graph  # noqa: E402
import vr_map  # noqa: E402
import vr_metrics  # noqa: E402
from vr_ra import FAST_OPTIONS, RaClient, RaError  # noqa: E402

VERSION = "1.0.0"
DEFAULT_DIR = os.path.join("docs", "architecture")
MANIFEST = "map-manifest.json"
CALLS_LIMIT = 8 * 3600.0
FILE_LIMIT = 300.0
INDEX_LIMIT = 1800.0
CALLS_SALT = "calls-1"


class RefreshError(Exception):
    pass


def _git(cwd: str, *args: str) -> str | None:
    try:
        p = subprocess.run(["git"] + list(args), cwd=cwd, capture_output=True, text=True)
    except FileNotFoundError:
        return None
    return p.stdout if p.returncode == 0 else None


def _ra_version() -> str:
    try:
        p = subprocess.run(["rust-analyzer", "--version"], capture_output=True, text=True, timeout=30)
        return p.stdout.strip() if p.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def _sha(*parts: str) -> str:
    h = hashlib.sha256()
    for x in parts:
        h.update(x.encode("utf-8", "replace"))
        h.update(b"\0")
    return h.hexdigest()[:20]


def _dump(g: dict) -> str:
    return json.dumps(g, indent=1, ensure_ascii=False) + "\n"


# ------------------------------------------------------------------ cache

def cache_dir(repo: str, override: str | None = None) -> str:
    if override:
        return os.path.abspath(override)
    common = (_git(repo, "rev-parse", "--path-format=absolute", "--git-common-dir") or "").strip()
    if not common:
        common = os.path.join(repo, ".git")
    return os.path.join(common, "asd-visual-review", "cache")


class Store:
    """JSON files under the cache dir; a corrupt or missing file reads as a miss."""

    def __init__(self, root: str):
        self.root = root

    def _p(self, *parts: str) -> str:
        return os.path.join(self.root, *parts)

    def read(self, *parts: str):
        try:
            with open(self._p(*parts), encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, ValueError):
            return None

    def write(self, obj, *parts: str) -> None:
        p = self._p(*parts)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        # a unique temp name in the same directory: worktrees share this cache, so two writers never meet on one temp file
        tmp = "%s.%d.%s.tmp" % (p, os.getpid(), os.urandom(4).hex())
        try:
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(obj, fh)
            os.replace(tmp, p)
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)

    def drop(self, *parts: str) -> None:
        try:
            os.remove(self._p(*parts))
        except OSError:
            pass

    def listdir(self, *parts: str) -> list:
        try:
            return sorted(os.listdir(self._p(*parts)))
        except OSError:
            return []


def blob_index(repo: str) -> dict:
    """{path: git blob id} of every tracked file; a file edited in the working tree gets the id its content would have."""
    idx = {}
    out = _git(repo, "ls-files", "-s", "-z") or ""
    for rec in out.split("\0"):
        if not rec:
            continue
        meta, _, path = rec.partition("\t")
        idx[path] = meta.split()[1]
    mod = _git(repo, "ls-files", "-m", "-z") or ""
    for path in (x for x in mod.split("\0") if x):
        if path in idx:
            if os.path.isfile(os.path.join(repo, path)):
                h = _git(repo, "hash-object", path)
                idx[path] = (h or "").strip() or "dirty"
            else:
                del idx[path]
    # untracked but not ignored: a new .rs file (or Cargo.toml / Cargo.lock) is an input of the keys too
    new = [x for x in (_git(repo, "ls-files", "-o", "--exclude-standard", "-z") or "").split("\0")
           if x and (x.endswith(".rs") or os.path.basename(x) in ("Cargo.toml", "Cargo.lock")) and os.path.isfile(os.path.join(repo, x))]
    if new:
        try:
            p = subprocess.run(["git", "hash-object", "--stdin-paths"], cwd=repo, input="\n".join(new) + "\n",
                               capture_output=True, text=True)
            ids = p.stdout.split() if p.returncode == 0 else []
        except OSError:
            ids = []
        for i, path in enumerate(new):
            idx[path] = ids[i] if len(ids) == len(new) else "untracked"
    return idx


class ModuleCache:
    """The `module_cache` of vr_extract.extract_rust: one entry per crate, keyed by the crate's source blobs."""

    def __init__(self, fs: Store, blobs: dict, ws_root: str, salt: str, force: bool = False):
        self.fs, self.blobs, self.ws_root, self.salt, self.force = fs, blobs, ws_root, salt, force

    def key(self, pkg: dict) -> str:
        d = os.path.relpath(os.path.dirname(pkg["manifest_path"]), self.ws_root)
        pre = "" if d == "." else d + "/"
        extra = {os.path.relpath(t["src_path"], self.ws_root) for t in pkg.get("targets", []) if t.get("src_path")}
        # the workspace manifest and lockfile, and any target root outside src/ (a [lib] or [[bin]] `path`), are inputs too;
        # a `#[path]` module outside src/ is not tracked
        own = sorted(p for p in self.blobs if p in (pre + "Cargo.toml", pre + "build.rs", "Cargo.toml", "Cargo.lock") or p in extra
                     or p.startswith(pre + "src/"))
        return _sha(self.salt, *["%s %s" % (p, self.blobs[p]) for p in own])

    def load(self, name: str, key: str):
        if self.force:
            return None
        rec = self.fs.read("modules", name + ".json")
        if rec and rec.get("key") == key:
            return rec["nodes"], rec["edges"]
        return None

    def store(self, name: str, key: str, nodes: list, edges: list) -> None:
        self.fs.write({"key": key, "nodes": nodes, "edges": edges}, "modules", name + ".json")


# ------------------------------------------------------------------ call layer

def _crate_src_files(pkgs: list, ws_root: str, blobs: dict) -> list:
    """Source files of the workspace crates (tracked .rs files under <crate>/src/), sorted."""
    res = set()
    for p in pkgs:
        d = os.path.relpath(os.path.dirname(p["manifest_path"]), ws_root)
        pre = "" if d == "." else d + "/"
        res.update(f for f in blobs if f.startswith(pre + "src/") and f.endswith(".rs"))
    return sorted(res)


def _file_name(rel: str) -> str:
    return hashlib.sha1(rel.encode()).hexdigest()[:16] + ".json"


def calls_layer(ws_root: str, pkgs: list, blobs: dict, store: Store, limit: float = CALLS_LIMIT,
                file_limit: float = FILE_LIMIT, index_limit: float = INDEX_LIMIT, ra_factory=None, force: bool = False,
                log=lambda m: None, ra_version: str = "") -> dict:
    """Bring the per-file call cache up to date. Returns the stats dict (also the view meta of the layer):
    status ran | partial | skipped, files, reused, recomputed (changed), dependents, deleted, pending, seconds."""
    t0 = time.time()
    files = _crate_src_files(pkgs, ws_root, blobs)
    # an entry keeps only the calls into workspace files ("known"), so the crate set and the server version are inputs
    salt = _sha(CALLS_SALT, json.dumps(FAST_OPTIONS, sort_keys=True), ra_version or "", *sorted(p["name"] for p in pkgs))
    entries, changed = {}, []
    for rel in files:
        e = None if force else store.read("calls", _file_name(rel))
        if e and e.get("blob") == blobs[rel] and e.get("salt") == salt and e.get("rel") == rel and not e.get("provisional"):
            entries[rel] = e
        else:
            changed.append(rel)
    deleted = []
    for name in store.listdir("calls"):
        e = store.read("calls", name)
        if e and e.get("rel") not in blobs or (e and e.get("rel") not in files):
            deleted.append((name, e.get("rel") if e else None))
    stats = {"status": "ran", "files": len(files), "reused": len(files) - len(changed), "recomputed": 0,
             "dependents": 0, "deleted": len(deleted), "pending": 0, "ra_started": False}
    for name, _ in deleted:
        store.drop("calls", name)
    known = set(files)
    done_now: dict = {}

    def finish(status=None, reason=None):
        if status:
            stats["status"] = status
        if reason:
            stats["reason"] = reason
        stats["seconds"] = round(time.time() - t0, 1)
        return stats

    ra_cm = None
    ra = None
    version = None

    def compute(rel: str, first: bool, retry: bool = False) -> bool:
        ra.set_deadline(index_limit if first else file_limit)
        fns = ra.outgoing_for_file(rel)
        norm = []
        for f in fns:
            out = None
            if f["out"] is not None:
                out = []
                for o in f["out"]:
                    p = o["path"]
                    p = os.path.relpath(p, ws_root) if os.path.isabs(p) else p
                    if p in known:
                        out.append({"name": o["name"], "path": p, "line": o["line"], "ranges": o["ranges"]})
            norm.append({"name": f["name"], "line": f["line"], "col": f["col"], "out": out})
        rec = {"rel": rel, "blob": blobs[rel], "salt": salt, "fns": norm}
        # a retry (second ask, server warm) is final: an answer still empty then is the real one
        if getattr(ra, "last_provisional", False) and not retry:
            rec["provisional"] = True  # the server may have answered empty while indexing: recomputed by the next run
        store.write(rec, "calls", _file_name(rel))
        entries[rel] = rec
        done_now[rel] = rec
        return True

    def dependents() -> list:
        """Unchanged files with a cached call into a recomputed file whose target is no longer there."""
        res = []
        for rel, e in entries.items():
            if rel in done_now:
                continue
            for f in e["fns"]:
                for o in f["out"] or []:
                    tgt = done_now.get(o["path"])
                    if tgt is not None and not any(x["name"] == o["name"] and x["line"] == o["line"] for x in tgt["fns"]):
                        res.append(rel)
                        break
                else:
                    continue
                break
        return sorted(res)

    # Dependents are found only after the changed files are computed, and a changed file is stored as soon as it is done:
    # a run that stops in between would never find them again. So the set of files still owed a recheck is persisted
    # (`pending-dependents`) before the first file is computed, kept up to date at every stop and cleared at the end.
    pend = set() if force else {r for r in (store.read("pending-dependents.json") or []) if r in known}
    todo = list(changed)
    if todo or deleted:
        # the files a deleted file's callers pointed into must be rechecked too
        gone = {r for _, r in deleted if r}
        for rel, e in entries.items():
            if any(o["path"] in gone for f in e["fns"] for o in (f["out"] or [])):
                pend.add(rel)
    pend &= set(entries)
    todo += sorted(pend)
    todo = sorted(set(todo))
    chg = set(changed)
    changed_set = set(changed)

    def save_pending(extra=()):
        owed = sorted((set(todo) | set(extra)) - chg) if (todo or extra) else []
        if owed:
            store.write(owed, "pending-dependents.json")
        else:
            store.drop("pending-dependents.json")
    if not todo:
        save_pending()
        return finish()
    save_pending()
    factory = ra_factory or (lambda root, t: RaClient(root, timeout=t, options=FAST_OPTIONS))
    try:
        ra_cm = factory(ws_root, index_limit)
        ra = ra_cm.__enter__()
    except RaError as exc:
        stats["pending"] = len(todo)
        return finish("skipped", str(exc))
    stats["ra_started"] = True
    stats["version"] = getattr(ra, "version", None)
    try:
        first = True
        n = 0
        phase = 0
        while todo or phase < 2:
            for rel in list(todo):
                if time.time() - t0 > limit:
                    stats["pending"] = len(todo)
                    save_pending(dependents() if phase == 0 else ())
                    return finish("partial", "stopped at the %.0f s limit; the next refresh continues" % limit)
                try:
                    compute(rel, first, retry=phase == 2)
                except RaError as exc:
                    stats["pending"] = len(todo)
                    save_pending(dependents() if phase == 0 else ())
                    return finish("partial", "%s; the next refresh continues" % exc)
                first = False
                todo.remove(rel)
                n += 1
                if phase == 2:
                    continue
                if phase == 0 and rel in changed_set:
                    stats["recomputed"] += 1
                else:
                    stats["dependents"] += 1
                if n % 50 == 0:
                    log("calls: %d files done, %d left, %.0f s" % (n, len(todo), time.time() - t0))
            if phase == 0:
                todo = dependents()
                phase = 1
                chg = set()
                save_pending()
            elif phase == 1:
                # A file answered while the index was still cold is asked once more now that the server has resolved
                # something; otherwise it would stay provisional, and every later refresh would recompute it cold again.
                todo = sorted(r for r, e in done_now.items() if e.get("provisional")) if getattr(ra, "_resolved", True) else []
                phase = 2
                if todo:
                    stats["retried"] = len(todo)
    finally:
        try:
            ra_cm.__exit__(None, None, None)
        except Exception:  # noqa: BLE001  (a dying server is not a failure of the layer)
            pass
    save_pending()
    stats["reused"] = len(files) - stats["recomputed"] - stats["dependents"] - stats["pending"]
    return finish()


# ------------------------------------------------------------------ views

def _meta(repo_name: str, commit: str, tools: list, layers: dict) -> dict:
    return {"repo": repo_name, "commit": commit, "generated_by": "extractor", "tools": tools, "layers": layers}


def _pseudo_pkgs(g: dict, ws_root: str) -> list:
    """The crates of an extraction graph as the {name, manifest_path} the call helpers need."""
    res = []
    for n in g["nodes"]:
        if n["kind"] == "crate":
            res.append({"name": n["label"], "manifest_path": os.path.join(ws_root, n["evidence"][0]["path"])})
    return sorted(res, key=lambda p: p["name"])


def _module_resolver(g: dict, parts: dict):
    ids = {n["id"] for n in g["nodes"]}
    for nodes, _ in parts.values():
        ids.update(n["id"] for n in nodes)
    crate_of = {n["label"].replace("-", "_"): n["id"] for n in g["nodes"] if n["kind"] == "crate"}

    def resolve(qual: str) -> str | None:
        q = qual
        while q:
            for pre in ("module:", "bin:"):
                if pre + q in ids:
                    return pre + q
            if q in crate_of:
                return crate_of[q]
            q = q.rpartition("::")[0]
        return None
    return resolve


def aggregate_calls(g: dict, parts: dict, pkgs: list, entries: dict, ws_root: str) -> tuple:
    """Module-level calls from the per-file entries: ({crate: {(src,dst): [count, rel, line, dispatch]}}, {(csrc,cdst): [...]})."""
    resolve = _module_resolver(g, parts)
    crate_node = {n["id"]: n for n in g["nodes"] if n["kind"] == "crate"}
    owner = {}
    for rel in entries:
        q = vr_extract._qual_for(rel, pkgs, ws_root)
        owner[rel] = (resolve(q), q.split("::")[0])
    cname = {n["label"].replace("-", "_"): n["id"] for n in crate_node.values()}
    mod_edges: dict = {}
    crate_edges: dict = {}
    disp: dict = {}

    def is_disp(path: str, line: int) -> bool:
        k = (path, line)
        if k not in disp:
            disp[k] = vr_extract._is_dispatch(os.path.join(ws_root, path), line)
        return disp[k]
    for rel, e in sorted(entries.items()):
        src, scrate = owner[rel]
        if src is None:
            continue
        for f in e["fns"]:
            for o in f["out"] or []:
                dst, dcrate = owner.get(o["path"], (None, None))
                if dst is None or not o["ranges"]:
                    continue
                site = o["ranges"][0]
                dd = is_disp(o["path"], o["line"])
                if dcrate == scrate:
                    if dst != src:
                        k = (scrate, src, dst)
                        rec = mod_edges.setdefault(k, [0, rel, site, False])
                        rec[0] += len(o["ranges"])
                        rec[3] = rec[3] or dd
                else:
                    k = (cname[scrate], cname[dcrate])
                    rec = crate_edges.setdefault(k, [0, rel, site, False])
                    rec[0] += len(o["ranges"])
                    rec[3] = rec[3] or dd
                    k2 = (scrate, src, cname[dcrate])
                    rec2 = mod_edges.setdefault(k2, [0, rel, site, False])
                    rec2[0] += len(o["ranges"])
                    rec2[3] = rec2[3] or dd
    return mod_edges, crate_edges


def _edge(src: str, dst: str, rec: list) -> dict:
    return {"id": "edge:calls:%s:%s" % (src, dst), "source": src, "target": dst, "kind": "calls", "label": str(rec[0]),
            "status": "context", "resolution": "dispatch" if rec[3] else "static",
            "evidence": [{"tier": "source", "path": rec[1], "line": rec[2], "note": "first of %d call sites" % rec[0]}]}


_GROUP_EV = {"tier": "inferred", "note": "grouped by top-level module for folding"}
GROUP_ABOVE = 15  # a module or calls view with more nodes than this carries groups the canvas can fold


def add_groups(graph: dict) -> dict:
    """Give a big modules/calls view groups to fold: one per top-level module that holds several nodes, one for the binaries,
    one for the crates the view only reaches, one pooling the single leftover modules. A pool of one stays loose; ids are
    group:<qualified name>, so they are stable across runs. A view of at most GROUP_ABOVE nodes is returned as it is."""
    nodes = graph["nodes"]
    if len(nodes) <= GROUP_ABOVE:
        return graph
    own = next((n["id"] for n in nodes if n["kind"] == "crate" and n["id"].startswith("crate:") and
                any(m["id"].startswith("module:" + n["label"].replace("-", "_") + "::") for m in nodes)), None)
    buckets: dict = {}
    for n in nodes:
        nid = n["id"]
        if n["kind"] == "crate":
            key = None if nid == own else "crates"
        elif n["kind"] == "bin":
            key = "bins"
        elif nid.startswith("module:") and nid.count("::") >= 1:
            key = "::".join(nid[7:].split("::")[:2])
        else:
            key = None
        buckets.setdefault(key, []).append(nid)
    labels = {"crates": "Other crates", "bins": "Binaries"}
    member: dict = {}
    pool: list = []
    groups = []
    for key in sorted(k for k in buckets if k):
        ids = buckets[key]
        if key in labels or len(ids) > 1:
            groups.append({"id": "group:" + key, "label": labels.get(key, key.split("::")[-1]), "status": "context", "label_source": "extractor", "evidence": [_GROUP_EV]})
            member.update({i: "group:" + key for i in ids})
        else:
            pool.extend(ids)
    if len(pool) > 1:
        groups.append({"id": "group:other-modules", "label": "Other modules", "status": "context", "label_source": "extractor", "evidence": [_GROUP_EV]})
        member.update({i: "group:other-modules" for i in pool})
    if not groups:
        return graph
    out = dict(graph, groups=groups)
    out["nodes"] = [dict(n, group=member[n["id"]]) if n["id"] in member else n for n in nodes]
    return out


def build_views(g: dict, parts: dict, pkgs: list, entries: dict, ws_root: str, meta: dict, has_calls: bool) -> dict:
    """{file name: graph} for every view of the map except the product map."""
    views = {}
    repo = meta["repo"]
    crates = [n for n in g["nodes"] if n["kind"] == "crate"]
    cids = {n["id"] for n in crates}
    views["crate-map.json"] = {
        "schema_version": "1.0.0", "view": "full", "title": "Crate map of %s" % repo, "meta": meta,
        "nodes": crates, "edges": [e for e in g["edges"] if e["kind"] == "depends"]}
    by_id = {n["id"]: n for n in crates}
    for c in crates:
        name = c["label"]
        nodes, edges = parts.get(name, ([], []))
        if not nodes:
            continue
        views["modules-%s.json" % name] = {
            "schema_version": "1.0.0", "view": "full", "title": "Modules of %s" % name, "meta": meta,
            "nodes": [c] + sorted(nodes, key=lambda n: n["id"]), "edges": sorted(edges, key=lambda e: e["id"])}
        views["modules-%s.json" % name] = add_groups(views["modules-%s.json" % name])
    if has_calls and entries:
        mod_edges, crate_edges = aggregate_calls(g, parts, pkgs, entries, ws_root)
        node_of = {n["id"]: n for nodes, _ in parts.values() for n in nodes}
        node_of.update(by_id)
        per: dict = {}
        for (crate, s, d), rec in mod_edges.items():
            per.setdefault(crate, []).append((s, d, rec))
        for crate, lst in sorted(per.items()):
            cid = [i for i in cids if by_id[i]["label"].replace("-", "_") == crate]
            ids, es = set(), []
            for s, d, rec in sorted(lst):
                if s not in node_of or d not in node_of:
                    continue
                ids.update((s, d))
                es.append(_edge(s, d, rec))
            if not es:
                continue
            label = by_id[cid[0]]["label"] if cid else crate
            views["calls-%s.json" % label] = {
                "schema_version": "1.0.0", "view": "full", "title": "Calls between modules of %s" % label, "meta": meta,
                "nodes": [node_of[i] for i in sorted(ids)], "edges": es}
            views["calls-%s.json" % label] = add_groups(views["calls-%s.json" % label])
        if crate_edges:
            ids = {x for k in crate_edges for x in k}
            views["crate-calls.json"] = {
                "schema_version": "1.0.0", "view": "full", "title": "Calls between crates of %s" % repo, "meta": meta,
                "nodes": [by_id[i] for i in sorted(ids)],
                "edges": [_edge(s, d, rec) for (s, d), rec in sorted(crate_edges.items())]}
    return views


def _strip_commit(g: dict) -> dict:
    h = json.loads(json.dumps(g))
    h.get("meta", {}).pop("commit", None)
    h.get("meta", {}).pop("layers", None)
    return h


VIEW_FILE_RE = re.compile(r"^(crate-map|crate-calls|product-map|(modules|calls)-[A-Za-z0-9_.-]+)\.json$")


def safe_view_name(name) -> bool:
    """A file name this tool writes: a plain basename (no slash, no `..`), one of its own view names."""
    return isinstance(name, str) and name == os.path.basename(name) and ".." not in name and bool(VIEW_FILE_RE.match(name))


def write_views(repo: str, out_dir: str, views: dict, log=lambda m: None, keep=lambda name: False) -> dict:
    """Write the views whose content changed; returns {name: "written" | "unchanged" | "removed"}."""
    d = os.path.join(repo, out_dir)
    os.makedirs(d, exist_ok=True)
    res = {}
    for name, g in sorted(views.items()):
        issues = vr_graph.validate(g, root=repo)
        if issues:
            raise RefreshError("view %s is invalid: %s" % (name, json.dumps(issues[:3])))
        p = os.path.join(d, name)
        old = None
        try:
            with open(p, encoding="utf-8") as fh:
                old = json.load(fh)
        except (OSError, ValueError):
            pass
        if old is not None and json.dumps(_strip_commit(old), sort_keys=True) == json.dumps(_strip_commit(g), sort_keys=True):
            res[name] = "unchanged"
            continue
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(_dump(g))
        res[name] = "written"
    # a crate that left the workspace leaves its views behind: remove the ones this tool owns
    prev = None
    try:
        with open(os.path.join(d, MANIFEST), encoding="utf-8") as fh:
            prev = json.load(fh)
    except (OSError, ValueError):
        pass
    droot = os.path.realpath(d)
    for v in (prev or {}).get("views", []):
        f = v.get("file") if isinstance(v, dict) else None
        if not safe_view_name(f):
            print("vr_refresh: ignoring manifest entry with an unsafe file name: %r" % (f,), file=sys.stderr)
            continue
        target = os.path.realpath(os.path.join(d, f))
        if os.path.dirname(target) != droot:
            print("vr_refresh: ignoring manifest entry outside %s: %r" % (out_dir, f), file=sys.stderr)
            continue
        if f not in views and f != "product-map.json" and not keep(f) and os.path.isfile(target):
            os.remove(target)
            res[f] = "removed"
    return res


# ------------------------------------------------------------------ product map

def _first_parent_revs(repo: str, prev: str, head: str) -> list | None:
    try:
        rc = subprocess.run(["git", "merge-base", "--is-ancestor", prev, head], cwd=repo, capture_output=True).returncode
    except OSError as exc:
        raise RefreshError("git cannot be run: %s" % exc)
    if rc != 0:
        return None
    out = _git(repo, "rev-list", "--first-parent", "--no-merges", "%s..%s" % (prev, head))
    merges = _git(repo, "rev-list", "--first-parent", "--merges", "%s..%s" % (prev, head))
    revs = []
    for r in ((merges or "").split() + (out or "").split()):
        if _git(repo, "rev-parse", "--verify", "-q", r + "^1") is not None:
            revs.append(r)
    return revs


def refresh_product_map(repo: str, out_dir: str, prev: str | None, head: str, full: bool) -> dict:
    """Grow the L0 map from `prev` to `head` per change (vr_map.build --touched), or build it whole the first time."""
    path = os.path.join(repo, out_dir, "product-map.json")
    try:
        book = vr_map.find_feature_book(repo)
    except vr_map.MapError as exc:
        return {"status": "skipped", "reason": str(exc)}
    defs = vr_map.parse_feature_book("\n".join(vr_map.read_lines(repo, book) or []))
    if not defs:
        return {"status": "skipped", "reason": "%s defines no feature" % book}
    existing = None
    if os.path.isfile(path) and not full:
        try:
            existing = vr_graph.load(path)
        except (OSError, ValueError):
            existing = None
    revs = _first_parent_revs(repo, prev, head) if (existing and prev) else None
    if existing is None or revs is None:
        g = vr_map.build(repo, all_features=True)
        stats = {"status": "ran", "mode": "all", "features": len(g["nodes"]), "relinked": len(g["nodes"]), "reused": 0}
    else:
        have = {n["id"]: n for n in existing["nodes"] if n["kind"] == "feature"}
        gone = [i for i in have if i.split(":", 1)[1] not in defs]
        for i in gone:
            del have[i]
        existing = dict(existing, nodes=[have[k] for k in sorted(have)])
        new = [f for f in defs if "feature:" + f not in have]
        relabel = [f for f in defs if "feature:" + f in have and have["feature:" + f]["label"] != defs[f]["label"]]
        try:
            g = vr_map.build(repo, features=new + relabel, touched=revs, existing=existing)
        except vr_map.MapError:
            g = dict(existing)
            g["meta"] = dict(existing["meta"], commit=vr_map.head_commit(repo))
        vr_map.organise(repo, g)
        before = {n["id"]: json.dumps(n, sort_keys=True) for n in existing["nodes"]}
        relinked = sum(1 for n in g["nodes"] if before.get(n["id"]) != json.dumps(n, sort_keys=True))
        stats = {"status": "ran", "mode": "touched", "revs": len(revs), "features": len(g["nodes"]),
                 "relinked": relinked, "reused": len(g["nodes"]) - relinked, "removed": len(gone)}
    issues = vr_graph.validate(g, root=repo)  # the budget is on: the map view counts its folded first screen
    if issues:
        raise RefreshError("the product map is invalid: %s" % json.dumps(issues[:3]))
    old = None
    try:
        with open(path, encoding="utf-8") as fh:
            old = json.load(fh)
    except (OSError, ValueError):
        pass
    os.makedirs(os.path.dirname(path), exist_ok=True)
    changed = old is None or json.dumps(_strip_commit(old), sort_keys=True) != json.dumps(_strip_commit(g), sort_keys=True) \
        or json.dumps(old["meta"].get("staleness", {}).get("elements"), sort_keys=True) != json.dumps(g["meta"]["staleness"]["elements"], sort_keys=True)
    if changed:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(_dump(g))
    stats["file"] = "written" if changed else "unchanged"
    return stats


# ------------------------------------------------------------------ refresh

def previous_commit(repo: str, out_dir: str, store: Store) -> str | None:
    """The commit the product map on disk was last refreshed at: the manifest's own `product_map_commit` (advanced only
    when the product map was refreshed), else the product map's meta.commit, else the manifest's extraction commit
    (manifests written before the field existed), else the cache state."""
    for rel, getter in ((MANIFEST, lambda g: g.get("product_map_commit")),
                        ("product-map.json", lambda g: (g.get("meta") or {}).get("commit")),
                        (MANIFEST, lambda g: g.get("extraction_commit"))):
        try:
            with open(os.path.join(repo, out_dir, rel), encoding="utf-8") as fh:
                c = getter(json.load(fh))
        except (OSError, ValueError):
            continue
        if c:
            return c
    st = store.read("state.json")
    return st.get("extraction_commit") if st else None


def refresh(repo: str, out_dir: str = DEFAULT_DIR, cache: str | None = None, since: str | None = None, calls: bool = True,
            full: bool = False, product: bool = True, calls_limit: float = CALLS_LIMIT, file_limit: float = FILE_LIMIT,
            index_limit: float = INDEX_LIMIT, jobs: int = 4, runner=None, ra_factory=None, log=lambda m: None) -> dict:
    t0 = time.time()
    if shutil.which("git") is None:
        raise RefreshError("git is not installed or not on PATH")
    repo = os.path.realpath(repo)
    head = (_git(repo, "rev-parse", "HEAD") or "").strip()
    if not head:
        raise RefreshError("%s is not a git repository with a commit" % repo)
    short = head[:8]
    store = Store(cache_dir(repo, cache))
    prev = since or previous_commit(repo, out_dir, store)
    prev_full = (_git(repo, "rev-parse", "--verify", "-q", prev + "^{commit}") or "").strip() if prev else ""
    blobs = blob_index(repo)
    runner = runner or vr_extract.default_runner
    ver, _ = vr_extract.cargo_modules_version(runner, repo)
    salt = _sha("modules-2", vr_extract.VERSION, ver or "none", *vr_extract.CM_FLAGS)
    mc = ModuleCache(store, blobs, repo, salt, force=full)
    parts: dict = {}
    t = time.time()
    g = vr_extract.extract_rust(repo, layers=(1, 2), jobs=jobs, runner=runner, module_cache=mc, parts=parts)
    t_extract = time.time() - t
    lm = g["meta"]["layers"]["modules"]
    ws_root = repo
    pkgs = _pseudo_pkgs(g, ws_root)
    log("crates: %d; modules: %d reused, %d recomputed (%.1f s)" % (len(pkgs), len(lm.get("reused", [])), len(lm.get("recomputed", [])), t_extract))
    cstats = {"status": "skipped", "reason": "not requested"}
    entries: dict = {}
    if calls:
        cstats = calls_layer(ws_root, pkgs, blobs, store, calls_limit, file_limit, index_limit, ra_factory, force=full, log=log,
                             ra_version=_ra_version() if ra_factory is None else "")
        for rel in _crate_src_files(pkgs, ws_root, blobs):
            e = store.read("calls", _file_name(rel))
            if e and e.get("rel") == rel and e.get("blob") == blobs[rel]:
                entries[rel] = e
        if cstats["status"] == "skipped":
            entries = {}
    tools = sorted(g["meta"]["tools"] + ([{"name": "vr_refresh", "version": VERSION}]), key=lambda x: x["name"])
    # the schema lets a layer say ran or skipped, with a reason: a partial call layer ran, and says so in the reason
    layers = {k: {x: y for x, y in v.items() if x in ("status", "reason", "limited_to", "skipped_crates")}
              for k, v in g["meta"]["layers"].items()}
    layers["calls"] = {"status": "ran" if cstats["status"] in ("ran", "partial") else "skipped"}
    if cstats.get("reason"):
        layers["calls"]["reason"] = ("partial: " if cstats["status"] == "partial" else "") + cstats["reason"]
    meta = _meta(g["meta"]["repo"], short, tools, layers)
    # A layer that did not run (cargo-modules missing or the wrong version, a crate whose target failed) leaves the
    # previous view of what it covers on disk and in the manifest: a skipped layer is not a deleted crate.
    mod_skipped = lm.get("status") == "skipped"
    affected = sorted(p["name"] for p in pkgs) if mod_skipped else sorted(lm.get("failed", []))
    if mod_skipped:
        parts.clear()
    for c in affected:
        parts.pop(c, None)
    views = build_views(g, parts, pkgs, entries, ws_root, meta, bool(calls and entries))
    for c in affected:
        views.pop("modules-%s.json" % c, None)
        views.pop("calls-%s.json" % c, None)
    if affected:
        views.pop("crate-calls.json", None)
    # a call layer that did not run (not requested, or no rust-analyzer) leaves the committed call views as they are
    is_calls = lambda n: n.startswith("calls-") or n == "crate-calls.json"
    keep_calls = cstats["status"] == "skipped"
    kept_mod = {"modules-%s.json" % c for c in affected} | {"calls-%s.json" % c for c in affected} | ({"crate-calls.json"} if affected else set())
    keep = lambda n: (keep_calls and is_calls(n)) or n in kept_mod
    written = write_views(repo, out_dir, views, log, keep=keep)
    pstats = {"status": "skipped", "reason": "not requested"}
    if product:
        pstats = refresh_product_map(repo, out_dir, prev_full or None, head, full)
    mp = os.path.join(repo, out_dir, MANIFEST)
    try:
        with open(mp, encoding="utf-8") as fh:
            old = json.load(fh)
    except (OSError, ValueError):
        old = None
    # the product map has its own base commit: it advances only when the product map was refreshed
    pm_commit = head if pstats.get("status") == "ran" else ((old or {}).get("product_map_commit") or prev_full or None)
    mlayer = {"reused": lm.get("reused", []), "recomputed": lm.get("recomputed", []), "status": lm.get("status")}
    if affected:
        mlayer["kept"] = affected
        if lm.get("reason"):
            mlayer["reason"] = lm["reason"]
    mf = {"schema": "map-manifest/1", "repo": g["meta"]["repo"], "extraction_commit": head, "previous_commit": prev_full or None,
          "product_map_commit": pm_commit,
          "views": [{"file": n, "title": v["title"], "nodes": len(v["nodes"]), "edges": len(v["edges"])}
                    for n, v in sorted(views.items())],
          # no timings here: the manifest is committed and must not change when nothing else did
          "layers": {"modules": mlayer, "calls": {k: v for k, v in cstats.items() if k != "seconds"}, "product_map": pstats}}
    if product and pstats.get("status") == "ran":
        try:
            pm = vr_graph.load(os.path.join(repo, out_dir, "product-map.json"))
            mf["views"].append({"file": "product-map.json", "title": pm["title"], "nodes": len(pm["nodes"]), "edges": len(pm["edges"])})
        except (OSError, ValueError):
            pass
    kept = []
    if old:
        have = {v["file"] for v in mf["views"]}
        for v in old.get("views", []):
            f = v.get("file")
            if f in have or not isinstance(f, str) or not os.path.isfile(os.path.join(repo, out_dir, f)):
                continue
            if f == "product-map.json":  # the product map was not refreshed this run: its row stays
                mf["views"].append(v)
            elif safe_view_name(f) and keep(f):
                mf["views"].append(v)
                kept.append(f)
    mf["views"].sort(key=lambda v: v["file"])
    # Rewrite the manifest only when a view moved or the list of views did: the extraction commit alone is not a change,
    # so a stub cut with no map change leaves a clean tree and opens no pull request.
    moved = any(x in ("written", "removed") for x in written.values()) or pstats.get("file") == "written"
    if old is None or moved or old.get("views") != mf["views"] or old.get("repo") != mf["repo"]:
        with open(mp, "w", encoding="utf-8") as fh:
            fh.write(_dump(mf))
        written[MANIFEST] = "written"
    store.write({"extraction_commit": head, "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}, "state.json")
    return {"commit": head, "previous": prev_full or None, "dir": out_dir, "cache": store.root, "views": written, "kept": sorted(kept),
            "modules": {"reused": lm.get("reused", []), "recomputed": lm.get("recomputed", []), "status": lm.get("status"),
                        **({"failed": affected} if affected and not mod_skipped else {}),
                        **({"reason": lm["reason"]} if lm.get("reason") else {})},
            "calls": cstats, "product_map": pstats, "seconds": round(time.time() - t0, 1),
            "seconds_extract": round(t_extract, 1)}


def render(r: dict) -> str:
    m, c, p = r["modules"], r["calls"], r["product_map"]
    lines = ["refresh at %s (previous %s), %.1f s, cache %s" % (r["commit"][:8], (r["previous"] or "none")[:8], r["seconds"], r["cache"]),
             "  crates:   recomputed (cargo metadata, always)",
             "  modules:  %d crates reused, %d recomputed%s" % (len(m["reused"]), len(m["recomputed"]),
                                                              (" [" + ", ".join(m["recomputed"]) + "]") if m["recomputed"] else "")]
    if m.get("reason"):
        lines.append("            " + m["reason"])
    if r.get("kept"):
        lines.append("  kept:     %d previous view(s) left as they were (their layer did not run): %s" % (
            len(r["kept"]), ", ".join(r["kept"][:6]) + (", ..." if len(r["kept"]) > 6 else "")))
    if c["status"] == "skipped":
        lines.append("  calls:    skipped (%s)" % c.get("reason", ""))
    else:
        lines.append("  calls:    %s; %d files: %d reused, %d recomputed, %d recomputed as dependents, %d deleted, %d pending%s, %.1f s" % (
            c["status"], c["files"], c["reused"], c["recomputed"], c["dependents"], c["deleted"], c["pending"],
            "" if c["ra_started"] else " (rust-analyzer not started)", c.get("seconds", 0)))
        if c.get("reason"):
            lines.append("            " + c["reason"])
    if p["status"] == "ran":
        lines.append("  product map: %s, %d features: %d re-linked, %d reused%s" % (
            p["mode"], p["features"], p["relinked"], p["reused"], " (%d changes)" % p["revs"] if "revs" in p else ""))
    else:
        lines.append("  product map: skipped (%s)" % p.get("reason", ""))
    w = r["views"]
    lines.append("  views:    %d written, %d unchanged%s" % (
        sum(1 for v in w.values() if v == "written"), sum(1 for v in w.values() if v == "unchanged"),
        ", %d removed" % sum(1 for v in w.values() if v == "removed") if "removed" in w.values() else ""))
    return "\n".join(lines)


# ------------------------------------------------------------------ CLI

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    ap.add_argument("repo", nargs="?")
    ap.add_argument("--since")
    ap.add_argument("--dir", default=DEFAULT_DIR)
    ap.add_argument("--cache")
    ap.add_argument("--full", action="store_true", help="ignore the cache and the previous map: compute everything")
    ap.add_argument("--no-calls", action="store_true")
    ap.add_argument("--no-product-map", action="store_true")
    ap.add_argument("--calls-limit", type=float, default=CALLS_LIMIT)
    ap.add_argument("--file-limit", type=float, default=FILE_LIMIT)
    ap.add_argument("--index-limit", type=float, default=INDEX_LIMIT)
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--metrics")
    ap.add_argument("--no-metrics", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args(argv)
    if a.selftest:
        return _selftest()
    if not a.repo:
        ap.print_usage(sys.stderr)
        return 2
    try:
        r = refresh(a.repo, a.dir, a.cache, a.since, not a.no_calls, a.full, not a.no_product_map, a.calls_limit,
                    a.file_limit, a.index_limit, a.jobs, log=lambda m: print("vr_refresh: " + m, file=sys.stderr, flush=True))
    except (RefreshError, vr_extract.ExtractError, vr_map.MapError) as exc:
        print("vr_refresh: %s" % exc, file=sys.stderr)
        return 1
    if not a.no_metrics:
        c = r["calls"]
        vr_metrics.log(os.path.abspath(a.repo), "vr_refresh", "refresh", {
            "seconds": r["seconds"], "modules_reused": len(r["modules"]["reused"]), "modules_recomputed": len(r["modules"]["recomputed"]),
            "call_files": c.get("files", 0), "call_files_recomputed": c.get("recomputed", 0) + c.get("dependents", 0)}, a.metrics)
    print(json.dumps(r, indent=1) if a.json else render(r))
    return 0


# ------------------------------------------------------------------ selftest

class _FakeRa:
    """In-process stand-in for RaClient: every function of the `app` crate calls `f` in crates/sys/src/lib.rs."""
    started = 0
    seen: list = []
    fail_prefix = None  # files under this prefix raise RaError
    prov: set = set()   # files whose answer is flagged provisional

    def __init__(self, root, timeout=0):
        self.root, self.version = root, "fake-ra"
        self.last_provisional = False

    def __enter__(self):
        _FakeRa.started += 1
        return self

    def __exit__(self, *a):
        return False

    def set_deadline(self, s):
        pass

    def outgoing_for_file(self, rel):
        if _FakeRa.fail_prefix and rel.startswith(_FakeRa.fail_prefix):
            raise RaError("boom on %s" % rel)
        _FakeRa.seen.append(rel)
        self.last_provisional = rel in _FakeRa.prov
        with open(os.path.join(self.root, rel), encoding="utf-8") as fh:
            text = fh.read()
        res = []
        sys_lib = os.path.join(self.root, "crates/sys/src/lib.rs")
        with open(sys_lib, encoding="utf-8") as fh:
            tgt = [(m.group(1), i) for i, ln in enumerate(fh.read().split("\n"), 1) for m in [re.search(r"fn (\w+)", ln)] if m]
        for i, ln in enumerate(text.split("\n"), 1):
            m = re.search(r"fn (\w+)", ln)
            if m:
                out = [{"name": tgt[0][0], "path": sys_lib, "line": tgt[0][1], "ranges": [i]}] if rel.startswith("crates/app/") and tgt else []
                res.append({"name": m.group(1), "line": i, "col": ln.index(m.group(1)) + 1, "out": out})
        return res


def _selftest() -> int:
    fails = []

    def check(name, ok, why=""):
        print(("ok   " if ok else "FAIL ") + name + ("" if ok else "  " + why))
        if not ok:
            fails.append(name)
    with tempfile.TemporaryDirectory() as d0:
        d = os.path.realpath(d0)
        meta = vr_extract._fake_workspace(d)
        for rel, text in (("crates/app/src/net.rs", "pub mod http;\npub fn get() {}\n"),
                          ("crates/sys/src/lib.rs", "pub fn f() {}\n"),
                          ("FEATURE-BOOK.md", "# Features\n\n- H-01: Gateway routes requests\n- H-02: Typed shapes\n"),
                          ("crates/sys/src/shapes.rs", "// H-02 shapes live here\npub fn shape() {}\n"),
                          (".gitignore", "target/\n")):
            with open(os.path.join(d, rel), "w") as fh:
                fh.write(text)

        def sh(*a):
            subprocess.run(["git"] + list(a), cwd=d, check=True, capture_output=True,
                           env=dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t"))
        sh("init", "-q", "-b", "main")
        sh("add", "-A")
        sh("commit", "-q", "-m", "one")
        cache = os.path.join(d0 + "-cache")
        log: list = []
        base = vr_extract.make_runner(meta, log)

        def run(cache_=None, **kw):
            del log[:]
            _FakeRa.started, _FakeRa.seen = 0, []
            return refresh(d, cache=cache_ or cache, runner=base, ra_factory=lambda r, t: _FakeRa(r), **kw)

        def mod_runs():
            return sorted({c[c.index("-p") + 1] for c in log if c[:3] == ["cargo", "modules", "structure"]})
        r1 = run()
        check("first-run:module-flags-shared", all(all(f in c for f in vr_extract.CM_FLAGS) and "--all-features" in c and "x86_64-unknown-linux-gnu" in c
                                                   for c in log if c[:3] == ["cargo", "modules", "structure"]) and vr_extract.CM_TARGET in vr_extract.CM_FLAGS, str(log[:3]))
        crates = ["app", "core-lib", "sys", "test-kit", "workspace-hack"]
        check("first-run:modules-all", mod_runs() == crates and r1["modules"]["recomputed"] == crates and not r1["modules"]["reused"], str(r1["modules"]))
        nfiles = r1["calls"]["files"]
        check("first-run:calls-all", r1["calls"]["recomputed"] == nfiles and nfiles >= 8 and _FakeRa.started == 1, str(r1["calls"]))
        check("first-run:product-map-all", r1["product_map"]["mode"] == "all" and r1["product_map"]["features"] == 2, str(r1["product_map"]))
        names = set(os.listdir(os.path.join(d, DEFAULT_DIR)))
        check("first-run:views", {"crate-map.json", "modules-app.json", "calls-app.json", "crate-calls.json", "product-map.json", MANIFEST} <= names, str(sorted(names)))
        bad = []
        for n in sorted(names):
            if n.endswith(".json") and n != MANIFEST:
                bad += vr_graph.validate(vr_graph.load(os.path.join(d, DEFAULT_DIR, n)), root=d)
        check("first-run:views-validate", not bad, str(bad[:2]))
        sh("add", "-A")
        sh("commit", "-q", "-m", "views")
        r2 = run()
        check("second-run:reuses-everything", not mod_runs() and len(r2["modules"]["reused"]) == 5 and not r2["modules"]["recomputed"]
              and r2["calls"]["recomputed"] == 0 and r2["calls"]["reused"] == nfiles and _FakeRa.started == 0, str((mod_runs(), r2["modules"], r2["calls"])))
        check("second-run:rust-analyzer-not-started", not r2["calls"]["ra_started"])
        check("second-run:no-view-rewritten", not any(v == "written" for k, v in r2["views"].items() if k != MANIFEST), str(r2["views"]))
        # the cargo-modules flags are part of the module-cache key: a cache from before them (or with other flags) is recomputed
        import shutil
        alt = cache + "-flags"
        shutil.copytree(cache, alt)
        run(cache_=alt)
        check("flags:same-flags-reuse", not mod_runs(), str(mod_runs()))
        saved = vr_extract.CM_FLAGS
        vr_extract.CM_FLAGS = saved + ("--features", "extra")
        try:
            r2b = run(cache_=alt)
        finally:
            vr_extract.CM_FLAGS = saved
        check("flags:changed-flags-recompute", mod_runs() == crates and len(r2b["modules"]["recomputed"]) == 5, str((mod_runs(), r2b["modules"])))
        with open(os.path.join(d, "crates/core-lib/src/lib.rs"), "a") as fh:
            fh.write("pub fn g() {}\n")
        sh("add", "-A")
        sh("commit", "-q", "-m", "core-lib change")
        r3 = run()
        check("one-crate-change:modules-only-that-crate", mod_runs() == ["core-lib"] and r3["modules"]["recomputed"] == ["core-lib"]
              and len(r3["modules"]["reused"]) == 4, str((mod_runs(), r3["modules"])))
        check("one-crate-change:calls-only-that-file", _FakeRa.seen == ["crates/core-lib/src/lib.rs"] and r3["calls"]["recomputed"] == 1
              and r3["calls"]["dependents"] == 0, str((_FakeRa.seen, r3["calls"])))
        check("second-run:manifest-not-rewritten", MANIFEST not in r2["views"], str(r2["views"]))
        check("one-crate-change:previous-is-the-product-map-base", r3["previous"] == r1["commit"], str(r3["previous"]))
        check("one-crate-change:product-map-touched", r3["product_map"]["mode"] == "touched" and r3["product_map"]["revs"] >= 1, str(r3["product_map"]))
        with open(os.path.join(d, "crates/sys/src/lib.rs"), "w") as fh:
            fh.write("pub fn added() {}\npub fn f() {}\n")
        sh("add", "-A")
        sh("commit", "-q", "-m", "sys change")
        r4 = run()
        check("callee-moved:dependents-recomputed", r4["modules"]["recomputed"] == ["sys"] and r4["calls"]["recomputed"] == 1
              and r4["calls"]["dependents"] >= 1 and any(x.startswith("crates/app/") for x in _FakeRa.seen), str((r4["modules"], r4["calls"], _FakeRa.seen)))
        with open(os.path.join(d, "crates/test-kit/src/lib.rs"), "a") as fh:
            fh.write("pub fn dirty() {}\n")
        r5 = run()
        check("working-tree-edit:counts-as-change", r5["modules"]["recomputed"] == ["test-kit"] and r5["calls"]["recomputed"] == 1, str((r5["modules"], r5["calls"])))
        sh("checkout", "-q", "--", "crates/test-kit/src/lib.rs")
        fresh = cache + "-fresh"
        r6 = run(fresh, calls_limit=0.0)
        check("limit:partial-then-resumes", r6["calls"]["status"] == "partial" and r6["calls"]["pending"] == nfiles, str(r6["calls"]))
        r7 = run(fresh)
        check("limit:next-run-continues", r7["calls"]["status"] == "ran" and r7["calls"]["pending"] == 0 and r7["calls"]["recomputed"] == nfiles
              and r7["modules"]["recomputed"] == [], str((r7["calls"], r7["modules"])))
        r7b = run(full=True)
        check("full:ignores-the-cache", len(r7b["modules"]["recomputed"]) == 5 and r7b["calls"]["recomputed"] == nfiles, str((r7b["modules"], r7b["calls"])))
        os.remove(os.path.join(d, "crates/app/src/net/http.rs"))
        with open(os.path.join(d, "crates/app/src/net.rs"), "w") as fh:
            fh.write("pub fn get() {}\n")
        r8 = run()
        check("deleted-file:dropped-from-cache", r8["calls"]["deleted"] == 1 and r8["modules"]["recomputed"] == ["app"], str((r8["calls"], r8["modules"])))
        r9 = run(calls=False)
        check("no-calls:modules-reused-and-layer-skipped", r9["calls"]["status"] == "skipped" and not r9["modules"]["recomputed"], str(r9["calls"]))
        check("no-calls:committed-call-views-kept", os.path.isfile(os.path.join(d, "docs/architecture/calls-app.json")) and
              "removed" not in r9["views"].values(), str(r9["views"]))

        def failing_ra(root, t):
            raise RaError("cannot start rust-analyzer: nope")
        with open(os.path.join(d, "crates/sys/src/shapes.rs"), "a") as fh:
            fh.write("pub fn more() {}\n")
        del log[:]
        r10 = refresh(d, cache=cache, runner=base, ra_factory=failing_ra)
        check("ra-missing:skipped-with-reason-modules-kept", r10["calls"]["status"] == "skipped" and "nope" in r10["calls"]["reason"]
              and r10["modules"]["status"] == "ran", str(r10["calls"]))
        # a layer that did not run keeps the previous views and their manifest rows: cargo-modules missing, or a crate failing
        def no_cm(cmd, root):
            if cmd[:3] == ["cargo", "modules", "--version"]:
                return 1, "", "no such command"
            return base(cmd, root)
        before = {n: open(os.path.join(d, DEFAULT_DIR, n)).read() for n in os.listdir(os.path.join(d, DEFAULT_DIR)) if n.startswith(("modules-", "calls-"))}
        r11 = refresh(d, cache=cache, runner=no_cm, ra_factory=lambda r, t: _FakeRa(r))
        after = {n: open(os.path.join(d, DEFAULT_DIR, n)).read() for n in os.listdir(os.path.join(d, DEFAULT_DIR)) if n.startswith(("modules-", "calls-"))}
        man = json.load(open(os.path.join(d, DEFAULT_DIR, MANIFEST)))
        check("module-layer-skipped:no-view-removed", r11["modules"]["status"] == "skipped" and "removed" not in r11["views"].values()
              and before == after, str((r11["modules"], r11["views"])))
        check("module-layer-skipped:manifest-keeps-rows", {"modules-app.json", "calls-app.json"} <= {v["file"] for v in man["views"]}
              and "modules-app.json" in r11["kept"], str(r11["kept"]))
        check("module-layer-skipped:says-so", "kept" in render(r11) and "not installed" in render(r11), render(r11))

        def fail_app(cmd, root):
            if cmd[:3] == ["cargo", "modules", "structure"] and "app" in cmd:
                return 1, "", "boom"
            return base(cmd, root)
        with open(os.path.join(d, "crates/app/src/lib.rs"), "a") as fh:
            fh.write("pub fn touched() {}\n")
        r12 = refresh(d, cache=cache, runner=fail_app, ra_factory=lambda r, t: _FakeRa(r))
        check("crate-failed:old-views-kept", "modules-app.json" in r12["kept"] and "removed" not in r12["views"].values()
              and os.path.isfile(os.path.join(d, DEFAULT_DIR, "modules-app.json")) and r12["modules"].get("failed") == ["app"],
              str((r12["kept"], r12["modules"])))
        # a manifest from a commit is not trusted for file names: traversal and absolute names are ignored
        with tempfile.TemporaryDirectory() as td0:
            td = os.path.realpath(td0)
            os.makedirs(os.path.join(td, "docs/architecture"))
            for rel in ("victim.txt", "docs/architecture/modules-gone.json", "docs/victim.json"):
                with open(os.path.join(td, rel), "w") as fh:
                    fh.write("x")
            with open(os.path.join(td, "docs/architecture", MANIFEST), "w") as fh:
                json.dump({"views": [{"file": "../../victim.txt"}, {"file": "../victim.json"}, {"file": "/etc/hosts"},
                                     {"file": "modules-gone.json"}, {"file": None}]}, fh)
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                res = write_views(td, DEFAULT_DIR, {})
            check("traversal:outside-files-survive", os.path.isfile(os.path.join(td, "victim.txt")) and os.path.isfile(os.path.join(td, "docs/victim.json"))
                  and err.getvalue().count("ignoring") == 4, err.getvalue())
            check("traversal:genuine-view-removed", res == {"modules-gone.json": "removed"}, str(res))
        # call layer: a stop between the changed files and their dependents does not lose the dependents
        c2 = cache + "-pending"
        run(c2)
        with open(os.path.join(d, "crates/sys/src/lib.rs"), "w") as fh:
            fh.write("pub fn added2() {}\npub fn added() {}\npub fn f() {}\n")
        _FakeRa.fail_prefix = "crates/app/"
        try:
            r13 = run(c2)
        finally:
            _FakeRa.fail_prefix = None
        check("pending-dependents:partial-run-stops", r13["calls"]["status"] == "partial" and r13["calls"]["pending"] >= 1, str(r13["calls"]))
        check("pending-dependents:persisted", bool(Store(c2).read("pending-dependents.json")))
        r14 = run(c2)
        check("pending-dependents:next-run-recomputes-them", r14["calls"]["status"] == "ran" and any(x.startswith("crates/app/") for x in _FakeRa.seen)
              and Store(c2).read("pending-dependents.json") is None, str((r14["calls"], _FakeRa.seen)))
        # a provisional answer (a server that was still indexing) is asked again in the same run once the server has
        # resolved something; the second answer is final, so the next run reuses it instead of recomputing it cold forever
        c3 = cache + "-prov"
        _FakeRa.prov = {"crates/sys/src/lib.rs"}
        try:
            r15 = run(c3)
            seen15 = list(_FakeRa.seen)
            run(c3)
            check("provisional:retried-once-in-run", seen15.count("crates/sys/src/lib.rs") == 2 and r15["calls"].get("retried") == 1, str((seen15, r15["calls"])))
            check("provisional:retry-is-final", _FakeRa.seen == [], str(_FakeRa.seen))
        finally:
            _FakeRa.prov = set()
        # a server that never resolved anything: no retry, the answer stays provisional and the next run asks again
        c4 = cache + "-prov-cold"
        _FakeRa.prov = {"crates/sys/src/lib.rs"}
        _FakeRa._resolved = False
        try:
            run(c4)
        finally:
            _FakeRa.prov = set()
            del _FakeRa._resolved
        run(c4)
        check("provisional:cold-server-recomputed-next-run", _FakeRa.seen == ["crates/sys/src/lib.rs"], str(_FakeRa.seen))
        check("calls:ra-target-matches-module-layer", FAST_OPTIONS["cargo"]["target"] == vr_extract.CM_TARGET and FAST_OPTIONS["cargo"]["features"] == "all")
        # the module cache key covers the workspace manifest, the lockfile and target roots outside src/
        pk = {"manifest_path": "/w/crates/x/Cargo.toml", "targets": [{"src_path": "/w/crates/x/lib/main.rs"}]}
        bl = {"Cargo.toml": "a", "Cargo.lock": "b", "crates/x/Cargo.toml": "c", "crates/x/src/lib.rs": "d", "crates/x/lib/main.rs": "e"}
        k0 = ModuleCache(None, bl, "/w", "s").key(pk)
        check("module-key:covers-root-manifest-lock-and-targets",
              all(ModuleCache(None, dict(bl, **{f: "z"}), "/w", "s").key(pk) != k0 for f in ("Cargo.toml", "Cargo.lock", "crates/x/lib/main.rs")))
        # concurrent writers: unique temp names, nothing left behind
        st = Store(os.path.join(d0 + "-store"))
        st.write({"a": 1}, "x", "y.json")
        st.write({"a": 2}, "x", "y.json")
        check("store:atomic-write-leaves-no-temp", st.read("x", "y.json") == {"a": 2} and st.listdir("x") == ["y.json"], str(st.listdir("x")))
    # add_groups: big modules/calls views carry groups to fold, small ones are untouched
    def _n(i, kind="module"):
        return {"id": i, "label": i.split(":")[-1].split("::")[-1], "kind": kind, "status": "context", "evidence": []}
    mods = [_n("crate:c", "crate")] + [_n("module:c::a::m%d" % i) for i in range(9)] + [_n("module:c::b::m%d" % i) for i in range(4)] \
        + [_n("module:c::x"), _n("module:c::y"), _n("bin:c::bin::t", "bin")]
    gg = add_groups({"view": "full", "nodes": mods, "edges": []})
    gids = {x["id"] for x in gg["groups"]}
    check("groups:per-top-module-and-pool", {"group:c::a", "group:c::b", "group:other-modules", "group:bins"} <= gids, str(gids))
    check("groups:own-crate-stays-loose", "group" not in next(n for n in gg["nodes"] if n["id"] == "crate:c"))
    check("groups:every-group-cites-evidence", all(x.get("evidence") for x in gg["groups"]))
    check("groups:small-view-untouched", add_groups({"view": "full", "nodes": mods[:5], "edges": []}) .get("groups") is None)
    for sub, hit in (("cache-in-git-common-dir", True),):
        with tempfile.TemporaryDirectory() as d1:
            subprocess.run(["git", "init", "-q", d1], check=True)
            check(sub, cache_dir(os.path.realpath(d1)).endswith(os.path.join(".git", "asd-visual-review", "cache")) == hit)
    print("%s" % ("all vr_refresh selftests passed" if not fails else "FAILED: " + ", ".join(fails)))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
