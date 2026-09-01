#!/usr/bin/env python3
"""Mechanical checks for the contract-first pipeline.

Two file kinds are checked: the dossier (.discovery/dossiers/*.md, local) and
the ADR (docs/adr/*.md, committed). See references/formats.md for both formats
and for the ASD-STE100 subset enforced here.

The check that matters most is work-package path disjointness. Three kinds of
agent build concurrently against one contract, so two packages that own the same
file corrupt a merge and produce test failures with no diagnosable cause.

Usage:
    validate_pipeline.py --dossier W-014
    validate_pipeline.py --all
    validate_pipeline.py --write-index
    validate_pipeline.py --selftest

Exit status is 1 when any DEFECT is reported, 0 otherwise. A WARNING never
fails the run.

No third-party imports: this runs wherever python3 does.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

# --------------------------------------------------------------------------
# format constants (references/formats.md)
# --------------------------------------------------------------------------

DOSSIER_SECTIONS = [
    "## Problem",
    "## Approach",
    "## Contract",
    "## Work packages",
    "## Acceptance criteria",
    "## Build log",
]
ADR_SECTIONS = ["## Context", "## Decision", "## Consequences", "## Alternatives"]

DOSSIER_STATUSES = {
    "planned",
    "ready",
    "building",
    "review",
    "pr",
    "done",
    "dropped",
}
ADR_STATUSES = {"accepted", "superseded", "rejected"}

DOSSIER_KEYS = [
    "id",
    "title",
    "status",
    "created",
    "updated",
    "anchors",
    "baseline_commit",
    "jira",
    "branch",
    "worktree",
    "pr",
    "blocked_by",
    "adrs",
]
ADR_KEYS = [
    "id",
    "title",
    "status",
    "date",
    "jira",
    "anchors",
    "supersedes",
    "superseded_by",
    "relates_to",
    "tags",
]

EVIDENCE_LABELS = ("FACT", "INFERENCE", "ASSUMPTION", "UNKNOWN")

# R1 — one term per concept. banned -> preferred
BANNED_TERMS = {
    "utilise": "use",
    "utilize": "use",
    "leverage": "use",
    "employ": "use",
    "begin": "start",
    "commence": "start",
    "initiate": "start",
    "cease": "stop",
    "halt": "stop",
    "terminate": "stop",
    "create": "make",
    "generate": "make",
    "produce": "make",
    "construct": "make",
    "modify": "change",
    "alter": "change",
    "adjust": "change",
    "tweak": "change",
    "revise": "change",
    "delete": "remove",
    "eliminate": "remove",
    "purge": "remove",
    "verify": "check",
    "validate": "check",
    "confirm": "check",
    "ensure": "check",
    "locate": "find",
    "discover": "find",
    "identify": "find",
    "resolve": "fix",
    "remediate": "fix",
    "issue": "problem",
    "defect": "problem",
    "flaw": "problem",
    "regarding": "about",
    "concerning": "about",
}

# R5 — no perfect tenses
PERFECT_RE = re.compile(
    r"\b(has|have|had)\s+(been\s+)?[a-z]+(ed|en|un|ne|de|wn|me|ad|lt)\b", re.I
)
# R8 — no unanchored pronoun as a bare subject
PRONOUN_RE = re.compile(
    r"^(This|That|These|Those|It|Which)\s+(is|are|was|were|"
    r"means|makes|gives|has|have|will|can|does|do|should)\b"
)
# R4 — passive voice heuristic
PASSIVE_RE = re.compile(
    r"\b(is|are|was|were|be|been|being)\s+(\w+ed|done|made|"
    r"given|taken|written|built|known|held|kept|sent|read)\b",
    re.I,
)
# R7 — noun cluster heuristic: 4+ consecutive lowercase words with no function word
FUNCTION_WORDS = {
    "the",
    "a",
    "an",
    "of",
    "in",
    "on",
    "to",
    "for",
    "with",
    "by",
    "from",
    "at",
    "and",
    "or",
    "but",
    "if",
    "as",
    "that",
    "than",
    "then",
    "so",
    "is",
    "are",
    "was",
    "were",
    "be",
    "not",
    "no",
    "it",
    "its",
    "this",
    "these",
    "each",
    "every",
    "any",
    "all",
    "one",
    "two",
    "three",
    "when",
    "where",
    "how",
    "why",
    "what",
    "who",
    "into",
    "over",
    "under",
    "after",
    "before",
    "during",
    "per",
    "do",
    "does",
    "did",
    "can",
    "may",
    "must",
    "will",
    "would",
    "should",
    "has",
    "have",
    "had",
    "run",
    "runs",
    "ran",
    "through",
    "across",
    "within",
    "without",
    "between",
    "against",
    "about",
    "above",
    "below",
    "beyond",
    "upon",
    "since",
    "until",
    "while",
    "because",
    "therefore",
    "such",
    "same",
    "other",
    "both",
    "only",
    "also",
    "still",
    "new",
    "old",
    "own",
    "more",
    "most",
    "less",
    "least",
    "many",
    "much",
}

# R7 — a word in a verb form breaks a noun cluster. The suffix test is crude,
# so R7 stays a WARNING and never fails a run.
VERBISH_RE = re.compile(r"(ed|ing|s)$")

VAGUE_CRITERION_WORDS = {
    "efficient",
    "efficiently",
    "fast",
    "quick",
    "quickly",
    "correct",
    "correctly",
    "properly",
    "robust",
    "clean",
    "good",
    "better",
    "reasonable",
    "appropriate",
    "acceptable",
    "sane",
    "sensible",
    "performant",
    "scalable",
    "maintainable",
    "readable",
    "nice",
}

# heuristic for a body inside the ## Contract section
BODY_RE = re.compile(r"^\s+(return|yield)\s+\S")
ASSIGN_RE = re.compile(r"^\s{2,}[A-Za-z_][\w.\[\]]*\s*(?<![=!<>+\-*/])=(?!=)\s*\S")
STUB_MARKERS = (
    "notimplemented",
    "todo!",
    "unimplemented!",
    "pass",
    "...",
    "abstract",
    "throw new notimplementedexception",
    "raise notimplementederror",
)


# --------------------------------------------------------------------------
# reporting
# --------------------------------------------------------------------------


@dataclass
class Report:
    defects: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def defect(self, where: str, msg: str) -> None:
        self.defects.append(f"DEFECT  {where}: {msg}")

    def warn(self, where: str, msg: str) -> None:
        self.warnings.append(f"WARNING {where}: {msg}")

    def merge(self, other: "Report") -> None:
        self.defects.extend(other.defects)
        self.warnings.extend(other.warnings)

    def emit(self) -> int:
        for line in self.defects:
            print(line)
        for line in self.warnings:
            print(line)
        n_d, n_w = len(self.defects), len(self.warnings)
        print(f"\n{'FAIL' if n_d else 'PASS'} — {n_d} defect(s), {n_w} warning(s)")
        return 1 if n_d else 0


# --------------------------------------------------------------------------
# front matter — a deliberately small parser for the flat subset used here
# --------------------------------------------------------------------------


def split_front_matter(text: str) -> tuple[dict[str, object], str, int]:
    """Return (front matter, body, body start line). Raises ValueError."""
    if not text.startswith("---\n"):
        raise ValueError("file does not start with a '---' front matter fence")
    end = text.find("\n---\n", 4)
    if end == -1:
        raise ValueError("front matter is not closed by a '---' line")
    raw = text[4:end]
    body = text[end + 5 :]
    body_start = raw.count("\n") + 3
    return parse_front_matter(raw), body, body_start


def parse_front_matter(raw: str) -> dict[str, object]:
    data: dict[str, object] = {}
    key: str | None = None
    for line in raw.split("\n"):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if line.startswith(("  - ", "- ")):
            if key is None:
                raise ValueError(f"list item with no key: {line!r}")
            data.setdefault(key, [])
            if not isinstance(data[key], list):
                data[key] = []
            data[key].append(line.split("- ", 1)[1].strip())  # type: ignore[union-attr]
            continue
        if ":" not in line:
            raise ValueError(f"cannot parse front matter line: {line!r}")
        key, _, value = line.partition(":")
        key, value = key.strip(), value.strip()
        if value == "":
            data[key] = []
        elif value.startswith("[") and value.endswith("]"):
            inner = value[1:-1].strip()
            data[key] = [p.strip() for p in inner.split(",") if p.strip()]
        elif value in ("null", "~", "None"):
            data[key] = None
        else:
            data[key] = value.strip("'\"")
    return data


def _fenced_lines(body: str) -> list[tuple[str, bool]]:
    """Each line paired with True when it sits inside a fenced code block.

    Fence delimiter lines count as fenced. A '## ' inside a fence is quoted
    text — a contract example, a pasted excerpt — never a document section.
    """
    out: list[tuple[str, bool]] = []
    in_fence = False
    for line in body.split("\n"):
        if line.strip().startswith("```"):
            in_fence = not in_fence
            out.append((line, True))
            continue
        out.append((line, in_fence))
    return out


def sections(body: str) -> dict[str, str]:
    """Map '## Heading' -> its text. Fenced '## ' lines are content."""
    out: dict[str, str] = {}
    current: str | None = None
    buf: list[str] = []
    for line, fenced in _fenced_lines(body):
        if not fenced and line.startswith("## "):
            if current is not None:
                out[current] = "\n".join(buf)
            current = line.strip()
            buf = []
        elif current is not None:
            buf.append(line)
    if current is not None:
        out[current] = "\n".join(buf)
    return out


def heading_order(body: str) -> list[str]:
    return [
        ln.strip()
        for ln, fenced in _fenced_lines(body)
        if not fenced and ln.startswith("## ")
    ]


# --------------------------------------------------------------------------
# prose extraction — code fences, tables and the contract are exempt
# --------------------------------------------------------------------------


def prose_lines(text: str) -> list[tuple[int, str]]:
    """Lines that STE applies to, as (offset within text, line)."""
    out: list[tuple[int, str]] = []
    in_fence = False
    for i, line in enumerate(text.split("\n")):
        stripped = line.strip()
        if stripped.startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence or not stripped:
            continue
        if stripped.startswith(("|", "<!--", "#", ">")):
            continue
        if re.fullmatch(r"[-|:\s]+", stripped):
            continue
        out.append((i, line))
    return out


def split_sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z`\"'(])", text.strip())
    return [p.strip() for p in parts if p.strip()]


def strip_technical(sentence: str) -> str:
    """Remove Technical Names — R10 exempts them from every other rule."""
    s = re.sub(r"`[^`]*`", " TN ", sentence)
    s = re.sub(r"\b[\w./-]+\.(py|cs|rs|ts|js|java|go|md|yaml|yml)\b", " TN ", s)
    s = re.sub(r"\b[\w]+(?:_[\w]+)+\b", " TN ", s)  # snake_case
    s = re.sub(r"\b[a-z]+[A-Z][\w]*\b", " TN ", s)  # camelCase
    s = re.sub(r"\b[A-Z][a-z]+[A-Z][\w]*\b", " TN ", s)  # PascalCase
    s = re.sub(r"\b[A-Z]{2,}\b", " TN ", s)  # HTTP, ADR
    return s


def check_ste(where: str, text: str, rep: Report) -> None:
    lines = prose_lines(text)
    if not lines:
        return
    blob = "\n".join(ln for _, ln in lines)

    for para in re.split(r"\n\s*\n", blob):
        if not para.strip():
            continue
        sents = split_sentences(para.replace("\n", " "))
        if len(sents) > 6:
            rep.warn(where, f"R9 paragraph has {len(sents)} sentences (max 6)")

    for _, line in lines:
        for sentence in split_sentences(line):
            bare = strip_technical(sentence)
            words = re.findall(r"[A-Za-z']+", bare)
            n = len([w for w in words if w != "TN"])

            if n > 25:
                rep.warn(
                    where, f"R2 sentence has {n} words (max 25): {sentence[:70]}..."
                )
            elif n > 20:
                rep.warn(
                    where,
                    f"R2 sentence has {n} words (20 is the "
                    f"instruction limit): {sentence[:70]}...",
                )

            low = [w.lower() for w in words]
            for w in low:
                if w in BANNED_TERMS:
                    rep.defect(
                        where,
                        f"R1 '{w}' is banned; use "
                        f"'{BANNED_TERMS[w]}': {sentence[:60]}...",
                    )
            if PERFECT_RE.search(bare):
                rep.warn(where, f"R5 perfect tense: {sentence[:70]}...")
            if PRONOUN_RE.match(sentence.strip()):
                rep.defect(
                    where, f"R8 unanchored pronoun as subject: {sentence[:70]}..."
                )
            if PASSIVE_RE.search(bare):
                rep.warn(where, f"R4 possible passive voice: {sentence[:70]}...")
            if " and " in bare.lower() and n > 18:
                rep.warn(
                    where,
                    f"R3 long sentence joined by 'and'; consider a "
                    f"split: {sentence[:60]}...",
                )

            run = 0
            for w in low:
                if w == "tn" or w in FUNCTION_WORDS or VERBISH_RE.search(w):
                    run = 0
                    continue
                run += 1
                if run >= 4:
                    rep.warn(where, f"R7 noun cluster of 4 or more: {sentence[:70]}...")
                    break


# --------------------------------------------------------------------------
# shared checks
# --------------------------------------------------------------------------


def check_keys(where: str, fm: dict, required: list[str], rep: Report) -> None:
    for k in required:
        if k not in fm:
            rep.defect(where, f"front matter is missing '{k}'")


def check_headings(where: str, body: str, expected: list[str], rep: Report) -> None:
    found = heading_order(body)
    if found != expected:
        missing = [h for h in expected if h not in found]
        extra = [h for h in found if h not in expected]
        if missing:
            rep.defect(where, f"missing section(s): {', '.join(missing)}")
        if extra:
            rep.defect(where, f"unexpected section(s): {', '.join(extra)}")
        if not missing and not extra:
            rep.defect(
                where, f"sections are out of order; expected {' -> '.join(expected)}"
            )


def check_anchors(where: str, fm: dict, repo_root: Path, rep: Report) -> None:
    anchors = fm.get("anchors") or []
    if not isinstance(anchors, list):
        rep.defect(where, "'anchors' must be a list")
        return
    for a in anchors:
        if ":" not in str(a):
            rep.defect(where, f"anchor '{a}' is not in path:line form")
            continue
        path_part, _, line_part = str(a).rpartition(":")
        target = repo_root / path_part
        if not target.exists():
            rep.defect(where, f"anchor '{a}' points at a file that does not exist")
            continue
        if not line_part.isdigit():
            rep.defect(where, f"anchor '{a}' has a non-numeric line")
            continue
        n = len(target.read_text(errors="replace").split("\n"))
        if int(line_part) > n:
            rep.defect(where, f"anchor '{a}' is past the end of the file ({n} lines)")


def check_evidence(where: str, text: str, rep: Report) -> None:
    if not any(lbl in text for lbl in EVIDENCE_LABELS):
        rep.defect(
            where,
            "no evidence label found; every claim needs one of "
            + ", ".join(EVIDENCE_LABELS),
        )


# --------------------------------------------------------------------------
# dossier
# --------------------------------------------------------------------------


def parse_packages(text: str) -> list[tuple[str, list[str]]]:
    rows: list[tuple[str, list[str]]] = []
    for line in text.split("\n"):
        line = line.strip()
        if not line.startswith("|") or re.fullmatch(r"[|\-:\s]+", line):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) < 2 or cells[0].lower() in ("package", ""):
            continue
        paths = [
            p.strip() for p in cells[1].split(",") if p.strip() and p.strip() != "—"
        ]
        rows.append((cells[0], paths))
    return rows


def check_disjoint(where: str, rows: list[tuple[str, list[str]]], rep: Report) -> None:
    if not rows:
        rep.defect(where, "## Work packages has no rows")
        return
    owner: dict[str, str] = {}
    for name, paths in rows:
        if not paths:
            rep.defect(where, f"package '{name}' owns no paths")
        for p in paths:
            norm = os.path.normpath(p)
            if norm in owner:
                rep.defect(
                    where,
                    f"path '{p}' is owned by both "
                    f"'{owner[norm]}' and '{name}' — owned paths "
                    f"must be disjoint",
                )
            else:
                owner[norm] = name
    # a directory owned by one row and a file under it owned by another
    for a_norm, a_name in owner.items():
        for b_norm, b_name in owner.items():
            if a_norm == b_norm or a_name == b_name:
                continue
            if b_norm.startswith(a_norm.rstrip("/") + os.sep):
                rep.defect(
                    where,
                    f"'{b_norm}' ({b_name}) is inside "
                    f"'{a_norm}' ({a_name}) — owned paths must "
                    f"be disjoint",
                )


def check_contract(where: str, text: str, rep: Report) -> None:
    blocks = re.findall(r"```[\w+-]*\n(.*?)```", text, re.S)
    if not blocks:
        rep.defect(where, "## Contract has no fenced code block")
        return
    body_text = "\n".join(blocks)
    if not re.search(r"(///|//!|\*|\"\"\"|'''|<summary>|/\*\*|#)", body_text):
        rep.defect(
            where,
            "## Contract has no documentation comment; every "
            "member states its promise in the language's own "
            "documentation form",
        )
    in_doc = False
    for i, line in enumerate(body_text.split("\n"), 1):
        low = line.strip().lower()
        if low.count('"""') == 1 or low.count("'''") == 1:
            in_doc = not in_doc
            continue
        if in_doc or any(m in low for m in STUB_MARKERS):
            continue
        if BODY_RE.match(line) or ASSIGN_RE.match(line):
            rep.defect(
                where,
                f"## Contract line {i} looks like a body, not a "
                f"signature: {line.strip()[:60]} — bodies belong "
                f"to the implementer",
            )
    for block in re.findall(r"```rust\n(.*?)```", text, re.S):
        for line in block.split("\n"):
            m = re.match(r"\s*fn\s+(\w+)", line)
            if m and "pub" not in line:
                rep.warn(
                    where,
                    f"rust fn '{m.group(1)}' states no visibility — a test "
                    f"only reaches a 'pub' seam",
                )


