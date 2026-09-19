#!/usr/bin/env python3
"""Check the flow documents' cross-references against the files they name.

Two inconsistencies lived in this repository for months because nothing
mechanical read the documents the way an agent does: `/plan` cited
`formats.md §4` for a section that did not exist, and four documents named a
command (`/open-work`) that no file provided. Both were dangling references
— the cheapest class of defect to catch and the most expensive to leave,
because an agent that follows a pointer into nothing improvises.

Four kinds of reference, each resolved against the repository:

1. Section references — `formats.md §4`, `formats.md § "The observability
   checklist"`. The named file (default: references/formats.md) must carry a
   heading that starts with that number, or whose title starts with the
   quoted text (case-insensitive, whitespace-normalised, backticks ignored).
   DEFECT when it does not.
2. Command mentions — a backticked `/plan`, `/work-on W-014`, `/deferred`.
   The command must exist as commands/<name>.md or primary-agents/<name>.md.
   DEFECT when it does not.
3. Plugin paths — `${PLUGIN_ROOT}/…`, and bare `scripts/…`, `references/…`,
   `sub-agents/…`, `primary-agents/…`, `commands/…`, `skills/…`. A
   `${PLUGIN_ROOT}` path that does not exist is a DEFECT; a bare path that
   does not exist is a WARNING, because a target repository's own
   `scripts/…` may be meant.
4. Phase references — `/plan Phase 3`, `work-on.md Phase 6` (DEFECT when the
   owner has no such heading), and a bare `Phase 8b` inside a file that has
   phase headings of its own (WARNING when that file lacks it: name the
   owner).

Prose only for 1, 2 and 4 — a code fence quotes text. Paths (3) are checked
inside fences too, because that is where the commands live. Generated files
under claude/ are not read: they mirror their sources.

Usage:
    check_docs.py              check every flow document
    check_docs.py --selftest   check the checker

Exit codes: 0 clean, 1 at least one DEFECT, 2 unusable input.

No third-party imports: this runs wherever python3 does.
"""

from __future__ import annotations

import argparse
import re
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

DOC_GLOBS = (
    "README.md",
    "references/*.md",
    "commands/*.md",
    "primary-agents/*.md",
    "sub-agents/*.md",
    "skills/**/*.md",
    "opencode/AGENTS.md",
)
DEFAULT_SECTION_FILE = "formats.md"
PATH_ROOTS = ("scripts", "references", "sub-agents", "primary-agents", "commands", "skills")
# A backticked `/name` that is a filesystem root, not a command.
NOT_A_COMMAND = {
    "tmp", "dev", "usr", "bin", "etc", "var", "opt", "home", "root", "abs",
    "path", "private", "users",
}

SECTION_RE = re.compile(r'§\s*(?:(?P<num>\d+)|"(?P<title>[^"]+)")')
MD_TOKEN_RE = re.compile(r"`[^`]*?([\w.-]+\.md)`")
COMMAND_RE = re.compile(r"(?<![`\w])`(/[a-z][a-z0-9-]*)((?:\s[^`]*)?)`")
PATH_RE = re.compile(
    r"(?:(?P<root>\$\{PLUGIN_ROOT\}/)|(?<![\w/.-]))"
    r"(?P<path>(?:" + "|".join(PATH_ROOTS) + r")/[\w./*<>-]+)"
)
OWNED_PHASE_RE = re.compile(
    r"(?:`?(?P<cmd>/(?:plan|work-on))`?|`?(?P<file>[\w-]+\.md)`?)\s+Phase\s+(?P<num>\d+[a-z]?)\b"
)
BARE_PHASE_RE = re.compile(r"\bPhase\s+(?P<num>\d+[a-z]?)\b")
PHASE_HEADING_RE = re.compile(r"^(?:Phase\s+)?(?P<num>\d+[a-z]?)\b")


class Report:
    def __init__(self) -> None:
        self.defects: list[str] = []
        self.warnings: list[str] = []

    def defect(self, where: str, msg: str) -> None:
        self.defects.append(f"DEFECT  {where}: {msg}")

    def warn(self, where: str, msg: str) -> None:
        self.warnings.append(f"WARNING {where}: {msg}")


# --------------------------------------------------------------------------
# reading
# --------------------------------------------------------------------------


def docs(repo: Path) -> list[Path]:
    out: list[Path] = []
    for pattern in DOC_GLOBS:
        out.extend(sorted(p for p in repo.glob(pattern) if p.is_file()))
    return out


def lines_with_fence(text: str) -> list[tuple[int, str, bool]]:
    """(1-based line number, line, inside a code fence) for every line."""
    out: list[tuple[int, str, bool]] = []
    fence = False
    for number, line in enumerate(text.split("\n"), 1):
        if line.strip().startswith("```"):
            fence = not fence
            out.append((number, line, True))
            continue
        out.append((number, line, fence))
    return out


