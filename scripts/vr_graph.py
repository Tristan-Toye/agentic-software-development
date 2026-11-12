#!/usr/bin/env python3
"""Visual review: validate a change graph and count it against the view budgets.

The schema is `references/visual-review/graph.schema.json` (version 1.0.0).
This file reads that schema with a small JSON-Schema subset interpreter (no
third-party package), then adds the checks a schema cannot say: dangling edge
ends, duplicate ids, evidence that points at nothing, and the view budgets.

    vr_graph.py validate GRAPH.json [--root REPO] [--no-budget]
    vr_graph.py budget GRAPH.json
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
line_range_outside_file, over_budget.
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
BUDGETS = {"L0": ("feature", 15), "L1": ("step", 12), "L2": (None, 12)}


# ---------------------------------------------------------------- loading

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
    minimum required properties additionalProperties(false) items oneOf."""
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
    if view not in BUDGETS:
        return {"view": view, "counted": None, "used": None, "limit": None, "over": False}
    kind, limit = BUDGETS[view]
    nodes = graph.get("nodes") or []
    used = sum(1 for n in nodes if isinstance(n, dict) and (kind is None or n.get("kind") == kind))
    return {"view": view, "counted": kind or "node", "used": used, "limit": limit, "over": used > limit}


def validate(graph, root: str | None = None, schema: dict | None = None, check_budget: bool = True) -> list:
    """Typed issues for a graph, [] when valid. Each issue is {code, path, message}.

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
        issues.append(_issue(code, path, msg))
    if errs:
        # Structure is unreliable: the cross-reference checks below assume it.
        shape_broken = any(c["code"] == "schema_error" for c in issues)
        if shape_broken:
            return issues

    nodes = [n for n in graph.get("nodes", []) if isinstance(n, dict)]
    edges = [e for e in graph.get("edges", []) if isinstance(e, dict)]
    groups = [g for g in graph.get("groups", []) if isinstance(g, dict)]
    lanes = [x for x in graph.get("lanes", []) if isinstance(x, dict)]
    flows = [f for f in graph.get("flows", []) if isinstance(f, dict)]

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

    cache: dict = {}
    for coll, items in (("nodes", graph.get("nodes", [])), ("edges", graph.get("edges", [])),
                        ("groups", graph.get("groups", [])), ("flows", graph.get("flows", []))):
        for i, it in enumerate(items):
            _evidence_issues(it.get("evidence"), "%s[%d]" % (coll, i), root, cache, issues)

    if check_budget:
        b = budget(graph)
        if b["over"]:
            issues.append(_issue("over_budget", "view",
                                 "view %s has %d %s(s), budget is %d (groups not counted)" % (b["view"], b["used"], b["counted"], b["limit"])))
    return issues


# --------------------------------------------------------------------- CLI

def _cmd_validate(args) -> int:
    try:
        graph = load(args.file)
    except (OSError, ValueError) as exc:
        print(json.dumps({"ok": False, "issues": [_issue("schema_error", "", str(exc))]}, indent=2))
        return 2
    issues = validate(graph, root=args.root, check_budget=not args.no_budget)
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
    # Valid fixtures, including the three W-145 ones and the removed-elements one.
    for name in ("w145-l0", "w145-l1", "w145-l2", "removed", "minimal"):
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
              "unknown_flow_step", "bad_evidence"]
    for code in wanted:
        p = os.path.join(FIXTURES, "invalid", code + ".json")
        check("vc03:exists:" + code, os.path.isfile(p))
        if os.path.isfile(p):
            got = codes(validate(load(p), root=repo))
            check("vc03:" + code, code in got, "got %s" % got)
            check("vc03:only:" + code, got == [code], "got %s" % got)
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
    if fails:
        print("vr_graph selftest FAILED:\n  " + "\n  ".join(fails), file=sys.stderr)
        return 1
    print("vr_graph selftest ok")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--selftest", action="store_true")
    sub = ap.add_subparsers(dest="cmd")
    v = sub.add_parser("validate", help="typed issues as JSON; exit 1 on any")
    v.add_argument("file")
    v.add_argument("--root", help="repository root: cited path:line must exist there")
    v.add_argument("--no-budget", action="store_true")
    v.set_defaults(fn=_cmd_validate)
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
