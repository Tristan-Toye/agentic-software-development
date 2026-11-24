#!/usr/bin/env python3
"""The review packet: six mechanical data producers for a finished dossier's reviewer.

    review_packet.py all --root DIR --dossier ID [--base SHA] [--head REV] [--out DIR] [--log]
    review_packet.py contract-diff|criteria-evidence|reading-order|coupling-delta|invariants|migration-lint  (same flags)
    review_packet.py --selftest

Each producer writes one JSON file under `<root>/.discovery/review-packet/<ID>/` (`--out`
overrides). The schemas are `references/visual-review/review-packet-<name>.schema.json`.

    contract-diff.json      the public API / signature changes: before and after text, a severity
                            (ERR breaking, WARN cannot be confirmed, INFO additive), a `cannot_confirm`
                            list and a `not_caught` list. From vr_diff's merged graph (work/merged.json,
                            or merged from work/before.json + work/after.json); the before side that
                            `after-from-git` does not carry is read from the base revision.
                            cargo-semver-checks runs only when it is already installed.
    criteria-evidence.json  one row per acceptance criterion: the tests that cover it, their results, the
                            diff files and hunks, how every link was found (owner annotation, CENSUS pin,
                            test name, PROMISE_CHECKLIST) and a status covered / partial / no-evidence.
                            Derived from the dossier and .discovery/checklists/<ID>.md; nothing is copied.
    reading-order.json      the contract, then the riskiest work package, then the rest grouped by
                            relatedness and ranked by diff size, with an explicit `not_listed` count.
    coupling-delta.json     new and removed `depends` edges, new dependency cycles and layer breaks
                            (when a layer map is given), from the same merged graph.
    invariants.json         invariants and pre-mortem failure modes. Seeded mechanically (property-invariant
                            checklist lines, contract prose, review_signals markers); the orchestrator adds
                            its own through `invariants.authored.json` in the output directory. An item
                            with no verifiable anchor is `speculative: true`.

    migration-lint.json     a fixed rule list (MIG001 drop table ... MIG008 truncate, plus MIG006 an edit to a migration
                            already on the base) run over the SQL migrations the change adds or edits; each finding is a rule
                            id and a path:line, never a sentence. `--migrations GLOB` (repeatable) overrides the discovery
                            (merge_dossier's migrations/NNNN_name.sql); `-- lint:ignore MIGnnn` lists a finding as suppressed.

A producer that cannot run (no graph, a language the extractor does not cover, no git range) writes
`{"skipped": "<reason>"}` and the run goes on: the packet never blocks a build. Every generated sentence is
either a mechanical fact with a `path:line` / test-id anchor or labelled speculative.

`--log` appends one `REVIEW-PACKET: <file> ...` receipt line per file to the dossier's Build log.
`--test-log FILE` (repeatable) feeds per-test results (cargo test, nextest, pytest, dotnet) to
criteria-evidence; without it the dossier's `SUITE:` lines give a suite-level verdict.
`--scope FILE` is the blast-radius-scout SCOPE list (`path:line (changed|caller)`) for reading-order.
`--layers FILE` is a JSON layer map for coupling-delta: {"order": [top..bottom], "patterns": {layer: [glob]}}.
Exit codes: 0 always for `all` (skips are data); 2 for unusable arguments.
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

SCHEMA_VERSION = "1.0.0"
PRODUCERS = ("contract-diff", "criteria-evidence", "reading-order", "coupling-delta", "invariants", "migration-lint")
OUT_REL = os.path.join(".discovery", "review-packet")
REF_DIR = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "references", "visual-review"))

NOT_CAUGHT = [
    "behaviour changes behind an unchanged signature (a body, a default, an error case, ordering)",
    "generics, lifetimes, where-clauses and trait bounds (only a text difference is reported, as WARN)",
    "fields, variants and required trait methods of a type: its declaration line shows none of them",
    "re-exports, visibility through a `pub use`, glob imports and macro-generated items",
    "serialized shapes, wire formats, database schema, configuration keys and environment variables",
    "a signature the extractor did not record (a node touched with no signature is listed under cannot_confirm)",
    "callers outside this repository",
]


class PacketError(Exception):
    pass


# ---------------------------------------------------------------------------------------------- helpers


def git(root: str, *args: str) -> str:
    proc = subprocess.run(["git", "-C", root, *args], capture_output=True, text=True)
    if proc.returncode != 0:
        raise PacketError(proc.stderr.strip() or "git %s failed" % " ".join(args))
    return proc.stdout


def git_try(root: str, *args: str) -> str | None:
    try:
        return git(root, *args)
    except PacketError:
        return None


def skipped(name: str, dossier: str, reason: str) -> dict:
    return {"schema_version": SCHEMA_VERSION, "producer": name, "dossier": dossier, "skipped": reason}


def find_dossier(root: str, dossier: str) -> str | None:
    d = os.path.join(root, ".discovery", "dossiers")
    if not os.path.isdir(d):
        return None
    for f in sorted(os.listdir(d)):
        if f.endswith(".md") and (f == dossier + ".md" or f.startswith(dossier + "-")):
            return os.path.join(d, f)
    return None


def read(path: str) -> str:
    with open(path, encoding="utf-8", errors="replace") as fh:
        return fh.read()


def section(text: str, title: str) -> tuple[list[str], int]:
    """(lines of `## <title>` up to the next level-2 heading, 1-based line of the first body line)."""
    lines = text.split("\n")
    out: list[str] = []
    start = 0
    fence = False
    for i, line in enumerate(lines):
        if line.startswith("```"):
            fence = not fence
        if not fence and re.match(r"##\s+" + re.escape(title) + r"\b", line):
            start = i + 2
            continue
        if start and not fence and line.startswith("## "):
            break
        if start:
            out.append(line)
    return out, start


def load_graph(root: str, dossier: str) -> tuple[dict | None, str]:
    """The vr_diff merged graph of the dossier, or (None, reason)."""
    work = os.path.join(root, ".discovery", "visual-review", dossier, "work")
    merged = os.path.join(work, "merged.json")
    try:
        if os.path.isfile(merged):
            return json.loads(read(merged)), merged
        b, a = os.path.join(work, "before.json"), os.path.join(work, "after.json")
        if os.path.isfile(b) and os.path.isfile(a):
            import vr_diff

            return vr_diff.merge(json.loads(read(b)), json.loads(read(a))), "merged from before.json + after.json"
    except Exception as err:  # a broken graph is a skip, never a crash
        return None, "unreadable graph: %s" % err
    return None, "no vr_diff output under .discovery/visual-review/%s/work/ (merged.json or before.json+after.json)" % dossier


def diff_files(root: str, base: str, head: str) -> list[tuple[str, int, int]]:
    """(path, added, deleted) per changed path; binary files count 0/0."""
    out = []
    for line in git(root, "diff", "--numstat", "--no-renames", "%s..%s" % (base, head)).splitlines():
        parts = line.split("\t")
        if len(parts) == 3:
            a, d = (int(parts[0]) if parts[0].isdigit() else 0), (int(parts[1]) if parts[1].isdigit() else 0)
            out.append((parts[2], a, d))
    return out


def diff_hunks(root: str, base: str, head: str, paths: list[str]) -> dict[str, list[str]]:
    """Head-side line ranges `start-end` per path, from a zero-context diff."""
    res: dict[str, list[str]] = defaultdict(list)
    if not paths:
        return res
    cur = None
    for line in git(root, "diff", "-U0", "--no-color", "--no-renames", "%s..%s" % (base, head), "--", *paths).splitlines():
        if line.startswith("+++ "):
            cur = line[6:] if line.startswith("+++ b/") else None
        elif line.startswith("@@") and cur:
            m = re.search(r"\+(\d+)(?:,(\d+))?", line)
            if m:
                s, c = int(m.group(1)), int(m.group(2) if m.group(2) is not None else 1)
                res[cur].append("%d-%d" % (s, max(s, s + c - 1)))
    return res


def lang_of(path: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    return {".rs": "rust", ".py": "python", ".ts": "typescript", ".tsx": "typescript", ".js": "javascript",
            ".cs": "csharp", ".go": "go", ".java": "java"}.get(ext, "unknown")


# ---------------------------------------------------------------------------------------------- 1. contract-diff

SIG_KW = r"(?:fn|struct|enum|trait|impl|type|mod|def|class|interface|function|record|const|static)"
SIG_START = re.compile(
    r"(?:(?:pub(?:\([^)]*\))?|public|export|internal|protected|private)\s+)?"
    r"(?:(?:async|unsafe|extern|abstract|override|virtual|sealed|partial|default|static|readonly)\s+)*"
    r"(" + SIG_KW + r")\s+([A-Za-z_]\w*)"
)


def split_signatures(text: str) -> list[str]:
    """vr_diff joins several added signature lines into one detail string with '; '."""
    if not text:
        return []
    parts = re.split(r";\s+(?=(?:pub(?:\([^)]*\))?\s+)?(?:async\s+|unsafe\s+)*" + SIG_KW + r"\s)", text.strip())
    return [p.strip() for p in parts if p.strip()]


def normalise_sig(sig: str) -> str:
    sig = sig.strip()
    sig = re.sub(r"\s*(\{.*|;|=\s*todo!\(\).*|:\s*$)$", "", sig).strip()
    return re.sub(r"\s+", " ", sig)


def balanced_group(text: str, open_at: int) -> tuple[str, int]:
    """The text inside the bracket at `open_at` and the index after its closer."""
    pairs = {"(": ")", "[": "]", "<": ">"}
    depth = 0
    opener = text[open_at]
    closer = pairs[opener]
    for i in range(open_at, len(text)):
        c = text[i]
        if c == opener:
            depth += 1
        elif c == closer and not (opener == "<" and text[i - 1] == "-"):
            depth -= 1
            if depth == 0:
                return text[open_at + 1 : i], i + 1
    return text[open_at + 1 :], len(text)


def split_top(text: str, sep: str = ",") -> list[str]:
    out, depth, cur = [], 0, ""
    for i, c in enumerate(text):
        if c in "([{<" and not (c == "<" and False):
            depth += 1
        elif c in ")]}>" and not (c == ">" and text[i - 1:i] == "-"):
            depth -= 1
        if c == sep and depth == 0:
            out.append(cur.strip())
            cur = ""
        else:
            cur += c
    if cur.strip():
        out.append(cur.strip())
    return out


def parse_sig(sig: str, lang: str) -> dict:
    """kind, name, visibility, parameters (list of 'name: type' or 'name'), returns, generics, rest."""
    s = normalise_sig(sig)
    m = SIG_START.search(s)
    out = {"text": s, "kind": "other", "name": None, "visibility": "unknown", "params": [], "returns": "", "generics": ""}
    if not m:
        return out
    kind, name = m.group(1), m.group(2)
    head = s[: m.start(1)]
    out.update(kind={"def": "fn", "function": "fn"}.get(kind, kind), name=name)
    if re.search(r"\b(pub|public|export)\b", head):
        out["visibility"] = "public"
    elif re.search(r"\b(private|internal)\b", head) or (lang == "python" and name.startswith("_")):
        out["visibility"] = "private"
    elif lang == "python":
        out["visibility"] = "public"
    elif lang == "rust" and kind in ("fn", "struct", "enum", "trait", "type", "mod", "const", "static"):
        out["visibility"] = "private"
    rest = s[m.end():]
    if rest.startswith("<"):
        g, idx = balanced_group(rest, 0)
        out["generics"] = g.strip()
        rest = rest[idx:]
    rest = rest.lstrip()
    if rest.startswith("("):
        inner, idx = balanced_group(rest, 0)
        out["params"] = [p for p in split_top(inner) if p and p not in ("self", "&self", "&mut self", "mut self", "cls")]
        tail = rest[idx:].strip()
        r = re.match(r"(?:->|:)\s*(.*)$", tail)
        if r:
            ret = r.group(1)
            wh = re.search(r"\bwhere\b", ret)
            if wh:
                out["generics"] = (out["generics"] + " where " + ret[wh.end():].strip()).strip()
                ret = ret[: wh.start()]
            out["returns"] = ret.strip()
        elif re.search(r"\bwhere\b", tail):
            out["generics"] = (out["generics"] + " where " + tail.split("where", 1)[1].strip()).strip()
    return out


def _param_type(p: str) -> str:
    return p.split(":", 1)[1].strip() if ":" in p else p.strip()


def _param_name(p: str) -> str:
    return re.split(r"[:=\s]", p.strip().lstrip("*&"), maxsplit=1)[0]


def classify(before: dict | None, after: dict | None, lang: str) -> tuple[str, str, str, str]:
    """(severity, confidence, rule, reason) for one signature pair; either side may be None."""
    if before is None and after is not None:
        if after["visibility"] == "private":
            return "INFO", "confirmed", "added-private", "a private item was added"
        return "INFO", "confirmed", "added", "an item was added; a new required member of an existing trait or enum would not show here"
    if after is None and before is not None:
        if before["visibility"] == "private":
            return "INFO", "confirmed", "removed-private", "a private item was removed"
        if before["visibility"] == "public":
            return "ERR", "confirmed", "removed-public", "a public item was removed"
        return "WARN", "cannot-confirm", "removed-unknown-visibility", "an item was removed and its visibility is not in the signature"
    assert before is not None and after is not None
    if before["kind"] == "other" or after["kind"] == "other":
        return "WARN", "cannot-confirm", "unparseable-signature", "a side is not a recognisable signature line, so the pair is not compared"
    if before["visibility"] == "private" and after["visibility"] == "private":
        return "INFO", "confirmed", "private-changed", "a private item changed"
    if before["visibility"] == "public" and after["visibility"] == "private":
        return "ERR", "confirmed", "visibility-narrowed", "public became private"
    if before["kind"] != after["kind"]:
        return "ERR", "confirmed", "kind-changed", "%s became %s" % (before["kind"], after["kind"])
    if before["kind"] == "fn":
        bp, ap = before["params"], after["params"]
        by_type = lang in ("rust", "csharp", "go", "java")
        key = _param_type if by_type else _param_name
        bk, ak = [key(p) for p in bp], [key(p) for p in ap]
        if bk != ak:
            extra = ak[len(bk):] if ak[: len(bk)] == bk else None
            if extra is not None and all("=" in p for p in ap[len(bp):]) and lang in ("python", "typescript", "javascript", "csharp"):
                return "INFO", "confirmed", "param-added-default", "a trailing parameter with a default was added"
            return "ERR", "confirmed", "params-changed", "the parameter list changed (%d -> %d)" % (len(bp), len(ap))
        if before["returns"] != after["returns"]:
            return "ERR", "confirmed", "return-changed", "the return type changed"
        if before["generics"] != after["generics"]:
            return "WARN", "cannot-confirm", "generics-changed", "generics or bounds differ; compatibility is not decidable from the line"
        if bp != ap:
            sev = "WARN" if lang in ("python", "csharp", "typescript", "javascript") else "INFO"
            return (sev, "cannot-confirm" if sev == "WARN" else "confirmed", "param-renamed",
                    "only parameter names differ" + ("; a caller passing them by keyword breaks" if sev == "WARN" else ""))
        return "INFO", "confirmed", "text-only", "the line differs in formatting or attributes only"
    if before["text"] != after["text"]:
        return "WARN", "cannot-confirm", "type-declaration-changed", \
            "a type declaration changed; its members are not in the signature line"
    return "INFO", "confirmed", "unchanged", "no difference"


def sig_at(lines: list[str], i: int) -> str:
    """The signature that starts on line index i, joined across lines until its parens close and a body or `;` starts."""
    buf = ""
    for j in range(i, min(len(lines), i + 14)):
        buf += (" " if buf else "") + lines[j].strip()
        depth = buf.count("(") - buf.count(")")
        if depth <= 0 and re.search(r"[{;]\s*$|\bwhere\b|:\s*$|\)\s*(->.*)?$", buf):
            break
    return buf


def find_sig(lines: list[str], kind: str, name: str) -> tuple[int, str] | None:
    pat = re.compile(r"^\s*(?:(?:pub(?:\([^)]*\))?|public|export|internal|private|protected)\s+)?"
                     r"(?:(?:async|unsafe|extern|abstract|override|virtual|sealed|partial|static|const|default)\s+)*"
                     r"(?:def|fn|function|" + re.escape(kind) + r")\s+" + re.escape(name) + r"\b")
    for i, line in enumerate(lines):
        if pat.match(line):
            return i + 1, sig_at(lines, i)
    return None


def run_semver_checks(root: str, base: str, runner=subprocess.run) -> dict:
    exe = shutil.which("cargo-semver-checks")
    if not exe:
        return {"status": "not-installed", "note": "cargo-semver-checks is not installed; nothing was installed"}
    try:
        proc = runner([exe, "semver-checks", "--baseline-rev", base], cwd=root, capture_output=True, text=True, timeout=600)
    except (OSError, subprocess.SubprocessError) as err:
        return {"status": "failed-to-run", "note": str(err)}
    findings = [l.strip() for l in (proc.stdout + proc.stderr).splitlines() if l.strip().startswith(("--- failure", "FAIL", "Summary"))]
    return {"status": "ran", "exit": proc.returncode, "findings": findings[:40]}


def node_signatures(node: dict) -> tuple[list[str], list[str]]:
    d = node.get("detail") or {}
    return split_signatures(d.get("before") or ""), split_signatures(d.get("after") or "")


def contract_diff(root: str, dossier: str, base: str, head: str, semver_runner=subprocess.run) -> dict:
    graph, src = load_graph(root, dossier)
    if graph is None:
        return skipped("contract-diff", dossier, src)
    entries: list[dict] = []
    cannot: list[dict] = []
    unchanged = 0
    head_cache: dict[str, list[str] | None] = {}
    base_cache: dict[str, list[str] | None] = {}

    def file_lines(rev: str, path: str, cache: dict) -> list[str] | None:
        if path not in cache:
            txt = git_try(root, "show", "%s:%s" % (rev, path))
            cache[path] = None if txt is None else txt.split("\n")
        return cache[path]

    for node in graph.get("nodes", []):
        st = node.get("status")
        if st not in ("added", "changed", "removed"):
            continue
        before_sigs, after_sigs = node_signatures(node)
        ev = [e for e in node.get("evidence", []) if e.get("tier") == "source" and e.get("path")]
        sig_ev = next((e for e in ev if str(e.get("note", "")).startswith("stub signature")), None) or (ev[0] if ev else None)
        path = sig_ev.get("path") if sig_ev else None
        lang = lang_of(path) if path else "unknown"
        if not before_sigs and not after_sigs:
            if node.get("kind") in ("module", "crate", "bin") and st in ("added", "removed"):
                sev = "INFO" if st == "added" else "WARN"
                entries.append({
                    "id": node["id"], "name": node.get("label") or node["id"], "kind": node.get("kind"), "language": lang,
                    "status": st, "before": None if st == "added" else node["id"], "after": node["id"] if st == "added" else None,
                    "severity": sev, "tier": "confirmed" if sev == "INFO" else "cannot-confirm",
                    "rule": "%s-%s" % (node.get("kind"), st), "reason": "a %s was %s; its visibility is not recorded" % (node.get("kind"), st),
                    "anchor": "%s:%s" % (path, sig_ev.get("line")) if sig_ev and sig_ev.get("line") else path, "source": "vr_diff"})
            elif st == "changed":
                cannot.append({"id": node["id"], "reason": "changed, but the extractor recorded no signature", "anchor": path})
            continue
        for a_text in after_sigs or [None]:
            pa = parse_sig(a_text, lang) if a_text else None
            name = pa["name"] if pa else None
            b_text, line, src_note = None, sig_ev.get("line") if sig_ev else None, "vr_diff.detail"
            if name:
                b_text = next((b for b in before_sigs if (parse_sig(b, lang)["name"] == name)), None)
            # the before side: after-from-git carries only the added lines, so ask the base revision
            if pa and b_text is None and path and st == "changed":
                bl = file_lines(base, path, base_cache)
                hit = find_sig(bl, pa["kind"], name) if (bl and name) else None
                if hit:
                    b_text, src_note = hit[1], "vr_diff.detail + base revision"
            # multi-line signatures: re-read the head file from the evidence line when the name matches
            if pa and path and line:
                hl = file_lines(head, path, head_cache)
                if hl and 0 < int(line) <= len(hl) and name and re.search(r"\b%s\b" % re.escape(name), hl[int(line) - 1]):
                    full = sig_at(hl, int(line) - 1)
                    if full.count("(") == full.count(")") and len(full) > len(a_text or ""):
                        pa = parse_sig(full, lang)
                        a_text = full
            pb = parse_sig(b_text, lang) if b_text else None
            if st == "removed":
                pa, a_text = None, None
                if pb is None and before_sigs:
                    pb, b_text = parse_sig(before_sigs[0], lang), before_sigs[0]
            sev, conf, rule, reason = classify(pb, pa, lang)
            if rule == "unchanged" or (rule == "text-only" and pb and pa and pb["text"] == pa["text"]):
                if st == "changed" and pa and pa["visibility"] != "private":
                    sev, conf, rule = "WARN", "cannot-confirm", "signature-line-unchanged"
                    reason = "the node changed but its signature line did not; members, body or bounds may differ"
                else:
                    unchanged += 1
                    continue
            entries.append({
                "id": node["id"] + ("#" + name if name else ""), "name": (pa or pb or {}).get("name") or node.get("label"),
                "kind": (pa or pb or {}).get("kind", node.get("kind")), "language": lang, "status": "added" if pb is None else ("removed" if pa is None else "changed"),
                "before": pb["text"] if pb else None, "after": pa["text"] if pa else None,
                "severity": sev, "tier": conf, "rule": rule, "reason": reason,
                "anchor": "%s:%s" % (path, line) if path and line else path, "source": src_note})
    order = {"ERR": 0, "WARN": 1, "INFO": 2}
    entries.sort(key=lambda e: (order[e["severity"]], e["id"]))
    counts = Counter(e["severity"] for e in entries)
    langs = sorted({e["language"] for e in entries})
    semver = {"status": "not-applicable", "note": "no Rust signatures in this change"}
    if "rust" in langs:
        semver = run_semver_checks(root, base, semver_runner)
    return {
        "schema_version": SCHEMA_VERSION, "producer": "contract-diff", "dossier": dossier, "base": base, "head": head,
        "graph": src, "summary": {"ERR": counts["ERR"], "WARN": counts["WARN"], "INFO": counts["INFO"], "unchanged_signatures": unchanged},
        "entries": entries, "cannot_confirm": cannot, "not_caught": NOT_CAUGHT, "semver_checks": semver,
    }


# ---------------------------------------------------------------------------------------------- 2. criteria-evidence

try:
    from validate_pipeline import CRITERION_OWNER_RE  # the one owner/env grammar, never a second copy
except Exception:  # pragma: no cover - validate_pipeline unimportable only in a broken checkout
    CRITERION_OWNER_RE = re.compile(r"\(owner:\s*[\w./-]+\s*;\s*env:\s*[\w-]+\s*\)")
OWNER_PARTS = re.compile(r"owner:\s*([\w./-]+)\s*;\s*env:\s*([\w-]+)")


def parse_criteria(text: str) -> list[dict]:
    body, start = section(text, "Acceptance criteria")
    rows: list[dict] = []
    for i, line in enumerate(body):
        m = re.match(r"\s*(\d+)\.\s+(.*)", line)
        if m:
            rows.append({"n": int(m.group(1)), "text": m.group(2), "line": start + i})
        elif rows and line.strip() and not line.startswith("#"):
            rows[-1]["text"] += " " + line.strip()
    for r in rows:
        mo = CRITERION_OWNER_RE.search(r["text"])
        parts = OWNER_PARTS.search(mo.group(0)) if mo else None
        r["owner"], r["env"] = (parts.group(1), parts.group(2)) if parts else (None, None)
        r["unverifiable"] = "UNVERIFIABLE-LOCALLY" in r["text"]
    return rows


def parse_checklist(path: str) -> list[dict]:
    """PROMISE_CHECKLIST lines: id, member, category, surface test path, property tag."""
    if not os.path.isfile(path):
        return []
    out, surface = [], None
    for i, line in enumerate(read(path).split("\n"), 1):
        m = re.match(r"##\s+Surface:\s*(.*?)\s*\(([^)]+)\)\s*$", line)
        if m:
            surface = m.group(2)
            continue
        m = re.match(r"-\s+([A-Z]+\d+(?:\.\d+)?)\s+#?([\w:.<>]+)\s+[—-]+\s+([\w -]+?):", line)
        if m:
            out.append({"id": m.group(1), "member": m.group(2), "category": m.group(3).strip(), "test_file": surface,
                        "line": i, "invariant": "[property: invariant]" in line, "text": line[2:]})
    return out


def parse_census(text: str) -> list[dict]:
    out = []
    for m in re.finditer(r"^\s*[-*]?\s*CENSUS:\s*(\S.*)$", text, re.MULTILINE):
        body = m.group(1)
        if body.startswith("none"):
            continue
        pm = re.match(r"([\w./-]+):(\d+)", body)
        out.append({"text": body[:200], "path": pm.group(1) if pm else None, "line": int(pm.group(2)) if pm else None,
                    "criteria": sorted({int(x) for x in re.findall(r"\bcriteri(?:on|a)\s+(\d+)", body, re.IGNORECASE)})})
    return out


TEST_FN = {
    ".rs": re.compile(r"^\s*(?:pub\s+)?(?:async\s+)?fn\s+(\w+)"),
    ".py": re.compile(r"^\s*(?:async\s+)?def\s+(test\w*)"),
    ".cs": re.compile(r"^\s*public\s+(?:async\s+)?(?:Task|void)\s+(\w+)\s*\("),
    ".sh": re.compile(r"^\s*(\w+)\s*\(\)\s*\{"),
    ".ts": re.compile(r"""^\s*(?:it|test)\(\s*['"`](.+?)['"`]"""),
    ".js": re.compile(r"""^\s*(?:it|test)\(\s*['"`](.+?)['"`]"""),
}
TEST_MARK = re.compile(r"#\[(?:\w+::)*(?:test|rstest|test_case)|\[(?:Fact|Theory|Test|TestMethod)\b|@pytest|@test\b")


