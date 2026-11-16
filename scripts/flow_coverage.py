#!/usr/bin/env python3
"""Which runtime entry points of a repo have no sequence diagram?

Reads `docs/architecture/seq-*.json` (sequence diagrams of the change-graph
format), finds the runtime entry points in the source with a per-language
signal table (Rust only for now), and reports each as covered, partial or
uncovered. Python standard library only.

Usage:
    flow_coverage.py --repo REPO [--json]
    flow_coverage.py --repo REPO --changed BASE_REF [--json]   # exit 1: uncovered entry in changed files
    flow_coverage.py --selftest

Anchors: a diagram anchors code as `path/file.rs:LINE` (optionally followed by
a backticked identifier) anywhere in its JSON text (node and note labels), plus
the `evidence[].path` fields (file level only).

Coverage of an entry (a function with its span, first line to matching brace):
  covered    some diagram anchors a line inside the span, or anchors the
             function by name (backticked identifier) in the same file;
  partial    some diagram anchors the file, but nothing inside the span;
  uncovered  no diagram anchors the file.

Config `docs/architecture/flow-coverage.json` (optional):
  {"exempt": [{"entry": "<path>::<fn or route>", "why": "..."}],
   "extra_signals": [{"name": "...", "regex": "...", "glob": "crates/**/*.rs"}]}
An exemption without `why` is an error (exit 2). One-shot binaries are not
auto-exempt; the repo exempts them.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

DIAGRAM_DIR = "docs/architecture"
CONFIG_NAME = "flow-coverage.json"
SKIP_DIRS = {"target", ".git", "node_modules", "tests", "benches", "examples", ".discovery", "vendor"}

ANCHOR_RE = re.compile(
    r"(?P<file>(?:[A-Za-z0-9_.@-]+/)*[A-Za-z0-9_.@-]+\.(?:rs|cs))"
    r":(?P<line>\d+)(?:-(?P<end>\d+))?"
    r"(?:\s*`(?P<ident>[^`]+)`)?"
)


# ------------------------------------------------------------------ model

@dataclass
class Entry:
    path: str
    line: int
    end: int          # last line of the span
    kind: str
    name: str
    status: str = "uncovered"
    diagrams: list = field(default_factory=list)
    exempt: str | None = None
    span_start: int = 0

    @property
    def key(self) -> str:
        return f"{self.path}::{self.name}"

    def as_dict(self) -> dict:
        return {"entry": self.key, "path": self.path, "line": self.line, "end": self.end,
                "kind": self.kind, "name": self.name, "status": self.status,
                "diagrams": self.diagrams, "exempt": self.exempt}


class ConfigError(Exception):
    pass


# ------------------------------------------------------------------ globs

def glob_re(pat: str) -> re.Pattern:
    out, i = "", 0
    while i < len(pat):
        if pat.startswith("**/", i):
            out += "(?:.*/)?"
            i += 3
        elif pat.startswith("**", i):
            out += ".*"
            i += 2
        elif pat[i] == "*":
            out += "[^/]*"
            i += 1
        elif pat[i] == "?":
            out += "[^/]"
            i += 1
        else:
            out += re.escape(pat[i])
            i += 1
    return re.compile("^" + out + "$")


# ------------------------------------------------------------------ Rust source helpers

def rust_mask(src: str) -> str:
    """Blank out comments, string and char literals (keeps length and newlines)."""
    out = list(src)
    n, i = len(src), 0

    def blank(a: int, b: int) -> None:
        for k in range(a, b):
            if out[k] != "\n":
                out[k] = " "

    while i < n:
        c = src[i]
        if src.startswith("//", i):
            j = src.find("\n", i)
            j = n if j < 0 else j
            blank(i, j)
            i = j
        elif src.startswith("/*", i):
            depth, j = 1, i + 2
            while j < n and depth:
                if src.startswith("/*", j):
                    depth += 1
                    j += 2
                elif src.startswith("*/", j):
                    depth -= 1
                    j += 2
                else:
                    j += 1
            blank(i, j)
            i = j
        elif c == "r" and re.match(r'r#*"', src[i:i + 12]) and (i == 0 or not (src[i - 1].isalnum() or src[i - 1] == "_")):
            m = re.match(r'r(#*)"', src[i:])
            close = '"' + m.group(1)
            j = src.find(close, i + m.end())
            j = n if j < 0 else j + len(close)
            blank(i + m.end() - 1, j)  # keep the r#" opener visible as r, blank the body
            i = j
        elif c == '"':
            j = i + 1
            while j < n and src[j] != '"':
                j += 2 if src[j] == "\\" else 1
            blank(i, min(j + 1, n))
            i = j + 1
        elif c == "'":
            m = re.match(r"'(?:\\.[^']*|[^\\'])'", src[i:i + 12])
            if m:
                blank(i, i + m.end())
                i += m.end()
            else:
                i += 1  # a lifetime
        else:
            i += 1
    return "".join(out)


def match_close(masked: str, open_idx: int, op: str = "{", cl: str = "}") -> int:
    """Index of the bracket closing the one at open_idx, or -1."""
    depth = 0
    for k in range(open_idx, len(masked)):
        ch = masked[k]
        if ch == op:
            depth += 1
        elif ch == cl:
            depth -= 1
            if depth == 0:
                return k
    return -1


def line_of(src: str, idx: int) -> int:
    return src.count("\n", 0, idx) + 1


def cfg_test_spans(masked: str) -> list:
    """Character spans of `#[cfg(test)] mod x { ... }` blocks."""
    spans = []
    for m in re.finditer(r"#\[cfg\(test\)\]\s*(?:pub\s+)?mod\s+\w+\s*\{", masked):
        end = match_close(masked, m.end() - 1)
        spans.append((m.start(), end if end >= 0 else len(masked)))
    return spans


FN_RE = re.compile(r"\bfn\s+(\w+)")


def fn_span(masked: str, fn_idx: int):
    """(start_idx, end_idx) of the body of the fn whose `fn` keyword is at fn_idx, or None."""
    k, depth = fn_idx, 0
    while k < len(masked):  # skip signature: stop at body `{` outside parens/brackets/angles-free
        ch = masked[k]
        if ch in "([":
            depth += 1
        elif ch in ")]":
            depth -= 1
        elif ch == ";" and depth == 0:
            return None
        elif ch == "{" and depth == 0:
            end = match_close(masked, k)
            return (k, end) if end >= 0 else None
        k += 1
    return None


def enclosing_fn(masked: str, idx: int):
    """(name, fn_line_idx, end_idx) of the innermost fn whose body holds idx."""
    best = None
    for m in FN_RE.finditer(masked, 0, idx):
        sp = fn_span(masked, m.start())
        if sp and sp[0] < idx <= sp[1]:
            best = (m.group(1), m.start(), sp[1])  # later matches are more nested
    return best


def params_of(masked: str, open_paren: int) -> str:
    end = match_close(masked, open_paren, "(", ")")
    return masked[open_paren + 1:end] if end >= 0 else ""


# ------------------------------------------------------------------ Rust signals

def _is_bin(path: str) -> bool:
    return path.endswith("src/main.rs") or "/src/bin/" in "/" + path


def sig_main(path, src, masked):
    if not _is_bin(path):
        return
    for m in re.finditer(r"\bfn\s+main\s*\(", masked):
        sp = fn_span(masked, m.start())
        if sp:
            yield "main", "main", m.start(), sp[1]


def sig_route(path, src, masked):
    if "Router::new" not in masked:
        return
    for m in re.finditer(r"\.route\s*\(", masked):
        enc = enclosing_fn(masked, m.start())
        lit = re.match(r'\s*"([^"]*)"', src[m.end():])
        name = lit.group(1) if lit else f"route@{line_of(src, m.start())}"
        # Span: the enclosing fn (the router is built there); fall back to the call itself.
        end = enc[2] if enc else (match_close(masked, m.end() - 1, "(", ")") or m.end())
        start = enc[1] if enc else m.start()
        yield "http-route", name, m.start(), end, start


def sig_run_shutdown(path, src, masked):
    for m in re.finditer(r"\bpub\s+async\s+fn\s+run\s*(?:<[^>(]*>)?\s*\(", masked):
        if "shutdown" in params_of(masked, m.end() - 1):
            sp = fn_span(masked, m.start())
            if sp:
                yield "background-task", "run", m.start(), sp[1]


def sig_register(path, src, masked):
    for m in re.finditer(r"\bpub\s+fn\s+(register_\w+)\s*(?:<[^>(]*>)?\s*\(", masked):
        if re.search(r"&\s*mut\s+(?:\w+::)*ToolRegistry\b", params_of(masked, m.end() - 1)):
            sp = fn_span(masked, m.start())
            if sp:
                yield "tool-registration", m.group(1), m.start(), sp[1]


def sig_serve(path, src, masked):
    for m in re.finditer(r"\bpub\s+async\s+fn\s+(serve_\w+_requests)\s*(?:<[^>(]*>)?\s*\(", masked):
        sp = fn_span(masked, m.start())
        if sp:
            yield "request-service", m.group(1), m.start(), sp[1]


# The signal table: language -> source file extension(s), file filter, signals.
# A signal yields (kind, name, start_idx, end_idx[, span_start_idx]). To add a
# language, add a row (e.g. C#: `.cs` files, `static void Main`, `app.Map*(`)
# with its own masker and brace matcher.
LANGUAGES = {
    "rust": {
        "exts": (".rs",),
        "mask": rust_mask,
        "cfg_test": cfg_test_spans,
        "signals": [sig_main, sig_route, sig_run_shutdown, sig_register, sig_serve],
        "test_file": re.compile(r"(?:_test|_tests|/tests)\.rs$|(?:^|/)tests\.rs$"),
    },
}


# ------------------------------------------------------------------ discovery

def source_files(repo: Path, exts) -> list:
    out = []
    for root, dirs, files in os.walk(repo):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not d.startswith(".git"))
        for f in sorted(files):
            if f.endswith(exts):
                out.append((Path(root) / f).relative_to(repo).as_posix())
    return out


def load_config(repo: Path) -> dict:
    p = repo / DIAGRAM_DIR / CONFIG_NAME
    if not p.is_file():
        return {"exempt": [], "extra_signals": []}
    try:
        cfg = json.loads(p.read_text(encoding="utf-8"))
    except ValueError as e:
        raise ConfigError(f"{p}: not valid JSON ({e})")
    if not isinstance(cfg, dict):
        raise ConfigError(f"{p}: top level must be an object")
    cfg.setdefault("exempt", [])
    cfg.setdefault("extra_signals", [])
    for i, ex in enumerate(cfg["exempt"]):
        if not isinstance(ex, dict) or not str(ex.get("entry", "")).strip():
            raise ConfigError(f"{p}: exempt[{i}] needs an `entry`")
        if not str(ex.get("why", "")).strip():
            raise ConfigError(f"{p}: exemption {ex.get('entry')!r} has no `why`")
    for i, sg in enumerate(cfg["extra_signals"]):
        if not isinstance(sg, dict) or not sg.get("name") or not sg.get("regex"):
            raise ConfigError(f"{p}: extra_signals[{i}] needs `name` and `regex`")
        try:
            re.compile(sg["regex"])
        except re.error as e:
            raise ConfigError(f"{p}: extra_signals[{i}] regex: {e}")
    return cfg


def find_entries(repo: Path, cfg: dict):
    """-> (entries, languages_found)"""
    entries, found = [], []
    extra = [(s, re.compile(s["regex"]), glob_re(s.get("glob", "**"))) for s in cfg["extra_signals"]]
    for lang, spec in LANGUAGES.items():
        files = source_files(repo, spec["exts"])
        if not files:
            continue
        found.append(lang)
        for rel in files:
            if spec["test_file"].search(rel):
                continue
            src = (repo / rel).read_text(encoding="utf-8", errors="replace")
            masked = spec["mask"](src)
            skip = spec["cfg_test"](masked)

            def in_test(i):
                return any(a <= i <= b for a, b in skip)

            for sig in spec["signals"]:
                for hit in sig(rel, src, masked):
                    kind, name, idx, end = hit[:4]
                    if in_test(idx):
                        continue
                    start = hit[4] if len(hit) > 4 else idx
                    entries.append(Entry(rel, line_of(src, idx), line_of(src, end), kind, name,
                                         span_start=line_of(src, start)))
            for s, rx, g in extra:
                if not g.match(rel):
                    continue
                for m in rx.finditer(src):
                    if in_test(m.start()):
                        continue
                    enc = enclosing_fn(masked, m.start()) if lang == "rust" else None
                    ln = line_of(src, m.start())
                    entries.append(Entry(rel, ln, line_of(src, enc[2]) if enc else ln, "extra:" + s["name"],
                                         m.groupdict().get("name") or s["name"]))
    return entries, found


# ------------------------------------------------------------------ diagrams

def load_diagrams(repo: Path) -> dict:
    """stem -> {"lines": [(file, a, b, ident|None)], "files": set}"""
    out = {}
    d = repo / DIAGRAM_DIR
    for p in sorted(d.glob("seq-*.json")) if d.is_dir() else []:
        text = p.read_text(encoding="utf-8", errors="replace")
        lines, files = [], set()
        for m in ANCHOR_RE.finditer(text):
            a = int(m["line"])
            b = int(m["end"]) if m["end"] else a
            f = m["file"].lstrip("./") if m["file"].startswith("./") else m["file"]
            lines.append((f, min(a, b), max(a, b), m["ident"]))
            files.add(f)
        try:
            doc = json.loads(text)
        except ValueError:
            doc = None
        stack = [doc]
        while stack:  # evidence[].path
            cur = stack.pop()
            if isinstance(cur, dict):
                for k, v in cur.items():
                    if k == "evidence" and isinstance(v, list):
                        for ev in v:
                            if isinstance(ev, dict) and isinstance(ev.get("path"), str) and ev["path"].strip():
                                files.add(ev["path"].strip().split(":")[0].lstrip("./"))
                    stack.append(v)
            elif isinstance(cur, list):
                stack.extend(cur)
        out[p.stem] = {"lines": lines, "files": files}
    return out


def classify(entries, diagrams, cfg):
    exempt = {ex["entry"].strip(): ex["why"].strip() for ex in cfg["exempt"]}
    for e in entries:
        covered, partial = [], []
        lo = e.span_start or e.line
        for stem, dg in diagrams.items():
            hit = False
            for f, a, b, ident in dg["lines"]:
                if f != e.path:
                    continue
                if a <= e.end and b >= lo:           # anchored range meets the span
                    hit = True
                elif ident and e.kind != "http-route" and re.search(r"\b" + re.escape(e.name) + r"\b", ident):
                    hit = True
            if hit:
                covered.append(stem)
            elif e.path in dg["files"]:
                partial.append(stem)
        if covered:
            e.status, e.diagrams = "covered", covered
        elif partial:
            e.status, e.diagrams = "partial", partial
        e.exempt = exempt.get(e.key)
    return sorted(set(exempt) - {e.key for e in entries})


# ------------------------------------------------------------------ git

def changed_files(repo: Path, base: str) -> set:
    r = subprocess.run(["git", "-C", str(repo), "diff", "--name-only", "--diff-filter=AMR", f"{base}...HEAD"],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"git diff {base}...HEAD failed: {r.stderr.strip()}")
    return {x for x in r.stdout.splitlines() if x}


# ------------------------------------------------------------------ report

def analyse(repo: Path, changed_base: str | None = None) -> dict:
    cfg = load_config(repo)
    entries, langs = find_entries(repo, cfg)
    diagrams = load_diagrams(repo)
    stale = classify(entries, diagrams, cfg)
    entries.sort(key=lambda e: (e.path, e.line, e.name))
    scope = entries
    if changed_base:
        ch = changed_files(repo, changed_base)
        scope = [e for e in entries if e.path in ch]
    failing = [e for e in scope if e.status == "uncovered" and not e.exempt]
    zero = sorted(s for s, d in diagrams.items() if not d["lines"] and not d["files"])
    return {"languages": langs, "entries": entries, "scope": scope, "failing": failing,
            "zero_anchor_diagrams": zero, "stale_exemptions": stale, "diagrams": len(diagrams)}


def render(rep: dict, changed: bool) -> str:
    if not rep["languages"]:
        return "flow-coverage: no supported language, skipped"
    rows = []
    for e in rep["scope"]:
        st = "exempt" if e.exempt and e.status == "uncovered" else e.status
        rows.append((f"{e.path}:{e.line}", e.kind, e.name, st, ",".join(e.diagrams)))
    w = [max([len(r[i]) for r in rows] + [5]) for i in range(5)]
    lines = ["  ".join(h.ljust(w[i]) for i, h in enumerate(("ENTRY", "KIND", "NAME", "STATUS", "DIAGRAMS")))]
    lines += ["  ".join(c.ljust(w[i]) for i, c in enumerate(r)).rstrip() for r in rows]
    cnt = {}
    for r in rows:
        cnt[r[3]] = cnt.get(r[3], 0) + 1
    summ = ", ".join(f"{k} {v}" for k, v in sorted(cnt.items())) or "none"
    lines.append("")
    lines.append(f"flow-coverage: {len(rows)} entry points{' in changed files' if changed else ''} ({summ}); "
                 f"{rep['diagrams']} seq diagrams")
    if rep["zero_anchor_diagrams"]:
        lines.append("seq diagrams with zero anchors: " + ", ".join(rep["zero_anchor_diagrams"]))
    if rep["stale_exemptions"]:
        lines.append("exemptions matching no entry point: " + ", ".join(rep["stale_exemptions"]))
    if changed and rep["failing"]:
        lines.append("FAIL: uncovered and not exempt: " + ", ".join(f"{e.path}:{e.line} {e.name}" for e in rep["failing"]))
    return "\n".join(lines)


def run(repo: Path, as_json: bool, changed: str | None) -> int:
    try:
        rep = analyse(repo, changed)
    except ConfigError as e:
        print(f"flow-coverage: config error: {e}", file=sys.stderr)
        return 2
    except RuntimeError as e:
        print(f"flow-coverage: {e}", file=sys.stderr)
        return 2
    if as_json:
        print(json.dumps({"languages": rep["languages"], "diagrams": rep["diagrams"],
                          "entries": [e.as_dict() for e in rep["scope"]],
                          "zero_anchor_diagrams": rep["zero_anchor_diagrams"],
                          "stale_exemptions": rep["stale_exemptions"],
                          "failing": [e.key for e in rep["failing"]]}, indent=2))
    else:
        print(render(rep, bool(changed)))
    return 1 if changed and rep["failing"] else 0


# ------------------------------------------------------------------ selftest

def _selftest() -> int:
    fails: list = []

    def check(name, ok):
        if not ok:
            fails.append(name)

    main_rs = 'fn main() {\n    let s = "{ not a brace";\n    // } nor this\n    run();\n}\n'
    bg_rs = ("pub async fn run(cfg: Cfg, shutdown: Shutdown) {\n    loop {}\n}\n\n"
             "pub async fn run(cfg: Cfg) {}\n")
    reg_rs = ("pub fn register_ask(registry: &mut ToolRegistry) -> R {\n    Ok(())\n}\n"
              "pub fn register_instruments(meter: &Meter) {}\n")
    srv_rs = "pub async fn serve_ping_requests(c: C) {\n    c.go();\n}\n"
    http_rs = ("pub fn router() -> Router {\n    Router::new()\n        .route(\"/health\", get(h))\n"
               "        .route(\"/v1/x\", post(x))\n}\n"
               "#[cfg(test)]\nmod tests {\n    fn t() { Router::new().route(\"/t\", get(h)); }\n}\n")
    exempt_rs = "pub async fn serve_old_requests(c: C) {\n}\n"

    def seq(*labels):
        return json.dumps({"nodes": [{"id": f"n{i}", "label": lb} for i, lb in enumerate(labels)]})

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)

        def w(rel, text):
            p = root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text, encoding="utf-8")

        w("crates/a/src/main.rs", main_rs)
        w("crates/a/src/bg.rs", bg_rs)
        w("crates/a/src/reg.rs", reg_rs)
        w("crates/a/src/srv.rs", srv_rs)
        w("crates/a/src/http.rs", http_rs)
        w("crates/a/src/old.rs", exempt_rs)
        w("crates/a/src/bin/tool.rs", "fn main() {}\n")
        w("crates/a/tests/it.rs", "fn main() {}\n")
        d = "docs/architecture/"
        w(d + "seq-one.json", seq("boot crates/a/src/main.rs:4 `run`",
                                  "bg crates/a/src/bg.rs:2",
                                  "h crates/a/src/http.rs:3"))
        w(d + "seq-two.json", seq("name only crates/a/src/srv.rs:900 `serve_ping_requests`",
                                  "file only crates/a/src/reg.rs:900"))
        w(d + "seq-empty.json", seq("no anchors here"))
        # Evidence-path-only diagram.
        w(d + "seq-ev.json", json.dumps({"nodes": [{"label": "x", "evidence": [{"path": "crates/a/src/old.rs"}]}]}))

        rep = analyse(root)
        by = {(e.path.split("/")[-1], e.name): e for e in rep["entries"]}
        check("sig:main", ("main.rs", "main") in by and by[("main.rs", "main")].kind == "main")
        check("sig:main-bin", ("tool.rs", "main") in by)
        check("sig:main-tests-dir-skipped", ("it.rs", "main") not in by)
        check("sig:main-span-ignores-string-braces", by[("main.rs", "main")].end == 5)
        check("sig:bg-only-with-shutdown", len([e for e in rep["entries"] if e.kind == "background-task"]) == 1)
        check("sig:register-needs-registry", [e.name for e in rep["entries"] if e.kind == "tool-registration"] == ["register_ask"])
        check("sig:serve", ("srv.rs", "serve_ping_requests") in by)
        routes = sorted(e.name for e in rep["entries"] if e.kind == "http-route")
        check("sig:routes-per-path-not-test", routes == ["/health", "/v1/x"])
        check("cov:line-in-span", by[("main.rs", "main")].status == "covered")
        check("cov:stems", by[("main.rs", "main")].diagrams == ["seq-one"])
        check("cov:bg", by[("bg.rs", "run")].status == "covered")
        check("cov:by-name", by[("srv.rs", "serve_ping_requests")].status == "covered")
        check("cov:partial", by[("reg.rs", "register_ask")].status == "partial")
        check("cov:uncovered", by[("tool.rs", "main")].status == "uncovered")
        check("cov:evidence-path-is-partial", by[("old.rs", "serve_old_requests")].status == "partial")
        check("cov:route-in-enclosing-fn", by[("http.rs", "/health")].status == "covered")
        check("zero-anchor", rep["zero_anchor_diagrams"] == ["seq-empty"])

        # Exemptions.
        cfgp = root / d / CONFIG_NAME
        cfgp.write_text(json.dumps({"exempt": [{"entry": "crates/a/src/bin/tool.rs::main", "why": "one-shot"}]}))
        rep = analyse(root)
        t = [e for e in rep["entries"] if e.path.endswith("bin/tool.rs")][0]
        check("exempt:with-why", t.exempt == "one-shot" and t not in rep["failing"])
        check("exempt:others-still-fail", rep["failing"] == [])  # everything else is covered or partial
        cfgp.write_text(json.dumps({"exempt": [{"entry": "x::y"}]}))
        try:
            analyse(root)
            check("exempt:no-why-is-error", False)
        except ConfigError:
            pass
        check("exempt:cli-exit-2", run(root, False, None) == 2)
        cfgp.write_text(json.dumps({"exempt": [{"entry": "gone::fn", "why": "x"}],
                                    "extra_signals": [{"name": "job", "regex": r"fn (?P<name>job_\w+)\(", "glob": "crates/**/*.rs"}]}))
        w("crates/a/src/jobs.rs", "fn job_nightly() {\n}\n")
        rep = analyse(root)
        check("config:extra-signal", any(e.kind == "extra:job" and e.name == "job_nightly" and e.status == "uncovered" for e in rep["entries"]))
        check("config:stale-exemption", rep["stale_exemptions"] == ["gone::fn"])
        cfgp.unlink()
        (root / "crates/a/src/jobs.rs").unlink()

        # --changed (needs git).
        g = lambda *a: subprocess.run(["git", "-C", td, *a], capture_output=True, text=True)
        if g("init", "-q", "-b", "main").returncode == 0:
            g("config", "user.email", "t@example.invalid")
            g("config", "user.name", "selftest")
            g("config", "commit.gpgsign", "false")
            g("add", "-A")
            g("commit", "-qm", "base")
            check("changed:nothing-changed", run(root, False, "HEAD") == 0)
            g("checkout", "-qb", "feat")
            w("crates/a/src/new.rs", "pub async fn serve_new_requests(c: C) {\n}\n")
            g("add", "-A")
            g("commit", "-qm", "new entry")
            rep = analyse(root, "main")
            check("changed:scope-only-changed-files", [e.name for e in rep["scope"]] == ["serve_new_requests"])
            check("changed:uncovered-exit-1", run(root, False, "main") == 1)
            w(d + CONFIG_NAME, json.dumps({"exempt": [{"entry": "crates/a/src/new.rs::serve_new_requests", "why": "internal"}]}))
            g("add", "-A")
            g("commit", "-qm", "exempt")
            check("changed:exempt-exit-0", run(root, False, "main") == 0)
            (root / d / CONFIG_NAME).unlink()
            w(d + "seq-new.json", seq("n crates/a/src/new.rs:2"))
            g("add", "-A")
            g("commit", "-qm", "diagram")
            check("changed:covered-exit-0", run(root, False, "main") == 0)
        else:
            print("flow_coverage selftest: git unavailable, --changed cases skipped", file=sys.stderr)

        # No supported language.
        with tempfile.TemporaryDirectory() as td2:
            check("nolang:exit-0", run(Path(td2), False, None) == 0)
            check("nolang:message", render(analyse(Path(td2)), False) == "flow-coverage: no supported language, skipped")

    if fails:
        print("flow_coverage selftest FAILED:\n  " + "\n  ".join(fails), file=sys.stderr)
        return 1
    print("flow_coverage selftest ok")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    ap.add_argument("--repo", help="repository root")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--changed", metavar="BASE_REF",
                    help="only entry points in files changed by `git diff BASE_REF...HEAD`; exit 1 if one is uncovered and not exempt")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)
    if args.selftest:
        return _selftest()
    if not args.repo:
        ap.error("--repo is required unless --selftest")
    return run(Path(args.repo), args.json, args.changed)


if __name__ == "__main__":
    sys.exit(main())
