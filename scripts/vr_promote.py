#!/usr/bin/env python3
"""Promote an owner-reviewed NEW architecture diagram (the dossier's `Diagrams new:` stems) to docs/architecture.

    vr_promote.py --dossier-id ID --stem STEM --repo PATH [--preview | --check-approval]
    vr_promote.py --shadow-all --dossier-id ID --repo PATH
    vr_promote.py --selftest

The owner reviews each new diagram as an "Architecture impact" tab of the visual review: the draft
`.discovery/visual-review/<ID>/arch/<STEM>.json` (anchor `arch:<STEM>`, the same verdicts and the same exact-version
approval as L0/L1/L2). Two kinds of stem:

  * a sequence stem: the L1 flow of `l1.json` whose `new_stem` is STEM; `--preview` writes the draft from it (below);
  * a c1 / c4 stem: the `diagram-author` writes the draft itself, in the repo's c1/c4 JSON format; `--preview` only
    validates it.

`--check-approval` and the default promotion read the draft and `review.json`: every element of the draft needs an `ok`
line whose `graph_hash` is the draft's current hash (`remove` drops the element, `change` / `question` block). The
default promotion then copies the approved draft (without the removed elements), re-anchors its evidence (below),
validates, writes `docs/architecture/<STEM>.json`, the `.md` stub when absent, and runs `vr_mermaid.py sync`.
A dossier reviewed before the arch tabs existed has no draft: a sequence stem then still promotes from `l1.json` and
the owner's L1 verdicts, as it did.

The sequence draft is made from `l1.json` and `review.json`, takes the flow whose
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
approves) only writes `.discovery/visual-review/<ID>/arch/<stem>.json`: no re-anchoring (anchors still
point at stubs), no review check, nothing under docs/.

`--shadow-all` (driver `vr_promote_preview`, ASD_DRIVER_VR_PROMOTE_PREVIEW; off: exit 3, shadow: runs, on: exit 2,
there is no on mode: promotion stays tied to `Diagrams new:`) measures whether a seq draft could be built
mechanically for every L1 flow with an added or changed step, not only the stems a dossier lists. For each such flow it
builds the draft exactly as `--preview` does (a flow with no `new_stem` gets the stem `seq-shadow-<flow id>`),
validates it the same way, and writes nothing under the dossier's visual-review directory: the drafts and one
`vr_promote_preview-<ts>.json` (per flow: steps, built ok or the errors, lanes, steps missing `to` / `call_kind`) go
into `<git common dir>/asd-drivers/driver-shadow/<ID>/`. l1.json and review.json are only read. Exit 0 when every flow
built, 1 when any flow failed to build (the shadow evidence says which), 2 when l1.json is missing or unusable.

Exit: 0 done, 1 a stale anchor, an unresolved `change` verdict or a validation failure, 2 usage / input error,
3 (`--shadow-all` only) the driver is off.
"""
from __future__ import annotations

import argparse
import copy
import difflib
import hashlib
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
    "```mermaid\n{head}\n```\n<!-- end generated -->\n"
)
ARCH_DIR = "arch"
ARCH_VIEWS = ("c1", "c4", "seq")
STEM_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$")  # the dashboard's ARCH_STEM: no dot, no slash
MERMAID_DEFAULT = {"c1": {"type": "flowchart", "header": "flowchart LR"}, "c4": {"type": "classDiagram"},
                   "seq": {"type": "sequenceDiagram"}}
STUB_LINE = {"c1": "The context: ", "c4": "The classes: ", "seq": "The flow: "}


class PromoteError(Exception):
    pass


def _load(path: str) -> dict:
    try:
        with open(path) as fh:
            return json.load(fh)
    except (OSError, ValueError) as exc:
        raise PromoteError("cannot read %s: %s" % (path, exc))


def canon(v):
    """The dashboard's canon(stripDollar(v)): `$`-prefixed keys dropped at any depth, keys sorted, whole floats as ints."""
    if isinstance(v, list):
        return [canon(x) for x in v]
    if isinstance(v, dict):
        return {k: canon(v[k]) for k in sorted(v) if not k.startswith("$")}
    if isinstance(v, float) and v == int(v):
        return int(v)
    return v


