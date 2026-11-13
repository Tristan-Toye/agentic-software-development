#!/usr/bin/env python3
"""Visual review: c3 agreement (VC-06 of feature 01). Module edges of one crate versus its c3 file.

    vr_c3_agree.py REPO --crate NAME [--c3 PATH] [--threshold 95] [--json]
    vr_c3_agree.py --selftest

The declared side is the c3 Mermaid file `module_graph.py check` guards (default
REPO/docs/architecture/c3-components-<crate>.md; the first-line `%% module_graph: <crate>` marker
is read, a file without it is a usage error). The derived side is the module layer of the same
cargo-modules (pinned 0.27.0) that vr_extract runs for its `modules` layer: its `structure`
command gives the module tree (vr_extract keeps that as `contains` edges), its `dependencies`
command gives the `uses` edges between modules, which are the edges c3 draws. vr_extract does not
emit those use edges today, so this script runs `cargo modules dependencies --lib -p CRATE
--no-owns` itself, through vr_extract's runner. Tests are excluded (cargo-modules default), as
module_graph.py excludes `#[cfg(test)]`.

Two scope rules (off with --raw) are in `normalise`. Agreement is |declared and derived| / |declared or derived| over module-to-module edges, in
percent: a missing edge and an extra edge both lower it. Reported: agreement, the missing edges
(derived, not in c3) and the extra edges (in c3, not derived). Exit 0 at or above the threshold
(default 95), 1 below it, 2 for a usage or tool error, and also 2 when both sides have no edges
(an empty comparison never passes). Nothing is written to REPO.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import vr_extract  # noqa: E402
import vr_graph  # noqa: E402

THRESHOLD = 95.0
FIXTURE = os.path.join(vr_graph.FIXTURES, "c3-agree")
_NODE = re.compile(r'^\s*([A-Za-z_][\w]*)\["([^"]+)"\]\s*$')
_EDGE = re.compile(r"^\s*([A-Za-z_][\w]*)\s*-->\s*([A-Za-z_][\w]*)\s*$")
_MARKER = re.compile(r"^%%\s*module_graph:\s*(\S+)\s*$", re.M)
_DOT_USES = re.compile(r'^\s*"([^"]+)"\s*->\s*"([^"]+)"\s*\[label="uses"')


class AgreeError(Exception):
    """Usage or tool error (exit 2)."""


def crate_ident(name: str) -> str:
    return name.replace("-", "_")


def parse_c3(text: str) -> dict:
    """{crate, edges {(src, dst)}} with module labels (`crate::a::b`) as ends."""
    m = _MARKER.search(text)
    block = re.search(r"```mermaid\n(.*?)```", text, re.S)
    if not block or not m:
        raise AgreeError("the c3 file has no mermaid block or no `%% module_graph:` marker")
    labels, raw = {}, []
    for line in block.group(1).split("\n"):
        n = _NODE.match(line)
        if n:
            labels[n.group(1)] = n.group(2)
            continue
        e = _EDGE.match(line)
        if e:
            raw.append((e.group(1), e.group(2)))
    edges = {(labels.get(a, a), labels.get(b, b)) for a, b in raw}
    return {"crate": m.group(1), "nodes": set(labels.values()), "edges": edges}


def parse_uses(dot: str) -> set:
    """Module `uses` edges of a cargo-modules dependencies dot graph."""
    out = set()
    for line in dot.split("\n"):
        m = _DOT_USES.match(line)
        if m:
            out.add((m.group(1), m.group(2)))
    return out


def derive(root: str, crate: str, runner=None) -> dict:
    runner = runner or vr_extract.default_runner
    ver, why = vr_extract.cargo_modules_version(runner, root)
    if why:
        raise AgreeError("module layer unavailable: %s" % why)
    cmd = ["cargo", "modules", "dependencies", "--lib", "-p", crate, "--no-owns", "--no-externs", "--no-sysroot",
           "--no-fns", "--no-traits", "--no-types"]
    rc, out, err = runner(cmd, root)
    if rc != 0:
        raise AgreeError("%s failed: %s" % (" ".join(cmd[1:4]), err.strip()[:200]))
    return {"edges": parse_uses(out), "tools": [{"name": "cargo-modules", "version": ver}]}


def _ancestor(a: str, b: str) -> bool:
    return b.startswith(a + "::")


def normalise(derived: set, declared: set, raw: bool = False) -> tuple:
    """Two documented scope rules, off with --raw. cargo-modules reports a module re-exporting its
    child (`schema -> schema::ids`, `pub use`) as a use edge; module_graph.py never draws those, so
    ancestor-to-descendant edges are dropped from the derived side. The c3 file may hold `::bin::`
    modules; the derived side here is the lib target, so edges touching them are dropped from c3."""
    if raw:
        return derived, declared, 0, 0
    d = {e for e in derived if not _ancestor(e[0], e[1])}
    c = {e for e in declared if "::bin::" not in e[0] and "::bin::" not in e[1]}
    return d, c, len(derived) - len(d), len(declared) - len(c)


def compare(derived: set, declared: set, threshold: float = THRESHOLD, raw: bool = False) -> dict:
    derived, declared, drop_d, drop_c = normalise(derived, declared, raw)
    union = derived | declared
    if not union:
        raise AgreeError("no module edges on either side: nothing to compare")
    agree = len(derived & declared)
    pct = 100.0 * agree / len(union)
    fmt = lambda s: sorted("%s -> %s" % e for e in s)  # noqa: E731
    return {"agreement": round(pct, 2), "agreed": agree, "derived": len(derived), "declared": len(declared),
            "missing_edges": fmt(derived - declared), "extra_edges": fmt(declared - derived),
            "threshold": threshold, "ok": pct >= threshold, "raw": raw,
            "ignored_reexport_edges": drop_d, "ignored_bin_edges": drop_c}


def find_c3(repo: str, crate: str) -> str:
    p = os.path.join(repo, "docs", "architecture", "c3-components-%s.md" % crate)
    if os.path.isfile(p):
        return p
    raise AgreeError("no c3 file %s (pass --c3 PATH)" % p)


def check(repo: str, crate: str, c3: str | None = None, threshold: float = THRESHOLD, runner=None, raw: bool = False) -> dict:
    repo = os.path.abspath(repo)
    c3 = c3 or find_c3(repo, crate)
    try:
        with open(c3, encoding="utf-8") as fh:
            declared = parse_c3(fh.read())
    except OSError as exc:
        raise AgreeError(str(exc))
    if declared["crate"] != crate_ident(crate):
        raise AgreeError("the c3 file is for %s, not %s" % (declared["crate"], crate_ident(crate)))
    d = derive(repo, crate, runner)
    r = compare(d["edges"], declared["edges"], threshold, raw)
    r.update(crate=crate, c3=os.path.relpath(c3, repo) if c3.startswith(repo) else c3, tools=d["tools"])
    return r


def render(r: dict) -> str:
    out = ["crate: %s  c3: %s" % (r["crate"], r["c3"]),
           "derived edges: %d  c3 edges: %d  agreed: %d" % (r["derived"], r["declared"], r["agreed"]),
           "agreement: %.2f%% (threshold %.0f%%)" % (r["agreement"], r["threshold"]),
           "ignored: %d derived re-export edges, %d c3 bin edges%s" % (r["ignored_reexport_edges"], r["ignored_bin_edges"], " (--raw)" if r["raw"] else ""),
           "missing edges (derived, not in c3): " + (", ".join(r["missing_edges"]) or "none"),
           "extra edges (in c3, not derived): " + (", ".join(r["extra_edges"]) or "none"),
           "OK" if r["ok"] else "DISAGREE"]
    return "\n".join(out)


# ----------------------------------------------------------------- selftest

def _fx(name: str) -> str:
    with open(os.path.join(FIXTURE, name), encoding="utf-8") as fh:
        return fh.read()


def fixture_runner(dot: str, cm_version: str = "0.27.0"):
    def run(cmd, cwd, timeout=None):
        if cmd[:2] == ["cargo", "modules"] and "--version" in cmd:
            return 0, "cargo-modules %s\n" % cm_version, ""
        if cmd[:3] == ["cargo", "modules", "dependencies"]:
            return 0, dot, ""
        return 0, "cargo-modules %s\n" % cm_version, ""
    return run


def _selftest() -> int:
    import contextlib
    import io
    fails = []

    def ok(name, cond, detail: object = ""):
        if not cond:
            fails.append("%s %s" % (name, detail))

    with tempfile.TemporaryDirectory() as tmp:
        arch = os.path.join(tmp, "docs", "architecture")
        os.makedirs(arch)

        def put(crate, text):
            with open(os.path.join(arch, "c3-components-%s.md" % crate), "w") as fh:
                fh.write(text)

        # frozen real pair 1: graph-runtime agrees fully
        put("graph-runtime", _fx("c3-components-graph-runtime.md"))
        run = fixture_runner(_fx("dependencies-graph-runtime.dot"))
        r = check(tmp, "graph-runtime", runner=run)
        ok("graph-runtime agrees", r["ok"] and r["agreement"] == 100.0 and r["derived"] == 2, r)
        # frozen real pair 2: protocol disagrees (c3 draws `ops -> schema`, cargo-modules `ops -> schema::ids`)
        c3 = _fx("c3-components-protocol.md")
        put("protocol", c3)
        run = fixture_runner(_fx("dependencies-protocol.dot"))
        r = check(tmp, "protocol", runner=run)
        ok("protocol below 95", not r["ok"] and 80 < r["agreement"] < 90, r)
        ok("protocol missing/extra", "protocol::ops -> protocol::schema::ids" in r["missing_edges"]
           and "protocol::ops -> protocol::schema" in r["extra_edges"], r)
        ok("re-export edges ignored", r["ignored_reexport_edges"] == 15, r)
        ok("raw keeps them", check(tmp, "protocol", runner=run, raw=True)["agreement"] < r["agreement"])
        # mutations over a c3 rebuilt from the derived edges: exact, one short, many short, one extra
        edges = sorted(normalise(parse_uses(_fx("dependencies-protocol.dot")), set())[0])
        ident = lambda m: m.replace("::", "__")  # noqa: E731

        def c3_of(es):
            names = sorted({x for e in es for x in e} | {x for e in edges for x in e})
            return "```mermaid\n%% module_graph: protocol\ngraph LR\n" + "\n".join(
                '    %s["%s"]' % (ident(m), m) for m in names) + "\n" + "\n".join(
                "    %s --> %s" % (ident(a), ident(b)) for a, b in es) + "\n```\n"
        put("protocol", c3_of(edges))
        ok("rebuilt c3 agrees", check(tmp, "protocol", runner=run)["agreement"] == 100.0)
        put("protocol", c3_of(edges[1:]))
        r2 = check(tmp, "protocol", runner=run)
        ok("one missing edge", len(r2["missing_edges"]) == 1 and not r2["extra_edges"] and r2["ok"], r2)
        put("protocol", c3_of(edges[6:]))
        ok("six missing edges fail", not check(tmp, "protocol", runner=run)["ok"])
        put("protocol", c3_of(edges + [("protocol::a", "protocol::b")]))
        r4 = check(tmp, "protocol", runner=run)
        ok("extra edge reported", r4["extra_edges"] == ["protocol::a -> protocol::b"], r4)
        path = os.path.join(arch, "c3-components-protocol.md")
        put("protocol", c3_of(edges))
        lines = c3_of(edges).split("\n")
        # errors: wrong crate, no marker, empty edges, wrong tool version
        for name, fn in (
            ("wrong crate", lambda: check(tmp, "graph-runtime", c3=path, runner=run)),
            ("no c3", lambda: check(tmp, "platform", runner=run)),
            ("empty both", lambda: compare(set(), set())),
            ("bad version", lambda: check(tmp, "protocol", runner=fixture_runner(_fx("dependencies-protocol.dot"), "0.1.0"))),
            ("no marker", lambda: parse_c3("```mermaid\ngraph LR\n```\n")),
        ):
            try:
                fn()
                ok(name + " raises", False)
            except AgreeError:
                pass
        ok("percent is a union ratio", compare({("a", "b")}, {("a", "b"), ("c", "d")})["agreement"] == 50.0)
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            ok("main exit 0", main([tmp, "--crate", "protocol"], runner=run) == 0)
            ok("main exit 2", main([tmp, "--crate", "platform"], runner=run) == 2)
            with open(path, "w") as fh:
                fh.write("\n".join(l for l in lines if "-->" not in l))
            ok("main exit 1", main([tmp, "--crate", "protocol"], runner=run) == 1)
    if fails:
        print("vr_c3_agree selftest FAILED:\n  " + "\n  ".join(fails))
        return 1
    print("vr_c3_agree selftest ok")
    return 0


def main(argv=None, runner=None) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    ap.add_argument("repo", nargs="?")
    ap.add_argument("--crate", help="crate directory name, e.g. graph-runtime")
    ap.add_argument("--c3", help="c3 file (default REPO/docs/architecture/c3-components-CRATE.md)")
    ap.add_argument("--threshold", type=float, default=THRESHOLD)
    ap.add_argument("--raw", action="store_true", help="no scope rules: compare every edge as emitted")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args(argv)
    if a.selftest:
        return _selftest()
    if not a.repo or not a.crate:
        ap.print_usage(sys.stderr)
        return 2
    try:
        r = check(a.repo, a.crate, a.c3, a.threshold, runner, a.raw)
    except AgreeError as exc:
        print("vr_c3_agree: %s" % exc, file=sys.stderr)
        return 2
    print(json.dumps(r, indent=1) if a.json else render(r))
    return 0 if r["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
