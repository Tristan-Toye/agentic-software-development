#!/usr/bin/env python3
"""Phase 3's criteria linter: four defects a dossier's acceptance criteria
carry that no agent notices until a test author turns them into an assertion.

Recorded rulings traced to criteria that contradicted themselves or named an
unmeasurable quantity. Each rule below is a regex or a comparison over the
dossier's `## Acceptance criteria` list; it reads that section only and spends
no model.

    example-vs-predicate   a criterion states a comparison ("below 1", "more
                           than 5", "between 2 and 9") and an example
                           ("e.g. 3", "for example 0", "such as 12") that does
                           not satisfy any comparison of the same criterion
    timing-clock           a criterion with a duration ("within 2 s", "under
                           500 ms", "at most 3 seconds") that names no clock
                           start and no clock stop ("from X until Y",
                           "after X ... when Y", "between X and Y")
    count-environment      a criterion with a count of things ("26 values",
                           "exactly 4 rows") that carries no `env:` annotation
                           and no frozen command in parentheses (`grep ...`)
    changed-file-set       a criterion that pins the changed-file set ("exactly
                           three files", "a fourth file falsifies") and names
                           no decision record (`docs/adr`, ADR): Phase 8a ships
                           one, so the criterion forbids the record (LRN-0115)

    no-discriminating-input  a criterion says the behaviour passes/holds/works ("by construction") and names no input
                           value, identifier or negative case, so no test can fail it
    refusal-text-uncovered   the `## Contract` quotes a refusal or error text (a backticked phrase on a line that
                           refuses, rejects or raises) that no criterion names: the text is unpinned
    per-case-undefined       a criterion says "per case" / "each case" / "every variant" and enumerates no cases

A finding is a defect of the dossier, fixed in the dossier (`dossier_edit.py`)
before the fan-out; it is never a question for a blind author.

Usage:
    lint_criteria.py DOSSIER.md
    lint_criteria.py --selftest

Exit 0 clean, 1 findings, 2 no criteria section / unreadable.
"""

from __future__ import annotations

import os
import re
import sys
import tempfile

NUM = r"-?\d+(?:\.\d+)?"
COMPARATORS = [
    (re.compile(r"\b(?:below|under|less than|fewer than|smaller than|<)\s*(" + NUM + ")", re.I), lambda x, n: x < n),
    (re.compile(r"\b(?:above|over|more than|greater than|larger than|exceeds?|>)\s*(" + NUM + ")", re.I),
     lambda x, n: x > n),
    (re.compile(r"\b(?:at most|no more than|up to|<=|≤)\s*(" + NUM + ")", re.I), lambda x, n: x <= n),
    (re.compile(r"\b(?:at least|no fewer than|no less than|>=|≥)\s*(" + NUM + ")", re.I), lambda x, n: x >= n),
]
BETWEEN = re.compile(r"\bbetween\s+(" + NUM + r")\s+and\s+(" + NUM + ")", re.I)
EXAMPLE = re.compile(r"(?:\be\.g\.|\beg\b|for example|for instance|such as)[,:]?\s*`?(" + NUM + ")", re.I)
DURATION = re.compile(r"\b(?:within|under|below|at most|no more than|less than|in under|takes?|after)\s+"
                      r"(?:about\s+)?" + NUM + r"\s*(?:ms|milliseconds?|s|secs?|seconds?|minutes?|min|hours?)\b", re.I)
CLOCK_START = re.compile(r"\b(?:from|after|since|starting|once|measured from|timer starts?)\b", re.I)
CLOCK_STOP = re.compile(r"\b(?:until|to|before|when|stops?|ends?|measured to|returns?|completes?|arrives?)\b", re.I)
COUNT = re.compile(r"\b(?:exactly\s+)?(\d{1,6})\s+(?!ms\b|s\b|seconds?\b|minutes?\b|hours?\b|%)([a-z][\w-]*)", re.I)
COUNT_NOUNS = re.compile(r"\b(?:values?|rows?|columns?|keys?|items?|entries|entry|files?|tests?|members?|"
                         r"events?|records?|fields?|bytes?|lines?|calls?|retries|attempts?|ids?|tables?|"
                         r"migrations?|variants?|kinds?|nodes?|edges?|users?|requests?)\b", re.I)
