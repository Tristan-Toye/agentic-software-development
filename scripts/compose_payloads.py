#!/usr/bin/env python3
"""Compose one Phase 4 spawn payload with every mechanical field filled in.

Why: `/work-on` Phase 4's orchestrator hand-writes every spawn payload —
5-20 KB each, 5+ per wave — and its biggest reasoning spikes (up to 24k
output tokens, `references/payloads/README.md`) are exactly these
compositions, even though most fields are mechanical: copied verbatim from
the dossier's front matter and sections, from the contract files on disk, or
from command-line facts already known before the spawn. Only a few fields
per kind need judgement (a test's fixtures, a review's blast-radius scope,
the concrete assertions a checklist makes). This script fills every
mechanical field itself and leaves every judgement field as an explicit
`@@FILL_<FIELD>@@` placeholder — `check_payload.py` refuses any `@@TOKEN@@`
left in a payload (its check 8), so a payload this script writes can never
ship with an unfilled judgement field silently guessed at.

It reuses `check_payload.py`'s own `FIELDS`/`CONDITIONAL`/`GROUPS` tables (by
importing the module, never copying them) to know which fields a kind takes
and which become required under `MODE`/`LENS`, and `dossier_edit.py`'s own
`section()` / `split_front_matter()` to read the dossier without a bespoke
parser.

# Mechanical vs judgement (see the field-by-field notes beside each `compose_*`
function below for the exact reasoning per field)

Mechanical, always derived, never a placeholder: WORKTREE_DIR, BRANCH, MODE
(default `build`), CONTRACT (+ CONTRACT_HASH), OWNED_PATHS (with a stub-location
aid appended: `member -> path:line`, grepped from the contract), PACKAGE,
CRITERIA, STANDARDS, JIRA_KEY, DOSSIER (the integration author's excerpt, or
the reviewer's build-log-free copy, generated and written under
`.agent-staging/` by this script — never the live dossier itself),
TEST_COMMAND/HOOKS (from the flags), MAX_SINGLE_EDIT, CITATION and NAMING
(fixed process conventions, from the skeletons, overridable with --set),
ROUND (default 1); ARBITRATIONS is always a fill.

Judgement, `@@FILL_<FIELD>@@` unless given with --set: PROMISE_CHECKLIST,
TEST_PATHS, TEST_FRAMEWORK, CONVENTIONS, VOCABULARY, FIXTURES, STYLE_PATHS /
STYLE_SAMPLE, SUPPORT_PATHS / SUPPORT (optional — omitted unless given),
HARNESS, BOUNDARIES, SCOPE, RUN_EVIDENCE, RULES, CONTEXT_DOCS, CRS / FAILURES
(implementer MODE=fix), LENS (reviewer — must be given with --set; there is
no safe default). SHARED_IDIOM / VERIFY_EMBEDDED / DELIVERY_CHANGE / PRIOR_CRS
are optional judgement fields, included only when given with --set.

# Usage

    compose_payloads.py --kind implementer --dossier LIVE_DOSSIER --root X \\
        --host opencode --out FILE --package P1 \\
        --contract-paths src/flush.py [--test-command CMD] [--hooks TEXT] \\
        [--set FIELD=VALUE ...] [--plugin-root DIR] [--manifest FILE] [--force]
    compose_payloads.py --selftest

Exit codes: 0 written; 1 refused (missing section, package row, or contract
file); 2 unusable input (bad arguments, unreadable files).

No third-party imports: this runs wherever python3 does.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import os
import re
import subprocess
import sys
import tempfile

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, os.path.join(SCRIPTS_DIR, name + ".py"))
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


cp = _load("check_payload")   # FIELDS, CONDITIONAL, GROUPS — the field tables
de = _load("dossier_edit")    # section(), split_front_matter(), KEY_RE, Refused

MARKER = "# compose_payloads.py: generated payload — re-run with --force to overwrite by hand"


class Refusal(Exception):
    """A missing section, package row or contract file. Exit 1."""


# ---------------------------------------------------------------------------
# Dossier reading — all of it goes through dossier_edit.py, never a bespoke
# read of the whole file into a fresh parser.
# ---------------------------------------------------------------------------

def read_text(path: str) -> str:
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    except OSError as err:
        print("compose_payloads: cannot read %s: %s" % (path, err), file=sys.stderr)
        raise SystemExit(2)


def front_matter(text: str) -> dict[str, str]:
    """Scalar front-matter fields (id, jira, branch, worktree, ...). List
    fields (adrs, anchors, blocked_by) are skipped — nothing here needs them."""
    try:
        fm_lines, _body = de.split_front_matter(text)
    except de.Refused as err:
        raise Refusal(str(err))
    out: dict[str, str] = {}
    for line in fm_lines:
        m = de.KEY_RE.match(line)
        if not m:
            continue
        value = m.group("rest").split(" #", 1)[0].strip()
        if value == "":
            continue  # a list field's own line is blank; its items follow indented
        out[m.group("key")] = value
    return out


def strip_line_numbers(numbered: str) -> str:
    """dossier_edit.section() prefixes every line `N\\t...` for a human
    reader; undo that for programmatic use."""
    return "\n".join(line.partition("\t")[2] for line in numbered.splitlines())


def section_body(text: str, heading: str, drop_heading: bool = True) -> str:
    """One section's text via dossier_edit.section(), heading and line
    numbers stripped, blank edges trimmed."""
    try:
        numbered = de.section(text, heading)
    except de.Refused as err:
        raise Refusal(str(err))
    lines = strip_line_numbers(numbered).splitlines()
    if drop_heading and lines:
        lines = lines[1:]
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    return "\n".join(lines)


def excerpt(text: str, headings: tuple[str, ...]) -> str:
    """Two or three whole sections, headings included, verbatim and
    concatenated — the shape the integration author's DOSSIER excerpt and
    the reviewer's build-log-free plan copy both are."""
    parts = []
    for heading in headings:
        parts.append(section_body(text, heading, drop_heading=False))
    return "\n\n".join(parts) + "\n"


def dossier_minus_build_log(text: str) -> str:
    """The whole dossier, `## Build log` excised — the reviewer's `LENS: plan`
    DOSSIER copy (references/payloads/reviewer.md: "a copy without
    `## Build log`", because a Read with no limit returns the whole file)."""
    match = re.search(r"^## Build log\s*$", text, re.MULTILINE)
    return text if not match else text[: match.start()].rstrip("\n") + "\n"


