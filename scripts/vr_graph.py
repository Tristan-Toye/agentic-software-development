#!/usr/bin/env python3
"""Visual review: validate a change graph and count it against the view budgets.

The schema is `references/visual-review/graph.schema.json` (version 1.1.0).
This file reads that schema with a small JSON-Schema subset interpreter (no
third-party package), then adds the checks a schema cannot say: dangling edge
ends, duplicate ids, evidence that points at nothing, and the view budgets.

    vr_graph.py validate GRAPH.json [--root REPO] [--no-budget]
    vr_graph.py budget GRAPH.json
    vr_graph.py parts DIR [--decision yes|no] [--new STEMS] [--affected STEMS]
    vr_graph.py --selftest

`validate` prints {"ok": bool, "issues": [{"code","path","message"}]} and exits
1 when any issue is an error. Without `--root`, the evidence format is checked
but no file is opened; with `--root REPO` every cited `path:line` must exist.
Importable: `load(path)`, `validate(graph, root=None, schema=None)`,
`budget(graph)`. Exit codes: 0 valid; 1 invalid; 2 unusable input.

Issue codes: schema_error, unsupported_version, bad_id, duplicate_node_id,
duplicate_edge_id, duplicate_group_id, unknown_group_id, unknown_lane_id,
unknown_flow_step, unknown_edge_source, unknown_edge_target,
missing_evidence_path, bad_evidence, missing_repository_path,
line_range_outside_file, over_budget, label_too_long, unknown_frame_step.
View `decision` adds: no_start, multiple_start, no_end, end_has_exit, dead_end, unreachable_node, decision_exits,
unlabeled_branch, duplicate_branch_label, fork_without_decision, unbounded_loop, decision_label, decision_evidence, bad_shape,
unknown_step (with --l1).
`parts` (the stage-3 mandatory parts of a visual review directory) adds: parts_missing, parts_conflict, parts_reason, parts_plan,
parts_unknown_stem, parts_unknown_key, parts_version, parts_unreadable.
unknown_frame_step: a frame (view seq) whose first, last or a branch's first is not a node id.

label_too_long: a node label over 80 characters, or one that would be clipped on
the canvas (it needs more than 3 lines, 2 with a stereotype, in the widest node
box; a word wider than a line is broken after / : . _ - or at the line width,
not rejected: the shared text-fit rule, `text_fit` below, ported from shared/questions/vrtext.ts), an edge or lane label over
40, a group or flow label over 60, a title over 100. The limits live in
graph.schema.json as maxLength; this reader maps that error to its own code.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REF_DIR = os.path.normpath(os.path.join(HERE, "..", "references", "visual-review"))
SCHEMA_PATH = os.path.join(REF_DIR, "graph.schema.json")
FIXTURES = os.path.join(REF_DIR, "fixtures")
SUPPORTED_MAJOR = 1

# Budgets per view (D0 REPORT section 7, "Element budgets": 15 / 12 / 12; they
# hold for W-145: 7 features, 10 steps, 12 nodes). Groups are NOT counted: the
# D0 count was 12 nodes, 17 with the 5 crate groups, and the owner-facing limit
# is the 12 nodes a reviewer reads. L0 counts nodes of kind "feature", L1 nodes
# of kind "step", L2 every node. The "full" view (an extractor graph) has none.
# Node label limit, mirrored by maxLength in graph.schema.json (selftest checks they agree).
NODE_LABEL_MAX = 80
BUDGETS = {"L0": ("feature", 15), "L1": ("step", 12), "L2": (None, 12)}
# View "map" (the whole-repo product map): the first screen is the map with every top-level group folded, so what is counted is
# the top-level units (groups, plus nodes outside any group): at most MAP_AREAS. Opening a group shows its members: at most MAP_GROUP
# of them (the L0 limit), counted per group. Groups are counted here, unlike in L0..L2, because the folded view is the budgeted one.
# View "decision" (a flow with conditions): at most DECISION_NODES nodes (terminals and junctions count) and DECISION_MAX decisions;
# a decision has 2 or 3 exits; the label of a decision fits its diamond. `paths` is reported, not budgeted.
DECISION_NODES = 14
DECISION_MAX = 5
DECISION_LABEL_MAX = 40
PATH_CAP = 99
# Canonical shape of each accepted spelling (the Mermaid-compatible aliases map to the same shape).
DECISION_SHAPES = {"terminal": "terminal", "stadium": "terminal", "process": "process", "rect": "process",
                   "decision": "decision", "diamond": "decision", "io": "io", "junction": "junction", "circle": "junction"}
MAP_AREAS = 7
MAP_GROUP = 15
# View "seq" (a sequence diagram): drawn flat up to SEQ["messages"] messages on SEQ["lanes"] lanes. A bigger one is never cropped and never
# an error: the canvas folds it into `ref` groups of consecutive messages (at most SEQ["group"] messages each, SEQ["levels"] levels,
# the top level folded on open, Expand all opens everything). The author supplies the groups (`groups` + `group` on each message,
# `parent` for the second level); without them the canvas groups mechanically. Labels: a lane label at most SEQ["lane_label"], a
# message label at most SEQ["msg_label"] characters (the schema limit of 80 stays the hard one). Same numbers as SEQ_BUDGET in
# shared/questions/vrlayout.ts (selftest checks they agree). `over` here means "folds", and a group over SEQ["group"] messages is an issue.
SEQ = {"lanes": 6, "messages": 10, "group": 6, "lane_label": 24, "msg_label": 32, "levels": 2}


# ---------------------------------------------------------------- loading

# ------------------------------------------------------- text fit (port of shared/questions/vrtext.ts)

# The canvas's one text-fit rule: the same numbers and algorithm as VR_TEXT, vrNodeWidth and vrFitLabel in vrtext.ts.
# references/visual-review/text-fit.fixtures.json pins the two together (selftest here, shared/test/vrtext.test.ts there).
TEXT = {"charPx": 6.6, "pad": 28, "inset": 24, "minW": 130, "maxW": 250, "foldedExtra": 36, "maxLines": 3, "seqMaxLines": 5,
        "shapeRoom": {"diamond": (0.6, 0), "circle": (0.7, 0), "terminal": (1, 36), "io": (1, 44)}, "ioSkew": 12,
        "diamondW": (160, 300), "circleD": (72, 150), "terminalExtra": 24}
TEXT_FIXTURES = os.path.join(REF_DIR, "text-fit.fixtures.json")


def _js_round(x: float) -> int:
    return int(x + 0.5) // 1 if x >= 0 else -int(-x + 0.5)


def node_width(label: str, folded: bool = False) -> int:
    extra = TEXT["foldedExtra"] if folded else 0
    return max(TEXT["minW"], min(TEXT["maxW"] + extra, _js_round(len(label) * TEXT["charPx"] + TEXT["pad"] + extra)))


# The shape a node is drawn in, for the text room (nodeShape in vrlayout.ts, only the names that change the room).
SHAPE_NAME_ROOM = {"diamond": "diamond", "decision": "diamond", "rhombus": "diamond", "circle": "circle", "stadium": "terminal",
                   "terminal": "terminal", "oval": "terminal", "io": "io", "parallelogram": "io", "junction": "junction", "dot": "junction"}
SHAPE_KIND_ROOM = {"decision": "diamond", "terminal": "terminal", "start": "terminal", "end": "terminal", "io": "io", "junction": "junction"}


def shape_of(node: dict, view: str | None = None) -> str | None:
    """The text-room shape of a node (diamond, circle, terminal, io, junction) or None for a box: its `shape`, else its `kind`."""
    if node.get("collapsed") is True:
        return None
    sh = node.get("shape")
    if isinstance(sh, str):
        got = SHAPE_NAME_ROOM.get(sh.lower())
        if got == "circle" and view == "decision":
            return "junction"
        if got or sh.lower() in ("rect", "process", "round", "rounded", "cylinder", "database", "subroutine", "asymmetric", "bare", "implicit"):
            return got
    return SHAPE_KIND_ROOM.get(str(node.get("kind", "")).lower())


L1_IO_VERB = re.compile(r"^(read|write|send|publish|fetch|receive|emit|store|save|load|query|request|return)(s|es)?\b", re.I)
L1_SHAPES_FIXTURES = os.path.join(REF_DIR, "l1-shapes.fixtures.json")


def l1_default_shapes(graph: dict) -> dict:
    """{step id: "terminal" | "io"} for the steps of an L1 graph that name no shape (l1DefaultShapes in vrlayout.ts, pinned by
    references/visual-review/l1-shapes.fixtures.json): the first and last step of a flow of 3 or more, a step that calls another
    lane or starts with a data verb is io. A node with a `shape`, a kind other than step or `collapsed` keeps what it has."""
    out: dict = {}
    if not isinstance(graph, dict) or graph.get("view") != "L1" or not isinstance(graph.get("nodes"), list):
        return out

    def fixed(n) -> bool:
        return not isinstance(n, dict) or n.get("shape") is not None or n.get("collapsed") is True or str(n.get("kind") or "").lower() not in ("", "step")

    by_id = {n.get("id"): n for n in graph["nodes"] if isinstance(n, dict)}
    for n in by_id.values():
        if fixed(n):
            continue
        if (n.get("call_kind") is not None and n.get("to") is not None and n.get("to") != n.get("lane")) or L1_IO_VERB.match(str(n.get("label") or "").strip()):
            out[n["id"]] = "io"
    for f in graph.get("flows") or []:
        if not isinstance(f, dict):
            continue
        ids = [i for i in ((f.get("after") or f.get("before") or []) if isinstance(f.get("after") or f.get("before") or [], list) else []) if i in by_id]
        if len(ids) < 3:
            continue
        for i in (ids[0], ids[-1]):
            if not fixed(by_id[i]):
                out[i] = "terminal"
    return out


def shape_width(label: str, shape: str | None = None, folded: bool = False) -> int:
    """vrShapeWidth in vrtext.ts: the box width the layout gives a node drawn in `shape`."""
    base = node_width(label, folded)
    if shape in ("terminal", "io"):
        return base + TEXT["terminalExtra"]
    if shape == "junction":
        return 14
    if shape not in ("diamond", "circle"):
        return base
    lo, hi = TEXT["diamondW" if shape == "diamond" else "circleD"]
    for w in range(lo, hi, 4):
        if text_fit(label, w, False, None, shape)["needed"] <= TEXT["maxLines"]:
            return w
    return hi


def text_fit(label: str, box_w: float, stereotype: bool = False, view: str | None = None, shape: str | None = None) -> dict:
    """The lines a label is drawn in inside a box `box_w` wide, as the page draws it: {"lines", "clipped", "needed"}."""
    if shape == "junction":  # a dot draws no text (its label is the title)
        return {"lines": [], "clipped": False, "needed": 0}
    factor, inset = TEXT["shapeRoom"].get(shape, (1, TEXT["inset"]))
    w = max(1, int((box_w * factor - inset) // TEXT["charPx"]))
    out: list = []
    cur = ""
    for t in re.split(r"[ \n]", label):
        if len((cur + " " + t).strip()) > w and cur:
            out.append(cur)
            cur = t
        else:
            cur = (cur + " " + t).strip()
        while len(cur) > w:  # a word wider than a line is broken: after the last of / : . _ - that leaves a third of a line, else at w
            cut = w
            for i in range(w, -(-w // 3) - 1, -1):
                if cur[i - 1] in "/:._-":
                    cut = i
                    break
            out.append(cur[:cut])
            cur = cur[cut:]
    if cur:
        out.append(cur)
    room = TEXT["seqMaxLines" if view == "seq" else "maxLines"] - (1 if stereotype else 0)
    clipped = len(out) > room
    lines = out[:room]
    if clipped and lines:
        lines[-1] = lines[-1][:-1] + "…"
    return {"lines": lines, "clipped": clipped, "needed": len(out) + (1 if stereotype else 0)}


def fit_label(label: str, stereotype: bool = False, suffix: str = "") -> str:
    """`label` shortened (ending with an ellipsis) until `label + suffix` is drawn whole by the canvas: at most
    NODE_LABEL_MAX characters, within the lines a node has (a long word is broken, see text_fit). A label that fits is returned as is.
    Producers of labels (vr_diff, vr_extract) call this, so what they write passes `validate`."""
    def fits(s: str) -> bool:
        full = s + suffix
        return len(full) <= NODE_LABEL_MAX and not text_fit(full, node_width(full), stereotype)["clipped"]
    if fits(label):
        return label
    for n in range(min(len(label), NODE_LABEL_MAX) - 1, 0, -1):
        cand = label[:n].rstrip() + "…"
        if fits(cand):
            return cand
    return "…"


def load(path: str) -> dict:
    """Read a graph file. Raises ValueError (not JSON) or OSError (unreadable)."""
    with open(path, encoding="utf-8") as fh:
        try:
            return json.load(fh)
        except json.JSONDecodeError as exc:
            raise ValueError("not JSON: %s" % exc) from exc


def load_schema() -> dict:
    return load(SCHEMA_PATH)


# ------------------------------------------- JSON-Schema subset interpreter

def _type_ok(value, t) -> bool:
    if t == "object":
        return isinstance(value, dict)
    if t == "array":
        return isinstance(value, list)
    if t == "string":
        return isinstance(value, str)
    if t == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if t == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if t == "boolean":
        return isinstance(value, bool)
    return True


def _resolve(root: dict, ref: str) -> dict:
    node = root
    for part in ref.lstrip("#/").split("/"):
        node = node[part]
    return node


def _schema_errors(value, schema: dict, root: dict, path: str, out: list) -> None:
    """Supported keywords: $ref type enum const pattern minLength minItems
    minimum maxLength required properties additionalProperties(false) items oneOf."""
    if "$ref" in schema:
        _schema_errors(value, _resolve(root, schema["$ref"]), root, path, out)
        return
    if "type" in schema and not _type_ok(value, schema["type"]):
        out.append((path, "expected %s, got %s" % (schema["type"], type(value).__name__)))
        return
    if "enum" in schema and value not in schema["enum"]:
        out.append((path, "%r is not one of %s" % (value, schema["enum"])))
    if "const" in schema and value != schema["const"]:
        out.append((path, "must be %r" % (schema["const"],)))
    if isinstance(value, str):
        if "pattern" in schema and not re.search(schema["pattern"], value):
            out.append((path, "%r does not match %s" % (value, schema["pattern"])))
        if len(value) < schema.get("minLength", 0):
            out.append((path, "shorter than %d" % schema["minLength"]))
        if len(value) > schema.get("maxLength", len(value)):
            out.append((path, "longer than %d characters (%d)" % (schema["maxLength"], len(value))))
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            out.append((path, "%r is below %r" % (value, schema["minimum"])))
    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0):
            out.append((path, "needs at least %d item(s)" % schema["minItems"]))
        if "items" in schema:
            for i, item in enumerate(value):
                _schema_errors(item, schema["items"], root, "%s[%d]" % (path, i), out)
    if isinstance(value, dict):
        props = schema.get("properties", {})
        for key in schema.get("required", []):
            if key not in value:
                out.append((path, "missing required field %r" % key))
        for key, sub in value.items():
            if key in props:
                _schema_errors(sub, props[key], root, "%s.%s" % (path, key) if path else key, out)
            elif schema.get("additionalProperties") is False:
                out.append((path, "unknown field %r" % key))
            elif isinstance(schema.get("additionalProperties"), dict):
                _schema_errors(sub, schema["additionalProperties"], root,
                               "%s.%s" % (path, key) if path else key, out)
    if "oneOf" in schema:
        trials = []
        for sub in schema["oneOf"]:
            errs: list = []
            _schema_errors(value, sub, root, path, errs)
            trials.append(errs)
        if sum(1 for e in trials if not e) != 1:
            # Report the closest alternative: the one with fewest errors.
            best = min(trials, key=len)
            out.extend(best or [(path, "matches more than one alternative")])


# ---------------------------------------------------------------- validate

def _issue(code: str, path: str, message: str) -> dict:
    return {"code": code, "path": path, "message": message}


def _evidence_issues(ev, path: str, root, line_cache: dict, out: list) -> None:
    """Semantic checks of one evidence list (shape was checked by the schema)."""
    if not isinstance(ev, list) or not ev:
        out.append(_issue("missing_evidence_path", path + ".evidence", "no evidence: cite path:line or an inferred note"))
        return
    for i, e in enumerate(ev):
        p = "%s.evidence[%d]" % (path, i)
        if not isinstance(e, dict):
            continue
        if e.get("tier") == "source":
            rel = e.get("path")
            if not isinstance(rel, str) or not rel:
                out.append(_issue("missing_evidence_path", p, "source evidence has no path"))
                continue
            line, end = e.get("line"), e.get("end_line", e.get("line"))
            if "end_line" in e and "line" not in e:
                out.append(_issue("bad_evidence", p, "end_line without line"))
            if isinstance(line, int) and isinstance(end, int) and end < line:
                out.append(_issue("bad_evidence", p, "end_line %d is before line %d" % (end, line)))
            if rel.startswith("/") or "\\" in rel or ".." in rel.split("/"):
                out.append(_issue("bad_evidence", p, "path must be repository-relative with forward slashes: %r" % rel))
                continue
            if root is None:
                continue
            full = os.path.join(root, rel)
            if not os.path.isfile(full):
                out.append(_issue("missing_repository_path", p, "%s does not exist under %s" % (rel, root)))
                continue
            if isinstance(end, int) and end > 0:
                if full not in line_cache:
                    with open(full, "rb") as fh:
                        data = fh.read()
                    line_cache[full] = data.count(b"\n") + (1 if data and not data.endswith(b"\n") else 0)
                if end > line_cache[full]:
                    out.append(_issue("line_range_outside_file", p,
                                      "%s has %d lines, evidence cites line %d" % (rel, line_cache[full], end)))


def budget(graph: dict) -> dict:
    """Budget use of the view. {"view","counted","used","limit","over"}; groups are not counted."""
    view = graph.get("view")
    nodes = graph.get("nodes") or []
    if view == "map":
        groups = [g for g in (graph.get("groups") or []) if isinstance(g, dict)]
        gids = {g.get("id") for g in groups}
        top = {g["id"] for g in groups if g.get("parent") not in gids}
        loose = sum(1 for n in nodes if isinstance(n, dict) and n.get("group") not in gids)
        per = {}
        for n in nodes:
            if isinstance(n, dict) and n.get("group") in gids:
                per[n["group"]] = per.get(n["group"], 0) + 1
        widest = max(per.values(), default=0)
        used = len(top) + loose
        return {"view": view, "counted": "area", "used": used, "limit": MAP_AREAS, "widest_group": widest, "group_limit": MAP_GROUP,
                "over": used > MAP_AREAS or widest > MAP_GROUP}
    if view == "decision":
        nodes = [n for n in nodes if isinstance(n, dict)]
        nd = sum(1 for n in nodes if DECISION_SHAPES.get(n.get("shape")) == "decision")
        return {"view": view, "counted": "node", "used": len(nodes), "limit": DECISION_NODES, "decisions": nd,
                "decision_limit": DECISION_MAX, "paths": _path_count(graph),
                "over": len(nodes) > DECISION_NODES or nd > DECISION_MAX}
    if view == "seq":
        msgs = sum(1 for n in nodes if isinstance(n, dict) and n.get("kind") != "note")
        lanes = len(graph.get("lanes") or [])
        return {"view": view, "counted": "message", "used": msgs, "limit": SEQ["messages"], "lanes": lanes, "lane_limit": SEQ["lanes"],
                "folds": msgs > SEQ["messages"] or (lanes > SEQ["lanes"] and msgs > SEQ["group"]), "over": False}
    if view not in BUDGETS:
        return {"view": view, "counted": None, "used": None, "limit": None, "over": False}
    kind, limit = BUDGETS[view]
    used = sum(1 for n in nodes if isinstance(n, dict) and (kind is None or n.get("kind") == kind))
    return {"view": view, "counted": kind or "node", "used": used, "limit": limit, "over": used > limit}


def _seq_group_issues(graph: dict, issues: list) -> None:
    """Author-supplied ref groups of a seq view: at most SEQ["group"] messages in a group (members of its subgroups count), at most SEQ["levels"] levels."""
    groups = {g["id"]: g for g in graph.get("groups") or [] if isinstance(g, dict) and "id" in g}
    if not groups:
        return
    count: dict = {}
    for n in graph.get("nodes") or []:
        if not isinstance(n, dict) or n.get("kind") == "note":
            continue
        gid, hops = n.get("group"), 0
        while gid in groups and hops < 50:
            count[gid] = count.get(gid, 0) + 1
            gid, hops = groups[gid].get("parent"), hops + 1
    for gid, c in sorted(count.items()):
        if c > SEQ["group"] and not any(g.get("parent") == gid for g in groups.values()):
            issues.append(_issue("seq_group_too_big", "groups", "seq group %r holds %d messages (budget %d): split it into groups of at most %d, or into two levels" % (gid, c, SEQ["group"], SEQ["group"])))
    for gid in sorted(groups):
        depth, p = 1, groups[gid].get("parent")
        while p in groups and depth < 50:
            depth, p = depth + 1, groups[p].get("parent")
        if depth > SEQ["levels"]:
            issues.append(_issue("seq_too_deep", "groups", "seq group %r is %d levels deep (at most %d)" % (gid, depth, SEQ["levels"])))


def _flow_graph(graph: dict) -> tuple:
    """(node dict by id, out-edges by id) of a decision graph; edges to anything but a node are ignored."""
    nodes = {n["id"]: n for n in graph.get("nodes") or [] if isinstance(n, dict) and "id" in n}
    out: dict = {i: [] for i in nodes}
    for e in graph.get("edges") or []:
        if isinstance(e, dict) and e.get("source") in nodes and e.get("target") in nodes:
            out[e["source"]].append(e)
    return nodes, out


def _terminals(nodes: dict, which: str) -> list:
    return [i for i, n in nodes.items() if DECISION_SHAPES.get(n.get("shape")) == "terminal" and n.get("terminal") == which]


def _path_count(graph: dict) -> int:
    """Start-to-end paths of a decision graph, capped at PATH_CAP. A node is never visited twice on one path, so a loop
    (retry) is walked once: the count is the number of distinct routes through the flow."""
    nodes, out = _flow_graph(graph)
    starts = _terminals(nodes, "start")
    if len(starts) != 1:
        return 0
    ends = set(_terminals(nodes, "end"))
    count = 0
    stack = [(starts[0], frozenset([starts[0]]))]
    while stack and count < PATH_CAP:
        cur, seen = stack.pop()
        if cur in ends:
            count += 1
            continue
        for e in out[cur]:
            if e["target"] not in seen:
                stack.append((e["target"], seen | {e["target"]}))
    return count


def _decision_issues(graph: dict, issues: list, l1_steps: set | None = None, flow_rules: bool = True) -> None:
    """Semantic rules of view `decision` (graph.schema.json 1.1.0). Edges to groups are ignored: this view has no groups."""
    nodes, out = _flow_graph(graph)
    index = {n["id"]: i for i, n in enumerate(graph.get("nodes") or []) if isinstance(n, dict) and "id" in n}
    shape_ok = True
    for nid, n in nodes.items():
        p = "nodes[%d]" % index[nid]
        sh = DECISION_SHAPES.get(n.get("shape"))
        if sh is None:
            shape_ok = False
            issues.append(_issue("bad_shape", p + ".shape", "node %s: shape %r is not one of %s" % (nid, n.get("shape"), ", ".join(sorted(DECISION_SHAPES)))))
            continue
        if sh == "terminal" and n.get("terminal") not in ("start", "end"):
            shape_ok = False
            issues.append(_issue("bad_shape", p + ".terminal", "node %s: a terminal node needs terminal start or end" % nid))
        if sh != "terminal" and "terminal" in n:
            issues.append(_issue("bad_shape", p + ".terminal", "node %s: terminal is only for the shape terminal" % nid))
        if "outcome" in n and not (sh == "terminal" and n.get("terminal") == "end"):
            issues.append(_issue("bad_shape", p + ".outcome", "node %s: outcome is only for a terminal end" % nid))
        if "io" in n and sh != "io":
            issues.append(_issue("bad_shape", p + ".io", "node %s: io is only for the shape io" % nid))
        if l1_steps is not None and n.get("step") is not None and n["step"] not in l1_steps:
            issues.append(_issue("unknown_step", p + ".step", "node %s: step %r is not a node of the L1 graph" % (nid, n["step"])))
        if sh == "decision":
            if len(n.get("label", "")) > DECISION_LABEL_MAX:
                issues.append(_issue("decision_label", p + ".label", "decision %s: label has %d characters, the diamond holds %d" % (nid, len(n["label"]), DECISION_LABEL_MAX)))
            ev = n.get("evidence") or []
            if ev and not all(e.get("tier") == "inferred" for e in ev) and not any(e.get("tier") == "source" and e.get("path") for e in ev):
                issues.append(_issue("decision_evidence", p + ".evidence", "decision %s cites no source path:line (or mark every entry inferred)" % nid))
    if not shape_ok or not flow_rules:
        return
    starts, ends = _terminals(nodes, "start"), set(_terminals(nodes, "end"))
    if not starts:
        issues.append(_issue("no_start", "nodes", "the decision view has no terminal start"))
    elif len(starts) > 1:
        issues.append(_issue("multiple_start", "nodes", "the decision view has %d terminal starts, exactly 1 is allowed: %s" % (len(starts), ", ".join(starts))))
    if not ends:
        issues.append(_issue("no_end", "nodes", "the decision view has no terminal end"))
    for nid, n in nodes.items():
        p = "nodes[%d]" % index[nid]
        sh = DECISION_SHAPES[n["shape"]]
        exits = out[nid]
        if nid in ends:
            if exits:
                issues.append(_issue("end_has_exit", p, "end %s has %d outgoing edge(s)" % (nid, len(exits))))
        elif not exits:
            issues.append(_issue("dead_end", p, "%s is not an end and has no exit" % nid))
        elif sh == "decision":
            if not 2 <= len(exits) <= 3:
                issues.append(_issue("decision_exits", p, "decision %s has %d exit(s), 2 or 3 are allowed (nest the rest)" % (nid, len(exits))))
            labels = []
            for e in exits:
                if not (e.get("label") or "").strip():
                    issues.append(_issue("unlabeled_branch", "edges[%d]" % graph["edges"].index(e), "edge %s leaves decision %s with no label" % (e["id"], nid)))
                else:
                    labels.append(e["label"].strip().lower())
            if len(set(labels)) != len(labels):
                issues.append(_issue("duplicate_branch_label", p, "decision %s has two exits with the same label" % nid))
        elif len(exits) > 1:
            issues.append(_issue("fork_without_decision", p, "%s has %d exits but is not a decision" % (nid, len(exits))))
    if len(starts) == 1:
        seen, todo = {starts[0]}, [starts[0]]
        while todo:
            for e in out[todo.pop()]:
                if e["target"] not in seen:
                    seen.add(e["target"])
                    todo.append(e["target"])
        for nid in nodes:
            if nid not in seen:
                issues.append(_issue("unreachable_node", "nodes[%d]" % index[nid], "%s cannot be reached from the start" % nid))
    # A cycle with no decision in it never ends: look for a cycle among the nodes that are not decisions.
    plain = {i for i, n in nodes.items() if DECISION_SHAPES[n["shape"]] != "decision"}
    state: dict = {}

    def cyclic(root: str) -> bool:
        stack = [(root, iter(out[root]))]
        state[root] = 1
        while stack:
            cur, it = stack[-1]
            for e in it:
                t = e["target"]
                if t not in plain:
                    continue
                if state.get(t) == 1:
                    return True
                if t not in state:
                    state[t] = 1
                    stack.append((t, iter(out[t])))
                    break
            else:
                state[cur] = 2
                stack.pop()
        return False

    for nid in sorted(plain):
        if nid not in state and cyclic(nid):
            issues.append(_issue("unbounded_loop", "edges", "a cycle through %s has no decision to leave it" % nid))
            break


def validate(graph, root: str | None = None, schema: dict | None = None, check_budget: bool = True,
             step_routing: bool = False, l1_steps: set | None = None, flow_rules: bool = True) -> list:
    """Typed issues for a graph, [] when valid. Each issue is {code, path, message}.

    `step_routing`: the vr_promote_preview driver is in shadow, so every added or changed step of every flow
    (not only a `new_stem` flow) needs `to` and `call_kind`; the driver builds a draft from each flow.

    `flow_rules` (view decision): False skips the flow rules (start, ends, exits, reachability, loops, paths); vr_diff passes it for a merged
    graph, which is the union of the before and the after flow and so is no flow itself. The shape and the schema are still checked.

    `l1_steps` (optional, view decision): the node ids of the L1 graph; a node.step outside it is `unknown_step`.

    `root` (optional): a repository root; every cited path:line must exist there.
    """
    schema = schema or load_schema()
    issues: list = []
    if not isinstance(graph, dict):
        return [_issue("schema_error", "", "a graph is a JSON object")]
    ver = graph.get("schema_version")
    if isinstance(ver, str) and re.match(r"^\d+\.\d+\.\d+$", ver) and int(ver.split(".")[0]) != SUPPORTED_MAJOR:
        return [_issue("unsupported_version", "schema_version",
                       "version %s: this reader supports major %d" % (ver, SUPPORTED_MAJOR))]
    errs: list = []
    _schema_errors(graph, schema, schema, "", errs)
    for path, msg in errs:
        code = "bad_id" if "does not match ^[A-Za-z0-9_.:-]+$" in msg else "schema_error"
        if msg.startswith("longer than ") and path.rsplit(".", 1)[-1] in ("label", "title"):
            code = "label_too_long"
        issues.append(_issue(code, path, msg))
    if errs:
        # Structure is unreliable: the cross-reference checks below assume it.
        shape_broken = any(c["code"] == "schema_error" for c in issues)
        if shape_broken:
            return issues

    l1_shapes = l1_default_shapes(graph)
    for i, n in enumerate(graph.get("nodes", [])):
        if isinstance(n, dict) and isinstance(n.get("label"), str):
            shp = (shape_of(n, graph.get("view")) or l1_shapes.get(n.get("id"))) if graph.get("view") != "seq" else None
            fit = text_fit(n["label"], shape_width(n["label"], shp), bool(n.get("stereotype")), graph.get("view"), shp)
            if fit["clipped"]:
                issues.append(_issue("label_too_long", "nodes[%d].label" % i,
                                     "the label needs %d lines (a stereotype takes one) in the widest node box and the canvas draws %d: shorten it"
                                     % (fit["needed"], TEXT["seqMaxLines" if graph.get("view") == "seq" else "maxLines"])))

    lanes = [x for x in graph.get("lanes", []) if isinstance(x, dict)]

    seen: dict = {}
    for i, n in enumerate(graph.get("nodes", [])):
        if n["id"] in seen:
            issues.append(_issue("duplicate_node_id", "nodes[%d].id" % i, "node id %r is already used by nodes[%d]" % (n["id"], seen[n["id"]])))
        else:
            seen[n["id"]] = i
    group_ids = {}
    for i, g in enumerate(graph.get("groups", [])):
        if g["id"] in group_ids:
            issues.append(_issue("duplicate_group_id", "groups[%d].id" % i, "group id %r is repeated" % g["id"]))
        group_ids[g["id"]] = i
        if g["id"] in seen:
            issues.append(_issue("duplicate_node_id", "groups[%d].id" % i, "group id %r is also a node id" % g["id"]))
    lane_ids = {x["id"] for x in lanes}
    ends = set(seen) | set(group_ids)

    for i, n in enumerate(graph.get("nodes", [])):
        if n.get("group") is not None and n["group"] not in group_ids:
            issues.append(_issue("unknown_group_id", "nodes[%d].group" % i, "group %r is not defined" % n["group"]))
        if n.get("lane") is not None and n["lane"] not in lane_ids:
            issues.append(_issue("unknown_lane_id", "nodes[%d].lane" % i, "lane %r is not defined" % n["lane"]))
        if n.get("to") is not None and n["to"] not in lane_ids:
            issues.append(_issue("unknown_lane_id", "nodes[%d].to" % i, "lane %r is not defined" % n["to"]))
    by_id = {n["id"]: (i, n) for i, n in enumerate(graph.get("nodes", []))}
    for fi, f in enumerate(graph.get("flows", [])):
        if graph.get("view") == "decision":
            break  # STEP_ROUTING (to + call_kind) is for drafts of a sequence diagram, never for a decision view
        if not f.get("new_stem") and not (step_routing and graph.get("view") == "L1"):
            continue
        for sid in f.get("after", []):
            if sid not in by_id:
                continue
            ni, n = by_id[sid]
            if not f.get("new_stem") and n.get("status") not in ("added", "changed"):
                continue
            for field in ("to", "call_kind"):
                if n.get(field) is None:
                    issues.append(_issue("step_missing_" + field, "nodes[%d].%s" % (ni, field),
                                         "step %r of flow %r (%s) has no %s" % (sid, f["id"], ("new_stem %r" % f["new_stem"]) if f.get("new_stem") else "STEP_ROUTING all", field)))
    for i, g in enumerate(graph.get("groups", [])):
        if g.get("parent") is not None and g["parent"] not in group_ids:
            issues.append(_issue("unknown_group_id", "groups[%d].parent" % i, "group %r is not defined" % g["parent"]))
    eseen: dict = {}
    for i, e in enumerate(graph.get("edges", [])):
        if e["id"] in eseen:
            issues.append(_issue("duplicate_edge_id", "edges[%d].id" % i, "edge id %r is repeated" % e["id"]))
        eseen[e["id"]] = i
        if e["source"] not in ends:
            issues.append(_issue("unknown_edge_source", "edges[%d].source" % i,
                                 "edge %s: source %r is not a node or group" % (e["id"], e["source"])))
        if e["target"] not in ends:
            issues.append(_issue("unknown_edge_target", "edges[%d].target" % i,
                                 "edge %s: target %r is not a node or group" % (e["id"], e["target"])))
    for i, f in enumerate(graph.get("flows", [])):
        for lid in f.get("lanes", []):
            if lid not in lane_ids:
                issues.append(_issue("unknown_lane_id", "flows[%d].lanes" % i, "lane %r is not defined" % lid))
        for side in ("before", "after"):
            for nid in f.get(side, []):
                if nid not in seen:
                    issues.append(_issue("unknown_flow_step", "flows[%d].%s" % (i, side), "step %r is not a node" % nid))

    for i, f in enumerate(graph.get("frames", [])):
        for key, nid in [("first", f.get("first")), ("last", f.get("last"))] + [("branches.first", b.get("first")) for b in f.get("branches", [])]:
            if nid not in seen:
                issues.append(_issue("unknown_frame_step", "frames[%d].%s" % (i, key), "step %r is not a node" % nid))
    cache: dict = {}
    for coll, items in (("nodes", graph.get("nodes", [])), ("edges", graph.get("edges", [])),
                        ("groups", graph.get("groups", [])), ("flows", graph.get("flows", []))):
        for i, it in enumerate(items):
            _evidence_issues(it.get("evidence"), "%s[%d]" % (coll, i), root, cache, issues)

    if graph.get("view") == "decision":
        _decision_issues(graph, issues, l1_steps, flow_rules)
    if graph.get("view") == "seq":
        _seq_group_issues(graph, issues)
    if check_budget:
        b = budget(graph)
        if b["over"] and b["view"] == "decision":
            issues.append(_issue("over_budget", "view", "decision view has %d node(s) (budget %d) and %d decision(s) (budget %d): split it in two diagrams" % (
                b["used"], b["limit"], b["decisions"], b["decision_limit"])))
        elif b["over"]:
            issues.append(_issue("over_budget", "view",
                                 ("map view has %d area(s) (budget %d) and a group of %d (budget %d)" % (b["used"], b["limit"], b["widest_group"], b["group_limit"]))
                                 if b["view"] == "map" else
                                 "view %s has %d %s(s), budget is %d (groups not counted)" % (b["view"], b["used"], b["counted"], b["limit"])))
    return issues


# ------------------------------------------------- the stage-3 parts (every diagram part is there, or says why not)

PARTS_FILE = "parts.json"
PARTS_VERSION = "1.0.0"
PARTS_REASON_MIN, PARTS_REASON_MAX = 8, 300
# The statements `parts.json` can carry: part -> the key. A part with content (a file) needs no statement; a part with neither is a defect.
PARTS_KEYS = {"schema_version", "plan", "decision_none", "sequence_none", "affected_none", "unchanged", "new_none", "new_note"}
PARTS_PLACEHOLDER_RE = re.compile(r"^(none|n/?a|nothing|-+|<[^>]*>|tbd|todo|\.+)$", re.I)


def _parts_reason(value, where: str, issues: list) -> None:
    if not isinstance(value, str) or not value.strip():
        issues.append(_issue("parts_reason", where, "%s must be a sentence saying why (8 to %d characters)" % (where, PARTS_REASON_MAX)))
        return
    t = value.strip()
    if len(t) < PARTS_REASON_MIN or len(t) > PARTS_REASON_MAX or PARTS_PLACEHOLDER_RE.match(t):
        issues.append(_issue("parts_reason", where, "%s %r is not a reason: 8 to %d characters, never a placeholder like `none` or `<why>`" % (where, t[:40], PARTS_REASON_MAX)))


def arch_drafts(vr_dir: str) -> dict:
    """{stem: graph or None} of the arch/<stem>.json files of a dossier's visual-review directory (None: unreadable)."""
    out: dict = {}
    folder = os.path.join(vr_dir, "arch")
    if not os.path.isdir(folder):
        return out
    for f in sorted(os.listdir(folder)):
        fp = os.path.join(folder, f)
        if f.endswith(".json") and os.path.isfile(fp) and not os.path.islink(fp):
            try:
                g = load(fp)
            except (OSError, ValueError):
                g = None
            out[f[:-5]] = g if isinstance(g, dict) else None
    return out


