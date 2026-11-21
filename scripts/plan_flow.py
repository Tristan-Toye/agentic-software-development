#!/usr/bin/env python3
"""Mechanical helper for /plan (primary-agents/plan.md, phases P1-P8).

The planner calls these subcommands instead of reading files or comparing
text by hand. Every subcommand takes `--root <repo checkout>` (default `.`);
a dossier is named by `--dossier <ID>`, resolved as
`.discovery/dossiers/<ID>.md` or `.discovery/dossiers/<ID>-*.md`.

# Usage

    plan_flow.py pins --root R --files a b ...
    plan_flow.py register --root R --dossier ID [--ready]
    plan_flow.py second-reader --root R --dossier ID
    plan_flow.py overlap --root R (--dossier ID | --files f1 f2 ...) [--terms t1 t2 ...]
    plan_flow.py hashes --root R --dossier ID (--write | --diff)
    plan_flow.py refresh --root R --dossier ID [--ref REF]
    plan_flow.py --selftest

# Subcommands

pins (P2, the pin scan). For each file prints one line per hit:
`path:line  <kind>  <name>=<value>  adr: <ADR id or none>`. Kinds: image (a
`FROM img:tag` / `image: img:tag`), digest (`@sha256:`), version (Cargo.toml,
package.json, requirements, pyproject, csproj, go.mod pins and rust-version),
toolchain (rust-toolchain channel), limit (SCREAMING_CASE = number, or a
config key holding max/min/limit/ttl/timeout/size/cap/count with a number).
The ADR is the first docs/adr/*.md (not index.md) that names the pin; heuristic.
Prints `pins: none` when nothing is found.

register (P3 to P6, the planner while drafting, and P7 with --ready). Lints
the dossier's `## Decisions` register: seven mandatory rows (R-PINS R-BOUNDS
R-STAGES R-NAMES R-WRITERS R-TRUST R-FAILS), subsections for R-STAGES,
R-WRITERS and R-TRUST unless n/a, at most 6 CONFIRM rows, a trigger on every
CONFIRM, who/when on a DECIDE that defers, a FIX token that landed in
`## Contract` or `## Work packages`, row-ID shape and uniqueness; with
--ready every CONFIRM needs a Ruling. Prints `DEFECT <id>: <why>` lines and
exits 1, or `register: ok (<n> rows, <c> CONFIRM)`.

second-reader (P7, the trigger rule for the second plan reader). Prints
`second-reader: yes — <reasons; joined>` or `second-reader: no`, exit 0.
Yes when R-NAMES is CONFIRM or lists a new/redefined concept, when two or more
work packages own paths in two or more modules, or when R-STAGES / R-WRITERS
is CONFIRM.

overlap (P1 at intake, P7 before ready, P8 at the refresh). Scans every other
non-dropped dossier (in-flight and done) for the same files (work-package
owned paths, anchors) and concepts (backticked R-NAMES tokens plus --terms),
and `.discovery/deferred-ledger.md` plus `DEFERRED:` build-log lines for the
same. Prints `file <ID> (<status>) <path>`, `concept <ID> (<status>) <term>`,
`deferred <ID or ledger> <D-id> <path or term>`, or `overlap: none`.

hashes (P7 contract review and P8 refresh). Splits the `## Contract` code
blocks into members and keeps one sha256 per member (whitespace normalised).
--write stores `.discovery/contract-review/<ID>.json`; --diff prints
`changed|added|removed <name>`, `user <name> (uses <changed>)`, then
`re-review: <list>` or `re-review: none` (`re-review: all (no stored hashes)`
when nothing was stored).

refresh (P8, and the start of /work-on for a `flow: 2` dossier). Compares the front-matter
`baseline_commit` with REF (default HEAD): `anchor gone <path:line>`,
`anchor moved? <path:line>`, `contract target changed <path>: <names>`
(exit 3), else `refresh: clean`; `refresh: no baseline_commit` (exit 0) when
none is recorded.
"""

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile

MANDATORY = ["R-PINS", "R-BOUNDS", "R-STAGES", "R-NAMES", "R-WRITERS", "R-TRUST", "R-FAILS"]
NEEDS_SUB = ["R-STAGES", "R-WRITERS", "R-TRUST"]
KINDS = ["CONFIRM", "DECIDE", "FIX", "FYI"]
EMPTY = {"", "-", "\u2014", "\u2013", "n/a"}
DEFER_RE = re.compile(r"\b(defer|deferred|later|for now|tbd|follow-up)\b", re.I)


class Fail(Exception):
    pass


# --------------------------------------------------------------------------
# Dossier reading
# --------------------------------------------------------------------------

def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def dossier_dir(root):
    return os.path.join(root, ".discovery", "dossiers")


def resolve(root, ident):
    if os.path.isfile(ident):
        return ident
    d = dossier_dir(root)
    exact = os.path.join(d, ident + ".md")
    if os.path.isfile(exact):
        return exact
    if os.path.isdir(d):
        hits = sorted(n for n in os.listdir(d) if n.startswith(ident + "-") and n.endswith(".md"))
        if hits:
            return os.path.join(d, hits[0])
    raise Fail(f"no dossier {ident} under {d}")


def split_front(text):
    """Return (front matter lines, body text)."""
    lines = text.split("\n")
    if lines and lines[0].strip() == "---":
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                return lines[1:i], "\n".join(lines[i + 1:])
    return [], text


def front_matter(text):
    fm, _ = split_front(text)
    out = {}
    key = None
    for ln in fm:
        m = re.match(r"^([A-Za-z_][\w-]*):\s*(.*)$", ln)
        if m:
            key, val = m.group(1), re.sub(r"\s+#.*$", "", m.group(2)).strip()
            if val.startswith("[") and val.endswith("]"):
                out[key] = [x.strip().strip("'\"") for x in val[1:-1].split(",") if x.strip()]
            elif val == "":
                out[key] = []
            else:
                out[key] = val.strip("'\"")
        elif key and re.match(r"^\s+-\s+", ln) and isinstance(out.get(key), list):
            out[key].append(re.sub(r"\s+#.*$", "", re.sub(r"^\s+-\s+", "", ln)).strip().strip("'\""))
    return out


def sections(text):
    """Map level-2 heading -> body lines (subsections included); fences respected."""
    _, body = split_front(text)
    out, cur, fence = {}, None, None
    for ln in body.split("\n"):
        m = re.match(r"^(```+|~~~+)", ln.strip())
        if m:
            tok = m.group(1)[0]
            fence = None if fence == tok else (fence or tok)
        if fence is None and re.match(r"^## (?!#)", ln):
            cur = ln[3:].strip()
            out.setdefault(cur, [])
            continue
        if cur is not None:
            out[cur].append(ln)
    return out


def section(text, name):
    for k, v in sections(text).items():
        if k.lower().startswith(name.lower()):
            return v
    return None


