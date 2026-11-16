#!/usr/bin/env python3
"""Promote an owner-reviewed L1 flow to a docs/architecture sequence diagram.

    vr_promote.py --dossier-id ID --stem STEM --repo PATH [--preview]
    vr_promote.py --selftest

Reads `.discovery/visual-review/<ID>/l1.json` and `review.json`, takes the flow whose
`new_stem` is STEM and maps it to a `seq` graph:

  lanes -> participants (unchanged); steps (the flow's `after` list, in order) -> `message` nodes on the
  step's lane, `seq.to` = the step's `to` lane, arrow from `call_kind` (call `->>`, reply `-->>`,
  async and publish `-)`); evidence anchors carried over; steps the owner marked `remove` dropped.

Then every source anchor whose note holds a backticked identifier is re-anchored: the identifier is
searched in the file at HEAD and the line updated (Phase 3 anchors point at stubs, the lines move once
the bodies exist). An anchor that no longer resolves prints `STALE-ANCHOR <element> <path>:<line> <id>`
and the exit code is 1. A step whose current review line is `change` (never replaced by an `ok` on the
revised graph) exits 1 with the element id.

Default: writes `docs/architecture/<stem>.json`, creates `<stem>.md` with the marker block when absent,
runs vr_graph validate and `vr_mermaid.py sync docs/architecture`. `--preview` (Phase 3, before the owner
approves) only writes `.discovery/visual-review/<ID>/<stem>.seq.json`: no re-anchoring (anchors still
point at stubs), no review check, nothing under docs/.

Exit: 0 done, 1 a stale anchor, an unresolved `change` verdict or a validation failure, 2 usage / input error.
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import vr_graph  # noqa: E402

ARROWS = {"call": "->>", "reply": "-->>", "async": "-)", "publish": "-)"}
MD_STUB = (
    "# {stem}\n\n{line}\n\n"
    "<!-- generated from {stem}.json by vr_mermaid.py render; edit the JSON, not this block -->\n"
    "```mermaid\nsequenceDiagram\n```\n<!-- end generated -->\n"
)


class PromoteError(Exception):
    pass


def _load(path: str) -> dict:
    try:
        with open(path) as fh:
            return json.load(fh)
    except (OSError, ValueError) as exc:
        raise PromoteError("cannot read %s: %s" % (path, exc))


def current_verdicts(review: dict | None) -> dict:
    """{L1 element id: verdict}, the latest line (by ts) per element."""
    best: dict = {}
    for ln in (review or {}).get("lines", []):
        a = ln.get("anchor", {})
        if a.get("diagram") != "L1":
            continue
        e = a.get("element")
        if e not in best or ln.get("ts", "") >= best[e].get("ts", ""):
            best[e] = ln
    return {e: ln.get("verdict") for e, ln in best.items()}


def promote_graph(l1: dict, stem: str, verdicts: dict, check_review: bool = True) -> dict:
    """The seq graph for the flow with new_stem == stem. Raises PromoteError on an input defect."""
    flows = [f for f in l1.get("flows", []) if f.get("new_stem") == stem]
    if len(flows) != 1:
        raise PromoteError("expected exactly one flow with new_stem %r in l1.json, found %d" % (stem, len(flows)))
    flow = flows[0]
    nodes = {n["id"]: n for n in l1.get("nodes", [])}
    lane_ids = {x["id"] for x in l1.get("lanes", [])}
    kept = []
    for sid in flow.get("after", []):
        n = nodes.get(sid)
        if n is None:
            raise PromoteError("flow %s lists unknown step %s" % (flow["id"], sid))
        v = verdicts.get(sid)
        if v == "remove":
            continue
        if v == "change" and check_review:
            raise PromoteError("UNRESOLVED-CHANGE %s: the owner asked for a change and no later ok replaced it" % sid)
        for field in ("to", "call_kind"):
            if n.get(field) is None:
                raise PromoteError("step %s has no %s (a flow with new_stem needs both)" % (sid, field))
        if n["to"] not in lane_ids:
            raise PromoteError("step %s: `to` %s is not a lane" % (sid, n["to"]))
        kept.append(n)
    if not kept:
        raise PromoteError("no step left in flow %s after the owner's removals" % flow["id"])
    out_nodes, out_edges = [], []
    for i, s in enumerate(kept, 1):
        out_nodes.append({"id": s["id"], "label": s["label"], "kind": "message", "status": s.get("status", "added"),
                          "lane": s["lane"], "order": i,
                          "seq": {"to": s["to"], "arrow": ARROWS[s["call_kind"]]},
                          "evidence": copy.deepcopy(s["evidence"])})
        if i > 1:
            out_edges.append({"id": "f%d" % (i - 1), "source": kept[i - 2]["id"], "target": s["id"], "kind": "flow",
                              "status": s.get("status", "added"), "evidence": copy.deepcopy(s["evidence"])})
    used = {s["lane"] for s in kept} | {s["to"] for s in kept}
    lanes = [copy.deepcopy(x) for x in l1["lanes"] if x["id"] in used]
    meta = dict(l1.get("meta") or {})
    meta["mermaid"] = {"type": "sequenceDiagram"}
    meta["promoted_from"] = "visual-review L1 flow %s" % flow["id"]
    return {"schema_version": l1["schema_version"], "view": "seq", "title": l1.get("title", stem), "meta": meta,
            "lanes": lanes, "nodes": out_nodes, "edges": out_edges, "groups": [],
            "flows": [{"id": re.sub(r"[^A-Za-z0-9_.:-]", "-", stem), "label": stem[:60], "status": "added",
                       "after": [n["id"] for n in out_nodes], "evidence": copy.deepcopy(kept[0]["evidence"])}]}


def _file_at_head(repo: str, path: str) -> list | None:
    p = subprocess.run(["git", "-C", repo, "show", "HEAD:" + path], capture_output=True, text=True)
    if p.returncode == 0:
        return p.stdout.split("\n")
    fp = os.path.join(repo, path)
    if os.path.isfile(fp):
        with open(fp, errors="replace") as fh:
            return fh.read().split("\n")
    return None


def reanchor(graph: dict, repo: str) -> list:
    """Update each source anchor that has a backticked identifier in its note. Returns STALE-ANCHOR lines."""
    stale = []
    cache: dict = {}
    parts = [("node", n) for n in graph["nodes"]] + [("edge", e) for e in graph["edges"]] + [("flow", f) for f in graph["flows"]]
    for _, part in parts:
        for ev in part.get("evidence", []):
            if ev.get("tier") != "source" or "line" not in ev:
                continue
            m = re.search(r"`([A-Za-z_][\w:.]*)`", ev.get("note", ""))
            if not m:
                continue
            ident = m.group(1).split("::")[-1].split(".")[-1]
            lines = cache.setdefault(ev["path"], _file_at_head(repo, ev["path"]))
            where = "%s %s:%s %s" % (part["id"], ev["path"], ev["line"], ident)
            if lines is None:
                stale.append("STALE-ANCHOR " + where + " (file gone)")
                continue
            word = re.compile(r"(?<![\w])" + re.escape(ident) + r"(?![\w])")
            hits = [i + 1 for i, ln in enumerate(lines) if word.search(ln)]
            if not hits:
                stale.append("STALE-ANCHOR " + where)
                continue
            if ev["line"] in hits:
                continue
            defs = [i for i in hits if re.search(r"\b(fn|def|class|struct|enum|trait|impl|interface|type|func|function)\b", lines[i - 1])]
            pool = defs or hits
            new = min(pool, key=lambda i: abs(i - ev["line"]))
            if "end_line" in ev:
                ev["end_line"] = max(new, ev["end_line"] + (new - ev["line"]))
            ev["line"] = new
    return stale


def _sync(repo: str) -> None:
    p = subprocess.run([sys.executable, os.path.join(HERE, "vr_mermaid.py"), "sync", "docs/architecture"],
                       cwd=repo, capture_output=True, text=True)
    if p.returncode != 0:
        raise PromoteError("vr_mermaid.py sync failed: " + (p.stdout + p.stderr).strip()[:300])


def run(dossier_id: str, stem: str, repo: str, preview: bool = False) -> int:
    vr = os.path.join(repo, ".discovery", "visual-review", dossier_id)
    l1 = _load(os.path.join(vr, "l1.json"))
    rpath = os.path.join(vr, "review.json")
    review = _load(rpath) if os.path.isfile(rpath) else None
    try:
        graph = promote_graph(l1, stem, current_verdicts(review), check_review=not preview)
    except PromoteError as exc:
        print("ERROR " + str(exc))
        return 1 if str(exc).startswith("UNRESOLVED-CHANGE") else 2
    if not preview:
        stale = reanchor(graph, repo)
        for s in stale:
            print(s)
        if stale:
            return 1
    issues = vr_graph.validate(graph, root=None if preview else repo)
    if issues:
        print("ERROR the promoted graph does not validate: " + json.dumps(issues)[:600])
        return 1
    if preview:
        out = os.path.join(vr, stem + ".seq.json")
    else:
        os.makedirs(os.path.join(repo, "docs", "architecture"), exist_ok=True)
        out = os.path.join(repo, "docs", "architecture", stem + ".json")
    with open(out, "w") as fh:
        json.dump(graph, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    print("wrote " + os.path.relpath(out, repo))
    if preview:
        return 0
    md = os.path.join(repo, "docs", "architecture", stem + ".md")
    if not os.path.exists(md):
        with open(md, "w") as fh:
            fh.write(MD_STUB.format(stem=stem, line="The flow: " + graph["title"] + "."))
        print("wrote " + os.path.relpath(md, repo))
    try:
        _sync(repo)
    except PromoteError as exc:
        print("ERROR " + str(exc))
        return 1
    print("synced docs/architecture")
    return 0


# --------------------------------------------------------------------------- selftest

def _selftest() -> int:
    fails: list = []

    def check(name, cond, detail=""):
        if not cond:
            fails.append("%s %s" % (name, detail))

    def git(d, *a):
        subprocess.run(["git", "-C", d, "-c", "user.email=t@t", "-c", "user.name=t", *a], check=True, capture_output=True)

    base = vr_graph.load(os.path.join(vr_graph.FIXTURES, "w145-l1.json"))

    def make_l1(src_stale=False):
        g = copy.deepcopy(base)
        kinds = ["call", "reply", "async", "publish", "call", "call", "call", "call", "call", "call"]
        lanes = [x["id"] for x in g["lanes"]]
        for i, n in enumerate(g["nodes"]):
            n["to"] = lanes[(lanes.index(n["lane"]) + 1) % len(lanes)]
            n["call_kind"] = kinds[i % len(kinds)]
            n.pop("detail", None)
            n["evidence"] = [{"tier": "source", "path": "src/a.rs", "line": 1, "note": "`step_%d`" % i}]
        g["flows"][0]["new_stem"] = "seq-new"
        g["flows"][0].pop("before", None)
        return g

    def repo_with(l1, review=None, shifted=True):
        d = tempfile.mkdtemp()
        os.makedirs(os.path.join(d, "src"))
        body = ["// header"] * (5 if shifted else 0)
        body += ["fn step_%d() {}" % i for i in range(10)]
        with open(os.path.join(d, "src", "a.rs"), "w") as fh:
            fh.write("\n".join(body) + "\n")
        git(d, "init", "-q", "-b", "main")
        git(d, "add", "-A")
        git(d, "commit", "-qm", "x")
        vr = os.path.join(d, ".discovery", "visual-review", "W-9")
        os.makedirs(vr)
        json.dump(l1, open(os.path.join(vr, "l1.json"), "w"))
        if review is not None:
            json.dump({"schema_version": "1.0.0", "dossier": "W-9", "updated": "2026-01-01T00:00:00Z", "lines": review},
                      open(os.path.join(vr, "review.json"), "w"))
        return d

    def line(el, verdict, ts="2026-01-01T00:00:00Z"):
        return {"anchor": {"diagram": "L1", "element": el, "graph_hash": "0" * 64}, "verdict": verdict, "comment": "", "ts": ts}

    import contextlib
    import io

    def call(d, preview=False):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = run("W-9", "seq-new", d, preview)
        return rc, buf.getvalue()

    # happy path: lines move, arrows follow call_kind, a removed step goes, md + sync
    l1 = make_l1()
    d = repo_with(l1, [line("s2", "remove"), line("s1", "ok")])
    rc, out = call(d)
    check("happy:rc", rc == 0, out)
    g = json.load(open(os.path.join(d, "docs", "architecture", "seq-new.json")))
    ids = [n["id"] for n in g["nodes"]]
    check("happy:remove", "s2" not in ids and "s1" in ids, ids)
    check("happy:view", g["view"] == "seq" and g["flows"][0]["after"] == ids)
    check("happy:arrows", g["nodes"][0]["seq"]["arrow"] == "->>" and g["nodes"][1]["seq"]["arrow"] == "-)", [n["seq"] for n in g["nodes"][:2]])
    check("happy:reanchored", g["nodes"][0]["evidence"][0]["line"] == 6, g["nodes"][0]["evidence"])
    check("happy:md", "sequenceDiagram" in open(os.path.join(d, "docs", "architecture", "seq-new.md")).read())
    p = subprocess.run([sys.executable, os.path.join(HERE, "vr_mermaid.py"), "check", "docs/architecture"], cwd=d, capture_output=True, text=True)
    check("happy:mermaid-check", p.returncode == 0, p.stdout + p.stderr)
    # a reply arrow
    l1r = make_l1()
    check("arrow:reply", promote_graph(l1r, "seq-new", {})["nodes"][1]["seq"]["arrow"] == "-->>")
    # unresolved change
    d = repo_with(make_l1(), [line("s1", "change")])
    rc, out = call(d)
    check("change:exit1", rc == 1 and "s1" in out, out)
    d = repo_with(make_l1(), [line("s1", "change", "2026-01-01T00:00:00Z"), line("s1", "ok", "2026-01-02T00:00:00Z")])
    check("change-then-ok:passes", call(d)[0] == 0)
    # stale anchor
    g1 = make_l1()
    g1["nodes"][0]["evidence"][0]["note"] = "`gone_fn`"
    d = repo_with(g1, [])
    rc, out = call(d)
    check("stale:exit1", rc == 1 and "STALE-ANCHOR s1 src/a.rs:1 gone_fn" in out, out)
    # preview: nothing under docs/, no re-anchoring, change tolerated
    d = repo_with(make_l1(), [line("s1", "change")])
    rc, out = call(d, preview=True)
    pv = os.path.join(d, ".discovery", "visual-review", "W-9", "seq-new.seq.json")
    check("preview", rc == 0 and os.path.isfile(pv) and not os.path.exists(os.path.join(d, "docs")), out)
    # no flow / missing fields
    d = repo_with(make_l1(), [])
    with contextlib.redirect_stdout(io.StringIO()):
        check("noflow:exit2", run("W-9", "seq-other", d) == 2)
    bad = make_l1()
    del bad["nodes"][0]["call_kind"]
    check("missing-kind", _raises(lambda: promote_graph(bad, "seq-new", {})))
    for f in fails:
        print("FAIL  " + f)
    print("vr_promote selftest " + ("ok" if not fails else "FAILED"))
    return 0 if not fails else 1


def _raises(fn) -> bool:
    try:
        fn()
    except PromoteError:
        return True
    return False


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--dossier-id")
    ap.add_argument("--stem")
    ap.add_argument("--repo", default=".")
    ap.add_argument("--preview", action="store_true")
    a = ap.parse_args(argv)
    if a.selftest:
        return _selftest()
    if not (a.dossier_id and a.stem):
        ap.error("--dossier-id and --stem are required")
    return run(a.dossier_id, a.stem, os.path.abspath(a.repo), a.preview)


if __name__ == "__main__":
    sys.exit(main())
