#!/usr/bin/env python3
"""Visual review: c3 agreement (VC-06 of feature 01). Module edges of one crate versus its c3 file.

    vr_c3_agree.py REPO --crate NAME [--c3 PATH] [--threshold 95] [--target TRIPLE] [--json] [--raw]
    vr_c3_agree.py --selftest

The declared side is the c3 Mermaid file `module_graph.py check` guards (default
REPO/docs/architecture/c3-components-<crate>.md; the first-line `%% module_graph: <crate>` marker
is read, a file without it is a usage error). The derived side is the module layer of the same
cargo-modules (pinned 0.27.0) that vr_extract runs for its `modules` layer: `cargo modules
dependencies --lib -p CRATE --no-owns` gives the `uses` edges between modules, which are the
edges c3 draws. vr_extract does not emit those use edges, so this script runs the command itself,
through vr_extract's runner. Nothing is written to REPO.

Agreement is |declared and derived| / |declared or derived| over module-to-module edges, in
percent: a missing edge (derived, not in c3) and an extra edge (in c3, not derived) both lower it.
Exit 0 at or above the threshold (default 95), 1 below it, 2 for a usage or tool error, and also 2
when both sides have no edges (an empty comparison never passes).

PARSING REQUIREMENTS (R1-R6). Each is a scope or analysis choice that makes the two sides describe
the same thing; none of them hides a difference between what the code imports and what c3 draws.
All are off with --raw, which compares the edges exactly as emitted.

  R1 Target. cargo-modules runs with `--target x86_64-unknown-linux-gnu` (override: --target). The
     c3 file is built from every `.rs` file whatever its cfg, and a crate such as harness gates
     whole modules on `target_os = "linux"`; analysing the host (macOS) would drop them and report
     their edges as extra. Linux is the platform the product runs on.
  R2 Features. cargo-modules runs with `--all-features`, for the same reason: c3 reads every file,
     so the analysis must compile every feature-gated module in.
  R3 Tests excluded. cargo-modules runs without `--cfg-test`, as module_graph.py drops
     `#[cfg(test)]` items. One exception is R4.
  R4 Cfg-gated module nodes (a cargo-modules 0.27.0 quirk). A module gated
     `#[cfg(any(test, feature = "x"))]` gets no module node, and so no edges, unless `--cfg-test`
     is on, even with its feature enabled (seen: harness::tools::red_corpus). When a module c3
     declares has no node in the R1-R3 run, this script runs once more with `--cfg-test` and takes
     only the edges that touch such a module. Every other edge of that second run is test-only
     code and is dropped.
  R5 Granularity. c3 draws one node per file; an inline `mod x { .. }` is a module to
     cargo-modules and part of its file to c3 (module_graph.py reads the `use` lines of the whole
     file). A derived module the c3 file does not declare is rolled up to its deepest declared
     ancestor; edges that become self-edges vanish.
  R6 Library target only. c3 holds `::bin::` modules (src/bin/*.rs, separate crates to cargo); the
     derived side is the lib target, so edges touching them are dropped from c3. Reported.
  Unmodelled edges. cargo-modules draws no node for a `const`, a `static` or a macro, so a `use`
     that reaches only those is invisible to it. module_graph.py marks such an edge `-.->` in the
     c3 file. A `-.->` edge the derived side lacks is reported under `unmodelled_edges` and left
     out of the percentage; a `-.->` edge cargo-modules does draw counts as agreed like any other.
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
import vr_mermaid  # noqa: E402

THRESHOLD = 95.0
TARGET = vr_extract.CM_TARGET  # one source of truth with the module layer (vr_extract.CM_FLAGS)
FIXTURE = os.path.join(vr_graph.FIXTURES, "c3-agree")
_NODE = re.compile(r'^\s*([A-Za-z_][\w]*)\["([^"]+)"\]\s*$')
_EDGE = re.compile(r"^\s*([A-Za-z_][\w]*)\s*(-->|-\.->)\s*([A-Za-z_][\w]*)\s*$")
_MARKER = re.compile(r"^%%\s*module_graph:\s*(\S+)\s*$", re.M)
_DOT_USES = re.compile(r'^\s*"([^"]+)"\s*->\s*"([^"]+)"\s*\[label="uses"')
_DOT_MOD = re.compile(r'^\s*"([^"]+)"\s*\[label="[^"]*\bmod\|')
BASE_CMD = ["cargo", "modules", "dependencies", "--lib", "-p", None, "--no-owns", "--no-externs", "--no-sysroot",
            "--no-fns", "--no-traits", "--no-types", vr_extract.CM_ALL_FEATURES]


class AgreeError(Exception):
    """Usage or tool error (exit 2)."""


def crate_ident(name: str) -> str:
    return name.replace("-", "_")


def parse_c3(text: str) -> dict:
    """{crate, nodes, edges {(src, dst)}, soft {(src, dst)} the `-.->` edges} with module labels
    (`crate::a::b`) as ends."""
    m = _MARKER.search(text)
    block = re.search(r"```mermaid\n(.*?)```", text, re.S)
    if not block or not m:
        raise AgreeError("the c3 file has no mermaid block or no `%% module_graph:` marker")
    labels, raw, soft_raw = {}, [], []
    for line in block.group(1).split("\n"):
        n = _NODE.match(line)
        if n:
            labels[n.group(1)] = n.group(2)
            continue
        e = _EDGE.match(line)
        if e:
            raw.append((e.group(1), e.group(3)))
            if e.group(2) == "-.->":
                soft_raw.append((e.group(1), e.group(3)))
    lab = lambda x: labels.get(x, x)  # noqa: E731
    return {"crate": m.group(1), "nodes": set(labels.values()),
            "edges": {(lab(a), lab(b)) for a, b in raw}, "soft": {(lab(a), lab(b)) for a, b in soft_raw}}


def parse_uses(dot: str) -> set:
    """Module `uses` edges of a cargo-modules dependencies dot graph."""
    out = set()
    for line in dot.split("\n"):
        m = _DOT_USES.match(line)
        if m:
            out.add((m.group(1), m.group(2)))
    return out


def parse_mod_nodes(dot: str) -> set:
    """The module nodes of a cargo-modules dependencies dot graph."""
    return {m.group(1) for m in map(_DOT_MOD.match, dot.split("\n")) if m}


def _is_bin(label: str) -> bool:
    return "::bin::" in label


def derive(root: str, crate: str, declared_nodes: set | None = None, runner=None, target: str = TARGET) -> dict:
    """{edges, tools, rerun: [modules taken from the --cfg-test run (R4)]}."""
    runner = runner or vr_extract.default_runner
    ver, why = vr_extract.cargo_modules_version(runner, root)
    if why:
        raise AgreeError("module layer unavailable: %s" % why)
    cmd = [crate if c is None else c for c in BASE_CMD] + (["--target", target] if target else [])
    rc, out, err = runner(cmd, root)
    if rc != 0:
        raise AgreeError("%s failed: %s" % (" ".join(cmd[1:4]), err.strip()[:200]))
    edges = parse_uses(out)
    nodeless = sorted(m for m in (declared_nodes or ()) if not _is_bin(m) and m not in parse_mod_nodes(out))
    rerun = []
    if nodeless:
        rc, out2, err = runner(cmd + ["--cfg-test"], root)
        if rc != 0:
            raise AgreeError("%s --cfg-test failed: %s" % (" ".join(cmd[1:4]), err.strip()[:200]))
        # only a module whose node appears in the second run was dropped by the quirk
        rerun = [m for m in nodeless if m in parse_mod_nodes(out2)]
        second = parse_uses(out2)
        touching = {e for e in second if e[0] in rerun or e[1] in rerun}
        # a hidden module's edges land on its nearest visible ancestor in the first run; the second
        # run names the module, so that stand-in edge is replaced, not kept beside it
        stand_in = {(s, m.rsplit("::", 1)[0]) for s, m in touching if m in rerun and "::" in m
                    and (s, m.rsplit("::", 1)[0]) not in second}
        edges = (edges - stand_in) | touching
    return {"edges": edges, "tools": [{"name": "cargo-modules", "version": ver}], "rerun": rerun}


def roll_up(label: str, nodes: set) -> str:
    """`label` if c3 declares it, else its deepest declared ancestor, else `label` (R5)."""
    parts = label.split("::")
    for n in range(len(parts), 0, -1):
        cand = "::".join(parts[:n])
        if cand in nodes:
            return cand
    return label


def normalise(derived: set, declared: set, nodes: set | None = None, raw: bool = False) -> tuple:
    """R5 and R6, off with --raw. Returns (derived, declared, rolled_up_count, bin_count)."""
    if raw:
        return derived, declared, 0, 0
    if nodes:
        d = {(roll_up(a, nodes), roll_up(b, nodes)) for a, b in derived}
        d = {e for e in d if e[0] != e[1]}
        rolled = len({e for e in derived if e not in d})
    else:
        d, rolled = set(derived), 0
    c = {e for e in declared if not _is_bin(e[0]) and not _is_bin(e[1])}
    return d, c, rolled, len(declared) - len(c)


def compare(derived: set, declared: set, threshold: float = THRESHOLD, raw: bool = False,
            nodes: set | None = None, soft: set | None = None) -> dict:
    derived, declared, rolled, bins = normalise(derived, declared, nodes, raw)
    unmodelled = {e for e in (declared - derived) if e in (soft or ())}
    union = derived | (declared - unmodelled)
    if not union:
        raise AgreeError("no module edges on either side: nothing to compare")
    agree = len(derived & declared)
    pct = 100.0 * agree / len(union)
    fmt = lambda s: sorted("%s -> %s" % e for e in s)  # noqa: E731
    return {"agreement": round(pct, 2), "agreed": agree, "derived": len(derived), "declared": len(declared),
            "missing_edges": fmt(derived - declared), "extra_edges": fmt(declared - derived - unmodelled),
            "unmodelled_edges": fmt(unmodelled),
            "threshold": threshold, "ok": pct >= threshold, "raw": raw,
            "rolled_up_edges": rolled, "ignored_bin_edges": bins}


def find_c3(repo: str, crate: str) -> str:
    p = os.path.join(repo, "docs", "architecture", "c3-components-%s.md" % crate)
    if os.path.isfile(p):
        return p
    raise AgreeError("no c3 file %s (pass --c3 PATH)" % p)


def check(repo: str, crate: str, c3: str | None = None, threshold: float = THRESHOLD, runner=None, raw: bool = False,
          target: str = TARGET) -> dict:
    repo = os.path.abspath(repo)
    c3 = c3 or find_c3(repo, crate)
    try:
        with open(c3, encoding="utf-8") as fh:
            declared = parse_c3(vr_mermaid.md_text_from_json(c3))
    except OSError as exc:
        raise AgreeError(str(exc))
    if declared["crate"] != crate_ident(crate):
        raise AgreeError("the c3 file is for %s, not %s" % (declared["crate"], crate_ident(crate)))
    d = derive(repo, crate, declared["nodes"], runner, target)
    r = compare(d["edges"], declared["edges"], threshold, raw, declared["nodes"], declared["soft"])
    r.update(crate=crate, c3=os.path.relpath(c3, repo) if c3.startswith(repo) else c3, tools=d["tools"],
             target=target, cfg_test_modules=d["rerun"])
    return r


def render(r: dict) -> str:
    out = ["crate: %s  c3: %s  target: %s" % (r["crate"], r["c3"], r["target"]),
           "derived edges: %d  c3 edges: %d  agreed: %d" % (r["derived"], r["declared"], r["agreed"]),
           "agreement: %.2f%% (threshold %.0f%%)" % (r["agreement"], r["threshold"]),
           "normalised: %d derived edges rolled up to a declared module, %d c3 bin edges dropped%s" % (
               r["rolled_up_edges"], r["ignored_bin_edges"], " (--raw)" if r["raw"] else ""),
           "cfg-test run for: " + (", ".join(r["cfg_test_modules"]) or "none"),
           "unmodelled edges (-.-> in c3, const/static/macro only, not counted): " + (", ".join(r["unmodelled_edges"]) or "none"),
           "missing edges (derived, not in c3): " + (", ".join(r["missing_edges"]) or "none"),
           "extra edges (in c3, not derived): " + (", ".join(r["extra_edges"]) or "none"),
           "OK" if r["ok"] else "DISAGREE"]
    return "\n".join(out)


# ----------------------------------------------------------------- selftest

def _fx(name: str) -> str:
    with open(os.path.join(FIXTURE, name), encoding="utf-8") as fh:
        return fh.read()


def fixture_runner(dot: str, cm_version: str = "0.27.0", cfgtest_dot: str | None = None, log: list | None = None):
    """A runner that answers `--version` and returns `dot` for the dependencies command, or
    `cfgtest_dot` when the command carries `--cfg-test`. Every command is appended to `log`."""
    def run(cmd, cwd, timeout=None):
        if log is not None:
            log.append(list(cmd))
        if cmd[:2] == ["cargo", "modules"] and "--version" in cmd:
            return 0, "cargo-modules %s\n" % cm_version, ""
        if cmd[:3] == ["cargo", "modules", "dependencies"]:
            return 0, (cfgtest_dot if "--cfg-test" in cmd and cfgtest_dot is not None else dot), ""
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
        # frozen real pair 2, BEFORE the generator fix: protocol disagrees (c3 drew `ops -> schema`,
        # cargo-modules resolved the re-exported item to `ops -> schema::ids`)
        put("protocol", _fx("c3-components-protocol.md"))
        run = fixture_runner(_fx("dependencies-protocol.dot"))
        r = check(tmp, "protocol", runner=run)
        ok("old protocol below 95", not r["ok"] and r["agreement"] < 90, r)
        ok("old protocol missing/extra", "protocol::ops -> protocol::schema::ids" in r["missing_edges"]
           and "protocol::ops -> protocol::schema" in r["extra_edges"], r)
        # frozen real pairs AFTER the generator fix: all exact
        results: dict = {}
        for crate, nodeless in (("protocol", False), ("platform", False), ("composition-root", False), ("harness", True)):
            put(crate, _fx("c3-components-%s-after.md" % crate))
            log: list = []
            run = fixture_runner(_fx("dependencies-%s-after.dot" % crate),
                                 cfgtest_dot=_fx("dependencies-harness-after-cfgtest.dot") if nodeless else None, log=log)
            r = results[crate] = check(tmp, crate, runner=run)
            ok("%s after agrees exactly" % crate, r["ok"] and r["agreement"] == 100.0 and not r["missing_edges"]
               and not r["extra_edges"], r)
            dep = [c for c in log if c[:3] == ["cargo", "modules", "dependencies"]][0]
            ok("%s R1/R2 flags" % crate, "--all-features" in dep and dep[dep.index("--target") + 1] == TARGET
               and "--cfg-test" not in dep and "--lib" in dep, dep)
            ok("%s flags come from vr_extract.CM_FLAGS" % crate, all(f in dep for f in vr_extract.CM_FLAGS) and TARGET == vr_extract.CM_TARGET, dep)
        r = results["harness"]
        ok("harness: unmodelled edges are reported, not counted", len(r["unmodelled_edges"]) >= 10, r["unmodelled_edges"])
        ok("harness: R4 cfg-test run for red_corpus", r["cfg_test_modules"] == ["harness::tools::red_corpus"], r["cfg_test_modules"])
        ok("harness: R5 roll-up (journal::tier_text)", r["rolled_up_edges"] >= 1, r)
        ok("composition-root: R6 bin edges dropped, R5 none needed", results["composition-root"]["ignored_bin_edges"] >= 5, results["composition-root"])
        ok("harness: second run happens", sum("--cfg-test" in c for c in log) == 1, log)
        r_nr = check(tmp, "harness", runner=fixture_runner(_fx("dependencies-harness-after.dot")))
        ok("harness without the cfg-test run loses red_corpus", not r_nr["ok"] or r_nr["agreement"] < 100.0
           and any("red_corpus" in e for e in r_nr["extra_edges"]), r_nr)
        node_dot = '    "c::a" [label="pub mod|c::a"]; // "mod" node\n    "c::a" -> "c::b" [label="uses", color="#7f7f7f"]; // "uses"\n'
        derive(tmp, "c", {"c::a", "c::bin::x"}, fixture_runner(node_dot, log=(log := [])))
        ok("no --cfg-test run when every module has a node", log and not any("--cfg-test" in c for c in log), log)
        ok("raw skips roll-up and bin", check(tmp, "harness", raw=True, runner=fixture_runner(
            _fx("dependencies-harness-after.dot"), cfgtest_dot=_fx("dependencies-harness-after-cfgtest.dot")))["agreement"] < 100.0)
        ok("target override", check(tmp, "protocol", target="aarch64-unknown-linux-gnu", runner=fixture_runner(
            _fx("dependencies-protocol-after.dot"), log=(log := [])))["target"] == "aarch64-unknown-linux-gnu"
           and ["--target", "aarch64-unknown-linux-gnu"] == [c for c in log if "--lib" in c][0][-2:])

        # rules, one by one, on synthetic edges
        nodes = {"c", "c::a", "c::b", "c::b::deep", "c::bin::x"}
        ok("roll_up to deepest declared", roll_up("c::b::deep::inline::more", nodes) == "c::b::deep"
           and roll_up("c::a::tests", nodes) == "c::a" and roll_up("other::m", nodes) == "other::m")
        d, c_, rolled, bins = normalise({("c::a::tests", "c::b"), ("c::a::tests", "c::a"), ("c::a", "c::b")},
                                        {("c::a", "c::b"), ("c::bin::x", "c::a")}, nodes)
        ok("normalise rolls up, drops self edges, drops bin", d == {("c::a", "c::b")} and c_ == {("c::a", "c::b")}
           and rolled == 2 and bins == 1, (d, c_, rolled, bins))
        agree_soft = compare({("c::a", "c::b")}, {("c::a", "c::b")}, nodes=nodes, soft={("c::a", "c::b")})
        ok("a soft edge cargo-modules draws still agrees", agree_soft["agreement"] == 100.0
           and not agree_soft["unmodelled_edges"], agree_soft)
        only_soft = compare({("c::a", "c::b")}, {("c::a", "c::b"), ("c::b", "c::a")}, nodes=nodes, soft={("c::b", "c::a")})
        ok("a soft edge it lacks is reported, not counted", only_soft["agreement"] == 100.0
           and only_soft["unmodelled_edges"] == ["c::b -> c::a"], only_soft)
        solid = compare({("c::a", "c::b")}, {("c::a", "c::b"), ("c::b", "c::a")}, nodes=nodes)
        ok("a solid edge it lacks lowers agreement", solid["agreement"] == 50.0 and solid["extra_edges"] == ["c::b -> c::a"], solid)
        ok("parse_c3 reads -.-> as soft", parse_c3("```mermaid\n%% module_graph: c\ngraph LR\n    a[\"c::a\"]\n    b[\"c::b\"]\n"
                                                   "    a -.-> b\n    b --> a\n```\n")["soft"] == {("c::a", "c::b")})
        ok("parse_mod_nodes", parse_mod_nodes('    "c::a" [label="pub mod|c::a", fillcolor="#81c169"]; // "mod" node\n'
                                              '    "c::a::T" [label="pub struct|T"]; // "struct" node\n') == {"c::a"})

        # mutations over a c3 rebuilt from the derived edges: exact, one short, many short, one extra
        run = fixture_runner(_fx("dependencies-protocol-after.dot"))
        edges = sorted(parse_uses(_fx("dependencies-protocol-after.dot")))
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
        # JSON path: the JSON (source) is read, not the stale Mermaid block of the .md generated from it
        jtxt = c3_of(edges).split("```mermaid\n", 1)[1].split("```", 1)[0].rstrip("\n")
        jg = vr_mermaid.import_block(jtxt, "c3-components-protocol", "c3")
        jg["edges"].pop(0)
        with open(os.path.join(arch, "c3-components-protocol.json"), "w") as fh:
            fh.write(vr_mermaid.dump_json(jg))
        put("protocol", "\n".join(["# c3", ""] + vr_mermaid.generated_lines("c3-components-protocol.json", jtxt) + [""]))
        rj = check(tmp, "protocol", runner=run)
        ok("JSON read, not the stale block", len(rj["missing_edges"]) == 1, rj)
        os.remove(os.path.join(arch, "c3-components-protocol.json"))
        put("protocol", c3_of(edges))
        lines = c3_of(edges).split("\n")
        # errors: wrong crate, no marker, empty edges, wrong tool version
        for name, fn in (
            ("wrong crate", lambda: check(tmp, "graph-runtime", c3=path, runner=run)),
            ("no c3", lambda: check(tmp, "test-support", runner=run)),
            ("empty both", lambda: compare(set(), set())),
            ("bad version", lambda: check(tmp, "protocol", runner=fixture_runner(_fx("dependencies-protocol-after.dot"), "0.1.0"))),
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
            ok("main exit 2", main([tmp, "--crate", "test-support"], runner=run) == 2)
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
    ap.add_argument("--target", default=TARGET, help="target triple cargo-modules analyses for (default %(default)s, R1)")
    ap.add_argument("--raw", action="store_true", help="no roll-up and no bin filter (R5, R6): compare every edge as emitted")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args(argv)
    if a.selftest:
        return _selftest()
    if not a.repo or not a.crate:
        ap.print_usage(sys.stderr)
        return 2
    try:
        r = check(a.repo, a.crate, a.c3, a.threshold, runner, a.raw, a.target)
    except AgreeError as exc:
        print("vr_c3_agree: %s" % exc, file=sys.stderr)
        return 2
    print(json.dumps(r, indent=1) if a.json else render(r))
    return 0 if r["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