PASSES = re.compile(r"\b(?:passes|holds|works|by construction|is correct|behaves)\b", re.I)
INPUT_VALUE = re.compile(r"`[^`]+`|\d|\"[^\"]+\"|\b(?:refus\w*|reject\w*|raises?|throws?|errors?|fails?|empty|absent|missing|none|null|"
                         r"invalid|unknown|exit)\b", re.I)
PER_CASE = re.compile(r"\b(?:per|for each|for every|in each|in every|each|every)[- ](?:case|variant|kind|mode|scenario|"
                      r"branch|class|type|input)s?\b", re.I)
# A rule whose dry run over the real dossiers showed a high false-positive rate reports as a warning: printed with
# a `CRITERIA-LINT-WARN` prefix, never failing the run. `refusal-text-uncovered` keeps one in five hits a quoted
# command or log phrase rather than a refusal text, so it warns until its detection is tighter.
WARN_RULES = frozenset({"refusal-text-uncovered"})
CHANGED_SET = re.compile(r"git diff[^.\n]*--name-only|\bchanged[- ]files?\b|\bfiles? changed\b|\bname[sd]? (?:exactly )?\w+ files\b", re.I)
PINNED_SET = re.compile(r"\bexactly \w+ files?\b|\b(?:second|third|fourth|fifth|sixth|extra|further|additional|other) files?\b"
                        r"|\bno other files?\b|\bnothing else\b|\bonly these\b", re.I)
RECORD = re.compile(r"docs/adr|decision record|\bADRs?\b", re.I)
ENV = re.compile(r"\benv:\s*\S|\((?:grep|git|wc|find|rg|ls)\b[^)]*\)", re.I)


def criteria(text: str) -> list[tuple[int, str]]:
    m = re.search(r"^## Acceptance criteria\s*$(.*?)(?=^## |\Z)", text, re.M | re.S)
    if not m:
        return []
    items: list[tuple[int, str]] = []
    for line in m.group(1).splitlines():
        n = re.match(r"^\s*(\d+)[.)]\s+(.*)", line)
        if n:
            items.append((int(n.group(1)), n.group(2)))
        elif items and line.strip():
            items[-1] = (items[-1][0], items[-1][1] + " " + line.strip())
    return items


def lint_one(number: int, text: str) -> list[str]:
    out: list[str] = []
    flat = re.sub(r"`", "", text)
    predicates = [(rx, fn, float(m)) for rx, fn in COMPARATORS for m in rx.findall(flat)]
    ranges = [(float(a), float(b)) for a, b in BETWEEN.findall(flat)]
    for ex in EXAMPLE.findall(flat):
        x = float(ex)
        if not (predicates or ranges):
            continue
        ok = any(fn(x, n) for _, fn, n in predicates) or any(min(a, b) <= x <= max(a, b) for a, b in ranges)
        if not ok:
            out.append("criterion %d [example-vs-predicate] the example %s satisfies none of the comparisons "
                       "it sits beside: fix the example or the predicate" % (number, ex))
    for m in DURATION.finditer(flat):
        if not (CLOCK_START.search(flat[:m.start()] + " " + flat[m.end():]) and CLOCK_STOP.search(flat)):
            out.append("criterion %d [timing-clock] %r names no clock start and stop: say what starts the "
                       "clock and what event stops it" % (number, m.group(0)))
            break
    counts = [m for m in COUNT.finditer(flat) if COUNT_NOUNS.match(m.group(2))]
    if counts and not ENV.search(text):
        out.append("criterion %d [count-environment] %r states a count with no `env:` and no frozen command: "
                   "say where the number is counted" % (number, counts[0].group(0)))
    claim = re.sub(r"\(owner:[^)]*\)", "", text)
    if PASSES.search(claim) and not INPUT_VALUE.search(PASSES.sub("", claim)):
        out.append("criterion %d [no-discriminating-input] says the behaviour %r but names no input value, "
                   "identifier or negative case: say which input makes it pass and which makes it fail"
                   % (number, PASSES.search(flat).group(0)))
    pc = PER_CASE.search(flat)
    if pc and len(re.findall(r"`[^`]+`", text)) < 2 and not re.search(r"[:(][^)]*,[^)]*[,)]|\bone of\b", flat):
        out.append("criterion %d [per-case-undefined] %r enumerates no cases: list each case, or say how the "
                   "set is derived" % (number, pc.group(0)))
    if CHANGED_SET.search(flat) and PINNED_SET.search(flat) and not RECORD.search(flat):
        out.append("criterion %d [changed-file-set] pins the changed files and names no decision record: "
                   "Phase 8a ships an ADR and its index, so list `docs/adr/` beside the files or scope the "
                   "criterion to the change" % number)
    return out