def draft_updates_existing(graph) -> bool:
    """An arch draft that updates a diagram already in docs/architecture says so in `meta.updates_existing`; any other draft is a new diagram."""
    return isinstance(graph, dict) and isinstance(graph.get("meta"), dict) and graph["meta"].get("updates_existing") is True


def has_sequence(vr_dir: str, drafts: dict | None = None) -> bool:
    """The sequence part has content: an auto-draft in seq-preview/ or an arch draft of view `seq`."""
    folder = os.path.join(vr_dir, "seq-preview")
    if os.path.isdir(folder) and any(f.endswith(".json") and os.path.isfile(os.path.join(folder, f)) for f in os.listdir(folder)):
        return True
    drafts = arch_drafts(vr_dir) if drafts is None else drafts
    return any(isinstance(g, dict) and g.get("view") == "seq" for g in drafts.values())


def load_parts(vr_dir: str):
    """(parts dict or None, issues): parts.json of the directory; None and no issue when the file does not exist."""
    path = os.path.join(vr_dir, PARTS_FILE)
    if not os.path.isfile(path):
        return None, []
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError) as exc:
        return None, [_issue("parts_unreadable", PARTS_FILE, "parts.json is not readable: %s" % exc)]
    if not isinstance(data, dict):
        return None, [_issue("parts_unreadable", PARTS_FILE, "parts.json must be a JSON object")]
    return data, []


