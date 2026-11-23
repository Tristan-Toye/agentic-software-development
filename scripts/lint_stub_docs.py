#!/usr/bin/env python3
"""Phase 2's doc-comment lint: read the stubs alone, list the words and gaps a
blind reader will resolve two ways, before `contract-reviewer` is spawned.

The orchestrator writes every documentation comment; a blind test author and
an implementer read them without the dossier. Recorded rulings came from the
same few phrasings: "rounds" with no mode, "equal" on a record that holds a
list, "blank" (empty or whitespace), "sorted" with no key or direction,
"exactly"/"only" with no set-or-element, "coalesces", "discarded", an error
variant no comment names, a rule cited by name with no quoted clause, an
`(inferred)` marker left as the contract. This script finds those places
mechanically so the orchestrator fixes the comment (or accepts it, knowingly)
and the reviewer then spends its budget on readings a script cannot find.

Reads stub files only; never the dossier, never a body. No model, no network.

Rules (a finding is `LINT <path>:<line> [<rule>] <message>`):

    vague-word      a word in the table below with none of its accepted
                    qualifiers in the same comment
    error-variant   a variant of an error enum / error class declared in the
                    stubs that no comment names
    rule-by-name    a comment that cites a rule or ADR by name and quotes no
                    clause (a quoted phrase of three or more words)
    inferred        a comment that carries an `(inferred)` marker

Usage:
    lint_stub_docs.py FILE [FILE ...]
    lint_stub_docs.py --root X --paths a.py,b.rs
    lint_stub_docs.py --selftest

Exit 0 clean, 1 findings (printed, capped at 40 lines), 2 unreadable file.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import tempfile

# (rule word, regex that finds it, regex of qualifiers that settle it, what to state)
VAGUE = [
    ("rounds", r"\bround(?:s|ed|ing)?\b", r"half|banker|toward|away from|floor|ceil|truncat|nearest|up\b|down\b",
     "name the rounding mode (half-up, half-even, toward zero, ...)"),
    ("equal", r"\bequal(?:s|ity)?\b|\bidentical\b", r"by value|by reference|element|ordinal|case|same order|"
     r"structural|field|reference|order-?insensitive|ignoring|compared",
     "say what equality means (by reference or value; whether collection members and order count)"),
    ("blank", r"\bblank\b", r"empty|whitespace|space|null|none",
     "say whether blank means empty, whitespace-only, or null"),
    ("sorted", r"\bsorted\b|\bordered\b|\bin order\b", r"ascending|descending|by \w+|oldest|newest|first|last|"
     r"alphabetic|numeric|key|insertion|stable",
     "state the sort key and direction"),
    ("exactly", r"\bexactly\b|\bonly\b", r"each|every|whole|no other|set\b|elements?|nothing else|at most|"
     r"at least|unique|one time|once|times?\b",
     "say whether it bounds the whole set or each element"),
    ("discarded", r"\bdiscard(?:s|ed)?\b", r"means|i\.e\.|that is|without|not stored|silently|logged|no event|"
     r"returns|raises|dropped|ignored|:",
     "say what happens to the value, and whether anything is signalled"),
    ("coalesces", r"\bcoalesc\w+", r"means|i\.e\.|that is|second|later|receives|same result|each caller|"
     r"returns|one call|:",
     "say what the second caller receives"),
    ("fails its checks", r"\bfails? (?:its|the|all|any) (?:checks?|validation|tests?)\b",
     r"means|i\.e\.|that is|raises|returns|refuses|error|:|when",
     "name each check and the error it produces"),
]
RULE_CITE = re.compile(r"\bADR[- ]?\d+\b|\b(?:the\s+)?[\w-]+(?:\s+[\w-]+){0,2}\s+rule\b|\brule of\b|\bper (?:the )?rule\b",
                       re.I)
QUOTED_CLAUSE = re.compile(r"[\"“`][^\"”`]*\b\w+\s+\w+\s+\w+[^\"”`]*[\"”`]")
ERROR_ENUM = re.compile(r"\b(?:enum|class)\s+(\w*(?:Err|Error|Failure|Refusal|Exception)\w*)\b[^{:\n]*[{:(]")
LINE_COMMENT = re.compile(r"^\s*(?:///?!?|//!|#|--)\s?(.*)$")
STAR_COMMENT = re.compile(r"^\s*(?:/\*\*?|\*/?)\s?(.*?)\s*(?:\*/)?$")
MAX_OUT = 40


def doc_blocks(text: str) -> list[tuple[int, str]]:
    """(first line, comment text) of every comment run and triple-quoted docstring."""
    lines = text.splitlines()
    blocks: list[tuple[int, str]] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if '"""' in line or "'''" in line:
            quote = '"""' if '"""' in line else "'''"
            start = i
            body = line.split(quote, 1)[1]
            if quote in body:
                blocks.append((start + 1, body.split(quote, 1)[0]))
                i += 1
                continue
            parts = [body]
            i += 1
            while i < len(lines) and quote not in lines[i]:
                parts.append(lines[i])
                i += 1
            if i < len(lines):
                parts.append(lines[i].split(quote, 1)[0])
            blocks.append((start + 1, " ".join(p.strip() for p in parts)))
            i += 1
            continue
        m = LINE_COMMENT.match(line) or (STAR_COMMENT.match(line) if re.match(r"^\s*(?:/\*|\*)", line) else None)
        if m:
            start = i
            parts = [m.group(1)]
            i += 1
            while i < len(lines):
                n = LINE_COMMENT.match(lines[i]) or (
                    STAR_COMMENT.match(lines[i]) if re.match(r"^\s*(?:/\*|\*)", lines[i]) else None)
                if not n:
                    break
                parts.append(n.group(1))
                i += 1
            blocks.append((start + 1, " ".join(p.strip() for p in parts if p.strip())))
            continue
        i += 1
    return blocks