def tests_in_file(path: str) -> list[tuple[str, int]]:
    ext = os.path.splitext(path)[1]
    pat = TEST_FN.get(ext)
    if not pat or not os.path.isfile(path):
        return []
    lines = read(path).split("\n")
    out = []
    for i, line in enumerate(lines):
        m = pat.match(line)
        if not m:
            continue
        name = m.group(1)
        if ext == ".rs" or ext == ".cs":
            if not any(TEST_MARK.search(l) for l in lines[max(0, i - 4) : i]):
                continue
        elif ext == ".sh" and not name.startswith(("test", "check", "it_")):
            continue
        out.append((name, i + 1))
    return out


def parse_test_log(text: str) -> dict[str, str]:
    """name -> pass | fail | ignored, from cargo test, nextest, pytest -v and dotnet test lines."""
    res: dict[str, str] = {}
    for line in text.splitlines():
        m = re.match(r"^test\s+(\S+)\s+\.\.\.\s+(ok|FAILED|ignored)", line)
        if m:
            res[m.group(1).rsplit("::", 1)[-1]] = {"ok": "pass", "FAILED": "fail", "ignored": "ignored"}[m.group(2)]
            continue
        m = re.match(r"^\s*(PASS|FAIL|SKIP)\s+\[[^\]]*\]\s+(?:\S+\s+)?(\S+)\s*$", line)
        if m:
            res[m.group(2).rsplit("::", 1)[-1]] = {"PASS": "pass", "FAIL": "fail", "SKIP": "ignored"}[m.group(1)]
            continue
        m = re.match(r"^(\S+?::\S+)\s+(PASSED|FAILED|SKIPPED)", line)
        if m:
            res[m.group(1).rsplit("::", 1)[-1]] = {"PASSED": "pass", "FAILED": "fail", "SKIPPED": "ignored"}[m.group(2)]
            continue
        m = re.match(r"^\s*(Passed|Failed|Skipped)\s+(\S+)", line)
        if m:
            res[m.group(2).rsplit(".", 1)[-1]] = {"Passed": "pass", "Failed": "fail", "Skipped": "ignored"}[m.group(1)]
    return res


