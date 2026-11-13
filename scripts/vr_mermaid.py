#!/usr/bin/env python3
"""Visual review: lossless Mermaid <-> change-graph JSON converter.

The JSON (graph.schema.json) is the SOURCE of a diagram; the Mermaid block in a
Markdown file is GENERATED from it, so GitHub still renders it and it cannot
drift. The prose around a block stays hand-written.

    vr_mermaid.py import FILE.md [--write] [--repo NAME] [--commit SHA] [--view V] [--force]
    vr_mermaid.py render FILE.json
    vr_mermaid.py sync DIR
    vr_mermaid.py check DIR
    vr_mermaid.py --selftest [--corpus DIR]

import   parse every ```mermaid block of FILE.md into one graph JSON, written next to the .md
         as <stem>.json (one block in the file) or <stem>.<n>.json (n = 1.. = the block's
         position among all blocks of the file). Without --write: print what it would do.
         With --write: also replace each block by the marked, generated block
             <!-- generated from NAME by vr_mermaid.py render; edit the JSON, not this block -->
             ```mermaid
             ...
             ```
             <!-- end generated -->
         A block is only replaced when render(import(block)) equals the block (see
         NORMALISATION); otherwise nothing of that file is written and the exit code is 1.
render   print the Mermaid text of a graph JSON.
import --adopt  also re-import each marked block whose text was edited in the .md (an
         anchor --fix, a hand edit), so `sync` does not revert it; the JSON follows the .md.
sync     rewrite every marked block of the .md files of DIR (not recursive) from its JSON.
check    exit 1 listing each marked block that differs from its JSON's render, each marker
         naming a missing JSON, each marker without its fence or end marker, and each unmarked
         ```mermaid block; exit 0 otherwise (CI).

NORMALISATION (the only difference render(import(block)) may have from the original block):
trailing whitespace of every line is removed; blank lines at the start and the end of the
block are dropped; a run of blank lines becomes one blank line. Nothing else: indentation
(4 spaces per level, frames and class bodies included), line order, label and message text are
kept exactly. Blank lines inside a block are kept (meta.mermaid.blanks).

SUPPORTED MERMAID
  flowchart / graph : header (+ `%%` lines before it), subgraph ID["label"] ... end, direction,
                      nodes ID["t"] ID("t") ID(["t"]) ID[("t")] ID(("t")) ID{"t"} ID[["t"]] ID>"t"] and bare ID,
                      edges  A --> B, A -.-> B, A ==> B, A --- B, A -.- B, A === B, A ~~~ B, with an optional |"label"|.
  classDiagram      : class X, class X["label"], class X { <<stereotype>> members }, namespace N { ... },
                      relations A <arrow> B [: label] for the arrows listed in CLASS_ARROWS.
  sequenceDiagram   : participant / actor [as "alias"], messages (->> -->> -> --> -x --x -) --)
                      with an optional +/- activation), Note right of / left of / over A[,B],
                      alt/else, opt, loop, par/and, critical/option, break, rect ... end, nested.
Any other line stops the import with its line number: a construct that cannot be kept is never dropped.

ID SCHEME (STABLE ID RULE: kind plus content, never a counter or an array position)
  node / group / lane  kind:<mermaid id>             container:protocol  group:vm  lane:Caller  class:RunId
                       a character outside A-Za-z0-9_.- in a mermaid id is written .xHH. (hex of the code point)
  edge                 edge:<kind>:<source id>:<target id>
  message              msg:<from>:<to>:<slug>-<h6>   slug = first 32 characters of the text as [a-z0-9-];
                       h6 = first 6 hex of sha1 over from, arrow, activation, to and the full text
  note                 note:<position>:<anchors>:<slug>-<h6>   position right|left|over, anchors joined by -
  frame                frame:<kind>:<slug>-<h6>      over the kind and the label
  flow                 flow:main
  A repeated identical id (the same message sent twice, the same edge twice) gets the suffix .2, .3 ...
  by order of occurrence; the first keeps the bare id. Changing a message's text changes its id.

LABELS. `label` is the display text (a literal \\n or <br/> becomes a space, cut to the schema limit with a
final ellipsis); `text` holds the exact Mermaid text and is present only when it differs from `label`.
Render uses `text` when present, else `label`: edit `text` when there is one.

SEQUENCE LAYOUT. Participants are lanes (view seq), each message and each note is a step node (kind
message / note) on its sender's / anchor's lane with `order` = its position; flows[0].after lists the
steps in order and is the order render uses. Edges of kind flow link consecutive steps for the layout; render
ignores them. Frames (alt, opt, ...) are graph.frames: first / last step id, plus branches (else, and, option)
with the step each starts at; nesting is the inclusion of ranges (an outer frame that opens on the same step as an
inner one comes first in the array).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

SCHEMA_VERSION = "1.0.0"
MARK_RE = re.compile(r"^<!-- generated from (\S+) by vr_mermaid\.py render; edit the JSON, not this block -->$")
END_MARK = "<!-- end generated -->"
EVIDENCE_NOTE = "diagram source authored as JSON (imported from Mermaid)"
INDENT = "    "

LABEL_MAX = {"node": 80, "edge": 40, "lane": 40, "group": 60, "title": 100}
SHAPES = {  # name: (opener, closer)
    "rect": ("[", "]"), "round": ("(", ")"), "stadium": ("([", "])"), "cylinder": ("[(", ")]"),
    "circle": ("((", "))"), "diamond": ("{", "}"), "subroutine": ("[[", "]]"), "asymmetric": (">", "]"),
}
OPENERS = sorted(SHAPES.items(), key=lambda kv: -len(kv[1][0]))
FLOW_ARROWS = {"-->": "depends", "-.->": "dashed", "==>": "thick", "---": "line", "-.-": "dotted", "===": "thick-line", "~~~": "invisible"}
CLASS_ARROWS = {"-->": "association", "<--": "association", "--": "link", "..": "dashed-link", "..>": "depends", "<..": "depends",
                "..|>": "realizes", "<|..": "realizes", "--|>": "inherits", "<|--": "inherits", "*--": "composition", "--*": "composition",
                "o--": "aggregation", "--o": "aggregation"}
SEQ_ARROWS = ("-->>", "->>", "--x", "-x", "--)", "-)", "-->", "->")
FRAME_KINDS = ("alt", "opt", "loop", "par", "critical", "break", "rect")
BRANCH_OF = {"alt": "else", "par": "and", "critical": "option"}
BRANCH_KINDS = ("else", "and", "option")
NODE_KIND = {"c1": "element", "c2": "container", "c3": "component", "c4": "class"}


class MermaidError(ValueError):
    pass


# ------------------------------------------------------------------ helpers
def enc(mid: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.\-]", lambda m: ".x%x." % ord(m.group(0)), mid)


def dec(idv: str) -> str:
    rest = idv.split(":", 1)[1] if ":" in idv else idv
    return re.sub(r"\.x([0-9a-f]+)\.", lambda m: chr(int(m.group(1), 16)), rest)


def slug(text: str, n: int = 32) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:n].strip("-") or "x"


def h6(*parts: str) -> str:
    return hashlib.sha1("\0".join(parts).encode("utf-8")).hexdigest()[:6]


def display(text: str, limit: int) -> str:
    s = re.sub(r"\\n|<br\s*/?>", " ", text)
    s = re.sub(r" {2,}", " ", s).strip() or text
    return s if len(s) <= limit else s[: limit - 1] + "\u2026"


def put_label(obj: dict, text: str, kind: str) -> None:
    obj["label"] = display(text, LABEL_MAX[kind])
    if obj["label"] != text:
        obj["text"] = text


def full(obj: dict) -> str:
    return obj["text"] if "text" in obj else obj["label"]


def evidence() -> list:
    return [{"tier": "inferred", "note": EVIDENCE_NOTE}]


class Ids:
    """Unique ids: the first use of a base id keeps it, the n-th gets .n."""

    def __init__(self) -> None:
        self.seen: dict = {}

    def make(self, base: str) -> str:
        n = self.seen.get(base, 0) + 1
        self.seen[base] = n
        return base if n == 1 else "%s.%d" % (base, n)


def mm_of(g: dict) -> dict:
    return (g.get("meta") or {}).get("mermaid") or {}


def split_block(block: str):
    """(preamble lines, header line, body lines) of a block; the body keeps blank lines (normalised)."""
    lines = [ln.rstrip() for ln in block.split("\n")]
    while lines and not lines[-1]:
        lines.pop()
    while lines and not lines[0]:
        lines.pop(0)
    pre = []
    i = 0
    while i < len(lines) and (lines[i].startswith("%%") or not lines[i]):
        pre.append(lines[i])
        i += 1
    if i >= len(lines):
        raise MermaidError("no diagram header")
    return pre, lines[i], lines[i + 1:]


def normalise(block: str) -> str:
    out: list = []
    for ln in block.split("\n"):
        ln = ln.rstrip()
        if not ln and (not out or not out[-1]):
            continue
        out.append(ln)
    while out and not out[-1]:
        out.pop()
    return "\n".join(out)


def detect_type(header: str) -> str:
    h = header.strip()
    if re.match(r"^(flowchart|graph)(\s+(TB|TD|LR|RL|BT))?$", h):
        return "flowchart"
    if h == "classDiagram":
        return "classDiagram"
    if h == "sequenceDiagram":
        return "sequenceDiagram"
    raise MermaidError("unsupported diagram header %r" % header)


def view_for(stem: str, typ: str) -> str:
    if typ == "classDiagram":
        return "c4"
    if typ == "sequenceDiagram":
        return "seq"
    m = re.match(r"^(c[123])(?:[-_.]|$)", stem)
    return m.group(1) if m else "full"


def new_graph(view: str, title: str, meta: dict, typ: str, header: str, pre: list) -> dict:
    mm: dict = {"type": typ, "header": header}
    if pre:
        mm["preamble"] = pre
    m = {"repo": meta.get("repo", "unknown"), "commit": meta.get("commit", "unknown"), "generated_by": "hand", "mermaid": mm}
    return {"schema_version": SCHEMA_VERSION, "view": view, "title": display(title, LABEL_MAX["title"]) or "diagram",
            "meta": m, "nodes": [], "edges": []}


def parse_label(raw: str, what: str, ln: int) -> str:
    raw = raw.strip()
    if len(raw) >= 2 and raw[0] == '"' and raw[-1] == '"':
        return raw[1:-1]
    raise MermaidError("line %d: %s label must be double-quoted: %r" % (ln, what, raw))


# ------------------------------------------------------------------ import: flowchart
SUB_RE = re.compile(r"^subgraph\s+([A-Za-z0-9_]+)(?:\[(.*)\])?$")
NODE_ID = r"[A-Za-z0-9_]+"
EDGE_RE = re.compile(r"^(%s)\s+(%s)(?:\|\"(.*)\"\|)?\s*(%s)$" % (NODE_ID, "|".join(re.escape(a) for a in sorted(FLOW_ARROWS, key=len, reverse=True)), NODE_ID))


def parse_node_line(s: str):
    """(mid, shape, text) for `ID`, `ID<open>"text"<close>`; None when the line is no node."""
    m = re.match(r"^(%s)(.*)$" % NODE_ID, s)
    if not m:
        return None
    mid, rest = m.group(1), m.group(2)
    if not rest:
        return mid, "bare", None
    for name, (op, cl) in OPENERS:
        if rest.startswith(op + '"') and rest.endswith('"' + cl):
            inner = rest[len(op) + 1: len(rest) - len(cl) - 1]
            return (mid, name, inner) if '"' not in inner else None
    return None


def import_flowchart(g: dict, body: list, view: str) -> None:
    kind = NODE_KIND.get(view, "node")
    stmts = []
    stack = []
    blank = False
    for i, raw in enumerate(body):
        ln = i + 2
        s = raw.strip()
        if not s:
            blank = True
            continue
        item = None
        m = SUB_RE.match(s)
        if m:
            label = parse_label(m.group(2), "subgraph", ln) if m.group(2) is not None else None
            item = ("subgraph", m.group(1), label)
            stack.append(m.group(1))
        elif s == "end":
            if not stack:
                raise MermaidError("line %d: end without subgraph" % ln)
            item = ("end", stack.pop())
        elif re.match(r"^direction\s+(TB|TD|LR|RL|BT)$", s):
            if not stack or blank or stmts[-1][0] != "subgraph":
                raise MermaidError("line %d: direction only directly under a subgraph line" % ln)
            stmts.append(("direction", s.split()[1]))
            continue
        elif s.startswith("%%"):
            raise MermaidError("line %d: a %% comment after the header cannot be kept" % ln)
        else:
            m = EDGE_RE.match(s)
            if m:
                item = ("edge", m.group(1), m.group(2), m.group(3), m.group(4))
            else:
                nd = parse_node_line(s)
                if nd is None:
                    raise MermaidError("line %d: unsupported flowchart statement %r" % (ln, s))
                item = ("node",) + nd
        stmts.append(item + (blank,))
        blank = False
    if stack:
        raise MermaidError("subgraph %s is never closed" % stack[-1])
    groups = {s[1] for s in stmts if s[0] == "subgraph"}
    declared = {s[1] for s in stmts if s[0] == "node"}
    ids = Ids()
    nodes: dict = {}
    order: list = []
    blanks: list = []
    gstack: list = []
    gmap: dict = {}

    def endpoint(mid: str) -> str:
        return ("group:" if mid in groups else kind + ":") + enc(mid)

    def implicit(mid: str) -> None:
        if mid not in declared and mid not in groups and mid not in nodes:
            nd = {"id": endpoint(mid), "label": mid, "kind": kind, "status": "context", "shape": "implicit", "evidence": evidence()}
            if gstack:
                nd["group"] = gstack[-1]
            nodes[mid] = nd
            g["nodes"].append(nd)

    for s in stmts:
        if s[0] == "direction":
            g["groups"][-1]["direction"] = s[1]
            continue
        tok = None
        if s[0] == "subgraph":
            gid = "group:" + enc(s[1])
            if s[1] in gmap:
                raise MermaidError("subgraph %s declared twice" % s[1])
            grp = {"id": gid, "label": s[1], "status": "context", "evidence": evidence()}
            if s[2] is not None:
                put_label(grp, s[2], "group")
            if gstack:
                grp["parent"] = gstack[-1]
            g.setdefault("groups", []).append(grp)
            gmap[s[1]] = grp
            gstack.append(gid)
            tok = gid
        elif s[0] == "end":
            tok = "end:" + gstack.pop()
        elif s[0] == "node":
            _, mid, shape, text, _b = s
            if mid in nodes or mid in groups:
                raise MermaidError("node %s declared twice" % mid)
            nd = {"id": "%s:%s" % (kind, enc(mid)), "label": mid, "kind": kind, "status": "context"}
            if text is not None:
                put_label(nd, text, "node")
            if shape != "rect":
                nd["shape"] = shape
            if gstack:
                nd["group"] = gstack[-1]
            nd["evidence"] = evidence()
            nodes[mid] = nd
            g["nodes"].append(nd)
            tok = nd["id"]
        else:
            _, a, arrow, label, b, _b = s
            implicit(a)
            implicit(b)
            ea, eb = endpoint(a), endpoint(b)
            ek = FLOW_ARROWS[arrow]
            e = {"id": ids.make("edge:%s:%s:%s" % (ek, ea, eb)), "source": ea, "target": eb, "kind": ek, "arrow": arrow, "status": "context"}
            if label is not None:
                put_label(e, label, "edge")
            e["evidence"] = evidence()
            g["edges"].append(e)
            tok = e["id"]
        order.append(tok)
        if s[-1]:
            blanks.append(tok)
    finish_order(g, order, blanks)


def finish_order(g: dict, order: list, blanks: list) -> None:
    mm = g["meta"]["mermaid"]
    if order != canonical_order(g):
        mm["order"] = order
    if blanks:
        mm["blanks"] = blanks


def canonical_order(g: dict) -> list:
    def body(gid):
        out = [n["id"] for n in g["nodes"] if n.get("group") == gid and n.get("shape") != "implicit"]
        for gr in g.get("groups", []):
            if gr.get("parent") == gid:
                out += [gr["id"]] + body(gr["id"]) + ["end:" + gr["id"]]
        return out
    return body(None) + [e["id"] for e in g["edges"]]


def effective_order(g: dict) -> list:
    """meta.mermaid.order with unknown ids dropped; an element the order lacks goes after its canonical predecessor."""
    canon = canonical_order(g)
    order = mm_of(g).get("order")
    if not order:
        return canon
    known = set(canon)
    toks = [t for t in order if t in known]
    have = set(toks)
    for i, t in enumerate(canon):
        if t in have:
            continue
        at = 0
        for j in range(i - 1, -1, -1):
            if canon[j] in have:
                at = toks.index(canon[j]) + 1
                break
        toks.insert(at, t)
        have.add(t)
    return toks


# ------------------------------------------------------------------ import: classDiagram
CLASS_REL_RE = re.compile(r"^(\S+)\s+(%s)\s+(\S+?)(?:\s*:\s?(.*))?$" % "|".join(re.escape(a) for a in sorted(CLASS_ARROWS, key=len, reverse=True)))
CLASS_DECL_RE = re.compile(r"^class\s+([A-Za-z0-9_.~]+)(?:\[(.*)\])?(\s*\{)?$")


def import_class(g: dict, body: list) -> None:
    ids = Ids()
    order: list = []
    blanks: list = []
    nodes: dict = {}
    stack: list = []   # ("ns", gid) | ("class", node)
    blank = False
    pending_edges: list = []
    for i, raw in enumerate(body):
        ln = i + 2
        s = raw.strip()
        if not s:
            blank = True
            continue
        top = stack[-1] if stack else None
        if top and top[0] == "class":
            if s == "}":
                nd = stack.pop()[1]
                members = nd.pop("_members")
                if members and re.match(r"^<<.*>>$", members[0]):
                    nd["stereotype"] = members.pop(0)[2:-2]
                if members:
                    nd["members"] = members
                continue
            if blank:
                raise MermaidError("line %d: a blank line inside a class body cannot be kept" % ln)
            top[1]["_members"].append(s)
            continue
        tok = None
        m = re.match(r"^namespace\s+([A-Za-z0-9_.]+)\s*\{$", s)
        if m:
            gid = "group:" + enc(m.group(1))
            grp = {"id": gid, "label": m.group(1), "kind": "namespace", "status": "context", "evidence": evidence()}
            if stack:
                grp["parent"] = stack[-1][1]
            g.setdefault("groups", []).append(grp)
            stack.append(("ns", gid))
            tok = gid
        elif s == "}":
            if not stack or stack[-1][0] != "ns":
                raise MermaidError("line %d: unexpected }" % ln)
            tok = "end:" + stack.pop()[1]
        else:
            m = CLASS_DECL_RE.match(s)
            if m:
                name = m.group(1)
                if name in nodes:
                    raise MermaidError("line %d: class %s declared twice" % (ln, name))
                nd = {"id": "class:" + enc(name), "label": name, "kind": "class", "status": "context"}
                if m.group(2) is not None:
                    put_label(nd, parse_label(m.group(2), "class", ln), "node")
                if stack:
                    nd["group"] = stack[-1][1]
                nodes[name] = nd
                g["nodes"].append(nd)
                tok = nd["id"]
                if m.group(3):
                    nd["_members"] = []
                    stack.append(("class", nd))
                else:
                    nd["evidence"] = evidence()
            else:
                m = CLASS_REL_RE.match(s)
                if not m:
                    raise MermaidError("line %d: unsupported classDiagram statement %r" % (ln, s))
                pending_edges.append((m.group(1), m.group(2), m.group(3), m.group(4)))
                tok = ("edge", len(pending_edges) - 1)
        order.append(tok)
        if blank:
            blanks.append(tok)
        blank = False
    if stack:
        raise MermaidError("%s is never closed" % ("class body" if stack[-1][0] == "class" else "namespace"))
    for nd in g["nodes"]:
        nd.setdefault("evidence", evidence())
    # relations: implicit classes, ids, then replace the edge placeholders in order / blanks
    eids: dict = {}
    for k, (a, arrow, b, label) in enumerate(pending_edges):
        for name in (a, b):
            if name not in nodes:
                nd = {"id": "class:" + enc(name), "label": name, "kind": "class", "status": "context", "shape": "implicit", "evidence": evidence()}
                nodes[name] = nd
                g["nodes"].append(nd)
        ek = CLASS_ARROWS[arrow]
        e = {"id": ids.make("edge:%s:%s:%s" % (ek, nodes[a]["id"], nodes[b]["id"])), "source": nodes[a]["id"], "target": nodes[b]["id"],
             "kind": ek, "arrow": arrow, "status": "context"}
        if label is not None:
            put_label(e, label, "edge")
        e["evidence"] = evidence()
        g["edges"].append(e)
        eids[("edge", k)] = e["id"]
    order = [eids.get(t, t) if isinstance(t, tuple) else t for t in order]
    blanks = [eids.get(t, t) if isinstance(t, tuple) else t for t in blanks]
    finish_order(g, order, blanks)


# ------------------------------------------------------------------ import: sequenceDiagram
PART_RE = re.compile(r"^(participant|actor)\s+([A-Za-z0-9_]+)(?:\s+as\s+(.*))?$")
MSG_RE = re.compile(r"^([A-Za-z0-9_]+)\s*(%s)([+-]?)([A-Za-z0-9_]+)\s*:\s?(.*)$" % "|".join(re.escape(a) for a in SEQ_ARROWS))
NOTE_RE = re.compile(r"^Note\s+(right of|left of|over)\s+([A-Za-z0-9_]+(?:,[A-Za-z0-9_]+)?)\s*:\s?(.*)$")
FRAME_RE = re.compile(r"^(%s)(?:\s+(.*))?$" % "|".join(FRAME_KINDS))
BRANCH_RE = re.compile(r"^(%s)(?:\s+(.*))?$" % "|".join(BRANCH_KINDS))


def import_sequence(g: dict, body: list) -> None:
    ids = Ids()
    lanes: dict = {}
    steps: list = []
    frames: list = []
    blanks: list = []
    stack: list = []          # open frames
    pend_first: list = []     # frames / branches waiting for their first step
    blank = False
    declare_done = False

    def lane(mid: str) -> str:
        if mid not in lanes:
            lanes[mid] = {"id": "lane:" + enc(mid), "label": mid, "implicit": True}
        return lanes[mid]["id"]

    for i, raw in enumerate(body):
        ln = i + 2
        s = raw.strip()
        if not s:
            blank = True
            continue
        tok = None
        m = PART_RE.match(s)
        if m:
            if declare_done:
                raise MermaidError("line %d: participant after the first message cannot be kept" % ln)
            mid = m.group(2)
            if mid in lanes:
                raise MermaidError("line %d: participant %s declared twice" % (ln, mid))
            ln_obj = {"id": "lane:" + enc(mid), "label": mid}
            if m.group(3) is None:
                ln_obj["bare"] = True
            else:
                put_label(ln_obj, parse_label(m.group(3), "participant alias", ln), "lane")
            if m.group(1) == "actor":
                ln_obj["declare"] = "actor"
            lanes[mid] = ln_obj
            tok = ln_obj["id"]
        else:
            declare_done = True
            m = MSG_RE.match(s)
            if m:
                a, arrow, act, b, text = m.groups()
                fr, to = lane(a), lane(b)
                base = "msg:%s:%s:%s-%s" % (enc(a), enc(b), slug(text), h6(a, arrow, act, b, text))
                nd = {"id": ids.make(base), "kind": "message", "status": "context", "lane": fr, "order": len(steps) + 1}
                put_label(nd, text, "node")
                nd["seq"] = {"to": to, "arrow": arrow}
                if act:
                    nd["seq"]["activate"] = act
                steps.append(nd)
                tok = nd["id"]
            else:
                m = NOTE_RE.match(s)
                if m:
                    pos, anchors, text = m.groups()
                    al = [lane(x) for x in anchors.split(",")]
                    base = "note:%s:%s:%s-%s" % (pos.split()[0], "-".join(enc(x) for x in anchors.split(",")), slug(text), h6(pos, anchors, text))
                    nd = {"id": ids.make(base), "kind": "note", "status": "context", "lane": al[0], "order": len(steps) + 1}
                    put_label(nd, text, "node")
                    nd["seq"] = {"position": pos}
                    if len(al) > 1:
                        nd["seq"]["over"] = al
                    steps.append(nd)
                    tok = nd["id"]
                else:
                    m = FRAME_RE.match(s)
                    if m:
                        kind, label = m.group(1), m.group(2) or ""
                        fr = {"id": ids.make("frame:%s:%s-%s" % (kind, slug(label), h6(kind, label))), "kind": kind}
                        if label:
                            fr["label"] = label
                        frames.append(fr)
                        stack.append(fr)
                        pend_first.append(("frame", fr))
                        tok = fr["id"]
                    else:
                        m = BRANCH_RE.match(s)
                        if m:
                            if not stack or BRANCH_OF.get(stack[-1]["kind"]) != m.group(1):
                                raise MermaidError("line %d: %r does not belong to the open frame" % (ln, s))
                            if pend_first:
                                raise MermaidError("line %d: an empty frame part cannot be kept" % ln)
                            fr = stack[-1]
                            br = {"kind": m.group(1)}
                            if m.group(2):
                                br["label"] = m.group(2)
                            fr.setdefault("branches", []).append(br)
                            pend_first.append(("branch", br))
                            tok = "else:%s:%d" % (fr["id"], len(fr["branches"]))
                        elif s == "end":
                            if not stack:
                                raise MermaidError("line %d: end without an open frame" % ln)
                            if pend_first:
                                raise MermaidError("line %d: an empty frame cannot be kept" % ln)
                            fr = stack.pop()
                            fr["last"] = steps[-1]["id"]
                            tok = "end:" + fr["id"]
                        else:
                            raise MermaidError("line %d: unsupported sequenceDiagram statement %r" % (ln, s))
        if tok is not None and steps and tok == steps[-1]["id"] and pend_first:
            for what, obj in pend_first:
                obj["first"] = tok
            pend_first = []
        if blank:
            blanks.append(tok)
        blank = False
    if stack:
        raise MermaidError("%s %r is never closed" % (stack[-1]["kind"], stack[-1].get("label", "")))
    g["lanes"] = [l for l in lanes.values()]
    g["nodes"] = steps
    for nd in steps:
        nd["evidence"] = evidence()
    for a, b in zip(steps, steps[1:]):
        g["edges"].append({"id": "edge:flow:%s:%s" % (a["id"], b["id"]), "source": a["id"], "target": b["id"], "kind": "flow", "status": "context", "evidence": evidence()})
    g["flows"] = [{"id": "flow:main", "label": display(g["title"], LABEL_MAX["group"]), "status": "context", "lanes": [l["id"] for l in g["lanes"]],
                   "after": [n["id"] for n in steps], "evidence": evidence()}]
    ordered = []
    for fr in frames:
        o = {"id": fr["id"], "kind": fr["kind"]}
        if "label" in fr:
            o["label"] = fr["label"]
        o["first"], o["last"] = fr["first"], fr["last"]
        if fr.get("branches"):
            o["branches"] = fr["branches"]
        ordered.append(o)
    if ordered:
        g["frames"] = ordered
    if blanks:
        g["meta"]["mermaid"]["blanks"] = blanks


# ------------------------------------------------------------------ import: entry
def import_block(block: str, stem: str = "diagram", title: str = "", meta: dict | None = None, view: str | None = None) -> dict:
    pre, header, body = split_block(block)
    typ = detect_type(header)
    view = view or view_for(stem, typ)
    g = new_graph(view, title or stem, meta or {}, typ, header, pre)
    if typ == "flowchart":
        import_flowchart(g, body, view)
    elif typ == "classDiagram":
        import_class(g, body)
    else:
        import_sequence(g, body)
    g["nodes"] = [{k: v for k, v in n.items() if not k.startswith("_")} for n in g["nodes"]]
    return tidy(g)


def tidy(g: dict) -> dict:
    """Stable key order (nodes: id, label, text, kind, status, group, lane, order, shape, stereotype, members, seq, evidence)."""
    keys = ["id", "label", "text", "kind", "status", "group", "lane", "order", "shape", "stereotype", "members", "seq", "evidence"]
    g["nodes"] = [dict(sorted(n.items(), key=lambda kv: keys.index(kv[0]) if kv[0] in keys else 99)) for n in g["nodes"]]
    top = ["schema_version", "view", "title", "meta", "lanes", "groups", "nodes", "edges", "flows", "frames"]
    return dict(sorted(g.items(), key=lambda kv: top.index(kv[0]) if kv[0] in top else 99))


# ------------------------------------------------------------------ render
def blank_set(g: dict) -> set:
    return set(mm_of(g).get("blanks") or [])


def render(g: dict) -> str:
    mm = mm_of(g)
    typ = mm.get("type")
    if typ not in ("flowchart", "classDiagram", "sequenceDiagram"):
        raise MermaidError("graph has no meta.mermaid.type (flowchart, classDiagram or sequenceDiagram)")
    out = list(mm.get("preamble") or []) + [mm.get("header") or typ]
    if typ == "sequenceDiagram":
        out += render_sequence(g)
    else:
        out += render_tree(g, typ)
    return "\n".join(out)


def render_tree(g: dict, typ: str) -> list:
    nodes = {n["id"]: n for n in g["nodes"]}
    groups = {x["id"]: x for x in g.get("groups", [])}
    edges = {e["id"]: e for e in g["edges"]}
    blanks = blank_set(g)
    out: list = []
    depth = 1
    for tok in effective_order(g):
        if tok in blanks and not (tok in nodes and nodes[tok].get("shape") == "implicit"):
            out.append("")
        pad = INDENT * depth
        if tok in groups:
            gr = groups[tok]
            if typ == "classDiagram":
                out.append("%snamespace %s {" % (pad, dec(gr["id"])))
            elif gr["label"] == dec(gr["id"]) and "text" not in gr:
                out.append("%ssubgraph %s" % (pad, dec(gr["id"])))
            else:
                out.append('%ssubgraph %s["%s"]' % (pad, dec(gr["id"]), full(gr)))
            if gr.get("direction"):
                out.append("%sdirection %s" % (pad + INDENT, gr["direction"]))
            depth += 1
        elif tok.startswith("end:") and tok[4:] in groups:
            depth -= 1
            out.append(INDENT * depth + ("}" if typ == "classDiagram" else "end"))
        elif tok in nodes:
            n = nodes[tok]
            if n.get("shape") == "implicit":
                continue
            out += render_node(n, typ, pad)
        elif tok in edges:
            out.append(pad + render_edge(edges[tok], typ))
    return out


def render_node(n: dict, typ: str, pad: str) -> list:
    mid = dec(n["id"])
    if typ == "classDiagram":
        head = "%sclass %s" % (pad, mid)
        if full(n) != mid:
            head += '["%s"]' % full(n)
        body = ([("<<%s>>" % n["stereotype"])] if n.get("stereotype") else []) + list(n.get("members") or [])
        if not body:
            return [head]
        return [head + " {"] + [pad + INDENT + m for m in body] + [pad + "}"]
    shape = n.get("shape", "rect")
    if shape == "bare":
        return [pad + mid]
    op, cl = SHAPES[shape]
    return [pad + '%s%s"%s"%s' % (mid, op, full(n), cl)]


def render_edge(e: dict, typ: str) -> str:
    a, b = dec(e["source"]), dec(e["target"])
    arrow = e.get("arrow") or "-->"
    has = "label" in e
    if typ == "classDiagram":
        return "%s %s %s" % (a, arrow, b) + (" : %s" % full(e) if has else "")
    return ("%s %s|\"%s\"| %s" % (a, arrow, full(e), b)) if has else "%s %s %s" % (a, arrow, b)


def render_sequence(g: dict) -> list:
    nodes = {n["id"]: n for n in g["nodes"]}
    lanes = {l["id"]: l for l in g.get("lanes", [])}
    blanks = blank_set(g)
    flow = (g.get("flows") or [{}])[0]
    steps = [i for i in flow.get("after", []) if i in nodes]
    frames = g.get("frames") or []
    opens: dict = {}
    closes: dict = {}
    branch_at: dict = {}
    for fr in frames:
        opens.setdefault(fr["first"], []).append(fr)
        closes.setdefault(fr["last"], []).append(fr)
        for k, br in enumerate(fr.get("branches") or [], 1):
            branch_at.setdefault(br["first"], []).append((fr, k, br))
    out: list = []
    for l in g.get("lanes", []):
        if l.get("implicit"):
            continue
        if l["id"] in blanks:
            out.append("")
        line = "%s%s %s" % (INDENT, l.get("declare", "participant"), dec(l["id"]))
        if not l.get("bare"):
            line += ' as "%s"' % full(l)
        out.append(line)
    depth = 1
    for sid in steps:
        for fr, k, br in branch_at.get(sid, []):
            depth -= 1
            if "else:%s:%d" % (fr["id"], k) in blanks:
                out.append("")
            out.append(INDENT * depth + br["kind"] + (" " + br["label"] if br.get("label") else ""))
            depth += 1
        for fr in opens.get(sid, []):
            if fr["id"] in blanks:
                out.append("")
            out.append(INDENT * depth + fr["kind"] + (" " + fr["label"] if fr.get("label") else ""))
            depth += 1
        n = nodes[sid]
        if sid in blanks:
            out.append("")
        sq = n.get("seq") or {}
        if n["kind"] == "note":
            anchors = ",".join(dec(x) for x in (sq.get("over") or [n["lane"]]))
            out.append("%sNote %s %s: %s" % (INDENT * depth, sq.get("position", "over"), anchors, full(n)))
        else:
            out.append("%s%s%s%s%s: %s" % (INDENT * depth, dec(n["lane"]), sq.get("arrow", "->>"), sq.get("activate", ""), dec(sq["to"]), full(n)))
        for fr in reversed(closes.get(sid, [])):
            depth -= 1
            if "end:" + fr["id"] in blanks:
                out.append("")
            out.append(INDENT * depth + "end")
    return out


# ------------------------------------------------------------------ Markdown blocks
def scan_md(lines: list) -> list:
    """Blocks of a Markdown file: dicts {marked, start, end, body (fence content lines), json, problem}. start..end is the line slice replaced."""
    blocks = []
    i = 0
    while i < len(lines):
        ln = lines[i]
        m = MARK_RE.match(ln.strip())
        if m or re.match(r"^<!-- generated from ", ln.strip()):
            b = {"marked": True, "start": i, "json": m.group(1) if m else None, "problem": None if m else "malformed marker"}
            j = i + 1
            if j < len(lines) and re.match(r"^```mermaid\s*$", lines[j]):
                k = j + 1
                while k < len(lines) and not re.match(r"^```\s*$", lines[k]):
                    k += 1
                b["body"] = lines[j + 1:k]
                k += 1
                if k < len(lines) and lines[k].strip() == END_MARK:
                    b["end"] = k + 1
                else:
                    b["end"] = k
                    b["problem"] = b["problem"] or "no `%s` after the block" % END_MARK
                i = b["end"]
            else:
                b["body"], b["end"] = [], i + 1
                b["problem"] = b["problem"] or "marker not followed by a ```mermaid fence"
                i += 1
            blocks.append(b)
            continue
        if re.match(r"^```mermaid\s*$", ln):
            k = i + 1
            while k < len(lines) and not re.match(r"^```\s*$", lines[k]):
                k += 1
            blocks.append({"marked": False, "start": i, "end": k + 1, "body": lines[i + 1:k], "json": None, "problem": None})
            i = k + 1
            continue
        i += 1
    return blocks


def generated_lines(json_name: str, text: str) -> list:
    return ["<!-- generated from %s by vr_mermaid.py render; edit the JSON, not this block -->" % json_name, "```mermaid"] + text.split("\n") + ["```", END_MARK]


def md_text_from_json(path: str) -> str:
    """The text of the .md at `path` with the Mermaid body of every marked block replaced by the render of its JSON (the source),
    so a reader that parses Mermaid reads the JSON's diagram; the line count of the file is unchanged when the block is in sync.
    A file with no marked block whose JSON exists is returned as it is (the Mermaid in the file is then the source)."""
    text = read(path)
    lines = text.split("\n")
    d = os.path.dirname(os.path.abspath(path))
    done = False
    for b in sorted(scan_md(lines), key=lambda x: -x["start"]):
        jp = os.path.join(d, b["json"]) if b["marked"] and b["json"] else ""
        if not jp or not os.path.isfile(jp):
            continue
        try:
            want = render(json.loads(read(jp)))
        except (ValueError, KeyError, MermaidError):
            continue
        lines[b["start"]:b["end"]] = generated_lines(b["json"], want)
        done = True
    return "\n".join(lines) if done else text


def read(path: str) -> str:
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def write(path: str, text: str) -> None:
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def dump_json(g: dict) -> str:
    return json.dumps(g, indent=2, ensure_ascii=False) + "\n"


def git_info(path: str) -> dict:
    d = os.path.dirname(os.path.abspath(path))
    info = {"repo": "unknown", "commit": "unknown"}
    git = "/usr/bin/git" if os.path.exists("/usr/bin/git") else "git"
    try:
        top = subprocess.run([git, "-C", d, "rev-parse", "--show-toplevel"], capture_output=True, text=True, timeout=10)
        if top.returncode == 0:
            info["repo"] = os.path.basename(top.stdout.strip())
        head = subprocess.run([git, "-C", d, "rev-parse", "HEAD"], capture_output=True, text=True, timeout=10)
        if head.returncode == 0:
            info["commit"] = head.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return info


def md_title(text: str, stem: str) -> str:
    m = re.search(r"^#\s+(.+?)\s*$", text, re.M)
    return m.group(1) if m else stem


# ------------------------------------------------------------------ commands
def cmd_import(args) -> int:
    path = args.file
    text = read(path)
    lines = text.split("\n")
    blocks = scan_md(lines)
    todo = [(k, b) for k, b in enumerate(blocks, 1) if not b["marked"]]
    d0 = os.path.dirname(os.path.abspath(path))
    if getattr(args, "adopt", False):
        # a marked block someone edited in the .md (e.g. check_diagram_anchors.py --fix):
        # take the block back into its JSON instead of letting `sync` revert the edit
        for k, b in enumerate(blocks, 1):
            jp = os.path.join(d0, b["json"]) if b["marked"] and b["json"] and not b["problem"] else None
            if jp and os.path.exists(jp) and render(json.loads(read(jp))) != normalise("\n".join(b["body"])):
                todo.append((k, b))
        todo.sort(key=lambda kb: kb[0])
    if not todo:
        print("%s: no %s mermaid block" % (path, "unmarked or edited" if getattr(args, "adopt", False) else "unmarked"))
        return 0
    stem = os.path.splitext(os.path.basename(path))[0]
    d = os.path.dirname(os.path.abspath(path))
    meta = git_info(path)
    if args.repo:
        meta["repo"] = args.repo
    if args.commit:
        meta["commit"] = args.commit
    title = md_title(text, stem)
    failed = 0
    plan = []
    for k, b in todo:
        name = b["json"] if b["marked"] else ("%s.json" % stem if len(blocks) == 1 else "%s.%d.json" % (stem, k))
        block = "\n".join(b["body"])
        try:
            g = import_block(block, stem, title if len(blocks) == 1 else "%s (%d)" % (title, k), meta, args.view)
            g = json.loads(json.dumps(g))
            back = render(g)
            if back != normalise(block):
                raise MermaidError("render(import(block)) differs from the block: first difference at line %d" % first_diff(back, normalise(block)))
            issues = validate_graph(g)
            if issues:
                raise MermaidError("graph invalid: %s" % issues[:3])
        except MermaidError as exc:
            print("%s block %d: FAILED: %s" % (path, k, exc), file=sys.stderr)
            failed += 1
            continue
        target = os.path.join(d, name)
        if os.path.exists(target) and not args.force and not b["marked"]:
            print("%s block %d: %s exists (use --force to replace it)" % (path, k, name), file=sys.stderr)
            failed += 1
            continue
        plan.append((b, name, target, g, back))
    for b, name, target, g, back in plan:
        print("%s: block at line %d (%s, %d nodes, %d edges) -> %s%s" % (path, b["start"] + 1, g["view"], len(g["nodes"]), len(g["edges"]), name,
                                                                         "" if args.write else " (dry run)"))
    if failed:
        print("%s: nothing written (%d block(s) failed)" % (path, failed), file=sys.stderr)
        return 1
    if args.write:
        for b, name, target, g, back in plan:
            write(target, dump_json(g))
        new = list(lines)
        for b, name, target, g, back in sorted(plan, key=lambda p: -p[0]["start"]):
            new[b["start"]:b["end"]] = generated_lines(name, back)
        write(path, "\n".join(new))
    return 0


def first_diff(a: str, b: str) -> int:
    la, lb = a.split("\n"), b.split("\n")
    for i in range(max(len(la), len(lb))):
        if i >= len(la) or i >= len(lb) or la[i] != lb[i]:
            return i + 1
    return 0


def validate_graph(g: dict) -> list:
    import vr_graph
    return vr_graph.validate(g, check_budget=False)


def cmd_render(args) -> int:
    try:
        print(render(json.loads(read(args.file))))
    except (OSError, ValueError) as exc:
        print("cannot render %s: %s" % (args.file, exc), file=sys.stderr)
        return 2
    return 0


def md_files(d: str) -> list:
    return sorted(os.path.join(d, f) for f in os.listdir(d) if f.endswith(".md"))


def process_dir(d: str, fix: bool) -> list:
    """Problems of every .md of d; with fix=True rewrites the marked blocks that differ."""
    problems: list = []
    for path in md_files(d):
        lines = read(path).split("\n")
        blocks = scan_md(lines)
        new = list(lines)
        changed = False
        for b in sorted(blocks, key=lambda x: -x["start"]):
            where = "%s:%d" % (path, b["start"] + 1)
            if not b["marked"]:
                problems.append("%s: unmarked mermaid block (run: vr_mermaid.py import %s --write)" % (where, path))
                continue
            if b["json"] is None:
                problems.append("%s: %s" % (where, b["problem"]))
                continue
            jp = os.path.join(d, b["json"])
            if not os.path.isfile(jp):
                problems.append("%s: marker names %s, which does not exist" % (where, b["json"]))
                continue
            try:
                want = render(json.loads(read(jp)))
            except (ValueError, KeyError, MermaidError) as exc:
                problems.append("%s: cannot render %s: %s" % (where, b["json"], exc))
                continue
            if b["problem"] or b["body"] != want.split("\n"):
                if fix:
                    new[b["start"]:b["end"]] = generated_lines(b["json"], want)
                    changed = True
                else:
                    problems.append("%s: block differs from %s%s (run: vr_mermaid.py sync %s)" % (
                        where, b["json"], "" if not b["body"] else " at line %d" % first_diff("\n".join(b["body"]), want), d))
                    if b["problem"]:
                        problems.append("%s: %s" % (where, b["problem"]))
        if changed:
            write(path, "\n".join(new))
    return problems


def cmd_sync(args) -> int:
    problems = process_dir(args.dir, fix=True)
    for p in problems:
        print(p, file=sys.stderr)
    return 1 if problems else 0


def cmd_check(args) -> int:
    problems = process_dir(args.dir, fix=False)
    for p in problems:
        print(p)
    if problems:
        print("%d problem(s)" % len(problems))
        return 1
    print("ok: every mermaid block is generated from its JSON")
    return 0


# ------------------------------------------------------------------ corpus
def corpus_check(d: str, say) -> tuple:
    total = ok = 0
    failed: list = []
    for path in md_files(d):
        stem = os.path.splitext(os.path.basename(path))[0]
        blocks = [b for b in scan_md(read(path).split("\n")) if not b["marked"]]
        for k, b in enumerate(blocks, 1):
            total += 1
            block = "\n".join(b["body"])
            try:
                g = import_block(block, stem, stem, {"repo": "corpus", "commit": "0"})
                g = json.loads(json.dumps(g))
                issues = validate_graph(g)
                if issues:
                    raise MermaidError("invalid graph: %s" % issues[:2])
                back = render(g)
                if back != normalise(block):
                    raise MermaidError("round trip differs at line %d" % first_diff(back, normalise(block)))
                ok += 1
            except MermaidError as exc:
                failed.append("%s block %d: %s" % (os.path.basename(path), k, exc))
    say("corpus: %d blocks, %d round-tripped exactly, %d failed" % (total, ok, len(failed)))
    for f in failed:
        say("  FAILED " + f)
    return total, ok, failed


# ------------------------------------------------------------------ selftest
FIX_FLOW = """%% module_graph: demo
flowchart LR
    dev["Developer\\n(a laptop terminal)"]

    subgraph vm["Lima VM: nightwatch\\n(deploy/host/lima/nightwatch.yaml)"]
        direction LR

        subgraph compose["docker compose"]
            direction TB
            nats["nats\\ndeploy/compose/docker-compose.yml:52"]
            pg["postgres"]
        end

        zfs[("ZFS pool")]
        root["composition-root"]
        bare

        root -->|"sqlx::PgPool"| pg
        root -.->|"connects\\n(as its instance id)"| nats
    end

    dev -->|"deploy/host/host.sh up"| vm
    dev --> root
    ghost ==> dev
    root --- pg
    a1("round")
    b1(["stadium"])
    c1{"dia"}
    d1(("circle"))
    e1[["sub"]]
    f1>"asym"]
    a1 --> b1
    c1 -.- d1
    e1 ~~~ f1
