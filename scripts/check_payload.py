#!/usr/bin/env python3
"""Lint a spawn payload before it reaches an agent.

Four failure modes this catches, each of which cost a real build:

1. **A misnamed or missing field.** `references/payloads.md` names a misnamed
   field the likeliest silent failure in the pipeline: an agent halts on a
   MISSING field only when it cannot proceed without it, and a MISNAMED field
   is simply ignored. A misspelled `OWNED_PATHS` is the worst case, because the
   agent then writes wherever it likes and corrupts a concurrent agent's work.
   A field left out is just as silent: a unit author shipped with no style
   sample and the run paid a `GAP:` return for it.

2. **A path that is not there.** An agent told to read a path that does not
   exist either halts or goes looking. A build twice told an agent to read
   credentials from a gitignored file "in your worktree root"; a fresh worktree
   never carries one, the agent searched sibling checkouts, and printed a live
   credential into its report.

3. **An unexpanded `${PLUGIN_ROOT}`.** The skeletons write it for brevity; an
   agent cannot resolve a variable it was never given, so what ships must be
   the expanded absolute path.

4. **A credential in the payload at all.** A verification that needs a secret
   is the orchestrator's to run. A literal password in a payload is a defect
   even when it is correct, because the payload lands in a transcript on disk.

5. **A dossier that is not the run's live copy.** `.discovery/` has two modes
   (references/formats.md § "Two modes"); in both, the dossier an agent may
   read is the one inside the tree the payload names as `WORKTREE_DIR`. A
   `DOSSIER` inside some other checkout's `.discovery/` is the stale copy by
   design — the main checkout's, while the live one sits in the worktree.

6. **A command verb that contradicts the script's shebang.** `TEST_COMMAND:
   python3 scripts/check.sh` names an existing file, so every other check
   passes — and the command dies at its first run. When a command field
   names a script that carries a shebang, the verb must be the same
   interpreter family; the recorded payload wrote the verb from memory
   instead of copying the verified invocation.

7. **A checklist that names a surface the author cannot see** (WARNING). A
   `PROMISE_CHECKLIST` line whose assertion constructs or calls a type found
   in neither `CONTRACT` nor `SUPPORT_PATHS` (nor the payload's own
   `FIXTURES` and `SHARED_IDIOM`) is a `GAP:` waiting to happen: a blind
   author cannot invent `RepoGrant::mint` from a docstring that names it. The
   recorded run paid three round trips before the surface was staged.

A field name may carry a parenthetical — `CRITERIA (15 verbatim): |`,
`RULES (narrowed per lens): |` — and lints as the bare name. Authors
annotate naturally, and the annotation is harmless; a genuinely misnamed
field (`RULE:` for `RULES:`) is still refused.

# Usage

    check_payload.py PAYLOAD_FILE --kind implementer [--worktree DIR]
                     [--allow-path PATH ...]
    check_payload.py --selftest

`--kind` is one of the agent kinds in FIELDS. Omit it and the field checks are
skipped; the path, variable and credential checks still run.

Exit codes: 0 clean; 1 at least one defect; 2 the inputs could not be read.

No third-party imports: this runs wherever python3 does.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import tempfile

# Required and optional fields per agent kind, derived from the skeletons in
# references/payloads.md. A skeleton says "fill every line, delete nothing", so
# every field it shows is required unless its own text says "omit when".
# Mode- and lens-dependent requirements live in CONDITIONAL below.
FIELDS: dict[str, dict[str, set[str]]] = {
    "unit-test-author": {
        "required": {
            "CONTRACT", "PROMISE_CHECKLIST", "TEST_PATHS", "TEST_FRAMEWORK",
            "MAX_SINGLE_EDIT", "CITATION", "CONVENTIONS", "STYLE_PATHS",
            "NAMING", "VOCABULARY", "FIXTURES", "CONTRACT_HASH",
        },
        "optional": {"SHARED_IDIOM", "SUPPORT_PATHS", "DELIVERY_CHANGE"},
    },
    "integration-test-author": {
        "required": {
            "WORKTREE_DIR", "DOSSIER", "CONTRACT", "TEST_PATHS",
            "TEST_FRAMEWORK", "HARNESS", "STYLE_SAMPLE", "BOUNDARIES",
            "CONTRACT_HASH",
        },
        "optional": {"SHARED_IDIOM", "DELIVERY_CHANGE"},
    },
    "implementer": {
        "required": {
            "WORKTREE_DIR", "BRANCH", "MODE", "CONTRACT", "OWNED_PATHS",
            "TEST_COMMAND", "STANDARDS", "JIRA_KEY", "HOOKS",
        },
        "optional": {
            "PACKAGE", "CRITERIA", "CRS", "FAILURES", "SHARED_IDIOM",
            "VERIFY_EMBEDDED",
        },
    },
    "reviewer": {
        "required": {"LENS", "WORKTREE_DIR", "STANDARDS", "ROUND"},
        "optional": {
            "DOSSIER", "SCOPE", "CONTRACT", "RUN_EVIDENCE", "CRITERIA",
            "CONTEXT_DOCS", "ARBITRATIONS", "PRIOR_CRS", "RULES",
        },
    },
    "contract-reviewer": {
        "required": {"WORKTREE_DIR", "CONTRACT_PATHS", "CRITERIA", "CHECKLIST_RULES"},
        "optional": set(),
    },
    "stub-materialiser": {
        "required": {"WORKTREE_DIR", "CONTRACT", "OWNED_PATHS", "STUB_STYLE", "BUILD_CHECK"},
        "optional": set(),
    },
    "blast-radius-scout": {
        "required": {"WORKTREE_DIR", "BASELINE", "HEAD", "HINT"},
        "optional": set(),
    },
    "document-drafter": {
        "required": {"MODE", "FORMAT", "TARGET_PATHS", "SCRUB"},
        "optional": {"DECISIONS", "DOSSIER-EXCERPTS"},
    },
}

# (kind, discriminator field, value) -> (fields that become required,
#                                        groups where at least one is required)
CONDITIONAL: dict[tuple[str, str, str], tuple[set[str], list[set[str]]]] = {
    ("implementer", "MODE", "build"): ({"PACKAGE", "CRITERIA"}, []),
    ("implementer", "MODE", "fix"): (set(), [{"CRS", "FAILURES"}]),
    ("reviewer", "LENS", "plan"): ({"DOSSIER", "CRITERIA", "CONTEXT_DOCS"}, []),
    ("reviewer", "LENS", "style"): (
        {"SCOPE", "CONTRACT", "RUN_EVIDENCE", "CRITERIA", "CONTEXT_DOCS", "ARBITRATIONS",
         "RULES"}, []),
    ("reviewer", "LENS", "architecture"): (
        {"SCOPE", "CONTRACT", "RUN_EVIDENCE", "CRITERIA", "CONTEXT_DOCS", "ARBITRATIONS",
         "RULES"}, []),
    ("reviewer", "LENS", "performance"): (
        {"SCOPE", "CONTRACT", "RUN_EVIDENCE", "CRITERIA", "CONTEXT_DOCS", "ARBITRATIONS",
         "RULES"}, []),
    ("document-drafter", "MODE", "adr"): ({"DECISIONS"}, []),
    ("document-drafter", "MODE", "pr"): ({"DOSSIER-EXCERPTS"}, []),
}

# A field line at the start of a line: NAME: value  /  NAME: |  — and the
# annotated forms NAME (note): value / NAME(note): |, which lint as NAME.
FIELD_LINE = re.compile(
    r"^(?P<name>[A-Z][A-Z0-9_-]{2,})(?:\s*\([^)]*\))?:(?P<rest>\s.*|\s*\|\s*)?$"
)
# A block field: NAME: | — every following line that is indented or blank is
# the block's body, never a field, however it is shaped (`CR-1:` in a CRS
# block, a `TODO:` in a pasted contract). A line at column 0 ends the block.
BLOCK_OPEN = re.compile(r"^[A-Z][A-Z0-9_-]{2,}(?:\s*\([^)]*\))?:\s*\|\s*$")


def field_lines(lines: list[str]) -> list[tuple[int, str, str]]:
    """Yield (line number, field name, rest) for every top-level field line."""
    found: list[tuple[int, str, str]] = []
    in_block = False
    for number, line in enumerate(lines, 1):
        if in_block:
            if line.strip() == "" or line[:1] in (" ", "\t"):
                continue
            in_block = False
        match = FIELD_LINE.match(line)
        if not match:
            continue
        found.append((number, match.group("name"), (match.group("rest") or "").strip()))
        if BLOCK_OPEN.match(line):
            in_block = True
    return found

# An absolute path. Kept deliberately narrow: two or more word-bearing segments.
ABS_PATH = re.compile(r"(?<![\w/$}])(/[\w.@+-]+(?:/[\w.@+-]+)+)")

UNEXPANDED = re.compile(r"\$\{[A-Z_][A-Z0-9_]*\}")

SECRET_NAME = re.compile(
    r"\b(pass|passwd|password|secret|token|api[_-]?key|credential)\b", re.IGNORECASE
)
# A value that is obviously not a live secret.
PLACEHOLDER = re.compile(
    r"^(|<.*>|\*+|x+|placeholder|redacted|none|null|changeme|\$\{.*\}|.*-placeholder)$",
    re.IGNORECASE,
)
ASSIGNMENT = re.compile(r"\b(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*[:=]\s*(?P<value>\S+)")

# A credential inside a connection URL: scheme://user:secret@host. No field name
# on such a line says "password", so the name-based check cannot see it.
URL_CREDENTIAL = re.compile(
    r"\b[a-zA-Z][a-zA-Z0-9+.-]*://(?P<user>[^:/@\s]+):(?P<secret>[^@/\s]+)@"
)

# Paths a payload legitimately names that will not exist yet: a path the agent
# is told to CREATE, or the drafter's target files.
CREATE_HINT = re.compile(r"\b(TEST_PATHS|OWNED_PATHS|TARGET_PATHS)\b")

# Fields that carry a command the agent will run verbatim.
COMMAND_FIELDS = ("TEST_COMMAND", "BUILD_CHECK")
# Interpreter verb -> family. A verb outside this table (pytest, cargo, make)
# is a runner, not an interpreter, and the check does not apply.
VERB_FAMILY = {
    "python": "python", "python2": "python", "python3": "python", "pypy": "python",
    "pypy3": "python",
    "bash": "shell", "sh": "shell", "zsh": "shell", "dash": "shell", "ksh": "shell",
    "node": "node", "nodejs": "node", "deno": "node", "bun": "node",
    "ruby": "ruby", "perl": "perl",
}
ENV_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")


def interpreter_family(token: str) -> str | None:
    """`/usr/bin/python3.12` -> python; `bash` -> shell; `pytest` -> None."""
    name = os.path.basename(token.strip("\"'"))
    return VERB_FAMILY.get(name) or VERB_FAMILY.get(re.sub(r"[\d.]+$", "", name))


def shebang_family(path: str) -> tuple[str | None, str]:
    """(family, shebang line) of a script, or (None, '') when it has none."""
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            first = handle.readline().rstrip("\n")
    except OSError:
        return None, ""
    if not first.startswith("#!"):
        return None, ""
    tokens = first[2:].split()
    if not tokens:
        return None, first
    interpreter = tokens[0]
    if os.path.basename(interpreter) == "env":
        rest = [t for t in tokens[1:] if not t.startswith("-") and not ENV_ASSIGNMENT.match(t)]
        interpreter = rest[0] if rest else ""
    return interpreter_family(interpreter), first


def field_body(lines: list[str], name: str) -> str:
    """A field's whole text: its inline value, or every line of its block."""
    for number, found, rest in field_lines(lines):
        if found != name:
            continue
        if rest and rest != "|":
            return rest
        body: list[str] = []
        for line in lines[number:]:
            if line.strip() and line[:1] not in (" ", "\t"):
                break
            body.append(line)
        return "\n".join(body)
    return ""