def parts_issues(vr_dir: str, decision: str | None = None, new=None, affected=None) -> list:
    """What is missing or wrong in the stage-3 diagram parts of one dossier directory. [] = every part is there.

    Every part is real content or a stated reason (`parts.json`, written by the diagram author):
      decision   decision.json, or `decision_none` (never with `decision` == "yes": the branch rule demands the file);
      sequence   a seq-preview/*.json auto-draft or an arch draft of view seq, or `sequence_none`;
      affected   per affected diagram (`affected` plus `plan.affected`): an arch draft with `meta.updates_existing`, or an
                 `unchanged` entry {stem, reason}; with none affected, `affected_none`;
      new        per `Diagrams new:` stem (`new`, else `plan.new`): its arch draft; with none, `new_none`.
    A part with content AND a statement saying there is none is a conflict. `new` / `affected` None: read from the plan."""
    issues: list = []
    parts, errs = load_parts(vr_dir)
    issues += errs
    if errs:
        return issues
    parts = parts or {}
    for k in parts:
        if k not in PARTS_KEYS:
            issues.append(_issue("parts_unknown_key", PARTS_FILE, "unknown key %r in parts.json" % k))
    if parts and parts.get("schema_version") != PARTS_VERSION:
        issues.append(_issue("parts_version", PARTS_FILE, "schema_version must be %r" % PARTS_VERSION))
    plan = parts.get("plan") if isinstance(parts.get("plan"), dict) else {}
    plan_aff = [s for s in (plan.get("affected") or []) if isinstance(s, str)]
    plan_new = [s for s in (plan.get("new") or []) if isinstance(s, str)]
    new_stems = list(plan_new if new is None else new)
    aff_stems = sorted(set(plan_aff if affected is None else affected) | set(plan_aff))
    if new is not None and sorted(plan_new) != sorted(new):
        issues.append(_issue("parts_plan", "plan.new", "plan.new %s differs from the dossier's `Diagrams new:` stems %s" % (plan_new, list(new))))
    if affected is not None and not set(affected) <= set(plan_aff):
        issues.append(_issue("parts_plan", "plan.affected", "plan.affected %s misses the affected diagrams %s" % (plan_aff, sorted(set(affected) - set(plan_aff)))))
    drafts = arch_drafts(vr_dir)

    def stated(key: str) -> bool:
        if key not in parts:
            return False
        _parts_reason(parts[key], key, issues)
        return True

    # decision
    has_dec = os.path.isfile(os.path.join(vr_dir, "decision.json"))
    if has_dec:
        if "decision_none" in parts:
            issues.append(_issue("parts_conflict", "decision_none", "decision.json exists, so the decision flow is not `none`: drop decision_none"))
    elif decision == "yes":
        issues.append(_issue("parts_missing", "decision", "decision.json is missing: the branch rule says the change has branches, so the decision flow is drawn"))
    elif not stated("decision_none"):
        issues.append(_issue("parts_missing", "decision", "no decision.json and no `decision_none` reason: the part is mandatory (`No decisions in this change: <why>`)"))
    # sequence
    if has_sequence(vr_dir, drafts):
        if "sequence_none" in parts:
            issues.append(_issue("parts_conflict", "sequence_none", "a sequence draft exists, so the sequence part is not `none`: drop sequence_none"))
    elif not stated("sequence_none"):
        issues.append(_issue("parts_missing", "sequence", "no sequence draft and no `sequence_none` reason: the part is mandatory (`No sequence: <why>`)"))
    # architecture impact: affected
    unchanged: dict = {}
    for i, u in enumerate(parts.get("unchanged") or []):
        if not isinstance(u, dict) or not isinstance(u.get("stem"), str):
            issues.append(_issue("parts_reason", "unchanged[%d]" % i, "an `unchanged` entry is {stem, reason}"))
            continue
        _parts_reason(u.get("reason"), "unchanged[%d].reason" % i, issues)
        unchanged[u["stem"]] = u.get("reason")
    for stem in sorted(unchanged):
        if stem not in aff_stems:
            issues.append(_issue("parts_unknown_stem", "unchanged", "`unchanged` names %s, which is not an affected diagram (%s)" % (stem, ", ".join(aff_stems) or "none")))
    upd = {s for s, g in drafts.items() if draft_updates_existing(g)}
    for stem in aff_stems:
        has_draft = stem in upd
        if has_draft and stem in unchanged:
            issues.append(_issue("parts_conflict", "unchanged", "%s has a draft and an `unchanged` entry: keep one" % stem))
        elif not has_draft and stem not in unchanged:
            issues.append(_issue("parts_missing", "affected:" + stem, "affected diagram %s has neither an updated draft arch/%s.json (meta.updates_existing) nor an `unchanged` entry" % (stem, stem)))
    for stem in sorted(upd - set(aff_stems)):
        issues.append(_issue("parts_unknown_stem", "arch/%s.json" % stem, "arch/%s.json updates an existing diagram that is not in the affected list %s" % (stem, aff_stems)))
    if aff_stems:
        if "affected_none" in parts:
            issues.append(_issue("parts_conflict", "affected_none", "diagrams %s are affected: drop affected_none" % ", ".join(aff_stems)))
    elif not stated("affected_none"):
        issues.append(_issue("parts_missing", "affected", "no affected diagram and no `affected_none` reason: the part is mandatory (`No architecture diagram affected: <why>`)"))
    # architecture impact: new
    for stem in new_stems:
        g = drafts.get(stem, False)
        if g is False:
            issues.append(_issue("parts_missing", "new:" + stem, "the dossier lists new diagram %s but arch/%s.json is missing" % (stem, stem)))
        elif draft_updates_existing(g):
            issues.append(_issue("parts_conflict", "new:" + stem, "arch/%s.json is marked updates_existing but %s is a new stem" % (stem, stem)))
    if new_stems:
        if "new_none" in parts:
            issues.append(_issue("parts_conflict", "new_none", "new diagrams %s are drafted: drop new_none" % ", ".join(new_stems)))
    elif not stated("new_none"):
        issues.append(_issue("parts_missing", "new", "no new diagram and no `new_none` reason: the part is mandatory (`No new diagram: <why>`)"))
    if "new_note" in parts:
        _parts_reason(parts["new_note"], "new_note", issues)
    return issues


