#!/usr/bin/env python3
"""Visual review: c2 drift. Cargo workspace members versus the c2 containers diagram.

    vr_c2_drift.py REPO [--c2 PATH] [--ignore NAME ...] [--json] [--metrics PATH | --no-metrics]
    vr_c2_drift.py --selftest

The declared side is the c2 Mermaid file (default: found under REPO/docs, first match of
`c2*.md` that holds a mermaid block). The derived side is the crate layer of vr_extract
(`cargo metadata`): the members and their normal dependency edges. Reported:

  missing_members   members with no container in c2                      (absent)
  extra_containers  containers in c2 that are not a member               (diverge)
  title_count       the number word in the c2 title against the member count
  missing_edges     normal dependencies between members with no solid edge in c2
  extra_edges       solid edges in c2 that are not a normal dependency
  stale_anchors     edge anchors `crates/X/Cargo.toml:LINE` whose line does not hold
                    the dependency (comment, other key, past the end), with the actual line

`workspace-hack` (a build helper) is ignored by default; --ignore adds names. Nothing is
written to REPO (VC-08); the counters go to the metrics log (vr_metrics), default
REPO/.discovery/visual-review/metrics.jsonl. Exit 0 clean, 1 drift, 2 usage or parse error.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import vr_extract  # noqa: E402
import vr_graph  # noqa: E402,F401
import vr_mermaid  # noqa: E402
import vr_metrics  # noqa: E402

DEFAULT_IGNORE = ("workspace-hack",)
WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
         "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15}
_NODE = re.compile(r'^\s*([A-Za-z_][\w-]*)\["([^"]+)"\]\s*$')
_EDGE = re.compile(r'^\s*([A-Za-z_][\w-]*)\s*(-->|-\.->)\s*\|"([^"]*)"\|\s*([A-Za-z_][\w-]*)\s*$')
_ANCHOR = re.compile(r"((?:[\w.-]+/)*Cargo\.toml):(\d+)")


class DriftError(Exception):
    """Usage or parse error (exit 2)."""


def find_c2(repo: str) -> str:
    for rel in ("docs/architecture/c2-containers.md",):
        if os.path.isfile(os.path.join(repo, rel)):
            return os.path.join(repo, rel)
    hits = sorted(glob.glob(os.path.join(repo, "docs", "**", "c2*.md"), recursive=True))
    for h in hits:
        with open(h, encoding="utf-8", errors="replace") as fh:
            if "```mermaid" in fh.read():
                return h
    raise DriftError("no c2 file found under %s/docs (pass --c2 PATH)" % repo)


def parse_c2(text: str) -> dict:
    """{title, containers {id: label}, edges [{source, target, dev, anchor_path, anchor_line, line}]}."""
    block = re.search(r"```mermaid\n(.*?)```", text, re.S)
    if not block:
        raise DriftError("no mermaid block in the c2 file")
    start = text[:block.start(1)].count("\n") + 1
    containers, edges = {}, []
    for i, raw in enumerate(block.group(1).split("\n")):
        m = _NODE.match(raw)
        if m:
            containers[m.group(1)] = m.group(2)
            continue
        m = _EDGE.match(raw)
        if m:
            a = _ANCHOR.search(m.group(3))
            edges.append({"source": m.group(1), "target": m.group(4), "dev": m.group(2) == "-.->",
                          "anchor_path": a.group(1) if a else None, "anchor_line": int(a.group(2)) if a else None,
                          "line": start + i})
    title = next((x[2:].strip() for x in text.split("\n") if x.startswith("# ")), "")
    return {"title": title, "containers": containers, "edges": edges}


def _dep_header(s: str, target: str, dev: bool):
    return re.match(r"^\[(?:target\.[^\]]+\.)?%sdependencies\.%s\]" % ("dev-" if dev else "", re.escape(target)), s)


def dep_lines(lines: list, target: str, dev: bool) -> list:
    """Lines (1-based) of the dependency `target` in a manifest's [dependencies] (or [dev-dependencies])."""
    sec, out = "", []
    keys = {target, target.replace("-", "_")}
    for i, raw in enumerate(lines, 1):
        s = raw.strip()
        if s.startswith("["):
            sec = s
            if _dep_header(s, target, dev) or any(_dep_header(s, k, dev) for k in keys):
                out.append(i)
            continue
        want = ("dev-dependencies" if dev else "dependencies")
        if re.match(r"^\[(?:target\.[^\]]+\.)?%s\]" % want, sec) and any(re.match(r"^%s\s*(=|\.)" % re.escape(k), s) for k in keys):
            out.append(i)
    return out


