#!/usr/bin/env python3
"""Visual review: merge a before and an after change graph into one diff graph (feature VR-02).

    vr_diff.py merge --before B.json --after A.json [-o merged.json]
    vr_diff.py view MERGED.json --mode before|after|diff [-o out.json]
    vr_diff.py after-from-git REPO --base REV --head REV [--layers 1,2] [--crates a,b] -o after.json
    vr_diff.py --selftest

`merge` joins the two graphs on the stable id (schema 1.0.0) and gives every node,
edge, group and flow a status:
  added    only in the after graph;
  removed  only in the before graph (or planned as removed by the after graph);
  changed  in both and the signature (`detail`), an attribute (kind, label, group,
           lane, order, resolution), or an incident edge differs; also a node the
           after graph itself marks `changed` (after-from-git does that for the
           modules whose files the diff touches); also a parent (group, or the
           source of a `contains` edge) with any member that is not `context`;
  context  everything else.
Evidence of both sides is kept. Before-side entries get the note prefix "before";
an entry on the same anchor on both sides is kept once. An element that is added or
changed and that no after-side source evidence proves gets `resolution: inferred`
(edges and nodes) and keeps an `inferred` evidence entry; a proven one never does.
A flow's steps are merged by flow id: `before` is the before graph's state
(its `before` list, else its `after`), `after` the after graph's state. A repeated run
of steps (same label, kind and lane) collapses to one run with a count: the label gets
a " xN" suffix and `meta.collapsed_runs` lists it. A non-context flow with more than
12 distinct steps fails with `over_budget`. The node budget of the view is NOT checked
(the diff graph is a union; VR-05 picks the views a reviewer sees).
`meta.diff` holds the counts ("N added, N changed, N removed") and the change list
with element ids; `meta.marks` the non-colour mark per status.

`view` filters the merged graph: before = removed + changed + context, after =
added + changed + context, diff = all. Edges whose ends were filtered out go too.
Nothing else is touched, so a surviving element is byte-equal in every mode.

`after-from-git` runs vr_extract at HEAD in a temporary git worktree under $TMPDIR
(removed again, also on failure), marks the modules whose files `git diff
base..head` touches as `changed` and adds those files as evidence; the added
signature lines (fn, struct, enum, trait, impl, type, mod) become the node's
`detail.after` and a "stub signature" evidence note.

Importable: `merge(before, after)`, `view(merged, mode)`, `after_from_git(repo, base, head, layers)`.
Issue codes: removed_element_missing, added_element_exists, over_budget, bad_revision,
plus every code of vr_graph.validate. Exit codes: 0 written; 1 the diff failed (issues
as JSON on stderr); 2 unusable input.
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import vr_graph  # noqa: E402
import vr_extract  # noqa: E402

VERSION = "1.0.0"
FLOW_STEP_CAP = 12
MODES = {
    "before": {"removed", "changed", "context"},
    "after": {"added", "changed", "context"},
    "diff": {"added", "changed", "removed", "context"},
}
# Non-colour mark per status (VC-10: a status is never colour only).
MARKS = {"added": "plus", "removed": "minus", "changed": "tilde", "context": "none"}
NODE_ATTRS = ("kind", "label", "group", "lane", "order", "resolution")
EDGE_ATTRS = ("kind", "label", "resolution")
GROUP_ATTRS = ("label", "parent")
SIGNATURE_LINE = {
    ".rs": re.compile(r"^\s*(?:pub(?:\([^)]*\))?\s+)?(?:default\s+)?(?:async\s+)?(?:unsafe\s+)?(?:const\s+)?"
                      r"(?:fn|struct|enum|trait|type|impl|mod)\b.*$"),
}
MAX_SIGNATURES = 6


class DiffError(Exception):
    """The diff cannot be made. `.issues` is a list of {code, path, message}."""

    def __init__(self, issues):
        self.issues = issues
        super().__init__("; ".join("%s: %s" % (i["code"], i["message"]) for i in issues))


def _issue(code: str, path: str, message: str) -> dict:
    return {"code": code, "path": path, "message": message}


# ------------------------------------------------------------------ helpers

def _by_id(items) -> dict:
    return {x["id"]: x for x in items or []}


def _sig(node: dict):
    """The signature text of a node's state: detail.after if present, else detail.before."""
    d = node.get("detail") or {}
    return d.get("after") if "after" in d else d.get("before")


def _differs(a: dict, b: dict, attrs) -> bool:
    return any(a.get(k) != b.get(k) for k in attrs)


def _ev_key(e: dict):
    if e.get("tier") == "source":
        return ("source", e.get("path"), e.get("line"), e.get("end_line"))
    return ("inferred", e.get("note"))


def _merge_evidence(after_ev, before_ev) -> list:
    out, seen = [], set()
    for e in after_ev or []:
        k = _ev_key(e)
        if k not in seen:
            seen.add(k)
            out.append(copy.deepcopy(e))
    for e in before_ev or []:
        k = _ev_key(e)
        if k in seen:
            continue
        seen.add(k)
        b = copy.deepcopy(e)
        b["note"] = "before" + (": " + e["note"] if e.get("note") else "")
        out.append(b)
    return out


def _proven(ev) -> bool:
    return any(e.get("tier") == "source" for e in ev or [])


def _apply_proof(el: dict, after_ev, has_resolution: bool) -> None:
    """Planned element (added or changed) with no source evidence on the after side: inferred."""
    if _proven(after_ev):
        if has_resolution and el.get("resolution") == "inferred":
            el["resolution"] = "static"
        return
    if has_resolution:
        el["resolution"] = "inferred"
    if not any(e.get("tier") == "inferred" for e in el["evidence"]):
        el["evidence"].insert(0, {"tier": "inferred", "note": "planned: no stub signature or diff line proves it"})


def _make(b, a, attrs, has_resolution: bool, status: str) -> dict:
    """Merged element: the after element (before element when removed), both evidence lists."""
    base = copy.deepcopy(b if (status == "removed" and b is not None) else a)
    base["status"] = status
    base["evidence"] = _merge_evidence(a.get("evidence") if a else None, b.get("evidence") if b else None)
    if status in ("added", "changed") and a is not None and "evidence" in base:
        _apply_proof(base, a.get("evidence"), has_resolution)
    return base


# ---------------------------------------------------------------- flows

def _step_key(nodes: dict, nid: str):
    n = nodes.get(nid)
    return (n.get("label"), n.get("kind"), n.get("lane")) if n else (nid,)