def check_criteria(where: str, text: str, rep: Report) -> None:
    items = [ln.strip() for ln in text.split("\n") if re.match(r"^\s*\d+[.)]\s+\S", ln)]
    if not items:
        rep.defect(where, "## Acceptance criteria has no numbered items")
        return
    for item in items:
        low = item.lower()
        vague = sorted(w for w in VAGUE_CRITERION_WORDS if re.search(rf"\b{w}\b", low))
        concrete = bool(re.search(r"\d", item) or "`" in item)
        if vague and not concrete:
            rep.defect(
                where,
                f"criterion is not falsifiable ({', '.join(vague)}"
                f" with no observable value): {item[:70]}",
            )
        elif not concrete:
            rep.warn(
                where, f"criterion names no concrete value or identifier: {item[:70]}"
            )
        if "unverifiable-locally" in low and not re.search(
            r"\b(via|substitute|defer)", low
        ):
            rep.defect(
                where,
                f"criterion is UNVERIFIABLE-LOCALLY with no "
                f"substitute named: {item[:70]}",
            )


def check_build_log(where: str, text: str, status: str, rep: Report) -> None:
    row3 = re.compile(r"^ARBITRATION\s+\d+\s*[—–-]+\s*row\s*3\b", re.M)
    for chunk in row3.split(text)[1:]:
        scope = re.split(r"^ARBITRATION\s", chunk, flags=re.M)[0]
        if "Lesson:" not in scope:
            rep.defect(
                where,
                "a row-3 arbitration records no 'Lesson:' line; the "
                "contract fix must name the lesson",
            )
    if "PROMISE_CHECKLIST" not in text:
        if status in {"review", "pr", "done"}:
            rep.defect(
                where,
                f"status is '{status}' but the Build log names no PROMISE_CHECKLIST",
            )
        elif status == "building":
            rep.warn(
                where,
                "status is 'building' but the Build log names no PROMISE_CHECKLIST",
            )