REFUSAL_LEAD = re.compile(r"\b(?:refus\w*|reject\w*|raises?|throws?|errors?|fails?)\b[^`\n]{0,48}$", re.I)


def contract_refusal_texts(text: str) -> list[str]:
    m = re.search(r"^## Contract\s*$(.*?)(?=^## |\Z)", text, re.M | re.S)
    out: list[str] = []
    for ln in (m.group(1) if m else "").splitlines():
        for mt in re.finditer(r"`([^`]+)`", ln):
            tok = mt.group(1).strip()
            prose = " " in tok and not tok.startswith("-") and tok.lower() == tok
            if prose and not re.search(r"[(){}=<>;:/\\]|->", tok) and REFUSAL_LEAD.search(ln[:mt.start()]):
                if tok not in out:
                    out.append(tok)
    return out


def is_warning(finding: str) -> bool:
    m = re.search(r"\[([\w-]+)\]", finding)
    return bool(m and m.group(1) in WARN_RULES)


def lint_text(text: str) -> tuple[list[str], int]:
    items = criteria(text)
    findings: list[str] = []
    for n, body in items:
        findings.extend(lint_one(n, body))
    if items:
        crit = " ".join(b for _, b in items).replace("`", "").lower()
        for tok in contract_refusal_texts(text):
            if tok.lower() not in crit:
                findings.append("contract [refusal-text-uncovered] the refusal text %r appears in no criterion: "
                                "pin it in one" % tok)
    return findings, len(items)