# A type a checklist line constructs or calls: `RepoGrant::mint`, `TrackId.new(`,
# `CredentialRef(`, or any CamelCase word of two or more humps.
TYPE_REF = re.compile(r"\b([A-Z][A-Za-z0-9]*)(?=::|\.[a-z_]\w*\(|\()")
CAMEL = re.compile(r"\b[A-Z][a-z0-9]+(?:[A-Z][a-z0-9]+)+\b")
# Words a checklist line uses as prose or as the language's own vocabulary.
NOT_A_SURFACE = {
    "Returns", "Raises", "Drains", "With", "The", "None", "Some", "Ok", "Err",
    "True", "False", "Result", "Option", "Vec", "String", "Self", "Box", "Arc",
    "Rc", "HashMap", "BTreeMap", "BTreeSet", "HashSet", "Debug", "Clone",
    "PartialEq", "Eq", "Default", "Send", "Sync", "Task", "Exception", "Error",
}


def surface_findings(lines: list[str], kind: str | None) -> list[str]:
    """Checklist types the unit author's world does not carry (a WARNING)."""
    if kind != "unit-test-author":
        return []
    checklist = field_body(lines, "PROMISE_CHECKLIST")
    if not checklist:
        return []
    known: list[str] = []
    for field in ("CONTRACT", "SUPPORT_PATHS", "FIXTURES", "SHARED_IDIOM"):
        body = field_body(lines, field)
        known.append(body)
        for token in body.split():
            path = token.strip(",;")
            if os.path.isabs(path) and os.path.isfile(path):
                try:
                    with open(path, encoding="utf-8", errors="replace") as handle:
                        known.append(handle.read())
                except OSError:
                    continue
    world = "\n".join(known)
    names = set(TYPE_REF.findall(checklist)) | set(CAMEL.findall(checklist))
    missing = sorted(
        n for n in names
        if n not in NOT_A_SURFACE and not re.search(r"\b%s\b" % re.escape(n), world)
    )
    if not missing:
        return []
    return [
        "PROMISE_CHECKLIST names %s, found in neither CONTRACT nor SUPPORT_PATHS "
        "(nor FIXTURES, SHARED_IDIOM). A blind author cannot invent a type's API: "
        "stage its signature surface under .agent-staging/contract-support/ and "
        "name it in SUPPORT_PATHS, or the spawn returns GAP:." % ", ".join(missing)
    ]