def subsections(lines):
    """Split section lines at `### ` headings (fences respected) -> {title: lines}."""
    out, cur, fence = {}, None, None
    for ln in lines:
        m = re.match(r"^(```+|~~~+)", ln.strip())
        if m:
            tok = m.group(1)[0]
            fence = None if fence == tok else (fence or tok)
        if fence is None and re.match(r"^### ", ln):
            cur = ln[4:].strip().strip("`")
            out[cur] = []
            continue
        if cur is not None:
            out[cur].append(ln)
    return out


def split_cells(line):
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|"):
        s = s[:-1]
    cells, cur, tick = [], "", False
    for ch in s:
        if ch == "`":
            tick = not tick
        if ch == "|" and not tick:
            cells.append(cur.strip())
            cur = ""
        else:
            cur += ch
    cells.append(cur.strip())
    return cells


def first_table(lines):
    """First markdown table in lines (before any ### heading) -> (header, rows)."""
    hdr, rows = None, []
    for ln in lines:
        if re.match(r"^### ", ln):
            break
        if ln.strip().startswith("|"):
            cells = split_cells(ln)
            if hdr is None:
                hdr = cells
            elif all(re.fullmatch(r":?-{2,}:?", c.replace(" ", "")) for c in cells if c) and any(cells):
                continue
            else:
                rows.append(cells)
        elif hdr is not None:
            break
    return hdr, rows


def ticks(s):
    return re.findall(r"`([^`]+)`", s)


# --------------------------------------------------------------------------
# The decision register
# --------------------------------------------------------------------------

def parse_register(text):
    """Return (rows list of dicts, subsections dict) or raise Fail."""
    sec = section(text, "Decisions")
    if sec is None:
        raise Fail("no `## Decisions` section")
    hdr, raw = first_table(sec)
    if hdr is None:
        raise Fail("`## Decisions` has no table")
    names = ["id", "kind", "choice", "why", "trigger", "ruling"]
    rows = []
    for cells in raw:
        cells = (cells + [""] * 6)[:6]
        rows.append(dict(zip(names, [c.strip() for c in cells])))
    return rows, subsections(sec)


def empty(v):
    return v.strip().lower() in EMPTY


def lint_register(text, ready):
    defects = []
    try:
        rows, subs = parse_register(text)
    except Fail as e:
        return [("register", str(e))], 0, 0
    by_id = {}
    seen = set()
    for r in rows:
        rid = r["id"].strip("` ")
        if rid in seen:
            defects.append((rid, "duplicate row ID"))
        seen.add(rid)
        by_id.setdefault(rid, r)
        if rid not in MANDATORY and not re.fullmatch(r"DR-\d+", rid):
            defects.append((rid, "row ID is neither a mandatory R- row nor DR-<number>"))
        if r["kind"] not in KINDS:
            defects.append((rid, f"Kind `{r['kind']}` is not one of {', '.join(KINDS)}"))
    for m in MANDATORY:
        if m not in by_id:
            defects.append((m, "mandatory row is missing"))
        elif empty(by_id[m]["choice"]):
            defects.append((m, "Choice is empty (write `n/a, because ...` if it does not apply)"))
    for m in NEEDS_SUB:
        r = by_id.get(m)
        if r and not r["choice"].lower().startswith("n/a") and m not in subs:
            defects.append((m, f"has no `### {m}` subsection and its Choice is not n/a"))
    confirms = [r for r in rows if r["kind"] == "CONFIRM"]
    if len(confirms) > 6:
        defects.append(("register", f"{len(confirms)} CONFIRM rows, at most 6 allowed"))
    contract = "\n".join((section(text, "Contract") or []) + (section(text, "Work packages") or []))
    for r in rows:
        rid = r["id"].strip("` ")
        if r["kind"] == "CONFIRM" and empty(r["trigger"]):
            defects.append((rid, "CONFIRM row has no `Trigger or where`"))
        if r["kind"] == "DECIDE" and DEFER_RE.search(r["choice"] + " " + r["why"]) and empty(r["trigger"]):
            defects.append((rid, "DECIDE row defers but `Trigger or where` names no who and when"))
        if r["kind"] == "FIX":
            toks = ticks(r["trigger"])
            if not toks:
                defects.append((rid, "FIX row names no backticked token in `Trigger or where`"))
            elif not any(t in contract for t in toks):
                defects.append((rid, f"FIX token {', '.join('`'+t+'`' for t in toks)} is in neither ## Contract nor ## Work packages"))
        if ready and r["kind"] == "CONFIRM" and empty(r["ruling"]):
            defects.append((rid, "CONFIRM row has no Ruling (--ready)"))
    return defects, len(rows), len(confirms)


def cmd_register(a):
    text = read(resolve(a.root, a.dossier))
    defects, n, c = lint_register(text, a.ready)
    if defects:
        for d, why in defects:
            print(f"DEFECT {d}: {why}")
        return 1
    print(f"register: ok ({n} rows, {c} CONFIRM)")
    return 0


# --------------------------------------------------------------------------
# Work packages and modules
# --------------------------------------------------------------------------

def norm_path(p):
    p = p.strip().strip("`").split(":")[0] if re.search(r":\d+$", p.strip().strip("`")) else p.strip().strip("`")
    p = re.sub(r"^\./", "", p)
    return p.rstrip("*").rstrip("/")


def work_packages(text):
    """-> list of (package name, [paths])."""
    sec = section(text, "Work packages")
    if sec is None:
        return []
    hdr, rows = first_table(sec)
    if hdr is None:
        return []
    out = []
    for cells in rows:
        if len(cells) < 2:
            continue
        name, owned = cells[0], cells[1]
        toks = ticks(owned) or [t for t in re.split(r"[,\s]+", owned) if "/" in t or "." in t]
        paths = [norm_path(t) for t in toks if norm_path(t)]
        out.append((name, paths))
    return out


def is_test_row(name):
    n = name.lower()
    return bool(re.match(r"^(ut|it)\b", n)) or "unit test" in n or "integration test" in n


def module_of(path):
    parts = [p for p in path.split("/") if p]
    dirs = parts[:-1]
    if not dirs:
        return parts[0] if parts else ""
    return "/".join(dirs[:2])


def cmd_second_reader(a):
    text = read(resolve(a.root, a.dossier))
    reasons = []
    try:
        rows, subs = parse_register(text)
    except Fail:
        rows, subs = [], {}
    by_id = {r["id"].strip("` "): r for r in rows}
    nm = by_id.get("R-NAMES")
    if nm:
        blob = nm["choice"] + " " + "\n".join(subs.get("R-NAMES", []))
        if nm["kind"] == "CONFIRM":
            reasons.append("R-NAMES is CONFIRM")
        elif re.search(r"\bnew:|redefine", blob, re.I):
            reasons.append("R-NAMES lists a new or redefined concept")
    mods = {}
    for name, paths in work_packages(text):
        if is_test_row(name) or not paths:
            continue
        mods[name] = {module_of(p) for p in paths}
    allm = set().union(*mods.values()) if mods else set()
    if len(mods) >= 2 and len(allm) >= 2:
        reasons.append(f"work packages span modules {', '.join(sorted(allm))}")
    for rid in ("R-STAGES", "R-WRITERS"):
        if by_id.get(rid, {}).get("kind") == "CONFIRM":
            reasons.append(f"{rid} is CONFIRM")
    print("second-reader: yes \u2014 " + "; ".join(reasons) if reasons else "second-reader: no")
    return 0


