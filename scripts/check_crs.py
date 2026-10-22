#!/usr/bin/env python3
"""Check a reviewer's change-request document by its fields, and list the defects to prove.

Phase 7 demotes a change request "by the fields, never the merits": a CR that does not
state what its lens requires is a note, not a work order. That check is mechanical, so it
is a script: the orchestrator runs this on each returned document instead of reading it,
and writes the ledger from the result. The same script lists every `DEFECT-n` section —
a claim that the code breaks a `CONTRACT` promise no test pins — which Phase 7 proves
with a failing test before any fix, and filters the document the fix implementer gets:
the sections kept go byte for byte, the dropped ones (a demoted CR, an unproven defect)
do not go at all.

# Usage

    check_crs.py DOCUMENT                          # one line per section, then a summary
    check_crs.py DOCUMENT --drop ID[,ID...] --out FILE
    check_crs.py --selftest

Output, one line per section, then the summary:

    CR-1 ok
    CR-2 demote (missing: Siblings, Rule)
    CR-2 follow-up ok
    DEFECT-1 prove — Input: an empty token list — Location: src/a.rs:40
    CRS: 2 ok, 1 demoted; DEFECTS: 1 to prove

# The fields each section must state

- `## CR-<n>: <requirement>` — Location (`path:line`), Observed, Required, Behaviour held
  fixed, Rule, Severity (`blocking` | `should-fix`), Siblings.
- `## CR-<n> follow-up: <requirement>` — Answers, Location; the rest may be one
  `- Observed / Required / ...: as above` line, and no Siblings (it is inside one CR's
  footprint by definition).
- `## DEFECT-<n>: <the promise the code breaks>` — Location, Contract (the promise,
  quoted), Input (the concrete input or state), Observed, Expected, Siblings, Severity.

A field counts when its `- Name:` bullet is present with a non-empty value. `Siblings`
is satisfied by `none` only with what was searched: `none (searched: ...)`; a bare
`none` is a missing field, because "I did not look" and "there are none" read the same.

`--drop` writes DOCUMENT to `--out` without the named sections (ids as printed: `CR-2`,
`DEFECT-1`, `CR-2 follow-up`); every other byte is kept, the heading line included. An id
that names no section is refused, so a typo cannot drop nothing silently.

Exit codes: 0 the check printed, or the filtered document was written; 2 unusable input
(no such file, an unknown `--drop` id).

No third-party imports: this runs wherever python3 does.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import tempfile

HEADING = re.compile(r"^## (CR-\d+(?: follow-up)?|DEFECT-\d+):", re.M)
REQUIRED = {
    "CR": ["Location", "Observed", "Required", "Behaviour held fixed", "Rule", "Severity", "Siblings"],
    "follow-up": ["Answers", "Location"],
    "DEFECT": ["Location", "Contract", "Input", "Observed", "Expected", "Siblings", "Severity"],
}
LOCATION = re.compile(r"\S+:\d+")
SEVERITY = {"blocking", "should-fix"}


def sections(text: str) -> list[tuple[str, int, int]]:
    """(id, start, end) per CR / follow-up / DEFECT section; a section ends at the next `## `
    heading or the end of the document."""
    heads = [m for m in re.finditer(r"^## ", text, re.M)]
    out = []
    for i, m in enumerate(heads):
        hit = HEADING.match(text, m.start())
        if not hit:
            continue
        end = heads[i + 1].start() if i + 1 < len(heads) else len(text)
        out.append((hit.group(1), m.start(), end))
    return out


def fields(body: str) -> dict[str, str]:
    out = {}
    for m in re.finditer(r"^- ([A-Z][\w /]*?):[ \t]*(.*)$", body, re.M):
        name, value = m.group(1).strip(), m.group(2).strip()
        if "/" in name:  # `- Observed / Required / ...: as above` in a follow-up
            for part in name.split("/"):
                out.setdefault(part.strip(), value)
        else:
            out[name] = value
    return out


def check(sid: str, body: str) -> list[str]:
    kind = "DEFECT" if sid.startswith("DEFECT") else "follow-up" if "follow-up" in sid else "CR"
    got = fields(body)
    missing = []
    for name in REQUIRED[kind]:
        value = got.get(name, "")
        if not value or value.startswith("{{"):
            missing.append(name)
        elif name == "Location" and not LOCATION.search(value):
            missing.append("Location (path:line)")
        elif name == "Severity" and value.split()[0].strip("`*") not in SEVERITY:
            missing.append("Severity (blocking | should-fix)")
        elif name == "Siblings" and re.fullmatch(r"`?none`?\.?", value, re.I):
            missing.append("Siblings (none: say what was searched)")
    return missing


def summary_lines(text: str) -> list[str]:
    lines, ok, demoted, defects = [], 0, 0, 0
    for sid, start, end in sections(text):
        missing = check(sid, text[start:end])
        if missing:
            lines.append(f"{sid} demote (missing: {', '.join(missing)})")
            demoted += 1
        elif sid.startswith("DEFECT"):
            got = fields(text[start:end])
            lines.append(f"{sid} prove — Input: {got['Input'][:160]} — Location: {got['Location']}")
            defects += 1
        else:
            lines.append(f"{sid} ok")
            ok += 1
    lines.append(f"CRS: {ok} ok, {demoted} demoted; DEFECTS: {defects} to prove")
    return lines


def drop(text: str, ids: list[str]) -> str:
    found = {sid: (start, end) for sid, start, end in sections(text)}
    unknown = [i for i in ids if i not in found]
    if unknown:
        raise ValueError(f"no section {', '.join(unknown)} in the document")
    for sid in sorted(ids, key=lambda i: found[i][0], reverse=True):
        start, end = found[sid]
        text = text[:start] + text[end:]
    return text


# ---------------------------------------------------------------- selftest

DOC = """# Change requests — architecture (round 1)

