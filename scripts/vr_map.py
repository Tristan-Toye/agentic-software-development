#!/usr/bin/env python3
"""Visual review: the living product map (L0) with hash-based staleness (VR-06).

    vr_map.py build REPO -o MAP.json (--features H-01,H-04 | --touched REV,REV | --all)
                         [--fresh] [--max-links 8] [--exclude GLOB ...] [--table FILE.md]
    vr_map.py check REPO MAP.json [--json]
    vr_map.py recall REPO MAP.json --changes REV1,REV2 [--expected FILE.json] [--include-tests] [--json]
    vr_map.py plan-check BASE.json PLAN.json [--json]
    vr_map.py list REPO [--dir docs/architecture] [--json]
    vr_map.py refresh REPO [--since REV] [--dir docs/architecture] [--full] [--no-calls] ...   (see vr_refresh.py)
    vr_map.py --selftest
    (every command takes --metrics PATH | --no-metrics; default REPO/.discovery/visual-review/metrics.jsonl)

The map is a change graph (references/visual-review/graph.schema.json 1.0.0): one
node of kind `feature` per feature of the repo's FEATURE-BOOK.md. A feature is linked to code
where its ID is found in the repo files (signal `text`: the ID in a comment, a doc or a
diagram). Each link is a source evidence entry `path:line` and is a span. The committed
location is `docs/architecture/product-map.json` (OQ-05 = A: the folder the anchor checker
covers). Structure is not stored in this file. Q8 said it is re-extracted fresh each run; for the
whole-repo map that is superseded (owner, 2026-11-13): `refresh` (vr_refresh.py) keeps a content-keyed
cache of the structure (modules per crate, calls per file) under the git common dir and recomputes only
what changed since the previous extraction commit; it also grows this file with `build --touched` from
that commit to HEAD. The per-dossier before-graph (vr_prepare.py) still extracts fresh, within VC-08.

First-run scope (OQ-09 = B): `build` needs a scope. `--features` and `--touched` (the features
whose ID appears in the files those changes touched, plus the features already on the map that
cite those files) grow the map per feature; an existing MAP.json is merged into, `--fresh`
replaces it. `--all` maps the whole book and organises it (`organise`): view `map`, one group per product area (an OVERVIEW.md),
`builds on` edges from the dependency-map tables, the build stage in each node's detail. The `map` budget counts the folded
first screen (areas), not the features (vr_graph.py).

Staleness record (`meta.staleness`): per element, per span: sha256 of the span's lines; for a
Rust file also the sha256 of the span with comments and spacing removed and the sha256 of the
file's public signatures; plus the extraction commit and the tool versions. `check` rereads:
  file gone                       -> STALE  "file gone"
  public signature of the file changed (Rust)  -> STALE "public signature changed"
  span hash equal                 -> fresh
  span found at another line      -> fresh, listed as moved
  Rust, only comments or spacing  -> "cosmetic change", not stale
  anything else                   -> STALE  "span changed"
The count of `check` is the banner count (VC-07); `check` writes no file (VC-08).

`recall` is the OQ-07 measure and sets no threshold. For each merged change (git diff of the
first parent), over its touched source files (Rust outside tests; --include-tests adds tests):
file recall = touched files cited by some element; unit recall = touched files whose unit (the
crate directory) holds a cited file; plus the marked elements. With --expected {rev: [IDs]} it
adds feature recall. It prints per change and in aggregate.

`plan-check BASE PLAN` (LikeC4 rule, VC-05): every `removed` element of PLAN must exist in BASE;
every `added` element must not. Exit 1 when the plan is stale.

Exit codes: 0 fine; 1 stale / plan stale / failed build; 2 bad arguments or unreadable input.
"""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import vr_graph  # noqa: E402
import vr_metrics  # noqa: E402

VERSION = "1.0.0"
DEFAULT_MAP = os.path.join("docs", "architecture", "product-map.json")
MAX_FILE = 1_000_000
EXCLUDE_DIRS = ("research", "inspiration-repos", ".discovery", "target", "releases", "node_modules", ".git", "features")
EXCLUDE_FILES = ("FEATURE-BOOK.md", "CLAUDE.md", "AGENTS.md", "Cargo.lock", "*.lock", "*.json", "*.jsonl")
NOTE_TEXT = "signal:text"


class MapError(Exception):
    """Unusable input (exit 2)."""


# --------------------------------------------------------------------- git

def _run(cmd: list, cwd: str):
    try:
        p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    except FileNotFoundError:
        return 127, "", "%s: not found" % cmd[0]
    return p.returncode, p.stdout, p.stderr


def git(repo: str, *args: str) -> str | None:
    rc, out, _ = _run(["git"] + list(args), repo)
    return out if rc == 0 else None


def head_commit(repo: str) -> str:
    out = git(repo, "rev-parse", "--short=8", "HEAD")
    return out.strip() if out and out.strip() else "unknown"


def tool_versions(repo: str) -> list:
    g = git(repo, "--version")
    return sorted([{"name": "git", "version": g.split()[-1] if g else "unknown"},
                   {"name": "python", "version": "%d.%d.%d" % sys.version_info[:3]},
                   {"name": "vr_map", "version": VERSION}], key=lambda x: x["name"])


def repo_files(repo: str) -> list:
    out = git(repo, "ls-files", "-z")
    if out is not None:
        return sorted(x for x in out.split("\0") if x)
    res = []
    for dp, dns, fns in os.walk(repo):
        dns[:] = [d for d in dns if d not in (".git", "target", "node_modules")]
        res += [os.path.relpath(os.path.join(dp, f), repo).replace(os.sep, "/") for f in fns]
    return sorted(res)


def read_lines(repo: str, rel: str):
    """Lines of a text file (list), or None when missing or unreadable."""
    try:
        with open(os.path.join(repo, rel), encoding="utf-8", errors="replace") as fh:
            return fh.read().split("\n")
    except OSError:
        return None


# --------------------------------------------------------------- features

def find_feature_book(repo: str) -> str:
    if os.path.isfile(os.path.join(repo, "FEATURE-BOOK.md")):
        return "FEATURE-BOOK.md"
    hits = [f for f in repo_files(repo) if os.path.basename(f) == "FEATURE-BOOK.md"]
    if not hits:
        raise MapError("no FEATURE-BOOK.md in %s" % repo)
    return hits[0]


_DEF = re.compile(r"^[-*]\s+\**([A-Z]{1,3}-\d{2,3})\**\s*[:—-]\s*(.+)$")


def parse_feature_book(text: str) -> dict:
    """{ID: {"label", "line"}}: the first `- ID: text` list line of each ID is its definition."""
    out = {}
    for i, raw in enumerate(text.split("\n"), 1):
        m = _DEF.match(raw.strip())
        if m and m.group(1) not in out:
            t = re.split(r"[;(.]", m.group(2), maxsplit=1)[0].strip().rstrip(",:")
            out[m.group(1)] = {"label": ("%s %s" % (m.group(1), t))[:70], "line": i}
    return out


# ------------------------------------------------------------------- hashes

def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def is_rust(path: str) -> bool:
    return path.endswith(".rs")


_BLOCK = re.compile(r"/\*.*?\*/", re.S)
_LINE = re.compile(r"//[^\n]*")
_PUB = re.compile(r'\bpub(?:\([^)]*\))?\s+(?:(?:async|unsafe|const|extern(?:\s+"[^"]*")?)\s+)*(?:fn|struct|enum|trait|type|const|static|mod|union)\s+[^{;=]*')


def strip_comments(text: str) -> str:
    return _LINE.sub("", _BLOCK.sub("", text))


def code_form(text: str) -> str:
    """Comments and spacing removed: equal forms mean a cosmetic change."""
    return re.sub(r"\s+", " ", strip_comments(text)).strip()


def signatures(lines: list) -> str:
    return sha("\n".join(re.sub(r"\s+", " ", m.strip()) for m in _PUB.findall(strip_comments("\n".join(lines)))))