def anchor_holds(lines: list, line: int, target: str, dev: bool) -> bool:
    return line in dep_lines(lines, target, dev)


def derive(root: str, runner=None) -> dict:
    """The derived side: members and normal edges from vr_extract's crate layer."""
    g = vr_extract.extract_rust(root, layers=(1,), runner=runner)
    members = {n["label"]: n["evidence"][0] for n in g["nodes"] if n["kind"] == "crate"}
    edges = {(e["source"].split(":", 1)[1], e["target"].split(":", 1)[1]): e["evidence"][0] for e in g["edges"] if e["kind"] == "depends"}
    return {"members": members, "edges": edges, "commit": g["meta"]["commit"], "tools": g["meta"]["tools"]}


def compare(derived: dict, declared: dict, root: str, ignore=DEFAULT_IGNORE) -> dict:
    ign = set(ignore)
    members = {k for k in derived["members"] if k not in ign}
    labels = declared["containers"]
    cont = {v for v in labels.values() if v not in ign}
    r: dict = {"members": sorted(members), "containers": sorted(cont),
         "missing_members": sorted(members - cont), "extra_containers": sorted(cont - members)}
    word = next((w for w in re.findall(r"[a-z]+", declared["title"].lower()) if w in WORDS), None)
    r["title_count"] = WORDS[word] if word else None
    r["title_count_matches"] = (WORDS[word] == len(members)) if word else None
    solid = {}
    for e in declared["edges"]:
        s, t = labels.get(e["source"]), labels.get(e["target"])
        if s is None or t is None or s in ign or t in ign:
            continue
        if not e["dev"]:
            solid[(s, t)] = e
    der = {k: v for k, v in derived["edges"].items() if k[0] not in ign and k[1] not in ign}
    r["missing_edges"] = sorted("%s -> %s" % k for k in der if k not in solid and k[0] in cont and k[1] in cont)
    r["extra_edges"] = sorted("%s -> %s" % k for k in solid if k not in der)
    stale, cache = [], {}
    for e in declared["edges"]:
        if e["anchor_path"] is None:
            continue
        s, t = labels.get(e["source"]), labels.get(e["target"])
        if s is None or t is None or s in ign or t in ign:
            continue
        p = os.path.join(root, e["anchor_path"])
        if p not in cache:
            try:
                with open(p, encoding="utf-8", errors="replace") as fh:
                    cache[p] = fh.read().split("\n")
            except OSError:
                cache[p] = None
        lines = cache[p]
        row = {"edge": "%s -> %s" % (s, t), "dev": e["dev"], "anchor": "%s:%d" % (e["anchor_path"], e["anchor_line"]), "c2_line": e["line"]}
        if lines is None:
            stale.append(dict(row, reason="file gone", actual=None))
        elif not anchor_holds(lines, e["anchor_line"], t, e["dev"]):
            actual = dep_lines(lines, t, e["dev"])
            stale.append(dict(row, reason="line %d does not hold the dependency" % e["anchor_line"], actual=actual[0] if actual else None))
    r["stale_anchors"] = stale
    r["drift"] = bool(r["missing_members"] or r["extra_containers"] or r["title_count_matches"] is False
                      or r["missing_edges"] or r["extra_edges"] or stale)
    r["extraction"] = {"commit": derived["commit"], "tools": derived["tools"]}
    return r


def check(root: str, c2: str | None = None, ignore=DEFAULT_IGNORE, runner=None) -> dict:
    """Run the drift check; writes nothing. Raises DriftError."""
    root = os.path.abspath(root)
    c2 = c2 or find_c2(root)
    try:
        with open(c2, encoding="utf-8") as fh:
            declared = parse_c2(vr_mermaid.md_text_from_json(c2))
        derived = derive(root, runner)
    except OSError as exc:
        raise DriftError(str(exc))
    except vr_extract.ExtractError as exc:
        raise DriftError(str(exc))
    r = compare(derived, declared, root, ignore)
    r["c2"] = os.path.relpath(c2, root) if c2.startswith(root) else c2
    return r


def render(r: dict) -> str:
    out = ["c2 file: %s" % r["c2"], "members: %d  containers in c2: %d" % (len(r["members"]), len(r["containers"])),
           "missing members (absent from c2): " + (", ".join(r["missing_members"]) or "none"),
           "extra containers (not a member): " + (", ".join(r["extra_containers"]) or "none")]
    if r["title_count"] is not None:
        out.append("title says %d; members are %d: %s" % (r["title_count"], len(r["members"]), "match" if r["title_count_matches"] else "MISMATCH"))
    out.append("missing edges: " + (", ".join(r["missing_edges"]) or "none"))
    out.append("extra edges: " + (", ".join(r["extra_edges"]) or "none"))
    out.append("stale anchors: %d" % len(r["stale_anchors"]))
    for a in r["stale_anchors"]:
        out.append("  %s (%s edge %s, c2 line %d): %s; actual line %s" % (a["anchor"], "dev" if a["dev"] else "normal", a["edge"], a["c2_line"], a["reason"], a["actual"] or "not found"))
    out.append("DRIFT" if r["drift"] else "OK")
    return "\n".join(out)


