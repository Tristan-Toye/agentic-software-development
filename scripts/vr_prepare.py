#!/usr/bin/env python3
"""Visual review: the mechanical half of the Phase 3 diagram step (VR-05).

    vr_prepare.py REPO DOSSIER-ID --base REV --head REV [--calls fn1,fn2]
                  [--layers 1,2] [--crates a,b|all] [--timeout 120] [--scripts-dir DIR]
    vr_prepare.py --selftest

The orchestrator runs this before it spawns `diagram-author`. It does the work
no model should do:

  1. finds the functions the contract touches: every `fn NAME` on a changed or
     added line of `git diff BASE HEAD -- '*.rs'` (hunk headers included), the
     `fn NAME` words in the dossier's `## Contract` section when
     `.discovery/dossiers/<ID>.md` exists, and the names given with --calls;
  2. extracts the "before" graph from a `git archive` of BASE (vr_extract.py,
     with the call hierarchy for those functions);
  3. gets the "after" graph with `vr_diff.py after-from-git` and joins the two
     with `vr_diff.py merge`;
  4. computes the call-hierarchy candidates: for each touched function its
     callers and callees, with evidence, taken from the merged graph.

Layer 2 (cargo-modules, the slow part) runs only for the crates the change
touches: those whose `Cargo.toml` or a file under `src/` differs between BASE
and HEAD (`changed_crates`). The same set is used for the before and the after
side, so the merge compares like with like; layer 1 stays the whole workspace.
`--crates a,b` sets the list by hand, `--crates all` turns the limit off.

Everything lands in `.discovery/visual-review/<ID>/work/` of REPO:
`before.json`, `after.json`, `merged.json`, `candidates.json`, `summary.json`
and a `.gitignore` holding `*`, so the work directory never reaches git (the
agent's three outputs, `l0.json`, `l1.json` and `l2.json` one level up, do).
A compact JSON summary goes to stdout. Exit codes: 0 prepared; 1 a step failed
(the JSON says which); 2 bad arguments or a script that is not there.

`vr_extract.py` and `vr_diff.py` are shelled out to, from --scripts-dir (default:
beside this file). No third-party imports.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_REL = os.path.join(".discovery", "visual-review")
FN_RE = re.compile(r"\bfn\s+([A-Za-z_][A-Za-z0-9_]*)")
HUNK_RE = re.compile(r"^@@ [^@]*@@\s*(.*)$")
ID_RE = re.compile(r"^[A-Za-z0-9_.-]+$")


class Failed(Exception):
    def __init__(self, step: str, message: str, code: int = 1):
        super().__init__(message)
        self.step, self.message, self.code = step, message, code


def run(step: str, cmd: list, cwd: str | None = None, timeout: int = 900) -> str:
    try:
        r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise Failed(step, "%s: %s" % (cmd[0], exc))
    if r.returncode != 0:
        raise Failed(step, (r.stderr or r.stdout).strip()[-600:] or "exit %d" % r.returncode)
    return r.stdout


def touched_functions(repo: str, base: str, head: str, dossier_id: str, extra: list) -> list:
    """Function names the contract touches, sorted, without duplicates."""
    names = set(extra)
    diff = run("diff", ["git", "diff", "-U0", "--no-color", base, head, "--", "*.rs"], cwd=repo)
    for line in diff.splitlines():
        m = HUNK_RE.match(line)
        if m:
            names.update(FN_RE.findall(m.group(1)))
        elif line.startswith("+") and not line.startswith("+++"):
            names.update(FN_RE.findall(line))
    dossier = os.path.join(repo, ".discovery", "dossiers", dossier_id + ".md")
    if os.path.isfile(dossier):
        text = open(dossier, encoding="utf-8").read()
        m = re.search(r"^## Contract\b.*?(?=^## |\Z)", text, re.S | re.M)
        if m:
            names.update(FN_RE.findall(m.group(0)))
    return sorted(names)


def _package_name(text: str) -> str | None:
    """The `[package] name` of a Cargo.toml text, or None (a workspace-only manifest)."""
    try:
        import tomllib
        pkg = tomllib.loads(text).get("package")
        return pkg.get("name") if isinstance(pkg, dict) and isinstance(pkg.get("name"), str) else None
    except Exception:
        m = re.search(r'^\[package\]\s*$(.*?)(?=^\[|\Z)', text, re.S | re.M)
        n = re.search(r'^name\s*=\s*"([^"]+)"', m.group(1), re.M) if m else None
        return n.group(1) if n else None


def changed_crates(repo: str, base: str, head: str) -> list:
    """Names of the crates the change touches, sorted: a changed `Cargo.toml` or a changed file
    under `src/` maps to the package whose manifest sits in the nearest directory above it
    (looked up at HEAD, then at BASE for a removed crate). A workspace-only manifest maps to none."""
    files = run("diff", ["git", "diff", "--name-only", "--no-renames", base, head], cwd=repo).splitlines()
    cache: dict = {}

    def pkg_at(d: str):
        if d not in cache:
            name = None
            for rev in (head, base):
                r = subprocess.run(["git", "show", "%s:%s" % (rev, (d + "/" if d else "") + "Cargo.toml")],
                                   cwd=repo, capture_output=True, text=True)
                if r.returncode == 0:
                    name = _package_name(r.stdout)
                    break
            cache[d] = name
        return cache[d]

    out = set()
    for f in files:
        parts = f.split("/")
        base_name = parts[-1]
        if base_name == "Cargo.toml":
            d = "/".join(parts[:-1])
        elif "src" in parts[:-1]:
            d = "/".join(parts[:parts.index("src")])
        else:
            continue
        # the nearest manifest at or above d (a src file may sit in a nested dir without one)
        while True:
            n = pkg_at(d)
            if n:
                out.add(n)
                break
            if not d:
                break
            d = d.rsplit("/", 1)[0] if "/" in d else ""
    return sorted(out)


def _short(node: dict) -> dict:
    return {k: node.get(k) for k in ("id", "label", "kind", "status", "evidence") if k in node}


def candidates(merged: dict, touched: list) -> dict:
    """Callers and callees of every touched function in the merged graph."""
    nodes = {n["id"]: n for n in merged.get("nodes", [])}
    want = set(touched)
    hit = {i: n for i, n in nodes.items() if i.startswith("fn:") and i.rsplit("::", 1)[-1] in want}
    out = {"touched": [], "calls": [], "missing": sorted(
        w for w in want if not any(i.rsplit("::", 1)[-1] == w for i in hit))}
    for i in sorted(hit):
        out["touched"].append(_short(hit[i]))
    seen = set()
    for e in merged.get("edges", []):
        if "call" not in str(e.get("kind", "")):
            continue
        s, t = e.get("source"), e.get("target")
        if s in hit or t in hit:
            key = (s, t)
            if key in seen:
                continue
            seen.add(key)
            out["calls"].append({
                "caller": _short(nodes[s]) if s in nodes else {"id": s},
                "callee": _short(nodes[t]) if t in nodes else {"id": t},
                "touches": s if s in hit else t,
                "status": e.get("status"),
                "resolution": e.get("resolution"),
                "evidence": e.get("evidence", []),
            })
    out["calls"].sort(key=lambda c: (c["caller"]["id"], c["callee"]["id"]))
    return out


def prepare(repo: str, dossier_id: str, base: str, head: str, scripts: str,
            calls: list, layers: str, timeout: int, crates: list | None | str = "derive") -> dict:
    repo = os.path.abspath(repo)
    extract = os.path.join(scripts, "vr_extract.py")
    diff = os.path.join(scripts, "vr_diff.py")
    for p in (extract, diff):
        if not os.path.isfile(p):
            raise Failed("scripts", "%s is not there (VR-02 ships vr_diff.py)" % p, 2)
    out_dir = os.path.join(repo, OUT_REL, dossier_id)
    work = os.path.join(out_dir, "work")
    os.makedirs(work, exist_ok=True)
    with open(os.path.join(work, ".gitignore"), "w", encoding="utf-8") as fh:
        fh.write("*\n")
    for rev in (base, head):
        run("rev", ["git", "rev-parse", "--verify", "--quiet", rev + "^{commit}"], cwd=repo)

    touched = touched_functions(repo, base, head, dossier_id, calls)
    if crates == "derive":
        crates = changed_crates(repo, base, head)
    t_start = time.time()
    py = sys.executable or "python3"

    tree = tempfile.mkdtemp(prefix="vr-before-", dir=work)
    try:
        arc = subprocess.Popen(["git", "archive", base], cwd=repo, stdout=subprocess.PIPE)
        assert arc.stdout is not None
        tar = subprocess.run(["tar", "-x", "-C", tree], stdin=arc.stdout, capture_output=True)
        arc.stdout.close()
        if arc.wait() != 0 or tar.returncode != 0:
            raise Failed("archive", "git archive %s failed" % base)
        cmd = [py, extract, "rust", tree, "--layers", layers, "--timeout", str(timeout)]
        if crates is not None:
            cmd += ["--crates", ",".join(crates)]
        if touched:
            cmd += ["--calls", ",".join(touched)]
        cmd += ["-o", os.path.join(work, "before.json")]
        run("extract-before", cmd, timeout=timeout + 600)
    finally:
        shutil.rmtree(tree, ignore_errors=True)

    t_before = time.time() - t_start
    after_cmd = [py, diff, "after-from-git", repo, "--base", base, "--head", head, "-o", os.path.join(work, "after.json")]
    if crates is not None:
        after_cmd += ["--crates", ",".join(crates)]
    run("after", after_cmd)
    run("merge", [py, diff, "merge", "--before", os.path.join(work, "before.json"),
                  "--after", os.path.join(work, "after.json"),
                  "-o", os.path.join(work, "merged.json")])

    with open(os.path.join(work, "merged.json"), encoding="utf-8") as fh:
        merged = json.load(fh)
    cand = candidates(merged, touched)
    with open(os.path.join(work, "candidates.json"), "w", encoding="utf-8") as fh:
        json.dump(cand, fh, indent=2, sort_keys=True)
        fh.write("\n")

    counts = {}
    for n in merged.get("nodes", []):
        counts[n.get("status", "?")] = counts.get(n.get("status", "?"), 0) + 1
    summary = {
        "ok": True, "dossier": dossier_id, "base": base, "head": head,
        "out_dir": os.path.join(OUT_REL, dossier_id), "work_dir": os.path.join(OUT_REL, dossier_id, "work"),
        "expected_outputs": [os.path.join(OUT_REL, dossier_id, n) for n in ("l0.json", "l1.json", "l2.json")],
        "touched_functions": touched, "touched_found": len(cand["touched"]),
        "touched_missing": cand["missing"], "call_candidates": len(cand["calls"]),
        "nodes": len(merged.get("nodes", [])), "edges": len(merged.get("edges", [])),
        "node_status": counts,
        "no_call_hierarchy": not cand["calls"],
        "crates": crates if crates is not None else "all",
        "seconds": {"before": round(t_before, 1), "total": round(time.time() - t_start, 1)},
    }
    with open(os.path.join(work, "summary.json"), "w", encoding="utf-8") as fh:
        json.dump(summary, fh, indent=2, sort_keys=True)
        fh.write("\n")
    return summary


# ------------------------------------------------------------------ selftest

_FAKE_EXTRACT = '''
import json, sys
a = sys.argv
out = a[a.index("-o") + 1]
open(out + ".argv", "w").write(" ".join(a))
calls = a[a.index("--calls") + 1].split(",") if "--calls" in a else []
nodes = [{"id": "fn:demo::lib::" + c, "label": c, "kind": "function", "status": "context",
          "evidence": [{"tier": "source", "path": "src/lib.rs", "line": 1}]} for c in calls]
nodes.append({"id": "fn:demo::lib::main", "label": "main", "kind": "function", "status": "context",
              "evidence": [{"tier": "source", "path": "src/lib.rs", "line": 1}]})
edges = [{"id": "edge:calls:main:" + c, "source": "fn:demo::lib::main", "target": "fn:demo::lib::" + c,
          "kind": "calls", "status": "context", "resolution": "static", "evidence": []} for c in calls]
json.dump({"schema_version": "1.0.0", "view": "full", "title": "before", "meta": {}, "nodes": nodes, "edges": edges},
          open(out, "w"))
'''

_FAKE_DIFF = '''
import json, sys
a = sys.argv
out = a[a.index("-o") + 1]
open(out + ".argv", "w").write(" ".join(a))
if a[1] == "after-from-git":
    g = {"schema_version": "1.0.0", "view": "full", "title": "after", "meta": {}, "nodes": [], "edges": []}
else:
    g = json.load(open(a[a.index("--before") + 1]))
    for n in g["nodes"]:
        n["status"] = "changed"
json.dump(g, open(out, "w"))
'''


def _git(cwd, *args):
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=cwd,
                   check=True, capture_output=True)


def selftest() -> int:
    ok = True

    def check(name, cond):
        nonlocal ok
        print(("ok   " if cond else "FAIL ") + name)
        ok &= bool(cond)

    with tempfile.TemporaryDirectory() as td:
        repo = os.path.join(td, "repo")
        scripts = os.path.join(td, "scripts")
        os.makedirs(os.path.join(repo, "src"))
        os.makedirs(scripts)
        _git(repo, "init", "-q")
        lib = os.path.join(repo, "src", "lib.rs")
        open(lib, "w").write("pub fn old_one() {}\n")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "base")
        base = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True).stdout.strip()
        open(lib, "a").write("pub fn flush_all(x: u8) -> u8 { x }\nimpl A { fn drain(&self) {} }\n")
        _git(repo, "commit", "-q", "-am", "head")

        names = touched_functions(repo, base, "HEAD", "W-1", ["extra"])
        check("touched: added fns and --calls names", {"drain", "extra", "flush_all"} <= set(names))

        # 1. the scripts are missing: a clear, typed refusal
        try:
            prepare(repo, "W-1", base, "HEAD", scripts, [], "1", 5)
            check("missing vr_diff.py is refused", False)
        except Failed as exc:
            check("missing vr_diff.py is refused (exit 2, step scripts)", exc.code == 2 and exc.step == "scripts")

        # 2. fake modules: the whole chain
        open(os.path.join(scripts, "vr_extract.py"), "w").write(_FAKE_EXTRACT)
        open(os.path.join(scripts, "vr_diff.py"), "w").write(_FAKE_DIFF)
        s = prepare(repo, "W-1", base, "HEAD", scripts, [], "1", 5)
        work = os.path.join(repo, OUT_REL, "W-1", "work")
        for f in ("before.json", "after.json", "merged.json", "candidates.json", "summary.json", ".gitignore"):
            check("work dir holds " + f, os.path.isfile(os.path.join(work, f)))
        check("no before-tree left behind", not [d for d in os.listdir(work) if d.startswith("vr-before-")])
        check("summary: touched functions named", "flush_all" in s["touched_functions"] and "drain" in s["touched_functions"])
        cand = json.load(open(os.path.join(work, "candidates.json")))
        check("candidates: a caller edge per touched fn", len(cand["calls"]) >= 2 and
              all(c["caller"]["id"] == "fn:demo::lib::main" for c in cand["calls"] if c["touches"] != c["caller"]["id"]))
        check("summary: expected outputs named", s["expected_outputs"][0].endswith("W-1/l0.json"))
        st = subprocess.run(["git", "status", "--porcelain", OUT_REL], cwd=repo, capture_output=True, text=True).stdout
        check("git sees only the work/.gitignore, not the work files", "merged.json" not in st and "before.json" not in st)

        # 2b. the layer-2 crate set: derived from the changed files, same on both sides
        wsr = os.path.join(td, "ws")
        for rel, txt in (("Cargo.toml", '[workspace]\nmembers = ["crates/*"]\n'),
                         ("crates/alpha/Cargo.toml", '[package]\nname = "alpha"\n'),
                         ("crates/alpha/src/lib.rs", "pub fn a() {}\n"),
                         ("crates/alpha/src/deep/mod.rs", "pub fn d() {}\n"),
                         ("crates/beta/Cargo.toml", '[package]\nname = "beta-two"\n'),
                         ("crates/beta/src/lib.rs", "pub fn b() {}\n"),
                         ("crates/beta/tests/t.rs", "fn t() {}\n"),
                         ("crates/gamma/Cargo.toml", '[package]\nname = "gamma"\n'),
                         ("crates/gamma/src/lib.rs", "pub fn g() {}\n"),
                         ("docs/x.md", "x\n")):
            os.makedirs(os.path.dirname(os.path.join(wsr, rel)), exist_ok=True)
            open(os.path.join(wsr, rel), "w").write(txt)
        _git(wsr, "init", "-q")
        _git(wsr, "add", "-A")
        _git(wsr, "commit", "-q", "-m", "base")
        b2 = subprocess.run(["git", "rev-parse", "HEAD"], cwd=wsr, capture_output=True, text=True).stdout.strip()
        open(os.path.join(wsr, "crates/alpha/src/deep/mod.rs"), "a").write("pub fn d2() {}\n")   # src file, nested
        open(os.path.join(wsr, "crates/beta/Cargo.toml"), "a").write("# touched\n")              # manifest
        os.makedirs(os.path.join(wsr, "crates/gamma/tests"), exist_ok=True)
        open(os.path.join(wsr, "crates/gamma/tests/x.rs"), "w").write("fn x() {}\n")             # tests only
        open(os.path.join(wsr, "Cargo.toml"), "a").write("# workspace manifest\n")           # no package
        open(os.path.join(wsr, "docs/x.md"), "a").write("y\n")
        _git(wsr, "add", "-A")
        _git(wsr, "commit", "-q", "-m", "head")
        check("changed_crates: manifest and nested src map to their crate, tests/docs/workspace manifest do not",
              changed_crates(wsr, b2, "HEAD") == ["alpha", "beta-two"])
        open(os.path.join(scripts, "vr_extract.py"), "w").write(_FAKE_EXTRACT)
        s2 = prepare(wsr, "W-2", b2, "HEAD", scripts, [], "1,2", 5)
        w2 = os.path.join(wsr, OUT_REL, "W-2", "work")
        ab = open(os.path.join(w2, "before.json.argv")).read()
        aa = open(os.path.join(w2, "after.json.argv")).read()
        check("prepare: before and after get the same derived --crates", "--crates alpha,beta-two" in ab and "--crates alpha,beta-two" in aa)
        check("summary: names the crate set", s2["crates"] == ["alpha", "beta-two"])
        prepare(wsr, "W-3", b2, "HEAD", scripts, [], "1,2", 5, crates=None)
        check("prepare: crates=None (--crates all) passes no limit",
              "--crates" not in open(os.path.join(wsr, OUT_REL, "W-3", "work", "before.json.argv")).read())
        prepare(wsr, "W-4", b2, "HEAD", scripts, [], "1,2", 5, crates=["gamma"])
        check("prepare: an explicit list wins", "--crates gamma" in open(os.path.join(wsr, OUT_REL, "W-4", "work", "after.json.argv")).read())

        # 3. a bad revision fails with a step name
        try:
            prepare(repo, "W-1", "nope", "HEAD", scripts, [], "1", 5)
            check("bad revision is refused", False)
        except Failed as exc:
            check("bad revision is refused", exc.step == "rev")

        # 4. a failing extractor
        open(os.path.join(scripts, "vr_extract.py"), "w").write("import sys; sys.exit(3)\n")
        try:
            prepare(repo, "W-1", base, "HEAD", scripts, [], "1", 5)
            check("failing extractor is refused", False)
        except Failed as exc:
            check("failing extractor is refused", exc.step == "extract-before")

    print("selftest:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("repo", nargs="?")
    ap.add_argument("dossier", nargs="?", help="dossier id, e.g. W-145")
    ap.add_argument("--base")
    ap.add_argument("--head")
    ap.add_argument("--calls", default="", help="extra function names, comma separated")
    ap.add_argument("--layers", default="1,2")
    ap.add_argument("--crates", default="derive",
                    help="layer 2 only for these crates: a comma list, `all` for no limit; default: derived from the changed files")
    ap.add_argument("--timeout", type=int, default=120)
    ap.add_argument("--scripts-dir", default=HERE)
    args = ap.parse_args(argv)
    if args.selftest:
        return selftest()
    if not (args.repo and args.dossier and args.base and args.head):
        ap.error("REPO, DOSSIER-ID, --base and --head are required")
    if not ID_RE.match(args.dossier):
        ap.error("dossier id may hold only letters, digits, . _ -")
    try:
        summary = prepare(args.repo, args.dossier, args.base, args.head, args.scripts_dir,
                          [c for c in args.calls.split(",") if c], args.layers, args.timeout,
                          crates=("derive" if args.crates == "derive" else None if args.crates == "all"
                                  else [c.strip() for c in args.crates.split(",") if c.strip()]))
    except Failed as exc:
        print(json.dumps({"ok": False, "step": exc.step, "error": exc.message}))
        return exc.code
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