def validate_dossier(path: Path, repo_root: Path) -> Report:
    rep = Report()
    where = path.name
    try:
        fm, body, _ = split_front_matter(path.read_text())
    except ValueError as exc:
        rep.defect(where, str(exc))
        return rep

    check_keys(where, fm, DOSSIER_KEYS, rep)
    status = fm.get("status")
    if status not in DOSSIER_STATUSES:
        rep.defect(where, f"status '{status}' is not one of {sorted(DOSSIER_STATUSES)}")
    if not re.fullmatch(r"W-\d{3,}", str(fm.get("id", ""))):
        rep.defect(where, f"id '{fm.get('id')}' must look like W-014")
    if len(str(fm.get("title", "")).split()) < 4:
        rep.warn(where, "title is very short; state the subject and the change")

    check_headings(where, body, DOSSIER_SECTIONS, rep)
    check_anchors(where, fm, repo_root, rep)

    sec = sections(body)
    if "## Problem" in sec:
        check_evidence(f"{where} ## Problem", sec["## Problem"], rep)
    if "## Approach" in sec:
        if "ASSUMPTION" in sec["## Approach"]:
            rep.defect(
                f"{where} ## Approach",
                "a load-bearing ASSUMPTION blocks 'ready'; check it and "
                "promote it to FACT, or move it to a stated risk",
            )
    if "## Contract" in sec:
        check_contract(f"{where} ## Contract", sec["## Contract"], rep)
    if "## Work packages" in sec:
        check_disjoint(
            f"{where} ## Work packages", parse_packages(sec["## Work packages"]), rep
        )
    if "## Acceptance criteria" in sec:
        check_criteria(
            f"{where} ## Acceptance criteria", sec["## Acceptance criteria"], rep
        )
    if "## Build log" in sec:
        check_build_log(f"{where} ## Build log", sec["## Build log"], str(status), rep)

    if status == "ready" and not fm.get("baseline_commit"):
        rep.defect(
            where,
            "status is 'ready' but baseline_commit is empty; /plan stamps it last",
        )

    for name in ("## Problem", "## Approach", "## Consequences"):
        if name in sec:
            check_ste(f"{where} {name}", sec[name], rep)
    return rep