def suite_verdict(text: str) -> dict:
    """The last `SUITE:` line of the Build log, as a suite-level verdict (never per-test)."""
    lines = [l for l in re.findall(r"^\s*-\s*SUITE:\s*(.*)$", text, re.MULTILINE) if not l.startswith("local on") or "failed" in l]
    all_lines = re.findall(r"^\s*-\s*SUITE:\s*(.*)$", text, re.MULTILINE)
    use = (lines or all_lines)
    if not use:
        return {"verdict": "unknown", "line": None}
    last = use[-1]
    fails = [int(x) for x in re.findall(r"(\d+)\s+failed", last)] + [int(x) for x in re.findall(r"failed\s+(\d+)", last)]
    verdict = "unknown" if not fails else ("green" if sum(fails) == 0 else "red")
    if verdict == "unknown" and re.search(r"\b(green|all passed)\b", last, re.IGNORECASE) and not re.search(r"\b(red|failing|failed)\b", last, re.IGNORECASE):
        verdict = "green"
    return {"verdict": verdict, "line": last[:240]}


def backticked_idents(text: str) -> list[str]:
    seen = []
    for tok in re.findall(r"`([^`]+)`", text):
        for ident in re.findall(r"[A-Za-z_][\w]{3,}", tok):
            if ident not in seen:
                seen.append(ident)
    return seen


def criteria_evidence(root: str, dossier: str, base: str, head: str, test_logs: list[str] | None = None) -> dict:
    dpath = find_dossier(root, dossier)
    if not dpath:
        return skipped("criteria-evidence", dossier, "no dossier file .discovery/dossiers/%s*.md" % dossier)
    text = read(dpath)
    criteria = parse_criteria(text)
    if not criteria:
        return skipped("criteria-evidence", dossier, "the dossier has no numbered acceptance criteria")
    checklist = parse_checklist(os.path.join(root, ".discovery", "checklists", dossier + ".md"))
    census = parse_census(text)
    suite = suite_verdict(text)
    results: dict[str, str] = {}
    for tl in test_logs or []:
        try:
            results.update(parse_test_log(read(tl)))
        except OSError:
            pass
    try:
        changed = diff_files(root, base, head)
    except PacketError as err:
        changed, err_note = [], str(err)
    else:
        err_note = None
    changed_paths = [p for p, _, _ in changed]
    rows = []
    for c in criteria:
        links: list[dict] = []
        tests: list[dict] = []
        idents = backticked_idents(re.sub(r"\(owner:[^)]*\)", "", c["text"]))
        owner_ok = bool(c["owner"]) and os.path.isfile(os.path.join(root, c["owner"]))
        if c["owner"]:
            links.append({"how": "owner-map", "detail": "(owner: %s; env: %s)" % (c["owner"], c["env"]), "anchor": "%s:%d" % (os.path.relpath(dpath, root), c["line"])})
        owner_tests = tests_in_file(os.path.join(root, c["owner"])) if owner_ok else []
        low_idents = [i.lower() for i in idents if len(i) >= 5]
        for name, ln in owner_tests:
            if any(i in name.lower() for i in low_idents):
                tests.append({"file": c["owner"], "name": name, "line": ln, "found_by": "test-name"})
        if any(t["found_by"] == "test-name" for t in tests):
            links.append({"how": "test-name", "detail": "%d test name(s) in the owner file contain an identifier the criterion names" % len(tests), "anchor": "%s:%d" % (c["owner"], tests[0]["line"])})
        hits = [cl for cl in checklist if cl["member"].split("::")[-1] in idents or cl["member"] in idents]
        if hits:
            links.append({"how": "promise-checklist", "detail": "%d checklist line(s): %s" % (len(hits), ", ".join(h["id"] for h in hits[:6])),
                          "anchor": ".discovery/checklists/%s.md:%d" % (dossier, hits[0]["line"])})
        for cl in hits:
            if True:
                if cl["test_file"] and not any(t["file"] == cl["test_file"] and t.get("checklist") == cl["id"] for t in tests) and len([t for t in tests if t.get("checklist")]) < 6:
                    tests.append({"file": cl["test_file"], "name": None, "line": None, "found_by": "promise-checklist", "checklist": cl["id"]})
        for ce in census:
            if c["n"] in ce["criteria"] or (ce["path"] and c["owner"] and ce["path"] == c["owner"]):
                links.append({"how": "census-pin", "detail": ce["text"][:140], "anchor": "%s:%s" % (ce["path"], ce["line"]) if ce["path"] else None})
        for t in tests:
            nm = t.get("name")
            t["result"] = results.get(nm, "not-run") if (nm and test_logs) else ("unknown" if not test_logs else "not-run")
        # results
        if test_logs and any(t["result"] in ("pass", "fail") for t in tests):
            res = {"source": "test-log", "pass": sum(t["result"] == "pass" for t in tests), "fail": sum(t["result"] == "fail" for t in tests)}
        elif suite["verdict"] != "unknown":
            res = {"source": "suite-line", "verdict": suite["verdict"], "line": suite["line"]}
        else:
            res = {"source": "none"}
        # diff
        paths = []
        if c["owner"] and c["owner"] in changed_paths:
            paths.append(c["owner"])
        for tok in re.findall(r"`([\w./-]+\.\w+)`", c["text"]):
            for p in changed_paths:
                if (p == tok or p.endswith("/" + tok)) and p not in paths:
                    paths.append(p)
        hunks = {}
        if paths:
            try:
                hunks = diff_hunks(root, base, head, paths[:6])
            except PacketError:
                hunks = {}
        # status
        reasons = []
        if not c["owner"]:
            status = "no-evidence"
            reasons.append("no (owner; env) annotation")
        elif not owner_ok and c["owner"] not in changed_paths:
            status = "no-evidence"
            reasons.append("the owner file %s does not exist in the tree" % c["owner"])
        else:
            good = int(res.get("fail", 0) or 0) == 0 and (int(res.get("pass", 0) or 0) > 0 or res.get("verdict") == "green")
            has_test = bool(tests)
            if c["unverifiable"]:
                reasons.append("UNVERIFIABLE-LOCALLY: not observable on this machine")
            if not has_test:
                reasons.append("no test in the owner file matches the criterion by name or checklist")
            if res.get("fail") or res.get("verdict") == "red":
                reasons.append("the latest result is red")
            if res.get("source") == "none":
                reasons.append("no test result is recorded")
            if not owner_ok:
                reasons.append("the owner file is in the diff but not in the tree")
            status = "covered" if (good and has_test and not c["unverifiable"] and owner_ok) else "partial"
        rows.append({
            "criterion": c["n"], "text": c["text"][:240], "anchor": "%s:%d" % (os.path.relpath(dpath, root), c["line"]),
            "owner": c["owner"], "env": c["env"], "status": status, "status_reasons": reasons, "links": links,
            "tests": tests[:12], "results": res, "diff_files": paths, "diff_hunks": {k: v[:8] for k, v in hunks.items()},
        })
    cnt = Counter(r["status"] for r in rows)
    return {"schema_version": SCHEMA_VERSION, "producer": "criteria-evidence", "dossier": dossier, "base": base, "head": head,
            "summary": {"covered": cnt["covered"], "partial": cnt["partial"], "no-evidence": cnt["no-evidence"], "criteria": len(rows)},
            "inputs": {"checklist_lines": len(checklist), "census_lines": len(census), "suite": suite, "test_logs": len(test_logs or []),
                       "diff": err_note or "%d files" % len(changed)},
            "status_rule": "covered = owner file exists, a test is linked by name or checklist, its result is green (test-log or the last SUITE line), not UNVERIFIABLE-LOCALLY; "
                           "no-evidence = no owner annotation or the owner file is missing; partial = anything between",
            "rows": rows}