def counters(r: dict) -> dict:
    return {"members": len(r["members"]), "missing_members": len(r["missing_members"]), "extra_containers": len(r["extra_containers"]),
            "missing_edges": len(r["missing_edges"]), "extra_edges": len(r["extra_edges"]),
            "stale_anchors": len(r["stale_anchors"]), "drift": r["drift"]}


# ----------------------------------------------------------------- selftest

FIXTURE = os.path.join(vr_graph.FIXTURES, "ch-drift")


def fixture_runner(root: str):
    """Fake cargo: serves the frozen metadata of the 2026-11-12 config-harness workspace."""
    with open(os.path.join(FIXTURE, "metadata.json"), encoding="utf-8") as fh:
        meta = json.loads(fh.read().replace("@ROOT@", root))
    return vr_extract.make_runner(meta, [])


def fixed_c2(root: str, runner) -> str:
    """The c2 text after the fix: every member, every normal edge, right anchors, right title."""
    d = derive(root, runner)
    names = sorted(k for k in d["members"] if k not in DEFAULT_IGNORE)
    ids = {n: re.sub(r"\W", "", n) for n in names}
    words = {v: k for k, v in WORDS.items()}
    lines = ["# c2 - Containers: the %s crates" % words[len(names)], "", "```mermaid", "flowchart LR"]
    lines += ['    %s["%s"]' % (ids[n], n) for n in names]
    lines.append("")
    for (s, t), ev in sorted(d["edges"].items()):
        if s in ids and t in ids:
            lines.append('    %s -->|"%s:%d"| %s' % (ids[s], ev["path"], ev["line"], ids[t]))
    lines += ["```", ""]
    return "\n".join(lines)