## CR-1: name the cause when the credential file cannot be read
- Location: crates/platform/src/cloud/gcp.rs:151
- Observed: map_err(|_| ...) drops the io::Error kind
- Required: carry the kind (not found vs permission denied)
- Behaviour held fixed: the same error variant is returned
- Rule: engineering-standards § errors carry their cause
- Severity: should-fix
- Siblings: gcp.rs:316, gcp.rs:323 (same shape, inside SCOPE)

## CR-2: log the fallback
- Location: crates/platform/src/cloud/selection.rs:288
- Observed: Err(_) => default, silently
- Required: a warn with the path and the error
- Behaviour held fixed: still falls back
- Rule: TCG 2000
- Severity: should-fix
- Siblings: none

## CR-2 follow-up: the new helper leaks the path
- Answers: CR-2 (structural — the fix added load_env_file)
- Location: crates/platform/src/cloud/selection.rs:300
- Observed / Required / Behaviour held fixed / Rule / Severity: as above

## DEFECT-1: an empty catalog must be refused, not loaded
- Location: crates/platform/src/cloud/bootstrap.rs:97
- Contract: "returns CatalogError::Empty when the catalog lists no secrets"
- Input: the catalog text `{"secrets": []}`
- Observed: returns Ok with an empty map
- Expected: Err(CatalogError::Empty)
- Siblings: none (searched: every `from_str` on catalog text in crates/platform)
- Severity: blocking
"""


def selftest() -> int:
    failures = []

    def ok(name, cond, detail=""):
        print(("PASS  " if cond else "FAIL  ") + name + ("" if cond else f"  {detail}"))
        if not cond:
            failures.append(name)

    lines = summary_lines(DOC)
    ok("a complete CR is ok", lines[0] == "CR-1 ok", lines)
    ok("a bare `none` sibling demotes", lines[1] == "CR-2 demote (missing: Siblings (none: say what was searched))", lines)
    ok("a follow-up needs no siblings and takes the combined line", lines[2] == "CR-2 follow-up ok", lines)
    ok("a complete defect is listed to prove", lines[3].startswith("DEFECT-1 prove — Input: the catalog text"), lines)
    ok("the summary counts", lines[-1] == "CRS: 2 ok, 1 demoted; DEFECTS: 1 to prove", lines)
    bad = DOC.replace("- Location: crates/platform/src/cloud/gcp.rs:151", "- Location: gcp.rs") \
             .replace("- Severity: should-fix\n- Siblings: gcp.rs", "- Severity: minor\n- Siblings: gcp.rs")
    ok("a location without a line and an unknown severity demote",
       summary_lines(bad)[0] == "CR-1 demote (missing: Location (path:line), Severity (blocking | should-fix))", summary_lines(bad))
    ok("an unfilled placeholder is missing",
       "Rule" in summary_lines(DOC.replace("- Rule: TCG 2000", "- Rule: {{the standard}}"))[1])
    kept = drop(DOC, ["CR-2", "DEFECT-1"])
    ok("drop removes exactly the named sections, keeps every other byte",
       "## CR-2:" not in kept and "## DEFECT-1" not in kept and "## CR-2 follow-up" in kept
       and kept.startswith(DOC[:DOC.index("## CR-2:")]), kept)
    try:
        drop(DOC, ["CR-9"])
        ok("an unknown id is refused", False)
    except ValueError:
        ok("an unknown id is refused", True)
    with tempfile.TemporaryDirectory() as tmp:
        src, dst = os.path.join(tmp, "doc.md"), os.path.join(tmp, "fix.md")
        with open(src, "w") as fh:
            fh.write(DOC)
        rc = main([src, "--drop", "CR-2,DEFECT-1", "--out", dst])
        ok("--drop writes the filtered document", rc == 0 and open(dst).read() == kept)
    print(f"selftest: {len(failures)} failure(s)")
    return 1 if failures else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("document", nargs="?")
    ap.add_argument("--drop", help="comma-separated section ids to leave out")
    ap.add_argument("--out", help="where --drop writes the filtered document")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)
    if args.selftest:
        return selftest()
    if not args.document or not os.path.isfile(args.document):
        print("check_crs: a DOCUMENT file is required", file=sys.stderr)
        return 2
    with open(args.document, encoding="utf-8") as fh:
        text = fh.read()
    if args.drop is not None:
        if not args.out:
            print("check_crs: --drop needs --out", file=sys.stderr)
            return 2
        try:
            kept = drop(text, [i.strip() for i in args.drop.split(",") if i.strip()])
        except ValueError as err:
            print(f"check_crs: {err}", file=sys.stderr)
            return 2
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(kept)
        print(f"wrote {args.out}")
        return 0
    print("\n".join(summary_lines(text)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