def graph_hash(graph) -> str:
    """Port of `graphHash` in shared/questions/vrlayout.ts: sha256 of the canonical compact JSON. The review lines'
    `anchor.graph_hash` is this hash of the file the owner saw. Cross-checked against shared/test/vrhash.test.ts
    through fixtures/hash-case.json (HASH_CASE_EXPECTED)."""
    txt = json.dumps(canon(graph), separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(txt.encode("utf-8")).hexdigest()


HASH_CASE_EXPECTED = "8ce8ade17ad88b468fe732549001fa366d19a7ee5fba0c509ad28cd8b081434a"


def approval(l1_hash: str, review: dict | None, step_ids: list, diagram: str = "L1", fname: str = "l1.json") -> tuple:
    """(removed ids, problems). The owner's approval is tied to the exact file (`diagram` is its anchor: L1, or
    `arch:<stem>` for a draft in arch/): every promoted element (a step of the flow) needs an `ok`
    line whose graph_hash is the current hash. A `change` or `question` line on the current hash is unresolved, and
    an `ok` never clears it (any order). A line on an older hash proves nothing about this file: the older versions
    of l1.json are not kept, so there is no way to tell whether the author revised that element, and the current-hash
    approval is required (STALE-APPROVAL). `remove` at any hash drops the step (the scope decision stands)."""
    lines: dict = {}
    for ln in (review or {}).get("lines", []):
        a = ln.get("anchor", {})
        if a.get("diagram") == diagram:
            lines.setdefault(a.get("element"), []).append(ln)
    removed = {e for e, ls in lines.items() if any(x.get("verdict") == "remove" for x in ls)}
    problems = []
    for sid in step_ids:
        if sid in removed:
            continue
        ls = lines.get(sid, [])
        cur = [x for x in ls if x["anchor"].get("graph_hash") == l1_hash]
        open_ = [x for x in cur if x.get("verdict") in ("change", "question")]
        if open_:
            problems.append("UNRESOLVED-CHANGE %s: a %s verdict on the current %s hash is open (%s)"
                            % (sid, open_[0]["verdict"], diagram, (open_[0].get("comment") or "no comment")[:80]))
        elif any(x.get("verdict") == "ok" for x in cur):
            continue
        elif ls:
            problems.append("STALE-APPROVAL %s: the owner's lines are on an older %s hash; %s changed since "
                            "(older versions are not kept): the owner approves the current file again" % (sid, diagram, fname))
        else:
            problems.append("NOT-APPROVED %s: no verdict for the step" % sid)
    return removed, problems


def promote_graph(l1: dict, stem: str, removed: set) -> dict:
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
        if sid in removed:
            continue
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


def _git_lines(repo: str, rev: str, path: str) -> list | None:
    p = subprocess.run(["git", "-C", repo, "show", "%s:%s" % (rev, path)], capture_output=True, text=True)
    if p.returncode == 0:
        return p.stdout.split("\n")
    return None


def _file_at_head(repo: str, path: str) -> list | None:
    got = _git_lines(repo, "HEAD", path)
    if got is not None:
        return got
    fp = os.path.join(repo, path)
    if os.path.isfile(fp):
        with open(fp, errors="replace") as fh:
            return fh.read().split("\n")
    return None


# Per-language "name of the function a line defines" scanners; add a language by adding an extension here.
_RS_FN = re.compile(r"^\s*(?:pub(?:\([^)]*\))?\s+)?(?:default\s+)?(?:const\s+)?(?:async\s+)?(?:unsafe\s+)?"
                    r"(?:extern\s+\"[^\"]*\"\s+)?fn\s+([A-Za-z_]\w*)")
DEF_SCANNERS = {".rs": _RS_FN}


def fn_def_name(path: str, text: str) -> str | None:
    rx = DEF_SCANNERS.get(os.path.splitext(path)[1])
    m = rx.match(text) if rx else None
    return m.group(1) if m else None


def enclosing_fn(path: str, lines: list, line: int) -> str | None:
    """The function the 1-based `line` of a base file sits in: the nearest definition at or above it."""
    if path and os.path.splitext(path)[1] not in DEF_SCANNERS:
        return None
    for i in range(min(line, len(lines)), 0, -1):
        n = fn_def_name(path, lines[i - 1])
        if n:
            return n
    return None


def _renames(repo: str, base: str) -> dict:
    """{old path: new path or None when deleted} from `git diff -M --name-status base HEAD`."""
    p = subprocess.run(["git", "-C", repo, "diff", "-M", "--name-status", base, "HEAD"], capture_output=True, text=True)
    out: dict = {}
    for ln in p.stdout.splitlines():
        f = ln.split("\t")
        if f[0].startswith("R") and len(f) == 3:
            out[f[1]] = f[2]
        elif f[0] == "D" and len(f) == 2:
            out[f[1]] = None
    return out


def map_line(base_lines: list, head_lines: list, line: int, path: str) -> tuple:
    """(new line, how) or (None, why). Unchanged region: through the difflib opcodes; changed hunk: the definition of the
    function that encloses the line at base, found at HEAD (nearest to where the line was)."""
    sm = difflib.SequenceMatcher(None, base_lines, head_lines, autojunk=False)
    idx = line - 1
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal" and i1 <= idx < i2:
            return j1 + (idx - i1) + 1, "unchanged"
    name = enclosing_fn(path, base_lines, line)
    if name is None:
        return None, "line is in a changed hunk and no enclosing function is known"
    hits = [i + 1 for i, ln in enumerate(head_lines) if fn_def_name(path, ln) == name]
    if not hits:
        return None, "enclosing function %s is gone at HEAD" % name
    return min(hits, key=lambda i: abs(i - line)), "function " + name


def reanchor(graph: dict, repo: str, base_sha: str | None) -> list:
    """Update every source anchor with a line. Returns the output lines (WARN and STALE-ANCHOR); any STALE-ANCHOR fails.

    With base_sha: renames and deletions through `git diff -M --name-status`, the line through map_line, and a
    backticked name in the note as a cross-check (it must be in the new line, or be the enclosing function).
    Without: the old lookup by the backticked name, and a WARN."""
    out: list = []
    renames = _renames(repo, base_sha) if base_sha else {}
    if base_sha and subprocess.run(["git", "-C", repo, "cat-file", "-e", base_sha + "^{commit}"], capture_output=True).returncode:
        return ["STALE-ANCHOR * meta.base_sha %s is not a commit of this repository" % base_sha]
    if not base_sha:
        out.append("WARN the graph has no meta.base_sha: anchors are re-found by their backticked name only; "
                   "an anchor without one keeps its Phase 3 line")
    cache: dict = {}
    # A sequence graph: edges and the flow copy their step's anchors afterwards. A c1/c4 graph: every element has its own.
    parts = graph["nodes"] if graph.get("view") == "seq" else graph["nodes"] + graph.get("edges", []) + graph.get("groups", [])
    for part in parts:
        for ev in part.get("evidence", []):
            if ev.get("tier") != "source" or "line" not in ev:
                continue
            old_path, old_line = ev["path"], ev["line"]
            m = re.search(r"`([A-Za-z_][\w:.]*)`", ev.get("note", ""))
            ident = m.group(1).split("::")[-1].split(".")[-1] if m else None
            where = "%s %s:%s%s" % (part["id"], old_path, old_line, (" " + ident) if ident else "")
            word = re.compile(r"(?<![\w])" + re.escape(ident) + r"(?![\w])") if ident else None
            if not base_sha:
                if not ident:
                    continue
                lines = cache.setdefault(old_path, _file_at_head(repo, old_path))
                hits = [i + 1 for i, ln in enumerate(lines or []) if word.search(ln)]
                if not hits:
                    out.append("STALE-ANCHOR " + where + (" (file gone)" if lines is None else ""))
                    continue
                if old_line not in hits:
                    defs = [i for i in hits if fn_def_name(old_path, lines[i - 1]) or re.search(r"\b(def|class|struct|enum|trait|interface|func|function)\b", lines[i - 1])]
                    new = min(defs or hits, key=lambda i: abs(i - old_line))
                    if "end_line" in ev:
                        ev["end_line"] = max(new, ev["end_line"] + (new - old_line))
                    ev["line"] = new
                continue
            new_path = old_path
            if old_path in renames:
                new_path = renames[old_path]
                if new_path is None:
                    out.append("STALE-ANCHOR " + where + " (file deleted)")
                    continue
            base_lines = _git_lines(repo, base_sha, old_path)
            head_lines = cache.setdefault(new_path, _git_lines(repo, "HEAD", new_path))
            if base_lines is None or head_lines is None:
                out.append("STALE-ANCHOR " + where + " (file not found at %s)" % ("base_sha" if base_lines is None else "HEAD"))
                continue
            new, how = map_line(base_lines, head_lines, old_line, old_path)
            if new is None:
                out.append("STALE-ANCHOR %s (%s)" % (where, how))
                continue
            if word is not None and not word.search(head_lines[new - 1]) and how.split(" ")[-1] != ident:
                out.append("STALE-ANCHOR %s (note names %s, line %d of %s holds %r)" % (where, ident, new, new_path, head_lines[new - 1].strip()[:60]))
                continue
            if "end_line" in ev:
                ev["end_line"] = min(len(head_lines), max(new, ev["end_line"] + (new - old_line)))
            ev["path"], ev["line"] = new_path, new
    if graph.get("view") == "seq":
        by_id = {n["id"]: n for n in graph["nodes"]}
        for e in graph["edges"]:
            e["evidence"] = copy.deepcopy(by_id[e["target"]]["evidence"])
        for f in graph["flows"]:
            f["evidence"] = copy.deepcopy(by_id[f["after"][0]]["evidence"])
    return out


def _sync(repo: str) -> None:
    p = subprocess.run([sys.executable, os.path.join(HERE, "vr_mermaid.py"), "sync", "docs/architecture"],
                       cwd=repo, capture_output=True, text=True)
    if p.returncode != 0:
        raise PromoteError("vr_mermaid.py sync failed: " + (p.stdout + p.stderr).strip()[:300])


def drop_removed(graph: dict, removed: set) -> dict:
    """The graph without the elements the owner marked `remove`. A sequence graph is renumbered and re-chained (its
    messages are consecutive); any other view loses the edges that touch a removed node."""
    g = copy.deepcopy(graph)
    if not removed:
        return g
    g["nodes"] = [n for n in g["nodes"] if n["id"] not in removed]
    if g.get("view") == "seq":
        kept = sorted(g["nodes"], key=lambda n: n.get("order", 0))
        for i, n in enumerate(kept, 1):
            n["order"] = i
        g["edges"] = [{"id": "f%d" % i, "source": kept[i - 1]["id"], "target": n["id"], "kind": "flow", "status": n.get("status", "added"),
                       "evidence": copy.deepcopy(n["evidence"])} for i, n in enumerate(kept[1:], 1)]
        used = {n["lane"] for n in kept} | {n["seq"]["to"] for n in kept if n.get("seq")}
        g["lanes"] = [x for x in g.get("lanes", []) if x["id"] in used]
        for f in g.get("flows", []):
            f["after"] = [n["id"] for n in kept]
            if kept:
                f["evidence"] = copy.deepcopy(kept[0]["evidence"])
    else:
        g["edges"] = [e for e in g["edges"] if e["source"] not in removed and e["target"] not in removed]
    return g


def _arch_draft(vr: str, stem: str) -> str:
    return os.path.join(vr, ARCH_DIR, stem + ".json")


def _run_draft(dossier_id: str, stem: str, repo: str, vr: str, l1: dict | None, review: dict | None,
               preview: bool, check_only: bool) -> int:
    """The reviewed draft arch/<stem>.json: approval on its exact hash, then the copy, re-anchor, validate and sync."""
    draft = _load(_arch_draft(vr, stem))
    if draft.get("view") not in ARCH_VIEWS:
        print("ERROR arch/%s.json has view %r, expected one of %s" % (stem, draft.get("view"), ", ".join(ARCH_VIEWS)))
        return 2
    label, fname = "arch:" + stem, "arch/%s.json" % stem
    ids = [n["id"] for n in draft.get("nodes", [])]
    removed, problems = approval(graph_hash(draft), review, ids, label, fname)
    if check_only:
        for pr in problems:
            print(pr)
        print("approval: " + ("every element of %s is approved on the current %s" % (stem, fname) if not problems else "%d element(s) not approved" % len(problems)))
        return 1 if problems else 0
    if not preview:
        for pr in problems:
            print(pr)
        if problems:
            return 1
    graph = drop_removed(draft, removed)
    if not graph["nodes"]:
        print("ERROR no element left in %s after the owner's removals" % fname)
        return 2
    graph["meta"] = dict(graph.get("meta") or {})
    graph["meta"].setdefault("mermaid", dict(MERMAID_DEFAULT[graph["view"]]))
    graph["meta"].setdefault("promoted_from", "visual-review arch draft %s" % stem)
    if not preview:
        base = graph["meta"].get("base_sha") or ((l1 or {}).get("meta") or {}).get("base_sha")
        out_lines = reanchor(graph, repo, base)
        for s in out_lines:
            print(s)
        if any(s.startswith("STALE-ANCHOR") for s in out_lines):
            return 1
    issues = vr_graph.validate(graph, root=repo)
    if issues:
        print("ERROR the promoted graph does not validate: " + json.dumps(issues)[:600])
        return 1
    if preview:
        print("draft ok: " + fname)
        return 0
    os.makedirs(os.path.join(repo, "docs", "architecture"), exist_ok=True)
    out = os.path.join(repo, "docs", "architecture", stem + ".json")
    with open(out, "w") as fh:
        json.dump(graph, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    print("wrote " + os.path.relpath(out, repo))
    md = os.path.join(repo, "docs", "architecture", stem + ".md")
    if not os.path.exists(md):
        with open(md, "w") as fh:
            fh.write(MD_STUB.format(stem=stem, line=STUB_LINE[graph["view"]] + graph["title"] + ".",
                                    head=graph["meta"]["mermaid"].get("header") or graph["meta"]["mermaid"]["type"]))
        print("wrote " + os.path.relpath(md, repo))
    try:
        _sync(repo)
    except PromoteError as exc:
        print("ERROR " + str(exc))
        return 1
    print("synced docs/architecture")
    return 0


SHADOW_DRIVER = "vr_promote_preview"


def _slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "-", str(text)).strip("-")[:60] or "flow"


def shadow_flow(l1: dict, flow: dict, removed: set) -> tuple:
    """(record, draft or None) for one L1 flow: the draft built and validated exactly as `--preview` does."""
    nodes = {n.get("id"): n for n in l1.get("nodes", [])}
    steps = [nodes[i] for i in flow.get("after", []) if i in nodes]
    stem = flow.get("new_stem") or "seq-shadow-" + _slug(flow.get("id", ""))
    rec = {"flow": flow.get("id"), "stem": stem, "steps": len(flow.get("after", [])),
           "changed_steps": sum(1 for n in steps if n.get("status") in ("added", "changed")),
           "missing_to": sum(1 for n in steps if n.get("to") is None),
           "missing_call_kind": sum(1 for n in steps if n.get("call_kind") is None),
           "built_ok": False, "errors": [], "lanes": 0}
    graph = None
    try:
        graph = promote_graph(dict(l1, flows=[dict(flow, new_stem=stem)]), stem, removed)
    except (PromoteError, KeyError, TypeError) as exc:
        msg = ("%s: %s" % (type(exc).__name__, exc))[:300] if not isinstance(exc, PromoteError) else str(exc)[:300]
        if isinstance(exc, PromoteError) and not flow.get("new_stem") and (rec["missing_to"] or rec["missing_call_kind"]):
            # the flow names no diagram, so its author wrote no routing: say which side to fix (STEP_ROUTING: all)
            msg = msg.replace("(a flow with new_stem needs both)", "(the author wrote no routing for a flow without new_stem: "
                              "the payload needs `STEP_ROUTING: all`)")
        rec["errors"].append(msg)
    if graph is not None:
        rec["lanes"] = len(graph.get("lanes", []))
        issues = vr_graph.validate(graph, root=None)
        if issues:
            rec["errors"].append("does not validate: " + json.dumps(issues)[:500])
        else:
            rec["built_ok"] = True
    return rec, graph


def shadow_all(dossier_id: str, repo: str, mode: str | None = None) -> int:
    """Driver `vr_promote_preview`, shadow only: build a seq draft for every L1 flow with an added or changed step and
    record the outcome in the git common dir. Reads l1.json / review.json, writes nothing under the dossier."""
    import datetime
    import driver_common
    mode = driver_common.read_mode(SHADOW_DRIVER, mode)
    if mode == "off":
        print("%s is off (the default): nothing to do. Set it to shadow to run this." % driver_common.env_name(SHADOW_DRIVER))
        return 3
    if mode == "on":
        print("ERROR no on mode yet: promotion stays tied to `Diagrams new:`")
        return 2
    vr = os.path.join(repo, ".discovery", "visual-review", dossier_id)
    l1p = os.path.join(vr, "l1.json")
    try:
        l1 = _load(l1p)
        if not isinstance(l1.get("flows", []), list) or not isinstance(l1.get("nodes", []), list):
            raise PromoteError("l1.json has no flows / nodes lists")
    except (OSError, ValueError, PromoteError, AttributeError) as exc:
        print("ERROR cannot use %s: %s" % (l1p, exc))
        return 2
    rpath = os.path.join(vr, "review.json")
    try:
        review = _load(rpath) if os.path.isfile(rpath) else None
    except (OSError, ValueError, PromoteError):
        review = None
    try:
        removed, _ = approval(graph_hash(l1), review, [])
    except (KeyError, TypeError, AttributeError):
        removed = set()
    nodes = {n.get("id"): n for n in l1.get("nodes", []) if isinstance(n, dict)}
    records, drafts = [], {}
    skipped = 0
    for flow in l1.get("flows", []):
        if not isinstance(flow, dict):
            continue
        if not any((nodes.get(i) or {}).get("status") in ("added", "changed") for i in flow.get("after", [])):
            skipped += 1
            continue
        rec, graph = shadow_flow(l1, flow, removed)
        records.append(rec)
        if graph is not None and rec["built_ok"]:
            drafts[rec["stem"]] = graph
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    base = os.path.join(driver_common.evidence_root(repo), "driver-shadow", dossier_id)
    ok = sum(1 for r in records if r["built_ok"])
    failed = [r for r in records if not r["built_ok"]]
    findings = ["flow %s: %s" % (r["flow"], "; ".join(r["errors"])[:300]) for r in failed]
    summary = "%d flows with an added or changed step: %d built ok, %d failed" % (len(records), ok, len(failed))
    evidence = {"driver": SHADOW_DRIVER, "mode": "shadow", "dossier": dossier_id, "verdict": "findings" if failed else "clean",
                "exit": 1 if failed else 0, "summary": summary, "stopped_at": None, "steps": [], "findings": findings,
                "facts": {"flows": records, "built_ok": ok, "failed": len(failed), "flows_without_change": skipped,
                          "drafts_dir": "%s-%s-drafts" % (SHADOW_DRIVER, stamp)},
                "time": datetime.datetime.now(datetime.timezone.utc).isoformat()}
    try:
        os.makedirs(base, exist_ok=True)
        if drafts:
            ddir = os.path.join(base, evidence["facts"]["drafts_dir"])
            os.makedirs(ddir, exist_ok=True)
            for stem, g in drafts.items():
                with open(os.path.join(ddir, stem + ".json"), "w") as fh:
                    json.dump(g, fh, indent=2, ensure_ascii=False)
                    fh.write("\n")
        path = os.path.join(base, "%s-%s.json" % (SHADOW_DRIVER, stamp))
        with open(path, "w") as fh:
            json.dump(evidence, fh, indent=1, sort_keys=True)
        print("%s: %s; evidence %s" % (SHADOW_DRIVER, summary, path))
    except OSError as exc:
        print("%s: %s; evidence not written: %s" % (SHADOW_DRIVER, summary, exc))
    return 0


def run(dossier_id: str, stem: str, repo: str, preview: bool = False, check_only: bool = False) -> int:
    if not STEM_RE.match(stem):
        print("ERROR stem %r is not a plain name (letters, digits, - and _)" % stem)
        return 2
    vr = os.path.join(repo, ".discovery", "visual-review", dossier_id)
    l1p = os.path.join(vr, "l1.json")
    l1 = _load(l1p) if os.path.isfile(l1p) else None
    rpath = os.path.join(vr, "review.json")
    review = _load(rpath) if os.path.isfile(rpath) else None
    flows = [f for f in (l1 or {}).get("flows", []) if f.get("new_stem") == stem]
    has_draft = os.path.isfile(_arch_draft(vr, stem))
    if len(flows) > 1 or (not flows and not has_draft):
        print("ERROR expected exactly one flow with new_stem %r in l1.json, found %d, and no draft arch/%s.json"
              % (stem, len(flows), stem))
        return 2
    # The draft is what the owner reviewed. A sequence preview (re)writes it from the L1 flow; anything else with a draft
    # (a c1/c4 stem, or a sequence stem already previewed) is read as it is.
    if not (preview and flows):
        if has_draft:
            return _run_draft(dossier_id, stem, repo, vr, l1, review, preview, check_only)
    removed: set = set()
    if check_only:
        _, problems = approval(graph_hash(l1), review, flows[0].get("after", []))
        for pr in problems:
            print(pr)
        print("approval: " + ("every step of %s is approved on the current l1.json" % stem if not problems else "%d step(s) not approved" % len(problems)))
        return 1 if problems else 0
    if not preview:
        removed, problems = approval(graph_hash(l1), review, flows[0].get("after", []))
        for pr in problems:
            print(pr)
        if problems:
            return 1
    else:
        removed, _ = approval(graph_hash(l1), review, [])
    try:
        graph = promote_graph(l1, stem, removed)
    except PromoteError as exc:
        print("ERROR " + str(exc))
        return 2
    if not preview:
        out_lines = reanchor(graph, repo, (l1.get("meta") or {}).get("base_sha"))
        for s in out_lines:
            print(s)
        if any(s.startswith("STALE-ANCHOR") for s in out_lines):
            return 1
    issues = vr_graph.validate(graph, root=None if preview else repo)
    if issues:
        print("ERROR the promoted graph does not validate: " + json.dumps(issues)[:600])
        return 1
    if preview:
        os.makedirs(os.path.join(vr, ARCH_DIR), exist_ok=True)
        out = _arch_draft(vr, stem)
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
            fh.write(MD_STUB.format(stem=stem, line="The flow: " + graph["title"] + ".", head="sequenceDiagram"))
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
    import contextlib
    import io
    fails: list = []

    def check(name, cond, detail=""):
        if not cond:
            fails.append("%s %s" % (name, detail))

    def git(d, *a):
        return subprocess.run(["git", "-C", d, "-c", "user.email=t@t", "-c", "user.name=t", *a],
                              check=True, capture_output=True, text=True).stdout.strip()

    # the hash is the dashboard's: shared/test/vrhash.test.ts asserts the same constant
    hc = vr_graph.load(os.path.join(vr_graph.FIXTURES, "hash-case.json"))
    check("hash:cross-language", graph_hash(hc) == HASH_CASE_EXPECTED, graph_hash(hc))

    base = vr_graph.load(os.path.join(vr_graph.FIXTURES, "w145-l1.json"))
    BASE_A = ["// header", "fn alpha() { todo!() }", "fn beta() {", "    todo!()", "}", "fn gamma() { todo!() }"]
    HEAD_A = ["// one", "// two", "// three", "// header", "fn alpha() { 1 }", "fn beta() {", "    let x = 1;", "    x", "}",
              "fn gamma() { todo!() }"]

    def make_l1(base_sha, anchors, with_base=True):
        """anchors: {step index: (path, line, note)}; the others sit on gamma (line 6 at base)."""
        g = copy.deepcopy(base)
        kinds = ["call", "reply", "async", "publish"] * 3
        lanes = [x["id"] for x in g["lanes"]]
        for i, n in enumerate(g["nodes"]):
            n["to"] = lanes[(lanes.index(n["lane"]) + 1) % len(lanes)]
            n["call_kind"] = kinds[i]
            n.pop("detail", None)
            path, line, note = anchors.get(i, ("src/a.rs", 6, "`gamma`"))
            n["evidence"] = [{"tier": "source", "path": path, "line": line, "note": note}]
        g["flows"][0]["new_stem"] = "seq-new"
        g["flows"][0].pop("before", None)
        if with_base:
            g["meta"]["base_sha"] = base_sha
        return g

    def scenario(anchors, head_files=None, rename=None, delete=False, with_base=True):
        """A repo: base commit with the stubs, then a HEAD commit with the bodies written. Returns (dir, l1)."""
        d = tempfile.mkdtemp()
        git(d, "init", "-q", "-b", "main")
        os.makedirs(os.path.join(d, "src"))
        open(os.path.join(d, "src", "a.rs"), "w").write("\n".join(BASE_A) + "\n")
        git(d, "add", "-A")
        git(d, "commit", "-qm", "stubs")
        sha = git(d, "rev-parse", "HEAD")
        if rename:
            git(d, "mv", "src/a.rs", rename)
        if delete:
            git(d, "rm", "-q", "src/a.rs")
        target = os.path.join(d, rename or "src/a.rs")
        if not delete:
            open(target, "w").write("\n".join(head_files or HEAD_A) + "\n")
        git(d, "add", "-A")
        git(d, "commit", "-qm", "bodies")
        l1 = make_l1(sha, anchors, with_base)
        vr = os.path.join(d, ".discovery", "visual-review", "W-9")
        os.makedirs(vr)
        json.dump(l1, open(os.path.join(vr, "l1.json"), "w"))
        return d, l1

    def review(d, l1, spec, hash_=None):
        """spec: [(step, verdict, ts[, hash])]; default hash = the file's current one."""
        h = graph_hash(l1)
        lines = [{"anchor": {"diagram": "L1", "element": e, "graph_hash": (rest[0] if rest else h)}, "verdict": v,
                  "comment": "", "ts": ts} for e, v, ts, *rest in spec]
        json.dump({"schema_version": "1.0.0", "dossier": "W-9", "updated": "2026-01-01T00:00:00Z", "lines": lines},
                  open(os.path.join(d, ".discovery", "visual-review", "W-9", "review.json"), "w"))

    def all_ok(l1, **kw):
        return [(n["id"], "ok", "2026-01-02T00:00:00Z") for n in l1["nodes"]]

    def call(d, preview=False):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = run("W-9", "seq-new", d, preview)
        return rc, buf.getvalue()

    def anchor_of(d, sid):
        g = json.load(open(os.path.join(d, "docs", "architecture", "seq-new.json")))
        return [n for n in g["nodes"] if n["id"] == sid][0]["evidence"][0]

    # (a) unchanged region + (b) changed hunk, approval on the current hash, md and sync
    d, l1 = scenario({1: ("src/a.rs", 4, "`beta`"), 2: ("src/a.rs", 4, "")})
    review(d, l1, all_ok(l1))
    rc, out = call(d)
    check("map:rc", rc == 0, out)
    if rc == 0:
        check("map:unchanged-region", anchor_of(d, "s5")["line"] == 10, anchor_of(d, "s5"))
        check("map:changed-hunk-enclosing-fn", anchor_of(d, "s2")["line"] == 6 and anchor_of(d, "s3")["line"] == 6,
              (anchor_of(d, "s2"), anchor_of(d, "s3")))
        check("map:md", os.path.isfile(os.path.join(d, "docs", "architecture", "seq-new.md")))
        p = subprocess.run([sys.executable, os.path.join(HERE, "vr_mermaid.py"), "check", "docs/architecture"], cwd=d,
                           capture_output=True, text=True)
        check("map:mermaid-check", p.returncode == 0, p.stdout + p.stderr)
        g = json.load(open(os.path.join(d, "docs", "architecture", "seq-new.json")))
        check("map:arrows", [n["seq"]["arrow"] for n in g["nodes"][:4]] == ["->>", "-->>", "-)", "-)"])
    # rename
    d, l1 = scenario({}, rename="src/b.rs", head_files=BASE_A + ["// tail"])  # -M needs the file to stay similar
    review(d, l1, all_ok(l1))
    rc, out = call(d)
    check("rename:follows", rc == 0 and anchor_of(d, "s1")["path"] == "src/b.rs" and anchor_of(d, "s1")["line"] == 6, out)
    # delete
    d, l1 = scenario({}, delete=True)
    review(d, l1, all_ok(l1))
    rc, out = call(d)
    check("delete:stale", rc == 1 and "STALE-ANCHOR s1 src/a.rs:6 gamma (file deleted)" in out, out)
    # backtick mismatch
    d, l1 = scenario({0: ("src/a.rs", 6, "`beta`")})
    review(d, l1, all_ok(l1))
    rc, out = call(d)
    check("mismatch:stale", rc == 1 and "STALE-ANCHOR s1" in out and "note names beta" in out, out)
    # an anchor in a changed hunk of a file with no known language scanner has nothing to map to
    d, l1 = scenario({0: ("src/a.rs", 2, "")})  # `fn alpha() { todo!() }` changed, enclosing fn alpha still there: ok
    review(d, l1, all_ok(l1))
    rc, out = call(d)
    check("changed-sigline:maps-to-def", rc == 0 and anchor_of(d, "s1")["line"] == 5, out)
    # no base_sha: warn, fall back to the name lookup
    d, l1 = scenario({0: ("src/a.rs", 6, "`gamma`")}, with_base=False)
    review(d, l1, all_ok(l1))
    rc, out = call(d)
    check("no-base:warn", rc == 0 and "WARN" in out and anchor_of(d, "s1")["line"] == 10, out)
    # approval: current hash, stale, change on the same hash, ok after change on the same hash, no line, remove
    d, l1 = scenario({})
    review(d, l1, all_ok(l1), None)
    check("approval:current", call(d)[0] == 0)
    d, l1 = scenario({})
    review(d, l1, [(n["id"], "ok", "2026-01-02T00:00:00Z", "0" * 64) for n in l1["nodes"]])
    rc, out = call(d)
    check("approval:stale", rc == 1 and "STALE-APPROVAL s1" in out, out)
    d, l1 = scenario({})
    spec = all_ok(l1)
    spec[2] = ("s3", "change", "2026-01-02T00:00:00Z")
    review(d, l1, spec)
    rc, out = call(d)
    check("change:same-hash", rc == 1 and "UNRESOLVED-CHANGE s3" in out, out)
    d, l1 = scenario({})
    spec = all_ok(l1) + [("s3", "ok", "2026-01-03T00:00:00Z")]
    spec[2] = ("s3", "change", "2026-01-01T00:00:00Z")
    review(d, l1, spec)
    rc, out = call(d)
    check("ok-after-change:still-open", rc == 1 and "UNRESOLVED-CHANGE s3" in out, out)
    d, l1 = scenario({})
    spec = all_ok(l1)[:-1]
    review(d, l1, spec)
    rc, out = call(d)
    check("approval:missing-line", rc == 1 and "NOT-APPROVED s10" in out, out)
    d, l1 = scenario({})
    spec = all_ok(l1)
    spec[2] = ("s3", "remove", "2026-01-02T00:00:00Z", "0" * 64)
    review(d, l1, spec)
    rc, out = call(d)
    check("remove:drops-the-step", rc == 0 and "s3" not in [n["id"] for n in json.load(open(os.path.join(d, "docs", "architecture", "seq-new.json")))["nodes"]], out)
    # --check-approval: rule only, nothing written
    d, l1 = scenario({})
    review(d, l1, all_ok(l1)[:-1])
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = run("W-9", "seq-new", d, check_only=True)
    check("check-approval:names-step", rc == 1 and "NOT-APPROVED s10" in buf.getvalue() and not os.path.exists(os.path.join(d, "docs")), buf.getvalue())
    review(d, l1, all_ok(l1))
    with contextlib.redirect_stdout(io.StringIO()):
        rc = run("W-9", "seq-new", d, check_only=True)
    check("check-approval:ok", rc == 0 and not os.path.exists(os.path.join(d, "docs")))
    # preview needs neither approval nor anchors
    d, l1 = scenario({})
    rc, out = call(d, preview=True)
    check("preview", rc == 0 and os.path.isfile(os.path.join(d, ".discovery", "visual-review", "W-9", "arch", "seq-new.json"))
          and not os.path.exists(os.path.join(d, "docs")), out)

    # ---- Architecture impact: the reviewed draft arch/<stem>.json (anchor arch:<stem>) ----
    def arch_review(d, stem, draft, spec):
        """spec: [(element, verdict[, hash])]; default hash = the draft's current one."""
        h = graph_hash(draft)
        lines = [{"anchor": {"diagram": "arch:" + stem, "element": e, "graph_hash": (rest[0] if rest else h)}, "verdict": v,
                  "comment": "", "ts": "2026-01-02T00:00:00Z"} for e, v, *rest in spec]
        json.dump({"schema_version": "1.0.0", "dossier": "W-9", "updated": "2026-01-01T00:00:00Z", "lines": lines},
                  open(os.path.join(d, ".discovery", "visual-review", "W-9", "review.json"), "w"))

    def draft_of(d, stem):
        return json.load(open(os.path.join(d, ".discovery", "visual-review", "W-9", "arch", stem + ".json")))

    def call_stem(d, stem, preview=False, check=False):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = run("W-9", stem, d, preview, check)
        return rc, buf.getvalue()

    # sequence stem: preview writes the draft; approval and promotion read the draft, not l1.json
    d, l1 = scenario({1: ("src/a.rs", 4, "`beta`")})
    check("arch-seq:preview", call_stem(d, "seq-new", preview=True)[0] == 0)
    dr = draft_of(d, "seq-new")
    check("arch-seq:draft-from-l1", dr["view"] == "seq" and [n["id"] for n in dr["nodes"]] == [n["id"] for n in l1["nodes"]])
    review(d, l1, all_ok(l1))  # L1 approvals alone no longer approve a stem that has a draft
    rc, out = call_stem(d, "seq-new", check=True)
    check("arch-seq:l1-lines-not-enough", rc == 1 and "NOT-APPROVED s1" in out, out)
    arch_review(d, "seq-new", dr, [(n["id"], "ok") for n in dr["nodes"]])
    check("arch-seq:check-ok", call_stem(d, "seq-new", check=True)[0] == 0 and not os.path.exists(os.path.join(d, "docs")))
    rc, out = call_stem(d, "seq-new")
    g = json.load(open(os.path.join(d, "docs", "architecture", "seq-new.json"))) if rc == 0 else {}
    check("arch-seq:promote", rc == 0 and g.get("view") == "seq" and g["nodes"][1]["evidence"][0]["line"] == 6
          and os.path.isfile(os.path.join(d, "docs", "architecture", "seq-new.md")), out)
    # a verdict on an older draft hash is stale; a change blocks; remove drops the message and re-chains
    arch_review(d, "seq-new", dr, [(n["id"], "ok", "0" * 64) for n in dr["nodes"]])
    rc, out = call_stem(d, "seq-new", check=True)
    check("arch-seq:stale", rc == 1 and "STALE-APPROVAL s1" in out and "arch/seq-new.json" in out, out)
    arch_review(d, "seq-new", dr, [(n["id"], "ok") for n in dr["nodes"]] + [("s2", "change")])
    rc, out = call_stem(d, "seq-new", check=True)
    check("arch-seq:change-blocks", rc == 1 and "UNRESOLVED-CHANGE s2" in out and "arch:seq-new" in out, out)
    arch_review(d, "seq-new", dr, [(n["id"], "ok") for n in dr["nodes"] if n["id"] != "s2"] + [("s2", "remove")])
    rc, out = call_stem(d, "seq-new")
    g = json.load(open(os.path.join(d, "docs", "architecture", "seq-new.json"))) if rc == 0 else {"nodes": []}
    check("arch-seq:remove-drops", rc == 0 and "s2" not in [n["id"] for n in g["nodes"]] and len(g["nodes"]) == len(dr["nodes"]) - 1
          and [e["source"] for e in g["edges"]][:1] == ["s1"] and [n["order"] for n in g["nodes"]] == list(range(1, len(g["nodes"]) + 1)), out)

    # c1 / c4 stems: the author's draft, no l1 flow; evidence re-anchored from base_sha, md stub, sync
    def put_draft(d, stem, graph):
        os.makedirs(os.path.join(d, ".discovery", "visual-review", "W-9", "arch"), exist_ok=True)
        json.dump(graph, open(os.path.join(d, ".discovery", "visual-review", "W-9", "arch", stem + ".json"), "w"))
        return graph

    for view, stem in (("c1", "c1-newsys"), ("c4", "c4-newtypes")):
        d, l1 = scenario({})
        sha = l1["meta"]["base_sha"]
        e1 = [{"tier": "source", "path": "src/a.rs", "line": 6, "note": "`gamma`"}]
        inf = [{"tier": "inferred", "note": "named by the dossier"}]
        if view == "c1":
            gr = {"schema_version": "1.0.0", "view": "c1", "title": "The new system", "meta": {"repo": "r", "commit": sha, "base_sha": sha, "generated_by": "agent"},
                  "nodes": [{"id": "element:app", "label": "App", "kind": "element", "status": "added", "evidence": e1},
                            {"id": "element:ext", "label": "External API", "kind": "element", "status": "added", "evidence": inf}],
                  "edges": [{"id": "edge:depends:element:app:element:ext", "source": "element:app", "target": "element:ext",
                             "kind": "depends", "arrow": "-->", "label": "calls", "status": "added", "evidence": e1}], "groups": []}
        else:
            gr = {"schema_version": "1.0.0", "view": "c4", "title": "The new types", "meta": {"repo": "r", "commit": sha, "base_sha": sha, "generated_by": "agent"},
                  "nodes": [{"id": "class:Gamma", "label": "Gamma", "kind": "class", "status": "added", "members": ["run()"], "evidence": e1},
                            {"id": "class:Delta", "label": "Delta", "kind": "class", "status": "added", "evidence": inf}],
                  "edges": [{"id": "edge:association:class:Gamma:class:Delta", "source": "class:Gamma", "target": "class:Delta",
                             "kind": "association", "arrow": "-->", "label": "holds", "status": "added", "evidence": e1}], "groups": []}
        put_draft(d, stem, gr)
        rc, out = call_stem(d, stem, preview=True)
        check(view + ":preview", rc == 0 and not os.path.exists(os.path.join(d, "docs")), out)
        rc, out = call_stem(d, stem, check=True)
        check(view + ":not-approved", rc == 1 and "NOT-APPROVED " + gr["nodes"][0]["id"] in out, out)
        arch_review(d, stem, gr, [(n["id"], "ok") for n in gr["nodes"]])
        check(view + ":check-ok", call_stem(d, stem, check=True)[0] == 0)
        rc, out = call_stem(d, stem)
        gp = os.path.join(d, "docs", "architecture", stem + ".json")
        if rc == 0:
            g = json.load(open(gp))
            check(view + ":reanchored", g["nodes"][0]["evidence"][0]["line"] == 10 and g["edges"][0]["evidence"][0]["line"] == 10, g["nodes"][0]["evidence"])
            p = subprocess.run([sys.executable, os.path.join(HERE, "vr_mermaid.py"), "check", "docs/architecture"], cwd=d, capture_output=True, text=True)
            md = open(os.path.join(d, "docs", "architecture", stem + ".md")).read()
            check(view + ":md-synced", p.returncode == 0 and ("flowchart LR" if view == "c1" else "classDiagram") in md and ("External API" if view == "c1" else "Delta") in md, md + p.stdout)
        else:
            check(view + ":promote", False, out)
        # removing a node drops it and the edges that touch it
        d2, l2_ = scenario({})
        gr = copy.deepcopy(gr)
        gr["meta"].update(commit=l2_["meta"]["base_sha"], base_sha=l2_["meta"]["base_sha"])
        put_draft(d2, stem, gr)
        arch_review(d2, stem, gr, [(gr["nodes"][0]["id"], "ok"), (gr["nodes"][1]["id"], "remove")])
        rc, out = call_stem(d2, stem)
        g = json.load(open(os.path.join(d2, "docs", "architecture", stem + ".json"))) if rc == 0 else {"nodes": [0], "edges": [0]}
        check(view + ":remove", rc == 0 and len(g["nodes"]) == 1 and g["edges"] == [], out)
        # an anchor the code no longer has is stale
        d3, l3_ = scenario({}, delete=True)
        gr = copy.deepcopy(gr)
        gr["meta"].update(commit=l3_["meta"]["base_sha"], base_sha=l3_["meta"]["base_sha"])
        put_draft(d3, stem, gr)
        arch_review(d3, stem, gr, [(n["id"], "ok") for n in gr["nodes"]])
        rc, out = call_stem(d3, stem)
        check(view + ":stale-anchor", rc == 1 and "STALE-ANCHOR" in out, out)
    # a stem that is not a plain name, and a stem with neither flow nor draft
    check("stem:traversal", call_stem(d, "../x")[0] == 2 and call_stem(d, "c1-none")[0] == 2)
    with contextlib.redirect_stdout(io.StringIO()):
        check("noflow:exit2", run("W-9", "seq-other", d) == 2)
    bad = copy.deepcopy(l1)
    del bad["nodes"][0]["call_kind"]
    check("missing-kind", _raises(lambda: promote_graph(bad, "seq-new", set())))
    check("arrow:reply", promote_graph(l1, "seq-new", set())["nodes"][1]["seq"]["arrow"] == "-->>")

    # ---- --shadow-all (driver vr_promote_preview): evidence in the git common dir, nothing under the dossier ----
    import glob
    ds, l1s = scenario({})
    vrs = os.path.join(ds, ".discovery", "visual-review", "W-9")
    l1s["flows"].append({"id": "f-extra", "label": "extra", "status": "added", "after": [n["id"] for n in l1s["nodes"][:2]],
                         "evidence": copy.deepcopy(l1s["flows"][0]["evidence"])})
    bad_node = copy.deepcopy(l1s["nodes"][0])
    bad_node.update(id="sx1", status="added")
    del bad_node["call_kind"]
    del bad_node["to"]
    ctx_node = copy.deepcopy(l1s["nodes"][1])
    ctx_node.update(id="sx2", status="context")
    l1s["nodes"] += [bad_node, ctx_node]
    l1s["flows"].append({"id": "f-bad", "label": "bad", "status": "added", "after": ["sx1"], "evidence": copy.deepcopy(l1s["flows"][0]["evidence"])})
    l1s["flows"].append({"id": "f-ctx", "label": "unchanged", "status": "context", "after": ["sx2"], "evidence": copy.deepcopy(l1s["flows"][0]["evidence"])})
    json.dump(l1s, open(os.path.join(vrs, "l1.json"), "w"))
    json.dump({"schema_version": "1.0.0", "dossier": "W-9", "updated": "2026-01-01T00:00:00Z", "lines": []}, open(os.path.join(vrs, "review.json"), "w"))

    def tree(path):
        return sorted((os.path.relpath(os.path.join(r, f), path), open(os.path.join(r, f), "rb").read())
                      for r, _, fs in os.walk(path) for f in fs)

    before = tree(vrs)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc_off = shadow_all("W-9", ds, "off")
        rc_on = shadow_all("W-9", ds, "on")
        rc_sh = shadow_all("W-9", ds, "shadow")
        rc_none = shadow_all("W-404", ds, "shadow")
    check("shadow-all:off-exit-3", rc_off == 3)
    check("shadow-all:on-refused", rc_on == 2 and "no on mode yet" in buf.getvalue())
    check("shadow-all:exit-0", rc_sh == 0, buf.getvalue())
    check("shadow-all:no-l1-exit-2", rc_none == 2)
    check("shadow-all:dossier-untouched", tree(vrs) == before and not os.path.exists(os.path.join(vrs, "arch")))
    evf = sorted(glob.glob(os.path.join(ds, ".git", "asd-drivers", "driver-shadow", "W-9", "vr_promote_preview-*.json")))
    ev = json.load(open(evf[-1])) if evf else {}
    recs = {r["flow"]: r for r in ev.get("facts", {}).get("flows", [])}
    check("shadow-all:evidence-flows", set(recs) == {l1s["flows"][0]["id"], "f-extra", "f-bad"}, sorted(recs))
    check("shadow-all:context-flow-skipped", "f-ctx" not in recs and ev.get("facts", {}).get("flows_without_change") == 1)
    check("shadow-all:new_stem-flow-built", recs.get(l1s["flows"][0]["id"], {}).get("built_ok") is True and recs[l1s["flows"][0]["id"]]["stem"] == "seq-new")
    check("shadow-all:no-new_stem-gets-shadow-stem", recs.get("f-extra", {}).get("stem") == "seq-shadow-f-extra" and recs["f-extra"]["built_ok"] is True
          and recs["f-extra"]["lanes"] >= 1)
    check("shadow-all:missing-fields-counted", recs.get("f-bad", {}).get("built_ok") is False and recs["f-bad"]["missing_to"] == 1
          and recs["f-bad"]["missing_call_kind"] == 1 and recs["f-bad"]["errors"])
    check("shadow-all:built-count", ev.get("facts", {}).get("built_ok") == 2 and ev["facts"]["failed"] == 1)
    check("shadow-all:findings", len(ev.get("findings", [])) == 1 and ev["findings"][0].startswith("flow f-bad") and ev.get("exit") == 1)
    # a flow with no new_stem whose steps all carry to/call_kind (STEP_ROUTING: all) builds and validates
    ns_flow = {"id": "f-routed", "label": "routed", "status": "changed", "after": [n["id"] for n in l1s["nodes"][:2]],
               "evidence": copy.deepcopy(l1s["flows"][0]["evidence"])}
    check("shadow-flow:no-new_stem-with-routing-builds", "new_stem" not in ns_flow
          and all(l1s["nodes"][i].get("to") and l1s["nodes"][i].get("call_kind") for i in (0, 1))
          and shadow_flow(l1s, ns_flow, set())[0]["built_ok"] is True)
    ns_bad = dict(ns_flow, after=["sx1"])
    check("shadow-flow:no-new_stem-without-routing-fails", shadow_flow(l1s, ns_bad, set())[0]["built_ok"] is False)
    # the shape of the config-harness shadow evidence (W-118/W-119/...): a flow without new_stem, every step added or
    # changed, none with to/call_kind. It fails with the hint naming the producer side; the same flow with the routing
    # the author writes under STEP_ROUTING: all (to = the next step's lane, the last step a self-message) builds.
    ev_l1 = copy.deepcopy(l1s)
    ev_ids = [n["id"] for n in ev_l1["nodes"][:3]]
    for n in ev_l1["nodes"]:
        n.pop("to", None)
        n.pop("call_kind", None)
        n["status"] = "added"
    ev_flow = {"id": "flow:save-node-config", "label": "save node config", "status": "added", "after": ev_ids,
               "evidence": copy.deepcopy(l1s["flows"][0]["evidence"])}
    rec_bad = shadow_flow(ev_l1, ev_flow, set())[0]
    check("shadow-flow:evidence-shape-fails-with-hint", rec_bad["built_ok"] is False and rec_bad["missing_to"] == 3
          and "STEP_ROUTING: all" in rec_bad["errors"][0], rec_bad)
    by_id = {n["id"]: n for n in ev_l1["nodes"]}
    for i, sid in enumerate(ev_ids):
        by_id[sid]["to"] = by_id[ev_ids[min(i + 1, len(ev_ids) - 1)]]["lane"]
        by_id[sid]["call_kind"] = "call"
    rec_ok, draft_ok = shadow_flow(ev_l1, ev_flow, set())
    check("shadow-flow:evidence-shape-with-routing-builds", rec_ok["built_ok"] is True and draft_ok is not None
          and rec_ok["missing_to"] == 0, rec_ok)
    drafts = glob.glob(os.path.join(ds, ".git", "asd-drivers", "driver-shadow", "W-9", "*-drafts", "*.json"))
    check("shadow-all:drafts-in-common-dir", sorted(os.path.basename(x) for x in drafts) == ["seq-new.json", "seq-shadow-f-extra.json"], drafts)
    check("shadow-all:no-driver-evidence-leak", not os.path.exists(os.path.join(ds, ".git", "asd-drivers", "driver-evidence")))
    rc_cli = subprocess.run([sys.executable, os.path.abspath(__file__), "--shadow-all", "--dossier-id", "W-9", "--repo", ds],
                            capture_output=True, text=True, env=dict(os.environ, ASD_DRIVER_VR_PROMOTE_PREVIEW="")).returncode
    check("shadow-all:cli-off-exit-3", rc_cli == 3)
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
    ap.add_argument("--shadow-all", action="store_true",
                    help="driver vr_promote_preview: build a seq draft for every L1 flow with an added or changed step; "
                         "evidence goes to the git common dir, nothing is written under the dossier")
    ap.add_argument("--check-approval", action="store_true",
                    help="only the approval rule (no writes, no re-anchoring): exit 1 and name the unapproved steps")
    a = ap.parse_args(argv)
    if a.selftest:
        return _selftest()
    if a.shadow_all:
        if not a.dossier_id:
            ap.error("--shadow-all needs --dossier-id")
        return shadow_all(a.dossier_id, os.path.abspath(a.repo))
    if not (a.dossier_id and a.stem):
        ap.error("--dossier-id and --stem are required")
    return run(a.dossier_id, a.stem, os.path.abspath(a.repo), a.preview, a.check_approval)


if __name__ == "__main__":
    sys.exit(main())