def selftest() -> int:
    failures: list[str] = []

    def check(name: str, ok: bool) -> None:
        if not ok:
            failures.append(name)
            print("SELFTEST FAIL  " + name)

    good = "\n".join([
        "## Acceptance criteria",
        "1. A size below 1 (e.g. 0) is refused. (owner: tests/a.py; env: unit)",
        "2. The flush returns within 2 s measured from the call until the store acknowledges. "
        "(owner: tests/a.py; env: CI)",
        "3. The catalog holds 27 values. (owner: tests/b.py; env: repo checkout)",
        "",
        "## Notes",
        "4. nothing",
    ])
    f, n = lint_text(good)
    check("a clean list has no findings: %s" % f, f == [] and n == 3)
    bad = "\n".join([
        "## Acceptance criteria",
        "1. A size below 1 (e.g. 5) is refused. (owner: tests/a.py; env: unit)",
        "2. The flush finishes within 2 seconds. (owner: tests/a.py; env: CI)",
        "3. The catalog holds exactly 27 values. (owner: tests/b.py)",
        "4. Between 2 and 9 items, for example 12, are kept. (owner: x; env: y)",
    ])
    f, n = lint_text(bad)
    j = "\n".join(f)
    check("example violating the predicate", "criterion 1 [example-vs-predicate]" in j)
    check("timing with no clock", "criterion 2 [timing-clock]" in j)
    check("count with no env", "criterion 3 [count-environment]" in j)
    check("example outside a between range", "criterion 4 [example-vs-predicate]" in j)
    pinned = "\n".join([
        "## Acceptance criteria",
        "1. `git diff --name-only` over the merged branch names exactly three files; a fourth file falsifies. (owner: a)",
    ])
    f, _ = lint_text(pinned)
    check("LRN-0115: a pinned changed-file set with no record is flagged", any("[changed-file-set]" in x for x in f))
    f, _ = lint_text(pinned.replace("exactly three files;", "exactly three files plus the ADR and docs/adr/index.md;"))
    check("LRN-0115: the same criterion naming the record passes", not f)
    check("no criteria section", lint_text("## Other\n1. x\n") == ([], 0))
    # review-cluster rules: each fires on its defect and is silent on its negative control
    unfalsifiable = ("## Acceptance criteria\n1. The cleanup passes by construction. (owner: tests/a.py; env: CI)\n"
                     "2. Each case is handled the same way per case. (owner: tests/a.py; env: CI)\n")
    f, _ = lint_text(unfalsifiable)
    j = "\n".join(f)
    check("passes with no input is flagged", "criterion 1 [no-discriminating-input]" in j)
    check("per case with no cases is flagged", "criterion 2 [per-case-undefined]" in j)
    controls = ("## Acceptance criteria\n1. The cleanup passes for `src/a` and refuses `src/b`. "
                "(owner: tests/a.py; env: CI)\n"
                "2. For each case (`empty`, `absent`, `full`) the store answers `none`. (owner: tests/a.py; env: CI)\n")
    f, _ = lint_text(controls)
    check("an input value or an enumerated list is silent: %s" % f, f == [])
    contract = "## Contract\n```\n/// Refuses with `claim lost` when the lease moved.\n```\n\n"
    uncovered = contract + "## Acceptance criteria\n1. A moved lease is refused. (owner: tests/a.py; env: CI)\n"
    covered = contract + "## Acceptance criteria\n1. A moved lease is refused with `claim lost`. (owner: tests/a.py; env: CI)\n"
    f, _ = lint_text(uncovered)
    check("a refusal text no criterion names is flagged", len(f) == 1 and "[refusal-text-uncovered]" in f[0])
    check("it is a warning, not a failure", bool(f) and is_warning(f[0]))
    f, _ = lint_text(covered)
    check("a refusal text a criterion names is silent: %s" % f, f == [])
    mention = "## Contract\n```\n/// Runs `zfs clone` on the pool.\n```\n\n## Acceptance criteria\n1. A clone exists. (owner: t; env: CI)\n"
    f, _ = lint_text(mention)
    check("a command that is no refusal is silent", f == [])
    with tempfile.TemporaryDirectory() as tmp:
        p = os.path.join(tmp, "W.md")
        with open(p, "w") as fh:
            fh.write(bad)
        check("file mode exits 1 on findings", main([p]) == 1)
    print("selftest: %d failure(s)" % len(failures))
    return 1 if failures else 0


def main(argv: list[str]) -> int:
    if argv[:1] == ["--selftest"]:
        return selftest()
    if len(argv) != 1:
        print((__doc__ or "").split("\n")[0] + "\nusage: lint_criteria.py DOSSIER.md | --selftest", file=sys.stderr)
        return 2
    try:
        with open(argv[0], encoding="utf-8") as fh:
            text = fh.read()
    except OSError as exc:
        print("lint_criteria: %s" % exc, file=sys.stderr)
        return 2
    findings, count = lint_text(text)
    if not count:
        print("lint_criteria: no `## Acceptance criteria` section with numbered items", file=sys.stderr)
        return 2
    errors = [f for f in findings if not is_warning(f)]
    for f in findings:
        print(("CRITERIA-LINT-WARN " if is_warning(f) else "CRITERIA-LINT ") + f)
    print("CRITERIA-LINT: %d finding(s), %d warning(s) over %d criteria"
          % (len(errors), len(findings) - len(errors), count))
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