# --------------------------------------------------------------------------
# ADR
# --------------------------------------------------------------------------

DECISION_VERBS = (
    "use",
    "reject",
    "keep",
    "remove",
    "move",
    "split",
    "merge",
    "replace",
    "add",
    "drop",
    "coalesce",
    "defer",
    "adopt",
    "stop",
    "start",
    "make",
    "change",
    "fix",
    "hold",
    "cache",
    "batch",
    "isolate",
    "expose",
    "hide",
)


def validate_adr(path: Path, repo_root: Path) -> Report:
    rep = Report()
    where = path.name
    try:
        fm, body, _ = split_front_matter(path.read_text())
    except ValueError as exc:
        rep.defect(where, str(exc))
        return rep

    check_keys(where, fm, ADR_KEYS, rep)
    if fm.get("status") not in ADR_STATUSES:
        rep.defect(
            where, f"status '{fm.get('status')}' is not one of {sorted(ADR_STATUSES)}"
        )
    if not re.fullmatch(r"ADR-\d{4}", str(fm.get("id", ""))):
        rep.defect(where, f"id '{fm.get('id')}' must look like ADR-0007")

    title = str(fm.get("title", ""))
    if (
        not any(re.search(rf"\b{v}\w*\b", title, re.I) for v in DECISION_VERBS)
        and ";" not in title
    ):
        rep.warn(
            where,
            f"title may state only the subject: '{title}' — a title "
            f"must state the subject AND the decision, because the "
            f"index is the only file a future agent scans",
        )

    if fm.get("status") == "superseded" and not fm.get("superseded_by"):
        rep.defect(where, "status is 'superseded' but superseded_by is empty")
    if fm.get("superseded_by") and fm.get("status") != "superseded":
        rep.defect(where, "superseded_by is set but status is not 'superseded'")

    check_headings(where, body, ADR_SECTIONS, rep)
    check_anchors(where, fm, repo_root, rep)

    sec = sections(body)
    if "## Context" in sec:
        check_evidence(f"{where} ## Context", sec["## Context"], rep)
    if "## Decision" in sec:
        sents = split_sentences(
            " ".join(ln for _, ln in prose_lines(sec["## Decision"]))
        )
        if not sents:
            rep.defect(f"{where} ## Decision", "section is empty")
        else:
            n = len(re.findall(r"[A-Za-z']+", strip_technical(sents[0])))
            if n > 25:
                rep.defect(
                    f"{where} ## Decision",
                    f"the first sentence is the decision and has {n} "
                    f"words; state one decision in one sentence",
                )
    if "## Alternatives" in sec and not re.search(
        r"^\s*[-*]\s+\S", sec["## Alternatives"], re.M
    ):
        rep.warn(
            f"{where} ## Alternatives",
            "no rejected option recorded; an ADR with no alternative "
            "usually means no choice was made",
        )
    if "## Consequences" in sec and len(sec["## Consequences"].strip()) < 40:
        rep.warn(
            f"{where} ## Consequences",
            "very short; state the constraint a future change must "
            "respect, not a summary of the work",
        )

    for name in ADR_SECTIONS:
        if name in sec:
            check_ste(f"{where} {name}", sec[name], rep)
    return rep