PACKAGE_ROW = re.compile(r"^\s*\|(.+)\|\s*$")


def work_packages(text: str) -> list[list[str]]:
    body = section_body(text, "## Work packages", drop_heading=True)
    rows: list[list[str]] = []
    for line in body.splitlines():
        m = PACKAGE_ROW.match(line)
        if not m:
            continue
        cells = [c.strip() for c in m.group(1).split("|")]
        if all(re.fullmatch(r"-+", c) for c in cells):
            continue  # the `|---|---|---|` separator
        if cells and cells[0].lower() == "package":
            continue  # the header row
        rows.append(cells)
    return rows


def find_package_row(text: str, package: str) -> list[str]:
    for cells in work_packages(text):
        name = cells[0]
        if name == package or name.split()[0] == package:
            return cells
    raise Refusal(
        "no `## Work packages` row matches --package %r (rows: %s)"
        % (package, ", ".join(c[0] for c in work_packages(text)) or "none")
    )


# ---------------------------------------------------------------------------
# The contract: verbatim bytes, always pasted (README's `CONTRACT` rule: the
# same bytes reach every author of a fan-out, and the pasted form is the one
# every host and every kind can carry) — never a path, so CONTRACT_HASH
# always stamps exactly what the field carries.
# ---------------------------------------------------------------------------

def contract_bytes(paths: list[str], root: str) -> str:
    if not paths:
        raise Refusal("CONTRACT is required and --contract-paths was not given")
    texts = []
    for raw in paths:
        path = raw if os.path.isabs(raw) else os.path.join(root, raw)
        if not os.path.isfile(path):
            raise Refusal("contract file not found: %s" % path)
        texts.append(read_text(path))
    return "\n\n".join(texts)