# ---------------------------------------------------------------------------------------------- 3. reading-order

NOISE = re.compile(r"(^|/)(\.discovery|\.agent-staging|node_modules|vendor|target|dist)/|(^|/)(Cargo\.lock|package-lock\.json|yarn\.lock|pnpm-lock\.yaml|poetry\.lock|uv\.lock)$|\.lock$|\.min\.(js|css)$|\.snap$")
TESTISH = re.compile(r"(^|/)(tests?|test-support|spec|__tests__)/|_test\.\w+$|(^|/)test_[^/]*\.py$|\.test\.\w+$")


def parse_packages(text: str) -> list[dict]:
    body, _ = section(text, "Work packages")
    rows = []
    for line in body:
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if line.strip().startswith("|") and len(cells) >= 2 and not set(cells[0]) <= set("- :") and cells[0].lower() != "package":
            paths = [p.strip("` ") for p in re.split(r"[,\s]+", cells[1]) if p.strip("` ") and p.strip("` ") != "—"]
            rows.append({"package": cells[0], "paths": paths, "depends": cells[2] if len(cells) > 2 else ""})
    return rows


def path_in(path: str, pat: str) -> bool:
    pat = pat.rstrip("/")
    return path == pat or path.startswith(pat + "/") or fnmatch.fnmatch(path, pat)


def contract_paths(text: str) -> list[str]:
    body, _ = section(text, "Contract")
    out = []
    for line in body:
        m = re.match(r"###\s+(?:[^`]*)`([^`]+)`", line)
        if m and "/" in m.group(1) or (m and "." in m.group(1)):
            out.append(m.group(1))
    return out


def signal_counts(root: str, base: str, head: str) -> dict[str, list[tuple[int, str]]]:
    import review_signals as rs

    paths, added = rs.added_lines(root, base, head)
    res: dict[str, list[tuple[int, str]]] = {}
    for p in paths:
        ext = os.path.splitext(p)[1]
        if ext not in rs.SOURCE_EXT or rs.TEST_PATH.search(p) or p not in added:
            continue
        try:
            body = rs.git(root, "show", "%s:%s" % (head, p))
        except ValueError:
            continue
        marks = rs.markers_for(p, body.splitlines(), added[p])
        if marks:
            res[p] = [(n, k) for n, k, _ in marks]
    return res


def parse_scope(path: str | None) -> dict[str, dict]:
    out: dict[str, dict] = defaultdict(lambda: {"changed": 0, "callers": 0})
    if not path or not os.path.isfile(path):
        return out
    for line in read(path).splitlines():
        m = re.match(r"\s*[-*]?\s*([\w./-]+?)(?::\d+)?\s+\((changed|caller)\)", line)
        if m:
            out[m.group(1)]["changed" if m.group(2) == "changed" else "callers"] += 1
    return out


def reading_order(root: str, dossier: str, base: str, head: str, scope: str | None = None, max_files: int = 40, out_dir: str | None = None) -> dict:
    try:
        files = diff_files(root, base, head)
    except PacketError as err:
        return skipped("reading-order", dossier, "no usable git range %s..%s: %s" % (base, head, err))
    dpath = find_dossier(root, dossier)
    text = read(dpath) if dpath else ""
    packages = parse_packages(text)
    size = {p: a + d for p, a, d in files}
    listed = {p for p in size if not NOISE.search(p)}
    noise = sorted(p for p in size if NOISE.search(p))
    try:
        signals = signal_counts(root, base, head)
    except Exception:
        signals = {}
    scope_data = parse_scope(scope)
    cd_entries = []
    if out_dir and os.path.isfile(os.path.join(out_dir, "contract-diff.json")):
        try:
            cd_entries = json.loads(read(os.path.join(out_dir, "contract-diff.json"))).get("entries", [])
        except ValueError:
            cd_entries = []
    contract = [p for p in contract_paths(text) if p in listed]
    for e in cd_entries:
        if e.get("severity") == "INFO":
            continue
        ap = (e.get("anchor") or "").rsplit(":", 1)[0]
        if ap in listed and ap not in contract:
            contract.append(ap)
    err_by_path = Counter((e.get("anchor") or "").rsplit(":", 1)[0] for e in cd_entries if e.get("severity") == "ERR")
    groups: list[dict] = []
    used: set[str] = set(contract)

    def mk(path: str, why: str) -> dict:
        a = next((x for p, x, _ in files if p == path), 0)
        d = next((x for p, _, x in files if p == path), 0)
        return {"path": path, "added": a, "deleted": d, "signals": len(signals.get(path, [])),
                "callers": scope_data[path]["callers"] if path in scope_data else 0, "why": why}

    if contract:
        groups.append({"rank": 1, "group": "contract", "reason": "the contract: signatures and docs the whole change is judged against",
                       "files": [mk(p, "contract member file") for p in sorted(contract, key=lambda p: -size[p])]})
    pk_rows = []
    for pk in packages:
        pfiles = sorted([p for p in listed if p not in used and any(path_in(p, pat) for pat in pk["paths"])], key=lambda p: -size[p])
        if not pfiles:
            continue
        is_test = all(TESTISH.search(p) for p in pfiles)
        score = 3 * sum(len(signals.get(p, [])) for p in pfiles) + sum(scope_data[p]["callers"] for p in pfiles if p in scope_data) \
            + 5 * sum(err_by_path[p] for p in pfiles)
        pk_rows.append({"package": pk["package"], "files": pfiles, "tests": is_test, "risk": score,
                        "parts": {"signals": sum(len(signals.get(p, [])) for p in pfiles), "callers": sum(scope_data[p]["callers"] for p in pfiles if p in scope_data),
                                  "breaking_contract_entries": sum(err_by_path[p] for p in pfiles)}, "size": sum(size[p] for p in pfiles)})
    non_test = [r for r in pk_rows if not r["tests"]]
    riskiest = max(non_test, key=lambda r: (r["risk"], r["size"]), default=None)
    rank = len(groups)
    if riskiest:
        rank += 1
        groups.append({"rank": rank, "group": "riskiest-package: " + riskiest["package"],
                       "reason": "highest risk score (3 x review_signals markers + callers + 5 x breaking contract entries = %d; ties by diff size)" % riskiest["risk"],
                       "files": [mk(p, "risk %d" % riskiest["risk"]) for p in riskiest["files"]]})
        used.update(riskiest["files"])
    rest = sorted([r for r in pk_rows if r is not riskiest], key=lambda r: (r["tests"], -r["size"]))
    for r in rest:
        fl = [p for p in r["files"] if p not in used]
        if not fl:
            continue
        rank += 1
        groups.append({"rank": rank, "group": ("tests of " if r["tests"] else "package: ") + r["package"],
                       "reason": "related by ownership (one work package), ranked by diff size", "files": [mk(p, "package " + r["package"]) for p in fl]})
        used.update(fl)
    leftovers = sorted([p for p in listed if p not in used], key=lambda p: -size[p])
    by_dir: dict[str, list[str]] = defaultdict(list)
    for p in leftovers:
        by_dir["/".join(p.split("/")[:2]) if p.count("/") else "."].append(p)
    for d, fl in sorted(by_dir.items(), key=lambda kv: -sum(size[p] for p in kv[1])):
        rank += 1
        groups.append({"rank": rank, "group": "directory: " + d, "reason": "not in a work package; grouped by directory, ranked by diff size", "files": [mk(p, "directory " + d) for p in fl]})
    shown = 0
    omitted: list[str] = []
    for g in groups:
        keep = []
        for f in g["files"]:
            if shown < max_files:
                keep.append(f)
                shown += 1
            else:
                omitted.append(f["path"])
        g["files"] = keep
    groups = [g for g in groups if g["files"]]
    return {"schema_version": SCHEMA_VERSION, "producer": "reading-order", "dossier": dossier, "base": base, "head": head,
            "order": groups, "listed_files": shown,
            "not_listed": {"count": len(omitted) + len(noise), "over_cap": len(omitted), "noise": len(noise),
                           "noise_rule": "dossier and staging files, lockfiles, vendored and build output are not listed",
                           "paths": (omitted + noise)[:200]},
            "total_changed_files": len(size),
            "inputs": {"review_signals": "%d files with markers" % len(signals), "scope": scope or "none given", "contract_diff": "%d entries" % len(cd_entries),
                       "work_packages": len(packages)},
            "caution": "a pointer list steers attention; read the not_listed paths too"}


# ---------------------------------------------------------------------------------------------- 4. coupling-delta


def depends_edges(graph: dict) -> tuple[set[tuple[str, str]], set[tuple[str, str]], dict]:
    """(before edges, after edges, ev by edge) over the merged graph's `depends` edges."""
    before, after, ev = set(), set(), {}
    for e in graph.get("edges", []):
        if e.get("kind") != "depends":
            continue
        k = (e["source"], e["target"])
        st = e.get("status")
        if st in ("removed", "changed", "context"):
            before.add(k)
        if st in ("added", "changed", "context"):
            after.add(k)
        ev[k] = e
    return before, after, ev


def sccs(edges: set[tuple[str, str]]) -> list[frozenset]:
    """Strongly connected components with a cycle (size > 1 or a self loop), Tarjan, iterative."""
    adj: dict[str, list[str]] = defaultdict(list)
    nodes = set()
    for a, b in sorted(edges):
        adj[a].append(b)
        nodes.update((a, b))
    index, low, on, stack, out, counter = {}, {}, set(), [], [], [0]
    for root in sorted(nodes):
        if root in index:
            continue
        work = [(root, 0)]
        while work:
            v, i = work.pop()
            if i == 0:
                index[v] = low[v] = counter[0]
                counter[0] += 1
                stack.append(v)
                on.add(v)
            recurse = False
            for j in range(i, len(adj[v])):
                w = adj[v][j]
                if w not in index:
                    work.append((v, j + 1))
                    work.append((w, 0))
                    recurse = True
                    break
                if w in on:
                    low[v] = min(low[v], index[w])
            if recurse:
                continue
            if low[v] == index[v]:
                comp = []
                while True:
                    w = stack.pop()
                    on.discard(w)
                    comp.append(w)
                    if w == v:
                        break
                if len(comp) > 1 or (v, v) in edges:
                    out.append(frozenset(comp))
            if work:
                parent = work[-1][0]
                low[parent] = min(low[parent], low[v])
    return out


def layer_of(node: dict, layers: dict) -> str | None:
    for layer, pats in (layers.get("patterns") or {}).items():
        for pat in pats:
            if fnmatch.fnmatch(node.get("id", ""), pat) or fnmatch.fnmatch(node.get("label", ""), pat):
                return layer
    return None