def _selftest() -> int:
    fails = []

    def ok(name, cond, detail: object = ""):
        if not cond:
            fails.append("%s %s" % (name, detail))

    with tempfile.TemporaryDirectory() as tmp:
        # the frozen fixture tree is copied so the root path matches the metadata
        import shutil
        shutil.copytree(FIXTURE, os.path.join(tmp, "repo"))
        repo = os.path.join(tmp, "repo")
        os.remove(os.path.join(repo, "metadata.json"))
        runner = fixture_runner(os.path.realpath(repo))
        before = {}
        for dp, _, fs in os.walk(repo):
            for f in fs:
                p = os.path.join(dp, f)
                before[p] = open(p, "rb").read()
        r = check(repo, runner=runner)
        # VC-01 (S1)
        ok("VC-01 fault", "fault" in r["missing_members"], r["missing_members"])
        ok("VC-01 step-timing", "step-timing" in r["missing_members"], r["missing_members"])
        ok("VC-01 sandbox-tool", "sandbox-tool" in r["missing_members"])
        ok("workspace-hack ignored", "workspace-hack" not in r["missing_members"])
        ok("title mismatch", r["title_count"] == 6 and r["title_count_matches"] is False, r["title_count"])
        ok("drift flagged", r["drift"] is True)
        # stale anchors: the three D0 found (and every other, now) (harness:63, graph-runtime:9, composition-root:27)
        sa = {a["anchor"]: a for a in r["stale_anchors"]}
        for a in ("crates/harness/Cargo.toml:63", "crates/graph-runtime/Cargo.toml:9", "crates/composition-root/Cargo.toml:27"):
            ok("stale anchor " + a, a in sa, sorted(sa))
        ok("actual line given", sa.get("crates/harness/Cargo.toml:63", {}).get("actual") == 82, sa.get("crates/harness/Cargo.toml:63"))
        ok("all 15 anchors of the frozen c2 are stale", len(sa) == 15, len(sa))  # the manifests grew since the c2 was written
        ok("dev anchor checked", sa.get("crates/protocol/Cargo.toml:46", {}).get("actual") == 45, sa.get("crates/protocol/Cargo.toml:46"))
        ok("extra none", r["extra_containers"] == [])
        # VC-08: nothing written
        after = {}
        for dp, _, fs in os.walk(repo):
            for f in fs:
                p = os.path.join(dp, f)
                after[p] = open(p, "rb").read()
        ok("VC-08 no file changed", before == after)
        # CLI exit codes + render
        ok("render names stale line", "actual line 82" in render(r))
        # VC-02 (S1 fixed): the fixed c2 exits zero
        fixed = fixed_c2(repo, runner)
        c2f = os.path.join(repo, "docs", "architecture", "c2-containers.md")
        with open(c2f, "w") as fh:
            fh.write(fixed)
        r2 = check(repo, runner=runner)
        ok("VC-02 clean", not r2["drift"], render(r2))
        # JSON path: the c2 JSON (source) beats the Mermaid block of the .md, which is only the generated copy
        jtxt = fixed.split("```mermaid\n", 1)[1].split("```", 1)[0].rstrip("\n")
        g = vr_mermaid.import_block(jtxt, "c2-containers", "c2")
        g["nodes"].append({"id": "node:zz", "label": "zz", "kind": "node", "status": "context", "evidence": vr_mermaid.evidence()})
        with open(os.path.join(repo, "docs", "architecture", "c2-containers.json"), "w") as fh:
            fh.write(vr_mermaid.dump_json(g))
        with open(c2f, "w") as fh:  # the .md block is the old clean one, marked as generated from the JSON
            fh.write("\n".join(["# c2 - Containers: the six crates", ""] + vr_mermaid.generated_lines("c2-containers.json", jtxt) + [""]))
        rj = check(repo, runner=runner)
        ok("JSON read, not the stale block", rj["extra_containers"] == ["zz"] and rj["drift"], rj["extra_containers"])
        os.remove(os.path.join(repo, "docs", "architecture", "c2-containers.json"))
        with open(c2f, "w") as fh:
            fh.write(fixed)
        # a comment-line anchor and an extra container are caught
        with open(c2f, "w") as fh:
            fh.write(fixed.replace("```mermaid\nflowchart LR\n", '```mermaid\nflowchart LR\n    zz["zz"]\n'))
        r3 = check(repo, runner=runner)
        ok("extra container", r3["extra_containers"] == ["zz"] and r3["drift"], r3["extra_containers"])
        with open(c2f, "w") as fh:
            fh.write("\n".join(x for x in fixed.split("\n") if "crates/composition-root" not in x))
        r4 = check(repo, runner=runner)
        ok("missing edge", any(e.startswith("composition-root -> ") for e in r4["missing_edges"]) and r4["drift"], r4["missing_edges"])
        # discovery and errors
        ok("find_c2", find_c2(repo).endswith("c2-containers.md"))
        try:
            find_c2(tmp)
            ok("no c2 raises", False)
        except DriftError:
            pass
        try:
            parse_c2("# no diagram")
            ok("no block raises", False)
        except DriftError:
            pass
        # metrics counters shape
        ok("counters", counters(r)["missing_members"] == 3 and counters(r)["drift"] is True, counters(r))
        # main() exit codes with the injected runner
        import contextlib
        import io
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            ok("main exit 1", main([repo, "--c2", c2f, "--no-metrics"], runner=runner) == 1)
            with open(c2f, "w") as fh:
                fh.write(fixed)
            ok("main exit 0", main([repo, "--no-metrics"], runner=runner) == 0)
            ok("main exit 2", main([tmp, "--no-metrics"], runner=runner) == 2)
    if fails:
        print("vr_c2_drift selftest FAILED:\n  " + "\n  ".join(fails))
        return 1
    print("vr_c2_drift selftest ok")
    return 0


def main(argv=None, runner=None) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    ap.add_argument("repo", nargs="?")
    ap.add_argument("--c2", help="c2 file (default: found under REPO/docs)")
    ap.add_argument("--ignore", nargs="*", default=[], help="more crate names to skip (workspace-hack is always skipped)")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--metrics", help="metrics log path (default REPO/.discovery/visual-review/metrics.jsonl)")
    ap.add_argument("--no-metrics", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args(argv)
    if a.selftest:
        return _selftest()
    if not a.repo:
        ap.print_usage(sys.stderr)
        return 2
    t0 = time.time()
    try:
        r = check(a.repo, a.c2, tuple(DEFAULT_IGNORE) + tuple(a.ignore), runner)
    except DriftError as exc:
        print("vr_c2_drift: %s" % exc, file=sys.stderr)
        return 2
    secs = time.time() - t0
    print(json.dumps(r, indent=1) if a.json else render(r))
    if not a.no_metrics:
        vr_metrics.log(os.path.abspath(a.repo), "vr_c2_drift", "check", dict(counters(r), seconds=round(secs, 2)), a.metrics)
    return 1 if r["drift"] else 0


if __name__ == "__main__":
    sys.exit(main())