"""

FIX_CLASS = """classDiagram
    class RunId {
        +mint()
        +parse(value)
    }
    class Port {
        <<trait>>
        +send(msg)
        -retries: Vec~u8~
    }
    class Mode {
        <<enum>>
        A
        B
    }
    class NodeSelection
    class FileCap["CaptureHeader"]
    RunId --> Port : holds
    RunId ..|> Port
    Port ..> Mode : uses a long label that is longer than forty characters
    Port <|-- Ghost
    class Late {
        +x()
    }
    Late --* RunId

    namespace Net {
        class Wire
    }
"""

FIX_SEQ = """sequenceDiagram
    participant Caller as "boot caller"
    participant Db
    actor User as "someone"
    participant Long as "a participant alias that is longer than forty characters"

    Caller->>Db: select 1
    Note right of Db: crates/a.rs:1 `x`
    Db-->>Caller: row
    Caller-)User: async
    Caller->>+Db: begin
    Db--xCaller: oops
    Note over Caller,Db: both
    Note left of User: l

    alt first \\n case
        Caller->>Db: select 1
        opt inner
            Db-->>Caller: one
        end
    else second
        Caller->>Db: select 1
        Caller->>Db: select 1

        loop every turn
            par left
                Caller-->Db: a
            and right
                Db->Caller: b
            end
        end
    end
    Caller->>Caller: self
    Long->>Db: a message with a very long text that goes well beyond the eighty characters limit of a node label for sure