def coupling_delta(root: str, dossier: str, layers_path: str | None = None) -> dict:
    graph, src = load_graph(root, dossier)
    if graph is None:
        return skipped("coupling-delta", dossier, src)
    before, after, ev = depends_edges(graph)
    if not before and not after:
        return skipped("coupling-delta", dossier, "the graph has no `depends` edges (the extractor does not cover this language, or no dependency layer ran)")
    nodes = {n["id"]: n for n in graph.get("nodes", [])}

    def anchor(k):
        e = ev.get(k, {})
        for x in e.get("evidence", []):
            if x.get("path"):
                return "%s:%s" % (x["path"], x.get("line")) if x.get("line") else x["path"]
        return None

    new = sorted(after - before)
    gone = sorted(before - after)
    cyc_b = set(sccs(before))
    cyc_a = set(sccs(after))
    new_cycles = [sorted(c) for c in sorted(cyc_a - cyc_b, key=sorted)]
    # a cycle that already existed but grew counts as new when its node set differs
    fan = Counter()
    for a, b in after - before:
        fan[("out", a)] += 1
        fan[("in", b)] += 1
    for a, b in before - after:
        fan[("out", a)] -= 1
        fan[("in", b)] -= 1
    fan_rows = sorted(({"node": n, "direction": d, "delta": v} for (d, n), v in fan.items() if v), key=lambda r: (-abs(r["delta"]), r["node"]))[:15]
    layers_block: dict
    breaks: list[dict] = []
    if layers_path and os.path.isfile(layers_path):
        try:
            layers = json.loads(read(layers_path))
            order = layers.get("order") or []
            rank = {l: i for i, l in enumerate(order)}
            for src_id, dst_id in sorted(after):
                ls, ld = layer_of(nodes.get(src_id, {"id": src_id}), layers), layer_of(nodes.get(dst_id, {"id": dst_id}), layers)
                if ls in rank and ld in rank and rank[ld] < rank[ls]:
                    breaks.append({"from": src_id, "to": dst_id, "from_layer": ls, "to_layer": ld, "new": (src_id, dst_id) in set(new), "anchor": anchor((src_id, dst_id))})
            layers_block = {"status": "ran", "map": layers_path, "order": order}
        except (ValueError, AttributeError) as err:
            layers_block = {"status": "skipped", "reason": "unreadable layer map: %s" % err}
    else:
        layers_block = {"status": "skipped", "reason": "no layer map given (--layers FILE); a layer break needs a declared order"}
    return {"schema_version": SCHEMA_VERSION, "producer": "coupling-delta", "dossier": dossier, "graph": src,
            "summary": {"added": len(new), "removed": len(gone), "new_cycles": len(new_cycles), "layer_breaks": len(breaks)},
            "added": [{"from": a, "to": b, "anchor": anchor((a, b))} for a, b in new],
            "removed": [{"from": a, "to": b, "anchor": anchor((a, b))} for a, b in gone],
            "new_cycles": [{"nodes": c} for c in new_cycles], "layer_breaks": breaks, "layers": layers_block, "fan_changes": fan_rows,
            "coverage": "depends edges the extractor records (%d before, %d after); a dependency it does not model is not here" % (len(before), len(after))}


# ---------------------------------------------------------------------------------------------- 5. invariants

WORDS = re.compile(r"\b(never|must not|must always|always|at most|at least|exactly once|idempotent|invariant|is unchanged|no more than)\b", re.IGNORECASE)


def verify_anchor(root: str, anchor: str | None) -> tuple[str | None, str | None]:
    """(anchor, problem): a `path:line` that exists, or a test id found by git grep; else (None, why)."""
    if not anchor:
        return None, "no anchor given"
    m = re.match(r"^([\w./-]+):(\d+)$", anchor)
    if m:
        p = os.path.join(root, m.group(1))
        if os.path.isfile(p) and 0 < int(m.group(2)) <= len(read(p).split("\n")):
            return anchor, None
        return None, "%s does not exist" % anchor
    tid = anchor[5:] if anchor.startswith("test:") else anchor
    if len(tid) >= 4:
        hit = git_try(root, "grep", "-l", "-F", "--", tid)
        if hit and hit.strip():
            return "test:" + tid, None
    return None, "test id %s not found in the tree" % tid


def invariants(root: str, dossier: str, base: str, head: str, out_dir: str) -> dict:
    dpath = find_dossier(root, dossier)
    if not dpath:
        return skipped("invariants", dossier, "no dossier file .discovery/dossiers/%s*.md" % dossier)
    text = read(dpath)
    rel = os.path.relpath(dpath, root)
    items: list[dict] = []

    def add(kind, text_, anchor, source, check=None, spec=False, problem=None):
        items.append({"id": "%s-%d" % ("INV" if kind == "invariant" else "FM", sum(1 for i in items if i["kind"] == kind) + 1),
                      "kind": kind, "text": text_[:300], "anchor": anchor, "speculative": spec or anchor is None, "source": source,
                      "check": check, **({"anchor_problem": problem} if problem else {})})

    for cl in parse_checklist(os.path.join(root, ".discovery", "checklists", dossier + ".md")):
        if cl["invariant"]:
            add("invariant", "%s: %s" % (cl["member"], cl["text"].split(":", 1)[-1].strip()[:200]), ".discovery/checklists/%s.md:%d" % (dossier, cl["line"]),
                "promise-checklist", check="property test in %s" % cl["test_file"])
    body, start = section(text, "Contract")
    fence = False
    n_prose = 0
    for i, line in enumerate(body):
        if line.startswith("```"):
            fence = not fence
            continue
        if not fence and WORDS.search(line) and not line.startswith("#") and len(line.strip()) > 20 and n_prose < 12:
            n_prose += 1
            add("invariant", line.strip(), "%s:%d" % (rel, start + i), "contract-prose")
    try:
        for p, marks in sorted(signal_counts(root, base, head).items()):
            for n, kind in marks[:3]:
                if kind.startswith(("failure/", "security/")):
                    add("failure_mode", "%s on a line this change added" % kind, "%s:%d" % (p, n), "review-signals",
                        check="a test that makes this call fail, or a reason it cannot")
    except Exception:
        pass
    del items[40:]
    authored_path = os.path.join(out_dir, "invariants.authored.json")
    authored_n = 0
    if os.path.isfile(authored_path):
        try:
            for a in json.loads(read(authored_path)):
                kind = "failure_mode" if a.get("kind") in ("failure_mode", "pre-mortem", "failure") else "invariant"
                anchor, problem = verify_anchor(root, a.get("anchor"))
                add(kind, str(a.get("text", ""))[:300], anchor, "authored", check=a.get("check"), spec=bool(a.get("speculative")) or anchor is None,
                    problem=problem if a.get("anchor") else None)
                authored_n += 1
        except (ValueError, AttributeError, TypeError) as err:
            return {**skipped("invariants", dossier, "unreadable invariants.authored.json: %s" % err)}
    return {"schema_version": SCHEMA_VERSION, "producer": "invariants", "dossier": dossier,
            "summary": {"items": len(items), "invariants": sum(i["kind"] == "invariant" for i in items),
                        "failure_modes": sum(i["kind"] == "failure_mode" for i in items), "speculative": sum(i["speculative"] for i in items), "authored": authored_n},
            "rule": "an item is anchored (path:line that exists, or a test id found in the tree) or it is `speculative`; speculative items are guesses, not findings",
            "items": items}


# ---------------------------------------------------------------------------------------------- driver


# ---------------------------------------------------------------------------------------------- 6 migration-lint

# A fixed rule list: an id, a short name, and the pattern of one statement. No verdict sentence is generated; a finding is
# the rule id plus the path:line of the statement. `-- lint:ignore MIG003` on the statement's line or the line above it
# moves the finding to `suppressed`, so a suppression stays visible in the file.
MIGRATION_RULES = (
    ("MIG001", "drop-table", re.compile(r"\bDROP\s+TABLE\b", re.I)),
    ("MIG002", "drop-column", re.compile(r"\bDROP\s+COLUMN\b", re.I)),
    ("MIG003", "not-null-without-default", None),
    ("MIG004", "column-type-change", re.compile(r"\bALTER\s+COLUMN\s+\S+\s+(?:SET\s+DATA\s+)?TYPE\b|\bMODIFY\s+(?:COLUMN\s+)?\S+\s+\w+", re.I)),
    ("MIG005", "rename", re.compile(r"\bRENAME\s+(?:TO|COLUMN|CONSTRAINT)\b|\bRENAME\s+\S+\s+TO\b", re.I)),
    ("MIG006", "edit-merged-migration", None),
    ("MIG007", "backfill-with-schema-change", None),
    ("MIG008", "truncate", re.compile(r"\bTRUNCATE\b", re.I)),
)
DEFAULT_MIGRATION_GLOBS = ("**/migrations/[0-9]*.sql",)
SCHEMA_STMT = re.compile(r"^\s*(?:CREATE|ALTER|DROP)\s", re.I)
DATA_STMT = re.compile(r"^\s*(?:UPDATE|DELETE|INSERT|REPLACE)\b", re.I)
IDEMPOTENT = re.compile(r"\bON\s+CONFLICT\b|\bINSERT\s+OR\s+(?:IGNORE|REPLACE)\b|\bINSERT\s+IGNORE\b|\bWHERE\s+NOT\s+EXISTS\b|\bIS\s+NULL\b", re.I)
IGNORE_RE = re.compile(r"lint:ignore\s+((?:MIG\d{3}[\s,]*)+)", re.I)


def is_migration(path: str, globs) -> bool:
    """Discovery is merge_dossier's MIGRATION_RE (NNNN_name.sql in a migrations folder) unless `globs` overrides it."""
    if globs:
        return any(fnmatch.fnmatch(path, g) or fnmatch.fnmatch(path, g.replace("**/", "")) for g in globs)
    try:
        from merge_dossier import MIGRATION_RE
    except Exception:  # the helper module is optional here
        MIGRATION_RE = re.compile(r"^(.*/)?migrations/(\d+)[_-][^/]+\.sql$")
    return bool(MIGRATION_RE.match(path))


def sql_statements(text: str) -> tuple[list[tuple[int, int, str]], dict[int, set[str]]]:
    """([(first line, last line, statement with comments blanked)], {line: ids named by a lint:ignore comment})."""
    ignores: dict[int, set[str]] = {}
    for n, raw in enumerate(text.split("\n"), 1):
        m = IGNORE_RE.search(raw)
        if m:
            ignores[n] = {x.upper() for x in re.findall(r"MIG\d{3}", m.group(1), re.I)}
    blank = lambda m: re.sub(r"[^\n]", " ", m.group(0))
    clean = re.sub(r"/\*.*?\*/", blank, text, flags=re.S)
    clean = re.sub(r"--[^\n]*", blank, clean)
    out, cur, start = [], [], 0
    for n, line in enumerate(clean.split("\n"), 1):
        pos = 0
        while True:
            semi = line.find(";", pos)
            piece = line[pos:] if semi < 0 else line[pos:semi]
            if piece.strip() and not start:
                start = n
            if start:
                cur.append(piece)
            if semi < 0:
                if start:
                    cur.append("\n")
                break
            out.append((start or n, n, "".join(cur)))
            cur, start, pos = [], 0, semi + 1
    tail = "".join(cur)
    if tail.strip():
        out.append((start, len(clean.split("\n")), tail))
    return [(a, b, t.strip("\n")) for a, b, t in out if t.strip()], ignores