# --------------------------------------------------------------------------
# Overlap
# --------------------------------------------------------------------------

def dossier_files(text):
    files = {p for _, ps in work_packages(text) for p in ps}
    for an in front_matter(text).get("anchors", []) or []:
        files.add(norm_path(re.sub(r":\d+$", "", an)))
    return {f for f in files if f}


def names_terms(text):
    try:
        rows, subs = parse_register(text)
    except Fail:
        return []
    out = []
    for r in rows:
        if r["id"].strip("` ") == "R-NAMES":
            for v in r.values():
                out += ticks(v)
    out += ticks("\n".join(subs.get("R-NAMES", [])))
    seen = []
    for t in out:
        if t not in seen:
            seen.append(t)
    return seen


def word_in(term, text):
    return re.search(r"(?<![\w])" + re.escape(term) + r"(?![\w])", text) is not None


def cmd_overlap(a):
    own_id = None
    files, terms = set(a.files or []), list(a.terms or [])
    files = {norm_path(f) for f in files}
    if a.dossier:
        own = resolve(a.root, a.dossier)
        otext = read(own)
        own_id = front_matter(otext).get("id") or a.dossier
        files |= dossier_files(otext)
        terms = names_terms(otext) + terms
    out = []
    d = dossier_dir(a.root)
    names = sorted(os.listdir(d)) if os.path.isdir(d) else []
    for n in names:
        p = os.path.join(d, n)
        if not n.endswith(".md") or (a.dossier and os.path.abspath(p) == os.path.abspath(resolve(a.root, a.dossier))):
            continue
        t = read(p)
        fm = front_matter(t)
        status = fm.get("status", "?")
        if status == "dropped":
            continue
        wm = re.match(r"^(W-\d+)", n)
        oid = (fm.get("id") or wm.group(1)) if wm else n
        oid = fm.get("id") or oid
        for f in sorted(files & dossier_files(t)):
            out.append(f"file {oid} ({status}) {f}")
        hay = "\n".join((section(t, "Approach") or []) + (section(t, "Decisions") or []) + (section(t, "Contract") or []))
        for term in terms:
            if word_in(term, hay):
                out.append(f"concept {oid} ({status}) {term}")
        for ln in section(t, "Build log") or []:
            if "DEFERRED:" in ln:
                out += deferred_hits(oid, ln, files, terms)
    ledger = os.path.join(a.root, ".discovery", "deferred-ledger.md")
    if os.path.isfile(ledger):
        for ln in read(ledger).split("\n"):
            if own_id and word_in(own_id, ln):
                continue
            out += deferred_hits("ledger", ln, files, terms)
    seen = []
    for o in out:
        if o not in seen:
            seen.append(o)
    if not seen:
        print("overlap: none")
    for o in seen:
        print(o)
    return 0


def deferred_hits(owner, line, files, terms):
    m = re.search(r"\bD-\d+\b", line)
    did = m.group(0) if m else "-"
    hits = []
    for f in sorted(files):
        if f and f in line:
            hits.append(f"deferred {owner} {did} {f}")
    for t in terms:
        if word_in(t, line):
            hits.append(f"deferred {owner} {did} {t}")
    return hits


# --------------------------------------------------------------------------
# Contract member hashes
# --------------------------------------------------------------------------

RUST_DECL = re.compile(r"^(?:pub(?:\([^)]*\))?\s+)?(?:(?:unsafe|async|const|extern(?:\s+\"[^\"]*\")?)\s+)*(fn|struct|enum|trait|type|const|static|mod|union)\s+(\w+)")
RUST_IMPL = re.compile(r"^(?:unsafe\s+)?impl\b(?:<[^>]*>)?\s*(.+?)\s*(?:\bwhere\b.*|\{.*|;)?$")
PY_DECL = re.compile(r"^(?:async\s+)?(?:def|class)\s+(\w+)")
MODS = r"(?:(?:public|private|protected|internal|static|abstract|sealed|final|partial|export|default|declare|readonly|unsafe|new)\s+)*"
OO_TYPE = re.compile(r"^" + MODS + r"(class|interface|record|enum|struct|type)\s+(\w+)")
FUNC_DECL = re.compile(r"^(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s*\*?\s*(\w+)|^func\s+(?:\([^)]*\)\s*)?(\w+)")
METHOD = re.compile(r"^\s*" + MODS + r"(?:async\s+)?[\w<>\[\],.?()\s]*?\s*\b(\w+)\s*(?:<[^>]*>)?\s*\([^;{]*\)\s*(?::\s*[^;{]+)?\s*(?:where\s+[^;{]+)?\s*(?:[;{]|=>.*)?\s*$")
PROPERTY = re.compile(r"^\s*" + MODS + r"[\w<>\[\],.?]+\s+(\w+)\s*\{\s*(?:get|set|init)\b")
NOT_METHOD = {"return", "if", "for", "while", "switch", "catch", "using", "else", "throw", "new", "foreach", "lock", "await", "var", "typeof", "nameof"}
COMMENTISH = re.compile(r"^\s*(///|//!|//|/\*|\*|#\[|#!\[|@\w|#|\[\w)")


def code_blocks(lines):
    """Fenced code blocks of a section -> [(lang, code)]."""
    out, cur, lang = [], None, ""
    for ln in lines:
        m = re.match(r"^\s*(```+|~~~+)\s*([\w#+-]*)", ln)
        if m and cur is None:
            cur, lang = [], m.group(2).lower()
        elif re.match(r"^\s*(```+|~~~+)\s*$", ln) and cur is not None:
            out.append((lang, "\n".join(cur)))
            cur = None
        elif cur is not None:
            cur.append(ln)
    return out