def span_text(lines: list, line: int, end: int) -> str | None:
    if line < 1 or end > len(lines):
        return None
    return "\n".join(lines[line - 1:end])


def span_record(repo: str, path: str, line: int, end: int, cache: dict) -> dict | None:
    if path not in cache:
        cache[path] = read_lines(repo, path)
    lines = cache[path]
    if lines is None:
        return None
    t = span_text(lines, line, end)
    if t is None:
        return None
    rec = {"path": path, "line": line, "end_line": end, "sha256": sha(t)}
    if is_rust(path):
        rec["code_sha256"] = sha(code_form(t))
        rec["sig_sha256"] = signatures(lines)
    return rec


def judge_span(repo: str, rec: dict, cache: dict) -> tuple:
    """(state, reason): state is fresh | moved | cosmetic | stale."""
    path = rec["path"]
    if path not in cache:
        cache[path] = read_lines(repo, path)
    lines = cache[path]
    if lines is None:
        return "stale", "file gone"
    if "sig_sha256" in rec and signatures(lines) != rec["sig_sha256"]:
        return "stale", "public signature changed"
    line, end = rec["line"], rec["end_line"]
    t = span_text(lines, line, end)
    if t is not None and sha(t) == rec["sha256"]:
        return "fresh", ""
    n = end - line + 1
    for start in range(1, len(lines) - n + 2):
        if start != line and sha("\n".join(lines[start - 1:start - 1 + n])) == rec["sha256"]:
            return "moved", "moved to line %d" % start
    if "code_sha256" in rec and t is not None and sha(code_form(t)) == rec["code_sha256"]:
        return "cosmetic", "cosmetic change"
    return "stale", "span changed" if t is not None else "span changed (file now shorter than line %d)" % end


# ------------------------------------------------------------------- build

def _excluded(path: str, extra: list) -> bool:
    parts = path.split("/")
    if any(p in EXCLUDE_DIRS for p in parts[:-1]):
        return True
    base = parts[-1]
    if any(fnmatch.fnmatch(base, g) for g in EXCLUDE_FILES):
        return True
    return any(fnmatch.fnmatch(path, g) for g in extra)


def _priority(path: str) -> int:
    if is_rust(path) and "/tests/" not in path and not path.endswith("_test.rs"):
        return 0
    if not path.endswith((".md", ".mdx", ".txt")):
        return 1
    return 2 if path.startswith("docs/architecture/") else 3


def scan_ids(repo: str, ids, files: list, extra_exclude=()) -> dict:
    """{ID: [(path, line)]} for every file that is not an excluded spec or generated file."""
    if not ids:
        return {}
    rx = re.compile(r"(?<![\w-])(%s)(?![\w-])" % "|".join(re.escape(i) for i in sorted(ids, key=len, reverse=True)))
    hits: dict = {}
    for rel in files:
        if _excluded(rel, list(extra_exclude)):
            continue
        p = os.path.join(repo, rel)
        try:
            if os.path.getsize(p) > MAX_FILE:
                continue
            with open(p, "rb") as fh:
                raw = fh.read()
        except OSError:
            continue
        if b"\0" in raw:
            continue
        for i, ln in enumerate(raw.decode("utf-8", "replace").split("\n"), 1):
            if len(ln) < 3000:
                for m in rx.finditer(ln):
                    hits.setdefault(m.group(1), []).append((rel, i))
    return hits


def pick_links(hits: list, max_links: int) -> list:
    """Code first, then architecture docs; one hit per file before a second; at most 2 per file."""
    ordered = sorted(set(hits), key=lambda h: (_priority(h[0]), h[0], h[1]))
    chosen, per = [], {}
    for rounds in (0, 1):
        for h in ordered:
            if len(chosen) >= max_links:
                break
            if per.get(h[0], 0) == rounds and h not in chosen:
                chosen.append(h)
                per[h[0]] = per.get(h[0], 0) + 1
    return sorted(chosen)


def touched_files(repo: str, rev: str) -> list:
    out = git(repo, "diff", "--name-only", "%s^1" % rev, rev)
    if out is None:
        raise MapError("cannot diff %s^1..%s in %s" % (rev, rev, repo))
    return [x for x in out.split("\n") if x]


def build(repo: str, features=None, touched=None, all_features=False, existing: dict | None = None,
          max_links: int = 8, exclude=()) -> dict:
    repo = os.path.abspath(repo)
    book = find_feature_book(repo)
    lines = read_lines(repo, book)
    defs = parse_feature_book("\n".join(lines or []))
    if not defs:
        raise MapError("%s defines no feature (`- ID: text` list lines)" % book)
    files = repo_files(repo)
    if all_features:
        want = set(defs)
    else:
        want = set()
        for f in features or []:
            if f not in defs:
                raise MapError("feature %s is not defined in %s" % (f, book))
            want.add(f)
        if touched:
            tf = sorted({p for rev in touched for p in touched_files(repo, rev)})
            present = [p for p in tf if os.path.isfile(os.path.join(repo, p))]
            for fid in scan_ids(repo, defs.keys(), present, exclude):
                want.add(fid)
            if existing:
                for n in existing.get("nodes", []):
                    if n.get("kind") == "feature" and any(e.get("path") in tf for e in n.get("evidence", [])):
                        want.add(n["id"].split(":", 1)[1])
    if not want:
        raise MapError("no feature selected: give --features, --touched (a change that mentions a feature ID) or --all")
    hits = scan_ids(repo, want, files, exclude)
    nodes, staleness, cache = {}, {}, {}
    if existing:
        for n in existing.get("nodes", []):
            nodes[n["id"]] = n
        staleness = dict(existing.get("meta", {}).get("staleness", {}).get("elements", {}))
    for fid in sorted(want):
        d = defs[fid]
        ev = [{"tier": "source", "path": book, "line": d["line"], "note": "definition"}]
        for p, ln in pick_links(hits.get(fid, []), max_links):
            ev.append({"tier": "source", "path": p, "line": ln, "note": NOTE_TEXT})
        spans = []
        for e in ev:
            rec = span_record(repo, e["path"], e["line"], e["line"], cache)
            if rec:
                spans.append(rec)
        node = {"id": "feature:" + fid, "label": d["label"], "kind": "feature", "status": "context",
                "label_source": "extractor", "evidence": ev}
        if len(ev) == 1:
            node["resolution"] = "inferred"
            node["evidence"].append({"tier": "inferred", "note": "no code or doc outside the spec mentions %s" % fid})
        else:
            node["resolution"] = "inferred"  # linked by the ID text only; no tool proved the link
        nodes["feature:" + fid] = node
        staleness["feature:" + fid] = {"spans": spans}
    tools = tool_versions(repo)
    ordered = [nodes[k] for k in sorted(nodes)]
    g = {"schema_version": "1.0.0", "view": "L0", "title": "Product map of %s" % os.path.basename(repo),
         "meta": {"repo": os.path.basename(repo), "commit": head_commit(repo), "generated_by": "extractor", "tools": tools,
                  "scope": "all" if all_features else "per-feature (OQ-09)",
                  "staleness": {"algorithm": "sha256 of the span lines; rust: also comment-free span and public-signature hashes",
                                "extraction_commit": head_commit(repo), "tools": tools, "elements": staleness}},
         "nodes": ordered, "edges": []}
    if all_features or (existing and existing.get("view") == "map"):
        organise(repo, g)
    return g


# ----------------------------------------------------- areas, dependencies, stages

_ID = re.compile(r"\b([A-Z]{1,3}-\d{2,3})\b")
_AREA_HEAD = re.compile(r"^##\s+([A-Z]{1,3})-\d{2,3}\b")
_STAGE = re.compile(r"^###\s+Stage\s+(\d+)\s*[—–:-]+\s*(.+?)\s*$")
EDGE_LABEL = "builds on"
OTHER_AREA = "area:other"


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "area"


