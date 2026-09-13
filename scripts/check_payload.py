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
        "optional": {"SHARED_IDIOM"},
    },
    "integration-test-author": {
        "required": {
            "WORKTREE_DIR", "DOSSIER", "CONTRACT", "TEST_PATHS",
            "TEST_FRAMEWORK", "HARNESS", "STYLE_SAMPLE", "BOUNDARIES",
            "CONTRACT_HASH",
        },
        "optional": {"SHARED_IDIOM"},
    },
    "implementer": {
        "required": {
            "WORKTREE_DIR", "BRANCH", "MODE", "CONTRACT", "OWNED_PATHS",
            "TEST_COMMAND", "STANDARDS", "JIRA_KEY",
        },
        "optional": {
            "PACKAGE", "CRITERIA", "CRS", "FAILURES", "SHARED_IDIOM",
            "VERIFY_EMBEDDED", "HOOKS",
        },
    },
    "reviewer": {
        "required": {"LENS", "WORKTREE_DIR", "STANDARDS", "ROUND"},
        "optional": {
            "DOSSIER", "SCOPE", "CONTRACT", "RUN_EVIDENCE", "CRITERIA",
            "CONTEXT_DOCS", "ARBITRATIONS", "PRIOR_CRS",
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
        {"SCOPE", "CONTRACT", "RUN_EVIDENCE", "CRITERIA", "CONTEXT_DOCS", "ARBITRATIONS"}, []),
    ("reviewer", "LENS", "architecture"): (
        {"SCOPE", "CONTRACT", "RUN_EVIDENCE", "CRITERIA", "CONTEXT_DOCS", "ARBITRATIONS"}, []),
    ("reviewer", "LENS", "performance"): (
        {"SCOPE", "CONTRACT", "RUN_EVIDENCE", "CRITERIA", "CONTEXT_DOCS", "ARBITRATIONS"}, []),
    ("document-drafter", "MODE", "adr"): ({"DECISIONS"}, []),
    ("document-drafter", "MODE", "pr"): ({"DOSSIER-EXCERPTS"}, []),
}

# A field line at the start of a line: NAME: value  /  NAME: |
FIELD_LINE = re.compile(r"^(?P<name>[A-Z][A-Z0-9_-]{2,}):(?P<rest>\s.*|\s*\|\s*)?$")

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


def read_lines(path: str) -> list[str]:
    try:
        with open(path, encoding="utf-8") as handle:
            return handle.read().splitlines()
    except OSError as err:
        print("check_payload: cannot read %s: %s" % (path, err), file=sys.stderr)
        raise SystemExit(2)


def field_value(lines: list[str], name: str) -> str:
    for line in lines:
        match = FIELD_LINE.match(line)
        if match and match.group("name") == name:
            return (match.group("rest") or "").strip().split()[0] if (match.group("rest") or "").strip() else ""
    return ""


def lint(lines: list[str], kind: str | None, worktree: str | None,
         allow_paths: list[str]) -> tuple[list[str], list[str], list[str]]:
    defects: list[str] = []
    warnings: list[str] = []

    # 1. field names — misnamed, missing, and mode/lens-conditional
    present: list[str] = []
    for number, line in enumerate(lines, 1):
        match = FIELD_LINE.match(line)
        if not match:
            continue
        name = match.group("name")
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
             must_mention: str = "") -> None:
        with tempfile.TemporaryDirectory() as tmp:
            real = os.path.join(tmp, "repo-W-014")
            os.makedirs(os.path.join(real, "tests", "unit"))
            with open(os.path.join(real, "tests", "unit", "test_retry.py"), "w") as fh:
                fh.write("def test_x(): pass\n")
            body = text.replace("{REAL}", real)
            _, defects, _ = lint(body.splitlines(), kind, None, [])
            got = bool(defects)
            if got != expect_defect:
                failures.append("%s: expected defect=%s, got %s: %s"
                                % (name, expect_defect, got, defects))
            elif must_mention and not any(must_mention in d for d in defects):
                failures.append("%s: no defect mentions %r: %s" % (name, must_mention, defects))

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

    impl = (
        "WORKTREE_DIR: {REAL}\nBRANCH: fix/x-p1\nMODE: build\nCONTRACT: |\n  fn a()\n"
        "PACKAGE: P1 thing\nOWNED_PATHS: src/flush.py\nCRITERIA: |\n  1. x\n"
        "TEST_COMMAND: pytest -q\nSTANDARDS: {REAL}/tests/unit/test_retry.py\nJIRA_KEY: PROJ-1\n"
    )
    case("implementer build clean", impl, "implementer", False)
    case("implementer build with hooks", impl + "HOOKS: |\n  stop and report\n", "implementer", False)
    case("implementer build missing package",
         impl.replace("PACKAGE: P1 thing\n", ""), "implementer", True, "PACKAGE is missing")
    fix = impl.replace("MODE: build", "MODE: fix").replace("PACKAGE: P1 thing\n", "").replace("CRITERIA: |\n  1. x\n", "")
    case("implementer fix needs crs or failures", fix, "implementer", True, "CRS / FAILURES")
    case("implementer fix with failures", fix + "FAILURES: |\n  assert x\n", "implementer", False)
    case("implementer bad mode", impl.replace("MODE: build", "MODE: repair"), "implementer", True, "takes one of")
    case("implementer misnamed owned paths",
         impl.replace("OWNED_PATHS:", "OWNED_PATH:"), "implementer", True, "did you mean OWNED_PATHS")

    plan = (
        "LENS: plan\nDOSSIER: {REAL}/tests/unit/test_retry.py\nWORKTREE_DIR: {REAL}\n"
        "CRITERIA: |\n  1. x\nSTANDARDS: {REAL}/tests/unit/test_retry.py\n"
        "CONTEXT_DOCS: {REAL}/tests/unit/test_retry.py\nROUND: 1\n"
    )
    case("reviewer plan clean", plan, "reviewer", False)
    case("reviewer style needs scope",
         plan.replace("LENS: plan", "LENS: style"), "reviewer", True, "SCOPE is missing")

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
    print("selftest: %d case group(s), %d failure(s)" % (22, len(failures)))
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
