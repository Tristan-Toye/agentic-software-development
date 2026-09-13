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
    validate_pipeline.py --finalize-ids --base origin/development
    validate_pipeline.py --selftest

Exit status is 1 when any DEFECT is reported, 0 otherwise. A WARNING never
fails the run.

No third-party imports: this runs wherever python3 does.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import os
import re
import shutil
import subprocess
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


CRITERION_OWNER_RE = re.compile(r"\(owner:\s*[\w./-]+\s*;\s*env:\s*[\w-]+\s*\)")


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
        if not CRITERION_OWNER_RE.search(item):
            rep.defect(
                where,
                f"criterion names no owner test file and no run "
                f"environment; append '(owner: <test path>; "
                f"env: <where it runs>)': {item[:70]}",
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
    if "DEFERRED" not in text and status == "done":
        rep.defect(
            where,
            "status is 'done' but the Build log carries no DEFERRED ledger; "
            "/work-on Phase 9 captures the deferred issues (or DEFERRED: none) "
            "before closing the run",
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
# finalize ids (--finalize-ids)
# --------------------------------------------------------------------------

ADR_FILE_RE = re.compile(r"^(\d{4})-")
LRN_HEADER_LINE_RE = re.compile(r"^### (LRN-\d{4}): .*$", re.MULTILINE)
LRN_ID_RE = re.compile(r"LRN-\d{4}")


def git_out(root: Path, *args: str) -> str:
    p = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {p.stderr.strip()}")
    return p.stdout


def git_show(root: Path, rev: str, path: str) -> str:
    p = subprocess.run(
        ["git", "-C", str(root), "show", f"{rev}:{path}"],
        capture_output=True,
        text=True,
    )
    return p.stdout if p.returncode == 0 else ""


def branch_tip(root: Path) -> str:
    """The commit that holds only this branch's own work.

    After the sync merge, HEAD is the merge commit and HEAD^ is the branch's
    last own commit. On an unmerged branch, HEAD itself is the tip.
    """
    line = git_out(root, "rev-list", "--parents", "-n", "1", "HEAD").split()
    return "HEAD^" if len(line) > 2 else "HEAD"


def own_added_adrs(root: Path, base: str, tip: str) -> set[str]:
    """ADR paths this branch added, as repo-relative names."""
    mb = git_out(root, "merge-base", base, tip).strip()
    out = git_out(
        root, "diff", "--name-only", "--diff-filter=A", mb, tip, "--", "docs/adr"
    )
    return {x for x in out.splitlines() if x}


def collect_adr_ids(adr_dir: Path) -> dict[str, list[Path]]:
    by_id: dict[str, list[Path]] = {}
    for p in sorted(adr_dir.glob("[0-9][0-9][0-9][0-9]-*.md")):
        fm, _, _ = split_front_matter(p.read_text())
        aid = str(fm.get("id", "?"))
        by_id.setdefault(aid, []).append(p)
    return by_id


def plan_adr_renumber(
    by_id: dict[str, list[str]], own: set[str], taken: list[int]
) -> tuple[dict[str, str], list[str]]:
    """Plan fresh ids for the branch's own files inside duplicate-id groups.

    `by_id` maps front-matter id to repo-relative file names, `own` is the
    set of files this branch added, `taken` holds every id number in use
    after the merge. A group with no own claimant means the base itself is
    broken; a group with two own claimants means this branch minted one id
    twice. Both are DEFECTs: this tool renumbers merge collisions only.
    """
    nxt = max(taken) + 1 if taken else 1
    renames: dict[str, str] = {}
    defects: list[str] = []
    for aid, files in sorted(by_id.items()):
        if len(files) < 2:
            continue
        own_files = [f for f in files if f in own]
        if not own_files:
            defects.append(f"{aid}: duplicate id, but no file came from this branch")
            continue
        if len(own_files) > 1:
            defects.append(f"{aid}: this branch minted the id twice")
            continue
        renames[own_files[0]] = f"ADR-{nxt:04d}"
        nxt += 1
    return renames, defects


def rewrite_ids(text: str, id_map: dict[str, str]) -> str:
    """Single-pass id replacement: no id is rewritten twice (no chaining)."""
    if not id_map:
        return text
    pattern = re.compile("|".join(sorted(id_map, key=len, reverse=True)))
    return pattern.sub(lambda m: id_map[m.group(0)], text)


def lrn_numbered_headers(text: str) -> list[tuple[int, str]]:
    return [
        (int(m.group(1)[-4:]), m.group(0)) for m in LRN_HEADER_LINE_RE.finditer(text)
    ]


def plan_lrn_renumber(
    merged: dict[str, str], tips: dict[str, str], bases: dict[str, str]
) -> tuple[dict[str, str], dict[str, set[str]]]:
    """Plan fresh numbers for the branch's own colliding LRN headers.

    A header is this branch's own when its full text ('### LRN-NNNN: title')
    sits in the branch tip ledger but not in the merge-base ledger — sibling
    titles differ, so a same-numbered sibling section is never mistaken for
    own. `merged` is the current working-tree text per ledger, `tips` and
    `bases` come from git. Returns the id map and, per ledger, the set of
    own header lines.
    """
    own_by_ledger: dict[str, set[str]] = {}
    foreign_nums: set[int] = set()
    for rel, text in merged.items():
        merged_headers = {h for _, h in lrn_numbered_headers(text)}
        base_headers = {h for _, h in lrn_numbered_headers(bases.get(rel, ""))}
        own = {
            h
            for _, h in lrn_numbered_headers(tips.get(rel, ""))
            if h not in base_headers and h in merged_headers
        }
        own_by_ledger[rel] = own
        for num, header in lrn_numbered_headers(text):
            if header not in own:
                foreign_nums.add(num)
    own_nums = {
        num
        for own in own_by_ledger.values()
        for num, _ in lrn_numbered_headers("".join(h + "\n" for h in own))
    }
    taken = sorted(
        {
            int(m.group(0)[-4:])
            for text in merged.values()
            for m in LRN_ID_RE.finditer(text)
        }
    )
    nxt = taken[-1] + 1 if taken else 1
    id_map: dict[str, str] = {}
    for num in sorted(own_nums & foreign_nums):
        id_map[f"LRN-{num:04d}"] = f"LRN-{nxt:04d}"
        nxt += 1
    return id_map, own_by_ledger


def apply_lrn_renumber(
    text: str, own_headers: set[str], id_map: dict[str, str], tip_text: str
) -> str:
    """Rewrite own LRN sections and own lines outside them.

    Inside an own section ('### LRN-...' up to the next heading) every id
    occurrence is rewritten, header included. Outside sections, a line is
    rewritten only when it also existed verbatim in the branch tip ledger —
    mirror-table and family-map rows the branch added. Sibling lines never
    existed in the tip, so they stay untouched.
    """
    if not id_map:
        return text
    lines = text.splitlines(keepends=True)
    tip_lines = set(tip_text.splitlines(keepends=True))
    headers = {
        i: ln.rstrip("\n") for i, ln in enumerate(lines) if LRN_HEADER_LINE_RE.match(ln)
    }
    in_own = set()
    for i, header in headers.items():
        if header not in own_headers:
            continue
        end = len(lines)
        for j in range(i + 1, len(lines)):
            if lines[j].startswith("#"):
                end = j
                break
        in_own.update(range(i, end))
    out = []
    for i, ln in enumerate(lines):
        if i in in_own or ln in tip_lines:
            out.append(rewrite_ids(ln, id_map))
        else:
            out.append(ln)
    return "".join(out)


def run_finalize(root: Path, base: str) -> int:
    print(f"finalize: base ref {base}")
    try:
        tip = branch_tip(root)
        own = own_added_adrs(root, base, tip)
    except RuntimeError as exc:
        print(f"DEFECT  {exc}")
        return 1
    mb = git_out(root, "merge-base", base, tip).strip()
    print(f"finalize: branch tip {tip}, merge base {mb[:12]}")

    defects: list[str] = []
    summary: list[str] = []
    id_map: dict[str, str] = {}

    adr_dir = root / "docs" / "adr"
    if adr_dir.exists():
        by_id = collect_adr_ids(adr_dir)
        rel_by_id = {
            aid: [f"docs/adr/{p.name}" for p in files] for aid, files in by_id.items()
        }
        taken = [
            int(aid.replace("ADR-", ""))
            for aid in by_id
            if re.fullmatch(r"ADR-\d{4}", aid)
        ]
        renames, defects = plan_adr_renumber(rel_by_id, own, taken)
        for rel, new_id in renames.items():
            old_id = f"ADR-{rel.split('/')[-1][:4]}"
            id_map[old_id] = new_id
            summary.append(f"{old_id} -> {new_id} ({rel})")
        if id_map:
            for p in sorted(adr_dir.glob("[0-9][0-9][0-9][0-9]-*.md")):
                if f"docs/adr/{p.name}" in own:
                    p.write_text(rewrite_ids(p.read_text(), id_map))
            for rel, new_id in sorted(renames.items()):
                p = root / rel
                p.replace(adr_dir / f"{new_id.replace('ADR-', '')}{p.name[4:]}")

    ledgers = ["docs/learned-rules.md", "docs/learned-rules-evidence.md"]
    merged = {rel: (root / rel).read_text() for rel in ledgers if (root / rel).exists()}
    if merged:
        tips = {rel: git_show(root, tip, rel) for rel in merged}
        bases = {rel: git_show(root, mb, rel) for rel in merged}
        lrn_map, own_by_ledger = plan_lrn_renumber(merged, tips, bases)
        for old, new in sorted(lrn_map.items()):
            summary.append(f"{old} -> {new}")
        for rel, text in merged.items():
            new_text = apply_lrn_renumber(text, own_by_ledger[rel], lrn_map, tips[rel])
            if new_text != text:
                (root / rel).write_text(new_text)

    dossier_dir = root / ".discovery" / "dossiers"
    if id_map and dossier_dir.exists():
        for p in sorted(dossier_dir.glob("*.md")):
            text = p.read_text()
            new_text = rewrite_ids(text, id_map)
            if new_text != text:
                p.write_text(new_text)
                print(f"updated dossier {p.name}")

    if not summary:
        print("no collisions — nothing to renumber")
    for line in summary:
        print(f"renumbered: {line}")

    if id_map:
        full_map = dict(id_map)
        if full_map:
            pattern = "|".join(sorted(full_map, key=len, reverse=True))
            p = subprocess.run(
                ["git", "-C", str(root), "grep", "-l", "-E", pattern],
                capture_output=True,
                text=True,
            )
            if p.returncode not in (0, 1):
                print(f"WARNING  leak scan failed: {p.stderr.strip()}")
            for path in p.stdout.splitlines():
                if path.startswith(("docs/adr/", ".discovery/")) or path in ledgers:
                    continue
                print(f"WARNING  {path} still mentions a renumbered id; update it")

    if defects:
        for d in defects:
            print(f"DEFECT  {d}")
        return 1

    if adr_dir.exists():
        (adr_dir / "index.md").write_text(render_index(adr_rows(adr_dir)))
        print("regenerated docs/adr/index.md")
        print("restore the index before you commit: git restore docs/adr/index.md")
    return 0


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

1. With 3 parallel `flush(batch_size=10)` calls and 5 queued items, the store receives each item one time. (owner: tests/integration/test_flush_flow.py; env: local)
2. `flush(batch_size=0)` raises ValueError. (owner: tests/unit/test_flush.py; env: local)

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


FINALIZE_ADR = """---
id: ADR-{num}
title: {title}
status: accepted
date: 2026-09-11
---

## Context

{title}.

## Decision

Decided.

## Consequences

Accepted.

## Alternatives

- None.
"""


def selftest_finalize() -> bool:
    print("\n--- selftest: the finalize planner (pure cases) ---")
    ok = True

    renames, defects = plan_adr_renumber(
        {
            "ADR-0001": ["docs/adr/0001-base.md"],
            "ADR-0100": ["docs/adr/0100-own.md", "docs/adr/0100-sib.md"],
            "ADR-0200": ["docs/adr/0200-a.md", "docs/adr/0200-b.md"],
            "ADR-0300": ["docs/adr/0300-a.md", "docs/adr/0300-b.md"],
        },
        {"docs/adr/0100-own.md", "docs/adr/0200-a.md", "docs/adr/0200-b.md"},
        [1, 100, 200, 300],
    )
    case = renames == {"docs/adr/0100-own.md": "ADR-0301"} and len(defects) == 2
    ok &= case
    print(
        f"{'PASS' if case else 'FAIL'} — ADR planner: own file renumbered, "
        f"base-broken and self-collision groups are DEFECTs"
    )
    print(f"  renames={renames} defects={defects}")

    no_chain = rewrite_ids(
        "x ADR-0100 y ADR-0101", {"ADR-0100": "ADR-0101", "ADR-0101": "ADR-0102"}
    )
    case = no_chain == "x ADR-0101 y ADR-0102"
    ok &= case
    print(f"{'PASS' if case else 'FAIL'} — rewrite is single-pass: {no_chain}")

    merged = {
        "docs/learned-rules.md": (
            "### LRN-0001: base rule\n\nBase.\n\n"
            "### LRN-0002: sibling rule\n\nSibling.\n\n"
            "### LRN-0002: own rule\n\nOwn cites LRN-0002.\n"
        )
    }
    tips = {
        "docs/learned-rules.md": (
            "### LRN-0001: base rule\n\nBase.\n\n"
            "### LRN-0002: own rule\n\nOwn cites LRN-0002.\n"
        )
    }
    bases = {"docs/learned-rules.md": "### LRN-0001: base rule\n\nBase.\n"}
    lrn_map, own_by_ledger = plan_lrn_renumber(merged, tips, bases)
    case = lrn_map == {"LRN-0002": "LRN-0003"} and own_by_ledger[
        "docs/learned-rules.md"
    ] == {"### LRN-0002: own rule"}
    ok &= case
    print(
        f"{'PASS' if case else 'FAIL'} — LRN planner: own header renumbered, "
        f"sibling kept; map={lrn_map}"
    )

    applied = apply_lrn_renumber(
        merged["docs/learned-rules.md"],
        own_by_ledger["docs/learned-rules.md"],
        lrn_map,
        tips["docs/learned-rules.md"],
    )
    case = (
        "### LRN-0003: own rule" in applied
        and "### LRN-0002: sibling rule" in applied
        and "### LRN-0002: own rule" not in applied
        and "Own cites LRN-0003." in applied
    )
    ok &= case
    print(
        f"{'PASS' if case else 'FAIL'} — LRN apply: own section rewritten, "
        f"sibling section untouched"
    )

    print("\n--- selftest: finalize end-to-end on a fixture repository ---")
    if not shutil.which("git"):
        print("SKIP  git not found; the repository scenario was not run")
        return ok
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "repo"
        root.mkdir()
        try:
            git_out(root, "init", "-q", "-b", "main")
            git_out(root, "config", "user.email", "selftest@example.invalid")
            git_out(root, "config", "user.name", "selftest")

            adr = root / "docs" / "adr"
            rules = root / "docs" / "learned-rules.md"
            evidence = root / "docs" / "learned-rules-evidence.md"
            dossiers = root / ".discovery" / "dossiers"
            adr.mkdir(parents=True)
            dossiers.mkdir(parents=True)
            (root / ".gitattributes").write_text("docs/learned-rules*.md merge=union\n")

            (adr / "0001-base.md").write_text(
                FINALIZE_ADR.format(num="0001", title="Base decision")
            )
            rules.write_text(
                "# Learned rules\n\n## Mirror\n\n"
                "| Rule | Title |\n|---|---|\n| LRN-0001 | base rule |\n\n"
                "## Ratified rules\n\n### LRN-0001: base rule\n\nBase body.\n"
            )
            evidence.write_text(
                "# Evidence\n\n### LRN-0001: base rule\n\nBase evidence.\n"
            )
            dossier = dossiers / "W-0901-finalize.md"
            dossier.write_text("---\nid: W-0901\nadrs: []\n---\n\n## Problem\n\nP.\n")
            git_out(root, "add", "-A")
            git_out(root, "commit", "-q", "-m", "base")

            git_out(root, "checkout", "-q", "-b", "feat")
            (adr / "0002-own.md").write_text(
                FINALIZE_ADR.format(num="0002", title="Own decision")
            )
            with rules.open("a") as f:
                f.write("\n### LRN-0002: own rule\n\nOwn body cites LRN-0002 once.\n")
                f.write("| LRN-0002 | own rule |\n")
            with evidence.open("a") as f:
                f.write("\n### LRN-0002: own rule\n\nOwn evidence LRN-0002.\n")
            dossier.write_text(
                "---\nid: W-0901\nadrs: [ADR-0002]\n---\n\n## Problem\n\nP.\n"
            )
            git_out(root, "add", "-A")
            git_out(root, "commit", "-q", "-m", "own")

            git_out(root, "checkout", "-q", "main")
            (adr / "0002-sib.md").write_text(
                FINALIZE_ADR.format(num="0002", title="Sibling decision")
            )
            with rules.open("a") as f:
                f.write(
                    "\n### LRN-0002: sibling rule\n\nSibling body.\n"
                    "| LRN-0002 | sibling rule |\n"
                )
            with evidence.open("a") as f:
                f.write("\n### LRN-0002: sibling rule\n\nSibling evidence.\n")
            git_out(root, "add", "-A")
            git_out(root, "commit", "-q", "-m", "sibling")

            git_out(root, "checkout", "-q", "feat")
            git_out(root, "merge", "-q", "--no-edit", "main")
        except RuntimeError as exc:
            print(f"FAIL  fixture repository could not be built: {exc}")
            return False

        rc = run_finalize(root, "main")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc2 = run_finalize(root, "main")
        rules_text = rules.read_text()
        evidence_text = evidence.read_text()
        index_text = (adr / "index.md").read_text()
        checks = {
            "exit code 0": rc == 0,
            "own ADR renamed to 0003": (adr / "0003-own.md").exists()
            and not (adr / "0002-own.md").exists()
            and "id: ADR-0003" in (adr / "0003-own.md").read_text(),
            "sibling ADR untouched": "id: ADR-0002"
            in (adr / "0002-sib.md").read_text(),
            "own LRN renumbered, sibling kept": "### LRN-0003: own rule" in rules_text
            and "### LRN-0002: own rule" not in rules_text
            and "### LRN-0002: sibling rule" in rules_text,
            "own mirror row rewritten": "| LRN-0003 | own rule |" in rules_text
            and "| LRN-0002 | own rule |" not in rules_text
            and "| LRN-0002 | sibling rule |" in rules_text,
            "own body id rewritten": "Own body cites LRN-0003 once." in rules_text,
            "evidence ledger follows": "### LRN-0003: own rule" in evidence_text
            and "Own evidence LRN-0003." in evidence_text,
            "dossier adrs updated": "adrs: [ADR-0003]" in dossier.read_text(),
            "index regenerated": index_text == render_index(adr_rows(adr))
            and "| ADR-0003 |" in index_text
            and index_text.count("| ADR-0002 |") == 1,
            "second run is a no-op": rc2 == 0
            and "nothing to renumber" in buf.getvalue(),
        }
        for name, passed in checks.items():
            ok &= passed
            print(f"{'PASS' if passed else 'FAIL'} — {name}")
    return ok


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

        print("\n--- selftest: a criterion with no owner/env is a DEFECT ---")
        noowner = GOOD_DOSSIER.replace(
            "2. `flush(batch_size=0)` raises ValueError. "
            "(owner: tests/unit/test_flush.py; env: local)",
            "2. `flush(batch_size=0)` raises ValueError.",
        )
        (d / "W-019-noowner.md").write_text(noowner.replace("W-014", "W-019"))
        noowner_rep = validate_dossier(d / "W-019-noowner.md", root)
        caught_owner = any("owner" in x for x in noowner_rep.defects)
        print("\n".join(noowner_rep.defects) or "(no defects)")
        print(
            f"\n{'PASS' if caught_owner else 'FAIL'} — ownerless criterion "
            f"{'detected' if caught_owner else 'NOT detected'}"
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

        print("\n--- selftest: done without a DEFERRED ledger is a DEFECT ---")
        nodeferred = GOOD_DOSSIER.replace("status: ready", "status: done").replace(
            "## Build log\n",
            "## Build log\n\nPROMISE_CHECKLIST: flush — return meaning.\n",
        )
        (d / "W-020-nodeferred.md").write_text(nodeferred.replace("W-014", "W-020"))
        nodeferred_rep = validate_dossier(d / "W-020-nodeferred.md", root)
        caught_deferred = any("DEFERRED" in x for x in nodeferred_rep.defects)
        print("\n".join(nodeferred_rep.defects) or "(no defects)")
        print(
            f"\n{'PASS' if caught_deferred else 'FAIL'} — missing DEFERRED "
            f"ledger {'detected' if caught_deferred else 'NOT detected'}"
        )

        print("\n--- selftest: done with a DEFERRED ledger is clean ---")
        deferred = GOOD_DOSSIER.replace("status: ready", "status: done").replace(
            "## Build log\n",
            "## Build log\n\nPROMISE_CHECKLIST: flush — return meaning.\n"
            "DEFERRED: none — sources: own reads, NOTICED harvest.\n",
        )
        (d / "W-021-deferred.md").write_text(deferred.replace("W-014", "W-021"))
        deferred_rep = validate_dossier(d / "W-021-deferred.md", root)
        deferred_clean = not any("DEFERRED" in x for x in deferred_rep.defects)
        print("\n".join(deferred_rep.defects) or "(no defects)")
        print(
            f"\n{'PASS' if deferred_clean else 'FAIL'} — DEFERRED ledger "
            f"{'accepted' if deferred_clean else 'rejected'}"
        )
        finalize_ok = selftest_finalize()
        return (
            rc
            if (
                caught
                and clean
                and caught_pcl
                and caught_lesson
                and caught_owner
                and caught_deferred
                and deferred_clean
                and finalize_ok
            )
            else 1
        )


# --------------------------------------------------------------------------
# pre-fan-out obligations
# --------------------------------------------------------------------------

CONTRACT_REVIEW_RE = re.compile(
    r"^\s*[-*]?\s*CONTRACT-REVIEW:\s*(spawned|skipped)\b", re.MULTILINE
)


def check_pre_fanout(path: Path) -> int:
    """Check the obligations that must hold before /work-on Phase 4 fans out.

    The contract review has no size gate: the orchestrator writes the contract
    and derives the checklist, so a defect in either is invisible to it for the
    same reason, on a small contract as much as a large one. Skipping stays
    allowed; skipping SILENTLY does not, because a skip that leaves no trace is
    indistinguishable from a step nobody remembered. Exactly one
    ``CONTRACT-REVIEW: spawned ...`` or ``CONTRACT-REVIEW: skipped — <reason>``
    line in ``## Build log`` satisfies it.

    Returns 0 when every obligation is recorded, 1 otherwise.
    """
    text = path.read_text(encoding="utf-8")
    body = text.split("## Build log", 1)
    if len(body) != 2:
        print(f"DEFECT  {path.name}: no '## Build log' section to check")
        return 1

    match = CONTRACT_REVIEW_RE.search(body[1])
    if not match:
        print(
            f"DEFECT  {path.name}: no CONTRACT-REVIEW: line in ## Build log.\n"
            "        The contract review runs on every build and has no size "
            "gate.\n"
            "        Record one of:\n"
            "          CONTRACT-REVIEW: spawned <ref> — <N> lines, <N> DEFECT, "
            "<N> AMBIGUITY\n"
            "          CONTRACT-REVIEW: skipped — <reason>"
        )
        return 1

    print(f"ok      {path.name}: contract review recorded ({match.group(1)})")
    return 0


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
        "--finalize-ids",
        action="store_true",
        help="renumber this branch's colliding ADR/LRN ids after the sync "
        "merge; requires --base",
    )
    ap.add_argument(
        "--base",
        metavar="REF",
        help="the ref this branch syncs against, e.g. origin/development "
        "(used by --finalize-ids)",
    )
    ap.add_argument(
        "--pre-fanout",
        action="store_true",
        help="check the pre-fan-out obligations for --dossier: the build log "
        "must record a CONTRACT-REVIEW: line (spawned or skipped)",
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

    if args.finalize_ids:
        if not args.base:
            print("DEFECT  --finalize-ids requires --base <ref>")
            return 1
        return run_finalize(root, args.base)

    rep = Report()
    targets_d: list[Path] = []
    targets_a: list[Path] = []

    if args.dossier:
        targets_d = sorted(dossier_dir.glob(f"{args.dossier}-*.md"))
        if not targets_d:
            print(f"DEFECT  no dossier matches '{args.dossier}' in {dossier_dir}")
            return 1
        if args.pre_fanout:
            return check_pre_fanout(targets_d[0])
    elif args.pre_fanout:
        print("DEFECT  --pre-fanout requires --dossier <ID>")
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