def paragraphs(text: str) -> list[tuple[int, str]]:
    """Prose paragraphs joined onto one line, with their first line number."""
    out: list[tuple[int, str]] = []
    start = 0
    buf: list[str] = []
    for number, line, fenced in lines_with_fence(text):
        if fenced or not line.strip():
            if buf:
                out.append((start, " ".join(buf)))
                buf = []
            continue
        if not buf:
            start = number
        buf.append(line.strip())
    if buf:
        out.append((start, " ".join(buf)))
    return out


def headings(path: Path) -> list[str]:
    """Heading texts outside code fences, with the `#` marks stripped."""
    out: list[str] = []
    for _, line, fenced in lines_with_fence(path.read_text(encoding="utf-8")):
        if not fenced and line.startswith("#"):
            out.append(line.lstrip("#").strip())
    return out


def normalise(text: str) -> str:
    text = re.sub(r"^\d+\.\s*", "", text.strip())
    text = text.replace("`", "")
    return " ".join(text.split()).lower()


def command_names(repo: Path) -> set[str]:
    names: set[str] = set()
    for folder in ("commands", "primary-agents"):
        names.update(p.stem for p in (repo / folder).glob("*.md"))
    return names


def known_doc(repo: Path, name: str) -> Path | None:
    for candidate in docs(repo):
        if candidate.name == name:
            return candidate
    return None


# --------------------------------------------------------------------------
# the four checks
# --------------------------------------------------------------------------


def heading_matches(heads: list[str], num: str | None, title: str | None) -> bool:
    for head in heads:
        if num is not None and re.match(rf"^{re.escape(num)}\.", head.strip()):
            return True
        if title is not None and normalise(head).startswith(normalise(title)):
            return True
    return False


def check_sections(rel: str, text: str, repo: Path, rep: Report) -> None:
    for number, para in paragraphs(text):
        for match in SECTION_RE.finditer(para):
            named = MD_TOKEN_RE.findall(para[: match.start()])
            file_name = named[-1] if named else DEFAULT_SECTION_FILE
            target = known_doc(repo, file_name)
            where = f"{rel}:{number}"
            if target is None:
                rep.defect(where, f"section reference names '{file_name}', which is not a flow document")
                continue
            num, title = match.group("num"), match.group("title")
            if not heading_matches(headings(target), num, title):
                shown = f"§{num}" if num else f'§ "{title}"'
                rep.defect(
                    where,
                    f"{shown} has no heading in {target.relative_to(repo).as_posix()}",
                )


def check_commands(rel: str, text: str, repo: Path, rep: Report) -> None:
    names = command_names(repo)
    for number, para in paragraphs(text):
        for match in COMMAND_RE.finditer(para):
            name, rest = match.group(1)[1:], match.group(2)
            if "/" in rest or name in NOT_A_COMMAND:
                continue
            if name not in names:
                rep.defect(
                    f"{rel}:{number}",
                    f"`/{name}` is not a command; the commands are "
                    + ", ".join(f"/{n}" for n in sorted(names)),
                )


def check_paths(rel: str, text: str, repo: Path, rep: Report) -> None:
    for number, line, _ in lines_with_fence(text):
        for match in PATH_RE.finditer(line):
            path = match.group("path").rstrip(".,;:)")
            if any(ch in path for ch in "<>*"):
                continue  # a placeholder or a glob, not a file
            target = repo / path
            exists = target.is_dir() if path.endswith("/") else target.exists()
            if exists:
                continue
            where = f"{rel}:{number}"
            if match.group("root"):
                rep.defect(where, f"${{PLUGIN_ROOT}}/{path} does not exist")
            else:
                rep.warn(where, f"{path} does not exist in this plugin (a target repository's own file?)")


def phase_headings(path: Path) -> list[str]:
    """Phase numbers a file's headings carry: `## Phase 4 — …` and, in a
    file that has such headings, the lettered sub-steps `### 8a — …`. A
    numbered section (`# 4. ID minting`) is not a phase."""
    heads = headings(path)
    if not any(head.startswith("Phase ") for head in heads):
        return []
    out: list[str] = []
    for head in heads:
        match = PHASE_HEADING_RE.match(head)
        if match and (head.startswith("Phase ") or re.match(r"^\d+[a-z]\b", head)):
            out.append(match.group("num"))
    return out


def check_phases(rel: str, text: str, repo: Path, rep: Report) -> None:
    own = phase_headings(repo / rel) if (repo / rel).exists() else []
    for number, para in paragraphs(text):
        owned_spans: list[tuple[int, int]] = []
        for match in OWNED_PHASE_RE.finditer(para):
            owned_spans.append(match.span())
            cmd, file_name, num = match.group("cmd"), match.group("file"), match.group("num")
            target = (
                repo / "primary-agents" / f"{cmd[1:]}.md" if cmd else known_doc(repo, file_name or "")
            )
            where = f"{rel}:{number}"
            if target is None or not target.exists():
                rep.defect(where, f"Phase {num} of '{cmd or file_name}', which is not a flow document")
                continue
            if num not in phase_headings(target):
                rep.defect(
                    where,
                    f"Phase {num} has no heading in {target.relative_to(repo).as_posix()}",
                )
        if not own:
            continue
        for match in BARE_PHASE_RE.finditer(para):
            if any(a <= match.start() < b for a, b in owned_spans):
                continue
            num = match.group("num")
            if num not in own:
                rep.warn(
                    f"{rel}:{number}",
                    f"bare 'Phase {num}' has no heading in this file; name the owner",
                )