# --------------------------------------------------------------------- CLI

def _cmd_validate(args) -> int:
    try:
        graph = load(args.file)
    except (OSError, ValueError) as exc:
        print(json.dumps({"ok": False, "issues": [_issue("schema_error", "", str(exc))]}, indent=2))
        return 2
    l1_steps = None
    if args.l1:
        try:
            l1_steps = {n["id"] for n in load(args.l1).get("nodes", [])}
        except (OSError, ValueError, KeyError, TypeError) as exc:
            print(json.dumps({"ok": False, "issues": [_issue("schema_error", "", "--l1: %s" % exc)]}, indent=2))
            return 2
    issues = validate(graph, root=args.root, check_budget=not args.no_budget, step_routing=args.step_routing == "all", l1_steps=l1_steps)
    print(json.dumps({"ok": not issues, "issues": issues}, indent=2))
    return 1 if issues else 0


def _stems_arg(v):
    """`--new a,b` / `--affected a,b`: a list; `none` or an empty value is the empty list; absent is None (read from the plan)."""
    if v is None:
        return None
    return [x.strip() for x in v.split(",") if x.strip() and x.strip().lower() != "none"]


def _cmd_parts(args) -> int:
    issues = parts_issues(args.dir, decision=args.decision, new=_stems_arg(args.new), affected=_stems_arg(args.affected))
    print(json.dumps({"ok": not issues, "issues": issues}, indent=2))
    return 1 if issues else 0