def parse_overview(text: str) -> dict:
    """One OVERVIEW.md: {"label", "label_line", "prefixes", "deps"}. `label` is the first heading up to its first dash
    ("# Mesh - sub-feature overview" is "Mesh"); `prefixes` are the ID prefixes of its "## ID - title" sections; `deps` are the rows
    of its "## Dependency map" table as (subject ID, [IDs the Needs cell names, in order], line). Text in a Needs cell that is not a
    feature ID (a service, a pillar number) is not an edge and is dropped."""
    label, label_line, prefixes, deps, in_dep = "", 0, [], [], False
    for i, raw in enumerate(text.split("\n"), 1):
        line = raw.rstrip()
        if line.startswith("# ") and not label:
            label, label_line = re.split(r"\s+[—–-]+\s+", line[2:].strip(), maxsplit=1)[0].strip(), i
            continue
        m = _AREA_HEAD.match(line)
        if m and m.group(1) not in prefixes:
            prefixes.append(m.group(1))
        if line.startswith("## "):
            in_dep = line[3:].strip().lower().startswith("dependency map")
            continue
        if in_dep and line.startswith("|"):
            cells = [c.strip() for c in line.strip("|").split("|")]
            first = _ID.search(cells[0]) if cells else None
            if len(cells) < 2 or not first:
                continue  # the header row, the --- row, a row with no subject ID
            needs = []
            for x in _ID.findall(cells[1]):
                if x != first.group(1) and x not in needs:
                    needs.append(x)
            deps.append((first.group(1), needs, i))
    return {"label": label, "label_line": label_line, "prefixes": prefixes, "deps": deps}


def parse_stages(text: str) -> dict:
    """{ID: {"stage": N, "name", "line"}} from the "### Stage N - name" sections of a FEATURE-BOOK: the IDs a section's list
    lines open with (`- H-01: ...`). The first stage that lists an ID wins."""
    out, cur = {}, None
    for i, raw in enumerate(text.split("\n"), 1):
        line = raw.strip()
        m = _STAGE.match(line)
        if m:
            cur = {"stage": int(m.group(1)), "name": m.group(2)}
            continue
        if line.startswith("#"):
            cur = None
        elif cur and line[:1] in "-*":
            for x in _ID.findall(re.split(r"[:—]", line.lstrip("-* ").replace("**", ""), maxsplit=1)[0]):
                out.setdefault(x, dict(cur, line=i))
    return out


def organise(repo: str, g: dict) -> dict:
    """Make the flat feature map of `build` a readable whole-repo map (view "map"), in place, and return it.
    Areas: each OVERVIEW.md that lists "## ID - title" sections is one product area, labelled by its title; a feature whose ID
    prefix no OVERVIEW claims goes in "Other". Edges: one "builds on" edge per feature ID in a Needs cell of a dependency-map row,
    with the row (path:line) as evidence. Stage: the FEATURE-BOOK's "### Stage N" section of each feature, in its detail.
    Idempotent: groups, edges and details are rebuilt from the repo's files on every call."""
    repo = os.path.abspath(repo)
    files = repo_files(repo)
    nodes = {n["id"].split(":", 1)[1]: n for n in g["nodes"] if n.get("kind") == "feature"}
    areas, owner = [], {}
    for rel in files:
        if os.path.basename(rel) != "OVERVIEW.md":
            continue
        ov = parse_overview("\n".join(read_lines(repo, rel) or []))
        mine = [p for p in ov["prefixes"] if p not in owner]
        if not mine:
            continue
        label = ov["label"] or os.path.basename(os.path.dirname(rel)).replace("-", " ").title()
        a = {"id": "area:" + _slug(label), "label": label, "path": rel, "line": ov["label_line"] or 1, "deps": ov["deps"]}
        areas.append(a)
        for pre in mine:
            owner[pre] = a
    try:
        book = find_feature_book(repo)
    except MapError:
        book = None
    stages = parse_stages("\n".join(read_lines(repo, book) or [])) if book else {}
    used = {}
    for fid in sorted(nodes):
        a = owner.get(fid.split("-", 1)[0])
        if a is None:
            a = used.setdefault(OTHER_AREA, {"id": OTHER_AREA, "label": "Other", "path": book or "", "line": 1, "deps": []})
        else:
            used[a["id"]] = a
        nodes[fid]["group"] = a["id"]
    edges, seen = [], set()
    for a in areas:
        for subj, needs, line in a["deps"]:
            for need in needs:
                if subj in nodes and need in nodes and (subj, need) not in seen:
                    seen.add((subj, need))
                    edges.append({"id": "edge:builds-on:feature:%s:feature:%s" % (subj, need), "source": "feature:" + subj,
                                  "target": "feature:" + need, "kind": "depends", "label": EDGE_LABEL, "status": "context",
                                  "resolution": "static", "evidence": [{"tier": "source", "path": a["path"], "line": line,
                                                                       "note": "dependency map row of %s" % subj}]})
    needs_of = {}
    for e in edges:
        needs_of.setdefault(e["source"], []).append(e["target"].split(":", 1)[1])
    for fid, n in nodes.items():
        st = stages.get(fid)
        parts = []
        if st:
            parts.append("Build stage %d: %s." % (st["stage"], st["name"]))
        parts.append("Area: %s." % used[n["group"]]["label"])
        parts.append("Builds on: %s." % (", ".join(needs_of[n["id"]]) if n["id"] in needs_of else "nothing else in the map"))
        n["detail"] = {"after": " ".join(parts)}
    groups = []
    for gid in sorted(used):
        a = used[gid]
        gr = {"id": gid, "label": a["label"], "status": "context", "label_source": "extractor"}
        if a["path"]:
            gr["evidence"] = [{"tier": "source", "path": a["path"], "line": a["line"], "note": "area overview"}]
        groups.append(gr)
    # staleness: the area headings and the dependency rows are spans too (a moved or edited row makes the map stale)
    els = {k: v for k, v in g["meta"]["staleness"]["elements"].items() if not k.startswith(("edge:", "area:"))}
    cache = {}
    for el in groups + edges:
        spans = [r for r in (span_record(repo, ev["path"], ev["line"], ev["line"], cache) for ev in el.get("evidence", [])) if r]
        els[el["id"]] = {"spans": spans}
    g["meta"]["staleness"]["elements"] = els
    inbound = {}
    for e in edges:
        sa, ta = nodes[e["source"].split(":", 1)[1]]["group"], nodes[e["target"].split(":", 1)[1]]["group"]
        if sa != ta:
            inbound[ta] = inbound.get(ta, 0) + 1
    label_of = {x["id"]: x["label"] for x in groups}
    foundation = max(sorted(inbound), key=lambda k: inbound[k]) if inbound else None
    stage_n = len({stages[f]["stage"] for f in nodes if f in stages})
    title = "%d features in %d product areas" % (len(nodes), len(groups))
    if stage_n:
        title += ", built in %d stages" % stage_n
    if foundation:
        title += "; the other areas build mostly on %s" % label_of[foundation]
    g["view"], g["title"], g["groups"], g["edges"] = "map", title[:100], groups, edges
    g["meta"]["scope"] = "all"
    return g


def table_md(g: dict) -> str:
    """The feature-to-code table as Markdown (the anchor checker reads `crates/..rs:LINE` in it)."""
    rows = ["# Feature to code", "", "Generated by `vr_map.py build --table`. Signal `text`: the feature ID is written at the place.",
            "One row per link, no backticked name after the anchor, so the anchor checker tests only that file and line exist.", "",
            "| Feature | Evidence | Signal |", "|---|---|---|"]
    for n in g["nodes"]:
        ev = ["%s:%d" % (e["path"], e["line"]) for e in n["evidence"] if e.get("tier") == "source" and e.get("note") != "definition"]
        rows += ["| %s | %s | text |" % (n["label"], x) for x in ev] or ["| %s | none | - |" % n["label"]]
    return "\n".join(rows) + "\n"


# ------------------------------------------------------------------- check