def field_command(lines: list[str], name: str) -> tuple[int, str]:
    """(line number, command text) of a command field — its inline value, or
    the first body line of a `NAME: |` block. (0, '') when absent."""
    for number, found, rest in field_lines(lines):
        if found != name:
            continue
        if rest and rest != "|":
            return number, rest.split("#", 1)[0].strip()
        for offset, line in enumerate(lines[number:], number + 1):
            if line.strip():
                return offset, line.strip().split("#", 1)[0].strip()
        return number, ""
    return 0, ""


def verb_findings(lines: list[str], worktree: str | None) -> list[str]:
    """A command field whose verb contradicts the named script's shebang."""
    out: list[str] = []
    for field in COMMAND_FIELDS:
        number, command = field_command(lines, field)
        if not command:
            continue
        tokens = [t for t in command.split() if not ENV_ASSIGNMENT.match(t)]
        if tokens and os.path.basename(tokens[0]) == "env":
            tokens = tokens[1:]
        if not tokens:
            continue
        verb_family = interpreter_family(tokens[0])
        if verb_family is None:
            continue
        for token in tokens[1:]:
            if token.startswith("-") or not ("/" in token or re.search(r"\.\w+$", token)):
                continue
            candidates = [token] if os.path.isabs(token) else [
                os.path.join(worktree, token) if worktree else token
            ]
            path = next((c for c in candidates if os.path.isfile(c)), None)
            if path is None:
                continue
            family, shebang = shebang_family(path)
            if family is None or family == verb_family:
                continue
            out.append(
                "line %d: %s runs %s with %r, but its shebang says %r (%s). A verb "
                "that contradicts the shebang lints clean and dies at run time; "
                "copy the verified invocation." % (number, field, token, tokens[0], shebang, family)
            )
            break
    return out