def _cmd_budget(args) -> int:
    try:
        graph = load(args.file)
    except (OSError, ValueError) as exc:
        print("cannot read %s: %s" % (args.file, exc), file=sys.stderr)
        return 2
    b = budget(graph)
    b["groups"] = len(graph.get("groups") or [])
    b["nodes"] = len(graph.get("nodes") or [])
    print(json.dumps(b, indent=2))
    return 1 if b["over"] else 0


# ----------------------------------------------------------------- selftest

def _selftest() -> int:
    fails: list = []

    def check(name: str, cond: bool, detail: str = "") -> None:
        if not cond:
            fails.append("%s %s" % (name, detail))

    def codes(issues) -> list:
        return sorted({i["code"] for i in issues})

    repo = os.path.join(FIXTURES, "repo")
    # The default L1 shapes agree with shared/questions/vrlayout.ts: the same fixture is pinned by shared/test/vrshapes.test.ts.
    with open(L1_SHAPES_FIXTURES, encoding="utf-8") as fh:
        l1f = json.load(fh)
    check("l1shapes:cases", len(l1f["cases"]) >= 8)
    for c in l1f["cases"]:
        check("l1shapes:" + c["name"], l1_default_shapes(c["graph"]) == c["shapes"], json.dumps(l1_default_shapes(c["graph"])))
    # the wider terminal / io box is the text room validate() judges a label by (negative: a plain step has the plain room)
    _lab = "x" * 36
    _g = {"view": "L1", "nodes": [{"id": "a", "label": _lab, "kind": "step"}], "flows": []}
    check("l1shapes:plain-room", l1_default_shapes(_g) == {} and shape_width(_lab, None) < shape_width(_lab, "terminal"))
    # The text-fit rule agrees with shared/questions/vrtext.ts: the same fixture is pinned by shared/test/vrtext.test.ts.
    with open(TEXT_FIXTURES, encoding="utf-8") as fh:
        tf = json.load(fh)
    check("textfit:cases", len(tf["cases"]) >= 10)
    for c in tf["cases"]:
        w = shape_width(c["label"], c.get("shape"), bool(c.get("folded")))
        f = text_fit(c["label"], w, bool(c.get("stereotype")), c.get("view"), c.get("shape"))
        check("textfit:" + c["label"][:30], (w, f["lines"], f["clipped"], f["needed"]) == (c["width"], c["lines"], c["clipped"], c["needed"]),
              json.dumps([w, f]))
    # A label that needs a fourth line is reported, a short one is not; the message comes from the same rule.
    long_g = load(os.path.join(FIXTURES, "minimal.json"))
    clipped_cases = [c for c in tf["cases"] if c["clipped"] and len(c["label"]) <= NODE_LABEL_MAX and not c.get("folded") and not c.get("shape")]
    check("textfit:has-clipped-case", bool(clipped_cases))
    for c in clipped_cases:
        long_g["nodes"][0]["label"] = c["label"]
        long_g["nodes"][0].pop("stereotype", None)
        if c.get("stereotype"):
            long_g["nodes"][0]["stereotype"] = "service"
        got = [i["code"] for i in validate(long_g) if i["path"] == "nodes[0].label"]
        check("textfit:validate-flags-clipped:" + c["label"][:20], got == ["label_too_long"], json.dumps(validate(long_g))[:300])
    # An over-long word is broken across lines; fit_label makes any label pass.
    long_g["nodes"][0].pop("stereotype", None)
    long_g["nodes"][0]["label"] = "go " + "w" * 50  # one long word is broken across lines, not rejected
    check("textfit:long-word-broken", validate(long_g) == [], str(validate(long_g)))
    long_g["nodes"][0]["label"] = "nats\ndeploy/compose/docker-compose.yml:52"
    check("textfit:path-anchor-ok", validate(long_g) == [], str(validate(long_g)))
    for raw in ("w" * 80, "one two " * 10, "x" * 40 + " y"):
        for st in (False, True):
            f = fit_label(raw, st, " x5")
            check("textfit:fit-label", not text_fit(f + " x5", node_width(f + " x5"), st)["clipped"], f)
    long_g["nodes"][0]["label"] = "Render the walkthrough"
    check("textfit:validate-accepts-fitting", validate(long_g) == [], json.dumps(validate(long_g))[:300])
    # Valid fixtures, including the three W-145 ones and the removed-elements one.
    for name in ("w145-l0", "w145-l1", "w145-l2", "removed", "minimal", "label-at-limit"):
        g = load(os.path.join(FIXTURES, name + ".json"))
        check("valid:" + name, validate(g) == [], json.dumps(validate(g))[:300])
    check("valid:minimal-with-root", validate(load(os.path.join(FIXTURES, "minimal.json")), root=repo) == [])
    rem = load(os.path.join(FIXTURES, "removed.json"))
    stat = {n["status"] for n in rem["nodes"]} | {e["status"] for e in rem["edges"]}
    check("removed-fixture-has-removed", "removed" in stat and "added" in stat and "changed" in stat)
    # VC-03: one fixture per issue code, the printed code is the file name.
    wanted = ["duplicate_node_id", "unknown_group_id", "missing_repository_path", "missing_evidence_path",
              "unknown_edge_source", "unknown_edge_target", "line_range_outside_file", "over_budget",
              "schema_error", "unsupported_version", "bad_id", "duplicate_edge_id", "unknown_lane_id",
              "unknown_flow_step", "bad_evidence", "label_too_long"]
    for code in wanted:
        p = os.path.join(FIXTURES, "invalid", code + ".json")
        check("vc03:exists:" + code, os.path.isfile(p))
        if os.path.isfile(p):
            got = codes(validate(load(p), root=repo))
            check("vc03:" + code, code in got, "got %s" % got)
            check("vc03:only:" + code, got == [code], "got %s" % got)
    g = load(os.path.join(FIXTURES, "w145-l1.json"))
    g["meta"]["base_sha"] = "abc1234"
    check("base_sha:ok", validate(g) == [], validate(g))
    g["meta"]["base_sha"] = "xyz"
    check("base_sha:bad", validate(g) != [])
    # promotion fields: `to` must be a lane, a flow with new_stem needs to + call_kind on every step.
    import copy
    l1 = load(os.path.join(FIXTURES, "w145-l1.json"))
    g = copy.deepcopy(l1)
    g["nodes"][0]["to"] = "lane-nope"
    check("to:unknown-lane", "unknown_lane_id" in codes(validate(g)))
    g = copy.deepcopy(l1)
    g["flows"][0]["new_stem"] = "seq-x"
    got = codes(validate(g))
    check("new_stem:steps-lack-to-and-kind", "step_missing_to" in got and "step_missing_call_kind" in got, got)
    for n in g["nodes"]:
        n["to"] = n["lane"]
        n["call_kind"] = "call"
    check("new_stem:complete-passes", validate(g) == [], validate(g))
    g["nodes"][0]["call_kind"] = "shout"
    check("call_kind:enum", validate(g) != [])
    # STEP_ROUTING all: a flow without new_stem (the shape of the shadow evidence flows) needs to + call_kind too.
    g = copy.deepcopy(l1)
    check("routing:off-flow-without-new_stem-passes", validate(g) == [], validate(g))
    got = codes(validate(g, step_routing=True))
    check("routing:all-flags-steps-lacking-to-and-kind", "step_missing_to" in got and "step_missing_call_kind" in got, got)
    for n in g["nodes"]:
        if n.get("status") in ("added", "changed"):
            n["to"] = n["lane"]
            n["call_kind"] = "call"
    check("routing:all-complete-passes", validate(g, step_routing=True) == [], validate(g, step_routing=True))
    # FU-02: 80 passes, 81 fails, a 200-character label fails, the message names the length.
    g = load(os.path.join(FIXTURES, "label-at-limit.json"))
    check("fu02:80-ok", len(g["nodes"][1]["label"]) == 80 and validate(g) == [])
    g["nodes"][1]["label"] += "y"
    check("fu02:81-fails", codes(validate(g)) == ["label_too_long"], str(validate(g)))
    g = load(os.path.join(FIXTURES, "invalid", "label_too_long.json"))
    check("fu02:200", len(g["nodes"][1]["label"]) == 200 and "200" in validate(g)[0]["message"])
    g = load(os.path.join(FIXTURES, "label-at-limit.json"))
    g["edges"][0]["label"] = "z" * 41
    g["title"] = "t" * 101
    check("fu02:edge-and-title", [i["path"] for i in validate(g)] == ["title", "edges[0].label"], str(validate(g)))
    sch = load_schema()
    check("fu02:schema-node-max", sch["$defs"]["node"]["properties"]["label"]["maxLength"] == NODE_LABEL_MAX)
    # S4 / S5 / S6 content: the message carries the edge id, the line, the view.
    g = load(os.path.join(FIXTURES, "invalid", "unknown_edge_target.json"))
    msg = [i for i in validate(g) if i["code"] == "unknown_edge_target"][0]
    check("s4:edge-id-in-message", g["edges"][0]["id"] in msg["message"], msg["message"])
    g = load(os.path.join(FIXTURES, "invalid", "over_budget.json"))
    check("s6:16-features", budget(g)["used"] == 16 and budget(g)["limit"] == 15 and budget(g)["over"])
    # Without --root the line check is skipped (format only).
    g = load(os.path.join(FIXTURES, "invalid", "line_range_outside_file.json"))
    check("root-optional", validate(g) == [])
    # Budgets: groups not counted; L1 counts steps; L2 counts nodes.
    l2 = load(os.path.join(FIXTURES, "w145-l2.json"))
    b = budget(l2)
    check("budget:l2-groups-not-counted", b["used"] == 12 and len(l2["groups"]) == 5 and not b["over"], str(b))
    check("budget:l1", budget(load(os.path.join(FIXTURES, "w145-l1.json")))["used"] == 10)
    check("budget:l0", budget(load(os.path.join(FIXTURES, "w145-l0.json")))["used"] == 7)
    full = dict(l2, view="full", nodes=l2["nodes"] * 3)
    check("budget:full-unbudgeted", not budget(full)["over"])
    # View "map": the folded first screen (top-level groups) and the widest group are budgeted; features are not counted at the top.
    nd = lambda i, gr: {"id": "n%d" % i, "label": "n", "kind": "feature", "status": "context", "group": gr}
    mp = {"view": "map", "groups": [{"id": "g%d" % i} for i in range(4)], "nodes": [nd(i, "g%d" % (i % 4)) for i in range(40)], "edges": []}
    check("budget:map-folded-ok", budget(mp)["used"] == 4 and budget(mp)["widest_group"] == 10 and not budget(mp)["over"], str(budget(mp)))
    check("budget:map-too-many-areas", budget(dict(mp, groups=[{"id": "g%d" % i} for i in range(8)]))["over"])
    check("budget:map-nested-groups-fold-into-top", not budget(dict(mp, groups=mp["groups"] + [{"id": "s", "parent": "g0"}] * 3))["over"])
    check("budget:map-wide-group", budget(dict(mp, nodes=[nd(i, "g0") for i in range(16)]))["over"])
    # View "seq": over the budget folds (never an error); only an author group that is too big or too deep is an issue.
    sm = lambda i: {"id": "m%d" % i, "label": "m", "kind": "message", "status": "context", "lane": "l0", "group": None}
    sq = {"view": "seq", "lanes": [{"id": "l0", "label": "A"}], "nodes": [sm(i) for i in range(20)], "edges": []}
    check("budget:seq-over-folds-not-errors", budget(sq)["folds"] and not budget(sq)["over"], str(budget(sq)))
    check("budget:seq-within", not budget(dict(sq, nodes=sq["nodes"][:10]))["folds"])
    gs = {"view": "seq", "groups": [{"id": "g"}], "nodes": [dict(sm(i), group="g") for i in range(7)]}
    issues_g: list = []
    _seq_group_issues(gs, issues_g)
    check("budget:seq-group-too-big", [i["code"] for i in issues_g] == ["seq_group_too_big"], str(issues_g))
    gd = {"view": "seq", "groups": [{"id": "a"}, {"id": "b", "parent": "a"}, {"id": "c", "parent": "b"}], "nodes": [dict(sm(0), group="c")]}
    issues_d: list = []
    _seq_group_issues(gd, issues_d)
    check("budget:seq-too-deep", [i["code"] for i in issues_d] == ["seq_too_deep"], str(issues_d))
    ts = open(os.path.join(HERE, "..", "shared", "questions", "vrlayout.ts")).read()
    mt = re.search(r"SEQ_BUDGET = \{ lanes: (\d+), messages: (\d+), group: (\d+), laneLabel: (\d+), msgLabel: (\d+), maxLevels: (\d+) \}", ts)
    check("budget:seq-numbers-match-vrlayout", bool(mt) and [int(x) for x in mt.groups()] == [SEQ[k] for k in ("lanes", "messages", "group", "lane_label", "msg_label", "levels")], str(mt and mt.groups()))
    # Evidence sentences: inferred ok, end before start, absolute path refused.
    base = load(os.path.join(FIXTURES, "minimal.json"))
    for label, ev, want in (
        ("inferred-ok", [{"tier": "inferred", "note": "why"}], None),
        ("end-before-line", [{"tier": "source", "path": "src/lib.rs", "line": 3, "end_line": 1}], "bad_evidence"),
        ("absolute-path", [{"tier": "source", "path": "/etc/passwd", "line": 1}], "bad_evidence"),
        ("empty-evidence", [], "missing_evidence_path"),
    ):
        gg = json.loads(json.dumps(base))
        gg["nodes"][0]["evidence"] = ev
        got = codes(validate(gg, root=repo))
        check("evidence:" + label, (want is None and got == []) or (want in got), str(got))
    # View decision: the fixture is valid; each rule has one failing graph (a mutation of it) that raises exactly that code.
    dec = load(os.path.join(FIXTURES, "decision-retry.json"))
    check("decision:valid", validate(dec) == [], json.dumps(validate(dec))[:300])
    check("decision:valid-with-routing-and-root-free", validate(dec, step_routing=True) == [], "STEP_ROUTING is for sequence drafts only")
    check("decision:no-to-call_kind-needed", all("to" not in n and "call_kind" not in n for n in dec["nodes"]))
    b = budget(dec)
    check("decision:budget", b["used"] == 8 and b["decisions"] == 2 and b["paths"] == 2 and not b["over"], str(b))
    check("decision:l1-step-ok", validate(dec, l1_steps={"step:deliver::send"}) == [])
    S, SEND, OK1, OK2 = "end:start:delivery", "proc:deliver::send", "decision:deliver::run:status-ok", "decision:deliver::run:attempts-left"
    BACK, DLQ, DONE, PARK = "proc:deliver::backoff", "proc:deliver::dead_letter", "end:ok:delivered", "end:error:parked"

    def mut(fn):
        g = copy.deepcopy(dec)
        fn(g)
        return g

    def node(g, nid):
        return next(n for n in g["nodes"] if n["id"] == nid)

    def edge(g, a, b2):
        return next(e for e in g["edges"] if e["source"] == a and e["target"] == b2)

    def drop_edge(g, a, b2):
        g["edges"].remove(edge(g, a, b2))

    def second_start(g):
        g["nodes"].append({"id": "end:start:retry", "label": "Replay requested", "kind": "terminal", "shape": "terminal", "terminal": "start", "status": "added",
                           "evidence": [{"tier": "inferred", "note": "t"}]})
        g["edges"].append({"id": "edge:flow:replay", "source": "end:start:retry", "target": SEND, "kind": "flow", "status": "added", "evidence": [{"tier": "inferred", "note": "t"}]})

    def no_end(g):
        for n in g["nodes"]:
            if n.get("terminal") == "end":
                n["shape"], n["kind"] = "process", "step"
                del n["terminal"]
                n.pop("outcome", None)

    def four_exit(g):
        for i in (1, 2):
            g["edges"].append({"id": "edge:flow:x%d" % i, "source": OK2, "target": PARK, "kind": "flow", "label": "OPT%d" % i, "status": "added",
                               "evidence": [{"tier": "inferred", "note": "t"}]})

    def one_exit(g):
        drop_edge(g, OK2, DLQ)

    def island(g):
        g["nodes"].append({"id": "proc:island", "label": "Orphan", "kind": "step", "shape": "process", "status": "added", "evidence": [{"tier": "inferred", "note": "t"}]})
        g["edges"].append({"id": "edge:flow:island", "source": "proc:island", "target": PARK, "kind": "flow", "status": "added", "evidence": [{"tier": "inferred", "note": "t"}]})

    def dup_labels(g):
        edge(g, OK1, OK2)["label"] = "YES"

    def loop_no_decision(g):
        g["nodes"].append({"id": "proc:spin", "label": "Spin", "kind": "step", "shape": "process", "status": "added", "evidence": [{"tier": "inferred", "note": "t"}]})
        g["edges"] += [{"id": "edge:flow:spin1", "source": SEND, "target": "proc:spin", "kind": "flow", "status": "added", "evidence": [{"tier": "inferred", "note": "t"}]},
                       {"id": "edge:flow:spin2", "source": "proc:spin", "target": SEND, "kind": "flow", "status": "added", "evidence": [{"tier": "inferred", "note": "t"}]}]
        drop_edge(g, SEND, OK1)
        g["edges"].append({"id": "edge:flow:spin3", "source": "proc:spin", "target": OK1, "kind": "flow", "status": "added", "evidence": [{"tier": "inferred", "note": "t"}]})

    def many_nodes(g):
        prev = PARK
        for i in range(7):
            g["nodes"].append({"id": "proc:pad%d" % i, "label": "Pad %d" % i, "kind": "step", "shape": "process", "status": "added", "evidence": [{"tier": "inferred", "note": "t"}]})
        # chain the padding between DLQ and the error end so every node stays reachable and has one exit
        drop_edge(g, DLQ, PARK)
        ids = [DLQ] + ["proc:pad%d" % i for i in range(7)] + [PARK]
        for i, (a, b2) in enumerate(zip(ids, ids[1:])):
            g["edges"].append({"id": "edge:flow:pad%d" % i, "source": a, "target": b2, "kind": "flow", "status": "added", "evidence": [{"tier": "inferred", "note": "t"}]})

    def six_decisions(g):
        # replace the process "backoff" by a chain of 4 more yes/no decisions that all leave to the same end
        for i in range(4):
            nid = "decision:extra%d" % i
            g["nodes"].append({"id": nid, "label": "Extra %d?" % i, "kind": "decision", "shape": "decision", "status": "added", "evidence": [{"tier": "inferred", "note": "t"}]})
        drop_edge(g, OK1, OK2)
        ids = [OK1] + ["decision:extra%d" % i for i in range(4)] + [OK2]
        for i, (a, b2) in enumerate(zip(ids, ids[1:])):
            g["edges"].append({"id": "edge:flow:ch%d" % i, "source": a, "target": b2, "kind": "flow", "label": "NO", "status": "added", "evidence": [{"tier": "inferred", "note": "t"}]})
        for i in range(4):
            g["edges"].append({"id": "edge:flow:out%d" % i, "source": "decision:extra%d" % i, "target": DONE, "kind": "flow", "label": "YES", "status": "added",
                               "evidence": [{"tier": "inferred", "note": "t"}]})

    rules = [
        ("no_start", lambda g: node(g, S).update(terminal="end"), {"no_start", "end_has_exit"}),
        ("multiple_start", second_start, {"multiple_start"}),
        ("no_end", no_end, {"no_end", "dead_end"}),
        ("end_has_exit", lambda g: g["edges"].append({"id": "edge:flow:out", "source": DONE, "target": SEND, "kind": "flow", "status": "added", "evidence": [{"tier": "inferred", "note": "t"}]}), {"end_has_exit"}),
        ("dead_end", lambda g: drop_edge(g, SEND, OK1), {"dead_end", "unreachable_node"}),
        ("unreachable_node", island, {"unreachable_node"}),
        ("decision_exits:4", four_exit, {"decision_exits"}),
        ("decision_exits:1", one_exit, {"decision_exits", "unreachable_node"}),
        ("unlabeled_branch", lambda g: edge(g, OK1, DONE).pop("label"), {"unlabeled_branch"}),
        ("duplicate_branch_label", dup_labels, {"duplicate_branch_label"}),
        ("fork_without_decision", lambda g: g["edges"].append({"id": "edge:flow:fork", "source": SEND, "target": DONE, "kind": "flow", "status": "added", "evidence": [{"tier": "inferred", "note": "t"}]}), {"fork_without_decision"}),
        ("unbounded_loop", loop_no_decision, {"unbounded_loop", "fork_without_decision"}),
        ("decision_label", lambda g: node(g, OK1).update(label="Did the subscriber answer with a 2xx status code?!"), {"decision_label"}),
        ("decision_evidence", lambda g: node(g, OK1).update(evidence=[{"tier": "source", "note": "no path"}]), {"decision_evidence", "missing_evidence_path"}),
        ("over_budget:nodes", many_nodes, {"over_budget"}),
        ("over_budget:decisions", six_decisions, {"over_budget"}),
        ("bad_shape:unknown", lambda g: node(g, SEND).update(shape="hexagon"), {"bad_shape"}),
        ("bad_shape:terminal-field", lambda g: node(g, S).pop("terminal"), {"bad_shape"}),
        ("bad_shape:outcome-off-end", lambda g: node(g, SEND).update(outcome="ok"), {"bad_shape"}),
        ("bad_shape:io-off-io", lambda g: node(g, BACK).update(io="in"), {"bad_shape"}),
    ]
    for name, fn, want in rules:
        got = set(codes(validate(mut(fn))))
        check("decision:rule:" + name, want <= got and got == want, "got %s want %s" % (sorted(got), sorted(want)))
    check("decision:unknown-step", codes(validate(dec, l1_steps={"step:other"})) == ["unknown_step"])
    # Mermaid-compatible aliases are the same shapes; the decision view is closed on the schema side too.
    al = mut(lambda g: [node(g, S).update(shape="stadium"), node(g, BACK).update(shape="rect"), node(g, OK1).update(shape="diamond"), node(g, OK2).update(shape="diamond")])
    check("decision:aliases", validate(al) == [] and budget(al)["decisions"] == 2, str(validate(al)))
    check("decision:bad-branch-enum", validate(mut(lambda g: edge(g, OK1, DONE).update(branch="maybe"))) != [])
    # Paths: a loop is walked once; 3-way decision gives 3 routes; the count is capped.
    tri = mut(lambda g: g["edges"].append({"id": "edge:flow:tri", "source": OK2, "target": DONE, "kind": "flow", "label": "SKIP", "branch": "case", "status": "added", "evidence": [{"tier": "inferred", "note": "t"}]}))
    check("decision:paths-3-way", budget(tri)["paths"] == 3 and validate(tri) == [], str(budget(tri)))
    broken = mut(lambda g: drop_edge(g, SEND, OK1))
    check("decision:flow-rules-off-for-merged", validate(broken, flow_rules=False) == [] and validate(broken) != [])
    check("decision:cycle-with-decision-ok", any(e["source"] == BACK and e["target"] == SEND for e in dec["edges"]))
    # CLI: exit 1 on an invalid file, 0 on a valid one, 2 on junk.
    me = os.path.abspath(__file__)
    def run(*a):
        return subprocess.run([sys.executable, me] + list(a), capture_output=True, text=True)
    r = run("validate", os.path.join(FIXTURES, "invalid", "duplicate_node_id.json"))
    check("cli:invalid-exit-1", r.returncode == 1 and "duplicate_node_id" in r.stdout)
    r = run("validate", os.path.join(FIXTURES, "w145-l1.json"))
    check("cli:valid-exit-0", r.returncode == 0 and json.loads(r.stdout)["ok"] is True)
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as tf:
        tf.write("{nope")
    r = run("validate", tf.name)
    os.unlink(tf.name)
    check("cli:junk-exit-2", r.returncode == 2)
    r = run("budget", os.path.join(FIXTURES, "invalid", "over_budget.json"))
    check("cli:budget-over", r.returncode == 1 and json.loads(r.stdout)["used"] == 16)
    # ---- the stage-3 parts: every part is content or a stated reason
    import shutil as _sh
    def _pd(files):
        d = tempfile.mkdtemp()
        for rel, val in files.items():
            fp = os.path.join(d, rel)
            os.makedirs(os.path.dirname(fp), exist_ok=True)
            with open(fp, "w") as fh:
                json.dump(val, fh)
        return d
    _g = lambda view, upd=False: {"view": view, "nodes": [], "meta": {"updates_existing": True} if upd else {}}
    _full = {"parts.json": {"schema_version": "1.0.0", "plan": {"affected": [], "new": []}, "decision_none": "no condition in the changed behaviour",
             "sequence_none": "no flow adds or changes a step", "affected_none": "the change stays inside one module no diagram shows",
             "new_none": "no new flow or system appears"}}
    _d = _pd(_full)
    check("parts:all-none-ok", parts_issues(_d) == [], json.dumps(parts_issues(_d)))
    _sh.rmtree(_d)
    for _key, _code in (("decision_none", "decision"), ("sequence_none", "sequence"), ("affected_none", "affected"), ("new_none", "new")):
        _f = json.loads(json.dumps(_full))
        del _f["parts.json"][_key]
        _d = _pd(_f)
        _i = parts_issues(_d)
        check("parts:missing-" + _code, [x["path"] for x in _i if x["code"] == "parts_missing"] == [_code], json.dumps(_i))
        _sh.rmtree(_d)
    _d = _pd({})
    check("parts:no-file-all-missing", sorted(x["path"] for x in parts_issues(_d)) == ["affected", "decision", "new", "sequence"])
    check("parts:decision-yes-needs-file", any(x["path"] == "decision" for x in parts_issues(_d, decision="yes")))
    _sh.rmtree(_d)
    _f = json.loads(json.dumps(_full)); _f["parts.json"]["decision_none"] = "none"
    _d = _pd(_f)
    check("parts:placeholder-reason", [x["code"] for x in parts_issues(_d)] == ["parts_reason"])
    _sh.rmtree(_d)
    _f = json.loads(json.dumps(_full)); _f["decision.json"] = _g("decision")
    _d = _pd(_f)
    check("parts:decision-file-and-none-conflict", [x["code"] for x in parts_issues(_d)] == ["parts_conflict"])
    _sh.rmtree(_d)
    _f = {"decision.json": _g("decision"), "seq-preview/seq-auto-x.json": _g("seq"), "arch/c4-a.json": _g("c4", True), "arch/seq-n.json": _g("seq"),
          "parts.json": {"schema_version": "1.0.0", "plan": {"affected": ["c4-a", "c1-b"], "new": ["seq-n"]},
                         "unchanged": [{"stem": "c1-b", "reason": "only a function body moved inside one container"}]}}
    _d = _pd(_f)
    check("parts:content-and-unchanged-ok", parts_issues(_d, decision="yes", new=["seq-n"], affected=["c4-a", "c1-b"]) == [], json.dumps(parts_issues(_d, decision="yes", new=["seq-n"], affected=["c4-a", "c1-b"])))
    check("parts:new-stem-mismatch", any(x["code"] == "parts_plan" for x in parts_issues(_d, new=["seq-other"])))
    check("parts:affected-not-in-plan", any(x["code"] == "parts_plan" for x in parts_issues(_d, affected=["c4-zzz"])))
    _f["parts.json"]["unchanged"] = []
    with open(os.path.join(_d, "parts.json"), "w") as fh:
        json.dump(_f["parts.json"], fh)
    check("parts:affected-needs-draft-or-unchanged", [x["path"] for x in parts_issues(_d) if x["code"] == "parts_missing"] == ["affected:c1-b"])
    _sh.rmtree(_d)
    _d = _pd({"parts.json": {"schema_version": "1.0.0", "bogus": 1}})
    check("parts:unknown-key", any(x["code"] == "parts_unknown_key" for x in parts_issues(_d)))
    _sh.rmtree(_d)
    r = run("parts", _d, "--decision", "no")
    check("cli:parts-exit-1", r.returncode == 1 and json.loads(r.stdout)["ok"] is False)
    if fails:
        print("vr_graph selftest FAILED:\n  " + "\n  ".join(fails), file=sys.stderr)
        return 1
    print("vr_graph selftest ok")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    ap.add_argument("--selftest", action="store_true")
    sub = ap.add_subparsers(dest="cmd")
    v = sub.add_parser("validate", help="typed issues as JSON; exit 1 on any")
    v.add_argument("file")
    v.add_argument("--root", help="repository root: cited path:line must exist there")
    v.add_argument("--no-budget", action="store_true")
    v.add_argument("--l1", help="view decision: the L1 graph; every node.step must be one of its node ids")
    v.add_argument("--step-routing", choices=("all", "off"), default="off",
                   help="all: every added/changed step of every L1 flow needs to + call_kind (driver vr_promote_preview in shadow)")
    v.set_defaults(fn=_cmd_validate)
    pt = sub.add_parser("parts", help="stage-3 check: every diagram part (decision, sequence, affected, new) is content or a stated reason; exit 1 on any")
    pt.add_argument("dir", help=".discovery/visual-review/<ID>/")
    pt.add_argument("--decision", choices=("yes", "no"), help="the branch rule's answer: yes demands decision.json")
    pt.add_argument("--new", help="the dossier's `Diagrams new:` stems, comma separated (`none` = no stem); default: parts.json plan.new")
    pt.add_argument("--affected", help="the affected existing diagrams, comma separated (`none` = no stem); default: parts.json plan.affected")
    pt.set_defaults(fn=_cmd_parts)
    b = sub.add_parser("budget", help="print the budget use of the view")
    b.add_argument("file")
    b.set_defaults(fn=_cmd_budget)
    args = ap.parse_args(argv)
    if args.selftest:
        return _selftest()
    if not args.cmd:
        ap.print_usage(sys.stderr)
        return 2
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