"""


def _selftest(corpus: str | None) -> int:
    fails: list = []

    def check(name: str, cond: bool, why: str = "") -> None:
        if not cond:
            fails.append("%s %s" % (name, why))

    cases = (("flow", "c2-demo", FIX_FLOW), ("class", "c4-demo", FIX_CLASS), ("seq", "seq-demo", FIX_SEQ))
    graphs = {}
    for name, stem, src in cases:
        try:
            g = import_block(src, stem, "Demo " + name, {"repo": "r", "commit": "c"})
            graphs[name] = g
            g2 = json.loads(json.dumps(g))
            check("roundtrip:" + name, render(g2) == normalise(src), "line %d" % first_diff(render(g2), normalise(src)))
            issues = validate_graph(g2)
            check("valid:" + name, not issues, str(issues[:3]))
            import vr_graph
            check("budget:" + name, not vr_graph.budget(g2)["over"])
            ids = [n["id"] for n in g2["nodes"]] + [e["id"] for e in g2["edges"]] + [x["id"] for x in g2.get("groups", [])]
            check("unique-ids:" + name, len(ids) == len(set(ids)))
            check("no-counter-ids:" + name, not any(re.search(r"(^|:)\d+$", i) for i in ids))
            check("deterministic:" + name, import_block(src, stem, "Demo " + name, {"repo": "r", "commit": "c"}) == g)
        except MermaidError as exc:
            check("import:" + name, False, str(exc))
    if len(graphs) == 3:
        f, c, s = graphs["flow"], graphs["class"], graphs["seq"]
        check("flow:view", f["view"] == "c2" and c["view"] == "c4" and s["view"] == "seq")
        check("flow:preamble", f["meta"]["mermaid"]["preamble"] == ["%% module_graph: demo"])
        check("flow:group-direction", [x.get("direction") for x in f["groups"]] == ["LR", "TB"] and f["groups"][1]["parent"] == "group:vm")
        check("flow:shapes", {n["shape"] for n in f["nodes"] if "shape" in n} >= {"cylinder", "bare", "implicit", "round", "stadium", "diamond", "circle", "subroutine", "asymmetric"})
        check("flow:edge-to-group", any(e["target"] == "group:vm" for e in f["edges"]))
        check("flow:dashed-kind", any(e["arrow"] == "-.->" and e["kind"] == "dashed" for e in f["edges"]))
        check("flow:text-kept", any(n.get("text", "").startswith("Developer\\n") and n["label"] == "Developer (a laptop terminal)" for n in f["nodes"]))
        check("flow:blanks", len(f["meta"]["mermaid"]["blanks"]) >= 3)
        check("flow:order", "order" in f["meta"]["mermaid"])
        check("class:members", next(n for n in c["nodes"] if n["id"] == "class:Port")["members"] == ["+send(msg)", "-retries: Vec~u8~"])
        check("class:stereotype", next(n for n in c["nodes"] if n["id"] == "class:Port")["stereotype"] == "trait")
        check("class:label", next(n for n in c["nodes"] if n["id"] == "class:FileCap")["label"] == "CaptureHeader")
        check("class:arrows", {e["arrow"] for e in c["edges"]} == {"-->", "..|>", "..>", "<|--", "--*"})
        check("class:namespace", c["groups"][0]["kind"] == "namespace" and next(n for n in c["nodes"] if n["id"] == "class:Wire")["group"] == "group:Net")
        check("class:long-edge-text", any(len(e.get("label", "")) <= 40 and e.get("text", "").endswith("forty characters") for e in c["edges"]))
        msgs = [n for n in s["nodes"] if n["kind"] == "message"]
        notes = [n for n in s["nodes"] if n["kind"] == "note"]
        same = [n["id"] for n in msgs if n["label"] == "select 1"]
        check("seq:repeat-suffix", len(same) == 4 and same[1].endswith(".2") and same[3].endswith(".4") and not same[0].endswith(".2"), str(same))
        check("seq:id-shape", all(re.match(r"^msg:[A-Za-z0-9_.-]+:[A-Za-z0-9_.-]+:[a-z0-9-]+-[0-9a-f]{6}(\.\d+)?$", n["id"]) for n in msgs), msgs[0]["id"])
        check("seq:note-over", any(n["seq"].get("over") == ["lane:Caller", "lane:Db"] for n in notes))
        check("seq:lane-of-message", msgs[0]["lane"] == "lane:Caller" and msgs[0]["seq"]["to"] == "lane:Db")
        check("seq:arrows", {n["seq"]["arrow"] for n in msgs} == {"->>", "-->>", "-)", "--x", "-->", "->"})
        check("seq:activate", any(n["seq"].get("activate") == "+" for n in msgs))
        check("seq:flow-order", s["flows"][0]["after"] == [n["id"] for n in s["nodes"]])
        check("seq:frames", [x["kind"] for x in s["frames"]] == ["alt", "opt", "loop", "par"] and s["frames"][0]["branches"][0]["kind"] == "else")
        check("seq:long-lane", any(l.get("text", "").endswith("characters") and len(l["label"]) <= 40 for l in s["lanes"]))
        check("seq:long-message", any(len(n["label"]) == 80 and n["label"].endswith("\u2026") and "text" in n for n in msgs))
        check("seq:actor", any(l.get("declare") == "actor" for l in s["lanes"]))
        bad = json.loads(json.dumps(s))
        bad["frames"][0]["last"] = "msg:nope"
        check("seq:frame-ref-validated", [i["code"] for i in validate_graph(bad)] == ["unknown_frame_step"])
        # editing the JSON changes the render (the JSON is the source)
        s2 = json.loads(json.dumps(s))
        for n in s2["nodes"]:
            if n["label"] == "row":
                n["label"] = "rows"
        check("seq:edit", "Db-->>Caller: rows" in render(s2))
        f2 = json.loads(json.dumps(f))
        f2["nodes"].append({"id": "container:new", "label": "new", "kind": "container", "status": "context", "evidence": evidence()})
        check("flow:added-node-rendered", 'new["new"]' in render(f2))
        f2["nodes"] = [n for n in f2["nodes"] if n["id"] != "container:pg"]
        f2["edges"] = [e for e in f2["edges"] if "container:pg" not in (e["source"], e["target"])]
        check("flow:removed-node-gone", 'pg["postgres"]' not in render(f2) and "-> pg" not in render(f2).replace('"| pg', "-> pg"))
    # unsupported input is an error, never a silent drop
    for name, bad in (("flow-class", "flowchart LR\n    classDef x fill:#f00\n"), ("seq-activate", "sequenceDiagram\n    activate A\n"),
                      ("seq-empty-frame", "sequenceDiagram\n    A->>B: x\n    alt y\n    end\n"), ("header", "pie\n    a: 1\n"),
                      ("class-quote", 'classDiagram\n    A "1" --> "n" B\n'), ("unclosed", "flowchart LR\n    subgraph a\n        x\n")):
        try:
            import_block(bad)
            check("reject:" + name, False, "accepted")
        except MermaidError:
            pass
    # markdown flow in a temp dir
    with tempfile.TemporaryDirectory() as d:
        md = os.path.join(d, "seq-demo.md")
        write(md, "# Demo sequence\n\nProse one.\n\n```mermaid\n%s```\n\nProse two.\n\n```mermaid\nflowchart LR\n    a[\"A\"]\n    b[\"B\"]\n    a --> b\n```\n\nEnd.\n" % FIX_SEQ)
        ns = argparse.Namespace(file=md, write=False, repo="r", commit="c", view=None, force=False)
        check("import:dry-run-writes-nothing", cmd_import(ns) == 0 and sorted(os.listdir(d)) == ["seq-demo.md"])
        ns.write = True
        check("import:write", cmd_import(ns) == 0)
        check("import:json-names", sorted(os.listdir(d)) == ["seq-demo.1.json", "seq-demo.2.json", "seq-demo.md"], str(sorted(os.listdir(d))))
        text = read(md)
        check("import:markers", text.count("<!-- generated from seq-demo.1.json by vr_mermaid.py render; edit the JSON, not this block -->\n```mermaid\n") == 1
              and text.count(END_MARK) == 2 and "Prose one." in text and "Prose two." in text and text.endswith("End.\n"))
        check("check:clean", process_dir(d, fix=False) == [], str(process_dir(d, fix=False)))
        check("import:again-is-noop", cmd_import(ns) == 0 and read(md) == text)
        jp = os.path.join(d, "seq-demo.2.json")
        g2 = json.loads(read(jp))
        g2["nodes"][1]["label"] = "B2"
        write(jp, dump_json(g2))
        p = process_dir(d, fix=False)
        check("check:drift", len(p) == 1 and "seq-demo.2.json" in p[0], str(p))
        check("sync:fixes", process_dir(d, fix=True) == [] and process_dir(d, fix=False) == [] and 'b["B2"]' in read(md))
        write(md, read(md).replace('b["B2"]', 'b["B3"]', 1))
        check("adopt:edited-block-drifts", len(process_dir(d, fix=False)) == 1)
        nsa = argparse.Namespace(file=md, write=True, repo="r", commit="c", view=None, force=False, adopt=True)
        check("adopt:takes-the-md-edit", cmd_import(nsa) == 0 and process_dir(d, fix=False) == [] and "B3" in read(jp) and 'b["B3"]' in read(md))
        write(md, read(md).replace("```mermaid\nflowchart", "```mermaid\nflowchart", 1) + "\n```mermaid\nflowchart LR\n    x --> y\n```\n")
        p = process_dir(d, fix=False)
        check("check:unmarked", any("unmarked" in x for x in p), str(p))
        os.remove(jp)
        p = process_dir(d, fix=False)
        check("check:missing-json", any("does not exist" in x for x in p), str(p))
        write(md, "<!-- generated from nope.json by vr_mermaid.py render; edit the JSON, not this block -->\n```mermaid\nflowchart LR\n```\n<!-- end generated -->\n")
        check("check:missing-json-2", any("nope.json" in x for x in process_dir(d, fix=False)))
        write(md, "<!-- generated from seq-demo.1.json by vr_mermaid.py render; edit the JSON, not this block -->\n```mermaid\nsequenceDiagram\n```\n")
        check("check:no-end-marker", any("end generated" in x for x in process_dir(d, fix=False)))
        # an import that cannot round-trip writes nothing
        write(md, "```mermaid\nflowchart LR\n    a --> b : bad\n```\n")
        ns2 = argparse.Namespace(file=md, write=True, repo="r", commit="c", view=None, force=False)
        before = sorted(os.listdir(d))
        check("import:refuses-bad", cmd_import(ns2) == 1 and sorted(os.listdir(d)) == before)
    # the schema declares the new view values and fields
    schema = json.loads(read(os.path.join(HERE, "..", "references", "visual-review", "graph.schema.json")))
    check("schema:views", {"c1", "c2", "c3", "c4", "seq"} <= set(schema["properties"]["view"]["enum"]))
    check("schema:frames", "frames" in schema["properties"] and "frame" in schema["$defs"])
    if corpus:
        total, ok, failed = corpus_check(corpus, print)
        check("corpus", not failed, "%d of %d failed" % (len(failed), total))
    if fails:
        print("vr_mermaid selftest FAILED:\n  " + "\n  ".join(fails))
        return 1
    print("all vr_mermaid selftests passed" + (" (with corpus)" if corpus else ""))
    return 0


def main(argv: list | None = None) -> int:
    ap = argparse.ArgumentParser(description="Lossless Mermaid <-> change-graph JSON converter")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--corpus", help="with --selftest: round-trip every mermaid block of the .md files in DIR")
    sub = ap.add_subparsers(dest="cmd")
    p = sub.add_parser("import")
    p.add_argument("--adopt", action="store_true", help="also take back marked blocks edited in the .md (they replace their JSON)")
    p.add_argument("file")
    p.add_argument("--write", action="store_true")
    p.add_argument("--repo")
    p.add_argument("--commit")
    p.add_argument("--view")
    p.add_argument("--force", action="store_true")
    p.set_defaults(fn=cmd_import)
    p = sub.add_parser("render")
    p.add_argument("file")
    p.set_defaults(fn=cmd_render)
    for name, fn in (("sync", cmd_sync), ("check", cmd_check)):
        p = sub.add_parser(name)
        p.add_argument("dir")
        p.set_defaults(fn=fn)
    args = ap.parse_args(argv)
    if args.selftest:
        return _selftest(args.corpus)
    if not args.cmd:
        ap.print_usage(sys.stderr)
        return 2
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