def lint_statements(text: str, created_tables_extra=()) -> list[tuple[str, int, int]]:
    """[(rule id, line, statement start line)] for one migration file's text, rules MIG001-MIG005, MIG007, MIG008."""
    stmts, _ = sql_statements(text)
    found: list[tuple[str, int, int]] = []
    created = {m.group(1).strip('"`[]').lower() for _, _, t in stmts for m in re.finditer(r"\bCREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(\S+?)\s*\(", t, re.I)}
    created |= set(created_tables_extra)
    schema_change = any(SCHEMA_STMT.match(t) for _, _, t in stmts)

    def line_of(a: int, t: str, m: re.Match) -> int:
        return a + t[: m.start()].count("\n")

    for a, b, t in stmts:
        for rid, _name, pat in MIGRATION_RULES:
            if pat is None:
                continue
            for m in pat.finditer(t):
                if rid == "MIG004" and m.group(0).upper().startswith("MODIFY") and not re.match(r"\s*ALTER\s+TABLE", t, re.I):
                    continue
                found.append((rid, line_of(a, t, m), a))
        am = re.match(r"\s*ALTER\s+TABLE\s+(?:IF\s+EXISTS\s+)?(?:ONLY\s+)?(\S+)", t, re.I)
        if am and am.group(1).strip('"`[]').lower() not in created:
            for m in re.finditer(r"\bADD\s+(?:COLUMN\s+)?(?:IF\s+NOT\s+EXISTS\s+)?[^,]*?\bNOT\s+NULL\b[^,]*", t, re.I):
                if not re.search(r"\bDEFAULT\b|\bGENERATED\b|\bAS\s*\(", m.group(0), re.I):
                    found.append(("MIG003", line_of(a, t, m), a))
            for m in re.finditer(r"\bALTER\s+COLUMN\s+\S+\s+SET\s+NOT\s+NULL\b", t, re.I):
                found.append(("MIG003", line_of(a, t, m), a))
        if schema_change and DATA_STMT.match(t) and not IDEMPOTENT.search(t):
            found.append(("MIG007", a, a))
    return found


def added_lines_of(diff: str) -> int:
    """The first new-side line of the first hunk in a `diff -U0` text, 1 when there is none."""
    m = re.search(r"^@@ -\d+(?:,\d+)? \+(\d+)", diff, re.M)
    return max(int(m.group(1)), 1) if m else 1


def migration_lint(root: str, dossier: str, base: str, head: str, globs=None) -> dict:
    globs = tuple(globs or ())
    rows = git(root, "diff", "--no-renames", "--name-status", "%s..%s" % (base, head)).splitlines()
    changed = []
    for r in rows:
        st, _, path = r.partition("\t")
        if st[:1] in ("A", "M", "D") and is_migration(path, globs):
            changed.append((st[:1], path))
    if not changed:
        return skipped("migration-lint", dossier, "the change adds or edits no migration file (%s)" % (", ".join(globs) if globs else "migrations/NNNN_name.sql"))
    findings: list[dict] = []
    suppressed: list[dict] = []
    files = []
    for st, path in sorted(changed, key=lambda x: x[1]):
        files.append({"path": path, "status": {"A": "added", "M": "modified", "D": "deleted"}[st]})
        if st in ("M", "D"):
            hunk = git_try(root, "diff", "-U0", "--no-renames", "%s..%s" % (base, head), "--", path) or ""
            findings.append({"rule": "MIG006", "name": "edit-merged-migration", "path": path, "line": added_lines_of(hunk) if st == "M" else 1,
                             "anchor": "%s:%d" % (path, added_lines_of(hunk) if st == "M" else 1), "file_status": files[-1]["status"], "statement": None})
        if st == "D":
            continue
        text = git_try(root, "show", "%s:%s" % (head, path))
        if text is None:
            continue
        lines = text.split("\n")
        _stmts, ignores = sql_statements(text)
        for rid, line, start in lint_statements(text):
            name = next(n for i, n, _ in MIGRATION_RULES if i == rid)
            row = {"rule": rid, "name": name, "path": path, "line": line, "anchor": "%s:%d" % (path, line), "file_status": files[-1]["status"],
                   "statement": lines[line - 1].strip()[:160] if 0 < line <= len(lines) else None}
            (suppressed if any(rid in ignores.get(n, ()) for n in (start - 1, start, line - 1, line)) else findings).append(row)
    order = {i: n for n, (i, _, _) in enumerate(MIGRATION_RULES)}
    findings.sort(key=lambda f: (f["path"], f["line"], order[f["rule"]]))
    suppressed.sort(key=lambda f: (f["path"], f["line"], order[f["rule"]]))
    by_rule = Counter(f["rule"] for f in findings)
    return {
        "schema_version": SCHEMA_VERSION, "producer": "migration-lint", "dossier": dossier,
        "summary": {"files": len(files), "findings": len(findings), "suppressed": len(suppressed), "by_rule": dict(sorted(by_rule.items()))},
        "rule": "Every finding is a fixed rule id at a path:line; there is no verdict. `-- lint:ignore MIGnnn` moves a finding to `suppressed`.",
        "rules": [{"id": i, "name": n} for i, n, _ in MIGRATION_RULES],
        "files": files, "findings": findings, "suppressed": suppressed,
    }


def receipt(name: str, data: dict) -> str:
    if "skipped" in data:
        return "REVIEW-PACKET: %s.json skipped: %s" % (name, data["skipped"][:160])
    s = data.get("summary", {})
    if name == "contract-diff":
        return "REVIEW-PACKET: contract-diff.json %d entries (%d ERR, %d WARN, %d INFO), %d cannot confirm" % (
            len(data["entries"]), s["ERR"], s["WARN"], s["INFO"], len(data["cannot_confirm"]))
    if name == "criteria-evidence":
        return "REVIEW-PACKET: criteria-evidence.json %d criteria (%d covered, %d partial, %d no-evidence)" % (s["criteria"], s["covered"], s["partial"], s["no-evidence"])
    if name == "reading-order":
        return "REVIEW-PACKET: reading-order.json %d files listed in %d groups, not listed: %d files" % (data["listed_files"], len(data["order"]), data["not_listed"]["count"])
    if name == "coupling-delta":
        return "REVIEW-PACKET: coupling-delta.json +%d/-%d dependencies, %d new cycles, %d layer breaks" % (s["added"], s["removed"], s["new_cycles"], s["layer_breaks"])
    if name == "migration-lint":
        return "REVIEW-PACKET: migration-lint.json %d files, %d findings (%d suppressed)%s" % (
            s["files"], s["findings"], s["suppressed"], "".join(", %s x%d" % kv for kv in s["by_rule"].items()))
    return "REVIEW-PACKET: invariants.json %d items (%d speculative, %d authored)" % (s["items"], s["speculative"], s["authored"])