# --------------------------------------------------------------------------
# index
# --------------------------------------------------------------------------


def adr_rows(adr_dir: Path) -> list[tuple[str, str, str]]:
    rows = []
    for p in sorted(adr_dir.glob("*.md")):
        if p.name == "index.md":
            continue
        try:
            fm, _, _ = split_front_matter(p.read_text())
        except ValueError:
            continue
        rows.append(
            (
                str(fm.get("id", "?")),
                str(fm.get("status", "?")),
                str(fm.get("title", "?")),
            )
        )
    return sorted(rows)


def render_index(rows: list[tuple[str, str, str]]) -> str:
    out = [
        "# ADR index",
        "",
        "<!-- generated by scripts/validate_pipeline.py --write-index; "
        "never hand-edit a row -->",
        "",
        "| ID | Status | Title |",
        "|---|---|---|",
    ]
    out += [f"| {i} | {s} | {t} |" for i, s, t in rows]
    return "\n".join(out) + "\n"


def check_index(adr_dir: Path, rep: Report) -> None:
    index = adr_dir / "index.md"
    rows = adr_rows(adr_dir)
    if not index.exists():
        if rows:
            rep.defect(
                "adr/index.md", "the index is missing; regenerate it with --write-index"
            )
        return
    if index.read_text() != render_index(rows):
        rep.defect(
            "adr/index.md",
            "the index does not match the ADR front "
            "matter; regenerate it with --write-index",
        )