def split_members(lang, code):
    lines = code.split("\n")
    brace = lang not in ("rust", "rs", "python", "py")
    starts = []  # (index, name)
    in_class = None
    for i, ln in enumerate(lines):
        if not ln.strip():
            continue
        indent = len(ln) - len(ln.lstrip())
        s = ln.strip()
        if indent == 0:
            name, cls = None, False
            m = RUST_IMPL.match(s) if re.match(r"^(?:unsafe\s+)?impl\b", s) else None
            if m:
                name = "impl " + re.sub(r"\s+", " ", m.group(1))
            elif (m := PY_DECL.match(s)):
                name = m.group(1)
            elif (m := RUST_DECL.match(s)) and lang not in ("python", "py") and not OO_TYPE.match(s):
                name = m.group(2)
            elif (m := OO_TYPE.match(s)):
                name, cls = m.group(2), m.group(1) in ("class", "interface", "record", "struct")
            elif (m := FUNC_DECL.match(s)):
                name = m.group(1) or m.group(2)
            elif brace and (m := METHOD.match(ln)) and m.group(1) not in NOT_METHOD:
                name = m.group(1)
            if name:
                starts.append((i, name))
                in_class = name if cls and brace and lang else None
            elif s.startswith("}"):
                in_class = None
        elif in_class:
            m = METHOD.match(ln) or PROPERTY.match(ln)
            if m and m.group(1) not in NOT_METHOD:
                starts.append((i, f"{in_class}.{m.group(1)}"))
    ext = []
    for idx, name in starts:
        j = idx
        while j > 0 and COMMENTISH.match(lines[j - 1]) and (not ext or j - 1 > ext[-1][0]):
            j -= 1
        ext.append((idx, name, j))
    members = []
    counts = {}
    for k, (idx, name, j) in enumerate(ext):
        end = ext[k + 1][2] if k + 1 < len(ext) else len(lines)
        text = "\n".join(lines[j:end]).rstrip()
        counts[name] = counts.get(name, 0) + 1
        nm = name if counts[name] == 1 else f"{name}#{counts[name]}"
        members.append((nm, text))
    return members


def contract_members(text):
    sec = section(text, "Contract") or []
    out = {}
    for lang, code in code_blocks(sec):
        for name, mtext in split_members(lang, code):
            base, n = name, 1
            while name in out:
                n += 1
                name = f"{base}#{n}"
            out[name] = mtext
    return out


def hash_text(s):
    return hashlib.sha256(" ".join(s.split()).encode("utf-8")).hexdigest()


def word_form(name):
    n = name.split("#")[0]
    if n.startswith("impl "):
        n = re.split(r"\s+for\s+", n[5:])[-1]
        n = re.sub(r"<.*", "", n).strip()
    return n.split(".")[-1].strip()


def git(root, *args):
    return subprocess.run(["git", "-C", root, *args], capture_output=True, text=True)


def head_commit(root):
    r = git(root, "rev-parse", "HEAD")
    return r.stdout.strip() if r.returncode == 0 and r.stdout.strip() else None


def dossier_id(path, text, fallback):
    return front_matter(text).get("id") or fallback


def cmd_hashes(a):
    path = resolve(a.root, a.dossier)
    text = read(path)
    ident = dossier_id(path, text, a.dossier)
    store = os.path.join(a.root, ".discovery", "contract-review", ident + ".json")
    cur = {n: hash_text(t) for n, t in contract_members(text).items()}
    if a.write:
        os.makedirs(os.path.dirname(store), exist_ok=True)
        with open(store, "w", encoding="utf-8") as f:
            json.dump({"members": cur, "commit": head_commit(a.root)}, f, indent=2, sort_keys=True)
            f.write("\n")
        print(f"hashes: wrote {len(cur)} members to {store}")
        return 0
    if not os.path.isfile(store):
        print("re-review: all (no stored hashes)")
        return 0
    old = json.load(open(store, encoding="utf-8")).get("members", {})
    changed = [n for n in cur if n in old and cur[n] != old[n]]
    added = [n for n in cur if n not in old]
    removed = [n for n in old if n not in cur]
    for n in changed:
        print(f"changed {n}")
    for n in added:
        print(f"added {n}")
    for n in removed:
        print(f"removed {n}")
    movers = changed + added + removed
    users = []
    members = contract_members(text)
    for n in cur:
        if n in movers:
            continue
        uses = [m for m in movers if word_form(m) and word_in(word_form(m), members[n])]
        if uses:
            users.append(n)
            print(f"user {n} (uses {', '.join(uses)})")
    every = movers + users
    print("re-review: " + (", ".join(every) if every else "none"))
    return 0


# --------------------------------------------------------------------------
# Refresh
# --------------------------------------------------------------------------

def file_at(root, ref, path):
    r = git(root, "show", f"{ref}:{path}")
    return r.stdout if r.returncode == 0 else None