def read_lines(path: str) -> list[str]:
    try:
        with open(path, encoding="utf-8") as handle:
            return handle.read().splitlines()
    except OSError as err:
        print("check_payload: cannot read %s: %s" % (path, err), file=sys.stderr)
        raise SystemExit(2)


def field_value(lines: list[str], name: str) -> str:
    for _, found, rest in field_lines(lines):
        if found == name:
            return rest.split()[0] if rest else ""
    return ""


def lint(lines: list[str], kind: str | None, worktree: str | None,
         allow_paths: list[str]) -> tuple[list[str], list[str], list[str]]:
    defects: list[str] = []
    warnings: list[str] = []

    # 1. field names — misnamed, missing, and mode/lens-conditional
    present: list[str] = []
    for number, name, _rest in field_lines(lines):
        present.append(name)
        if kind is None:
            continue
        known = FIELDS[kind]["required"] | FIELDS[kind]["optional"]
        if name not in known:
            near = sorted(
                f for f in known
                if f[:4] == name[:4] or f.replace("_", "") == name.replace("_", "")
            )
            hint = (" did you mean %s?" % ", ".join(near)) if near else ""
            defects.append(
                "line %d: %r is not a field of %s.%s "
                "A misnamed field is IGNORED, never refused."
                % (number, name, kind, hint)
            )
    if kind is not None:
        required = set(FIELDS[kind]["required"])
        groups: list[set[str]] = []
        for (k, disc, value), (more, any_of) in CONDITIONAL.items():
            if k == kind and field_value(lines, disc) == value:
                required |= more
                groups.extend(any_of)
        for name in sorted(required - set(present)):
            defects.append(
                "%s is missing from this %s payload. A field left out is silent: "
                "the agent halts only when it cannot proceed, and otherwise "
                "guesses." % (name, kind)
            )
        for group in groups:
            if not group & set(present):
                defects.append(
                    "one of %s is required in this %s payload and none is present."
                    % (" / ".join(sorted(group)), kind)
                )
        for disc in ("MODE", "LENS"):
            wanted = {v for (k, d, v) in CONDITIONAL if k == kind and d == disc}
            if wanted and disc in present and field_value(lines, disc) not in wanted:
                defects.append(
                    "%s is %r; a %s payload takes one of %s."
                    % (disc, field_value(lines, disc), kind, ", ".join(sorted(wanted)))
                )

    # 2. unexpanded variables
    for number, line in enumerate(lines, 1):
        for match in UNEXPANDED.finditer(line):
            defects.append(
                "line %d: %s is unexpanded. An agent cannot resolve a variable it "
                "was never given; ship the absolute path." % (number, match.group(0))
            )

    # 3. paths that do not exist
    owned_lines = {
        number for number, line in enumerate(lines, 1) if CREATE_HINT.search(line)
    }
    allowed = set(allow_paths)
    allowed_used: set[str] = set()
    for number, line in enumerate(lines, 1):
        if number in owned_lines:
            continue  # a path the agent is told to create
        for match in ABS_PATH.finditer(line):
            path = match.group(1)
            if os.path.exists(path):
                continue
            if worktree and os.path.exists(os.path.join(worktree, path.lstrip("/"))):
                continue
            if path in allowed:
                allowed_used.add(path)
                continue
            defects.append(
                "line %d: the path %s does not exist. An agent told to read a "
                "path that is not there either halts or goes looking." % (number, path)
            )
    for stale in sorted(allowed - allowed_used):
        defects.append(
            "--allow-path %s matched no missing path in this payload. A stale "
            "exemption hides the next real one." % stale
        )

    # 4. credential literals
    for number, line in enumerate(lines, 1):
        for match in URL_CREDENTIAL.finditer(line):
            secret = match.group("secret")
            if PLACEHOLDER.match(secret) or secret.startswith("$"):
                continue
            defects.append(
                "line %d: a connection URL carries a password for user %r. A "
                "payload never carries a live secret, and a URL hides one from "
                "any check that keys on a field name." % (number, match.group("user"))
            )
        if not SECRET_NAME.search(line):
            continue
        for match in ASSIGNMENT.finditer(line):
            name, value = match.group("name"), match.group("value")
            if not SECRET_NAME.search(name):
                continue
            value = value.strip("\"'`,;")
            if PLACEHOLDER.match(value):
                continue
            defects.append(
                "line %d: %s carries what looks like a live secret. A payload "
                "never carries one: run the step that needs it yourself." % (number, name)
            )

    # 5. a command verb that contradicts the script's shebang
    defects.extend(verb_findings(lines, worktree))

    # 7. a checklist surface the unit author's world does not carry
    warnings.extend(surface_findings(lines, kind))

    # 6. a dossier path that is not the run's live copy
    dossier, worktree_dir = field_value(lines, "DOSSIER"), field_value(lines, "WORKTREE_DIR")
    if (
        dossier
        and worktree_dir
        and "/.discovery/" in dossier
        and not dossier.startswith(worktree_dir.rstrip("/") + "/")
    ):
        defects.append(
            "DOSSIER %s sits in a .discovery/ that is not WORKTREE_DIR's (%s). The "
            "live dossier is the copy inside the run's own tree; another checkout's "
            "copy is stale by design." % (dossier, worktree_dir)
        )

    return present, defects, warnings


