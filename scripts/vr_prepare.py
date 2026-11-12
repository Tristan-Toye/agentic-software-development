#!/usr/bin/env python3
"""Visual review: the mechanical half of the Phase 3 diagram step (VR-05).

    vr_prepare.py REPO DOSSIER-ID --base REV --head REV [--calls fn1,fn2]
                  [--layers 1,2] [--timeout 120] [--scripts-dir DIR]
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
            calls: list, layers: str, timeout: int) -> dict:
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
    py = sys.executable or "python3"

    tree = tempfile.mkdtemp(prefix="vr-before-", dir=work)
    try:
        arc = subprocess.Popen(["git", "archive", base], cwd=repo, stdout=subprocess.PIPE)
        tar = subprocess.run(["tar", "-x", "-C", tree], stdin=arc.stdout, capture_output=True)
        arc.stdout.close()
        if arc.wait() != 0 or tar.returncode != 0:
            raise Failed("archive", "git archive %s failed" % base)
        cmd = [py, extract, "rust", tree, "--layers", layers, "--timeout", str(timeout)]
        if touched:
            cmd += ["--calls", ",".join(touched)]
        cmd += ["-o", os.path.join(work, "before.json")]
        run("extract-before", cmd, timeout=timeout + 600)
    finally:
        shutil.rmtree(tree, ignore_errors=True)

    run("after", [py, diff, "after-from-git", repo, "--base", base, "--head", head,
                  "-o", os.path.join(work, "after.json")])
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
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("repo", nargs="?")
    ap.add_argument("dossier", nargs="?", help="dossier id, e.g. W-145")
    ap.add_argument("--base")
    ap.add_argument("--head")
    ap.add_argument("--calls", default="", help="extra function names, comma separated")
    ap.add_argument("--layers", default="1,2")
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
                          [c for c in args.calls.split(",") if c], args.layers, args.timeout)
    except Failed as exc:
        print(json.dumps({"ok": False, "step": exc.step, "error": exc.message}))
        return exc.code
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