def contract_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# A member name a contract signature declares: an identifier immediately
# before `(`. The contract holds signatures and doc comments only (no
# bodies — references/formats/dossier.md, "## Contract"), so almost every
# such identifier in it names something callable; the stoplist removes the
# handful of language keywords that also precede `(`.
MEMBER_RE = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*\(")
KEYWORDS = {
    "if", "for", "while", "switch", "catch", "return", "match", "fn", "def",
    "function", "class", "struct", "impl", "pub", "void", "new", "print",
    "raise", "throw", "assert", "with", "except", "async", "await", "lambda",
    "yield", "self", "this", "super", "static", "public", "private",
    "protected", "override", "virtual", "abstract", "interface", "enum",
    "trait", "use", "import", "from", "as", "let", "var", "const", "type",
    "typeof", "instanceof", "in", "is", "and", "or", "not", "true", "false",
    "null", "none", "nil", "case", "default", "do", "else", "elif", "finally",
    "try",
}
COMMENT_PREFIX = ("#", "//", "*", "///", "'''", '"""')


def contract_members(contract_text: str) -> list[str]:
    names: list[str] = []
    for line in contract_text.splitlines():
        stripped = line.strip()
        if stripped.startswith(COMMENT_PREFIX):
            continue
        for m in MEMBER_RE.finditer(line):
            name = m.group(1)
            if name.lower() in KEYWORDS or name in names:
                continue
            names.append(name)
    return names


def stub_locations(members: list[str], owned_paths: list[str], root: str) -> list[str]:
    """`member -> path:line` for every contract member found in an owned
    path — the TARGETS-style aid so the implementer does not spend steps
    orienting. Silent (no line) for a path not yet materialised."""
    out: list[str] = []
    for rel in owned_paths:
        path = rel if os.path.isabs(rel) else os.path.join(root, rel)
        if not os.path.isfile(path):
            continue
        lines = read_text(path).splitlines()
        for name in members:
            pattern = re.compile(r"\b%s\s*\(" % re.escape(name))
            for number, line in enumerate(lines, 1):
                if pattern.search(line):
                    out.append("%s -> %s:%d" % (name, rel, number))
                    break
    return out


# ---------------------------------------------------------------------------
# Placeholders and rendering
# ---------------------------------------------------------------------------

def placeholder(field: str, hint: str) -> str:
    """`@@FILL_<FIELD>@@ <hint>` — the token half matches check_payload.py's
    TEMPLATE_TOKEN (`@@[A-Za-z_][A-Za-z0-9_-]*@@`, no whitespace inside) so
    an unfilled judgement field is refused (its check 8), not merely noted;
    the trailing hint is free text the lint does not need to parse."""
    return "@@FILL_%s@@ %s" % (field, hint)


def is_placeholder(value: str) -> bool:
    return "@@FILL_" in value


class Field:
    __slots__ = ("name", "value", "block")

    def __init__(self, name: str, value: str, block: bool = False):
        self.name = name
        self.value = value
        self.block = block


def render_field(field: Field) -> str:
    if field.block:
        lines = field.value.splitlines() or [""]
        body = "\n".join(("  " + l if l.strip() else "") for l in lines)
        return "%s: |\n%s\n" % (field.name, body)
    return "%s: %s\n" % (field.name, field.value)


# ---------------------------------------------------------------------------
# Per-kind composition. Each function returns the ordered field list, the
# root context is a dict of the parsed dossier plus the CLI's flags.
# ---------------------------------------------------------------------------

MAX_SINGLE_EDIT_DEFAULT = (
    "350 lines — the cap for one Write or Edit; a larger deliverable is "
    "named as a split at spawn, never improvised"
)
CITATION_DEFAULT = (
    "// promise: <member>/<category>\n"
    "One comment per test, the line above the test, in exactly this shape — "
    "`promise: <checklist id>/<category>`. One citation vocabulary per "
    "build; authors inventing their own shape diverge per file."
)
NAMING_DEFAULT = (
    "Subject_StateUnderTest_ExpectedBehavior — Subject is the public member "
    "under test, e.g. Flush_EmptyQueue_ReturnsZero"
)


def apply_overrides(fields: dict[str, Field], sets: dict[str, str], blocks: set[str]) -> None:
    for name, value in sets.items():
        fields[name] = Field(name, value, block=name in blocks)


def compose_implementer(ctx: dict) -> list[Field]:
    """WORKTREE_DIR/BRANCH: mechanical — build mode forks a per-package
    worktree and branch suffix (`references/payloads/implementer.md`,
    `references/work-on/phase-4.md`: `git worktree add ../<repo>-<ID>-P1 -b
    <branch>-p1`); fix mode reuses the base worktree and branch unchanged.
    MODE: mechanical, default `build`. CONTRACT/CONTRACT_HASH: mechanical,
    pasted bytes + their hash. OWNED_PATHS: mechanical, the --package row's
    Owned paths, with a stub-location aid appended (grepped member ->
    path:line) so the implementer does not spend steps orienting — this is
    the TARGETS-style aid the design calls for, attached here rather than to
    a new field. PACKAGE: mechanical, the row's own Package cell.
    CRITERIA: mechanical, the whole `## Acceptance criteria` section verbatim
    (build mode only — fix mode is scoped by CRS/FAILURES instead).
    STANDARDS/JIRA_KEY: mechanical, from --plugin-root and the dossier.
    TEST_COMMAND/HOOKS: mechanical when the flag is given, else judgement
    (the orchestrator verifies these in its own shell first —
    `references/work-on/phase-4.md`) — @@FILL. CRS/FAILURES (fix mode): pure
    judgement (the merged review change requests, or a failing test's
    output) — always @@FILL unless given with --set. SHARED_IDIOM/
    VERIFY_EMBEDDED: optional judgement, included only when --set names
    them."""
    root, dossier_text, fm, args, sets = ctx["root"], ctx["dossier_text"], ctx["fm"], ctx["args"], ctx["sets"]
    mode = sets.get("MODE", "build")
    if mode not in ("build", "fix"):
        raise Refusal("MODE must be build or fix, got %r" % mode)
    branch = fm.get("branch")
    if not branch:
        raise Refusal("dossier front matter has no `branch:`")

    fields: dict[str, Field] = {}
    owned_paths: list[str] | None = None
    if mode == "build":
        if not args.package:
            raise Refusal("implementer MODE=build needs --package")
        row = find_package_row(dossier_text, args.package)
        code = args.package.split()[0]
        fields["WORKTREE_DIR"] = Field("WORKTREE_DIR", "%s-%s" % (root.rstrip("/"), code.upper()))
        fields["BRANCH"] = Field("BRANCH", "%s-%s" % (branch, code.lower()))
        owned_paths = [p.strip() for p in row[1].split(",") if p.strip()]
        fields["PACKAGE"] = Field("PACKAGE", row[0])
        criteria = section_body(dossier_text, "## Acceptance criteria")
        fields["CRITERIA"] = Field("CRITERIA", criteria, block=True)
    else:
        fields["WORKTREE_DIR"] = Field("WORKTREE_DIR", root)
        fields["BRANCH"] = Field("BRANCH", branch)
        if args.package:
            row = find_package_row(dossier_text, args.package)
            owned_paths = [p.strip() for p in row[1].split(",") if p.strip()]
        if "CRS" not in sets and "FAILURES" not in sets:
            fields["CRS"] = Field("CRS", placeholder(
                "CRS", "the merged change-request documents from the three lenses, "
                "verbatim, plus any user arbitration that overrides one"), block=True)
    fields["MODE"] = Field("MODE", mode)

    # implementer has no CONTRACT_HASH field (check_payload.py's FIELDS table
    # gives that field to the two test authors only, whose re-spawn rule
    # depends on it — README, "CONTRACT_HASH stamps which contract an author
    # saw"); the implementer's CONTRACT is still pasted verbatim.
    contract_text = contract_bytes(args.contract_paths, root)
    fields["CONTRACT"] = Field("CONTRACT", contract_text, block=True)

    if owned_paths is None:
        fields["OWNED_PATHS"] = Field("OWNED_PATHS", placeholder(
            "OWNED_PATHS", "the owned paths for this package, from ## Work packages "
            "(pass --package to fill this mechanically)"), block=True)
    else:
        members = contract_members(contract_text)
        aid = stub_locations(members, owned_paths, root)
        body = "\n".join(owned_paths)
        if aid:
            body += "\n# stub locations (grepped from CONTRACT members):\n" + "\n".join(
                "#   " + line for line in aid
            )
        fields["OWNED_PATHS"] = Field("OWNED_PATHS", body, block=True)

    fields["TEST_COMMAND"] = Field(
        "TEST_COMMAND",
        args.test_command if args.test_command else placeholder(
            "TEST_COMMAND", "the EXISTING suite's invocation, verified in your own shell "
            "first, and its known-red shape if the baseline is not green"),
    )
    fields["HOOKS"] = Field(
        "HOOKS", args.hooks if args.hooks else placeholder(
            "HOOKS", "what this repo's commit hooks do to a partly migrated tree, or "
            "`none` when verified there are none"), block=True,
    )
    fields["STANDARDS"] = Field("STANDARDS", os.path.join(
        ctx["plugin_root"], "skills", "standards", "engineering-standards.md"))
    fields["JIRA_KEY"] = Field("JIRA_KEY", fm.get("jira", placeholder(
        "JIRA_KEY", "the dossier's jira: front-matter field")))

    apply_overrides(fields, sets, blocks={"CRS", "FAILURES", "SHARED_IDIOM", "VERIFY_EMBEDDED", "HOOKS", "CONTRACT"})

    order = (["WORKTREE_DIR", "BRANCH", "MODE", "CONTRACT"]
             + (["PACKAGE", "SHARED_IDIOM", "OWNED_PATHS", "CRITERIA"] if mode == "build"
                else ["CRS", "FAILURES", "OWNED_PATHS"])
             + ["TEST_COMMAND", "HOOKS", "VERIFY_EMBEDDED", "STANDARDS", "JIRA_KEY"])
    return [fields[n] for n in order if n in fields]


def compose_unit_test_author(ctx: dict) -> list[Field]:
    """CONTRACT/CONTRACT_HASH: mechanical, pasted bytes + hash — always
    pasted regardless of host (README: "the pasted fields ... carry what the
    path was meant to carry"; pasting is valid under opencode too, and is
    the only valid form under Claude Code, so it is the one universal
    choice). MAX_SINGLE_EDIT/CITATION/NAMING: mechanical fixed process
    conventions from the skeleton (overridable with --set when a repo's own
    convention differs). PROMISE_CHECKLIST/TEST_PATHS/TEST_FRAMEWORK/
    CONVENTIONS/VOCABULARY/FIXTURES/STYLE_PATHS or STYLE_SAMPLE: judgement —
    the checklist's concrete assertions, where the test file lands, the
    repo's own verified conventions and a real existing test to match — none
    of this is derivable from the dossier or the contract alone, so all
    become @@FILL unless given with --set. SUPPORT_PATHS/SUPPORT/
    SHARED_IDIOM/DELIVERY_CHANGE: optional judgement, included only when
    --set names them."""
    root, args, sets, host = ctx["root"], ctx["args"], ctx["sets"], ctx["host"]
    contract_text = contract_bytes(args.contract_paths, root)
    fields: dict[str, Field] = {
        "CONTRACT": Field("CONTRACT", contract_text, block=True),
        "CONTRACT_HASH": Field("CONTRACT_HASH", contract_hash(contract_text)),
        "PROMISE_CHECKLIST": Field("PROMISE_CHECKLIST", placeholder(
            "PROMISE_CHECKLIST", "one line per member per category, the concrete "
            "assertion (never a topic), [whole-value]/[member] tags on lines that "
            "state the whole result — references/formats/dossier.md, the "
            "observability checklist"), block=True),
        "TEST_PATHS": Field("TEST_PATHS", placeholder(
            "TEST_PATHS", "the absolute path this author writes, inside the base "
            "worktree, per the Phase 3 split")),
        "TEST_FRAMEWORK": Field("TEST_FRAMEWORK", placeholder(
            "TEST_FRAMEWORK", "the framework, assertion style, and the exact command "
            "that runs this file's suite")),
        "MAX_SINGLE_EDIT": Field("MAX_SINGLE_EDIT", MAX_SINGLE_EDIT_DEFAULT),
        "CITATION": Field("CITATION", CITATION_DEFAULT, block=True),
        "CONVENTIONS": Field("CONVENTIONS", placeholder(
            "CONVENTIONS", "repo facts a blind author cannot discover — derives to "
            "add, spelling-checker tokens, id shapes — each verified against the "
            "repo itself"), block=True),
        "NAMING": Field("NAMING", NAMING_DEFAULT),
        "VOCABULARY": Field("VOCABULARY", placeholder(
            "VOCABULARY", "the project's own terms for the ideas under test")),
        "FIXTURES": Field("FIXTURES", placeholder(
            "FIXTURES", "how to build every value the checklist's assertions "
            "construct or call"), block=True),
    }
    if host == "opencode":
        fields["STYLE_PATHS"] = Field("STYLE_PATHS", placeholder(
            "STYLE_PATHS", "an existing test file from this repo, inside this "
            "author's read map, to match"))
    else:
        fields["STYLE_SAMPLE"] = Field("STYLE_SAMPLE", placeholder(
            "STYLE_SAMPLE", "one existing test from this repo, pasted verbatim"), block=True)

    apply_overrides(fields, sets, blocks={
        "CONTRACT", "PROMISE_CHECKLIST", "CONVENTIONS", "FIXTURES", "STYLE_SAMPLE",
        "SUPPORT", "SHARED_IDIOM", "DELIVERY_CHANGE", "CITATION",
    })
    order = ["CONTRACT", "PROMISE_CHECKLIST", "TEST_PATHS", "TEST_FRAMEWORK",
             "MAX_SINGLE_EDIT", "CITATION", "CONVENTIONS",
             "STYLE_PATHS", "STYLE_SAMPLE", "SUPPORT_PATHS", "SUPPORT",
             "NAMING", "VOCABULARY", "FIXTURES", "SHARED_IDIOM",
             "DELIVERY_CHANGE", "CONTRACT_HASH"]
    return [fields[n] for n in order if n in fields]


def compose_integration_test_author(ctx: dict) -> list[Field]:
    """WORKTREE_DIR: mechanical, --root. DOSSIER: mechanical — this script
    generates the excerpt itself (`## Problem`, `## Approach`,
    `## Acceptance criteria`, verbatim, nothing else) under
    `.agent-staging/` inside --root and names that file, regenerated fresh
    every run so it can never go stale by construction (the rule in
    `check_payload.py`'s check 11 and `references/payloads/
    integration-test-author.md`). CONTRACT/CONTRACT_HASH: mechanical, pasted
    bytes + hash (this author always has Read, but CONTRACT is still pasted,
    never a path, so the byte-identical rule across the whole fan-out holds
    regardless of host). TEST_PATHS/TEST_FRAMEWORK/HARNESS/STYLE_SAMPLE/
    BOUNDARIES: judgement — which flows get which file, the harness fixture,
    a real style sample, and which boundaries are substituted are all
    decisions only the orchestrator can make; @@FILL unless --set.
    PROMISE_CHECKLIST/SHARED_IDIOM/DELIVERY_CHANGE: optional, included only
    when --set names them."""
    root, dossier_text, args, sets = ctx["root"], ctx["dossier_text"], ctx["args"], ctx["sets"]
    fm = ctx["fm"]
    dossier_id = fm.get("id")
    if not dossier_id:
        raise Refusal("dossier front matter has no `id:`")
    staging = os.path.join(root, ".agent-staging")
    os.makedirs(staging, exist_ok=True)
    excerpt_path = os.path.join(staging, "%s.excerpt.md" % dossier_id)
    with open(excerpt_path, "w", encoding="utf-8") as fh:
        fh.write(excerpt(dossier_text, ("## Problem", "## Approach", "## Acceptance criteria")))

    contract_text = contract_bytes(args.contract_paths, root)
    fields: dict[str, Field] = {
        "WORKTREE_DIR": Field("WORKTREE_DIR", root),
        "DOSSIER": Field("DOSSIER", excerpt_path),
        "CONTRACT": Field("CONTRACT", contract_text, block=True),
        "TEST_PATHS": Field("TEST_PATHS", placeholder(
            "TEST_PATHS", "one absolute path per flow — the default, not a hint")),
        "TEST_FRAMEWORK": Field("TEST_FRAMEWORK", placeholder(
            "TEST_FRAMEWORK", "the framework and the exact command that runs this "
            "suite")),
        "HARNESS": Field("HARNESS", placeholder(
            "HARNESS", "the fixture that stands up the real app/system and how to "
            "seed it"), block=True),
        "STYLE_SAMPLE": Field("STYLE_SAMPLE", placeholder(
            "STYLE_SAMPLE", "one existing integration test, pasted verbatim"), block=True),
        "BOUNDARIES": Field("BOUNDARIES", placeholder(
            "BOUNDARIES", "which two or three seams are substituted (e.g. IClock, an "
            "external API) — everything else runs for real")),
        "CONTRACT_HASH": Field("CONTRACT_HASH", contract_hash(contract_text)),
    }
    apply_overrides(fields, sets, blocks={
        "CONTRACT", "HARNESS", "STYLE_SAMPLE", "PROMISE_CHECKLIST", "SHARED_IDIOM",
        "DELIVERY_CHANGE",
    })
    order = ["WORKTREE_DIR", "DOSSIER", "CONTRACT", "PROMISE_CHECKLIST", "TEST_PATHS",
             "TEST_FRAMEWORK", "HARNESS", "STYLE_SAMPLE", "BOUNDARIES", "SHARED_IDIOM",
             "DELIVERY_CHANGE", "CONTRACT_HASH"]
    return [fields[n] for n in order if n in fields]


def compose_reviewer(ctx: dict) -> list[Field]:
    """LENS: pure judgement at the CLI level — there is no safe default, so
    it must arrive via --set LENS=... or the payload cannot be composed at
    all (refused, exit 1) since every other required field depends on it.
    WORKTREE_DIR: mechanical, --root. STANDARDS: mechanical, --plugin-root.
    ROUND: mechanical, default `1`. DOSSIER (LENS=plan): mechanical — the
    whole live dossier with `## Build log` excised, generated under
    `.agent-staging/` (a Read with no limit returns the whole file
    otherwise — `references/payloads/reviewer.md`). CRITERIA: mechanical,
    the whole `## Acceptance criteria` section verbatim. CONTRACT
    (code lenses): mechanical, pasted bytes. ARBITRATIONS: judgement, never
    defaulted — the rulings accumulate across rounds, so `none` must be a
    decision, not a default. SCOPE/RUN_EVIDENCE/RULES/CONTEXT_DOCS/PRIOR_CRS: pure
    judgement — the blast radius, the green run's output, the narrowed OCR
    rules for this one lens, and which doc is relevant are all decisions
    only the orchestrator makes; @@FILL unless --set."""
    root, dossier_text, args, sets = ctx["root"], ctx["dossier_text"], ctx["args"], ctx["sets"]
    lens = sets.get("LENS")
    if lens not in ("plan", "style", "architecture", "performance"):
        raise Refusal("reviewer needs --set LENS=plan|style|architecture|performance")

    fields: dict[str, Field] = {
        "LENS": Field("LENS", lens),
        "WORKTREE_DIR": Field("WORKTREE_DIR", root),
        "STANDARDS": Field("STANDARDS", os.path.join(
            ctx["plugin_root"], "skills", "standards", "engineering-standards.md")),
        "ROUND": Field("ROUND", sets.get("ROUND", "1")),
    }
    if lens == "plan":
        staging = os.path.join(root, ".agent-staging")
        os.makedirs(staging, exist_ok=True)
        dossier_id = ctx["fm"].get("id", "dossier")
        copy_path = os.path.join(staging, "%s.plan-copy.md" % dossier_id)
        with open(copy_path, "w", encoding="utf-8") as fh:
            fh.write(dossier_minus_build_log(dossier_text))
        fields["DOSSIER"] = Field("DOSSIER", copy_path)
        fields["CRITERIA"] = Field("CRITERIA", section_body(dossier_text, "## Acceptance criteria"), block=True)
        fields["CONTEXT_DOCS"] = Field("CONTEXT_DOCS", placeholder(
            "CONTEXT_DOCS", "the ADR(s) this plan should be checked against, if any"))
    else:
        contract_text = contract_bytes(args.contract_paths, root)
        fields["SCOPE"] = Field("SCOPE", placeholder(
            "SCOPE", "the blast radius as a location list (file :: member (changed) "
            "/ (direct caller, unchanged)) — never a diff"), block=True)
        fields["CONTRACT"] = Field("CONTRACT", contract_text, block=True)
        fields["RUN_EVIDENCE"] = Field("RUN_EVIDENCE", placeholder(
            "RUN_EVIDENCE", "the full green test run's output, verbatim"), block=True)
        fields["CRITERIA"] = Field("CRITERIA", section_body(dossier_text, "## Acceptance criteria"), block=True)
        fields["RULES"] = Field("RULES", placeholder(
            "RULES", "this lens's own slice of the resolved OCR checklist, or `none`"), block=True)
        fields["CONTEXT_DOCS"] = Field("CONTEXT_DOCS", placeholder(
            "CONTEXT_DOCS", "the ADR(s) relevant to this lens, if any"))
        # Never defaulted: the rulings accumulate across rounds and go into
        # every later spawn, so a silent `none` could drop a real one.
        fields["ARBITRATIONS"] = Field("ARBITRATIONS", placeholder(
            "ARBITRATIONS", "every Phase 6 ruling so far, one per line, or `none`"), block=True)

    apply_overrides(fields, sets, blocks={
        "DOSSIER", "CRITERIA", "SCOPE", "CONTRACT", "RUN_EVIDENCE", "RULES",
        "ARBITRATIONS", "PRIOR_CRS",
    })
    order = ["LENS", "DOSSIER", "WORKTREE_DIR", "SCOPE", "CONTRACT", "RUN_EVIDENCE",
             "CRITERIA", "STANDARDS", "RULES", "CONTEXT_DOCS", "ARBITRATIONS",
             "PRIOR_CRS", "ROUND"]
    return [fields[n] for n in order if n in fields]


COMPOSERS = {
    "implementer": compose_implementer,
    "unit-test-author": compose_unit_test_author,
    "integration-test-author": compose_integration_test_author,
    "reviewer": compose_reviewer,
}


# ---------------------------------------------------------------------------
# Required-field check against check_payload.py's own tables — imported, not
# duplicated, so a rule added there is honoured here without a second edit.
# ---------------------------------------------------------------------------

def required_fields(kind: str, present: dict[str, Field]) -> set[str]:
    required = set(cp.FIELDS[kind]["required"])
    for (k, disc, value), (more, _groups) in cp.CONDITIONAL.items():
        if k == kind and present.get(disc) and present[disc].value == value:
            required |= more
    return required


def missing_after_compose(kind: str, fields: list[Field]) -> list[str]:
    present = {f.name: f for f in fields}
    required = required_fields(kind, present)
    for group in cp.GROUPS.get(kind, []):
        if not (group & set(present)):
            required.add(sorted(group)[0])  # at least name the first as missing
    return sorted(required - set(present))


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def parse_sets(pairs: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for pair in pairs:
        key, sep, value = pair.partition("=")
        if not sep:
            raise SystemExit("compose_payloads: --set expects FIELD=VALUE, got %r" % pair)
        out[key.strip()] = value
    return out


def compose(args: argparse.Namespace) -> tuple[list[Field], str]:
    composer = COMPOSERS.get(args.kind)
    if composer is None:
        raise Refusal("no composer for --kind %s (supported: %s)" % (
            args.kind, ", ".join(sorted(COMPOSERS))))
    dossier_text = read_text(args.dossier)
    fm = front_matter(dossier_text)
    ctx = {
        "root": os.path.abspath(args.root),
        "dossier_text": dossier_text,
        "fm": fm,
        "args": args,
        "sets": parse_sets(args.set),
        "host": args.host,
        "plugin_root": os.path.abspath(args.plugin_root),
    }
    fields = composer(ctx)
    missing = missing_after_compose(args.kind, fields)
    if missing:
        raise Refusal("%s still missing after compose: %s (pass --set to fill)" % (
            args.kind, ", ".join(missing)))
    return fields, ctx["root"]


def write_payload(path: str, fields: list[Field], force: bool) -> None:
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            first_line = fh.readline().rstrip("\n")
        if first_line != MARKER and not force:
            raise Refusal(
                "%s exists and is not a previous output of this script "
                "(pass --force to overwrite)" % path
            )
    text = MARKER + "\n" + "".join(render_field(f) for f in fields)
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def manifest_line(args: argparse.Namespace, out_path: str, fields: list[Field]) -> str:
    by_name = {f.name: f for f in fields}
    parts = ["kind=%s" % args.kind, "payload=%s" % os.path.abspath(out_path)]
    if args.kind in ("unit-test-author", "integration-test-author"):
        test_paths = by_name.get("TEST_PATHS")
        if test_paths and not is_placeholder(test_paths.value):
            parts.append("test-paths=%s" % test_paths.value)
    if args.kind == "integration-test-author":
        parts.append("live-dossier=%s" % os.path.abspath(args.dossier))
    return " ".join(parts)


def append_manifest(path: str, line: str) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--kind", choices=sorted(COMPOSERS))
    parser.add_argument("--dossier", help="the run's live dossier")
    parser.add_argument("--root", help="X — the base worktree the payload's paths resolve against")
    parser.add_argument("--host", choices=("opencode", "claude"))
    parser.add_argument("--out", help="where to write the composed payload")
    parser.add_argument("--package", help="the ## Work packages row (implementer)")
    parser.add_argument("--contract-paths", default="",
                        help="comma-separated contract files, absolute or relative to --root")
    parser.add_argument("--test-command")
    parser.add_argument("--hooks")
    parser.add_argument("--set", action="append", default=[], metavar="FIELD=VALUE")
    parser.add_argument("--plugin-root", default=os.path.dirname(SCRIPTS_DIR))
    parser.add_argument("--manifest", help="append this spawn's prepare_wave.py manifest line")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args(argv)

    if args.selftest:
        return selftest()

    missing_cli = [name for name, value in (
        ("--kind", args.kind), ("--dossier", args.dossier), ("--root", args.root),
        ("--host", args.host), ("--out", args.out),
    ) if not value]
    if missing_cli:
        parser.error("%s required unless --selftest" % ", ".join(missing_cli))
    args.contract_paths = [p for p in args.contract_paths.split(",") if p]

    try:
        fields, _root = compose(args)
        write_payload(args.out, fields, args.force)
    except Refusal as err:
        print("compose_payloads: REFUSED — %s" % err, file=sys.stderr)
        return 1

    filled = [f.name for f in fields if not is_placeholder(f.value)]
    left = [f.name for f in fields if is_placeholder(f.value)]
    print("wrote %s" % os.path.abspath(args.out))
    print("filled: %s" % (", ".join(filled) or "none"))
    print("left as @@FILL: %s" % (", ".join(left) or "none"))
    if args.manifest:
        line = manifest_line(args, args.out, fields)
        append_manifest(args.manifest, line)
        print("manifest: %s" % line)
    return 0


# ---------------------------------------------------------------------------
# --selftest
# ---------------------------------------------------------------------------

DOSSIER_FIXTURE = """---
id: W-014
title: Coalesce concurrent flush calls in FlushQueue
status: ready
created: 2026-08-10
updated: 2026-08-10
anchors:
  - src/flush.py:41
baseline_commit: 3f2611f
jira: PROJ-142
branch: fix/PROJ-142-flush-coalescing
worktree: ../repo-W-014
pr: null
blocked_by: []
adrs: []
---

## Problem

Concurrent flush() calls each open their own drain, so queued items reach the
store more than once (src/flush.py:41).

## Approach

Coalesce concurrent flush() calls behind a single drain.

Rejected: a global lock around the whole queue — serialises unrelated callers.

## Contract

see src/flush.py

## Work packages

| Package | Owned paths | Depends on |
|---|---|---|
| P1 flush coalescing | src/flush.py | — |
| P2 reload guard | src/reload.py | P1 |

## Acceptance criteria

1. With 3 parallel flush(batch_size=10) calls and 5 queued items, the store
   receives each item one time. (owner: tests/integration/test_flush_flow.py; env: local)
2. flush() on an empty queue returns 0 and writes nothing. (owner: tests/unit/test_flush.py; env: local)

## Build log

- secret build-log line that must never reach a blind author
"""

CONTRACT_FIXTURE = """class FlushQueue:
    def flush(self, batch_size: int) -> int:
        \"\"\"Drains the queue. Returns the count written.\"\"\"

    def _drain(self) -> None:
        \"\"\"Coalesces concurrent callers behind one drain.\"\"\"
"""


def _run(cmd: list[str]) -> tuple[int, str]:
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    return proc.returncode, proc.stdout + proc.stderr


PLACEHOLDER_FILL = {
    # judgement fields -> a plausible value used to check the FILLED payload
    # lints clean. Path-shaped fields get a real fixture path; everything
    # else gets descriptive text.
    "TEST_COMMAND": "pytest -q",
    "HOOKS": "none",
    "OWNED_PATHS": "src/flush.py",
    "JIRA_KEY": "PROJ-142",
    "PROMISE_CHECKLIST": "flush — return meaning: with 3 items queued, assert the "
                          "return value equals 3",
    "TEST_FRAMEWORK": "pytest; plain assert; run with `pytest tests/unit -q`",
    "CONVENTIONS": "Derive Debug on every constructed type.",
    "VOCABULARY": "drain, coalesce, batch",
    "FIXTURES": "FlushQueue(store=FakeStore())",
    "STYLE_PATHS": "__STYLE_PATH__",
    "STYLE_SAMPLE": "def test_x():\\n    pass",
    "HARNESS": "the app_client fixture stands up the real app",
    "BOUNDARIES": "IClock",
    "CONTEXT_DOCS": "__STYLE_PATH__",
    "SCOPE": "src/flush.py :: FlushQueue.flush (changed)",
    "RUN_EVIDENCE": "3 passed",
    "RULES": "none",
    "CRS": "CR-1: rename x",
    "TEST_PATHS": "__NEW_TEST_PATH__",
}


def _fill_placeholders(text: str, real_style_path: str, new_test_path: str) -> str:
    def repl(match: re.Match) -> str:
        field = match.group(1)
        value = PLACEHOLDER_FILL.get(field, "a plausible value")
        value = value.replace("__STYLE_PATH__", real_style_path).replace("__NEW_TEST_PATH__", new_test_path)
        return value
    return re.sub(r"@@FILL_([A-Z_]+)@@[^\n]*", repl, text)


def selftest() -> int:
    failures: list[str] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        if not ok:
            failures.append("%s%s" % (name, (": " + detail) if detail else ""))

    with tempfile.TemporaryDirectory() as tmp:
        root = os.path.join(tmp, "repo-W-014")
        os.makedirs(os.path.join(root, "src"))
        os.makedirs(os.path.join(root, "tests", "unit"))
        os.makedirs(os.path.join(root, "tests", "integration"))
        with open(os.path.join(root, "src", "flush.py"), "w", encoding="utf-8") as fh:
            fh.write(CONTRACT_FIXTURE)
        style_path = os.path.join(root, "tests", "unit", "test_retry.py")
        with open(style_path, "w", encoding="utf-8") as fh:
            fh.write("def test_retry():\n    pass\n")
        dossier_path = os.path.join(root, "W-014-flush-coalescing.md")
        with open(dossier_path, "w", encoding="utf-8") as fh:
            fh.write(DOSSIER_FIXTURE)
        # a package worktree exists by the time the fan-out spawns (Phase 4
        # forks it before this script runs) — simulate it so WORKTREE_DIR
        # resolves for the lint, exactly as it would in a real run.
        os.makedirs(root + "-P1", exist_ok=True)

        plugin_root = tmp
        os.makedirs(os.path.join(plugin_root, "skills", "standards"), exist_ok=True)
        with open(os.path.join(plugin_root, "skills", "standards", "engineering-standards.md"),
                  "w", encoding="utf-8") as fh:
            fh.write("# engineering standards fixture\n")

        def run_compose(out_name: str, extra: list[str]) -> tuple[int, str, str]:
            out_path = os.path.join(root, ".agent-staging", "payloads", out_name)
            argv = ["--kind", extra[0], "--dossier", dossier_path, "--root", root,
                    "--out", out_path, "--plugin-root", plugin_root] + extra[1:]
            buf_out, buf_err = [], []
            import io, contextlib
            out_io, err_io = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out_io), contextlib.redirect_stderr(err_io):
                rc = main(argv)
            return rc, out_path, out_io.getvalue() + err_io.getvalue()

        # --- implementer, MODE=build, --package P1 ---
        rc, impl_path, out = run_compose("P1.md", [
            "implementer", "--host", "opencode", "--package", "P1",
            "--contract-paths", "src/flush.py",
            "--test-command", "pytest -q", "--hooks", "none",
        ])
        check("implementer compose exit 0", rc == 0, out)
        impl_text = read_text(impl_path) if rc == 0 else ""
        check("implementer WORKTREE_DIR is the P1 worktree",
              ("WORKTREE_DIR: %s-P1" % root) in impl_text)
        check("implementer BRANCH carries the -p1 suffix",
              "BRANCH: fix/PROJ-142-flush-coalescing-p1" in impl_text)
        check("implementer JIRA_KEY verbatim from the dossier", "JIRA_KEY: PROJ-142" in impl_text)
        check("implementer PACKAGE verbatim from the table row",
              "PACKAGE: P1 flush coalescing" in impl_text)
        check("implementer OWNED_PATHS carries the stub-location aid",
              "flush -> src/flush.py:" in impl_text and "_drain -> src/flush.py:" in impl_text)
        check("implementer has no CONTRACT_HASH field (not one of its fields)",
              "CONTRACT_HASH" not in impl_text)
        check("implementer CRITERIA is the acceptance-criteria section verbatim",
              "receives each item one time" in impl_text and "Build log" not in impl_text)
        check("implementer has no @@FILL left (every field was given)",
              "@@FILL_" not in impl_text)
        rc2, out2 = _run([sys.executable, os.path.join(SCRIPTS_DIR, "check_payload.py"),
                          impl_path, "--kind", "implementer", "--host", "opencode",
                          "--worktree", root])
        check("implementer payload lints clean", rc2 == 0, out2)

        # --- unit-test-author, opencode ---
        rc, ut_op_path, out = run_compose("UT1-opencode.md", [
            "unit-test-author", "--host", "opencode", "--contract-paths", "src/flush.py",
        ])
        check("unit-test-author (opencode) compose exit 0", rc == 0, out)
        ut_op_text = read_text(ut_op_path) if rc == 0 else ""
        check("unit-test-author (opencode) uses STYLE_PATHS", "STYLE_PATHS:" in ut_op_text)
        check("unit-test-author (opencode) has judgement fields as @@FILL",
              "@@FILL_PROMISE_CHECKLIST@@" in ut_op_text and "@@FILL_TEST_PATHS@@" in ut_op_text)
        check("unit-test-author (opencode) MAX_SINGLE_EDIT is the fixed constant",
              "MAX_SINGLE_EDIT: 350 lines" in ut_op_text)

        # --- unit-test-author, claude ---
        rc, ut_cl_path, out = run_compose("UT1-claude.md", [
            "unit-test-author", "--host", "claude", "--contract-paths", "src/flush.py",
        ])
        check("unit-test-author (claude) compose exit 0", rc == 0, out)
        ut_cl_text = read_text(ut_cl_path) if rc == 0 else ""
        check("unit-test-author (claude) uses STYLE_SAMPLE, not STYLE_PATHS",
              "STYLE_SAMPLE:" in ut_cl_text and "STYLE_PATHS:" not in ut_cl_text)

        new_test_path = os.path.join(root, "tests", "unit", "test_flush_new.py")
        for label, path, host in (("opencode", ut_op_path, "opencode"), ("claude", ut_cl_path, "claude")):
            filled = _fill_placeholders(read_text(path), style_path, new_test_path)
            filled_path = path + ".filled.md"
            with open(filled_path, "w", encoding="utf-8") as fh:
                fh.write(filled)
            rc2, out2 = _run([sys.executable, os.path.join(SCRIPTS_DIR, "check_payload.py"),
                              filled_path, "--kind", "unit-test-author", "--host", host,
                              "--worktree", root])
            check("unit-test-author (%s) filled payload lints clean" % label, rc2 == 0, out2)

        # --- integration-test-author ---
        rc, it_path, out = run_compose("IT1.md", [
            "integration-test-author", "--host", "claude", "--contract-paths", "src/flush.py",
        ])
        check("integration-test-author compose exit 0", rc == 0, out)
        it_text = read_text(it_path) if rc == 0 else ""
        excerpt_path = os.path.join(root, ".agent-staging", "W-014.excerpt.md")
        check("integration-test-author DOSSIER names the generated excerpt",
              ("DOSSIER: %s" % excerpt_path) in it_text)
        check("integration-test-author excerpt has the three sections, not Build log",
              os.path.isfile(excerpt_path)
              and "## Problem" in read_text(excerpt_path)
              and "## Acceptance criteria" in read_text(excerpt_path)
              and "Build log" not in read_text(excerpt_path))
        filled_it = _fill_placeholders(it_text, style_path, new_test_path)
        filled_it_path = it_path + ".filled.md"
        with open(filled_it_path, "w", encoding="utf-8") as fh:
            fh.write(filled_it)
        rc2, out2 = _run([sys.executable, os.path.join(SCRIPTS_DIR, "check_payload.py"),
                          filled_it_path, "--kind", "integration-test-author", "--host", "claude",
                          "--worktree", root, "--live-dossier", dossier_path])
        check("integration-test-author filled payload lints clean", rc2 == 0, out2)

        # --- reviewer, LENS: plan ---
        rc, rev_path, out = run_compose("R1.md", [
            "reviewer", "--host", "claude", "--set", "LENS=plan",
        ])
        check("reviewer (plan) compose exit 0", rc == 0, out)
        rev_text = read_text(rev_path) if rc == 0 else ""
        plan_copy = os.path.join(root, ".agent-staging", "W-014.plan-copy.md")
        check("reviewer DOSSIER names the build-log-free copy",
              ("DOSSIER: %s" % plan_copy) in rev_text)
        check("reviewer plan copy excises Build log",
              os.path.isfile(plan_copy) and "Build log" not in read_text(plan_copy)
              and "## Problem" in read_text(plan_copy))
        check("reviewer CRITERIA is verbatim, mechanical",
              "receives each item one time" in rev_text)
        check("reviewer has @@FILL for CONTEXT_DOCS (judgement)",
              "@@FILL_CONTEXT_DOCS@@" in rev_text)
        filled_rev = _fill_placeholders(rev_text, style_path, new_test_path)
        filled_rev_path = rev_path + ".filled.md"
        with open(filled_rev_path, "w", encoding="utf-8") as fh:
            fh.write(filled_rev)
        rc2, out2 = _run([sys.executable, os.path.join(SCRIPTS_DIR, "check_payload.py"),
                          filled_rev_path, "--kind", "reviewer", "--host", "claude",
                          "--worktree", root])
        check("reviewer filled payload lints clean", rc2 == 0, out2)

        # --- refusals ---
        rc, out = main(["--kind", "implementer", "--dossier", dossier_path, "--root", root,
                        "--host", "opencode", "--out", os.path.join(root, "bad.md"),
                        "--package", "P9", "--contract-paths", "src/flush.py"]), ""
        check("implementer refuses an unknown --package", rc == 1)
        rc, out = main(["--kind", "implementer", "--dossier", dossier_path, "--root", root,
                        "--host", "opencode", "--out", os.path.join(root, "bad2.md"),
                        "--package", "P1", "--contract-paths", "src/absent.py"]), ""
        check("implementer refuses a missing contract file", rc == 1)
        rc, out = main(["--kind", "reviewer", "--dossier", dossier_path, "--root", root,
                        "--host", "claude", "--out", os.path.join(root, "bad3.md")]), ""
        check("reviewer refuses with no --set LENS", rc == 1)

        # --- overwrite guard ---
        with open(os.path.join(root, "handwritten.md"), "w", encoding="utf-8") as fh:
            fh.write("WORKTREE_DIR: /x\n")
        rc, out = main(["--kind", "implementer", "--dossier", dossier_path, "--root", root,
                        "--host", "opencode", "--out", os.path.join(root, "handwritten.md"),
                        "--package", "P1", "--contract-paths", "src/flush.py",
                        "--test-command", "pytest -q", "--hooks", "none"]), ""
        check("refuses to overwrite a file this script did not generate", rc == 1)
        rc = main(["--kind", "implementer", "--dossier", dossier_path, "--root", root,
                  "--host", "opencode", "--out", os.path.join(root, "handwritten.md"),
                  "--package", "P1", "--contract-paths", "src/flush.py",
                  "--test-command", "pytest -q", "--hooks", "none", "--force"])
        check("--force allows the overwrite", rc == 0)

        # --- manifest ---
        manifest_path = os.path.join(root, "wave.txt")
        rc = main(["--kind", "implementer", "--dossier", dossier_path, "--root", root,
                  "--host", "opencode", "--out", impl_path,
                  "--package", "P1", "--contract-paths", "src/flush.py",
                  "--test-command", "pytest -q", "--hooks", "none",
                  "--force", "--manifest", manifest_path])
        check("manifest write exit 0", rc == 0)
        check("manifest line names kind and payload",
              os.path.isfile(manifest_path)
              and "kind=implementer" in read_text(manifest_path)
              and ("payload=%s" % impl_path) in read_text(manifest_path))

    for item in failures:
        print("SELFTEST FAIL  %s" % item)
    print("selftest: %d failure(s)" % len(failures))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