def _find_runs(keys: list) -> list:
    """[(start, block_len, count)] of non-overlapping repeated runs, most covered first at each start."""
    out, i = [], 0
    while i < len(keys):
        best = None
        for ln in range(1, (len(keys) - i) // 2 + 1):
            blk, c = keys[i:i + ln], 1
            while keys[i + c * ln:i + (c + 1) * ln] == blk:
                c += 1
            if c >= 2 and (best is None or ln * c > best[0]):
                best = (ln * c, ln, c)
        if best:
            out.append((i, best[1], best[2]))
            i += best[0]
        else:
            i += 1
    return out


def _collapse(steps: list, nodes: dict, canon: dict, runs_out: list, side: str) -> list:
    keys = [_step_key(nodes, s) for s in steps]
    result, i = [], 0
    for start, ln, cnt in _find_runs(keys):
        result += steps[i:start]
        block = steps[start:start + ln]
        for k in range(1, cnt):
            for j in range(ln):
                dropped = steps[start + k * ln + j]
                if dropped != block[j]:
                    canon[dropped] = block[j]
        result += block
        runs_out.append({"side": side, "steps": list(block), "count": cnt})
        i = start + ln * cnt
    return result + steps[i:]


# ------------------------------------------------------------------ merge

def merge(before: dict, after: dict) -> dict:
    """Merge a before and an after graph into one diff graph. Raises DiffError with typed issues."""
    issues: list = []
    for side, g in (("before", before), ("after", after)):
        for i in vr_graph.validate(g, check_budget=False):
            i["path"] = "%s.%s" % (side, i["path"]) if i["path"] else side
            issues.append(i)
    if issues:
        raise DiffError(issues)

    bn, an = _by_id(before.get("nodes")), _by_id(after.get("nodes"))
    be, ae = _by_id(before.get("edges")), _by_id(after.get("edges"))
    bg, ag = _by_id(before.get("groups")), _by_id(after.get("groups"))
    bf, af = _by_id(before.get("flows")), _by_id(after.get("flows"))

    # The plan's own claims are checked against the base (VC-05).
    for coll, bmap, amap in (("nodes", bn, an), ("edges", be, ae), ("groups", bg, ag), ("flows", bf, af)):
        for eid, a in amap.items():
            if a["status"] == "removed" and eid not in bmap:
                issues.append(_issue("removed_element_missing", "%s.%s" % (coll, eid),
                                     "the plan removes %s %r but the base graph has no such element" % (coll[:-1], eid)))
            if a["status"] == "added" and eid in bmap:
                issues.append(_issue("added_element_exists", "%s.%s" % (coll, eid),
                                     "the plan adds %s %r but the base graph already has it" % (coll[:-1], eid)))
    if issues:
        raise DiffError(issues)

    def order(amap, bmap):
        return list(amap) + [k for k in bmap if k not in amap]

    # edges first: a changed edge changes its ends
    edges: dict = {}
    for eid in order(ae, be):
        a, b = ae.get(eid), be.get(eid)
        if a is None:
            st = "removed"
        elif b is None:
            st = "added"
        elif a["status"] == "removed":
            st = "removed"
        else:
            st = "changed" if (_differs(a, b, EDGE_ATTRS) or a["status"] == "changed") else "context"
        edges[eid] = _make(b, a, EDGE_ATTRS, True, st)
    edge_touched = set()
    for e in edges.values():
        if e["status"] == "changed":
            edge_touched.update((e["source"], e["target"]))

    nodes: dict = {}
    for nid in order(an, bn):
        a, b = an.get(nid), bn.get(nid)
        if a is None:
            st = "removed"
        elif b is None:
            st = "added"
        elif a["status"] == "removed":
            st = "removed"
        else:
            diff = _sig(a) != _sig(b) or _differs(a, b, NODE_ATTRS) or nid in edge_touched or a["status"] == "changed"
            st = "changed" if diff else "context"
        n = _make(b, a, NODE_ATTRS, True, st)
        if a is not None and b is not None and st != "removed" and _sig(a) != _sig(b):
            n["detail"] = {k: v for k, v in (("before", _sig(b)), ("after", _sig(a))) if v is not None}
        nodes[nid] = n

    groups: dict = {}
    for gid in order(ag, bg):
        a, b = ag.get(gid), bg.get(gid)
        if a is None:
            st = "removed"
        elif b is None:
            st = "added"
        elif a["status"] == "removed":
            st = "removed"
        else:
            st = "changed" if (_differs(a, b, GROUP_ATTRS) or a["status"] == "changed") else "context"
        groups[gid] = _make(b, a, GROUP_ATTRS, False, st)

    # flows: collapse repeated runs, then merge before and after steps by flow id
    canon: dict = {}
    runs: dict = {}
    flow_steps: dict = {}
    for fid in order(af, bf):
        a, b = af.get(fid), bf.get(fid)
        bsteps = list((b.get("before") if b and "before" in b else (b or {}).get("after")) or []) if b else []
        asteps = list((a.get("after") if a and "after" in a else (a or {}).get("before")) or []) if a else []
        lst: list = []
        flow_steps[fid] = (_collapse(bsteps, nodes, canon, lst, "before"), _collapse(asteps, nodes, canon, lst, "after"), lst)
        if lst:
            runs[fid] = lst
    if canon:
        used = {s for bs, as_, _ in flow_steps.values() for s in bs + as_}
        for gone in canon:
            if gone not in used:
                nodes.pop(gone, None)
        seen, kept = set(), {}
        for eid, e in edges.items():
            s, t = canon.get(e["source"], e["source"]), canon.get(e["target"], e["target"])
            if s == t and e["source"] != e["target"]:
                continue
            if (s, t, e["kind"]) in seen:
                continue
            seen.add((s, t, e["kind"]))
            e["source"], e["target"] = s, t
            kept[eid] = e
        edges = kept
        for fid, rl in runs.items():
            for r in rl:
                for sid in r["steps"]:
                    if sid in nodes and not re.search(r" x\d+$", nodes[sid]["label"]):
                        suffix = " x%d" % r["count"]
                        lab = nodes[sid]["label"]
                        if len(lab) + len(suffix) > vr_graph.NODE_LABEL_MAX:  # trim the label, never the count
                            lab = lab[:vr_graph.NODE_LABEL_MAX - len(suffix) - 1].rstrip() + "\u2026"
                        nodes[sid]["label"] = lab + suffix
    # drop edges whose end was removed by the collapse alone
    flows: dict = {}
    for fid in order(af, bf):
        a, b = af.get(fid), bf.get(fid)
        bs, as_, _ = flow_steps[fid]
        if a is None:
            st = "removed"
        elif b is None:
            st = "added"
        elif a["status"] == "removed":
            st = "removed"
        else:
            st = "changed" if (bs != as_ or a["status"] == "changed" or
                               any(nodes[s]["status"] != "context" for s in bs + as_ if s in nodes)) else "context"
        f = _make(b, a, (), False, st)
        f["before"], f["after"] = bs, as_
        if a is not None and b is not None and a.get("lanes") != b.get("lanes"):
            f["lanes"] = list(a.get("lanes") or []) + [x for x in (b.get("lanes") or []) if x not in (a.get("lanes") or [])]
        flows[fid] = f
        distinct = len(set(bs) | set(as_))
        if st != "context" and distinct > FLOW_STEP_CAP:
            issues.append(_issue("over_budget", "flows.%s" % fid,
                                 "flow %r has %d steps, the cap is %d" % (fid, distinct, FLOW_STEP_CAP)))
    if issues:
        raise DiffError(issues)

    # roll-up: a parent with any member that is not context is changed (VC-04), to a fixed point
    contains: dict = {}
    for e in edges.values():
        if e["kind"] == "contains" and e["source"] in nodes:
            contains.setdefault(e["source"], []).append(e["target"])
    changed = True
    while changed:
        changed = False
        for gid, g in groups.items():
            if g["status"] != "context":
                continue
            members = [n["status"] for n in nodes.values() if n.get("group") == gid]
            members += [x["status"] for x in groups.values() if x.get("parent") == gid]
            if any(s != "context" for s in members):
                g["status"] = "changed"
                changed = True
        for nid, kids in contains.items():
            n = nodes[nid]
            if n["status"] == "context" and any(k in nodes and nodes[k]["status"] != "context" for k in kids):
                n["status"] = "changed"
                changed = True

    meta = copy.deepcopy(after["meta"])
    meta["before_commit"] = before["meta"].get("commit")
    tools = {(t["name"], t["version"]): t for t in (before["meta"].get("tools") or []) + (after["meta"].get("tools") or [])}
    meta["tools"] = [tools[k] for k in sorted(tools)]
    if runs:
        meta["collapsed_runs"] = runs
    merged = {
        "schema_version": after["schema_version"], "view": after["view"], "title": after["title"], "meta": meta,
        "nodes": list(nodes.values()), "edges": list(edges.values()),
    }
    if groups:
        merged["groups"] = list(groups.values())
    if flows:
        merged["flows"] = list(flows.values())
    lanes = list(after.get("lanes") or [])
    have = {x["id"] for x in lanes}
    lanes += [x for x in (before.get("lanes") or []) if x["id"] not in have]
    if lanes:
        merged["lanes"] = lanes
    _summarise(merged)
    bad = vr_graph.validate(merged, check_budget=False)
    if bad:
        raise DiffError(bad)
    return merged


def _summarise(graph: dict) -> None:
    """Counts and the change list into meta.diff, the marks into meta.marks."""
    by_type, total, changes = {}, {"added": 0, "changed": 0, "removed": 0, "context": 0}, []
    for coll, typ in (("nodes", "node"), ("edges", "edge"), ("groups", "group"), ("flows", "flow")):
        c = {"added": 0, "changed": 0, "removed": 0, "context": 0}
        for x in graph.get(coll) or []:
            c[x["status"]] += 1
            total[x["status"]] += 1
            if x["status"] != "context":
                changes.append({"id": x["id"], "type": typ, "status": x["status"], "label": x.get("label", x["id"])})
        by_type[coll] = c
    graph["meta"]["diff"] = {
        "counts": total, "by_type": by_type, "changes": changes,
        "summary": "%d added, %d changed, %d removed" % (total["added"], total["changed"], total["removed"]),
    }
    graph["meta"]["marks"] = dict(MARKS)


# ------------------------------------------------------------------- view

def view(merged: dict, mode: str) -> dict:
    """Filter a merged graph to one mode (before, after, diff). Surviving elements are untouched."""
    if mode not in MODES:
        raise DiffError([_issue("schema_error", "mode", "mode must be one of %s" % ", ".join(sorted(MODES)))])
    keep = MODES[mode]
    out = copy.deepcopy(merged)
    out["nodes"] = [n for n in out["nodes"] if n["status"] in keep]
    ids = {n["id"] for n in out["nodes"]}
    if "groups" in out:
        out["groups"] = [g for g in out["groups"] if g["status"] in keep]
        gids = {g["id"] for g in out["groups"]}
        for g in out["groups"]:
            if g.get("parent") and g["parent"] not in gids:
                del g["parent"]
        for n in out["nodes"]:
            if n.get("group") and n["group"] not in gids:
                del n["group"]
        ids |= gids
    out["edges"] = [e for e in out["edges"] if e["status"] in keep and e["source"] in ids and e["target"] in ids]
    if "flows" in out:
        out["flows"] = [f for f in out["flows"] if f["status"] in keep]
        nid = {n["id"] for n in out["nodes"]}
        for f in out["flows"]:
            f["before"] = [s for s in f.get("before", []) if s in nid]
            f["after"] = [s for s in f.get("after", []) if s in nid]
    out["meta"]["view_mode"] = mode
    _summarise(out)
    return out


# --------------------------------------------------------------- git side

def _git(repo: str, *args, check: bool = True) -> str:
    r = subprocess.run(["git", "-C", repo] + list(args), capture_output=True, text=True)
    if check and r.returncode != 0:
        raise DiffError([_issue("bad_revision", "git", "git %s failed: %s" % (" ".join(args[:3]), r.stderr.strip()[:200]))])
    return r.stdout


def parse_diff(text: str) -> dict:
    """`git diff -U0` text -> {path: {"line": first new line, "signatures": [added signature lines]}}."""
    files: dict = {}
    cur = None
    new_line = 0
    for raw in text.splitlines():
        if raw.startswith("+++ "):
            p = raw[4:]
            cur = p[2:] if p.startswith("b/") else None
            if cur:
                files.setdefault(cur, {"line": None, "signatures": []})
        elif raw.startswith("--- "):
            continue
        elif raw.startswith("@@") and cur:
            m = re.match(r"@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@", raw)
            if m:
                new_line = max(1, int(m.group(1)))
                if files[cur]["line"] is None:
                    files[cur]["line"] = new_line
        elif raw.startswith("+") and cur:
            pat = SIGNATURE_LINE.get(os.path.splitext(cur)[1])
            if pat and pat.match(raw[1:]) and len(files[cur]["signatures"]) < MAX_SIGNATURES:
                files[cur]["signatures"].append(raw[1:].strip()[:160])
    return files


def _owner_for(path: str, graph: dict, pkgs: list, ws_root: str):
    """Id of the node a changed file belongs to, or None: module, bin or crate, climbing to the parent module."""
    nodes = {n["id"] for n in graph["nodes"]}
    if path.endswith("Cargo.toml"):
        d = os.path.dirname(path)
        for p in pkgs:
            if os.path.dirname(os.path.relpath(p["manifest_path"], ws_root)) == d and "crate:" + p["name"] in nodes:
                return "crate:" + p["name"]
        return None
    if not path.endswith(".rs"):
        return None
    qual = vr_extract._qual_for(path, pkgs, ws_root)
    crates = {p["name"].replace("-", "_"): "crate:" + p["name"] for p in pkgs}
    parts = qual.split("::")
    while parts:
        q = "::".join(parts)
        for cand in ("module:" + q, "bin:" + q):
            if cand in nodes:
                return cand
        if len(parts) == 1:
            return crates.get(parts[0]) if crates.get(parts[0]) in nodes else None
        parts.pop()
    return None


def _mark_changed(graph: dict, files: dict, base: str, head: str, ws_root: str) -> list:
    pkgs = []
    for n in graph["nodes"]:
        if n["kind"] == "crate":
            ev = (n.get("evidence") or [{}])[0]
            if ev.get("path"):
                pkgs.append({"name": n["id"][len("crate:"):], "manifest_path": os.path.join(ws_root, ev["path"])})
    by_id = _by_id(graph["nodes"])
    unmapped = []
    for path in sorted(files):
        owner = _owner_for(path, graph, pkgs, ws_root)
        if owner is None:
            unmapped.append(path)
            continue
        n = by_id[owner]
        info = files[path]
        n["status"] = "changed"
        sigs = info["signatures"]
        note = ("stub signature: " + "; ".join(sigs))[:300] if sigs else "changed in %s..%s" % (base[:8], head[:8])
        n["evidence"].append({"tier": "source", "path": path, "line": info["line"] or 1, "note": note})
        if sigs:
            old = (n.get("detail") or {}).get("after")
            n["detail"] = {"after": ((old + "; ") if old else "") + "; ".join(sigs)}
    return unmapped


def after_from_git(repo: str, base: str, head: str, layers=(1, 2), extractor=None, crates=None) -> dict:
    """The plan-time "after" graph: the extractor at `head` in a temporary worktree, changed files marked.

    `extractor(root, layers)` defaults to vr_extract.extract("rust", ...); with `crates` (names) it is
    called as `extractor(root, layers, crates)` and layer 2 runs only for them. The worktree is
    created under $TMPDIR and removed again, also when the extraction fails.
    """
    repo = os.path.realpath(repo)
    base_sha = _git(repo, "rev-parse", "--verify", base + "^{commit}").strip()
    head_sha = _git(repo, "rev-parse", "--verify", head + "^{commit}").strip()
    tmp = os.path.realpath(tempfile.mkdtemp(prefix="vr-diff-"))
    if tmp == repo or tmp.startswith(repo + os.sep):
        shutil.rmtree(tmp, ignore_errors=True)
        raise DiffError([_issue("bad_revision", "tmp", "$TMPDIR is inside the repository; refusing to put a worktree there")])
    wt = os.path.join(tmp, os.path.basename(repo))
    added = False
    try:
        _git(repo, "worktree", "add", "--detach", "--quiet", wt, head_sha)
        added = True
        extract = extractor or (lambda root, lay, crs=None: vr_extract.extract("rust", root, layers=lay, crates=crs))
        try:
            g = extract(wt, tuple(layers)) if crates is None else extract(wt, tuple(layers), list(crates))
        except vr_extract.ExtractError as exc:
            raise DiffError([_issue("schema_error", "extract", str(exc))])
        diff = _git(repo, "diff", "--no-renames", "-U0", "%s..%s" % (base_sha, head_sha))
        files = parse_diff(diff)
        unmapped = _mark_changed(g, files, base_sha, head_sha, wt)
        g["title"] = "After graph of %s at %s" % (os.path.basename(repo), head_sha[:8])
        g["meta"]["repo"] = os.path.basename(repo)
        g["meta"]["commit"] = head_sha[:8]
        g["meta"]["base_commit"] = base_sha[:8]
        g["meta"]["from_git"] = {"base": base_sha[:8], "head": head_sha[:8], "changed_files": len(files),
                                 "unmapped": unmapped[:20]}
        bad = vr_graph.validate(g, root=wt, check_budget=False)
        if bad:
            raise DiffError(bad)
        return g
    finally:
        if added:
            _git(repo, "worktree", "remove", "--force", wt, check=False)
        shutil.rmtree(tmp, ignore_errors=True)
        _git(repo, "worktree", "prune", check=False)


# --------------------------------------------------------------------- CLI

def _write(graph: dict, path) -> None:
    text = json.dumps(graph, indent=1, ensure_ascii=False) + "\n"
    if path:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
    else:
        sys.stdout.write(text)


def _fail(exc: DiffError) -> int:
    print(json.dumps({"ok": False, "issues": exc.issues}, indent=2), file=sys.stderr)
    return 1


def _cmd_merge(args) -> int:
    try:
        b, a = vr_graph.load(args.before), vr_graph.load(args.after)
    except (OSError, ValueError) as exc:
        print("vr_diff: %s" % exc, file=sys.stderr)
        return 2
    try:
        m = merge(b, a)
    except DiffError as exc:
        return _fail(exc)
    _write(m, args.output)
    print("vr_diff: " + m["meta"]["diff"]["summary"], file=sys.stderr)
    return 0


def _cmd_view(args) -> int:
    try:
        m = vr_graph.load(args.file)
    except (OSError, ValueError) as exc:
        print("vr_diff: %s" % exc, file=sys.stderr)
        return 2
    try:
        _write(view(m, args.mode), args.output)
    except DiffError as exc:
        return _fail(exc)
    return 0


def _cmd_after(args) -> int:
    try:
        layers = tuple(sorted({int(x) for x in args.layers.split(",") if x.strip()}))
    except ValueError:
        print("--layers takes numbers, e.g. 1,2", file=sys.stderr)
        return 2
    try:
        crates = None if args.crates is None else [c.strip() for c in args.crates.split(",") if c.strip()]
        g = after_from_git(args.repo, args.base, args.head, layers, crates=crates)
    except DiffError as exc:
        return _fail(exc)
    _write(g, args.output)
    return 0


# ----------------------------------------------------------------- selftest

def _split(g: dict):
    """A merged fixture into (before, after) state graphs: statuses reset to context, changed
    elements get a differing signature (nodes), label (edges) on each side."""
    def state(side: str) -> dict:
        drop = "added" if side == "before" else "removed"
        out = copy.deepcopy(g)
        out["meta"]["commit"] = "base0000" if side == "before" else "head0000"
        for coll in ("nodes", "edges", "groups", "flows"):
            if coll not in out:
                continue
            items = []
            for x in out[coll]:
                if x["status"] == drop:
                    continue
                if x["status"] == "changed":
                    if coll == "nodes":
                        x.pop("detail", None)
                        x["detail"] = {"after" if side == "after" else "before": "%s %s" % (x["id"], side)}
                    elif coll == "edges":
                        x["label"] = "%s" % side
                x["status"] = "context"
                if side == "before" and x.get("evidence"):
                    x["evidence"] = copy.deepcopy(x["evidence"])
                items.append(x)
            out[coll] = items
        ids = {n["id"] for n in out["nodes"]} | {x["id"] for x in out.get("groups", [])}
        out["edges"] = [e for e in out["edges"] if e["source"] in ids and e["target"] in ids]
        for f in out.get("flows", []):
            if side == "before":
                f["before"] = [s for s in f.get("before", []) if s in ids]
                f.pop("after", None)
            else:
                f["after"] = [s for s in f.get("after", []) if s in ids]
                f.pop("before", None)
            f["status"] = "context"
        return out
    return state("before"), state("after")


def _mk(nodes=(), edges=(), groups=(), flows=(), view_="L2", commit="c0"):
    def ev(x):
        return x.get("evidence") or [{"tier": "source", "path": "src/a.rs", "line": 1}]
    g = {"schema_version": "1.0.0", "view": view_, "title": "t", "meta": {"repo": "r", "commit": commit, "generated_by": "hand"},
         "nodes": [], "edges": []}
    for n in nodes:
        d: dict = {"label": n["id"], "kind": "module", "status": "context"}
        d.update(n)
        d["evidence"] = ev(d)
        g["nodes"].append(d)
    for e in edges:
        d: dict = {"kind": "calls", "status": "context"}
        d.update(e)
        d["id"] = d.get("id") or "e:%s:%s" % (e["source"], e["target"])
        d["evidence"] = ev(d)
        g["edges"].append(d)
    if groups:
        g["groups"] = [dict({"label": x["id"], "status": "context", "evidence": ev(x)}, **x) for x in groups]
    if flows:
        g["flows"] = [dict({"label": x["id"], "status": "context", "evidence": ev(x)}, **x) for x in flows]
    return g


def _selftest() -> int:
    fails: list = []

    def check(name, cond, detail=""):
        if not cond:
            fails.append("%s %s" % (name, detail))

    def codes(exc):
        return sorted({i["code"] for i in exc.issues})

    def stat(g, coll="nodes"):
        return {x["id"]: x["status"] for x in g.get(coll, [])}

    def items(g):
        return [x for c in ("nodes", "edges", "groups", "flows") for x in g.get(c, [])]

    fx = vr_graph.FIXTURES
    names = ["removed", "w145-l0", "w145-l1", "w145-l2", "minimal"]
    merged_all = {}
    for name in names:
        full = vr_graph.load(os.path.join(fx, name + ".json"))
        b, a = _split(full)
        try:
            m = merge(b, a)
        except DiffError as exc:
            check("fixture:%s:merge" % name, False, str(exc))
            continue
        merged_all[name] = m
        # round trip: the merge of the split fixture reproduces the fixture's statuses
        nst = stat(full)
        exp_edges = {}
        for e in full["edges"]:  # a hand fixture may call an edge to an added node "context"; it cannot exist before
            ends = {nst.get(e["source"]), nst.get(e["target"])}
            exp_edges[e["id"]] = "added" if "added" in ends else "removed" if "removed" in ends else e["status"]
        for coll in ("nodes", "edges", "groups", "flows"):
            want = exp_edges if coll == "edges" else stat(full, coll)
            check("fixture:%s:%s-statuses" % (name, coll), stat(m, coll) == want, "%s != %s" % (stat(m, coll), want))
        check("fixture:%s:valid" % name, vr_graph.validate(m, check_budget=False) == [])
        d, bef, aft = view(m, "diff"), view(m, "before"), view(m, "after")
        # VC-01, VC-02
        check("VC-01:%s" % name, not any(x["status"] == "removed" for x in items(aft)))
        check("VC-02:%s" % name, not any(x["status"] == "added" for x in items(bef)))
        # VC-03
        for st in ("added", "changed", "removed"):
            check("VC-03:%s:%s" % (name, st), m["meta"]["diff"]["counts"][st] == sum(1 for x in items(d) if x["status"] == st) ==
                  d["meta"]["diff"]["counts"][st])
        check("VC-03:%s:summary" % name, re.match(r"\d+ added, \d+ changed, \d+ removed$", m["meta"]["diff"]["summary"]) is not None)
        check("view:%s:valid" % name, all(vr_graph.validate(v, check_budget=False) == [] for v in (d, bef, aft)), name)
        # VC-09 (the part that needs no layout): a context node is byte-equal and in the same order in every mode
        ctx = lambda g: [json.dumps(n, sort_keys=True) for n in g["nodes"] if n["status"] == "context"]
        check("VC-09:%s" % name, ctx(d) == ctx(bef) == ctx(aft))
        # VC-10: a status field and a non-colour mark per status
        check("VC-10:%s" % name, all(m["meta"]["marks"].get(s) not in (None, "") for s in ("added", "removed", "changed", "context")))
        check("view:%s:edges-no-dangling" % name,
              all(e["source"] in {x["id"] for x in bef["nodes"]} | {x["id"] for x in bef.get("groups", [])} for e in bef["edges"]))
    rm = merged_all.get("removed")
    if rm:
        # S3: a removed node stays in the diff view, with its before evidence
        q = next(n for n in view(rm, "diff")["nodes"] if n["id"] == "module:demo::queue")
        check("S3:removed-in-diff", q["status"] == "removed" and q["evidence"][0]["tier"] == "source")
        check("S1:added", stat(rm)["module:demo::cache"] == "added")
        check("S4:modes", "module:demo::queue" not in stat(view(rm, "after")) and "module:demo::cache" not in stat(view(rm, "before")))
        e = next(x for x in rm["edges"] if x["status"] == "removed")
        check("edges:removed-edge-dropped-from-after", e["id"] not in {x["id"] for x in view(rm, "after")["edges"]})
    # S2 / VC-04: parent roll-up
    b = _mk([{"id": "m", "group": "G"}, {"id": "n", "group": "G"}, {"id": "p", "group": "H"}],
            groups=[{"id": "G"}, {"id": "H"}])
    a = copy.deepcopy(b)
    a["nodes"][0]["detail"] = {"after": "fn m(x: u8)"}
    m = merge(b, a)
    check("S2:m-changed", stat(m)["m"] == "changed" and stat(m)["n"] == "context")
    check("VC-04:parent-changed", stat(m, "groups") == {"G": "changed", "H": "context"}, str(stat(m, "groups")))
    # roll-up through contains edges, two levels
    b = _mk([{"id": "crate:c", "kind": "crate"}, {"id": "module:c::a"}, {"id": "module:c::a::b"}],
            [{"source": "crate:c", "target": "module:c::a", "kind": "contains"}, {"source": "module:c::a", "target": "module:c::a::b", "kind": "contains"}])
    a = copy.deepcopy(b)
    a["nodes"][2]["status"] = "changed"
    m = merge(b, a)
    check("VC-04:contains-rollup", set(stat(m).values()) == {"changed"}, str(stat(m)))
    # a new module: its parent changes through the contains edge
    a = copy.deepcopy(b)
    a["nodes"].append(_mk([{"id": "module:c::z"}])["nodes"][0])
    a["edges"].append(_mk(edges=[{"source": "crate:c", "target": "module:c::z", "kind": "contains"}], nodes=[])["edges"][0])
    m = merge(b, a)
    check("S1:added-module", stat(m)["module:c::z"] == "added" and stat(m)["crate:c"] == "changed" and stat(m)["module:c::a"] == "context")
    # unchanged graphs are all context
    m = merge(b, copy.deepcopy(b))
    check("context:identical", set(stat(m).values()) == {"context"} and m["meta"]["diff"]["counts"]["added"] == 0)
    # evidence of both sides, an anchor present on both sides kept once
    b = _mk([{"id": "x", "evidence": [{"tier": "source", "path": "src/old.rs", "line": 3}, {"tier": "source", "path": "src/same.rs", "line": 1}]}])
    a = _mk([{"id": "x", "evidence": [{"tier": "source", "path": "src/new.rs", "line": 9}, {"tier": "source", "path": "src/same.rs", "line": 1}],
              "detail": {"after": "v2"}}])
    b["nodes"][0]["detail"] = {"before": "v1"}
    m = merge(b, a)
    ev = m["nodes"][0]["evidence"]
    check("evidence:both-sides", [e["path"] for e in ev] == ["src/new.rs", "src/same.rs", "src/old.rs"] and ev[2]["note"] == "before", str(ev))
    check("evidence:detail", m["nodes"][0]["detail"] == {"before": "v1", "after": "v2"} and m["nodes"][0]["status"] == "changed")
    # S6 / VC-05: stale plan, two directions
    b = _mk([{"id": "keep"}])
    a = _mk([{"id": "keep"}, {"id": "Y", "status": "removed"}])
    try:
        merge(b, a)
        check("VC-05:removed-missing", False, "passed")
    except DiffError as exc:
        check("VC-05:removed-missing", codes(exc) == ["removed_element_missing"] and "Y" in exc.issues[0]["message"], str(exc))
    b = _mk([{"id": "keep"}, {"id": "Z"}])
    a = _mk([{"id": "keep"}, {"id": "Z", "status": "added"}])
    try:
        merge(b, a)
        check("VC-05:added-exists", False, "passed")
    except DiffError as exc:
        check("VC-05:added-exists", codes(exc) == ["added_element_exists"] and "Z" in exc.issues[0]["message"], str(exc))
    # a planned removal that exists is fine and ends up removed
    b = _mk([{"id": "keep"}, {"id": "Y"}])
    a = _mk([{"id": "keep"}, {"id": "Y", "status": "removed"}])
    check("VC-05:planned-removal-ok", stat(merge(b, a))["Y"] == "removed")
    # S7 / VC-06: unproven planned edge is inferred, a proven one is not
    inf = [{"tier": "inferred", "note": "the plan says so"}]
    b = _mk([{"id": "p"}, {"id": "q"}])
    a = _mk([{"id": "p"}, {"id": "q"}, {"id": "r", "evidence": inf}],
            [{"source": "p", "target": "q", "evidence": inf}, {"source": "p", "target": "r", "evidence": [{"tier": "source", "path": "src/a.rs", "line": 2}]}])
    m = merge(b, a)
    e1, e2 = m["edges"]
    check("VC-06:unproven-edge", e1["status"] == "added" and e1["resolution"] == "inferred" and e1["evidence"][0]["tier"] == "inferred", str(e1))
    check("VC-06:proven-edge", e2["status"] == "added" and e2.get("resolution") != "inferred" and not any(x["tier"] == "inferred" for x in e2["evidence"]), str(e2))
    nr = next(n for n in m["nodes"] if n["id"] == "r")
    check("VC-06:unproven-node", nr["resolution"] == "inferred")
    a["edges"][1]["resolution"] = "inferred"
    check("VC-06:proven-never-inferred", merge(b, a)["edges"][1]["resolution"] == "static")
    # an unproven added element with no evidence cannot be built (validator), so the inferred note is the way in
    # VC-07 / S9: flow caps
    def flow_graph(n, steps_status="added"):
        ids = ["s%d" % i for i in range(1, n + 1)]
        nodes = [{"id": s, "kind": "step", "label": "step %s" % s, "lane": "L"} for s in ids]
        bg = _mk(view_="L1")
        bg["lanes"] = [{"id": "L", "label": "lane"}]
        ag = _mk(nodes, view_="L1")
        ag["lanes"] = bg["lanes"]
        ag["flows"] = [{"id": "f", "label": "f", "status": "changed", "after": ids, "evidence": [{"tier": "inferred", "note": "plan"}]}]
        bg["flows"] = [{"id": "f", "label": "f", "status": "context", "before": [], "evidence": [{"tier": "inferred", "note": "plan"}]}]
        return bg, ag
    for n, ok in ((12, True), (13, False), (15, False)):
        try:
            m = merge(*flow_graph(n))
            check("VC-07:%d-steps" % n, ok)
        except DiffError as exc:
            check("VC-07:%d-steps" % n, (not ok) and codes(exc) == ["over_budget"] and "'f'" in exc.issues[0]["message"], str(exc))
    # S8 / VC-08: a 3-step sequence repeated 5 times, as distinct nodes with the same labels
    ids = []
    nodes, edges = [], []
    for r in range(5):
        for k in "abc":
            sid = "s%d%s" % (r, k)
            ids.append(sid)
            nodes.append({"id": sid, "kind": "step", "label": "do " + k, "lane": "L"})
    for x, y in zip(ids, ids[1:]):
        edges.append({"source": x, "target": y, "kind": "flow"})
    bg = _mk(view_="L1")
    bg["lanes"] = [{"id": "L", "label": "lane"}]
    ag = _mk(nodes, edges, view_="L1")
    ag["lanes"] = bg["lanes"]
    ag["flows"] = [{"id": "f", "label": "f", "status": "changed", "after": ids, "evidence": [{"tier": "inferred", "note": "plan"}]}]
    m = merge(bg, ag)
    f = m["flows"][0]
    check("VC-08:collapsed", f["after"] == ["s0a", "s0b", "s0c"] and len(m["nodes"]) == 3, str(f))
    check("VC-08:count", m["meta"]["collapsed_runs"]["f"] == [{"side": "after", "steps": ["s0a", "s0b", "s0c"], "count": 5}]
          and all(n["label"].endswith(" x5") for n in m["nodes"]))
    check("VC-08:edges-valid", vr_graph.validate(m, check_budget=False) == [] and len(m["edges"]) == 3, str(len(m["edges"])))
    # a collapsed label at the 80-character limit is trimmed to take its " xN" suffix, not rejected
    long_label = "q" * vr_graph.NODE_LABEL_MAX
    lids, lnodes = [], []
    for r in range(5):
        for k in "abc":
            lids.append("s%d%s" % (r, k))
            lnodes.append({"id": "s%d%s" % (r, k), "kind": "step", "label": long_label if k == "a" else "do " + k, "lane": "L"})
    ag = _mk(lnodes, [{"source": x, "target": y, "kind": "flow"} for x, y in zip(lids, lids[1:])], view_="L1")
    ag["lanes"] = bg["lanes"]
    ag["flows"] = [{"id": "f", "label": "f", "status": "changed", "after": lids, "evidence": [{"tier": "inferred", "note": "plan"}]}]
    try:
        m = merge(bg, ag)
        la = next(n["label"] for n in m["nodes"] if n["id"] == "s0a")
        check("VC-08:long-label-trimmed", len(la) <= vr_graph.NODE_LABEL_MAX and la.endswith("\u2026 x5") and vr_graph.validate(m, check_budget=False) == [], la)
    except DiffError as exc:
        check("VC-08:long-label-trimmed", False, str(exc))
    # same node ids repeated, 15 steps over the cap before collapse, 3 after
    ag = _mk([{"id": k, "kind": "step", "label": "do " + k, "lane": "L"} for k in "abc"], view_="L1")
    ag["lanes"] = bg["lanes"]
    ag["flows"] = [{"id": "f", "label": "f", "status": "changed", "after": list("abc") * 5, "evidence": [{"tier": "inferred", "note": "plan"}]}]
    m = merge(bg, ag)
    check("VC-08:same-ids", m["flows"][0]["after"] == list("abc") and m["meta"]["collapsed_runs"]["f"][0]["count"] == 5)
    # a run that is not contiguous stays expanded
    ag["flows"][0]["after"] = list("abcxabc")[:0] + ["a", "b", "c", "a", "b"]
    check("VC-08:short-run-not-a-run", merge(bg, ag)["flows"][0]["after"] == ["a", "b", "c", "a", "b"])
    # flow merge by id: before and after steps of the same flow
    l1 = vr_graph.load(os.path.join(fx, "w145-l1.json"))
    mf = merged_all.get("w145-l1")
    if mf:
        fl = mf["flows"][0]
        check("flow:w145", fl["before"] == l1["flows"][0]["before"] and fl["after"] == l1["flows"][0]["after"] and fl["status"] == "changed")
    # view is byte-stable for survivors and does not mutate its input
    if rm:
        snap = json.dumps(rm, sort_keys=True)
        view(rm, "before")
        check("view:pure", json.dumps(rm, sort_keys=True) == snap)
    try:
        view({}, "bogus")
        check("view:bad-mode", False)
    except DiffError:
        pass
    # after_from_git on a throw-away repo with a fake extractor: worktree under $TMPDIR, removed, evidence from the diff
    with tempfile.TemporaryDirectory() as d:
        repo = os.path.join(d, "demo")
        os.makedirs(os.path.join(repo, "crates", "a", "src"))

        def git(*a):
            return subprocess.run(["git", "-C", repo, "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false"] + list(a),
                                  capture_output=True, text=True, check=True).stdout
        subprocess.run(["git", "init", "-q", repo], check=True)
        w = lambda rel, txt: open(os.path.join(repo, rel), "w").write(txt)
        w("crates/a/Cargo.toml", '[package]\nname = "a"\n')
        w("crates/a/src/lib.rs", "pub mod x;\npub mod y;\n")
        w("crates/a/src/x.rs", "pub fn old() {}\n")
        w("crates/a/src/y.rs", "pub fn keep() {}\n")
        w("README.md", "hi\n")
        git("add", "-A")
        git("commit", "-qm", "base")
        w("crates/a/src/x.rs", "pub fn old() {}\npub fn planned(a: u32) -> u32 {\n    todo!()\n}\n")
        w("crates/a/src/lib.rs", "pub mod x;\npub mod y;\npub mod z;\n")
        w("crates/a/src/z.rs", "pub struct Z;\n")
        w("README.md", "hi there\n")
        git("add", "-A")
        git("commit", "-qm", "contract")
        seen = {}

        def fake(root, layers, crates=None):
            seen["root"], seen["layers"], seen["crates"] = root, layers, crates
            seen["has_z"] = os.path.exists(os.path.join(root, "crates/a/src/z.rs"))
            ev = lambda p, l: {"tier": "source", "path": p, "line": l}
            nd = lambda i, k, e: {"id": i, "label": i.split(":")[-1], "kind": k, "status": "context", "label_source": "extractor", "evidence": [e]}
            return {"schema_version": "1.0.0", "view": "full", "title": "x",
                    "meta": {"repo": "tmp", "commit": "abc", "generated_by": "extractor", "tools": []},
                    "nodes": [nd("crate:a", "crate", ev("crates/a/Cargo.toml", 1)), nd("module:a::x", "module", ev("crates/a/src/lib.rs", 1)),
                              nd("module:a::y", "module", ev("crates/a/src/lib.rs", 2)), nd("module:a::z", "module", ev("crates/a/src/lib.rs", 3))],
                    "edges": []}
        try:
            g = after_from_git(repo, "HEAD~1", "HEAD", (1, 2), extractor=fake)
        except DiffError as exc:
            g = None
            check("git:run", False, str(exc))
        if g:
            sts = stat(g)
            check("git:ran-at-head", seen["has_z"] and seen["layers"] == (1, 2))
            check("git:crates-default-none", seen["crates"] is None)
            after_from_git(repo, "HEAD~1", "HEAD", (1, 2), extractor=fake, crates=["a"])
            check("git:crates-passed-through", seen["crates"] == ["a"], str(seen["crates"]))
            check("git:under-tmpdir", not os.path.realpath(seen["root"]).startswith(os.path.realpath(repo) + os.sep)
                  and os.path.realpath(seen["root"]).startswith(os.path.realpath(tempfile.gettempdir())), seen["root"])
            check("git:worktree-removed", not os.path.exists(seen["root"]) and len(git("worktree", "list").strip().splitlines()) == 1)
            check("git:changed", sts["module:a::x"] == "changed" and sts["module:a::z"] == "changed" and sts["module:a::y"] == "context"
                  and sts["crate:a"] == "changed", str(sts))
            x = next(n for n in g["nodes"] if n["id"] == "module:a::x")
            check("git:stub-signature", "planned(a: u32) -> u32" in x["detail"]["after"] and
                  any(e.get("note", "").startswith("stub signature") and e["path"] == "crates/a/src/x.rs" and e["line"] == 2 for e in x["evidence"]), str(x))
            check("git:unmapped", g["meta"]["from_git"]["unmapped"] == ["README.md"])
            # and the merge with the base side
            bef = copy.deepcopy(g)
            bef["nodes"] = [n for n in bef["nodes"] if n["id"] != "module:a::z"]
            for n in bef["nodes"]:
                n["status"] = "context"
                n.pop("detail", None)
                n["evidence"] = [n["evidence"][0]]
            m = merge(bef, g)
            ms = stat(m)
            check("git:merge", ms["module:a::z"] == "added" and ms["module:a::x"] == "changed" and ms["module:a::y"] == "context", str(ms))
        try:
            after_from_git(repo, "nosuchrev", "HEAD", (1,), extractor=fake)
            check("git:bad-rev", False)
        except DiffError as exc:
            check("git:bad-rev", codes(exc) == ["bad_revision"])
        def boom(root, layers):
            raise vr_extract.ExtractError("cargo is broken")
        try:
            after_from_git(repo, "HEAD~1", "HEAD", (1,), extractor=boom)
            check("git:extract-fail", False)
        except DiffError:
            pass
        check("git:cleanup-on-failure", len(git("worktree", "list").strip().splitlines()) == 1)
    # CLI: merge and view through the files, exit codes
    with tempfile.TemporaryDirectory() as d:
        full = vr_graph.load(os.path.join(fx, "removed.json"))
        b, a = _split(full)
        paths = {}
        for k, g in (("b", b), ("a", a)):
            paths[k] = os.path.join(d, k + ".json")
            json.dump(g, open(paths[k], "w"))
        me = os.path.join(d, "m.json")
        run = lambda *args: subprocess.run([sys.executable, os.path.abspath(__file__)] + list(args), capture_output=True, text=True)
        r = run("merge", "--before", paths["b"], "--after", paths["a"], "-o", me)
        check("cli:merge", r.returncode == 0 and "added" in r.stderr and os.path.exists(me), r.stderr)
        r = run("view", me, "--mode", "after")
        check("cli:view", r.returncode == 0 and "removed" not in {n["status"] for n in json.loads(r.stdout)["nodes"]})
        r = run("view", me, "--mode", "nope")
        check("cli:view-bad-mode", r.returncode == 2 or r.returncode == 1)
        stale = copy.deepcopy(a)
        stale["nodes"].append(dict(stale["nodes"][0], id="module:demo::ghost", status="removed"))
        json.dump(stale, open(paths["a"], "w"))
        r = run("merge", "--before", paths["b"], "--after", paths["a"])
        check("cli:stale-exit-1", r.returncode == 1 and "removed_element_missing" in r.stderr and r.stdout == "", r.stderr)
        r = run("merge", "--before", os.path.join(d, "none.json"), "--after", paths["a"])
        check("cli:missing-file-exit-2", r.returncode == 2)
    if fails:
        print("vr_diff selftest FAILED:\n  " + "\n  ".join(fails), file=sys.stderr)
        return 1
    print("vr_diff selftest ok")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    ap.add_argument("--selftest", action="store_true")
    sub = ap.add_subparsers(dest="cmd")
    m = sub.add_parser("merge", help="merge a before and an after graph")
    m.add_argument("--before", required=True)
    m.add_argument("--after", required=True)
    m.add_argument("-o", "--output")
    m.set_defaults(fn=_cmd_merge)
    v = sub.add_parser("view", help="filter a merged graph to one mode")
    v.add_argument("file")
    v.add_argument("--mode", required=True, choices=sorted(MODES))
    v.add_argument("-o", "--output")
    v.set_defaults(fn=_cmd_view)
    g = sub.add_parser("after-from-git", help="the plan-time after graph from two commits")
    g.add_argument("repo")
    g.add_argument("--base", required=True)
    g.add_argument("--head", required=True)
    g.add_argument("--layers", default="1,2")
    g.add_argument("--crates", default=None, help="comma list: layer 2 only for these crates (as vr_extract.py --crates)")
    g.add_argument("-o", "--output", required=True)
    g.set_defaults(fn=_cmd_after)
    args = ap.parse_args(argv)
    if args.selftest:
        return _selftest()
    if not args.cmd:
        ap.print_usage(sys.stderr)
        return 2
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