def check(repo: str, g: dict) -> dict:
    repo = os.path.abspath(repo)
    els = g.get("meta", {}).get("staleness", {}).get("elements")
    if els is None:
        raise MapError("the map has no meta.staleness record")
    cache, stale, cosmetic, moved, fresh = {}, [], [], [], 0
    for nid in sorted(els):
        reasons, kinds = [], set()
        for rec in els[nid].get("spans", []):
            state, why = judge_span(repo, rec, cache)
            kinds.add(state)
            if state != "fresh":
                reasons.append({"path": rec["path"], "line": rec["line"], "state": state, "reason": why})
        if "stale" in kinds:
            stale.append({"id": nid, "reasons": [r for r in reasons if r["state"] == "stale"]})
        elif "cosmetic" in kinds:
            cosmetic.append({"id": nid, "reasons": [r for r in reasons if r["state"] == "cosmetic"]})
        elif "moved" in kinds:
            moved.append({"id": nid, "reasons": [r for r in reasons if r["state"] == "moved"]})
        else:
            fresh += 1
    ex = g.get("meta", {}).get("staleness", {}).get("extraction_commit")
    n = len(stale)
    return {"elements": len(els), "stale": stale, "cosmetic": cosmetic, "moved": moved, "fresh": fresh, "count": n,
            "banner": ("%d stale element%s: %s" % (n, "" if n == 1 else "s", ", ".join(s["id"] for s in stale))) if n else "",
            "extraction_commit": ex, "head": head_commit(repo)}


def render_check(r: dict) -> str:
    out = ["elements: %d  fresh: %d  moved: %d  cosmetic: %d  stale: %d" % (r["elements"], r["fresh"], len(r["moved"]), len(r["cosmetic"]), r["count"]),
           "extraction commit %s, repo HEAD %s" % (r["extraction_commit"], r["head"])]
    for s in r["stale"]:
        for x in s["reasons"]:
            out.append("STALE %s  %s:%d  %s" % (s["id"], x["path"], x["line"], x["reason"]))
    for s in r["cosmetic"]:
        out.append("cosmetic %s  %s" % (s["id"], "; ".join("%s:%d" % (x["path"], x["line"]) for x in s["reasons"])))
    for s in r["moved"]:
        out.append("moved %s  %s" % (s["id"], "; ".join("%s:%d %s" % (x["path"], x["line"], x["reason"]) for x in s["reasons"])))
    if r["banner"]:
        out.append("BANNER: " + r["banner"])
    return "\n".join(out)


# ------------------------------------------------------------------ recall

def unit_of(path: str) -> str:
    """The crate-like unit of a path: the part before /src/ or /tests/, else the directory."""
    m = re.match(r"^(.*?)/(?:src|tests|benches|examples)/", path)
    return m.group(1) if m else (os.path.dirname(path) or ".")


def is_source(path: str, include_tests: bool) -> bool:
    if not is_rust(path):
        return False
    if include_tests:
        return True
    return "/tests/" not in path and not path.endswith("_test.rs") and "/benches/" not in path


def recall(repo: str, g: dict, revs: list, expected: dict | None = None, include_tests: bool = False) -> dict:
    repo = os.path.abspath(repo)
    cited: dict = {}  # path -> {element ids}
    for n in g.get("nodes", []):
        for e in n.get("evidence", []):
            if e.get("tier") == "source" and e.get("note") != "definition":
                cited.setdefault(e["path"], set()).add(n["id"])
    units: dict = {}
    for p, ids in cited.items():
        units.setdefault(unit_of(p), set()).update(ids)
    rows = []
    for rev in revs:
        try:
            files = [f for f in touched_files(repo, rev) if is_source(f, include_tests)]
        except MapError as exc:
            raise MapError(str(exc))
        subj = (git(repo, "log", "-1", "--format=%s", rev) or "").strip()
        fcov = [f for f in files if f in cited]
        ucov = [f for f in files if unit_of(f) in units]
        marked_f = sorted({i for f in files for i in cited.get(f, ())})
        marked_u = sorted({i for f in files for i in units.get(unit_of(f), ())})
        row = {"rev": rev, "subject": subj, "touched_source_files": len(files), "file_covered": len(fcov), "unit_covered": len(ucov),
               "file_recall": (len(fcov) / len(files)) if files else None, "unit_recall": (len(ucov) / len(files)) if files else None,
               "marked_by_file": marked_f, "marked_by_unit": marked_u,
               "uncovered_files": sorted(set(files) - set(fcov))}
        if expected and rev in expected:
            exp = set(expected[rev])
            row["expected_features"] = sorted(exp)
            row["feature_recall_by_file"] = len(exp & {m.split(":", 1)[1] for m in marked_f}) / len(exp) if exp else None
            row["feature_recall_by_unit"] = len(exp & {m.split(":", 1)[1] for m in marked_u}) / len(exp) if exp else None
        rows.append(row)
    tf = sum(r["touched_source_files"] for r in rows)
    agg = {"changes": len(rows), "touched_source_files": tf,
           "file_recall": (sum(r["file_covered"] for r in rows) / tf) if tf else None,
           "unit_recall": (sum(r["unit_covered"] for r in rows) / tf) if tf else None,
           "mean_file_recall": _mean([r["file_recall"] for r in rows]),
           "mean_unit_recall": _mean([r["unit_recall"] for r in rows]),
           "changes_with_a_marked_element_by_file": sum(1 for r in rows if r["marked_by_file"]),
           "changes_with_a_marked_element_by_unit": sum(1 for r in rows if r["marked_by_unit"]),
           "threshold": None}
    return {"per_change": rows, "aggregate": agg, "elements": len(g.get("nodes", [])), "map_commit": g.get("meta", {}).get("commit")}