def cmd_refresh(a):
    path = resolve(a.root, a.dossier)
    text = read(path)
    fm = front_matter(text)
    base = fm.get("baseline_commit")
    if not base or base == "null":
        print("refresh: no baseline_commit")
        return 0
    ref = a.ref or "HEAD"
    out = []
    anchors = fm.get("anchors", []) or []
    for an in anchors:
        m = re.match(r"^(.*?)(?::(\d+))?$", an)
        if m is None:
            continue
        p, ln = m.group(1), int(m.group(2)) if m.group(2) else None
        now = file_at(a.root, ref, p)
        if now is None:
            out.append(f"anchor gone {an}")
            continue
        was = file_at(a.root, base, p)
        if was is None or was == now or ln is None:
            continue
        wl, nl = was.split("\n"), now.split("\n")
        old_line = wl[ln - 1] if ln <= len(wl) else None
        new_line = nl[ln - 1] if ln <= len(nl) else None
        moved = old_line != new_line
        if not moved:
            diff = git(a.root, "diff", "-U0", base, ref, "--", p).stdout
            for h in re.finditer(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", diff, re.M):
                oc = int(h.group(2)) if h.group(2) is not None else 1
                nc = int(h.group(4)) if h.group(4) is not None else 1
                if int(h.group(1)) < ln and oc != nc:
                    moved = True
        if moved:
            out.append(f"anchor moved? {an}")
    names = {word_form(n) for n in contract_members(text)}
    names.discard("")
    targets = {p for _, ps in work_packages(text) for p in ps}
    targets |= {re.sub(r":\d+$", "", x) for x in anchors}
    hit = False
    for p in sorted(targets):
        if not p or file_at(a.root, base, p) is None:
            continue
        diff = git(a.root, "diff", "-U0", base, ref, "--", p).stdout
        if not diff:
            continue
        lines = [l[1:] for l in diff.split("\n") if l[:1] in "+-" and not l.startswith(("+++", "---"))]
        found = sorted(n for n in names if any(word_in(n, l) for l in lines))
        if found:
            hit = True
            out.append(f"contract target changed {p}: {', '.join(found)}")
    if not out:
        print("refresh: clean")
        return 0
    for o in out:
        print(o)
    return 3 if hit else 0


# --------------------------------------------------------------------------
# Pin scan
# --------------------------------------------------------------------------

LIMIT_KEY = re.compile(r"(max|min|limit|ttl|timeout|size|cap|count)", re.I)
SCREAM = re.compile(r"\b([A-Z][A-Z0-9_]{2,})\b\s*(?::[^=\n]+)?=\s*(-?\d[\d_.]*)")
KEYNUM = re.compile(r"""["']?([A-Za-z_][\w.-]*)["']?\s*[:=]\s*["']?(\d[\d_.]*[A-Za-z%]{0,3})["']?\s*[,;]?\s*(?:#.*|//.*)?$""")
DEP_SECTION = re.compile(r"^\[(?:[\w.\-'\"]+\.)?(?:dev-|build-)?dependencies(?:\.([\w\-]+))?\]$")


def adr_index(root):
    d = os.path.join(root, "docs", "adr")
    out = []
    if os.path.isdir(d):
        for n in sorted(os.listdir(d)):
            if n.endswith(".md") and n != "index.md":
                m = re.match(r"^(\d+)", n)
                out.append(("ADR-" + m.group(1) if m else n[:-3], read(os.path.join(d, n)).lower()))
    return out


def tok_in(tok, text):
    t = tok.lower()
    return re.search(r"(?<![a-z0-9.])" + re.escape(t) + r"(?![a-z0-9])", text) is not None


def find_adr(adrs, name, value):
    variants = {name.lower(), name.lower().replace("_", " "), name.lower().replace("_", "-"), name.lower().split("/")[-1]}
    variants.discard("")
    val = value.split(".")[0] if re.match(r"^v?\d", value) else value
    val = val.lstrip("v^~=<>")
    first = None
    for ident, text in adrs:
        if any(tok_in(v, text) for v in variants):
            if value and (tok_in(value, text) or (val and tok_in(val, text))):
                return ident
            first = first or ident
    if first:
        return first
    if len(value) >= 6 and not value.isdigit():
        for ident, text in adrs:
            if value.lower() in text:
                return ident
    return "none"


def scan_pins(root, relpath):
    full = relpath if os.path.isabs(relpath) else os.path.join(root, relpath)
    base = os.path.basename(relpath)
    try:
        lines = read(full).split("\n")
    except (OSError, UnicodeDecodeError):
        return []
    hits = []  # (line, kind, name, value)
    section_name = ""
    claimed = set()
    for i, ln in enumerate(lines, 1):
        s = ln.strip()
        if not s or s.startswith(("#", "//")) and not s.startswith("#["):
            if not (base.startswith("requirements") and s):
                continue
        # images
        m = re.match(r"^FROM\s+(?:--platform=\S+\s+)?(\S+)", s, re.I) or re.match(r"^-?\s*image:\s*['\"]?([^\s'\"#]+)", s)
        if m:
            ref = m.group(1)
            if "$" not in ref and ref.lower() != "scratch":
                dg = re.search(r"@(sha256:[0-9a-f]+)", ref)
                nm = re.sub(r"[:@].*", "", ref)
                if dg:
                    hits.append((i, "digest", nm, dg.group(1)))
                tm = None if ref.startswith("@") else re.match(r"^[^@:]+(?::([^@]+))?", ref)
                tag = tm.group(1) if tm else None
                if tag:
                    hits.append((i, "image", nm, tag))
                elif not dg:
                    pass
                claimed.add(i)
                continue
        # toolchain
        if base in ("rust-toolchain", "rust-toolchain.toml"):
            m = re.match(r"""^channel\s*=\s*["']([^"']+)["']""", s) or (re.match(r"^[\w.\-]+$", s) and re.match(r"(.+)", s))
            if m:
                hits.append((i, "toolchain", "rust-toolchain", m.group(1)))
                claimed.add(i)
            continue
        if base == "Cargo.toml" or base.endswith(".toml") and base != "pyproject.toml":
            hs = re.match(r"^\[(.+)\]$", s)
            if hs:
                section_name = hs.group(1)
                continue
            m = re.match(r"""^rust-version\s*=\s*["']([^"']+)["']""", s)
            if m:
                hits.append((i, "version", "rust-version", m.group(1)))
                claimed.add(i)
                continue
            if re.search(r"dependencies", section_name):
                m = re.match(r"""^([\w\-]+)\s*=\s*["']([^"']+)["']""", s)
                m2 = re.match(r"""^([\w\-]+)\s*=\s*\{.*\bversion\s*=\s*["']([^"']+)["']""", s)
                tbl = re.search(r"dependencies\.([\w\-]+)$", section_name)
                m3 = re.match(r"""^version\s*=\s*["']([^"']+)["']""", s) if tbl else None
                mm = m or m2
                if mm:
                    hits.append((i, "version", mm.group(1), mm.group(2)))
                    claimed.add(i)
                    continue
                if m3 and tbl:
                    hits.append((i, "version", tbl.group(1), m3.group(1)))
                    claimed.add(i)
                    continue
        if base == "package.json":
            m = re.match(r"""^"(@?[\w./\-]+)"\s*:\s*"([^"]+)"\s*,?$""", s)
            if m and re.search(r'"(dependencies|devDependencies|peerDependencies|optionalDependencies|engines)"', "\n".join(lines[:i])):
                if not m.group(1) in ("name", "version", "description", "main", "license", "type"):
                    hits.append((i, "version", m.group(1), m.group(2)))
                    claimed.add(i)
                    continue
        if base.startswith("requirements") and base.endswith(".txt"):
            m = re.match(r"^([A-Za-z0-9_.\-\[\]]+)\s*(==|>=|<=|~=|!=|<|>)\s*([^,;\s#]+)", s)
            if m:
                hits.append((i, "version", m.group(1), m.group(2) + m.group(3)))
                claimed.add(i)
            continue
        if base == "pyproject.toml":
            m = re.match(r"""^requires-python\s*=\s*["']([^"']+)["']""", s)
            if m:
                hits.append((i, "version", "requires-python", m.group(1)))
                claimed.add(i)
                continue
            m = re.search(r"""["']([A-Za-z0-9_.\-]+)(?:\[[^\]]*\])?\s*(==|>=|<=|~=|!=|<|>)\s*([^,;\s"']+)""", s)
            if m:
                hits.append((i, "version", m.group(1), m.group(2) + m.group(3)))
                claimed.add(i)
                continue
            hs = re.match(r"^\[(.+)\]$", s)
            if hs:
                section_name = hs.group(1)
                continue
            m = re.match(r"""^([\w\-]+)\s*=\s*["']([^"']+)["']""", s)
            if m and re.search(r"poetry.*dependencies", section_name):
                hits.append((i, "version", m.group(1), m.group(2)))
                claimed.add(i)
                continue
        if base.endswith((".csproj", ".props", ".fsproj")):
            m = re.search(r"""<PackageReference\s+[^>]*Include="([^"]+)"[^>]*\bVersion="([^"]+)\"""", s) or re.search(r"""<PackageReference\s+[^>]*Version="([^"]+)"[^>]*Include="([^"]+)\"""", s)
            if m:
                inc, ver = (m.group(1), m.group(2)) if "Include" in s[: s.find("Version")] else (m.group(2), m.group(1))
                hits.append((i, "version", inc, ver))
                claimed.add(i)
                continue
            m = re.search(r"<(TargetFrameworks?)>([^<]+)</", s)
            if m:
                hits.append((i, "version", m.group(1), m.group(2)))
                claimed.add(i)
                continue
        if base == "go.mod":
            m = re.match(r"^(go|toolchain)\s+(\S+)", s)
            if m:
                hits.append((i, "version", m.group(1), m.group(2)))
                claimed.add(i)
                continue
            m = re.match(r"^(?:require\s+)?([\w.\-]+(?:/[\w.\-~]+)+)\s+(v[\w.\-+]+)", s)
            if m:
                hits.append((i, "version", m.group(1), m.group(2)))
                claimed.add(i)
                continue
    for i, ln in enumerate(lines, 1):
        if i in claimed:
            continue
        s = ln.strip()
        if s.startswith(("//", "#")) and not s.startswith("#["):
            continue
        m = SCREAM.search(ln)
        if m:
            hits.append((i, "limit", m.group(1), m.group(2)))
            continue
        if base in ("Cargo.toml", "package.json", "go.mod") or base.startswith("requirements"):
            if base != "package.json":
                continue
        m = KEYNUM.search(s)
        if m and LIMIT_KEY.search(m.group(1)) and not re.fullmatch(r"version", m.group(1), re.I):
            hits.append((i, "limit", m.group(1), m.group(2)))
    hits.sort(key=lambda h: (h[0], h[1]))
    return hits


def cmd_pins(a):
    adrs = adr_index(a.root)
    n = 0
    for f in a.files:
        for line, kind, name, value in scan_pins(a.root, f):
            n += 1
            print(f"{f}:{line}  {kind}  {name}={value}  adr: {find_adr(adrs, name, value)}")
    if n == 0:
        print("pins: none")
    return 0


# --------------------------------------------------------------------------
# Selftest
# --------------------------------------------------------------------------

DOSSIER = """---
id: {id}
title: t
status: {status}
created: 2026-11-01
updated: 2026-11-01
anchors:
{anchors}
baseline_commit: {base}
jira: null
---
# {id} title

## Problem
p

## Approach
a

## Decisions

| ID | Kind | Choice | Why | Trigger or where | Ruling |
|---|---|---|---|---|---|
{rows}

{subs}
## Contract

{contract}

## Work packages

| Package | Owned paths | Depends on |
|---|---|---|
{wps}

## Acceptance criteria
1. a

## Build log
{log}
"""

NA = "n/a, because nothing changes"
CONTRACT = """```rust
/// Flush the queue once.
pub fn flush(q: &Queue) -> Result<(), Error>;

pub struct Queue { len: usize }

impl Queue {
    pub fn new() -> Self;
}

pub fn drain(q: &Queue) -> usize { flush(q) }
```"""


def default_rows():
    rows = {m: f"| {m} | FYI | {NA} | - | - | |" for m in MANDATORY}
    return rows


def mk(root, ident, rows=None, extra=(), subs="", wps=None, status="ready", anchors=("src/flush.rs:2",), base="null", contract=CONTRACT, log="- started", slug="x"):
    r = default_rows()
    r.update(rows or {})
    allrows = "\n".join(list(r.values()) + list(extra))
    wps = wps if wps is not None else ["| P1 flush | `src/flush.rs` | \u2014 |", "| UT unit tests | `tests/unit/test_flush.rs` | \u2014 |"]
    text = DOSSIER.format(id=ident, status=status, anchors="\n".join(f"  - {x}" for x in anchors) or "  []", base=base,
                          rows=allrows, subs=subs, contract=contract, wps="\n".join(wps), log=log)
    d = dossier_dir(root)
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, f"{ident}-{slug}.md")
    with open(p, "w", encoding="utf-8") as f:
        f.write(text)
    return p


