#!/usr/bin/env python3
"""Mechanical checks for the contract-first pipeline.

Two file kinds are checked: the dossier (.discovery/dossiers/*.md, local by
default, committed when the repository tracks it) and
the ADR (docs/adr/*.md, committed). See references/formats/dossier.md and
references/formats/adr.md for both formats, and references/formats/writing.md
for the ASD-STE100 subset enforced here.

The check that matters most is work-package path disjointness. Three kinds of
agent build concurrently against one contract, so two packages that own the same
file corrupt a merge and produce test failures with no diagnosable cause.

Usage:
    validate_pipeline.py --dossier W-014
    validate_pipeline.py --all
    validate_pipeline.py --write-index
    validate_pipeline.py --finalize-ids --base origin/development
    validate_pipeline.py --mode
    validate_pipeline.py --selftest

Exit status is 1 when any DEFECT is reported, 0 otherwise. A WARNING never
fails the run.

No third-party imports: this runs wherever python3 does.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

# --------------------------------------------------------------------------
# format constants (references/formats/dossier.md, references/formats/adr.md)
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
    "planning_repo",
    "jira",
    "branch",
    "worktree",
    "pr",
    "blocked_by",
    "adrs",
]
DOSSIER_OPTIONAL_KEYS = ["planning_repo"]
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
    # A dossier planned in another checkout (#164): its anchors and baseline
    # were stamped against the planning repository. They are re-pointed at the
    # build repository at Phase 2; until then the mismatch is a warning that
    # names the re-point, never a DEFECT that fails validation.
    planning_repo = str(fm.get("planning_repo") or "").strip()
    for a in anchors:
        if ":" not in str(a):
            rep.defect(where, f"anchor '{a}' is not in path:line form")
            continue
        path_part, _, line_part = str(a).rpartition(":")
        target = repo_root / path_part
        if not target.exists():
            if planning_repo:
                rep.warn(
                    where,
                    f"anchor '{a}' does not resolve in the build repository — "
                    f"stamped against the planning repository ({planning_repo}); "
                    f"/work-on Phase 2 re-points it (a missing docs/research/ "
                    f"prefix is the recorded shape)",
                )
                continue
            rep.defect(where, f"anchor '{a}' points at a file that does not exist")
            continue
        if not line_part.isdigit():
            rep.defect(where, f"anchor '{a}' has a non-numeric line")
            continue
        n = len(target.read_text(errors="replace").split("\n"))
        if int(line_part) > n:
            rep.warn(
                where,
                f"anchor '{a}' is past the end of the file ({n} lines)"
                + (" — planned against another checkout" if planning_repo else ""),
            )


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


def _records():
    """build_records.py, beside this script (the one reader of the record formats)."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import build_records

    return build_records


def contract_paths(contract: str) -> list[str]:
    """The files the contract names: each `### <path>` heading under `## Contract`
    (`### Definitions` is prose, not a file)."""
    out = []
    for m in re.finditer(r"^###\s+`?([^\s`]+)`?[^\n]*$", contract, re.M):
        if not m.group(1).lower().startswith("definitions"):
            out.append(m.group(1))
    return out


CONTRACT_MEMBER_RE = re.compile(r"\b(?:def|fn|func|function)\s+([A-Za-z_]\w*)")


def check_checklist(where: str, path: Path, fm: dict, contract: str, log: str, status: str, rep: Report) -> None:
    """The promise checklist is an artefact, `.discovery/checklists/<ID>.md`, not a log mention.
    It must exist once the build is past planning, and its members must be in the contract.
    A contract member (def/fn/func/function) with no checklist line is a WARNING: a member with
    no stated promise legitimately has none."""
    if status not in {"building", "review", "pr", "done"}:
        return
    sev = rep.warn if status == "building" else rep.defect
    f = path.parent.parent / "checklists" / f"{fm.get('id')}.md"
    if not f.is_file():
        if "PROMISE_CHECKLIST" in log:
            rep.warn(where, f"promise checklist only named in the Build log (legacy); write {f.name} with "
                            f"`build_records.py checklist write {fm.get('id')} --from-file F`")
            return
        sev(where, f"status is '{status}' but there is no promise checklist artefact "
                   f".discovery/checklists/{fm.get('id')}.md (`build_records.py checklist write`)")
        return
    members = _records().checklist_members(f.read_text(encoding="utf-8"))
    if not members:
        rep.defect(where, f"checklist {f.name} holds no `<member> — <category>: <promise>` line")
        return
    phantom = [m for m in members if not re.search(r"(?<![\w])" + re.escape(re.split(r"[.:#]", m)[-1].strip("()")) + r"(?![\w])", contract)]
    if phantom:
        rep.defect(where, f"checklist {f.name} names members the contract does not: {', '.join(phantom)}")
    tails = {re.split(r"[.:#]", m)[-1].strip("()") for m in members}
    uncovered = sorted({m for m in CONTRACT_MEMBER_RE.findall(contract) if m not in tails and m not in members})
    if uncovered:
        rep.warn(where, f"contract members with no checklist line (fine when the docs state no promise): "
                        f"{', '.join(uncovered)}")


def check_deferred_record(where: str, path: Path, fm: dict, log: str, status: str, rep: Report) -> None:
    """The deferred ledger is a record, `.discovery/deferred/<ID>.yaml`: items (possibly none)
    plus `sources`. Status `done` needs it; a record that exists is checked at any status."""
    f = path.parent.parent / "deferred" / f"{fm.get('id')}.yaml"
    if f.is_file():
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except ValueError as exc:
            rep.defect(where, f"{f.name} is not readable: {exc}")
            return
        for problem in _records().validate_deferred(data if isinstance(data, dict) else {}):
            rep.defect(where, f"deferred record {f.name}: {problem}")
        return
    if status == "done":
        if "DEFERRED" in log:
            rep.warn(where, "deferred ledger only in the Build log (legacy); "
                            "`build_records.py deferred add|none` writes the record")
        else:
            rep.defect(
                where,
                "status is 'done' but there is no deferred record "
                f".discovery/deferred/{fm.get('id')}.yaml; /work-on Phase 9 writes it "
                "(`build_records.py deferred add`, or `deferred none --source ...` when nothing was left)",
            )


ROW3_RE = re.compile(r"^\W*ARBITRATION\b[^\n]*?\brows?\s*3\b", re.M)


def check_contract_fix_lessons(where: str, fm: dict, contract: str, repo_root: Path, status: str, rep: Report,
                               log: str = "") -> None:
    """A row-3 arbitration (the contract was ambiguous) is fixed by a commit that names its lesson in a
    `Lesson:` git trailer. Each row-3 ARBITRATION line in the build log needs one such commit after
    `contract_commit` touching a contract file. Implementer bodies and row-1/2 fixes touch the same
    files and need no trailer, so only the count is checked. Silent without git, a worktree, or a
    recorded contract commit."""
    if status not in {"building", "review", "pr", "done"}:
        return
    row3 = len(ROW3_RE.findall(log))
    base = str(fm.get("contract_commit") or "").strip()
    paths = contract_paths(contract)
    wt = str(fm.get("worktree") or "").strip()
    cwd = (repo_root / wt).resolve() if wt else repo_root
    if not row3 or not base or not paths or not cwd.is_dir():
        return
    try:
        out = git_out(cwd, "log", "--no-merges", "--format=%H%x00%B%x01", f"{base}..HEAD", "--", *paths)
    except RuntimeError:
        return
    lessons = sum(
        1 for chunk in out.split("\x01")
        if chunk.strip() and re.search(r"^Lesson:\s*\S", chunk.partition("\x00")[2], re.M)
    )
    if lessons < row3:
        rep.defect(
            where,
            f"{row3} row-3 arbitration(s) in the build log but {lessons} commit(s) after the contract "
            f"commit {base[:8]} carry a `Lesson:` trailer; a contract fix names the lesson in its commit message",
        )


# --------------------------------------------------------------------------
# visual review gate (VR-05)
# --------------------------------------------------------------------------

# The gate applies to a dossier whose front matter carries a `visual_review:` field. The Phase 3
# visual-review step writes it (`dossier_edit.py set <dossier> 'visual_review=required'`) before it
# runs, so a build that started before this step existed - including one still running when the
# plugin updates - is never flagged retroactively.
VISUAL_REVIEW_RE = re.compile(r"^\s*[-*]?\s*VISUAL-REVIEW:\s*(.+?)\s*$", re.M)
VISUAL_REVIEW_APPROVED_RE = re.compile(
    r"^approved\b.*?\bL0\s+(\d+)\b.*?\bL1\s+(\d+)\b.*?\bL2\s+(\d+)\b", re.I)
VISUAL_REVIEW_CHANGES_RE = re.compile(r"^changes requested\b.*\S", re.I)
VISUAL_REVIEW_VIEWS = (("l0.json", "L0"), ("l1.json", "L1"), ("l2.json", "L2"))


def visual_review_applies(fm: dict) -> bool:
    return "visual_review" in fm


def visual_review_problems(fm: dict, log: str, root: Path, worktree: Path | None = None) -> list[str]:
    """What is wrong with a dossier's visual review, [] when it is fine or does not apply.

    Needs, for a dossier the gate applies to (``visual_review_applies``): a ``VISUAL-REVIEW:`` line
    in the build log, the last one being ``approved — L0 <n>, L1 <n>, L2 <n>`` (a ``changes
    requested — <ids>`` line sends Phase 3 back to the contract; it is history, not a pass), and
    ``.discovery/visual-review/<ID>/l0.json``, ``l1.json`` and ``l2.json``, each valid under
    ``vr_graph.py validate`` with the view its name says, whose node counts equal the line's."""
    if not visual_review_applies(fm):
        return []
    lines = VISUAL_REVIEW_RE.findall(log)
    if not lines:
        return ["no VISUAL-REVIEW: line in ## Build log. Phase 3 runs the diagram step on every build: "
                "`VISUAL-REVIEW: approved — L0 <n>, L1 <n>, L2 <n> (<dashboard link>)`"]
    last = lines[-1]
    approved = VISUAL_REVIEW_APPROVED_RE.match(last)
    if not approved:
        if VISUAL_REVIEW_CHANGES_RE.match(last):
            return ["the last VISUAL-REVIEW: line is 'changes requested': revise the contract in Phase 3, "
                    "run the diagram step again and record 'approved'"]
        return [f"the last VISUAL-REVIEW: line is not well formed: {last[:80]!r}. Use "
                "`approved — L0 <n>, L1 <n>, L2 <n>` or `changes requested — <element ids>`"]
    dossier_id = str(fm.get("id") or "").strip()
    base = None
    for cand in (worktree, root):
        if cand and (cand / ".discovery" / "visual-review" / dossier_id).is_dir():
            base = cand
            break
    if base is None:
        return [f".discovery/visual-review/{dossier_id}/ is missing: the diagram step wrote no l0.json, l1.json, l2.json"]
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import vr_graph
    except ImportError:
        return ["vr_graph.py is not beside this script; the visual-review files cannot be validated"]
    problems: list[str] = []
    for (name, view), claimed in zip(VISUAL_REVIEW_VIEWS, approved.groups()):
        f = base / ".discovery" / "visual-review" / dossier_id / name
        if not f.is_file():
            problems.append(f"{f.relative_to(base)} is missing")
            continue
        try:
            graph = vr_graph.load(str(f))
        except (OSError, ValueError) as exc:
            problems.append(f"{name} is not readable: {exc}")
            continue
        for issue in vr_graph.validate(graph, root=str(base)):
            problems.append(f"{name}: {issue.get('code')} {issue.get('path')}: {issue.get('message')}")
        if isinstance(graph, dict) and graph.get("view") != view:
            problems.append(f"{name}: view is {graph.get('view')!r}, expected {view!r}")
        used = vr_graph.budget(graph).get("used") if isinstance(graph, dict) else None
        if used is not None and used != int(claimed):
            problems.append(f"{name}: the VISUAL-REVIEW: line says {view} {claimed}, the file holds {used}")
    return problems