def _mean(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def _pct(x):
    return "n/a" if x is None else "%.0f%%" % (100 * x)


def render_recall(r: dict) -> str:
    out = ["map: %d elements, built at %s. No threshold is set (OQ-07)." % (r["elements"], r["map_commit"])]
    for c in r["per_change"]:
        out.append("%s  %s" % (c["rev"], c["subject"]))
        out.append("   touched source files %d: file recall %d/%d (%s), unit recall %d/%d (%s); elements marked by file %d, by unit %d"
                   % (c["touched_source_files"], c["file_covered"], c["touched_source_files"], _pct(c["file_recall"]),
                      c["unit_covered"], c["touched_source_files"], _pct(c["unit_recall"]), len(c["marked_by_file"]), len(c["marked_by_unit"])))
        if "expected_features" in c:
            out.append("   feature recall by file %s, by unit %s (expected %s)" % (_pct(c["feature_recall_by_file"]), _pct(c["feature_recall_by_unit"]), ", ".join(c["expected_features"])))
    a = r["aggregate"]
    out.append("aggregate over %d changes, %d touched source files: file recall %s, unit recall %s (mean per change: %s, %s); changes with a marked element: %d by file, %d by unit"
               % (a["changes"], a["touched_source_files"], _pct(a["file_recall"]), _pct(a["unit_recall"]), _pct(a["mean_file_recall"]),
                  _pct(a["mean_unit_recall"]), a["changes_with_a_marked_element_by_file"], a["changes_with_a_marked_element_by_unit"]))
    return "\n".join(out)


# -------------------------------------------------------------- plan check

def plan_check(base: dict, plan: dict) -> dict:
    """LikeC4 rule: a removed element must exist in the base, an added element must not."""
    have = {x["id"] for k in ("nodes", "edges", "groups") for x in base.get(k, []) or []}
    bad = []
    for k in ("nodes", "edges", "groups"):
        for x in plan.get(k, []) or []:
            if x.get("status") == "removed" and x["id"] not in have:
                bad.append({"id": x["id"], "problem": "removed element is not in the base"})
            if x.get("status") == "added" and x["id"] in have:
                bad.append({"id": x["id"], "problem": "added element is already in the base"})
    return {"stale": bool(bad), "problems": bad}


# -------------------------------------------------------------------- list

GRAPH_KIND_ORDER = ("product-map", "crate-map", "crate-calls", "modules", "calls")


def graph_kind(fname: str, g: dict) -> str:
    """The kind of a committed graph view: product-map, crate-map, crate-calls, modules, calls (by the file name vr_refresh gives
    it); the whole-repo `map` view is the product map; any other graph keeps its view name."""
    stem = fname[:-5] if fname.endswith(".json") else fname
    if stem in ("product-map", "crate-map", "crate-calls") or stem.startswith(("modules-", "calls-")):
        return stem.split("-", 1)[0] if stem.startswith(("modules-", "calls-")) else stem
    return "product-map" if g.get("view") == "map" else "graph"


def list_diagrams(repo: str, rel_dir: str = "docs/architecture") -> dict:
    d = os.path.join(repo, rel_dir)
    entries = []
    if os.path.isdir(d):
        for f in sorted(os.listdir(d)):
            p = os.path.join(d, f)
            if not os.path.isfile(p):
                continue
            rel = "%s/%s" % (rel_dir, f)
            if f.endswith(".json"):
                try:
                    with open(p, encoding="utf-8") as fh:
                        g = json.load(fh)
                except (OSError, ValueError):
                    continue
                if isinstance(g, dict) and g.get("view") in ("L0", "L1", "L2", "map", "full") and "nodes" in g:
                    entries.append({"path": rel, "kind": graph_kind(f, g), "view": g["view"], "title": g.get("title", f)})
            elif f.endswith(".md"):
                with open(p, encoding="utf-8", errors="replace") as fh:
                    text = fh.read()
                if "```mermaid" in text:
                    m = re.match(r"^(c[1-4]|seq|threat-model)", f)
                    title = next((x[2:].strip() for x in text.split("\n") if x.startswith("# ")), f)
                    entries.append({"path": rel, "kind": m.group(1) if m else "diagram", "title": title})
    # the product map first, then the other committed views by kind, then the mermaid diagrams (each group in file order)
    entries.sort(key=lambda e: GRAPH_KIND_ORDER.index(e["kind"]) if e["kind"] in GRAPH_KIND_ORDER else len(GRAPH_KIND_ORDER))
    has_map = any(e["kind"] == "product-map" for e in entries)
    return {"dir": rel_dir, "diagrams": entries, "has_map": has_map,
            "message": "" if has_map else "no committed product map yet in %s" % rel_dir}


# ---------------------------------------------------------------- CLI glue

def _load(path: str) -> dict:
    try:
        return vr_graph.load(path)
    except (OSError, ValueError) as exc:
        raise MapError("cannot read %s: %s" % (path, exc))


def _metric(a, repo, event, fields):
    if not getattr(a, "no_metrics", False):
        vr_metrics.log(os.path.abspath(repo), "vr_map", event, fields, getattr(a, "metrics", None))


def _cmd_build(a) -> int:
    t0 = time.time()
    out = a.output or os.path.join(a.repo, DEFAULT_MAP)
    existing = None
    if os.path.isfile(out) and not a.fresh:
        existing = _load(out)
    feats = [x for x in (a.features or "").split(",") if x.strip()]
    revs = [x for x in (a.touched or "").split(",") if x.strip()]
    g = build(a.repo, feats, revs, a.all, existing, a.max_links, a.exclude or [])
    issues = vr_graph.validate(g, root=os.path.abspath(a.repo), check_budget=False)
    if issues:
        print("vr_map: the built map is invalid: %s" % json.dumps(issues[:5]), file=sys.stderr)
        return 1
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(g, indent=1, ensure_ascii=False) + "\n")
    if a.table:
        with open(a.table, "w", encoding="utf-8") as fh:
            fh.write(table_md(g))
    b = vr_graph.budget(g)
    nodes = g["nodes"]
    linked = sum(1 for n in nodes if any(e.get("note") == NOTE_TEXT for e in n["evidence"]))
    ev = sum(len(n["evidence"]) for n in nodes)
    spans = sum(len(v["spans"]) for v in g["meta"]["staleness"]["elements"].values())
    secs = round(time.time() - t0, 2)
    print("wrote %s: %d features, %d with a code or doc link, %d evidence entries, %d spans hashed, %.2fs" % (out, len(nodes), linked, ev, spans, secs))
    if b["over"]:
        print("warning: %d features exceed the L0 budget of %d; VR-05 picks the view" % (b["used"], b["limit"]), file=sys.stderr)
    _metric(a, a.repo, "build", {"features": len(nodes), "linked_features": linked, "evidence_entries": ev, "spans": spans,
                                  "inferred_share": round(sum(1 for n in nodes if n.get("resolution") == "inferred") / len(nodes), 3),
                                  "evidence_valid": True, "over_budget": b["over"], "seconds": secs})
    return 0


def _cmd_check(a) -> int:
    t0 = time.time()
    r = check(a.repo, _load(a.map))
    secs = round(time.time() - t0, 2)
    print(json.dumps(r, indent=1) if a.json else render_check(r))
    _metric(a, a.repo, "check", {"elements": r["elements"], "stale": r["count"], "cosmetic": len(r["cosmetic"]), "moved": len(r["moved"]), "seconds": secs})
    return 1 if r["count"] else 0


def _cmd_recall(a) -> int:
    t0 = time.time()
    exp = None
    if a.expected:
        try:
            with open(a.expected, encoding="utf-8") as fh:
                exp = json.load(fh)
        except (OSError, ValueError) as exc:
            raise MapError("cannot read --expected: %s" % exc)
    r = recall(a.repo, _load(a.map), [x for x in a.changes.split(",") if x.strip()], exp, a.include_tests)
    print(json.dumps(r, indent=1) if a.json else render_recall(r))
    ag = r["aggregate"]
    _metric(a, a.repo, "recall", {"changes": ag["changes"], "touched_source_files": ag["touched_source_files"], "file_recall": ag["file_recall"],
                                   "unit_recall": ag["unit_recall"], "threshold": None, "seconds": round(time.time() - t0, 2)})
    return 0


def _cmd_plan(a) -> int:
    r = plan_check(_load(a.base), _load(a.plan))
    if a.json:
        print(json.dumps(r, indent=1))
    else:
        print("plan STALE" if r["stale"] else "plan ok")
        for p in r["problems"]:
            print("  %s: %s" % (p["id"], p["problem"]))
    return 1 if r["stale"] else 0


def _cmd_list(a) -> int:
    r = list_diagrams(os.path.abspath(a.repo), a.dir)
    if a.json:
        print(json.dumps(r, indent=1))
    else:
        for e in r["diagrams"]:
            print("%-14s %s  %s" % (e["kind"], e["path"], e["title"]))
        print(r["message"] or "%d diagrams, product map present" % len(r["diagrams"]))
    return 0


# ---------------------------------------------------------------- selftest