def selftest():
    failures = []
    me = os.path.abspath(__file__)

    def run(root, *args):
        r = subprocess.run([sys.executable, me, *args, "--root", root], capture_output=True, text=True)
        return r.returncode, r.stdout

    def check(name, ok):
        print(("ok   " if ok else "FAIL ") + name)
        if not ok:
            failures.append(name)

    with tempfile.TemporaryDirectory() as tmp:
        root = os.path.join(tmp, "repo")
        os.makedirs(os.path.join(root, "docs", "adr"))
        os.makedirs(os.path.join(root, "src"))
        g = lambda *x: subprocess.run(["git", "-C", root, "-c", "user.name=t", "-c", "user.email=t@t", *x], capture_output=True, text=True, check=True, env={**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull})
        g("init", "-q")
        w = lambda rel, s: (os.makedirs(os.path.dirname(os.path.join(root, rel)), exist_ok=True), open(os.path.join(root, rel), "w").write(s))
        w("docs/adr/index.md", "postgres 16 serde\n")
        w("docs/adr/0001-database.md", "# Use postgres\nWe pin postgres 16 for storage.\nMAX_RETRIES stays small.\n")
        w("Cargo.toml", '[package]\nname = "x"\nrust-version = "1.75"\n\n[dependencies]\nserde = "1.0"\ntokio = { version = "1.38", features = ["rt"] }\n')
        w("docker-compose.yml", "services:\n  db:\n    image: postgres:16\n    mem_limit: 512m\n")
        w("Dockerfile", "FROM rust:1.75-slim\nFROM base@sha256:" + "ab" * 8 + "\n")
        w("src/lib.rs", "pub const MAX_RETRIES: usize = 5;\n")
        w("package.json", '{\n  "dependencies": {\n    "left-pad": "^1.3.0"\n  }\n}\n')
        w("requirements.txt", "requests==2.31.0\n")
        w("App.csproj", '<Project>\n<PropertyGroup><TargetFramework>net8.0</TargetFramework></PropertyGroup>\n<ItemGroup><PackageReference Include="Serilog" Version="3.1.1" /></ItemGroup>\n</Project>\n')
        w("go.mod", "module m\n\ngo 1.22\n\nrequire (\n\tgithub.com/x/y v1.2.3\n)\n")
        w("rust-toolchain.toml", '[toolchain]\nchannel = "1.75.0"\n')
        w("src/flush.rs", "// line1\npub fn flush() {}\n// line3\n// line4\n// line5\n")
        w("src/gone.rs", "fn gone() {}\n")
        w("src/other.rs", "fn other() {}\n")
        g("add", "-A")
        g("commit", "-qm", "base")
        base = g("rev-parse", "HEAD").stdout.strip()

        # --- pins
        rc, out = run(root, "pins", "--files", "docker-compose.yml", "Cargo.toml", "src/lib.rs", "Dockerfile", "package.json", "requirements.txt", "App.csproj", "go.mod", "rust-toolchain.toml")
        check("pins: compose image with ADR", "docker-compose.yml:3  image  postgres=16  adr: ADR-0001" in out)
        check("pins: cargo dep without ADR", "Cargo.toml:6  version  serde=1.0  adr: none" in out)
        check("pins: cargo inline-table dep and rust-version", "tokio=1.38" in out and "rust-version=1.75" in out)
        check("pins: SCREAMING limit linked to ADR", "src/lib.rs:1  limit  MAX_RETRIES=5  adr: ADR-0001" in out)
        check("pins: config limit key", "mem_limit=512m" in out)
        check("pins: Dockerfile FROM tag and digest", "image  rust=1.75-slim" in out and "digest  base=sha256:" in out)
        check("pins: package.json, requirements, csproj, go.mod, toolchain", all(x in out for x in ["left-pad=^1.3.0", "requests===2.31.0".replace("===", "==="), "Serilog=3.1.1", "TargetFramework=net8.0", "go=1.22", "github.com/x/y=v1.2.3", "rust-toolchain=1.75.0"]))
        rc, out = run(root, "pins", "--files", "src/other.rs")
        check("pins: none", "pins: none" in out and rc == 0)

        # --- register
        p1 = mk(root, "W-001", base=base, slug="flush",
                rows={"R-NAMES": "| R-NAMES | FYI | n/a, because no new names | - | - | |"},
                subs="", log="- DEFERRED: D-1 fix `FlushQueue` retry in src/zzz.rs\n")
        rc, out = run(root, "register", "--dossier", "W-001")
        check("register: ok", rc == 0 and out.startswith("register: ok (7 rows, 0 CONFIRM)"))
        rc, out = run(root, "register", "--dossier", "W-001-flush")
        check("register: dossier resolved by full stem", rc == 0)
        rows = {m: f"| {m} | FYI | {NA} | - | - | |" for m in MANDATORY}
        del rows["R-TRUST"]
        mk(root, "W-010", slug="a")
        t = read(os.path.join(root, ".discovery/dossiers/W-010-a.md")).replace("| R-TRUST | FYI | " + NA + " | - | - | |\n", "")
        open(os.path.join(root, ".discovery/dossiers/W-010-a.md"), "w").write(t)
        rc, out = run(root, "register", "--dossier", "W-010")
        check("register: missing mandatory row", rc == 1 and "DEFECT R-TRUST: mandatory row is missing" in out)
        mk(root, "W-011", rows={"R-STAGES": "| R-STAGES | DECIDE | two stages | because | `x` | |"}, slug="a")
        rc, out = run(root, "register", "--dossier", "W-011")
        check("register: R-STAGES without subsection", rc == 1 and "DEFECT R-STAGES: has no `### R-STAGES`" in out)
        mk(root, "W-012", slug="a", rows={"R-PINS": "| R-PINS | CONFIRM | pin | w | `x` | |"},
           extra=[f"| DR-{i} | CONFIRM | c | w | `t` | |" for i in range(1, 7)])
        rc, out = run(root, "register", "--dossier", "W-012")
        check("register: 7 CONFIRMs", rc == 1 and "7 CONFIRM rows, at most 6" in out)
        mk(root, "W-013", slug="a", extra=["| DR-1 | CONFIRM | c | w | - | |"])
        rc, out = run(root, "register", "--dossier", "W-013")
        check("register: CONFIRM without trigger", rc == 1 and "DEFECT DR-1: CONFIRM row has no" in out)
        mk(root, "W-014", slug="a", extra=["| DR-1 | DECIDE | decide later | w | | |", "| DR-2 | DECIDE | go with A | w | | |"])
        rc, out = run(root, "register", "--dossier", "W-014")
        check("register: deferral without who/when", rc == 1 and "DEFECT DR-1: DECIDE row defers" in out and "DR-2" not in out)
        mk(root, "W-015", slug="a", extra=["| DR-1 | FIX | fixed | w | `NoSuchThing` | |"])
        rc, out = run(root, "register", "--dossier", "W-015")
        check("register: FIX token not found", rc == 1 and "DEFECT DR-1: FIX token `NoSuchThing`" in out)
        mk(root, "W-016", slug="a", extra=["| DR-1 | FIX | fixed | w | `flush` in the contract | |"])
        rc, out = run(root, "register", "--dossier", "W-016")
        check("register: FIX token found in contract", rc == 0)
        mk(root, "W-017", slug="a", extra=["| DR-1 | CONFIRM | c | w | `t` | |"])
        rc, out = run(root, "register", "--dossier", "W-017")
        check("register: CONFIRM counted without --ready", rc == 0 and "1 CONFIRM" in out)
        rc, out = run(root, "register", "--dossier", "W-017", "--ready")
        check("register: --ready without ruling", rc == 1 and "DEFECT DR-1: CONFIRM row has no Ruling" in out)
        mk(root, "W-018", slug="a", extra=["| DR-1 | CONFIRM | c | w | `t` | yes |"])
        rc, out = run(root, "register", "--dossier", "W-018", "--ready")
        check("register: --ready with ruling", rc == 0)
        mk(root, "W-019", slug="a", extra=["| DR-1 | FYI | c | w | - | |", "| DR-1 | FYI | c | w | - | |", "| X-9 | FYI | c | w | - | |"])
        rc, out = run(root, "register", "--dossier", "W-019")
        check("register: duplicate and malformed IDs", rc == 1 and "DEFECT DR-1: duplicate" in out and "DEFECT X-9" in out)

        # --- second-reader
        def sr(ident, **kw):
            mk(root, ident, slug="s", **kw)
            return run(root, "second-reader", "--dossier", ident)[1].strip()
        check("second-reader: no", sr("W-020") == "second-reader: no")
        check("second-reader: R-NAMES CONFIRM", "R-NAMES is CONFIRM" in sr("W-021", rows={"R-NAMES": "| R-NAMES | CONFIRM | x | w | `t` | |"}))
        check("second-reader: new concept in Choice", "new or redefined" in sr("W-022", rows={"R-NAMES": "| R-NAMES | FYI | new: `FlushQueue` | w | - | |"}))
        check("second-reader: redefine in subsection", "new or redefined" in sr("W-023", subs="### R-NAMES\n\nwe Redefine `Queue`\n\n"))
        check("second-reader: two modules", "work packages span modules" in sr("W-024", wps=["| P1 a | `crates/a/src/x.rs` | \u2014 |", "| P2 b | `crates/b/src/y.rs` | P1 |"]))
        check("second-reader: one module is no", sr("W-025", wps=["| P1 a | `crates/a/src/x.rs` | \u2014 |", "| P2 b | `crates/a/src/y.rs` | P1 |", "| UT unit tests | `tests/unit/z.rs` | \u2014 |"]) == "second-reader: no")
        check("second-reader: R-STAGES/R-WRITERS CONFIRM", "R-STAGES is CONFIRM; R-WRITERS is CONFIRM" in sr("W-026", rows={"R-STAGES": "| R-STAGES | CONFIRM | s | w | `t` | |", "R-WRITERS": "| R-WRITERS | CONFIRM | s | w | `t` | |"}))

        # --- overlap
        mk(root, "W-002", slug="o", status="building", anchors=("src/flush.rs:5",), contract="```rust\npub struct FlushQueue;\n```")
        mk(root, "W-003", slug="o", status="dropped", anchors=("src/flush.rs:5",))
        mk(root, "W-004", slug="o", status="done", anchors=("src/other.rs:1",), wps=["| P1 o | `src/other.rs` | \u2014 |"], log="- DEFERRED: D-7 tidy `FlushQueue` drain")
        w(".discovery/deferred-ledger.md", "- D-9 src/flush.rs is racy\n")
        mk(root, "W-005", slug="n", rows={"R-NAMES": "| R-NAMES | FYI | new: `FlushQueue` | w | - | |"})
        rc, out = run(root, "overlap", "--dossier", "W-005")
        check("overlap: file of an in-flight dossier", "file W-002 (building) src/flush.rs" in out)
        check("overlap: concept of a dossier", "concept W-002 (building) FlushQueue" in out)
        check("overlap: dropped dossier skipped", "W-003" not in out)
        check("overlap: deferred in build log and ledger", "deferred W-004 D-7 FlushQueue" in out and "deferred ledger D-9 src/flush.rs" in out)
        rc, out = run(root, "overlap", "--files", "nothing/here.rs", "--terms", "Zzz")
        check("overlap: none", out.strip() == "overlap: none" and rc == 0)
        rc, out = run(root, "overlap", "--files", "src/other.rs", "--terms", "Qq")
        check("overlap: --files without dossier", "file W-004 (done) src/other.rs" in out)

        # --- hashes
        rc, out = run(root, "hashes", "--dossier", "W-001", "--diff")
        check("hashes: diff without store", out.strip() == "re-review: all (no stored hashes)")
        rc, out = run(root, "hashes", "--dossier", "W-001", "--write")
        stored = json.load(open(os.path.join(root, ".discovery/contract-review/W-001.json")))
        check("hashes: write stores members and commit", rc == 0 and set(stored["members"]) == {"flush", "Queue", "impl Queue", "drain"} and stored["commit"] == base)
        rc, out = run(root, "hashes", "--dossier", "W-001", "--diff")
        check("hashes: unchanged is none", out.strip() == "re-review: none")
        dp = p1
        t = read(dp)
        open(dp, "w").write(t.replace("Result<(), Error>", "Result<usize, Error>"))
        rc, out = run(root, "hashes", "--dossier", "W-001", "--diff")
        check("hashes: changed member", "changed flush" in out and "changed Queue" not in out)
        check("hashes: user propagation", "user drain (uses flush)" in out and "re-review: flush, drain" in out)
        open(dp, "w").write(t.replace("pub struct Queue { len: usize }", "pub struct Queue { len: usize }\n\npub fn extra() {}").replace("pub fn drain(q: &Queue) -> usize { flush(q) }", ""))
        rc, out = run(root, "hashes", "--dossier", "W-001", "--diff")
        check("hashes: added and removed", "added extra" in out and "removed drain" in out)
        open(dp, "w").write(t.replace("/// Flush the queue once.", "/// Flush the\n   queue once."))
        rc, out = run(root, "hashes", "--dossier", "W-001", "--diff")
        check("hashes: doc comment is part of the member", "changed flush" in out)
        open(dp, "w").write(t.replace("pub fn flush(q: &Queue)", "pub fn   flush(q:  &Queue)"))
        rc, out = run(root, "hashes", "--dossier", "W-001", "--diff")
        check("hashes: whitespace normalised", out.strip() == "re-review: none")
        open(dp, "w").write(t)
        members = split_members("csharp", "/// doc\npublic class Foo\n{\n    public int Bar(int x);\n    public string Name { get; }\n}\ndef_x\n")
        check("hashes: C# class methods split out", [m[0] for m in members] == ["Foo", "Foo.Bar", "Foo.Name"])
        members = split_members("python", "@dec\ndef f(a):\n    pass\n\nclass C:\n    def m(self): ...\n")
        check("hashes: python def and class", [m[0] for m in members] == ["f", "C"] and members[0][1].startswith("@dec"))

        # --- refresh
        rc, out = run(root, "refresh", "--dossier", "W-005")
        check("refresh: no baseline_commit", "refresh: no baseline_commit" in out and rc == 0)
        mk(root, "W-006", slug="r", base=base, anchors=("src/flush.rs:2", "src/gone.rs:1"))
        rc, out = run(root, "refresh", "--dossier", "W-006")
        check("refresh: clean", out.strip() == "refresh: clean" and rc == 0)
        w("src/other.rs", "fn other() { 1 }\n")
        os.remove(os.path.join(root, "src/gone.rs"))
        g("add", "-A")
        g("commit", "-qm", "c2")
        rc, out = run(root, "refresh", "--dossier", "W-006")
        check("refresh: anchor gone", "anchor gone src/gone.rs:1" in out and rc == 0)
        w("src/flush.rs", "// line1\npub fn flush(x: u8) {}\n// line3\n// line4\n// line5\n")
        g("add", "-A")
        g("commit", "-qm", "c3")
        rc, out = run(root, "refresh", "--dossier", "W-006", "--ref", "HEAD~1")
        check("refresh: --ref earlier commit stays clean of contract change", "contract target" not in out and rc == 0)
        rc, out = run(root, "refresh", "--dossier", "W-006")
        check("refresh: contract target changed exits 3", rc == 3 and "contract target changed src/flush.rs: flush" in out)
        check("refresh: anchor moved", "anchor moved? src/flush.rs:2" in out)

        # --- misc
        rc, out = run(root, "register", "--dossier", "W-999")
        check("missing dossier is an error, not a traceback", rc == 2 and "Traceback" not in out)
    if failures:
        print(f"{len(failures)} FAILED")
        return 1
    print("all ok")
    return 0


