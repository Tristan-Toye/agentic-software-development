#!/usr/bin/env python3
"""Promote an owner-reviewed L1 flow to a docs/architecture sequence diagram.

    vr_promote.py --dossier-id ID --stem STEM --repo PATH [--preview | --check-approval]
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


def approval(l1_hash: str, review: dict | None, step_ids: list) -> tuple:
    """(removed ids, problems). The owner's approval is tied to the exact L1 file: every promoted step needs an `ok`
    line whose graph_hash is the current hash. A `change` or `question` line on the current hash is unresolved, and
    an `ok` never clears it (any order). A line on an older hash proves nothing about this file: the older versions
    of l1.json are not kept, so there is no way to tell whether the author revised that element, and the current-hash
    approval is required (STALE-APPROVAL). `remove` at any hash drops the step (the scope decision stands)."""
    lines: dict = {}
    for ln in (review or {}).get("lines", []):
        a = ln.get("anchor", {})
        if a.get("diagram") == "L1":
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
            problems.append("UNRESOLVED-CHANGE %s: a %s verdict on the current L1 hash is open (%s)"
                            % (sid, open_[0]["verdict"], (open_[0].get("comment") or "no comment")[:80]))
        elif any(x.get("verdict") == "ok" for x in cur):
            continue
        elif ls:
            problems.append("STALE-APPROVAL %s: the owner's lines are on an older L1 hash; l1.json changed since "
                            "(older versions are not kept): the owner approves the current file again" % sid)
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
        out.append("WARN l1.json has no meta.base_sha: anchors are re-found by their backticked name only; "
                   "an anchor without one keeps its Phase 3 line")
    cache: dict = {}
    for part in graph["nodes"]:  # edges and the flow copy their step's anchors afterwards
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


def run(dossier_id: str, stem: str, repo: str, preview: bool = False, check_only: bool = False) -> int:
    vr = os.path.join(repo, ".discovery", "visual-review", dossier_id)
    l1 = _load(os.path.join(vr, "l1.json"))
    rpath = os.path.join(vr, "review.json")
    review = _load(rpath) if os.path.isfile(rpath) else None
    flows = [f for f in l1.get("flows", []) if f.get("new_stem") == stem]
    if len(flows) != 1:
        print("ERROR expected exactly one flow with new_stem %r in l1.json, found %d" % (stem, len(flows)))
        return 2
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
    check("preview", rc == 0 and os.path.isfile(os.path.join(d, ".discovery", "visual-review", "W-9", "seq-new.seq.json"))
          and not os.path.exists(os.path.join(d, "docs")), out)
    with contextlib.redirect_stdout(io.StringIO()):
        check("noflow:exit2", run("W-9", "seq-other", d) == 2)
    bad = copy.deepcopy(l1)
    del bad["nodes"][0]["call_kind"]
    check("missing-kind", _raises(lambda: promote_graph(bad, "seq-new", set())))
    check("arrow:reply", promote_graph(l1, "seq-new", set())["nodes"][1]["seq"]["arrow"] == "-->>")
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
    ap.add_argument("--check-approval", action="store_true",
                    help="only the approval rule (no writes, no re-anchoring): exit 1 and name the unapproved steps")
    a = ap.parse_args(argv)
    if a.selftest:
        return _selftest()
    if not (a.dossier_id and a.stem):
        ap.error("--dossier-id and --stem are required")
    return run(a.dossier_id, a.stem, os.path.abspath(a.repo), a.preview, a.check_approval)


if __name__ == "__main__":
    sys.exit(main())