def check_visual_review(where: str, path: Path, fm: dict, log: str, status: str, rep: Report) -> None:
    """Past the review (status review, pr, done) a dossier the gate applies to carries its record.
    The same check runs before the fan-out in ``check_pre_fanout``."""
    if status not in {"review", "pr", "done"}:
        return
    root = path.resolve().parents[2]
    wt = str(fm.get("worktree") or "").strip()
    worktree = (root / wt).resolve() if wt and (root / wt).is_dir() else None
    for problem in visual_review_problems(fm, log, root, worktree):
        rep.defect(where, problem)


def check_build_log(where: str, text: str, status: str, rep: Report, path: Path | None = None,
                    fm: dict | None = None, contract: str = "") -> None:
    """The gates read facts: the checklist artefact, the deferred record, the `Lesson:` trailers.
    The build log is the human view; nothing here greps it for a fact except the legacy fallbacks."""
    if path is None or fm is None:
        return
    check_checklist(where, path, fm, contract, text, status, rep)
    check_deferred_record(where, path, fm, text, status, rep)
    check_visual_review(where, path, fm, text, status, rep)


def check_contract_id(where: str, dossier_id: str, contract: str, rep: Report) -> None:
    """The dossier's own id inside `## Contract` is a DEFECT (#233) in local mode: /work-on copies the contract into
    source files, so the gitignored id would reach the contract commit as code text. In committed mode the id is
    public and this check does not run. The id anywhere else in the dossier raises nothing."""
    if not dossier_id:
        return
    pat = re.compile(r"(?<![A-Za-z0-9])" + re.escape(dossier_id) + r"(?![0-9])", re.I)
    for i, line in enumerate(contract.splitlines(), 1):
        if pat.search(line):
            rep.defect(where, f"line {i} names the dossier's own id {dossier_id}; the contract is copied into source "
                       "files, so the id would reach the contract commit. Describe the behaviour, or cite the Jira key")
            return


def validate_dossier(path: Path, repo_root: Path) -> Report:
    rep = Report()
    where = path.name
    try:
        fm, body, _ = split_front_matter(path.read_text())
    except ValueError as exc:
        rep.defect(where, str(exc))
        return rep

    check_keys(
        where, fm, [k for k in DOSSIER_KEYS if k not in DOSSIER_OPTIONAL_KEYS], rep
    )
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
        # Only where the id is local (#233): in committed mode dossiers are tracked and their ids are public, and a
        # repository that cites them in source is following its own habit, not leaking a gitignored name.
        if discovery_mode(repo_root)[0] != "committed":
            check_contract_id(f"{where} ## Contract", str(fm.get("id", "")), sec["## Contract"], rep)
    if "## Work packages" in sec:
        check_disjoint(
            f"{where} ## Work packages", parse_packages(sec["## Work packages"]), rep
        )
    if "## Acceptance criteria" in sec:
        check_criteria(
            f"{where} ## Acceptance criteria", sec["## Acceptance criteria"], rep
        )
    if "## Build log" in sec:
        check_build_log(f"{where} ## Build log", sec["## Build log"], str(status), rep,
                        path, fm, sec.get("## Contract", ""))
    check_contract_fix_lessons(where, fm, sec.get("## Contract", ""), repo_root, str(status), rep,
                               sec.get("## Build log", ""))

    if status == "ready" and not fm.get("baseline_commit"):
        rep.defect(
            where,
            "status is 'ready' but baseline_commit is empty; /plan stamps it last",
        )
    elif fm.get("baseline_commit") and not str(fm.get("planning_repo") or "").strip():
        # #164: a baseline that resolves in no repository of the build checkout
        # was stamped against the planning one; /work-on forks from the target
        # tip and re-stamps. Recorded separately, it is a warning, not a DEFECT.
        sha = str(fm.get("baseline_commit"))
        probe = (
            subprocess.run(
                ["git", "-C", str(repo_root), "cat-file", "-e", f"{sha}^{{commit}}"],
                capture_output=True,
            )
            if (repo_root / ".git").exists()
            else None
        )
        if probe is not None and probe.returncode != 0:
            rep.warn(
                where,
                f"baseline_commit '{sha[:12]}' is not a commit in this repository — "
                "stamped against the planning checkout; record `planning_repo:` or "
                "re-stamp against the build repository (/work-on Phase 2 forks from "
                "the target tip)",
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


# Non-ADR documents that may legitimately live in docs/adr/: the index, and
# the rules file phase-8.md 8b itself recommends creating there.
ADR_SKIP_FILES = {"index.md", "RULES.md"}


def adr_targets(adr_dir: Path) -> list[Path]:
    """Every ADR under `adr_dir`; the index and RULES.md are not ADRs
    (#165: phase-8.md 8b recommends `docs/adr/RULES.md` for a repository's
    rules file, and a rules file there was failed as a fence-less ADR)."""
    if not adr_dir.exists():
        return []
    return [p for p in sorted(adr_dir.glob("*.md")) if p.name not in ADR_SKIP_FILES]


def adr_rows(adr_dir: Path) -> list[tuple[str, str, str]]:
    rows = []
    for p in sorted(adr_dir.glob("*.md")):
        if p.name in ADR_SKIP_FILES:
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


def own_added(
    root: Path, base: str, tip: str, pathspec: str, diff_filter: str = "A"
) -> set[str]:
    """Files this branch added (or, with `diff_filter="AM"`, added or modified)
    under `pathspec`, as repo-relative names, relative to `merge-base(base, tip)`
    — taken before the merge commit, so a sync merge never widens ownership.
    """
    mb = git_out(root, "merge-base", base, tip).strip()
    out = git_out(
        root,
        "diff",
        "--name-only",
        f"--diff-filter={diff_filter}",
        mb,
        tip,
        "--",
        pathspec,
    )
    return {x for x in out.splitlines() if x}


def own_added_adrs(root: Path, base: str, tip: str) -> set[str]:
    """ADR paths this branch added, as repo-relative names."""
    return own_added(root, base, tip, "docs/adr")


def collect_adr_ids(adr_dir: Path) -> dict[str, list[Path]]:
    by_id: dict[str, list[Path]] = {}
    for p in sorted(adr_dir.glob("[0-9][0-9][0-9][0-9]-*.md")):
        fm, _, _ = split_front_matter(p.read_text())
        aid = str(fm.get("id", "?"))
        by_id.setdefault(aid, []).append(p)
    return by_id


# A blocker taken out of `blocked_by` is justified by a fact: the blocker dossier is `done`, or
# `.discovery/blocker-removals/<ID>.yaml` holds a `{blocker, reason}` row (written by
# `dossier_edit.py ... --reason`). The legacy `BLOCKER-REMOVED: W-079 — <reason>` build-log line
# still justifies a removal in a dossier written before the record existed.
BLOCKER_REMOVED_RE = re.compile(r"BLOCKER-REMOVED:\s*(W-\d+)\s*[—-]+\s*\S")
WID_RE = re.compile(r"W-\d+")


def default_base(root: Path) -> str | None:
    """The ref a branch is measured against when --base is not given:
    `origin/HEAD` (the remote's default branch), or None when there is none."""
    p = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "--verify", "--quiet", "origin/HEAD"],
        capture_output=True,
        text=True,
    )
    return "origin/HEAD" if p.returncode == 0 else None


def blocker_removal_facts(path: Path, then: set[str]) -> set[str]:
    """The ids of `then` whose removal from the dossier at `path` is justified by a fact."""
    ok: set[str] = set()
    disc = path.parent.parent
    try:
        fm, _, _ = split_front_matter(path.read_text())
        rec = json.loads((disc / "blocker-removals" / f"{fm.get('id')}.yaml").read_text())
        ok |= {str(r.get("blocker")) for r in rec.get("removals", []) if str(r.get("reason") or "").strip()}
    except (ValueError, OSError, AttributeError):
        pass
    for wid in then - ok:
        for q in sorted(path.parent.glob(f"{wid}*.md")):
            try:
                qfm, _, _ = split_front_matter(q.read_text())
            except (ValueError, OSError):
                continue
            if qfm.get("id") == wid and qfm.get("status") == "done":
                ok.add(wid)
    return ok


def check_blocker_removals(
    root: Path, paths: list[Path], base: str | None, rep: Report
) -> None:
    """A blocker never silently leaves `blocked_by`.

    `blocked_by` is the dossier graph as well as a gate: a done blocker records
    what the dossier builds on. A recorded `/plan` run dropped a done blocker
    because it no longer gated anything, and cut that dossier out of the graph.
    For each dossier, compare its `blocked_by` with the dossier at
    `merge-base(base, HEAD)`; an id there and not here is a DEFECT unless
    `## Build log` holds `BLOCKER-REMOVED: <id> — <reason>`. Outside git, with
    no base, or for a dossier the base does not have, the check is silent.
    """
    if base is None:
        return
    try:
        mb = git_out(root, "merge-base", base, "HEAD").strip()
    except RuntimeError:
        return
    for p in paths:
        try:
            rel = p.resolve().relative_to(root.resolve()).as_posix()
            fm_now, body, _ = split_front_matter(p.read_text())
        except (ValueError, OSError):
            continue
        before = git_show(root, mb, rel)
        if not before:
            continue
        try:
            fm_then, _, _ = split_front_matter(before)
        except ValueError:
            continue
        then = set(WID_RE.findall(str(fm_then.get("blocked_by") or "")))
        now = set(WID_RE.findall(str(fm_now.get("blocked_by") or "")))
        justified = blocker_removal_facts(p, then - now) | set(BLOCKER_REMOVED_RE.findall(body))
        for wid in sorted(then - now - justified):
            rep.defect(
                p.name,
                f"blocked_by drops {wid}, which it holds at {base}, and {wid} is not done. "
                f"Remove it with `dossier_edit.py remove ... blocked_by={wid} --reason '<why>'`, "
                f"which records the reason.",
            )


def collect_dossier_ids(dossier_dir: Path) -> dict[str, list[Path]]:
    """Front-matter id -> the dossier files that carry it."""
    by_id: dict[str, list[Path]] = {}
    for p in sorted(dossier_dir.glob("W-*.md")):
        try:
            fm, _, _ = split_front_matter(p.read_text())
        except ValueError:
            continue
        by_id.setdefault(str(fm.get("id", "?")), []).append(p)
    return by_id


def check_dossier_ids(dossier_dir: Path, rep: Report) -> None:
    """Two dossiers with one id, or a file named for another id, is a DEFECT.

    In committed mode two plan PRs can land the same number
    (formats/evidence-and-ids.md §4).
    Nothing else catches it: each file validates on its own, and /work-on
    would build whichever one it read first.
    """
    for did, files in sorted(collect_dossier_ids(dossier_dir).items()):
        if len(files) > 1:
            names = ", ".join(p.name for p in files)
            rep.defect(
                "dossiers",
                f"{did} is the id of two dossiers: {names} — a plan collision "
                "that landed; the later one renumbers in its own PR "
                "(--finalize-ids)",
            )
        for p in files:
            if not p.name.startswith(f"{did}-"):
                rep.defect(
                    p.name,
                    f"file is named for another id than its front matter ({did})",
                )