# --------------------------------------------------------------------------
# selftest
# --------------------------------------------------------------------------

GOOD_DOSSIER = """---
id: W-014
title: Coalesce concurrent flush calls in FlushQueue
status: ready
created: 2026-08-10
updated: 2026-08-10
anchors:
  - src/flush.py:3
baseline_commit: 3f2611f
jira: PROJ-142
branch: fix/PROJ-142-flush-coalescing
worktree: ../repo-W-014
pr: null
blocked_by: []
adrs: []
---

## Problem

FACT: two threads call `flush` at the same time (`src/flush.py:3`).
INFERENCE: the store writes one item two times, from the fact above.

## Approach

The queue drains behind one lock, so parallel callers share one drain.

## Contract

```python
class FlushQueue:
    def flush(self, batch_size: int) -> int:
        \"\"\"Drain queued items to the store, oldest first.

        Drains at most `batch_size` items per call. Parallel calls share one
        drain, so each queued item reaches the store one time. Returns the
        count of items written. Returns 0 for an empty queue. Raises
        ValueError when `batch_size` is less than 1.
        \"\"\"
        raise NotImplementedError
```

## Work packages

| Package | Owned paths | Depends on |
|---|---|---|
| P1 drain lock | src/flush.py | — |
| UT unit tests | tests/unit/test_flush.py | — |
| IT flow tests | tests/integration/test_flush_flow.py | — |

## Acceptance criteria

1. With 3 parallel `flush(batch_size=10)` calls and 5 queued items, the store
   receives each item one time.
2. `flush(batch_size=0)` raises ValueError.

## Build log
"""