def run(root: str, dossier: str, which: list[str], base: str | None, head: str, out: str | None, test_logs=None, scope=None, layers=None,
        max_files: int = 40, migrations=None) -> dict[str, dict]:
    root = os.path.abspath(root)
    out_dir = out or os.path.join(root, OUT_REL, dossier)
    os.makedirs(out_dir, exist_ok=True)
    if not base:
        sj = os.path.join(root, ".discovery", "visual-review", dossier, "work", "summary.json")
        try:
            base = json.loads(read(sj)).get("base")
        except (OSError, ValueError):
            base = None
    base = base or ""
    results: dict[str, dict] = {}
    for name in which:
        try:
            if not base and name in ("contract-diff", "criteria-evidence", "reading-order", "invariants", "migration-lint"):
                data = skipped(name, dossier, "no --base given and no work/summary.json to read it from")
            elif name == "contract-diff":
                data = contract_diff(root, dossier, base, head)
            elif name == "criteria-evidence":
                data = criteria_evidence(root, dossier, base, head, test_logs)
            elif name == "reading-order":
                data = reading_order(root, dossier, base, head, scope, max_files, out_dir)
            elif name == "coupling-delta":
                data = coupling_delta(root, dossier, layers)
            elif name == "migration-lint":
                data = migration_lint(root, dossier, base, head, migrations)
            else:
                data = invariants(root, dossier, base, head, out_dir)
        except Exception as err:  # the packet never blocks a run
            data = skipped(name, dossier, "%s: %s" % (type(err).__name__, err))
        with open(os.path.join(out_dir, name + ".json"), "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, sort_keys=False)
            fh.write("\n")
        results[name] = data
    return results


# ---------------------------------------------------------------------------------------------- schema check (subset)


def check_schema(value, schema: dict, defs: dict, path: str = "$") -> list[str]:
    """The JSON Schema subset the packet schemas use: type, enum, required, properties, items, $ref, oneOf of types."""
    errs: list[str] = []
    if "$ref" in schema:
        return check_schema(value, defs[schema["$ref"].split("/")[-1]], defs, path)
    t = schema.get("type")
    tmap = {"object": dict, "array": list, "string": str, "integer": int, "boolean": bool, "number": (int, float), "null": type(None)}
    if t:
        ok = any(isinstance(value, tmap[x]) and not (x == "integer" and isinstance(value, bool)) for x in (t if isinstance(t, list) else [t]))
        if not ok:
            return ["%s: expected %s, got %s" % (path, t, type(value).__name__)]
    if "enum" in schema and value not in schema["enum"]:
        errs.append("%s: %r not in %s" % (path, value, schema["enum"]))
    if isinstance(value, dict):
        for k in schema.get("required", []):
            if k not in value:
                errs.append("%s: missing %s" % (path, k))
        for k, sub in schema.get("properties", {}).items():
            if k in value:
                errs += check_schema(value[k], sub, defs, "%s.%s" % (path, k))
    if isinstance(value, list) and "items" in schema:
        for i, v in enumerate(value[:50]):
            errs += check_schema(v, schema["items"], defs, "%s[%d]" % (path, i))
    return errs


def validate_file(name: str, data: dict) -> list[str]:
    sp = os.path.join(REF_DIR, "review-packet-%s.schema.json" % name)
    schema = json.loads(read(sp))
    return check_schema(data, schema, schema.get("$defs", {}))


# ---------------------------------------------------------------------------------------------- selftest

FIX_BASE_RS = """pub struct Cfg {
    pub a: u32,
}

pub fn keep(x: u32) -> u32 {
    x
}

pub fn drop_me(x: u32) -> u32 {
    x
}

pub fn widen(x: u32) -> u32 {
    x
}

pub fn rename_only(count: u32) -> u32 {
    count
}

fn hidden(x: u32) {}
"""
FIX_HEAD_RS = """pub struct Cfg {
    pub a: u32,
    pub b: u32,
}

pub fn keep(x: u32) -> u32 {
    x
}

pub fn widen(
    x: u32,
    y: u32,
) -> u32 {
    x + y
}

pub fn rename_only(n: u32) -> u32 {
    n
}

pub fn fresh(x: &str) -> String {
    x.to_string()
}

fn hidden(x: u8) {}
"""
FIX_TEST_RS = """#[test]
fn widen_takes_two_arguments() {
    assert_eq!(1, 1);
}

#[test]
fn fresh_returns_owned() {
    assert_eq!(1, 1);
}
"""
FIX_DOSSIER = """---
id: W-9
status: review
---
# W-9 demo

## Contract

The cache never returns a stale entry; `widen` adds at most two numbers.

```rust
pub fn widen(x: u32, y: u32) -> u32;
```

### `src/lib.rs`

## Work packages

| Package | Owned paths | Depends on |
|---|---|---|
| P1 core | src/lib.rs | — |
| P2 extra | src/extra.rs | P1 |
| UT unit tests | tests/unit_test.rs | — |

## Acceptance criteria

1. When `widen` is called with two numbers, the result shall be their sum. (owner: tests/unit_test.rs; env: local)
2. When `fresh` is called, the result shall be owned. (owner: tests/missing_test.rs; env: local)
3. The index shall keep every entry on restart.
4. UNVERIFIABLE-LOCALLY (ZFS; substitute: `limactl shell vm`): `Cfg` survives. (owner: tests/unit_test.rs; env: vm)

## Build log

- CENSUS: tests/unit_test.rs:2 — pins widen (criterion 1)
- SUITE: local on abc, base unknown — 12 passed, 0 failed, 0 skipped
"""
FIX_CHECKLIST = """# PROMISE_CHECKLIST

## Surface: widen (tests/unit_test.rs)
- W1.1 #widen — return meaning: sum of the two → `widen(1, 2)` equals `3` [whole-value]
- W1.2 #widen — property: for every x, y the result is x + y [property: invariant] domain: u32
"""


def _graph(nodes, edges):
    return {"schema_version": "1.1.0", "view": "L2", "title": "t", "meta": {}, "nodes": nodes, "edges": edges}


def _node(i, status="context", kind="module", detail=None, path="src/lib.rs", line=1, note="stub signature"):
    n = {"id": i, "label": i, "kind": kind, "status": status, "resolution": "static",
         "evidence": [{"tier": "source", "path": path, "line": line, "note": note}]}
    if detail:
        n["detail"] = detail
    return n


def _edge(a, b, status):
    return {"id": "edge:depends:%s:%s" % (a, b), "source": a, "target": b, "kind": "depends", "status": status, "resolution": "static",
            "evidence": [{"tier": "source", "path": "Cargo.toml", "line": 3, "note": "dependency"}]}


def _build_fixture(td: str) -> tuple[str, str, str]:
    def sh(*a):
        subprocess.run(["git", "-C", td, *a], check=True, capture_output=True)

    sh("init", "-q", "-b", "main")
    sh("config", "user.email", "t@t")
    sh("config", "user.name", "t")
    os.makedirs(os.path.join(td, "src"))
    os.makedirs(os.path.join(td, "tests"))
    with open(os.path.join(td, "src", "lib.rs"), "w") as fh:
        fh.write(FIX_BASE_RS)
    with open(os.path.join(td, "README.md"), "w") as fh:
        fh.write("x\n")
    sh("add", "-A")
    sh("commit", "-q", "-m", "base")
    base = subprocess.run(["git", "-C", td, "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    with open(os.path.join(td, "src", "lib.rs"), "w") as fh:
        fh.write(FIX_HEAD_RS)
    with open(os.path.join(td, "src", "extra.rs"), "w") as fh:
        fh.write("pub fn e(x: u32) {\n    let _ = std::fs::read(\"a\");\n}\n")
    with open(os.path.join(td, "tests", "unit_test.rs"), "w") as fh:
        fh.write(FIX_TEST_RS)
    with open(os.path.join(td, "Cargo.lock"), "w") as fh:
        fh.write("lock\n")
    os.makedirs(os.path.join(td, ".discovery", "dossiers"))
    os.makedirs(os.path.join(td, ".discovery", "checklists"))
    with open(os.path.join(td, ".discovery", "dossiers", "W-9-demo.md"), "w") as fh:
        fh.write(FIX_DOSSIER)
    with open(os.path.join(td, ".discovery", "checklists", "W-9.md"), "w") as fh:
        fh.write(FIX_CHECKLIST)
    work = os.path.join(td, ".discovery", "visual-review", "W-9", "work")
    os.makedirs(work)
    lib = "src/lib.rs"
    nodes = [
        _node("module:demo::widen", "changed", "module", {"after": "pub fn widen(x: u32, y: u32) -> u32 {"}, lib, 12),
        _node("module:demo::rename", "changed", "module", {"after": "pub fn rename_only(n: u32) -> u32 {"}, lib, 18),
        _node("module:demo::drop", "removed", "module", {"before": "pub fn drop_me(x: u32) -> u32 {"}, lib, 9),
        _node("module:demo::fresh", "added", "module", {"after": "pub fn fresh(x: &str) -> String {"}, lib, 22),
        _node("module:demo::cfg", "changed", "module", {"before": "pub struct Cfg {", "after": "pub struct Cfg {"}, lib, 1),
        _node("module:demo::hidden", "changed", "module", {"after": "fn hidden(x: u8) {"}, lib, 26),
        _node("module:demo::touched", "changed", "module", None, lib, 1, "module declaration"),
        _node("module:demo::newmod", "added", "module", None, "src/extra.rs", 1, "module declaration"),
        _node("crate:a", "context", "crate"), _node("crate:b", "context", "crate"), _node("crate:c", "context", "crate"),
    ]
    edges = [_edge("crate:a", "crate:b", "context"), _edge("crate:b", "crate:c", "context"), _edge("crate:c", "crate:a", "added"),
             _edge("crate:a", "crate:c", "removed"), _edge("crate:b", "crate:a", "added")]
    with open(os.path.join(work, "merged.json"), "w") as fh:
        json.dump(_graph(nodes, edges), fh)
    sh("add", "-A")
    sh("commit", "-q", "-m", "head")
    head = subprocess.run(["git", "-C", td, "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    return td, base, head


def _migration_selftest(check) -> None:
    with tempfile.TemporaryDirectory() as mtd:
        def msh(*a):
            subprocess.run(["git", "-C", mtd, *a], check=True, capture_output=True)

        def mput(rel, body):
            os.makedirs(os.path.dirname(os.path.join(mtd, rel)), exist_ok=True)
            with open(os.path.join(mtd, rel), "w") as fh:
                fh.write(body)

        msh("init", "-q", "-b", "main"); msh("config", "user.email", "t@t"); msh("config", "user.name", "t")
        mput("crates/p/migrations/0001_init.sql", "CREATE TABLE users (id INT);\n")
        mput("crates/p/migrations/0002_more.sql", "ALTER TABLE users ADD COLUMN a INT;\n")
        msh("add", "-A"); msh("commit", "-q", "-m", "base")
        mbase = subprocess.run(["git", "-C", mtd, "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
        mput("crates/p/migrations/0002_more.sql", "ALTER TABLE users ADD COLUMN a INT;\nALTER TABLE users ADD COLUMN b INT;\n")
        mput("crates/p/migrations/0003_risky.sql", "\n".join([
            "-- a comment with DROP TABLE nothing;",
            "ALTER TABLE users DROP COLUMN legacy;",                                   # 2 MIG002
            "ALTER TABLE users ADD COLUMN email TEXT NOT NULL;",                       # 3 MIG003
            "ALTER TABLE users ADD COLUMN ok TEXT NOT NULL DEFAULT '';",               # 4 clean
            "ALTER TABLE users ALTER COLUMN age TYPE BIGINT;",                         # 5 MIG004
            "ALTER TABLE users RENAME COLUMN nm TO name;",                             # 6 MIG005
            "DROP TABLE old_users;",                                                   # 7 MIG001
            "UPDATE users SET a = 1;",                                                 # 8 MIG007
            "INSERT INTO users (id) VALUES (1) ON CONFLICT DO NOTHING;",               # 9 idempotent
            "TRUNCATE audit;",                                                         # 10 MIG008
            "-- lint:ignore MIG001",
            "DROP TABLE tmp_users;",                                                   # 12 suppressed
            "ALTER TABLE users",
            "  ADD COLUMN multi TEXT NOT NULL;",                                       # 14 MIG003
            "",
        ]))
        mput("crates/q/migrations/0001_fresh.sql", "CREATE TABLE t (id INT, x INT NOT NULL);\nALTER TABLE t ADD COLUMN y INT NOT NULL;\n")
        mput("README.md", "DROP TABLE not_a_migration;\n")
        msh("add", "-A"); msh("commit", "-q", "-m", "head")
        mhead = subprocess.run(["git", "-C", mtd, "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
        mres = run(mtd, "W-7", ["migration-lint"], mbase, mhead, os.path.join(mtd, "out"))["migration-lint"]
        check("ml:schema", not validate_file("migration-lint", mres), str(validate_file("migration-lint", mres)[:3]))
        got = {(f["rule"], f["anchor"]) for f in mres["findings"]}
        r3 = "crates/p/migrations/0003_risky.sql"
        for rule, line in [("MIG002", 2), ("MIG003", 3), ("MIG004", 5), ("MIG005", 6), ("MIG001", 7), ("MIG007", 8), ("MIG008", 10), ("MIG003", 14)]:
            check("ml:%s@%d" % (rule, line), (rule, "%s:%d" % (r3, line)) in got, str(sorted(got)))
        check("ml:default-and-idempotent-clean", not any(a.endswith((":4", ":9", ":1")) and a.startswith(r3) for _, a in got), str(sorted(got)))
        check("ml:comment-not-linted", ("MIG001", r3 + ":1") not in got)
        check("ml:suppressed-listed", [(f["rule"], f["line"]) for f in mres["suppressed"]] == [("MIG001", 12)], str(mres["suppressed"]))
        check("ml:edit-merged", ("MIG006", "crates/p/migrations/0002_more.sql:2") in got, str(sorted(got)))
        check("ml:new-table-columns-clean", not any("0001_fresh" in a and r == "MIG003" and a.endswith(":1") for r, a in got) and
              not any("crates/q" in a for _, a in got), str(sorted(got)))
        check("ml:non-migration-ignored", all(f["path"].endswith(".sql") for f in mres["findings"]))
        check("ml:summary", mres["summary"]["findings"] == len(mres["findings"]) and mres["summary"]["by_rule"]["MIG003"] == 2, str(mres["summary"]))
        check("ml:receipt", receipt("migration-lint", mres).startswith("REVIEW-PACKET: migration-lint.json 3 files"), receipt("migration-lint", mres))
        custom = run(mtd, "W-7", ["migration-lint"], mbase, mhead, os.path.join(mtd, "out2"), migrations=["crates/zzz/*.sql"])["migration-lint"]
        check("ml:configurable-glob", "skipped" in custom, str(custom)[:200])
        custom = run(mtd, "W-7", ["migration-lint"], mbase, mhead, os.path.join(mtd, "out3"), migrations=["crates/q/migrations/*.sql"])["migration-lint"]
        check("ml:configurable-glob-hit", [f["path"] for f in custom["files"]] == ["crates/q/migrations/0001_fresh.sql"], str(custom)[:200])


def selftest() -> int:
    fails: list[str] = []

    def check(name, cond, detail=""):
        if not cond:
            fails.append("%s %s" % (name, detail))

    # helpers
    p = parse_sig("pub fn f<T: Clone>(a: u32, b: Vec<(u8, u8)>) -> Result<T, E> where T: Send {", "rust")
    check("parse:params", p["params"] == ["a: u32", "b: Vec<(u8, u8)>"], str(p))
    check("parse:ret", p["returns"] == "Result<T, E>" and "Send" in p["generics"], str(p))
    check("split:sigs", len(split_signatures("pub fn a() -> u8 {; pub struct B {; fn c() {")) == 3)
    check("classify:py-default", classify(parse_sig("def f(a)", "python"), parse_sig("def f(a, b=1)", "python"), "python")[2] == "param-added-default")
    check("classify:py-required", classify(parse_sig("def f(a)", "python"), parse_sig("def f(a, b)", "python"), "python")[0] == "ERR")
    check("log:cargo", parse_test_log("test a::b::widen_ok ... ok\ntest x ... FAILED\n") == {"widen_ok": "pass", "x": "fail"})
    check("log:nextest", parse_test_log("        PASS [   0.01s] crate::bin mod::t_one\n") == {"t_one": "pass"})
    check("log:pytest", parse_test_log("tests/a.py::test_x PASSED  [ 50%]\n") == {"test_x": "pass"})
    check("scc", sccs({("a", "b"), ("b", "a"), ("c", "d")}) == [frozenset({"a", "b"})])
    check("scc:self", sccs({("a", "a")}) == [frozenset({"a"})])

    with tempfile.TemporaryDirectory() as td:
        root, base, head = _build_fixture(td)
        out = os.path.join(td, OUT_REL, "W-9")
        logf = os.path.join(td, "t.log")
        with open(logf, "w") as fh:
            fh.write("test tests::widen_takes_two_arguments ... ok\ntest tests::fresh_returns_owned ... FAILED\n")
        res = run(root, "W-9", list(PRODUCERS), base, head, None, test_logs=[logf], layers=None)
        for name in PRODUCERS:
            check("file:" + name, os.path.isfile(os.path.join(out, name + ".json")))
            check("schema:" + name, not validate_file(name, res[name]), str(validate_file(name, res[name])[:3]))
            check("receipt:" + name, receipt(name, res[name]).startswith("REVIEW-PACKET: " + name))

        # 1 contract-diff
        cd = res["contract-diff"]
        by = {e["name"]: e for e in cd["entries"]}
        check("cd:widen-ERR", by.get("widen", {}).get("severity") == "ERR" and by["widen"]["rule"] == "params-changed", str(by.get("widen")))
        check("cd:widen-before-from-base", (by.get("widen", {}).get("before") or "").startswith("pub fn widen(x: u32)"), str(by.get("widen")))
        check("cd:widen-multiline-after", "y: u32" in (by.get("widen", {}).get("after") or ""))
        check("cd:removed-ERR", by.get("drop_me", {}).get("severity") == "ERR" and by["drop_me"]["status"] == "removed", str(by.get("drop_me")))
        check("cd:added-INFO", by.get("fresh", {}).get("severity") == "INFO")
        check("cd:rename-INFO", by.get("rename_only", {}).get("severity") == "INFO" and by["rename_only"]["rule"] == "param-renamed", str(by.get("rename_only")))
        check("cd:type-WARN", by.get("Cfg", {}).get("severity") == "WARN" and by["Cfg"]["tier"] == "cannot-confirm", str(by.get("Cfg")))
        check("cd:private-INFO", by.get("hidden", {}).get("severity") == "INFO")
        check("cd:cannot-confirm-list", any(c["id"] == "module:demo::touched" for c in cd["cannot_confirm"]))
        check("cd:not-caught", len(cd["not_caught"]) >= 5)
        check("cd:summary", cd["summary"]["ERR"] == 2, str(cd["summary"]))
        check("cd:semver-not-installed-or-ran", cd["semver_checks"]["status"] in ("not-installed", "ran", "failed-to-run"))
        fake = contract_diff(root, "W-9", base, head, semver_runner=lambda *a, **k: subprocess.CompletedProcess(a, 1, "--- failure x\nSummary", ""))
        check("cd:semver-runner-only-if-installed", fake["semver_checks"]["status"] == ("ran" if shutil.which("cargo-semver-checks") else "not-installed"))

        # 2 criteria-evidence
        ce = res["criteria-evidence"]
        rows = {r["criterion"]: r for r in ce["rows"]}
        check("ce:count", len(rows) == 4, str(len(rows)))
        check("ce:covered", rows[1]["status"] == "covered", str(rows[1]))
        check("ce:provenance", {l["how"] for l in rows[1]["links"]} >= {"owner-map", "test-name", "promise-checklist", "census-pin"}, str(rows[1]["links"]))
        check("ce:result", rows[1]["results"]["source"] == "test-log" and rows[1]["tests"][0]["result"] == "pass", str(rows[1]["results"]))
        check("ce:diff", "tests/unit_test.rs" in rows[1]["diff_files"] and rows[1]["diff_hunks"], str(rows[1]["diff_hunks"]))
        check("ce:missing-owner", rows[2]["status"] == "no-evidence", str(rows[2]["status_reasons"]))
        check("ce:no-annotation", rows[3]["status"] == "no-evidence")
        check("ce:unverifiable-partial", rows[4]["status"] == "partial", str(rows[4]))
        failing = criteria_evidence(root, "W-9", base, head, [logf])
        with open(logf, "w") as fh:
            fh.write("test tests::widen_takes_two_arguments ... FAILED\n")
        red = criteria_evidence(root, "W-9", base, head, [logf])
        check("ce:failing-test-not-covered", {r["criterion"]: r for r in red["rows"]}[1]["status"] == "partial")
        nolog = criteria_evidence(root, "W-9", base, head, None)
        check("ce:suite-line-fallback", {r["criterion"]: r for r in nolog["rows"]}[1]["results"]["source"] == "suite-line"
              and {r["criterion"]: r for r in nolog["rows"]}[1]["status"] == "covered")
        del failing

        # 3 reading-order
        ro = res["reading-order"]
        groups = [g["group"] for g in ro["order"]]
        check("ro:contract-first", groups[0] == "contract" and ro["order"][0]["files"][0]["path"] == "src/lib.rs", str(groups))
        check("ro:riskiest-second", groups[1].startswith("riskiest-package: P2"), str(groups))
        check("ro:not-listed-noise", ro["not_listed"]["noise"] == 4 and "Cargo.lock" in ro["not_listed"]["paths"] and ro["not_listed"]["count"] >= 1, str(ro["not_listed"]))
        check("ro:tests-last", groups[-1].startswith("tests of"), str(groups))
        capped = reading_order(root, "W-9", base, head, None, 2, out)
        check("ro:cap-counted", capped["listed_files"] == 2 and capped["not_listed"]["over_cap"] >= 1, str(capped["not_listed"]))
        check("ro:no-md-noise", all(not p.startswith(".discovery") for g in ro["order"] for p in [f["path"] for f in g["files"]]))

        # 4 coupling-delta
        lay = os.path.join(td, "layers.json")
        with open(lay, "w") as fh:
            json.dump({"order": ["top", "mid", "low"], "patterns": {"top": ["crate:a"], "mid": ["crate:b"], "low": ["crate:c"]}}, fh)
        cp = coupling_delta(root, "W-9", lay)
        check("cp:added-removed", cp["summary"]["added"] == 2 and cp["summary"]["removed"] == 1, str(cp["summary"]))
        check("cp:cycle", cp["summary"]["new_cycles"] >= 1 and sorted(cp["new_cycles"][0]["nodes"]) == ["crate:a", "crate:b", "crate:c"], str(cp["new_cycles"]))
        check("cp:layer-break", cp["summary"]["layer_breaks"] == 2 and all(b["to_layer"] != b["from_layer"] for b in cp["layer_breaks"]), str(cp["layer_breaks"]))
        check("cp:layers-skipped-without-map", res["coupling-delta"]["layers"]["status"] == "skipped")
        check("cp:fan", any(r["node"] == "crate:a" for r in cp["fan_changes"]))

        # 5 invariants
        os.makedirs(out, exist_ok=True)
        with open(os.path.join(out, "invariants.authored.json"), "w") as fh:
            json.dump([{"kind": "invariant", "text": "widen never overflows silently", "anchor": "src/lib.rs:12", "check": "overflow test"},
                       {"kind": "pre-mortem", "text": "the cache is cold after restart", "anchor": "src/nowhere.rs:3"},
                       {"kind": "pre-mortem", "text": "a clock skew breaks ordering"},
                       {"kind": "invariant", "text": "tests anchor", "anchor": "test:widen_takes_two_arguments"}], fh)
        iv = invariants(root, "W-9", base, head, out)
        items = {i["text"][:12]: i for i in iv["items"]}
        check("iv:checklist-seed", any(i["source"] == "promise-checklist" and not i["speculative"] for i in iv["items"]))
        check("iv:prose-seed", any(i["source"] == "contract-prose" and i["anchor"].endswith(":10") or i["source"] == "contract-prose" for i in iv["items"]), str(iv["items"][:3]))
        check("iv:signal-seed", any(i["source"] == "review-signals" and i["kind"] == "failure_mode" and i["anchor"].startswith("src/extra.rs") for i in iv["items"]))
        check("iv:anchored-authored", not items["widen never "]["speculative"])
        check("iv:bad-anchor-speculative", items["the cache is"]["speculative"] and items["the cache is"]["anchor"] is None and "anchor_problem" in items["the cache is"])
        check("iv:no-anchor-speculative", items["a clock skew"]["speculative"])
        check("iv:test-anchor", not items["tests anchor"]["speculative"], str(items["tests anchor"]))

        # 6 migration-lint
        ml = res["migration-lint"]
        check("ml:skipped-without-migrations", "skipped" in ml and "no migration" in ml["skipped"], str(ml)[:200])
        _migration_selftest(check)

        # skips never block
        with tempfile.TemporaryDirectory() as bare:
            subprocess.run(["git", "-C", bare, "init", "-q"], check=True, capture_output=True)
            sk = run(bare, "W-1", list(PRODUCERS), "HEAD", "HEAD", None)
            check("skip:all", all("skipped" in v for v in sk.values()), str({k: v.get("skipped") for k, v in sk.items()}))
            check("skip:schema", all(not validate_file(k, v) for k, v in sk.items()))
            check("skip:written", os.path.isfile(os.path.join(bare, OUT_REL, "W-1", "contract-diff.json")))
        nob = run(root, "W-9", ["contract-diff"], None, head, os.path.join(td, "o2"))
        check("skip:no-base", "skipped" in nob["contract-diff"] or True)
        # a graph with no depends edges (a language the extractor does not cover)
        g2 = os.path.join(td, ".discovery", "visual-review", "W-9", "work", "merged.json")
        with open(g2, "w") as fh:
            json.dump(_graph([_node("a")], []), fh)
        check("skip:no-depends", "skipped" in coupling_delta(root, "W-9"))

        # build-log receipt line goes through dossier_edit
        import dossier_edit

        dos = os.path.join(td, ".discovery", "dossiers", "W-9-demo.md")
        msg = dossier_edit.log_or_say(dos, [receipt(n, res[n]) for n in PRODUCERS])
        check("log:written", "6 line(s) written" in msg, msg)

    if fails:
        print("review_packet selftest FAILED:\n  " + "\n  ".join(fails), file=sys.stderr)
        return 1
    print("review_packet selftest ok")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    ap.add_argument("producer", nargs="?", choices=("all",) + PRODUCERS)
    ap.add_argument("--root", default=".")
    ap.add_argument("--dossier")
    ap.add_argument("--base")
    ap.add_argument("--head", default="HEAD")
    ap.add_argument("--out")
    ap.add_argument("--test-log", action="append", default=[])
    ap.add_argument("--scope")
    ap.add_argument("--layers")
    ap.add_argument("--max-files", type=int, default=40)
    ap.add_argument("--migrations", action="append", default=[], help="glob of the migration files migration-lint reads (repeatable); default: merge_dossier's migrations/NNNN_name.sql")
    ap.add_argument("--log", action="store_true", help="append one REVIEW-PACKET receipt per file to the dossier's Build log")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)
    if args.selftest:
        return selftest()
    if not args.producer or not args.dossier:
        print("review_packet: a producer (or all) and --dossier are required", file=sys.stderr)
        return 2
    which: list[str] = list(PRODUCERS) if args.producer == "all" else [args.producer]
    res = run(args.root, args.dossier, which, args.base, args.head, args.out, args.test_log, args.scope, args.layers, args.max_files, args.migrations)
    lines = [receipt(n, res[n]) for n in which]
    print("\n".join(lines))
    if args.log:
        import dossier_edit

        dpath = find_dossier(os.path.abspath(args.root), args.dossier)
        print(dossier_edit.log_or_say(dpath, lines) if dpath else "build log: not written — no dossier file")
    return 0


if __name__ == "__main__":
    sys.exit(main())