def check_repo(repo: Path) -> Report:
    rep = Report()
    for path in docs(repo):
        rel = path.relative_to(repo).as_posix()
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            rep.defect(rel, f"cannot read: {exc}")
            continue
        check_sections(rel, text, repo, rep)
        check_commands(rel, text, repo, rep)
        check_paths(rel, text, repo, rep)
        check_phases(rel, text, repo, rep)
    return rep


# --------------------------------------------------------------------------
# selftest
# --------------------------------------------------------------------------

_FORMATS = """# Formats

## Two modes for `.discovery/`

Text.

---

# 1. The dossier

## Front matter

# 4. ID minting

#### The observability checklist
"""

_PLAN = """---
description: plan
---

# /plan — a description in

## Phase 0 — Mode

## Phase 1 — Intake

## Phase 7 — Write `ready`
"""

_GOOD = """Read `formats.md` §4 and `${PLUGIN_ROOT}/references/formats.md` § "The
observability checklist". Then `/plan W-014` runs `/plan` Phase 1 and hands
off to `/work-on W-014`; `references/formats.md` § "Two modes" says why.
Run `python3 ${PLUGIN_ROOT}/scripts/validate_pipeline.py --root .` and read
`/abs/path/repo` and `/tmp`. A bare Phase 7 is fine here.

```bash
python3 ${PLUGIN_ROOT}/scripts/validate_pipeline.py --all
```
"""

_BAD = """See `formats.md` §5 and `formats.md` § "ASD-STE200". Then `/open-work`
reports, and `/plan` Phase 9 never runs. Read
`${PLUGIN_ROOT}/scripts/nope.py` and `scripts/maybe.py`; `plan.md Phase 3`
is not a heading either. A bare Phase 12 has no heading.
"""


def selftest() -> int:
    failures: list[str] = []

    def check(name: str, cond: bool) -> None:
        if not cond:
            failures.append(name)

    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp)
        for folder in ("references", "commands", "primary-agents", "scripts"):
            (repo / folder).mkdir()
        (repo / "references" / "formats.md").write_text(_FORMATS)
        (repo / "commands" / "deferred.md").write_text("# /deferred\n")
        (repo / "primary-agents" / "plan.md").write_text(_PLAN)
        (repo / "primary-agents" / "work-on.md").write_text("# /work-on\n\n## Phase 4 — Fan out\n")
        (repo / "scripts" / "validate_pipeline.py").write_text("")
        (repo / "README.md").write_text(_GOOD)

        rep = check_repo(repo)
        check("good doc has no defects: " + "; ".join(rep.defects), not rep.defects)
        check("good doc has no warnings: " + "; ".join(rep.warnings), not rep.warnings)

        (repo / "README.md").write_text(_BAD)
        rep = check_repo(repo)
        joined = "\n".join(rep.defects)
        check("§5 caught", "§5 has no heading" in joined)
        check("quoted title caught", '§ "ASD-STE200" has no heading' in joined)
        check("unknown command caught", "`/open-work` is not a command" in joined)
        check("owned phase caught", "Phase 9 has no heading in primary-agents/plan.md" in joined)
        check("plan.md phase caught", "Phase 3 has no heading in primary-agents/plan.md" in joined)
        check("plugin path caught", "${PLUGIN_ROOT}/scripts/nope.py does not exist" in joined)
        check("bare path is a warning", any("scripts/maybe.py" in w for w in rep.warnings))
        check(f"defect count is 6, got {len(rep.defects)}", len(rep.defects) == 6)

        # A bare phase inside a file with phase headings warns; the README has none.
        (repo / "primary-agents" / "plan.md").write_text(_PLAN + "\nA bare Phase 12 here.\n")
        rep = check_repo(repo)
        check("bare phase in plan.md warns", any("Phase 12" in w for w in rep.warnings))

    for item in failures:
        print(f"SELFTEST FAIL  {item}")
    print(f"selftest: {len(failures)} failure(s)")
    return 1 if failures else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--root", default=str(REPO), help="plugin checkout (default: this one)")
    ap.add_argument("--selftest", action="store_true", help="check the checker")
    args = ap.parse_args()
    if args.selftest:
        return selftest()
    repo = Path(args.root).resolve()
    if not (repo / "references").is_dir():
        print(f"UNUSABLE  {repo} has no references/ directory", file=sys.stderr)
        return 2
    rep = check_repo(repo)
    for item in rep.defects + rep.warnings:
        print(item)
    print(
        f"{'FAIL' if rep.defects else 'PASS'} — {len(docs(repo))} document(s), "
        f"{len(rep.defects)} defect(s), {len(rep.warnings)} warning(s)"
    )
    return 1 if rep.defects else 0


if __name__ == "__main__":
    sys.exit(main())