# --------------------------------------------------------------------------

def main(argv=None):
    ap = argparse.ArgumentParser(description="Mechanical helper for /plan (see module docstring)")
    ap.add_argument("--selftest", action="store_true", help="check the checker")
    sub = ap.add_subparsers(dest="cmd")

    def add(name, **kw):
        sp = sub.add_parser(name, **kw)
        sp.add_argument("--root", default=".")
        return sp

    s = add("pins")
    s.add_argument("--files", nargs="+", required=True)
    s = add("register")
    s.add_argument("--dossier", required=True)
    s.add_argument("--ready", action="store_true")
    s = add("second-reader")
    s.add_argument("--dossier", required=True)
    s = add("overlap")
    s.add_argument("--dossier")
    s.add_argument("--files", nargs="*")
    s.add_argument("--terms", nargs="*")
    s = add("hashes")
    s.add_argument("--dossier", required=True)
    g = s.add_mutually_exclusive_group(required=True)
    g.add_argument("--write", action="store_true")
    g.add_argument("--diff", action="store_true")
    s = add("refresh")
    s.add_argument("--dossier", required=True)
    s.add_argument("--ref")
    args = ap.parse_args(argv)
    if args.selftest:
        return selftest()
    if not args.cmd:
        ap.error("a subcommand is required unless --selftest")
    if args.cmd == "overlap" and not (args.dossier or args.files or args.terms):
        ap.error("overlap needs --dossier or --files/--terms")
    fn = {"pins": cmd_pins, "register": cmd_register, "second-reader": cmd_second_reader,
          "overlap": cmd_overlap, "hashes": cmd_hashes, "refresh": cmd_refresh}[args.cmd]
    try:
        return fn(args)
    except Fail as e:
        print(f"plan_flow: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