def report(payload: str, kind: str | None, present: list[str],
           defects: list[str], warnings: list[str]) -> int:
    print("payload lint: %s" % payload)
    if kind:
        print("  kind: %s, %d field(s) present" % (kind, len(present)))
    for item in defects:
        print("  DEFECT   %s" % item)
    for item in warnings:
        print("  WARNING  %s" % item)
    print("%s — %d defect(s), %d warning(s)"
          % ("FAIL" if defects else "PASS", len(defects), len(warnings)))
    return 1 if defects else 0


def selftest() -> int:
    failures: list[str] = []

    def case(name: str, text: str, kind: str | None, expect_defect: bool,
             must_mention: str = "", must_warn: str | None = None) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            real = os.path.join(tmp, "repo-W-014")
            os.makedirs(os.path.join(real, "tests", "unit"))
            os.makedirs(os.path.join(real, ".discovery", "dossiers"))
            os.makedirs(os.path.join(real, "scripts"))
            os.makedirs(os.path.join(real, ".agent-staging", "contract-support"))
            with open(os.path.join(real, ".agent-staging", "contract-support", "grant.rs"), "w") as fh:
                fh.write("pub struct RepoGrant;\nimpl RepoGrant { pub fn mint() {} }\n")
            with open(os.path.join(real, "scripts", "check.sh"), "w") as fh:
                fh.write("#!/usr/bin/env bash\necho ok\n")
            with open(os.path.join(real, "scripts", "check.py"), "w") as fh:
                fh.write("#!/usr/bin/env python3\nprint('ok')\n")
            with open(os.path.join(real, "tests", "unit", "test_retry.py"), "w") as fh:
                fh.write("def test_x(): pass\n")
            with open(os.path.join(real, ".discovery", "dossiers", "W-014-x.md"), "w") as fh:
                fh.write("---\nid: W-014\n---\n")
            body = text.replace("{REAL}", real)
            _, defects, warnings = lint(body.splitlines(), kind, None, [])
            got = bool(defects)
            if got != expect_defect:
                failures.append("%s: expected defect=%s, got %s: %s"
                                % (name, expect_defect, got, defects))
            elif must_mention and not any(must_mention in d for d in defects):
                failures.append("%s: no defect mentions %r: %s" % (name, must_mention, defects))
            if must_warn is not None:
                if must_warn == "" and warnings:
                    failures.append("%s: expected no warning, got %s" % (name, warnings))
                elif must_warn and not any(must_warn in w for w in warnings):
                    failures.append("%s: no warning mentions %r: %s" % (name, must_warn, warnings))

    unit_ok = (
        "CONTRACT: {REAL}/tests/unit/test_retry.py\n"
        "PROMISE_CHECKLIST: |\n  flush — return meaning: count\n"
        "TEST_PATHS: {REAL}/tests/unit/test_flush.py\n"
        "TEST_FRAMEWORK: pytest\nMAX_SINGLE_EDIT: 350 lines\nCITATION: |\n  // promise: x\n"
        "CONVENTIONS: |\n  Derive Debug\nSTYLE_PATHS: {REAL}/tests/unit/test_retry.py\n"
        "NAMING: Subject_State_Expected\nVOCABULARY: drain\nFIXTURES: |\n  FlushQueue()\n"
        "CONTRACT_HASH: 3f2611f0a91c4d8e\n"
    )
    case("unit author clean", unit_ok, "unit-test-author", False)
    case("unit author missing checklist",
         unit_ok.replace("PROMISE_CHECKLIST: |\n  flush — return meaning: count\n", ""),
         "unit-test-author", True, "PROMISE_CHECKLIST is missing")
    case("unit author optional shared idiom",
         unit_ok + "SHARED_IDIOM: |\n  fn make() -> Item\n", "unit-test-author", False)
    case("misnamed field",
         unit_ok.replace("TEST_PATHS:", "TEST_PATH:"), "unit-test-author", True,
         "'TEST_PATH' is not a field")
    case("missing path",
         unit_ok.replace("STYLE_PATHS: {REAL}/tests/unit/test_retry.py",
                         "STYLE_PATHS: {REAL}/tests/unit/absent.py"),
         "unit-test-author", True, "does not exist")
    case("unexpanded variable",
         unit_ok + "STANDARDS: ${PLUGIN_ROOT}/skills/standards/engineering-standards.md\n",
         "unit-test-author", True, "unexpanded")
    case("url credential", unit_ok + "FIXTURES: |\n  postgres://app:hunter2@db/x\n",
         "unit-test-author", True, "carries a password")
    case("secret placeholder ok", unit_ok + "FIXTURES: |\n  password: <redacted>\n",
         "unit-test-author", False)
    case("checklist names only contract members: quiet", unit_ok, "unit-test-author", False,
         must_warn="")
    grant = unit_ok.replace("PROMISE_CHECKLIST: |\n  flush — return meaning: count\n",
                            "PROMISE_CHECKLIST: |\n  flush — invalid case: RepoGrant::mint(bad) raises\n")
    case("checklist names an unstaged surface: warns", grant, "unit-test-author", False,
         must_warn="RepoGrant")
    case("staged support surface silences the warning",
         grant + "SUPPORT_PATHS: {REAL}/.agent-staging/contract-support/grant.rs\n",
         "unit-test-author", False, must_warn="")
    case("a delivery change is a legal resume field",
         unit_ok + "DELIVERY_CHANGE: |\n  land the file as one Write plus Edits under the cap\n",
         "unit-test-author", False)

    impl = (
        "WORKTREE_DIR: {REAL}\nBRANCH: fix/x-p1\nMODE: build\nCONTRACT: |\n  fn a()\n"
        "PACKAGE: P1 thing\nOWNED_PATHS: src/flush.py\nCRITERIA: |\n  1. x\n"
        "TEST_COMMAND: pytest -q\nHOOKS: none\nSTANDARDS: {REAL}/tests/unit/test_retry.py\nJIRA_KEY: PROJ-1\n"
    )
    case("implementer build clean", impl, "implementer", False)
    case("implementer hooks required",
         impl.replace("HOOKS: none\n", ""), "implementer", True, "HOOKS is missing")
    case("implementer block body is not a field",
         impl.replace("MODE: build", "MODE: fix").replace("PACKAGE: P1 thing\n", "")
         .replace("CRITERIA: |\n  1. x\n", "CRS: |\n  CR-1: rename x\n  TODO: y\n\n  CR-2: hoist z\n"),
         "implementer", False)
    case("implementer build missing package",
         impl.replace("PACKAGE: P1 thing\n", ""), "implementer", True, "PACKAGE is missing")
    fix = impl.replace("MODE: build", "MODE: fix").replace("PACKAGE: P1 thing\n", "").replace("CRITERIA: |\n  1. x\n", "")
    case("implementer fix needs crs or failures", fix, "implementer", True, "CRS / FAILURES")
    case("implementer fix with failures", fix + "FAILURES: |\n  assert x\n", "implementer", False)
    case("implementer bad mode", impl.replace("MODE: build", "MODE: repair"), "implementer", True, "takes one of")
    case("implementer misnamed owned paths",
         impl.replace("OWNED_PATHS:", "OWNED_PATH:"), "implementer", True, "did you mean OWNED_PATHS")
    case("test command verb contradicts a bash shebang",
         impl.replace("TEST_COMMAND: pytest -q", "TEST_COMMAND: python3 {REAL}/scripts/check.sh"),
         "implementer", True, "shebang says")
    case("test command verb matches the shebang",
         impl.replace("TEST_COMMAND: pytest -q", "TEST_COMMAND: bash {REAL}/scripts/check.sh --fast"),
         "implementer", False)
    case("test command bash verb over a python script",
         impl.replace("TEST_COMMAND: pytest -q", "TEST_COMMAND: RUST_LOG=x sh {REAL}/scripts/check.py"),
         "implementer", True, "shebang says")
    case("test command as a block field",
         impl.replace("TEST_COMMAND: pytest -q", "TEST_COMMAND: |\n  python3 {REAL}/scripts/check.sh"),
         "implementer", True, "shebang says")

    plan = (
        "LENS: plan\nDOSSIER: {REAL}/tests/unit/test_retry.py\nWORKTREE_DIR: {REAL}\n"
        "CRITERIA: |\n  1. x\nSTANDARDS: {REAL}/tests/unit/test_retry.py\n"
        "CONTEXT_DOCS: {REAL}/tests/unit/test_retry.py\nROUND: 1\n"
    )
    case("reviewer plan clean", plan, "reviewer", False)
    case("reviewer style needs scope",
         plan.replace("LENS: plan", "LENS: style"), "reviewer", True, "SCOPE is missing")

    style = (
        "LENS: style\nWORKTREE_DIR: {REAL}\nSCOPE: |\n  src/flush.py :: flush (changed)\n"
        "CONTRACT: |\n  fn flush()\nRUN_EVIDENCE: |\n  3 passed\nCRITERIA: |\n  1. x\n"
        "STANDARDS: {REAL}/tests/unit/test_retry.py\n"
        "RULES: |\n  #### Resource Management\n  Prefer `with` over manual close.\n"
        "CONTEXT_DOCS: {REAL}/tests/unit/test_retry.py\nARBITRATIONS: none\nROUND: 1\n"
    )
    case("reviewer style clean", style, "reviewer", False)
    case("reviewer style needs rules",
         style.replace("RULES: |\n  #### Resource Management\n  Prefer `with` over manual close.\n", ""),
         "reviewer", True, "RULES is missing")
    case("a parenthetical after the field name lints as the field",
         style.replace("RULES: |", "RULES (narrowed per lens): |")
         .replace("CRITERIA: |", "CRITERIA(3 verbatim): |"),
         "reviewer", False)
    case("a misnamed field is still refused beside a parenthetical",
         style.replace("RULES: |", "RULE (narrowed per lens): |"),
         "reviewer", True, "'RULE' is not a field")
    case("reviewer plan takes no rules",
         plan + "RULES: none\n", "reviewer", False)
    live = plan.replace("DOSSIER: {REAL}/tests/unit/test_retry.py",
                        "DOSSIER: {REAL}/.discovery/dossiers/W-014-x.md")
    case("reviewer plan dossier inside its worktree", live, "reviewer", False)
    case("reviewer plan dossier from another checkout",
         live.replace("WORKTREE_DIR: {REAL}\n", "WORKTREE_DIR: {REAL}/tests\n"),
         "reviewer", True, "not WORKTREE_DIR's")

    drafter = (
        "MODE: pr\nDOSSIER-EXCERPTS: |\n  ## Problem\nFORMAT: |\n  shape\n"
        "TARGET_PATHS: {REAL}/.discovery/pr-draft.md\nSCRUB: W-014\n"
    )
    case("drafter pr clean, hyphen field accepted", drafter, "document-drafter", False)
    case("drafter adr needs decisions",
         drafter.replace("MODE: pr", "MODE: adr"), "document-drafter", True, "DECISIONS is missing")
    case("no kind still checks paths", "X: /no/such/place/here\n", None, True, "does not exist")

    for item in failures:
        print("SELFTEST FAIL  %s" % item)
    print("selftest: %d case group(s), %d failure(s)" % (38, len(failures)))
    return 1 if failures else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("payload", nargs="?")
    parser.add_argument("--kind", choices=sorted(FIELDS), default=None)
    parser.add_argument("--worktree", default=None,
                        help="resolve a relative path against this directory as well")
    parser.add_argument(
        "--allow-path", action="append", default=[], metavar="PATH",
        help="a path that exists somewhere this checker cannot reach (inside a VM "
        "or a container image) and that the orchestrator has verified by listing "
        "it there. Repeatable. An --allow-path that matches no path in the payload "
        "is itself a defect, so a stale exemption cannot linger.",
    )
    parser.add_argument("--selftest", action="store_true", help="check the checker")
    args = parser.parse_args()

    if args.selftest:
        return selftest()
    if not args.payload:
        parser.error("PAYLOAD_FILE is required unless --selftest")

    lines = read_lines(args.payload)
    present, defects, warnings = lint(lines, args.kind, args.worktree, args.allow_path)
    return report(args.payload, args.kind, present, defects, warnings)


if __name__ == "__main__":
    sys.exit(main())