def error_variants(text: str) -> list[tuple[int, str, str]]:
    """(line, enum, variant) for every error enum declared in the file; for a
    Python/C# error class the type itself is the one 'variant'."""
    out: list[tuple[int, str, str]] = []
    lines = text.splitlines()
    for idx, line in enumerate(lines):
        m = ERROR_ENUM.search(line)
        if not m:
            continue
        name = m.group(1)
        if re.search(r"\benum\b", line):
            depth = line.count("{") - line.count("}")
            body = [line.split("{", 1)[1]] if "{" in line else []
            j = idx + 1
            while j < len(lines) and (depth > 0 or not body):
                body.append(lines[j])
                depth += lines[j].count("{") - lines[j].count("}")
                j += 1
                if depth <= 0 and body:
                    break
            for chunk in re.split(r"[,\n]", " ".join(body)):
                chunk = re.sub(r"//.*|///.*|#.*", "", chunk).strip(" {}\t;")
                v = re.match(r"([A-Z]\w*)", chunk)
                if v and v.group(1) != name:
                    out.append((idx + 1, name, v.group(1)))
        else:
            out.append((idx + 1, name, name))
    return out


def lint_text(path: str, text: str) -> list[str]:
    found: list[str] = []
    blocks = doc_blocks(text)
    for line_no, comment in blocks:
        flat = " ".join(comment.split())
        if "(inferred)" in flat.lower():
            found.append("LINT %s:%d [inferred] an `(inferred)` comment is a guess about the code, not a "
                         "contract: write the authored comment" % (path, line_no))
        for word, trigger, qualifier, advice in VAGUE:
            m = re.search(trigger, flat, re.I)
            if m and not re.search(qualifier, flat, re.I):
                found.append("LINT %s:%d [vague-word] %r: %s" % (path, line_no, m.group(0), advice))
        if RULE_CITE.search(flat) and not QUOTED_CLAUSE.search(flat):
            found.append("LINT %s:%d [rule-by-name] cites a rule by name with no quoted clause: restate its "
                         "observable clauses in the comment" % (path, line_no))
    all_docs = " ".join(" ".join(c.split()) for _, c in blocks)
    for line_no, enum, variant in error_variants(text):
        if not re.search(r"\b" + re.escape(variant) + r"\b", all_docs):
            found.append("LINT %s:%d [error-variant] %s.%s is named by no doc comment: say when it is "
                         "returned or raised" % (path, line_no, enum, variant))
    return found