GOOD_ADR = """---
id: ADR-0007
title: Coalesce concurrent flush calls behind one drain
status: accepted
date: 2026-08-10
jira: PROJ-142
anchors:
  - src/flush.py:3
supersedes: []
superseded_by: null
relates_to: []
tags: [concurrency]
---

## Context

FACT: two threads reach `flush` at the same time (`src/flush.py:3`).

## Decision

One lock guards the drain, so parallel callers share one pass over the queue.

## Consequences

A new caller goes through `flush`. A direct write to the store skips the lock,
so the store keeps no ordering promise for such a write.

## Alternatives

- **A queue per thread** — the store then loses the global order of items.
"""


def selftest() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "src").mkdir()
        (root / "src" / "flush.py").write_text("a\nb\nc\nd\n")
        d = root / ".discovery" / "dossiers"
        a = root / "docs" / "adr"
        d.mkdir(parents=True)
        a.mkdir(parents=True)
        (d / "W-014-flush.md").write_text(GOOD_DOSSIER)
        (a / "0007-flush.md").write_text(GOOD_ADR)
        (a / "index.md").write_text(render_index(adr_rows(a)))

        rep = Report()
        rep.merge(validate_dossier(d / "W-014-flush.md", root))
        rep.merge(validate_adr(a / "0007-flush.md", root))
        check_index(a, rep)
        print("--- selftest: the reference dossier and ADR ---")
        rc = rep.emit()

        print("\n--- selftest: overlapping owned paths must be a DEFECT ---")
        bad = GOOD_DOSSIER.replace(
            "| UT unit tests | tests/unit/test_flush.py",
            "| UT unit tests | src/flush.py",
        )
        (d / "W-015-bad.md").write_text(bad.replace("W-014", "W-015"))
        bad_rep = validate_dossier(d / "W-015-bad.md", root)
        caught = any("disjoint" in x for x in bad_rep.defects)
        print("\n".join(bad_rep.defects) or "(no defects)")
        print(
            f"\n{'PASS' if caught else 'FAIL'} — overlap "
            f"{'detected' if caught else 'NOT detected'}"
        )

        print("\n--- selftest: a fenced '## ' in the build log is not a section ---")
        fenced = GOOD_DOSSIER.replace(
            "## Build log\n",
            "## Build log\n\n"
            "- r1 style: CR-style-1 accepted — quoted excerpt below\n\n"
            "```markdown\n"
            "## CR-1: rename the drain lock\n"
            "```\n",
        )
        (d / "W-016-fenced.md").write_text(fenced.replace("W-014", "W-016"))
        fenced_rep = validate_dossier(d / "W-016-fenced.md", root)
        clean = not any("section" in x for x in fenced_rep.defects)
        print("\n".join(fenced_rep.defects) or "(no defects)")
        print(
            f"\n{'PASS' if clean else 'FAIL'} — fenced heading "
            f"{'ignored' if clean else 'treated as a section'}"
        )

        print("\n--- selftest: review status without PROMISE_CHECKLIST is a DEFECT ---")
        nocheck = GOOD_DOSSIER.replace("status: ready", "status: review")
        (d / "W-017-nocheck.md").write_text(nocheck.replace("W-014", "W-017"))
        nocheck_rep = validate_dossier(d / "W-017-nocheck.md", root)
        caught_pcl = any("PROMISE_CHECKLIST" in x for x in nocheck_rep.defects)
        print("\n".join(nocheck_rep.defects) or "(no defects)")
        print(
            f"\n{'PASS' if caught_pcl else 'FAIL'} — missing PROMISE_CHECKLIST "
            f"{'detected' if caught_pcl else 'NOT detected'}"
        )

        print("\n--- selftest: a row-3 arbitration without a Lesson is a DEFECT ---")
        row3 = GOOD_DOSSIER.replace(
            "## Build log\n",
            "## Build log\n\n"
            "ARBITRATION 1 — row 3 (contract ambiguous).\n"
            "Re-spawned both agents.\n",
        )
        (d / "W-018-row3.md").write_text(row3.replace("W-014", "W-018"))
        row3_rep = validate_dossier(d / "W-018-row3.md", root)
        caught_lesson = any("Lesson" in x for x in row3_rep.defects)
        print("\n".join(row3_rep.defects) or "(no defects)")
        print(
            f"\n{'PASS' if caught_lesson else 'FAIL'} — lesson-less row 3 "
            f"{'detected' if caught_lesson else 'NOT detected'}"
        )
        return rc if caught and clean and caught_pcl and caught_lesson else 1


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--root", default=".", help="repository root (default: .)")
    ap.add_argument("--dossier", help="one dossier ID, e.g. W-014")
    ap.add_argument("--adr", help="one ADR ID, e.g. ADR-0007")
    ap.add_argument("--all", action="store_true", help="every dossier and ADR")
    ap.add_argument(
        "--write-index",
        action="store_true",
        help="regenerate docs/adr/index.md and exit",
    )
    ap.add_argument(
        "--selftest",
        action="store_true",
        help="check this script against a reference pair",
    )
    args = ap.parse_args()

    if args.selftest:
        return selftest()

    root = Path(args.root).resolve()
    dossier_dir = root / ".discovery" / "dossiers"
    adr_dir = root / "docs" / "adr"

    if args.write_index:
        adr_dir.mkdir(parents=True, exist_ok=True)
        (adr_dir / "index.md").write_text(render_index(adr_rows(adr_dir)))
        print(f"wrote {adr_dir / 'index.md'}")
        return 0

    rep = Report()
    targets_d: list[Path] = []
    targets_a: list[Path] = []

    if args.dossier:
        targets_d = sorted(dossier_dir.glob(f"{args.dossier}-*.md"))
        if not targets_d:
            print(f"DEFECT  no dossier matches '{args.dossier}' in {dossier_dir}")
            return 1
    if args.adr:
        num = args.adr.replace("ADR-", "")
        targets_a = sorted(adr_dir.glob(f"{num}-*.md"))
        if not targets_a:
            print(f"DEFECT  no ADR matches '{args.adr}' in {adr_dir}")
            return 1
    if args.all or (not args.dossier and not args.adr):
        targets_d = sorted(dossier_dir.glob("*.md")) if dossier_dir.exists() else []
        targets_a = (
            [p for p in sorted(adr_dir.glob("*.md")) if p.name != "index.md"]
            if adr_dir.exists()
            else []
        )
        if not targets_d and not targets_a:
            print(f"nothing to check under {dossier_dir} or {adr_dir}")
            return 0

    for p in targets_d:
        rep.merge(validate_dossier(p, root))
    for p in targets_a:
        rep.merge(validate_adr(p, root))
    if adr_dir.exists() and (args.all or args.adr or not args.dossier):
        check_index(adr_dir, rep)

    print(f"checked {len(targets_d)} dossier(s), {len(targets_a)} ADR(s)\n")
    return rep.emit()


if __name__ == "__main__":
    sys.exit(main())