def _selftest() -> int:
    fails = []

    def ok(name, cond, detail: object = ""):
        if not cond:
            fails.append("%s %s" % (name, detail))

    def w(root, rel, text):
        p = os.path.join(root, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w") as fh:
            fh.write(text)

    def g(root, *args):
        rc, out, err = _run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false"] + list(args), root)
        assert rc == 0, (args, err)
        return out

    def run(*argv):
        import contextlib
        import io
        o, e = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(o), contextlib.redirect_stderr(e):
            rc = main(list(argv))
        return rc, o.getvalue(), e.getvalue()

    book = ("# Book\n\nIntro: H-99 is not a feature here.\n\n## Order\n\n- H-01: tool layer core; with checks.\n"
            "- H-02: reader (contract views).\n- PF-01: ops plane.\n\n## Open\n\n- H-01: the cap value.\n")
    lib = ("//! crate a\n// H-01 tool layer starts here\npub fn run(x: u32) -> u32 {\n    x + 1\n}\n\n"
           "/// H-02 reader\npub struct Reader {\n    pub n: u8,\n}\n\nfn private() {}\n")
    with tempfile.TemporaryDirectory() as d:
        repo = os.path.join(d, "repo")
        os.makedirs(repo)
        g(repo, "init", "-q")
        w(repo, "FEATURE-BOOK.md", book)
        w(repo, "crates/a/src/lib.rs", lib)
        w(repo, "crates/a/src/other.rs", "pub fn other() {}\n")
        w(repo, "crates/b/src/lib.rs", "pub fn b() {}\n")
        w(repo, "docs/architecture/c2-containers.md", "# c2\n```mermaid\nflowchart LR\n  a[\"a\"]\n```\nH-01 note\n")
        w(repo, "research/notes.md", "H-01 and H-02 and PF-01 live here\n")
        w(repo, "config/features/x/README.md", "H-02 spec\n")
        g(repo, "add", "-A")
        g(repo, "commit", "-qm", "base")
        base_rev = g(repo, "rev-parse", "HEAD").strip()

        # feature book parse: first list line per ID wins; H-99 (prose) is no feature
        defs = parse_feature_book(book)
        ok("book ids", sorted(defs) == ["H-01", "H-02", "PF-01"], sorted(defs))
        ok("book label", defs["H-01"]["label"] == "H-01 tool layer core" and defs["H-01"]["line"] == 7, defs["H-01"])

        # OQ-09: a scope is required; --features builds only those
        mp = os.path.join(d, "map.json")
        rc, _, err = run("build", repo, "-o", mp, "--no-metrics")
        ok("no scope refused", rc == 2 and "no feature selected" in err, (rc, err))
        rc, out, _ = run("build", repo, "-o", mp, "--features", "H-01", "--no-metrics")
        m = json.load(open(mp))
        ok("build ok", rc == 0 and [n["id"] for n in m["nodes"]] == ["feature:H-01"], (rc, out))
        ok("view L0", m["view"] == "L0")
        paths = {(e["path"], e.get("line")) for e in m["nodes"][0]["evidence"] if e["tier"] == "source"}
        ok("code link", ("crates/a/src/lib.rs", 2) in paths, paths)
        ok("doc link", ("docs/architecture/c2-containers.md", 6) in paths, paths)
        ok("definition link", ("FEATURE-BOOK.md", 7) in paths, paths)
        ok("spec dirs excluded", not any(p.startswith(("research/", "config/features/")) for p, _ in paths), paths)
        ok("validates with root", vr_graph.validate(m, root=repo, check_budget=False) == [])
        # VC-09: tool versions and extraction commit in the record
        st = m["meta"]["staleness"]
        ok("VC-09 commit", st["extraction_commit"] == base_rev[:8], st["extraction_commit"])
        ok("VC-09 tools", {t["name"] for t in st["tools"]} >= {"git", "python", "vr_map"})
        # merge: a second build adds a feature and keeps the first
        rc, _, _ = run("build", repo, "-o", mp, "--features", "H-02", "--no-metrics")
        m = json.load(open(mp))
        ok("merge keeps", [n["id"] for n in m["nodes"]] == ["feature:H-01", "feature:H-02"] and len(m["meta"]["staleness"]["elements"]) == 2)
        rc, _, _ = run("build", repo, "-o", mp, "--features", "H-02", "--fresh", "--no-metrics")
        ok("fresh replaces", [n["id"] for n in json.load(open(mp))["nodes"]] == ["feature:H-02"])
        rc, out, err = run("build", repo, "-o", mp, "--all", "--fresh", "--no-metrics")
        m = json.load(open(mp))
        pf = next(n for n in m["nodes"] if n["id"] == "feature:PF-01")
        ok("all + unlinked feature is inferred", rc == 0 and len(m["nodes"]) == 3 and pf["evidence"][-1]["tier"] == "inferred", (rc, err))
        ok("unlinked still validates", vr_graph.validate(m, root=repo, check_budget=False) == [])
        rc, _, _ = run("build", repo, "-o", mp, "--features", "ZZ-01", "--fresh", "--no-metrics")
        ok("unknown feature refused", rc == 2)
        # table for the anchor checker
        tbl = os.path.join(d, "t.md")
        run("build", repo, "-o", mp, "--features", "H-01", "--fresh", "--table", tbl, "--no-metrics")
        ok("table has the anchor", "| H-01 tool layer core | crates/a/src/lib.rs:2 | text |" in open(tbl).read())

        # ---- check
        def snapshot():
            s = {}
            for dp, dn, fn in os.walk(repo):
                dn[:] = [x for x in dn if x != ".git"]
                for f in fn:
                    s[os.path.join(dp, f)] = open(os.path.join(dp, f), "rb").read()
            return s
        run("build", repo, "-o", mp, "--all", "--fresh", "--no-metrics")
        before = snapshot()
        rc, out, _ = run("check", repo, mp, "--no-metrics")
        ok("fresh exit 0", rc == 0 and "stale: 0" in out, out)
        ok("VC-08 check writes nothing", snapshot() == before)
        # S2 / VC-03: comment-only edits (the ID comment line, a doc comment, a spacing change)
        w(repo, "crates/a/src/lib.rs", lib.replace("// H-01 tool layer starts here", "// H-01 tool layer begins here, reworded").replace("/// H-02 reader", "/// H-02 reader, now documented").replace("x + 1", "x  +  1"))
        rc, out, _ = run("check", repo, mp, "--no-metrics", "--json")
        r = json.loads(out)
        ok("VC-03 cosmetic not stale", rc == 0 and r["count"] == 0 and {c["id"] for c in r["cosmetic"]} >= {"feature:H-01"}, out[:400])
        # S3 / VC-04: a public signature change
        w(repo, "crates/a/src/lib.rs", lib.replace("pub fn run(x: u32) -> u32", "pub fn run(x: u64) -> u64"))
        rc, out, _ = run("check", repo, mp, "--no-metrics", "--json")
        r = json.loads(out)
        ok("VC-04 signature stale", rc == 1 and {s["id"] for s in r["stale"]} == {"feature:H-01", "feature:H-02"}
           and any(x["reason"] == "public signature changed" for s in r["stale"] for x in s["reasons"]), out[:500])
        # a private edit of the span itself: span changed
        w(repo, "crates/a/src/lib.rs", lib.replace("// H-01 tool layer starts here", "let h_01 = tool_layer(); // H-01 changed"))
        rc, out, _ = run("check", repo, mp, "--no-metrics", "--json")
        r = json.loads(out)
        ok("span changed", rc == 1 and any(x["reason"] == "span changed" for s in r["stale"] for x in s["reasons"]), out[:400])
        # moved: three lines inserted above, nothing else
        w(repo, "crates/a/src/lib.rs", "// pad\n// pad\n// pad\n" + lib)
        rc, out, _ = run("check", repo, mp, "--no-metrics", "--json")
        r = json.loads(out)
        ok("moved not stale", rc == 0 and {x["id"] for x in r["moved"]} >= {"feature:H-01"}, out[:400])
        # file gone + markdown edit; VC-07 banner count equals the printed count
        w(repo, "crates/a/src/lib.rs", lib)
        os.remove(os.path.join(repo, "crates/a/src/lib.rs"))
        w(repo, "docs/architecture/c2-containers.md", "# c2\n```mermaid\nflowchart LR\n```\nH-01 note edited\n")
        rc, out, _ = run("check", repo, mp, "--no-metrics")
        ok("file gone", "file gone" in out and rc == 1, out)
        r = check(repo, json.load(open(mp)))
        m = re.search(r"BANNER: (\d+) stale", out)
        ok("VC-07 banner = count", m and int(m.group(1)) == r["count"] == len(r["stale"]) and f"stale: {r['count']}" in out, out)
        ok("banner lists ids", all(s["id"] in r["banner"] for s in r["stale"]))
        w(repo, "crates/a/src/lib.rs", lib)
        w(repo, "docs/architecture/c2-containers.md", "# c2\n```mermaid\nflowchart LR\n  a[\"a\"]\n```\nH-01 note\n")
        rc, out, _ = run("check", repo, mp, "--no-metrics")
        ok("restored fresh", rc == 0, out)
        ok("check bad map exit 2", run("check", repo, os.path.join(d, "nope.json"), "--no-metrics")[0] == 2)

        # ---- recall over two changes
        run("build", repo, "-o", mp, "--all", "--fresh", "--no-metrics")
        g(repo, "add", "-A")
        g(repo, "commit", "-qm", "map base", "--allow-empty")
        w(repo, "crates/a/src/lib.rs", lib + "// more\n")
        w(repo, "crates/a/src/other.rs", "pub fn other() {}\npub fn two() {}\n")
        w(repo, "crates/b/src/lib.rs", "pub fn b() {}\npub fn bb() {}\n")
        w(repo, "crates/a/tests/t.rs", "fn t() {}\n")
        w(repo, "README.md", "doc\n")
        g(repo, "add", "-A")
        g(repo, "commit", "-qm", "change one")
        c1 = g(repo, "rev-parse", "HEAD").strip()
        w(repo, "crates/b/src/lib.rs", "pub fn b() {}\npub fn bb() {}\npub fn bbb() {}\n")
        g(repo, "add", "-A")
        g(repo, "commit", "-qm", "change two")
        c2 = g(repo, "rev-parse", "HEAD").strip()
        mm = json.load(open(mp))
        r = recall(repo, mm, [c1, c2])
        p1, p2 = r["per_change"]
        ok("recall change1 files", p1["touched_source_files"] == 3 and p1["file_covered"] == 1 and p1["unit_covered"] == 2, p1)
        ok("recall change1 marked", p1["marked_by_file"] == ["feature:H-01", "feature:H-02"], p1["marked_by_file"])
        ok("recall change2 zero", p2["touched_source_files"] == 1 and p2["file_covered"] == 0 and p2["unit_covered"] == 0 and p2["file_recall"] == 0.0, p2)
        ag = r["aggregate"]
        ok("aggregate", ag["touched_source_files"] == 4 and ag["file_recall"] == 0.25 and ag["unit_recall"] == 0.5 and ag["threshold"] is None, ag)
        ok("tests only with the flag", recall(repo, mm, [c1], include_tests=True)["per_change"][0]["touched_source_files"] == 4)
        ex = {c1: ["H-01", "PF-01"]}
        r = recall(repo, mm, [c1], ex)
        ok("feature recall", r["per_change"][0]["feature_recall_by_file"] == 0.5, r["per_change"][0])
        ep = os.path.join(d, "exp.json")
        json.dump(ex, open(ep, "w"))
        rc, out, _ = run("recall", repo, mp, "--changes", c1 + "," + c2, "--expected", ep, "--no-metrics")
        ok("recall cli", rc == 0 and "aggregate over 2 changes" in out and "No threshold" in out, out)
        ok("recall bad rev exit 2", run("recall", repo, mp, "--changes", "deadbeef", "--no-metrics")[0] == 2)
        # --touched: grow per feature from a change that mentions IDs
        w(repo, "crates/b/src/lib.rs", "pub fn b() {}\n// PF-01 wired here\n")
        g(repo, "add", "-A")
        g(repo, "commit", "-qm", "change three")
        c3 = g(repo, "rev-parse", "HEAD").strip()
        run("build", repo, "-o", mp, "--features", "H-02", "--fresh", "--no-metrics")
        rc, out, _ = run("build", repo, "-o", mp, "--touched", c3, "--no-metrics")
        ok("touched adds the mentioned feature", [n["id"] for n in json.load(open(mp))["nodes"]] == ["feature:H-02", "feature:PF-01"], out)

        # ---- plan check (S4 / VC-05)
        base = {"nodes": [{"id": "feature:A", "status": "context"}], "edges": []}
        plan_ok = {"nodes": [{"id": "feature:A", "status": "removed"}, {"id": "feature:B", "status": "added"}], "edges": []}
        plan_bad = {"nodes": [{"id": "feature:Z", "status": "removed"}, {"id": "feature:A", "status": "added"}], "edges": []}
        ok("plan ok", not plan_check(base, plan_ok)["stale"])
        pb = plan_check(base, plan_bad)
        ok("VC-05 removed missing is stale", pb["stale"] and any(p["id"] == "feature:Z" for p in pb["problems"]), pb)
        ok("added present is stale", any(p["id"] == "feature:A" for p in pb["problems"]))
        bp, pp = os.path.join(d, "b.json"), os.path.join(d, "p.json")
        json.dump(base, open(bp, "w"))
        json.dump(plan_bad, open(pp, "w"))
        ok("plan cli exit 1", run("plan-check", bp, pp)[0] == 1)

        # ---- list (S6, S9, VC-10)
        rc, out, _ = run("list", repo, "--json")
        r = json.loads(out)
        ok("S9 no map yet", not r["has_map"] and "no committed product map" in r["message"] and len(r["diagrams"]) == 1, out)
        shutil_dst = os.path.join(repo, DEFAULT_MAP)
        os.makedirs(os.path.dirname(shutil_dst), exist_ok=True)
        run("build", repo, "-o", shutil_dst, "--features", "H-01", "--no-metrics")
        r = json.loads(run("list", repo, "--json")[1])
        paths = [e["path"] for e in r["diagrams"]]
        ok("VC-10 listed once", r["has_map"] and sorted(paths) == sorted(set(paths)) == ["docs/architecture/c2-containers.md", DEFAULT_MAP.replace(os.sep, "/")], paths)

        # ---- metrics
        mpath = os.path.join(d, "metrics.jsonl")
        run("check", repo, shutil_dst, "--metrics", mpath)
        run("recall", repo, shutil_dst, "--changes", c1, "--metrics", mpath)
        rows = vr_metrics.read(mpath)
        ok("metrics rows", [x["event"] for x in rows] == ["check", "recall"] and rows[1]["threshold"] is None and "stale" in rows[0], rows)
        run("build", repo, "-o", mp, "--features", "H-01", "--fresh")
        ok("default metrics path", os.path.isfile(vr_metrics.default_path(repo)))

    # ---- the whole-repo map: areas from the OVERVIEW files, "builds on" edges from the dependency maps, stages, list kinds
    book2 = ("# Book\n\n## Features\n\n- M-01: topology model.\n- M-02: channels.\n- H-01: tool layer.\n- H-02: reader.\n- X-01: stray.\n\n"
             "## Suggested build order\n\n### Stage 0 — Foundations (all depend on it)\n\n- H-01: tool layer core.\n\n"
             "### Stage 1 — One agent works\n\n- M-01, M-02: graph and channels.\n- H-02: reader (needs H-01).\n")
    mesh = ("# Mesh — sub-feature overview\n\n## M-01 — Topology model\n\ntext\n\n## M-02 — Channels\n\ntext\n\n## Dependency map\n\n"
            "| Sub-feature | Needs | Feeds |\n|---|---|---|\n| M-01 topology model | H-01 (tools), Postgres config (P8) | M-02 |\n"
            "| M-02 channels | NATS, P2 trait, M-01, M-01 again | all traffic |\n")
    harness = ("# Harness — sub-feature overview\n\n## H-01 — Tool layer\n\ntext\n\n## H-02 — Reader\n\ntext\n\n## Dependency map\n\n"
               "| Sub-feature | Needs | Feeds |\n|---|---|---|\n| H-01 tool layer | nothing yet | H-02 |\n| H-02 reader | H-01, Z-99 (unknown), H-02 (self) | M-01 |\n")
    ov = parse_overview(mesh)
    ok("overview: label, prefixes", ov["label"] == "Mesh" and ov["prefixes"] == ["M"], ov)
    ok("overview: needs keep IDs only, once, in order", [(a, b) for a, b, _ in ov["deps"]] == [("M-01", ["H-01"]), ("M-02", ["M-01"])], ov["deps"])
    st = parse_stages(book2)
    ok("stages: first stage wins, lists with several IDs", {k: v["stage"] for k, v in st.items()} == {"H-01": 0, "M-01": 1, "M-02": 1, "H-02": 1}, st)
    with tempfile.TemporaryDirectory() as d2:
        repo2 = os.path.join(d2, "shop")
        os.makedirs(repo2)
        g(repo2, "init", "-q")
        w(repo2, "FEATURE-BOOK.md", book2)
        w(repo2, "vol/features/mesh/OVERVIEW.md", mesh)
        w(repo2, "vol/features/harness/OVERVIEW.md", harness)
        w(repo2, "research/OVERVIEW.md", "# Research overview\n\nno feature sections\n")
        w(repo2, "crates/a/src/lib.rs", "// M-01 here\n")
        g(repo2, "add", "-A")
        g(repo2, "commit", "-q", "-m", "c")
        mp2 = os.path.join(repo2, DEFAULT_MAP)
        os.makedirs(os.path.dirname(mp2), exist_ok=True)
        rc, out, err = run("build", repo2, "-o", mp2, "--all", "--no-metrics")
        m2 = json.load(open(mp2))
        ok("map: built", rc == 0 and m2["view"] == "map", err)
        ok("map: areas derived from the OVERVIEW files, stray feature in Other",
           {x["id"]: x["label"] for x in m2["groups"]} == {"area:mesh": "Mesh", "area:harness": "Harness", OTHER_AREA: "Other"}
           and {n["id"]: n["group"] for n in m2["nodes"]} == {"feature:H-01": "area:harness", "feature:H-02": "area:harness", "feature:M-01": "area:mesh",
                                                              "feature:M-02": "area:mesh", "feature:X-01": OTHER_AREA}, m2["groups"])
        eds = {(e["source"], e["target"]): e for e in m2["edges"]}
        ok("map: edges from Needs, non-ID text, unknown IDs and self refs ignored",
           sorted(eds) == [("feature:H-02", "feature:H-01"), ("feature:M-01", "feature:H-01"), ("feature:M-02", "feature:M-01")], sorted(eds))
        e0 = eds[("feature:M-02", "feature:M-01")]
        ok("map: edge label and evidence (the table row)", e0["label"] == EDGE_LABEL and len(e0["label"]) <= 40 and e0["evidence"][0]["path"] == "vol/features/mesh/OVERVIEW.md"
           and e0["evidence"][0]["line"] == 16 and "| M-02 channels" in read_lines(repo2, e0["evidence"][0]["path"])[15], e0["evidence"])
        det = {n["id"]: n["detail"]["after"] for n in m2["nodes"]}
        ok("map: stage in the detail", det["feature:H-01"].startswith("Build stage 0: Foundations") and "Build stage 1: One agent works." in det["feature:M-02"]
           and "Build stage" not in det["feature:X-01"] and "Builds on: M-01." in det["feature:M-02"], det)
        ok("map: takeaway title", m2["title"].startswith("5 features in 3 product areas") and "Product map" not in m2["title"], m2["title"])
        iss = vr_graph.validate(m2, root=repo2)
        ok("map: valid with the budget on", not iss and not vr_graph.budget(m2)["over"] and vr_graph.budget(m2)["used"] == 3, iss)
        ok("map: check is fresh", run("check", repo2, mp2, "--json")[0] == 0)
        # an edited dependency row makes the map stale (the edge cites it)
        w(repo2, "vol/features/mesh/OVERVIEW.md", mesh.replace("| M-02 channels | NATS", "| M-02 channels | NATS and TLS"))
        ok("map: edited dependency row is stale", run("check", repo2, mp2, "--json")[0] == 1)
        # a flat map of a map is organised again (incremental build keeps the view)
        run("build", repo2, "-o", mp2, "--features", "M-01", "--no-metrics")
        ok("map: incremental build keeps the grouping", json.load(open(mp2))["view"] == "map" and len(json.load(open(mp2))["groups"]) == 3)
        # the area groups fold under the budget; one more area is not enough to break it, eight are
        big = json.loads(json.dumps(m2))
        big["groups"] += [{"id": "area:x%d" % i, "label": "X%d" % i, "status": "context"} for i in range(5)]
        ok("map budget: 8 areas is over", vr_graph.budget(big)["over"] and any(i["code"] == "over_budget" for i in vr_graph.validate(big, root=repo2)))
        wide = json.loads(json.dumps(m2))
        for i in range(16):
            n = dict(wide["nodes"][0], id="feature:Q-%02d" % i, label="Q", group="area:mesh")
            wide["nodes"].append(n)
        ok("map budget: 16 features in one area is over", vr_graph.budget(wide)["over"] and vr_graph.budget(wide)["widest_group"] == 18)
        # the dashboard list: the real kind of each committed view, the product map first
        ad = os.path.join(repo2, "docs/architecture")
        for fn, view, title in (("modules-a.json", "full", "Modules of a"), ("crate-map.json", "full", "Crate map of shop"), ("calls-a.json", "full", "Calls of a"),
                                ("crate-calls.json", "full", "Calls between crates of shop")):
            json.dump({"schema_version": "1.0.0", "view": view, "title": title, "nodes": [], "edges": []}, open(os.path.join(ad, fn), "w"))
        w(repo2, "docs/architecture/c1-context.md", "# Context\n\n```mermaid\nflowchart LR\n```\n")
        lst = json.loads(run("list", repo2, "--json")[1])["diagrams"]
        ok("list: kinds and order", [(e["kind"], os.path.basename(e["path"])) for e in lst] == [
            ("product-map", "product-map.json"), ("crate-map", "crate-map.json"), ("crate-calls", "crate-calls.json"), ("modules", "modules-a.json"),
            ("calls", "calls-a.json"), ("c1", "c1-context.md")], lst)
        ok("list: the map's title is its takeaway", lst[0]["title"] == json.load(open(mp2))["title"], lst[0])

    if fails:
        print("vr_map selftest FAILED:\n  " + "\n  ".join(fails))
        return 1
    print("vr_map selftest ok")
    return 0


def main(argv=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args[:1] == ["refresh"]:
        import vr_refresh
        return vr_refresh.main(args[1:])
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    ap.add_argument("--selftest", action="store_true")
    sub = ap.add_subparsers(dest="cmd")

    def common(p):
        p.add_argument("--metrics", help="metrics log path (default REPO/.discovery/visual-review/metrics.jsonl)")
        p.add_argument("--no-metrics", action="store_true")
        return p
    b = common(sub.add_parser("build", help="write the L0 feature map with its staleness record"))
    b.add_argument("repo")
    b.add_argument("-o", "--output", help="default REPO/%s" % DEFAULT_MAP)
    b.add_argument("--features", help="comma list of feature IDs (OQ-09: per feature)")
    b.add_argument("--touched", help="comma list of revs: add the features their touched files mention")
    b.add_argument("--all", action="store_true", help="map the whole book")
    b.add_argument("--fresh", action="store_true", help="do not merge into an existing output")
    b.add_argument("--max-links", type=int, default=8)
    b.add_argument("--exclude", nargs="*", help="more path globs to skip when looking for IDs")
    b.add_argument("--table", help="also write the feature-to-code table as Markdown here")
    b.set_defaults(fn=_cmd_build)
    c = common(sub.add_parser("check", help="recompute the hashes; exit 1 when any element is stale"))
    c.add_argument("repo")
    c.add_argument("map")
    c.add_argument("--json", action="store_true")
    c.set_defaults(fn=_cmd_check)
    r = common(sub.add_parser("recall", help="OQ-07 measure: does the map cover what merged changes touched"))
    r.add_argument("repo")
    r.add_argument("map")
    r.add_argument("--changes", required=True)
    r.add_argument("--expected", help="JSON {rev: [feature IDs]} for feature recall")
    r.add_argument("--include-tests", action="store_true")
    r.add_argument("--json", action="store_true")
    r.set_defaults(fn=_cmd_recall)
    pc = sub.add_parser("plan-check", help="a removed element must exist in the base, an added one must not")
    pc.add_argument("base")
    pc.add_argument("plan")
    pc.add_argument("--json", action="store_true")
    pc.set_defaults(fn=_cmd_plan)
    ls = sub.add_parser("list", help="the committed diagrams of a repo, each once")
    ls.add_argument("repo")
    ls.add_argument("--dir", default="docs/architecture")
    ls.add_argument("--json", action="store_true")
    ls.set_defaults(fn=_cmd_list)
    a = ap.parse_args(argv)
    if a.selftest:
        return _selftest()
    if not a.cmd:
        ap.print_usage(sys.stderr)
        return 2
    try:
        return a.fn(a)
    except MapError as exc:
        print("vr_map: %s" % exc, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