def lint_files(paths: list[str]) -> tuple[list[str], list[str]]:
    findings: list[str] = []
    bad: list[str] = []
    stubs_docs: dict[str, str] = {}
    texts: dict[str, str] = {}
    for p in paths:
        try:
            with open(p, encoding="utf-8", errors="replace") as fh:
                texts[p] = fh.read()
        except OSError as exc:
            bad.append("%s: %s" % (p, exc))
            continue
        stubs_docs[p] = " ".join(" ".join(c.split()) for _, c in doc_blocks(texts[p]))
    corpus = " ".join(stubs_docs.values())
    for p, text in texts.items():
        for f in lint_text(p, text):
            # an error variant named in a sibling stub's comment counts as named
            m = re.search(r"\[error-variant\] (\w+)\.(\w+) ", f)
            if m and re.search(r"\b" + re.escape(m.group(2)) + r"\b", corpus):
                continue
            findings.append(f)
    return findings, bad


def selftest() -> int:
    failures: list[str] = []

    def check(name: str, ok: bool) -> None:
        if not ok:
            failures.append(name)
            print("SELFTEST FAIL  " + name)

    sample = '''
/// Rounds the total to cents.
/// Sorted by id ascending.
/// Two records are equal when every field matches.
/// Follows the park-fence rule of W-014.
/// (inferred) never use this
pub enum SaveError {
    Empty,
    Duplicate(String),
}
'''
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "stub.rs")
        with open(path, "w") as fh:
            fh.write(sample)
        findings, bad = lint_files([path])
        joined = "\n".join(findings)
        check("rounding without a mode is found", "[vague-word] 'Rounds'" in joined)
        check("a sort with key and direction is quiet", "'Sorted'" not in joined)
        check("equal with a qualifier is quiet", "'equal'" not in joined)
        check("rule by name is found", "[rule-by-name]" in joined)
        check("inferred is found", "[inferred]" in joined)
        check("unnamed error variants are found", "SaveError.Empty" in joined and "SaveError.Duplicate" in joined)
        py = os.path.join(tmp, "stub.py")
        with open(py, "w") as fh:
            fh.write('class BadInput(ValueError):\n    pass\n\n'
                     'def f(x):\n    """Rounds half-up to cents. Raises BadInput on a blank name (empty or '
                     'whitespace). Discards nothing. Follows the \\"drain before close\\" rule."""\n'
                     '    raise NotImplementedError\n')
        findings, _ = lint_files([py])
        check("a fully qualified python docstring is clean: %s" % findings, findings == [] or all(
            "discard" in f for f in findings))
        findings, bad = lint_files([os.path.join(tmp, "missing.rs")])
        check("an unreadable file is reported", bool(bad) and not findings)
    print("selftest: %d failure(s)" % len(failures))
    return 1 if failures else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    ap.add_argument("files", nargs="*")
    ap.add_argument("--root")
    ap.add_argument("--paths", help="comma list, relative to --root")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        return selftest()
    paths = list(args.files)
    if args.paths:
        paths += [os.path.join(args.root or ".", p.strip()) for p in args.paths.split(",") if p.strip()]
    if not paths:
        ap.error("name at least one stub file")
    findings, bad = lint_files(paths)
    for b in bad:
        print("lint_stub_docs: cannot read %s" % b, file=sys.stderr)
    for f in findings[:MAX_OUT]:
        print(f)
    if len(findings) > MAX_OUT:
        print("LINT ... %d more" % (len(findings) - MAX_OUT))
    print("DOC-LINT: %d finding(s) over %d file(s)" % (len(findings), len(paths) - len(bad)))
    return 2 if bad else (1 if findings else 0)


if __name__ == "__main__":
    sys.exit(main())