def plan_renumber(
    by_id: dict[str, list[str]], own: set[str], taken: list[int], fmt: str
) -> tuple[dict[str, str], list[str]]:
    """Plan fresh ids for the branch's own files inside duplicate-id groups.

    `by_id` maps front-matter id to repo-relative file names, `own` is the
    set of files this branch added, `taken` holds every id number in use
    after the merge, and `fmt` renders a number as an id (`ADR-{:04d}`,
    `W-{:03d}`). A group with no own claimant means the base itself is
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
        renames[own_files[0]] = fmt.format(nxt)
        nxt += 1
    return renames, defects


def plan_adr_renumber(
    by_id: dict[str, list[str]], own: set[str], taken: list[int]
) -> tuple[dict[str, str], list[str]]:
    return plan_renumber(by_id, own, taken, "ADR-{:04d}")


def rewrite_ids(text: str, id_map: dict[str, str]) -> str:
    """Single-pass id replacement: no id is rewritten twice (no chaining)."""
    if not id_map:
        return text
    pattern = re.compile("|".join(sorted(id_map, key=len, reverse=True)))
    return pattern.sub(lambda m: id_map[m.group(0)], text)


def rewrite_dossier_ids(text: str, id_map: dict[str, str]) -> str:
    """Like rewrite_ids, but W-015 never matches inside W-0150 or W-0157."""
    if not id_map:
        return text
    pattern = re.compile(
        r"(?<!\d)(?:"
        + "|".join(sorted(map(re.escape, id_map), key=len, reverse=True))
        + r")(?!\d)"
    )
    return pattern.sub(lambda m: id_map[m.group(0)], text)


def rewrite_dossier_front_matter(text: str, id_map: dict[str, str]) -> str:
    """Rewrite `id_map` occurrences inside a dossier's front matter only.

    The front matter holds the live references to a renamed ADR — the
    `adrs:` list and any anchor naming the renamed file. The body, including
    `## Build log` prose that quotes an id as history, is never touched: a
    dossier this branch owns may still narrate, verbatim, what an id used to
    be.
    """
    if not id_map or not text.startswith("---\n"):
        return text
    end = text.find("\n---\n", 4)
    if end == -1:
        return text
    fm = text[: end + 5]
    body = text[end + 5 :]
    return rewrite_ids(fm, id_map) + body


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
        # Own = added or modified by this branch relative to the merge-base,
        # taken before the merge commit. A dossier the base branch owns is
        # never rewritten, even when it mentions the moving id.
        own_dossiers = own_added(
            root, base, tip, ".discovery/dossiers", diff_filter="AM"
        )
        moving_re = re.compile("|".join(sorted(id_map, key=len, reverse=True)))
        for p in sorted(dossier_dir.glob("*.md")):
            text = p.read_text()
            rel = f".discovery/dossiers/{p.name}"
            if rel in own_dossiers:
                new_text = rewrite_dossier_front_matter(text, id_map)
                if new_text != text:
                    p.write_text(new_text)
                    print(f"updated dossier {p.name}")
            elif moving_re.search(text):
                print(
                    f"NOTICE  {p.name} mentions a renumbered id "
                    f"({', '.join(sorted(id_map))}) but this branch does "
                    f"not own it — left untouched"
                )

    # Dossiers collide the same way when .discovery/ is committed: two plan
    # branches mint one number against the same base tip. Own files only.
    dossier_map: dict[str, str] = {}
    if dossier_dir.exists():
        own_d = own_added(root, base, tip, ".discovery/dossiers")
        by_did = collect_dossier_ids(dossier_dir)
        rel_by_did = {
            did: [f".discovery/dossiers/{p.name}" for p in files]
            for did, files in by_did.items()
        }
        numbered = [did for did in by_did if re.fullmatch(r"W-\d{3,}", did)]
        taken_d = [int(did[2:]) for did in numbered]
        width = max((len(did[2:]) for did in numbered), default=3)
        renames_d, defects_d = plan_renumber(
            rel_by_did, own_d, taken_d, "W-{:0" + str(width) + "d}"
        )
        defects += defects_d
        for rel, new_id in sorted(renames_d.items()):
            old_id = next(did for did, rels in rel_by_did.items() if rel in rels)
            dossier_map[old_id] = new_id
            summary.append(f"{old_id} -> {new_id} ({rel})")
        if dossier_map:
            for p in sorted(dossier_dir.glob("*.md")):
                if f".discovery/dossiers/{p.name}" in own_d:
                    p.write_text(rewrite_dossier_ids(p.read_text(), dossier_map))
            for rel, new_id in sorted(renames_d.items()):
                p = root / rel
                old_id = next(k for k, v in dossier_map.items() if v == new_id)
                p.replace(dossier_dir / (new_id + p.name[len(old_id) :]))

    if not summary:
        print("no collisions — nothing to renumber")
    for line in summary:
        print(f"renumbered: {line}")

    if id_map or dossier_map:
        full_map = dict(id_map)
        full_map.update(dossier_map)
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

    renames_d, defects_d = plan_renumber(
        {"W-014": ["a/W-014-one.md"], "W-015": ["a/W-015-own.md", "a/W-015-sib.md"]},
        {"a/W-015-own.md"},
        [14, 15],
        "W-{:03d}",
    )
    bounded = rewrite_dossier_ids("W-015 W-0150 W-0157 W-015.", {"W-015": "W-016"})
    case = (
        renames_d == {"a/W-015-own.md": "W-016"}
        and not defects_d
        and bounded == "W-016 W-0150 W-0157 W-016."
    )
    ok &= case
    print(
        f"{'PASS' if case else 'FAIL'} — dossier planner and bounded rewrite: {renames_d} / {bounded}"
    )

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
            (dossiers / "W-0902-own.md").write_text(
                "---\nid: W-0902\nadrs: []\nblocked_by: [W-0901]\n---\n\n"
                "## Problem\n\nOwn plan W-0902 cites W-0901.\n"
            )
            with rules.open("a") as f:
                f.write("\n### LRN-0002: own rule\n\nOwn body cites LRN-0002 once.\n")
                f.write("| LRN-0002 | own rule |\n")
            with evidence.open("a") as f:
                f.write("\n### LRN-0002: own rule\n\nOwn evidence LRN-0002.\n")
            dossier.write_text(
                "---\nid: W-0901\nadrs: [ADR-0002]\n---\n\n## Problem\n\nP.\n\n"
                "## Build log\n\n"
                "- note: earlier this branch wrote adrs: [ADR-0002] here by "
                "mistake, restored by hand\n"
            )
            git_out(root, "add", "-A")
            git_out(root, "commit", "-q", "-m", "own")

            git_out(root, "checkout", "-q", "main")
            (adr / "0002-sib.md").write_text(
                FINALIZE_ADR.format(num="0002", title="Sibling decision")
            )
            (dossiers / "W-0902-sib.md").write_text(
                "---\nid: W-0902\nadrs: []\n---\n\n## Problem\n\nSibling plan.\n"
            )
            (dossiers / "W-0900-foreign.md").write_text(
                "---\nid: W-0900\nadrs: []\n---\n\n## Build log\n\n"
                "- note: see ADR-0002 (the sibling's own decision) for "
                "background\n"
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

        sib_before = (dossiers / "W-0902-sib.md").read_bytes()
        foreign_before = (dossiers / "W-0900-foreign.md").read_bytes()

        buf1 = io.StringIO()
        with contextlib.redirect_stdout(buf1):
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
            "own dossier build-log quote unchanged": (
                "wrote adrs: [ADR-0002] here by mistake" in dossier.read_text()
            ),
            "sibling dossier byte-identical": (dossiers / "W-0902-sib.md").read_bytes()
            == sib_before,
            "foreign dossier untouched, NOTICE printed": (
                dossiers / "W-0900-foreign.md"
            ).read_bytes()
            == foreign_before
            and "W-0900-foreign.md" in buf1.getvalue()
            and "NOTICE" in buf1.getvalue(),
            "own dossier renumbered to W-0903": (dossiers / "W-0903-own.md").exists()
            and not (dossiers / "W-0902-own.md").exists()
            and "id: W-0903" in (dossiers / "W-0903-own.md").read_text()
            and "Own plan W-0903 cites W-0901."
            in (dossiers / "W-0903-own.md").read_text(),
            "sibling dossier untouched": "id: W-0902"
            in (dossiers / "W-0902-sib.md").read_text(),
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

        # #164: a dossier planned in another checkout validates in the build
        # repository — anchors and baseline warn and name the re-point, they do
        # not fail; without planning_repo the old defects stand.
        planned_elsewhere = (
            GOOD_DOSSIER.replace("id: W-014", "id: W-015")
            .replace(
                "baseline_commit: 3f2611f",
                "baseline_commit: 7a1486b\nplanning_repo: https://github.com/org/planning-checkout",
            )
            .replace(
                "  - src/flush.py:3",
                "  - docs/research/podcast-plan.md:12\n  - src/flush.py:3",
            )
        )
        (d / "W-015-planned.md").write_text(planned_elsewhere)
        rep2 = Report()
        rep2.merge(validate_dossier(d / "W-015-planned.md", root))
        checks_164 = {
            "planned elsewhere: no anchor DEFECT": not rep2.defects
            or not any("anchor" in x for x in rep2.defects),
            "planned elsewhere: the unresolvable anchor warns and names the re-point": any(
                "planning repository" in w for w in rep2.warnings
            ),
        }
        (d / "W-016-noplanning.md").write_text(
            planned_elsewhere.replace(
                "\nplanning_repo: https://github.com/org/planning-checkout", ""
            )
            .replace("  - docs/research/podcast-plan.md:12\n", "")
            .replace("id: W-015", "id: W-016")
        )
        rep3 = Report()
        rep3.merge(validate_dossier(d / "W-016-noplanning.md", root))
        checks_164[
            "without planning_repo an unresolvable baseline only warns (a tempdir is no git repository; the probe is skipped)"
        ] = not rep3.defects
        for name, passed in checks_164.items():
            print(f"{'PASS' if passed else 'FAIL'} — {name}")
        if not all(checks_164.values()):
            return 1
        (d / "W-015-planned.md").unlink()
        (d / "W-016-noplanning.md").unlink()

        # #233: the dossier's own id inside ## Contract is a DEFECT; in ## Build log it is fine.
        (d / "W-017-idcontract.md").write_text(
            GOOD_DOSSIER.replace("id: W-014", "id: W-017").replace(
                "Drain queued items to the store, oldest first.", "Drain queued items (see w-017) to the store, oldest first."))
        (d / "W-018-idlog.md").write_text(GOOD_DOSSIER.replace("id: W-014", "id: W-018") + "\nW-018: noted in the log.\n")
        r17, r18 = Report(), Report()
        r17.merge(validate_dossier(d / "W-017-idcontract.md", root))
        r18.merge(validate_dossier(d / "W-018-idlog.md", root))
        # In committed mode (the repository tracks .discovery/) the id is public: no DEFECT.
        croot = root / "committed"
        (croot / ".discovery" / "dossiers").mkdir(parents=True)
        (croot / "src").mkdir()
        (croot / "src" / "flush.py").write_text("a\nb\nc\nd\n")
        (croot / ".discovery" / "dossiers" / "W-017-idcontract.md").write_text((d / "W-017-idcontract.md").read_text())
        for args in (["init", "-q"], ["add", ".discovery"], ["-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "x"]):
            subprocess.run(["git", "-C", str(croot), *args], capture_output=True, check=False)
        r19 = Report()
        r19.merge(validate_dossier(croot / ".discovery" / "dossiers" / "W-017-idcontract.md", croot))
        checks_233 = {
            "the dossier's own id inside ## Contract is a DEFECT naming the id": any("## Contract" in x and "W-017" in x for x in r17.defects),
            "the dossier's own id outside ## Contract raises nothing": not any("own id" in x for x in r18.defects),
            "in committed mode the id inside ## Contract raises nothing": not any("own id" in x for x in r19.defects),
        }
        for name, passed in checks_233.items():
            print(f"{'PASS' if passed else 'FAIL'} — {name}")
        if not all(checks_233.values()):
            return 1
        (d / "W-017-idcontract.md").unlink()
        (d / "W-018-idlog.md").unlink()

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

        print("\n--- selftest: review status without a checklist artefact is a DEFECT ---")
        nocheck = GOOD_DOSSIER.replace("status: ready", "status: review")
        (d / "W-017-nocheck.md").write_text(nocheck.replace("W-014", "W-017"))
        nocheck_rep = validate_dossier(d / "W-017-nocheck.md", root)
        caught_pcl = any("checklist artefact" in x for x in nocheck_rep.defects)
        # a Build log mention is not a checklist
        (d / "W-017-nocheck.md").write_text(
            nocheck.replace("W-014", "W-017").replace("## Build log\n", "## Build log\n\nPROMISE_CHECKLIST: flush — return meaning.\n")
        )
        mention_rep = validate_dossier(d / "W-017-nocheck.md", root)
        caught_pcl = caught_pcl and not any("PROMISE_CHECKLIST" in x for x in mention_rep.defects) and any(
            "legacy" in x for x in mention_rep.warnings
        )
        # the artefact must name contract members
        ck = root / ".discovery" / "checklists"
        ck.mkdir(parents=True, exist_ok=True)
        (ck / "W-017.md").write_text("ghost_member — return meaning: gives 3\n")
        ghost_rep = validate_dossier(d / "W-017-nocheck.md", root)
        caught_pcl = caught_pcl and any("ghost_member" in x for x in ghost_rep.defects)
        (ck / "W-017.md").write_text("flush — return meaning: gives 3\n")
        ok_rep = validate_dossier(d / "W-017-nocheck.md", root)
        caught_pcl = caught_pcl and not any("checklist" in x for x in ok_rep.defects)
        print("\n".join(nocheck_rep.defects + ghost_rep.defects) or "(no defects)")
        print(
            f"\n{'PASS' if caught_pcl else 'FAIL'} — missing checklist artefact "
            f"{'detected, a log mention is not one, phantom members refused' if caught_pcl else 'NOT detected'}"
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

        print("\n--- selftest: a contract-fix commit without a Lesson trailer is a DEFECT ---")
        caught_lesson = selftest_contract_lessons()
        caught_receipts = selftest_receipts()

        print("\n--- selftest: done without a deferred record is a DEFECT ---")
        nodeferred = GOOD_DOSSIER.replace("status: ready", "status: done")
        (d / "W-020-nodeferred.md").write_text(nodeferred.replace("W-014", "W-020"))
        nodeferred_rep = validate_dossier(d / "W-020-nodeferred.md", root)
        caught_deferred = any("no deferred record" in x for x in nodeferred_rep.defects)
        legacy = nodeferred.replace("W-014", "W-020").replace("## Build log\n", "## Build log\n\nDEFERRED: none — sources: own reads.\n")
        (d / "W-020-nodeferred.md").write_text(legacy)
        legacy_rep = validate_dossier(d / "W-020-nodeferred.md", root)
        caught_deferred = caught_deferred and not any("deferred record" in x for x in legacy_rep.defects)
        dr = root / ".discovery" / "deferred"
        dr.mkdir(parents=True, exist_ok=True)
        (dr / "W-020.yaml").write_text('{"items": [], "sources": []}')
        empty_rep = validate_dossier(d / "W-020-nodeferred.md", root)
        caught_deferred = caught_deferred and any("sources" in x for x in empty_rep.defects)
        (dr / "W-020.yaml").unlink()
        print("\n".join(nodeferred_rep.defects) or "(no defects)")
        print(
            f"\n{'PASS' if caught_deferred else 'FAIL'} — missing deferred "
            f"record {'detected' if caught_deferred else 'NOT detected'}"
        )

        print("\n--- selftest: done with a deferred record is clean ---")
        deferred = GOOD_DOSSIER.replace("status: ready", "status: done").replace("W-014", "W-021")
        (d / "W-021-deferred.md").write_text(deferred)
        (ck / "W-021.md").write_text("flush — return meaning: gives 3\n")
        (dr / "W-021.yaml").write_text('{"items": [], "sources": ["own reads", "NOTICED harvest"]}')
        deferred_rep = validate_dossier(d / "W-021-deferred.md", root)
        deferred_clean = not any("deferred record" in x or "checklist" in x for x in deferred_rep.defects)
        print("\n".join(deferred_rep.defects) or "(no defects)")
        print(
            f"\n{'PASS' if deferred_clean else 'FAIL'} — deferred record "
            f"{'accepted' if deferred_clean else 'rejected'}"
        )
        print("\n--- selftest: two dossiers with one id is a DEFECT ---")
        (d / "W-014-twin.md").write_text(GOOD_DOSSIER)
        dup_rep = Report()
        check_dossier_ids(d, dup_rep)
        caught_dup = any("two dossiers" in x for x in dup_rep.defects)
        (d / "W-014-twin.md").unlink()
        clean_rep = Report()
        check_dossier_ids(d, clean_rep)
        caught_dup = caught_dup and not clean_rep.defects
        print("\n".join(dup_rep.defects) or "(no defects)")
        print(
            f"\n{'PASS' if caught_dup else 'FAIL'} — duplicate dossier id "
            f"{'detected, distinct ids clean' if caught_dup else 'NOT handled'}"
        )

        finalize_ok = selftest_finalize()
        pre_fanout_ok = selftest_pre_fanout()
        pre_fanout_local_ok = selftest_pre_fanout_local()
        visual_ok = selftest_visual_review()
        mode_ok = selftest_mode()
        blockers_ok = selftest_blocker_removal()
        return (
            rc
            if (
                caught
                and clean
                and caught_pcl
                and caught_lesson
                and caught_receipts
                and caught_owner
                and caught_deferred
                and deferred_clean
                and caught_dup
                and finalize_ok
                and pre_fanout_ok
                and pre_fanout_local_ok
                and visual_ok
                and mode_ok
                and blockers_ok
            )
            else 1
        )


def selftest_receipts() -> bool:
    """check_receipts: hooks and contract_review live in front matter; a stale SHA is refused."""
    if not shutil.which("git"):
        print("SKIP  git not found; the receipts scenario was not run")
        return True
    body = "## Contract\n\n### src/flush.py\n\n```python\ndef flush(): ...\n```\n"

    def doc(extra: str) -> str:
        return f"---\nid: W-9\nstatus: building\n{extra}---\n\n{body}"

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        try:
            git_out(root, "init", "-q", "-b", "main")
            git_out(root, "config", "user.email", "selftest@example.invalid")
            git_out(root, "config", "user.name", "selftest")
            (root / "src").mkdir()
            (root / "src" / "flush.py").write_text("def flush(): ...\n")
            git_out(root, "add", "-A")
            git_out(root, "commit", "-q", "-m", "contract")
            first = git_out(root, "rev-parse", "HEAD").strip()
            (root / "src" / "flush.py").write_text("def flush():  # returns the count\n")
            git_out(root, "commit", "-q", "-am", "contract moved")
            second = git_out(root, "rev-parse", "HEAD").strip()
        except RuntimeError as exc:
            print(f"FAIL  fixture: {exc}")
            return False

        def run(extra: str) -> int:
            f = root / "dossier.md"
            f.write_text(doc(extra), encoding="utf-8")
            return check_receipts(f, f.read_text(encoding="utf-8"), root, None)

        cases = [
            ("current SHA passes", run(f"hooks: none\ncontract_review: {second}\n") == 0),
            ("stale SHA is refused", run(f"hooks: none\ncontract_review: {first}\n") == 1),
            ("missing hooks is refused", run(f"contract_review: {second}\n") == 1),
            ("missing review is refused", run("hooks: none\n") == 1),
            ("skipped with a reason passes", run("hooks: none\ncontract_review: skipped \u2014 tiny\n") == 0),
            ("skipped without a reason is refused", run("hooks: none\ncontract_review: skipped\n") == 1),
        ]
    ok = True
    for name, passed in cases:
        print(f"{'ok  ' if passed else 'FAIL'}  receipts: {name}")
        ok &= passed
    return ok


def selftest_contract_lessons() -> bool:
    """Each row-3 arbitration needs one `Lesson:`-trailed commit to a contract file after the contract commit."""
    if not shutil.which("git"):
        print("SKIP  git not found; the contract-fix scenario was not run")
        return True
    contract = "### src/flush.py\n\n```python\ndef flush(): ...\n```\n"
    one = "ARBITRATION 1 — row 3 (contract ambiguous).\n"
    two = one + "- ARBITRATION: test_flush red — contract ambiguous, row 3\n"
    fm = {"worktree": "", "contract_commit": ""}
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        try:
            git_out(root, "init", "-q", "-b", "main")
            git_out(root, "config", "user.email", "selftest@example.invalid")
            git_out(root, "config", "user.name", "selftest")
            (root / "src").mkdir()
            (root / "src" / "flush.py").write_text("def flush(): ...\n")
            git_out(root, "add", "-A")
            git_out(root, "commit", "-q", "-m", "contract")
            fm["contract_commit"] = git_out(root, "rev-parse", "HEAD").strip()
            (root / "src" / "flush.py").write_text("def flush():\n    return 0\n")
            git_out(root, "commit", "-q", "-am", "implement flush")
            body = Report()
            check_contract_fix_lessons("w", fm, contract, root, "building", body, "")
            bad = Report()
            check_contract_fix_lessons("w", fm, contract, root, "building", bad, one)
            (root / "src" / "flush.py").write_text("def flush():  # returns the count\n    return 0\n")
            git_out(root, "commit", "-q", "-am", "fix the contract\n\nLesson: name the return meaning")
            good = Report()
            check_contract_fix_lessons("w", fm, contract, root, "building", good, one)
            short = Report()
            check_contract_fix_lessons("w", fm, contract, root, "building", short, two)
            planned = Report()
            check_contract_fix_lessons("w", fm, contract, root, "ready", planned, two)
        except RuntimeError as exc:
            print(f"FAIL  fixture: {exc}")
            return False
    cases = [
        ("an implementer body with no row 3 needs no trailer", not body.defects),
        ("a row 3 with no Lesson commit is refused", len(bad.defects) == 1 and "Lesson" in bad.defects[0]),
        ("a Lesson commit covers one row 3", not good.defects),
        ("two row 3s need two Lesson commits", len(short.defects) == 1),
        ("a ready dossier is not checked", not planned.defects),
    ]
    for name, passed in cases:
        print(f"{'PASS' if passed else 'FAIL'}  {name}")
    return all(p for _, p in cases)


def selftest_blocker_removal() -> bool:
    """A blocker dropped on a branch is a defect unless the build log justifies it."""
    print("\n--- selftest: a blocker never silently leaves blocked_by ---")
    if not shutil.which("git"):
        print("SKIP  git not found; the blocker scenario was not run")
        return True
    dossier = (
        "---\nid: W-0002\nstatus: planned\nblocked_by: {blockers}\n---\n\n"
        "## Problem\n\nx\n\n## Build log\n\n- seeded{extra}\n"
    )
    ok = True
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "repo"
        d = root / ".discovery" / "dossiers"
        d.mkdir(parents=True)
        path = d / "W-0002-b.md"
        try:
            git_out(root, "init", "-q", "-b", "main")
            git_out(root, "config", "user.email", "selftest@example.invalid")
            git_out(root, "config", "user.name", "selftest")
            path.write_text(dossier.format(blockers="[W-0001]", extra=""))
            git_out(root, "add", "-A")
            git_out(root, "commit", "-q", "-m", "base")
            git_out(root, "checkout", "-q", "-b", "plan/x")
        except RuntimeError as exc:
            print(f"FAIL  fixture: {exc}")
            return False

        def defects(blockers: str, extra: str = "") -> list[str]:
            path.write_text(dossier.format(blockers=blockers, extra=extra))
            rep = Report()
            check_blocker_removals(root, [path], "main", rep)
            return rep.defects

        cases = [
            ("a dropped blocker is refused", bool(defects("[]"))),
            ("the defect names the blocker", any("W-0001" in x for x in defects("[]"))),
            ("a kept blocker passes", not defects("[W-0001]")),
            ("an added blocker passes", not defects("[W-0001, W-0003]")),
            (
                "a legacy log line still justifies a removal",
                not defects("[]", "\n- BLOCKER-REMOVED: W-0001 — the dependency was wrong"),
            ),
            (
                "a legacy log line without a reason is refused",
                bool(defects("[]", "\n- BLOCKER-REMOVED: W-0001 —")),
            ),
        ]
        rec_dir = root / ".discovery" / "blocker-removals"
        rec_dir.mkdir()
        (rec_dir / "W-0002.yaml").write_text('{"removals": [{"blocker": "W-0001", "reason": "wrong"}]}')
        cases.append(("a recorded reason justifies a removal", not defects("[]")))
        (rec_dir / "W-0002.yaml").write_text('{"removals": [{"blocker": "W-0001", "reason": ""}]}')
        cases.append(("a recorded row without a reason does not", bool(defects("[]"))))
        (rec_dir / "W-0002.yaml").unlink()
        (d / "W-0001-a.md").write_text("---\nid: W-0001\nstatus: done\n---\n\n## Build log\n")
        cases.append(("a done blocker leaves without a reason", not defects("[]")))
        (d / "W-0001-a.md").write_text("---\nid: W-0001\nstatus: building\n---\n\n## Build log\n")
        cases.append(("a building blocker does not", bool(defects("[]"))))
        rep = Report()
        check_blocker_removals(root, [path], None, rep)
        cases.append(("no base, no check", not rep.defects))
        for name, passed in cases:
            print(f"{'PASS' if passed else 'FAIL'}  {name}")
            ok = ok and passed
    return ok


# --------------------------------------------------------------------------
# pre-fan-out obligations
# --------------------------------------------------------------------------

# The contract review and the hook policy are front matter facts, not log lines: `contract_review:`
# holds the SHA the review read (or `skipped — <reason>`), `hooks:` holds `none` or `<hook> — <policy>`.
# See check_receipts.
# `PAYLOAD-LINT: <N> payloads, <N> defects ...`: every payload was a file and
# went through check_payload.py before the fan-out message. A payload that was
# never a file was never linted.
PAYLOAD_LINT_RE = re.compile(
    r"^\s*[-*]?\s*PAYLOAD-LINT:\s*(\d+)\s+payloads?\b.*?(\d+)\s+defects?\b",
    re.MULTILINE,
)
# `ADMISSION: granted <N> slots — <provider>/<model>, cap <C>, ceiling <K>` or
# `ADMISSION: skipped — <reason>`: the wave took its slots from the machine-wide
# ledger (scripts/spawn_admission.py) before the spawn message, so a wave is
# split or deferred before it spawns instead of dying half-way.
ADMISSION_RE = re.compile(
    r"^\s*[-*]?\s*ADMISSION:\s*(granted|skipped)\b(?P<rest>.*)$", re.MULTILINE
)
# `SUITE-RUNNER: ci — <workflow>, <trigger>`, `SUITE-RUNNER: local — <reason>` or
# `SUITE-RUNNER: stub — <policy>; target <stub branch>`:
# which runner produces the orchestrator's suite verdicts (work-on.md Phase 2).
# CI is eligible only when it covers the required integrated suite; partial
# CI can retain its gates while `local` supplies the full verdict. Repository
# policy and required coverage determine the choice, recorded with a reason. Decided before the fan-out because `ci` and `stub` need
# the user's yes for the Phase 6 push and draft PR, and a yes asked after the
# agents have returned stalls the run with a merged tree waiting on a question.
SUITE_RUNNER_RE = re.compile(
    r"^\s*[-*]?\s*SUITE-RUNNER:\s*(ci|local|stub)\b(?P<rest>.*)$", re.MULTILINE
)
# `CENSUS: <path>:<line> — <owner>` or `CENSUS: none — <what was grepped>`: the
# Phase 3 grep for count/list pins a migration, table, counted-file or moved-
# file change shifts (a table-name census, a `(1..=N)` migration count, an
# inventory row count), each given an owner before the fan-out.
CENSUS_RE = re.compile(r"^\s*[-*]?\s*CENSUS:\s*(none|\S.*)$", re.MULTILINE)
# `CHECKLIST-IDS: <script> parsed <N>/<N> lines` or `CHECKLIST-IDS: none — no
# coverage script`: the draft PROMISE_CHECKLIST ran through the repo's own
# promise-coverage script, in its own id grammar, before the fan-out.
CHECKLIST_IDS_RE = re.compile(
    r"^\s*[-*]?\s*CHECKLIST-IDS:\s*(none|\S.*)$", re.MULTILINE
)
# A script that parses zero promise lines ("no promise lines found") means
# the checklist ids do not match the script's grammar — a Phase 3 defect to
# fix by re-deriving the ids, never a line to record and move past.
CHECKLIST_IDS_ZERO_RE = re.compile(r"\bparsed\s+0\s*/\s*\d+")

# `GATES: <file> — <N> gates; axes: <...>` or `GATES: none — <reason>`: the
# gate list read once from CI's workflows at Phase 2 (one command per feature
# axis CI compiles, plus its doc and lint gates). The contract commit, the
# first push and every fix round run this same list, never a narrower one.
GATES_RE = re.compile(
    r"^\s*[-*]?\s*GATES:\s*(none\s+—\s+\S.*|\S+\s+—\s+\d+\s+gates?\b.*)$",
    re.MULTILINE,
)
# `DOSSIER-ONLY-TERMS: <term>, <term>`: `### Definitions` terms deliberately
# kept out of the stubs' documentation.
DOSSIER_ONLY_RE = re.compile(r"^\s*[-*]?\s*DOSSIER-ONLY-TERMS:\s*(.+)$", re.MULTILINE)
DEFINITION_TERM_RE = re.compile(r"^\s*(?:[-*]\s*)?\*\*([^*]+?)\*\*", re.MULTILINE)


def definition_terms(contract: str) -> list[str]:
    """The terms `### Definitions` under `## Contract` defines: the leading
    `**term**` of each line in that subsection. Prose defined elsewhere in
    `## Contract` is not extracted; /plan puts definitions in the list."""
    m = re.search(
        r"^### Definitions[ \t]*\n(.*?)(?=^#{2,3} |\Z)", contract, re.M | re.S
    )
    if not m:
        return []
    return [t.strip().rstrip(":.,") for t in DEFINITION_TERM_RE.findall(m.group(1))]


def check_definitions(
    path: Path, text: str, build_log: str, root: Path, worktree: Path | None = None
) -> int:
    """Every `### Definitions` term appears in an owned file that exists (the
    stubs), or is logged `DOSSIER-ONLY-TERMS:`. An owned path that is a
    directory is walked — every file under it is a stub (#169). Owned paths
    resolve against `worktree` when given, else `root`: in local mode the
    dossier is validated from the main checkout while the stubs live only in
    the package worktree X (#157, #161). Returns 1 on a missing term."""
    secs = sections(text)
    terms = definition_terms(secs.get("## Contract", ""))
    if not terms:
        return 0
    skip = {
        t.strip().lower()
        for m in DOSSIER_ONLY_RE.finditer(build_log)
        # an em dash starts the line's reason; only the term list is terms
        for t in m.group(1).split("\u2014")[0].split(",")
    }
    stub_root = worktree if worktree is not None else root
    stub_text = ""
    for _, paths in parse_packages(secs.get("## Work packages", "")):
        for rel in paths:
            target = stub_root / rel.strip("`")
            files = (
                sorted(p for p in target.rglob("*") if p.is_file())
                if target.is_dir()
                else ([target] if target.is_file() else [])
            )
            for f in files:
                stub_text += (
                    f.read_text(encoding="utf-8", errors="replace").lower() + "\n"
                )
    rc = 0
    for term in terms:
        if term.lower() in skip or term.lower() in stub_text:
            continue
        print(
            f"DEFECT  {path.name}: Definitions term {term!r} is in no owned stub "
            f"file. Place it in the documentation of the members that use it, or "
            f"log DOSSIER-ONLY-TERMS: {term}"
        )
        rc = 1
    if not rc:
        print(
            f"ok      {path.name}: {len(terms)} Definitions terms placed or dossier-only"
        )
    return rc


PROSE_ENV_RE = re.compile(r"\b[A-Z][A-Z0-9]*_[A-Z0-9_]*[A-Z0-9]\b")
PROSE_ENV_CUE_RE = re.compile(
    r"\b(env|environment|variables?|vars?|secrets?|reads?|set)\b", re.I
)
PROSE_ROUTE_RE = re.compile(
    r"\b(?:GET|POST|PUT|PATCH|DELETE|mounts?|mounted at|routes?|endpoints?)\s+`?(/[\w.{}:/-]*\w)"
)
PROSE_BIN_RE = re.compile(r"\bbin/([\w.-]+)|`([\w.-]+)`\s+binary\b")


def _command_names() -> set[str]:
    """Slash-command names of this plugin (commands/*.md stems), never routes."""
    base = Path(__file__).resolve().parent.parent
    return {
        f.stem
        for d in ("commands", "claude/commands")
        for f in (base / d).glob("*.md")
    }


def prose_promises(contract: str) -> list[tuple[str, str]]:
    """The (token, sentence) promises in `## Contract` prose, outside fenced
    code blocks and table rows: an ALL_CAPS_UNDERSCORE env var (the sentence
    also has a cue word: env, variable, secret, reads, set), a route after
    GET/POST/PUT/PATCH/DELETE/mount/route/endpoint, or a binary (`bin/x`,
    "the `x` binary"). Plugin command names (/work-on) are never routes."""
    prose = re.sub(r"^```.*?^```[ \t]*$", "", contract, flags=re.M | re.S)
    commands = _command_names()
    found: list[tuple[str, str]] = []
    for line in prose.splitlines():
        if line.lstrip().startswith("|"):
            continue
        for sentence in re.split(r"(?<=[.!?])\s+", line):
            tokens = []
            if PROSE_ENV_CUE_RE.search(sentence):
                tokens += PROSE_ENV_RE.findall(sentence)
            tokens += [
                r for r in PROSE_ROUTE_RE.findall(sentence) if r.lstrip("/") not in commands
            ]
            tokens += [
                re.sub(r"\.\w+$", "", a or b)
                for a, b in PROSE_BIN_RE.findall(sentence)
            ]
            found += [(t, sentence.strip()) for t in tokens]
    return found


def check_prose_promises(path: Path, text: str) -> int:
    """Every prose promise in `## Contract` names a token that some
    `## Acceptance criteria` item also contains; one with none is a
    pre-fan-out WARNING (#337), never blocking. Prints `PROSE-PROMISES: N, M without a criterion`."""
    secs = sections(text)
    promises = prose_promises(secs.get("## Contract", ""))
    if not promises:
        return 0
    criteria = secs.get("## Acceptance criteria", "")
    seen: set[str] = set()
    missing = []
    for token, sentence in promises:
        if token in seen:
            continue
        seen.add(token)
        if token not in criteria:
            missing.append((token, sentence))
    print(f"PROSE-PROMISES: {len(seen)}, {len(missing)} without a criterion")
    for token, sentence in missing:
        print(
            f"WARNING {path.name}: ## Contract prose promises {token!r} "
            f"({sentence[:80]!r}) but no acceptance criterion names it. "
            f"Add a criterion that names {token!r}, or state why none is needed."
        )
    return 0


PRE_FANOUT_LINES = (
    (
        "PAYLOAD-LINT",
        PAYLOAD_LINT_RE,
        "Every payload is written to a file and linted before the fan-out "
        "message; a payload that was never a file was never linted.",
        ("PAYLOAD-LINT: <N> payloads, <N> defects fixed — <agent ids>",),
    ),
    (
        "ADMISSION",
        ADMISSION_RE,
        "The wave acquires its slots from the machine-wide admission ledger "
        "before the spawn message; a refusal splits it, a deferral waits.",
        (
            "ADMISSION: granted <N> slots — <provider>/<model>, cap <C>, ceiling <K>",
            "ADMISSION: skipped — <reason>",
        ),
    ),
    (
        "SUITE-RUNNER",
        SUITE_RUNNER_RE,
        "The suite runner is decided by required coverage at the contract "
        "commit — complete CI, local with a reason, or stub with the "
        "deferral; partial CI keeps its gates.",
        (
            "SUITE-RUNNER: ci — <workflow>, <trigger>; push and draft PR: yes",
            "SUITE-RUNNER: local — <reason>",
            "SUITE-RUNNER: stub — <policy source>; target <stub branch>; "
            "integrated verdict deferred to the /stub run; push and draft PR: yes",
        ),
    ),
    (
        "GATES",
        GATES_RE,
        "The gate list (one command per CI feature axis, plus CI's doc and "
        "lint gates) is read from CI once and run at every commit class.",
        (
            "GATES: <gates file> — <N> gates; axes: <default, ...>",
            "GATES: none — <reason>",
        ),
    ),
    (
        "CENSUS",
        CENSUS_RE,
        "The contract's migration, table, counted-file or moved-file change "
        "shifts a census pin somewhere in the tree; each pin gets an owner "
        "before the fan-out, or the grep that found none is named.",
        (
            "CENSUS: <path>:<line> — <owner package or criterion>",
            "CENSUS: none — <what was grepped>",
        ),
    ),
    (
        "CHECKLIST-IDS",
        CHECKLIST_IDS_RE,
        "The draft PROMISE_CHECKLIST runs through the repo's own promise-"
        "coverage script, in its id grammar, before the fan-out.",
        (
            "CHECKLIST-IDS: <script> parsed <N>/<N> lines",
            "CHECKLIST-IDS: none — no coverage script",
        ),
    ),
)


def front_matter_worktree(text: str, root: Path) -> Path | None:
    """The dossier's `worktree:` front-matter field, resolved against `root`
    (it is written relative to the main checkout, e.g. `../repo-W-014`), or
    None. In local mode the stubs live in that worktree, not in the checkout
    the dossier is validated from."""
    try:
        fm, _, _ = split_front_matter(text)
    except ValueError:
        return None
    value = fm.get("worktree")
    if not isinstance(value, str) or not value.strip():
        return None
    wt = (root / value.strip()).resolve()
    return wt if wt.is_dir() else None


def check_receipts(path: Path, text: str, root: Path, worktree: Path | None) -> int:
    """The pre-fan-out facts that live in front matter:

    * ``hooks: none | <hook> — <policy>`` — the hook policy, decided at the contract commit.
    * ``contract_review: <sha> | skipped — <reason>`` — the contract commit the review read. A SHA
      that is not the current contract commit (the last commit touching a contract file) is a stale
      review: the contract moved after it was read. ``skipped`` stays allowed with its reason.

    Write them with ``dossier_edit.py set DOSSIER hooks=... contract_review=<sha>``. Returns 0 or 1."""
    try:
        fm, _, _ = split_front_matter(text)
    except ValueError:
        fm = {}
    rc = 0
    if not str(fm.get("hooks") or "").strip():
        print(f"DEFECT  {path.name}: no `hooks:` front matter field. The hook policy is decided at the "
              f"contract commit, never at an implementer's commit step. Record `hooks: none` or "
              f"`hooks: <hook> — <policy>` (dossier_edit.py set).")
        rc = 1
    else:
        print(f"ok      {path.name}: hooks recorded ({fm['hooks']})")
    review = str(fm.get("contract_review") or "").strip()
    if not review:
        print(f"DEFECT  {path.name}: no `contract_review:` front matter field. The contract review runs on "
              f"every build and has no size gate. Record the contract commit SHA it read, or "
              f"`contract_review: skipped — <reason>` (dossier_edit.py set).")
        return 1
    if review.startswith("skipped"):
        if not re.match(r"skipped\s*[—–-]+\s*\S", review):
            print(f"DEFECT  {path.name}: `contract_review: skipped` names no reason; write "
                  f"`skipped — <reason>`.")
            return 1
        print(f"ok      {path.name}: contract review skipped ({review})")
        return rc
    cwd = worktree or root
    try:
        reviewed = git_out(cwd, "rev-parse", "--verify", "--quiet", f"{review}^{{commit}}").strip()
    except RuntimeError:
        print(f"DEFECT  {path.name}: contract_review {review!r} is not a commit in {cwd}.")
        return 1
    current = ""
    paths = contract_paths(text.split("## Contract", 1)[1].split("\n## ", 1)[0]) if "## Contract" in text else []
    if paths:
        try:
            current = git_out(cwd, "log", "-1", "--format=%H", "--", *paths).strip()
        except RuntimeError:
            current = ""
    if not current and str(fm.get("contract_commit") or "").strip():
        try:
            current = git_out(cwd, "rev-parse", "--verify", "--quiet", f"{fm['contract_commit']}^{{commit}}").strip()
        except RuntimeError:
            current = ""
    if not current:
        print(f"DEFECT  {path.name}: cannot tell which commit is the contract commit (no contract file has "
              f"a commit in {cwd}, and no `contract_commit:` field); commit the contract first.")
        return 1
    if reviewed != current:
        print(f"DEFECT  {path.name}: stale contract review: it read {reviewed[:8]} but the contract commit "
              f"is {current[:8]}. The contract changed after the review; run the review again and "
              f"record the new SHA.")
        return 1
    print(f"ok      {path.name}: contract review read the current contract commit ({reviewed[:8]})")
    return rc


def check_pre_fanout(
    path: Path, root: Path | None = None, worktree: Path | None = None
) -> int:
    """Check the obligations that must hold before /work-on Phase 4 fans out.

    Two facts in front matter (``hooks:`` and ``contract_review:``, see
    ``check_receipts``) and six lines in ``## Build log``, each recording a
    decision the orchestrator otherwise makes silently — or forgets, which
    reads the same:

    * ``PAYLOAD-LINT: <N> payloads, <N> defects ...`` — every payload went
      through ``check_payload.py`` as a file.
    * ``ADMISSION: granted ... <provider>/<model> ... | skipped — <reason>`` —
      the wave took its slots from the admission ledger. A ``granted`` line is
      refused while that ledger holds an unexpired reset deadline for the
      provider and model it names.
    * ``SUITE-RUNNER: ci — ... | local — <reason> | stub — <policy>; target
      <stub branch>`` — which runner produces the
      suite verdicts from Phase 6 on, decided once at the contract commit.
    * ``GATES: <file> — <N> gates; axes: ... | none — <reason>`` — the CI
      gate list, run at every commit class.
    * ``CENSUS: <path>:<line> — <owner> | none — <what was grepped>`` — every
      count or list pin the contract shifts has an owner before the fan-out.
    * ``CHECKLIST-IDS: <script> parsed <N>/<N> lines | none — no coverage
      script`` — the draft checklist parses in the repo's own promise-line
      grammar; a ``parsed 0/`` line is refused, not recorded.

    Also refuses a ``## Contract`` prose promise (env var, route, binary) that
    no ``## Acceptance criteria`` item names (a WARNING, ``PROSE-PROMISES:``), and a ``### Definitions`` term (under ``## Contract``) found in no
    owned stub file and not listed in ``DOSSIER-ONLY-TERMS:``.

    Returns 0 when every obligation is recorded, 1 otherwise.
    """
    text = path.read_text(encoding="utf-8")
    if root is None:
        root = path.resolve().parents[2]
    worktree = worktree or front_matter_worktree(text, root)
    body = text.split("## Build log", 1)
    if len(body) != 2:
        print(f"DEFECT  {path.name}: no '## Build log' section to check")
        return 1

    rc = 0
    for token, pattern, why, shapes in PRE_FANOUT_LINES:
        match = pattern.search(body[1])
        if not match:
            shape_lines = "\n".join(f"          {s}" for s in shapes)
            print(
                f"DEFECT  {path.name}: no {token}: line in ## Build log.\n"
                f"        {why}\n"
                f"        Record one of:\n{shape_lines}"
            )
            rc = 1
            continue
        print(f"ok      {path.name}: {token} recorded ({match.group(1)})")

    granted = ADMISSION_RE.search(body[1])
    key = (
        re.search(r"([\w.-]+)/([\w.-]+)", granted.group("rest"))
        if granted and granted.group(1) == "granted"
        else None
    )
    if key:
        try:
            sys.path.insert(0, str(Path(__file__).resolve().parent))
            import spawn_admission

            pending = spawn_admission.pending_deferral(key.group(1), key.group(2))
        except ImportError:
            pending = None
            print(
                "note    spawn_admission.py is not beside this script; the ledger was not consulted"
            )
        if pending:
            deadline, text = pending
            print(
                f"DEFECT  {path.name}: ADMISSION granted for {key.group(1)}/{key.group(2)}, "
                f"but the admission ledger holds a reset at {spawn_admission._fmt(deadline)} "
                f"— {text[:80]!r}. Wait it out; never spawn into a closed window."
            )
            rc = 1

    rc |= check_receipts(path, text, root, worktree)
    rc |= check_definitions(path, text, body[1], root, worktree)
    rc |= check_prose_promises(path, text)

    checklist_ids = CHECKLIST_IDS_RE.search(body[1])
    if checklist_ids and CHECKLIST_IDS_ZERO_RE.search(checklist_ids.group(1)):
        print(
            f"DEFECT  {path.name}: CHECKLIST-IDS reports 'parsed 0/...' — the "
            f"checklist ids don't match the coverage script's promise-line "
            f"grammar. This is a Phase 3 defect: read the script's regex and "
            f"re-derive the ids in it, never a Phase 5 manual-diff fallback."
        )
        rc = 1

    try:
        fm_vr, _, _ = split_front_matter(text)
    except ValueError:
        fm_vr = {}
    for problem in visual_review_problems(fm_vr, body[1], root, worktree):
        print(f"DEFECT  {path.name}: {problem}")
        rc = 1
    return rc


RECEIPTS_FM = (
    "---\nid: W-9\nstatus: building\nhooks: none\n"
    "contract_review: skipped — two-member contract, reviewed by hand\n---\n\n"
)


def selftest_pre_fanout_local() -> bool:
    """#157/#161/#169/#165: directory owned paths walked, stubs resolved in
    the live worktree (flag and front matter), the em-dash reason, RULES.md."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        full = (
            "## Build log\n"
            "- PAYLOAD-LINT: 5 payloads, 1 defect fixed — P1 P2 UT-A UT-B IT\n"
            "- ADMISSION: granted 5 slots — selftest-provider/selftest-model, cap 8, ceiling 5\n"
            "- SUITE-RUNNER: local — no CI in this repository\n"
            "- GATES: none — no CI workflow builds anything\n"
            "- CENSUS: none — grepped for table censuses and migration counts\n"
            "- CHECKLIST-IDS: none — no coverage script\n"
        )
        dossier = (
            "## Contract\n\n### Definitions\n\n"
            "- **window start** — the first instant a flush covers.\n"
            "- **track rows** — the rows of one track, oldest first.\n\n"
            "```python\ndef flush(): ...\n```\n\n"
            "## Work packages\n\n| Package | Owned paths |\n|---|---|\n"
            "| P1 | src/flush.py |\n\n"
        )
        ok = True
        # #169: a package that owns a directory has every file under it walked.
        (root / "Engine").mkdir()
        (root / "Engine" / "PlanValidator.cs").write_text(
            "/// <summary>The plan window is the first instant a plan covers.</summary>\n",
            encoding="utf-8",
        )
        dir_dossier = dossier.replace(
            "| P1 | src/flush.py |", "| P1 | Engine/ |"
        ).replace(
            "### Definitions\n\n"
            "- **window start** — the first instant a flush covers.\n"
            "- **track rows** — the rows of one track, oldest first.\n\n",
            "### Definitions\n\n"
            "- **plan window** — the first instant a plan covers.\n"
            "- **track rows** — the rows of one track, oldest first.\n\n",
        )
        (root / "def-dir.md").write_text(RECEIPTS_FM + dir_dossier + full, encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()) as dirbuf:
            dir_rc = check_pre_fanout(root / "def-dir.md", root)
        ok &= (
            dir_rc == 1
            and "'track rows'" in dirbuf.getvalue()
            and "'plan window'" not in dirbuf.getvalue()
        )

        # #157/#161: local mode — the dossier is validated from the main
        # checkout, the stubs live only in the worktree X.
        x = Path(tmp) / "X-stubs"
        (x / "src").mkdir(parents=True)
        (x / "src" / "flush.py").write_text(
            '"""Flush.\n\nThe window start and the track rows are documented here."""\n',
            encoding="utf-8",
        )
        (root / "src").mkdir(exist_ok=True)
        (root / "src" / "flush.py").write_text(
            '"""Flush — the stubs are not here in local mode."""\n', encoding="utf-8"
        )
        (root / "def-wt.md").write_text(RECEIPTS_FM + dossier + full, encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()) as wbuf:
            wt_rc = check_pre_fanout(root / "def-wt.md", root, x)
        ok &= wt_rc == 0 and "is in no owned stub file" not in wbuf.getvalue()
        # the front-matter `worktree:` field resolves the same way when the flag is absent
        (root / "def-fmwt.md").write_text(
            RECEIPTS_FM.replace("---\nid:", "---\nworktree: X-stubs\nid:", 1) + dossier + full, encoding="utf-8"
        )
        with contextlib.redirect_stdout(io.StringIO()) as fbuf:
            fm_rc = check_pre_fanout(root / "def-fmwt.md", root)
        ok &= fm_rc == 0 and "is in no owned stub file" not in fbuf.getvalue()
        # without the worktree, in local mode, every term is missing
        with contextlib.redirect_stdout(io.StringIO()) as nobuf:
            no_rc = check_pre_fanout(root / "def-wt.md", root)
        ok &= no_rc == 1

        # #169: a DOSSIER-ONLY-TERMS line may carry its reason after an em dash.
        (root / "def-dash.md").write_text(
            RECEIPTS_FM + dossier
            + full
            + "- DOSSIER-ONLY-TERMS: Track Rows — kept in the dossier for the operators\n",
            encoding="utf-8",
        )
        ok &= check_pre_fanout(root / "def-dash.md", root, x) == 0

        # #165: docs/adr/RULES.md is a rules file, not a fence-less ADR.
        adr = root / "docs" / "adr"
        adr.mkdir(parents=True)
        (adr / "RULES.md").write_text(
            RECEIPTS_FM + "# Rules\n\nNo front matter: a rules file.\n", encoding="utf-8"
        )
        (adr / "index.md").write_text(RECEIPTS_FM + "# Index\n", encoding="utf-8")
        (adr / "0007-flush.md").write_text(
            RECEIPTS_FM + "---\nid: ADR-0007\nstatus: accepted\ntitle: Coalesce flush calls\n---\n\nBody.\n",
            encoding="utf-8",
        )
        ok &= [p.name for p in adr_targets(adr)] == ["0007-flush.md"]
        print(
            f"{'PASS' if ok else 'FAIL'} — directory owned paths walked, local-mode stubs resolved in the live worktree, the em-dash reason, RULES.md skipped"
        )
        return bool(ok)


def selftest_visual_review() -> bool:
    """The visual-review gate: gated by the `visual_review:` field the Phase 3 step sets, never otherwise."""
    import copy

    def node(i: str, kind: str) -> dict:
        return {"id": i, "label": i, "kind": kind, "status": "added",
                "evidence": [{"tier": "inferred", "note": "selftest"}], "label_source": "llm"}

    def graph(view: str, kind: str, n: int) -> dict:
        return {"schema_version": "1.0.0", "view": view, "title": f"{view} selftest",
                "meta": {"repo": "t", "commit": "0", "generated_by": "agent", "tools": []},
                "nodes": [node(f"n{i}", kind) for i in range(n)], "edges": []}

    good = {"l0.json": graph("L0", "feature", 3), "l1.json": graph("L1", "step", 4),
            "l2.json": graph("L2", "module", 5)}
    approved = "- VISUAL-REVIEW: approved — L0 3, L1 4, L2 5 (dashboard link)\n"
    new_fm = "---\nid: W-9\nstatus: building\ncreated: 2026-11-12\nvisual_review: required\n---\n\n"
    unmarked_fm = "---\nid: W-9\nstatus: building\ncreated: 2026-11-12\n---\n\n"
    old_fm = "---\nid: W-9\nstatus: building\ncreated: 2026-08-10\n---\n\n"
    optin_fm = "---\nid: W-9\nstatus: building\ncreated: 2026-08-10\nvisual_review: required\n---\n\n"

    def run(fm: str, log: str, files: dict | None, mutate=None) -> list[str]:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".discovery" / "dossiers").mkdir(parents=True)
            if files is not None:
                out = root / ".discovery" / "visual-review" / "W-9"
                out.mkdir(parents=True)
                for name, g in copy.deepcopy(files).items():
                    if mutate:
                        mutate(name, g)
                    (out / name).write_text(json.dumps(g), encoding="utf-8")
            fmd, _, _ = split_front_matter(fm + "## Build log\n")
            return visual_review_problems(fmd, log, root)

    def dup(name: str, g: dict) -> None:
        if name == "l1.json":
            g["nodes"][1]["id"] = g["nodes"][0]["id"]

    def no_evidence(name: str, g: dict) -> None:
        if name == "l0.json":
            g["nodes"][0]["evidence"] = []

    def wrong_view(name: str, g: dict) -> None:
        if name == "l2.json":
            g["view"] = "L1"

    cases = [
        ("old dossier, no line: not flagged", run(old_fm, "## Build log\n", None), False),
        ("new dossier the step never marked: not flagged", run(unmarked_fm, "no line\n", None), False),
        ("new dossier, no line: flagged", run(new_fm, "no line\n", good), True),
        ("new dossier, line and files: clean", run(new_fm, approved, good), False),
        ("opt-in field on an old dossier: flagged", run(optin_fm, "no line\n", None), True),
        ("changes requested last: flagged", run(new_fm, "- VISUAL-REVIEW: changes requested — n1 n2\n", good), True),
        ("changes requested, then approved: clean",
         run(new_fm, "- VISUAL-REVIEW: changes requested — n1\n" + approved, good), False),
        ("malformed line: flagged", run(new_fm, "- VISUAL-REVIEW: fine\n", good), True),
        ("folder missing: flagged", run(new_fm, approved, None), True),
        ("l2.json missing: flagged", run(new_fm, approved, {k: v for k, v in good.items() if k != "l2.json"}), True),
        ("counts differ from the line: flagged",
         run(new_fm, "- VISUAL-REVIEW: approved — L0 3, L1 4, L2 6\n", good), True),
        ("duplicate id in a file: flagged", run(new_fm, approved, good, dup), True),
        ("element without evidence: flagged", run(new_fm, approved, good, no_evidence), True),
        ("file with the wrong view: flagged", run(new_fm, approved, good, wrong_view), True),
    ]
    ok = True
    for name, problems, expect in cases:
        passed = bool(problems) == expect
        ok &= passed
        print(f"{'ok  ' if passed else 'FAIL'}    visual review: {name}" + (f" ({problems[0][:70]})" if problems else ""))

    # the same gate through the two entry points
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / ".discovery" / "dossiers").mkdir(parents=True)
        d = root / ".discovery" / "dossiers" / "W-9.md"
        d.write_text(new_fm + "## Build log\n- hooks: none\n", encoding="utf-8")
        rep = Report()
        check_visual_review("W-9", d, {"id": "W-9", "created": "2026-11-12", "visual_review": "required"}, "- x\n", "review", rep)
        ok &= bool(rep.defects)
        rep = Report()
        check_visual_review("W-9", d, {"id": "W-9", "created": "2026-11-12", "visual_review": "required"}, "- x\n", "building", rep)
        ok &= not rep.defects  # mid-build: the pre-fan-out check owns that state
        rc = check_pre_fanout(d, root)
        ok &= rc == 1
        out = root / ".discovery" / "visual-review" / "W-9"
        out.mkdir(parents=True)
        for name, g in good.items():
            (out / name).write_text(json.dumps(g), encoding="utf-8")
    print(f"{'PASS' if ok else 'FAIL'} — visual-review gate")
    return ok


def selftest_pre_fanout() -> bool:
    """The pre-fan-out check refuses each missing line and passes a full log."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        full = (
            "## Build log\n"
            "- PAYLOAD-LINT: 5 payloads, 1 defect fixed — P1 P2 UT-A UT-B IT\n"
            "- ADMISSION: granted 5 slots — selftest-provider/selftest-model, cap 8, ceiling 5\n"
            "- SUITE-RUNNER: ci — .github/workflows/ci.yml, on push; push and draft PR: yes\n"
            "- GATES: .discovery/analysis/gates-W-1.txt — 3 gates; axes: default, fault-inject\n"
            "- CENSUS: none — grepped for PROTECTED_TABLES, EXPECTED_TABLES, migration counts\n"
            "- CHECKLIST-IDS: scripts/check_promise_coverage.py parsed 8/8 lines\n"
            # Claude Code spawn, workflow and return lines carry agent and run IDs
            # (phase-4.md, "resume instead of re-spawn"); none of them is a pre-fan-out
            # obligation, and none may be mistaken for one.
            "- SPAWNED: P1 (agent a1b2c3d4) — implementer, wave 1\n"
            "- WORKFLOW: phase4-wave wave1 run run-77 — P1, UT-A\n"
            "- P1 (agent a1b2c3d4) RETURNED: 3f2a1b9 on feat/x-p1, lib.rs +2/-2 only, 0 unimplemented left, hash stable, tree exact\n"
        )
        ok = True
        (root / "full.md").write_text(RECEIPTS_FM + full, encoding="utf-8")
        ok &= check_pre_fanout(root / "full.md") == 0
        for token in (
            "PAYLOAD-LINT",
            "ADMISSION",
            "SUITE-RUNNER",
            "GATES",
            "CENSUS",
            "CHECKLIST-IDS",
        ):
            partial = "\n".join(
                line for line in full.splitlines() if not line.startswith(f"- {token}:")
            )
            (root / f"no-{token}.md").write_text(RECEIPTS_FM + partial + "\n", encoding="utf-8")
            ok &= check_pre_fanout(root / f"no-{token}.md") == 1
        (root / "none.md").write_text(
            RECEIPTS_FM + "## Build log\n- PAYLOAD-LINT: 2 payloads, 0 defects — P1 UT\n- ADMISSION: skipped — one spawn\n- SUITE-RUNNER: local — no CI in this repository\n- GATES: none — no CI workflow builds anything\n- CENSUS: none — grepped for table censuses and migration counts\n- CHECKLIST-IDS: none — no coverage script\n",
            encoding="utf-8",
        )
        ok &= check_pre_fanout(root / "none.md") == 0

        # A CHECKLIST-IDS line that parsed zero promise lines is refused, not
        # recorded — it means the checklist ids do not match the script's
        # grammar (a Phase 3 defect), never a fallback to record and move past.
        zero = full.replace(
            "- CHECKLIST-IDS: scripts/check_promise_coverage.py parsed 8/8 lines\n",
            "- CHECKLIST-IDS: scripts/check_promise_coverage.py parsed 0/8 lines\n",
        )
        (root / "zero.md").write_text(RECEIPTS_FM + zero, encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()) as zbuf:
            zero_rc = check_pre_fanout(root / "zero.md")
        ok &= zero_rc == 1 and "parsed 0/" in zbuf.getvalue()

        # A `### Definitions` term must reach an owned stub file or be logged
        # dossier-only; a log with no Definitions subsection is untouched.
        (root / "src").mkdir()
        (root / "src" / "flush.py").write_text(
            '"""Flush.\n\nThe window start is the first instant a flush covers."""\n',
            encoding="utf-8",
        )
        dossier = (
            "## Contract\n\n### Definitions\n\n"
            "- **window start** — the first instant a flush covers.\n"
            "- **track rows** — the rows of one track, oldest first.\n\n"
            "```python\ndef flush(): ...\n```\n\n"
            "## Work packages\n\n| Package | Owned paths |\n|---|---|\n"
            "| P1 | src/flush.py |\n\n"
        )
        (root / "def-missing.md").write_text(RECEIPTS_FM + dossier + full, encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()) as dbuf:
            miss = check_pre_fanout(root / "def-missing.md", root)
        ok &= (
            miss == 1
            and "'track rows'" in dbuf.getvalue()
            and "'window start'" not in dbuf.getvalue()
        )
        (root / "def-skipped.md").write_text(
            RECEIPTS_FM + dossier + full + "- DOSSIER-ONLY-TERMS: Track Rows\n", encoding="utf-8"
        )
        ok &= check_pre_fanout(root / "def-skipped.md", root) == 0
        (root / "src" / "flush.py").write_text(
            '"""Flush: window start; track rows are one track\'s rows."""\n',
            encoding="utf-8",
        )
        (root / "def-placed.md").write_text(RECEIPTS_FM + dossier + full, encoding="utf-8")
        ok &= check_pre_fanout(root / "def-placed.md", root) == 0
        # #337: a prose promise (env var, route, binary) needs a criterion.
        pp = (
            "## Contract\n\nThe runner reads ASD_RUNNER_MAX_LIVE and mounts `/api/runs`.\n\n"
            "```sh\nexport HIDDEN_CODE_VAR=1\n```\n\n"
            "## Acceptance criteria\n\n1. {crit}\n\n"
        )
        (root / "pp-none.md").write_text(
            RECEIPTS_FM + pp.format(crit="It starts.") + full, encoding="utf-8"
        )
        with contextlib.redirect_stdout(io.StringIO()) as pbuf:
            pp_rc = check_pre_fanout(root / "pp-none.md")
        out = pbuf.getvalue()
        ok &= (
            pp_rc == 0
            and "PROSE-PROMISES: 2, 2 without" in out
            and "WARNING" in out
            and "'ASD_RUNNER_MAX_LIVE'" in out
            and "HIDDEN_CODE_VAR" not in out
        )
        (root / "pp-ok.md").write_text(
            RECEIPTS_FM + pp.format(crit="ASD_RUNNER_MAX_LIVE=2 caps spawns; GET `/api/runs` lists.")
            + full,
            encoding="utf-8",
        )
        ok &= check_pre_fanout(root / "pp-ok.md") == 0
        # A GET/POST route without backticks is a promise too.
        (root / "pp-route.md").write_text(
            RECEIPTS_FM + "## Contract\n\nThe server answers GET /health. Run /work-on next. "
            "See TIMED_OUT.\n\n| `/patches` | SOME_KEY reads |\n\n"
            "## Acceptance criteria\n\n1. It starts.\n\n" + full,
            encoding="utf-8",
        )
        with contextlib.redirect_stdout(io.StringIO()) as rbuf:
            route_rc = check_pre_fanout(root / "pp-route.md")
        ok &= (
            route_rc == 0
            and "PROSE-PROMISES: 1, 1 without" in rbuf.getvalue()
            and "/health" in rbuf.getvalue()
        )
        # A malformed GATES line (no gate count) is refused like a missing one.
        bad = full.replace("— 3 gates; axes: default, fault-inject", "— several")
        (root / "gates-bad.md").write_text(RECEIPTS_FM + bad, encoding="utf-8")
        ok &= check_pre_fanout(root / "gates-bad.md", root) == 1

        # A granted line is refused while the admission ledger defers that key.
        saved = os.environ.get("ASD_ADMISSION_DIR")
        os.environ["ASD_ADMISSION_DIR"] = str(root / "admission")
        try:
            sys.path.insert(0, str(Path(__file__).resolve().parent))
            import spawn_admission

            with contextlib.redirect_stdout(io.StringIO()):
                spawn_admission.main(
                    [
                        "record-error",
                        "--provider",
                        "selftest-provider",
                        "--model",
                        "selftest-model",
                        "Limit Exhausted. Your limit will reset in 30 minutes",
                    ]
                )
            with contextlib.redirect_stdout(io.StringIO()) as buf:
                deferred = check_pre_fanout(root / "full.md")
            ok &= deferred == 1 and "admission ledger holds a reset" in buf.getvalue()
        except ImportError:
            print(
                "SKIP  spawn_admission.py not importable; the ledger refusal was not run"
            )
        finally:
            if saved is None:
                os.environ.pop("ASD_ADMISSION_DIR", None)
            else:
                os.environ["ASD_ADMISSION_DIR"] = saved
        print(
            f"\n{'PASS' if ok else 'FAIL'} — pre-fan-out lines each refused when missing, accepted when present, and a granted admission refused under a live deferral"
        )
        return bool(ok)


# --------------------------------------------------------------------------
# the two modes of .discovery/  (references/formats/README.md § "Two modes")
# --------------------------------------------------------------------------

# One probe per path that stays local in committed mode. `git check-ignore`
# answers from the patterns alone, so the probe file never has to exist.
LOCAL_ONLY_PROBES = (
    (".discovery/analysis/", ".discovery/analysis/open-work.html"),
    (".discovery/pr-draft-*.md", ".discovery/pr-draft-probe.md"),
    (".discovery/deferred-ledger.md", ".discovery/deferred-ledger.md"),
)
DOSSIER_PROBE = ".discovery/dossiers/probe.md"


def _ignored(root: Path, path: str) -> bool:
    p = subprocess.run(
        ["git", "-C", str(root), "check-ignore", "-q", path], capture_output=True
    )
    return p.returncode == 0


def discovery_mode(root: Path) -> tuple[str, list[str]]:
    """`local`, `committed`, `conflict` or `no-git`, plus the notes to print.

    The definition is `git ls-files -- .discovery`: any tracked file means
    committed. Two checks ride on it. A tracked `.discovery/` whose
    `.gitignore` also ignores a new dossier is a repository in two minds
    (`conflict`): the dossier `/plan` writes would vanish from its PR. In
    committed mode the three local-only paths must stay ignored, and each
    one that is not is a WARNING. In local mode a missing `.discovery/`
    gitignore line is a WARNING — `/plan`'s gitignore guarantee adds it.
    """
    p = subprocess.run(
        ["git", "-C", str(root), "ls-files", "--", ".discovery"],
        capture_output=True,
        text=True,
    )
    if p.returncode != 0:
        return "no-git", [f"UNUSABLE {p.stderr.strip() or 'git ls-files failed'}"]
    tracked = [line for line in p.stdout.splitlines() if line.strip()]
    notes: list[str] = []
    if not tracked:
        if not _ignored(root, DOSSIER_PROBE):
            notes.append(
                "WARNING .discovery/ is not gitignored; /plan adds the line "
                "(the gitignore guarantee)"
            )
        return "local", notes
    if _ignored(root, DOSSIER_PROBE):
        return "conflict", [
            f"DEFECT  {len(tracked)} tracked file(s) under .discovery/ while "
            ".gitignore ignores a new dossier — a repository in two minds. "
            "Show the user the .gitignore line; never pick a side."
        ]
    for pattern, probe in LOCAL_ONLY_PROBES:
        if not _ignored(root, probe):
            notes.append(
                f"WARNING {pattern} is not gitignored; it stays local in committed "
                "mode — add the pattern (in the plan worktree, so the PR carries it)"
            )
    notes.insert(0, f"ok      {len(tracked)} tracked file(s) under .discovery/")
    return "committed", notes


def run_mode(root: Path) -> int:
    mode, notes = discovery_mode(root)
    print(f"mode: {mode}")
    for note in notes:
        print(note)
    return {"local": 0, "committed": 0, "conflict": 1}.get(mode, 2)


def selftest_mode() -> bool:
    """Each of the three answers on a fixture repository."""
    print("\n--- selftest: the two modes of .discovery/ ---")
    if not shutil.which("git"):
        print("SKIP  git not found; the mode scenario was not run")
        return True
    ok = True
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "repo"
        (root / ".discovery" / "dossiers").mkdir(parents=True)
        try:
            git_out(root, "init", "-q", "-b", "main")
            git_out(root, "config", "user.email", "selftest@example.invalid")
            git_out(root, "config", "user.name", "selftest")
        except RuntimeError as exc:
            print(f"FAIL  fixture repository could not be built: {exc}")
            return False
        (root / ".discovery" / "dossiers" / "W-001-a.md").write_text(
            "---\nid: W-001\n---\n"
        )

        mode, notes = discovery_mode(root)
        case = mode == "local" and any("not gitignored" in n for n in notes)
        ok &= case
        print(
            f"{'PASS' if case else 'FAIL'} — untracked, no .gitignore: local with a warning"
        )

        (root / ".gitignore").write_text(".discovery/\n")
        mode, notes = discovery_mode(root)
        case = mode == "local" and not notes
        ok &= case
        print(f"{'PASS' if case else 'FAIL'} — untracked and ignored: local, clean")

        (root / ".gitignore").unlink()
        git_out(root, "add", "-A")
        git_out(root, "commit", "-q", "-m", "track dossiers")
        mode, notes = discovery_mode(root)
        case = mode == "committed" and sum("not gitignored" in n for n in notes) == 3
        ok &= case
        print(
            f"{'PASS' if case else 'FAIL'} — tracked, no local-only patterns: committed with 3 warnings"
        )

        (root / ".gitignore").write_text(
            ".discovery/analysis/\n.discovery/pr-draft-*.md\n.discovery/deferred-ledger.md\n"
        )
        mode, notes = discovery_mode(root)
        case = mode == "committed" and not any("WARNING" in n for n in notes)
        ok &= case
        print(
            f"{'PASS' if case else 'FAIL'} — tracked with the three patterns: committed, clean"
        )

        (root / ".gitignore").write_text(".discovery/\n")
        mode, notes = discovery_mode(root)
        case = mode == "conflict" and run_mode(root) == 1
        ok &= case
        print(f"{'PASS' if case else 'FAIL'} — tracked yet ignored: conflict, exit 1")

        mode, _ = discovery_mode(Path(tmp))
        case = mode == "no-git"
        ok &= case
        print(f"{'PASS' if case else 'FAIL'} — outside a repository: no-git")
    return bool(ok)


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
        help="renumber this branch's colliding ADR, LRN and dossier ids after "
        "the sync merge; requires --base",
    )
    ap.add_argument(
        "--base",
        metavar="REF",
        help="the ref this branch syncs against, e.g. origin/development "
        "(used by --finalize-ids, and by the dossier checks to catch a blocker "
        "dropped from blocked_by; default for those: origin/HEAD)",
    )
    ap.add_argument(
        "--pre-fanout",
        action="store_true",
        help="check the pre-fan-out obligations for --dossier: the build log "
        "must carry `hooks:` and `contract_review:` (current contract commit SHA, "
        "or skipped — reason) front matter, and record PAYLOAD-LINT: (payload and defect counts) and GATES: "
        "(the CI gate list) lines, and every ### Definitions term of "
        "## Contract must reach a stub file or be in DOSSIER-ONLY-TERMS:",
    )
    ap.add_argument(
        "--worktree",
        help="with --pre-fanout: the worktree X that holds the stubs, when it "
        "is not --root (local mode validates the dossier from the main "
        "checkout while the stubs live in X; the dossier's `worktree:` front-"
        "matter field is used when the flag is not given)",
    )
    ap.add_argument(
        "--mode",
        action="store_true",
        help="print which mode .discovery/ is in for --root — local, committed "
        "or conflict (exit 1) — and the gitignore checks that mode relies on",
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

    if args.mode:
        return run_mode(root)

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
            return check_pre_fanout(
                targets_d[0],
                root,
                Path(args.worktree).resolve() if args.worktree else None,
            )
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
        targets_a = adr_targets(adr_dir)
        if not targets_d and not targets_a:
            print(f"nothing to check under {dossier_dir} or {adr_dir}")
            return 0

    for p in targets_d:
        rep.merge(validate_dossier(p, root))
    check_blocker_removals(root, targets_d, args.base or default_base(root), rep)
    for p in targets_a:
        rep.merge(validate_adr(p, root))
    if adr_dir.exists() and (args.all or args.adr or not args.dossier):
        check_index(adr_dir, rep)
    if dossier_dir.exists() and (args.all or not (args.dossier or args.adr)):
        check_dossier_ids(dossier_dir, rep)

    print(f"checked {len(targets_d)} dossier(s), {len(targets_a)} ADR(s)\n")
    return rep.emit()


if __name__ == "__main__":
    sys.exit(main())
