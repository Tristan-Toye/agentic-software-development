#!/usr/bin/env python3
"""Check sub-agent permission maps for shape, symmetry, and spawn readiness.

Three questions this script answers mechanically:

  1. Shape — every path map (read/edit/write) that allows anything must be an
     allow-list: specific allows plus a trailing "*": deny. A path map without
     the default deny admits everything a glob can reach, so blindness or
     scoping stated in prose is not enforced by the map.
  2. Symmetry — when an agent has both read and edit maps, every family the
     read map admits (except the staging area, which is orchestrator-owned
     input) must also be admitted by the edit map. A read-allowed, edit-denied
     family bricks a spawn: the author reads the existing file, refuses to
     write it, and returns empty — once per retry.
  3. Spawn readiness — given an agent file, a target worktree root, and the
     TEST_PATHS you are about to name, would every path land inside the edit
     map? Would every STYLE_PATHS-style read path land inside the read map?
     Answer before the spawn, not after an empty return.
  4. Negative controls — named canonical implementation paths (src/, lib/,
     internal/, cmd/, and the deploy/scripts bootstrap that a broad `*/scripts/*`
     read pattern used to admit) must stay denied. A widening pattern fails
     the plugin's own checks before it ships.
  5. The host — an agent file states two tool policies: opencode's
     `permission:` maps, and the Claude Code tool list (`claude.tools` in a
     source under sub-agents/, a top-level `tools:` in a generated file under
     claude/agents/). The frontmatter tool list is what loads under Claude
     Code, and it is read FIRST: a read path validated against a body map
     that the tool list contradicts passes the map check and is still a dead
     letter at run time — worse than no check, because it reads as evidence.
     The recorded spawn printed `OK 2 read path(s) admitted` for an agent
     whose tools were `Write` alone; every one of those paths was unreachable.
     So `--read-paths` for a host whose tool list grants no Read is REFUSED,
     naming the frontmatter line; a `--test-paths` target that already exists
     on disk for an agent with neither Read nor Edit is a WARNING (a `Write`
     is whole-file and refuses a file the agent has not read — delete first
     with safe_revert.py --delete); and the two policies disagreeing about a
     tool — a blindness boundary the tool list grants anyway, or a flat grant
     the tool list withholds — is a defect in the agent file itself, reported
     by the scan. `--host claude|opencode|both` picks the policy to validate;
     omitted, it is inferred from what the file declares (a source with a
     `claude:` block validates on both, and a refusal names its host).

Usage:
    check_permission_maps.py
        scan every sub-agents/*.md for shape, symmetry, negative controls,
        and frontmatter/map disagreement
    check_permission_maps.py --agent sub-agents/unit-test-author.md \\
        --host opencode --root /abs/path/target-worktree \\
        --test-paths tests/unit/test_flush.py,tests/util/helpers.py \\
        --read-paths tests/unit/test_retry.py --expected-lines 620
        validate a spawn before it happens, against the opencode maps
    check_permission_maps.py --agent claude/agents/unit-test-author.md \\
        --host claude --root /abs/path/target-worktree \\
        --test-paths tests/unit/test_flush.py
        the same spawn under Claude Code: the tool list is `Write`, so any
        --read-paths is refused and an existing TEST_PATH warns delete-first
    check_permission_maps.py --agent sub-agents/integration-test-author.md \\
        --root /abs/path/target-worktree \\
        --test-paths tests/integration/test_flow.py --flows 3 --expected-lines 700
        the size and split gates for an agent with no path map: warns when the
        flows outnumber the paths (one TEST_PATH per flow is the default) and
        past the single-write cap
    check_permission_maps.py --selftest

Exit codes:
    0  no findings (warnings may have printed)
    1  findings — fix the map, the split, or the payload before spawning
    2  unusable — unreadable agent file, unresolvable path. Never guessed
       around; fix the input.

Glob semantics: "*" in a pattern crosses directory separators, so "tests/*"
matches "tests/unit/test_x.py" — the payloads depend on it. A path is allowed
by a map when any allow pattern matches it; with the default deny verified by
the shape check, that is the whole rule.

No third-party imports: this runs wherever python3 does.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import re
import sys
import tempfile
from collections.abc import Mapping
from pathlib import Path

PATH_TOOLS = ("read", "edit", "write")
STAGING_MARKERS = (".agent-staging/",)


def parse_permission(text: str) -> dict[str, object]:
    """Frontmatter text -> {tool: {pattern: verdict}} or {tool: "deny"}.

    Understands exactly the two shapes the plugin's agents use: a scalar
    verdict (`bash: deny`) or an indented pattern map under `permission:`.
    """
    m = re.search(r"^permission:\n((?:  .*\n?)+)", text, re.MULTILINE)
    if not m:
        return {}
    maps: dict[str, object] = {}
    open_map: dict[str, str] | None = None
    for line in m.group(1).rstrip("\n").split("\n"):
        if not line.startswith("  "):
            break
        body = line[2:]
        scalar = re.match(r"^([a-z]+):\s*(allow|deny)\s*$", body)
        if scalar:
            maps[scalar.group(1)] = scalar.group(2)
            open_map = None
            continue
        opener = re.match(r"^([a-z]+):\s*$", body)
        if opener:
            open_map = {}
            maps[opener.group(1)] = open_map
            continue
        entry = re.match(r'^\s*"(.*)":\s*(allow|deny)\s*$', body)
        if entry and open_map is not None:
            open_map[entry.group(1)] = entry.group(2)
    return maps


def compile_pattern(pattern: str) -> re.Pattern[str]:
    """A permission glob -> anchored regex; '*' crosses separators."""

    def one(ch: str) -> str:
        return ".*" if ch == "*" else re.escape(ch)

    return re.compile("^" + "".join(one(c) for c in pattern) + "$")


def is_allowed(amap: object, path: str) -> bool | None:
    """True/False under a pattern map; None when the tool is scalar-denied."""
    if not isinstance(amap, dict):
        return False if amap == "deny" else None
    return any(compile_pattern(p).match(path) for p, v in amap.items() if v == "allow")


def allow_families(amap: object) -> list[str]:
    if not isinstance(amap, dict):
        return []
    return [p for p, v in amap.items() if v == "allow"]


def shape_findings(agent: str, maps: Mapping[str, object]) -> list[str]:
    """Every path map with allows must carry the default deny."""
    out: list[str] = []
    for tool in PATH_TOOLS:
        amap = maps.get(tool)
        if amap is None:
            continue
        if not isinstance(amap, dict):
            if amap == "allow":
                out.append(
                    f"{agent}: {tool} is a flat allow — path maps must be allow-lists"
                )
            continue
        if allow_families(amap) and amap.get("*") != "deny":
            out.append(
                f"{agent}: {tool} map has allows but no '*': deny — "
                "everything a glob reaches is admitted"
            )
    return out


def symmetry_findings(agent: str, maps: Mapping[str, object]) -> list[str]:
    """Read-admitted families must be writable, except the staging area."""
    read_map, edit_map = maps.get("read"), maps.get("edit")
    if not (isinstance(read_map, dict) and isinstance(edit_map, dict)):
        return []
    out: list[str] = []
    for family in allow_families(read_map):
        if any(marker in family for marker in STAGING_MARKERS):
            continue
        if edit_map.get(family) != "allow":
            out.append(
                f"{agent}: read map admits '{family}' but the edit map does not — "
                "a spawn told to extend a file there reads it, refuses to write it, "
                "and returns empty"
            )
    return out


# Canonical paths per agent with the verdict each map must keep: a control
# names a path and, per tool, the boolean is_allowed must return. The blind
# author's implementation paths stay read-denied (and edit-denied except the
# deploy/scripts blind-write family); the document drafter, which reads
# freely but writes only its three families, keeps every code path
# write-denied while its own TARGET_PATHS stay write-allowed. These mirror
# the target repo's lib/negative_control.sh idea: the plugin asserts its own
# boundaries against named paths, so a widening pattern fails the plugin's
# checks before it ships.
NEGATIVE_CONTROLS: dict[str, list[tuple[str, dict[str, bool]]]] = {
    "unit-test-author.md": [
        ("src/flush.py", {"read": False, "edit": False}),
        ("lib/foo.py", {"read": False, "edit": False}),
        ("internal/bar.rs", {"read": False, "edit": False}),
        ("cmd/main.go", {"read": False, "edit": False}),
        ("deploy/scripts/openbao_bootstrap.sh", {"read": False, "edit": True}),
    ],
    "document-drafter.md": [
        ("src/flush.py", {"write": False}),
        ("lib/foo.py", {"write": False}),
        ("internal/bar.rs", {"write": False}),
        ("cmd/main.go", {"write": False}),
        ("docs/adr/0031-flush-drain-lock.md", {"write": True}),
        (".discovery/pr-draft-W-014.md", {"write": True}),
    ],
}


def negative_control_findings(agent: str, maps: Mapping[str, object]) -> list[str]:
    """Named control paths must keep their expected verdicts under the maps."""
    controls = NEGATIVE_CONTROLS.get(agent.rsplit("/", 1)[-1], [])
    out: list[str] = []
    for path, assertions in controls:
        for tool, expected in assertions.items():
            allowed = bool(is_allowed(maps.get(tool), path))
            if allowed != expected:
                out.append(
                    f"{agent}: negative control '{path}' is "
                    f"{tool.upper()}-{'ALLOWED' if allowed else 'DENIED'} — "
                    f"this path must stay {'allowed' if expected else 'denied'}"
                )
    return out


# opencode permission key -> the Claude Code tool it governs. Mirrors the
# table in build_claude_plugin.py; `list` has no Claude counterpart.
CLAUDE_TOOL_OF: dict[str, str] = {
    "read": "Read",
    "edit": "Edit",
    "write": "Write",
    "bash": "Bash",
    "glob": "Glob",
    "grep": "Grep",
    "webfetch": "WebFetch",
    "websearch": "WebSearch",
    "task": "Agent",
}


def front_matter_lines(text: str) -> list[tuple[int, str]]:
    """(line number, line) for every line inside the front matter fence."""
    if not text.startswith("---\n"):
        return []
    out: list[tuple[int, str]] = []
    for number, line in enumerate(text.split("\n")[1:], 2):
        if line == "---":
            break
        out.append((number, line))
    return out


def parse_tools(text: str) -> tuple[list[str] | None, int, set[str]]:
    """The Claude Code tool list, the front matter line it sits on, and the
    permission keys widened on purpose under `claude.widen`.

    A generated file (claude/agents/*.md) carries a top-level `tools:` line;
    a source (sub-agents/*.md) carries it inside its `claude:` block. The
    tool list is what loads under Claude Code, whatever the body prose says.
    (None, 0, set()) when the file declares neither.
    """
    tools: list[str] | None = None
    line_no = 0
    widened: set[str] = set()
    in_claude = in_widen = False
    for number, line in front_matter_lines(text):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(line) - len(line.lstrip())
        if indent == 0:
            in_claude, in_widen = stripped == "claude:", False
            top = re.match(r"^tools:\s*(.+)$", stripped)
            if top:
                tools = [t.strip() for t in top.group(1).split(",") if t.strip()]
                line_no = number
            continue
        if not in_claude:
            continue
        if indent == 2:
            in_widen = stripped == "widen:"
            nested = re.match(r"^tools:\s*(.+)$", stripped)
            if nested:
                tools = [t.strip() for t in nested.group(1).split(",") if t.strip()]
                line_no = number
        elif in_widen and indent > 2:
            widened.add(stripped.split(":", 1)[0].strip().strip("\"'"))
    return tools, line_no, widened


def hosts_declared(maps: Mapping[str, object], tools: list[str] | None) -> list[str]:
    """Which hosts' policies an agent file states, in validation order."""
    hosts: list[str] = []
    if tools is not None:
        hosts.append("claude")
    if maps:
        hosts.append("opencode")
    return hosts


def disagreement_findings(
    agent: str,
    maps: Mapping[str, object],
    tools: list[str] | None,
    tools_line: int,
    widened: set[str],
) -> list[str]:
    """The two declared policies of one file must agree, tool by tool.

    A blindness boundary in a map — a scalar deny, or an allow-list closed by
    `"*": deny` — that the tool list grants anyway is a boundary that holds
    on one host only. A flat opencode grant — scalar allow, or a map whose
    default is allow — that the tool list withholds is a capability lost
    silently under Claude Code. `claude.widen.<key>` exempts a key on
    purpose, with its reason, exactly as build_claude_plugin.py reads it.
    """
    if tools is None:
        return []
    out: list[str] = []
    for key, tool in CLAUDE_TOOL_OF.items():
        policy = maps.get(key)
        if policy is None or key in widened:
            continue
        granted = tool in tools
        boundary = policy == "deny" or (
            isinstance(policy, dict) and policy.get("*") == "deny"
        )
        flat_allow = policy == "allow" or (
            isinstance(policy, dict) and policy.get("*") == "allow"
        )
        if boundary and granted:
            out.append(
                f"{agent}: frontmatter tools (line {tools_line}) grants {tool}, but "
                f"the {key} policy is a blindness boundary ('*': deny) — the boundary "
                f"holds on one host only; withhold {tool}, or record the widening "
                f"under claude.widen.{key} with its reason"
            )
        elif flat_allow and not granted:
            out.append(
                f"{agent}: the {key} policy is a flat allow, but frontmatter tools "
                f"(line {tools_line}) withholds {tool} — a capability lost silently "
                "under Claude Code; grant it, or deny it on both hosts"
            )
    return out


def dead_letter_notes(
    agent: str, maps: Mapping[str, object], tools: list[str] | None, tools_line: int
) -> list[str]:
    """Allow-list maps the tool list makes unreachable: not a defect (the
    generator withholds the tool on purpose), but every path the map admits
    is a dead letter under Claude Code, and the scan says so."""
    if tools is None:
        return []
    out: list[str] = []
    for key in PATH_TOOLS:
        policy = maps.get(key)
        tool = CLAUDE_TOOL_OF[key]
        if isinstance(policy, dict) and allow_families(policy) and tool not in tools:
            out.append(
                f"{agent}: the {key} map admits {len(allow_families(policy))} "
                f"families, but frontmatter tools (line {tools_line}: "
                f"{', '.join(tools)}) grants no {tool} — the map is opencode-only; "
                f"under Claude Code every path it admits is a dead letter, so the "
                "payload pastes the content instead"
            )
    return out


def resolve(root: Path, path: str) -> str | None:
    """A path against a root -> repository-relative, or None when outside."""
    p = Path(path)
    if not p.is_absolute():
        p = root / p
    p = p.resolve()
    try:
        p.relative_to(root.resolve())
    except ValueError:
        return None
    return p.relative_to(root.resolve()).as_posix()


MAX_SINGLE_EDIT_LINES = 400


def spawn_validation(
    agent_file: Path,
    root: Path,
    test_paths: list[str],
    read_paths: list[str],
    expected_lines: int | None = None,
    flows: int | None = None,
    host: str | None = None,
) -> int:
    try:
        text = agent_file.read_text()
    except OSError as exc:
        print(f"UNUSABLE  cannot read the agent file: {exc}")
        return 2
    maps = parse_permission(text)
    tools, tools_line, _widened = parse_tools(text)
    declared = hosts_declared(maps, tools)
    if not declared:
        print(
            f"UNUSABLE  {agent_file} declares neither a permission map nor a "
            "tools list — nothing to validate a spawn against"
        )
        return 2
    hosts = declared if host in (None, "both") else [host]
    for wanted in hosts:
        if wanted not in declared:
            print(
                f"UNUSABLE  --host {wanted} asked, but {agent_file} declares no "
                f"{'tool list' if wanted == 'claude' else 'permission map'} for it"
            )
            return 2
    if expected_lines is not None and expected_lines > MAX_SINGLE_EDIT_LINES:
        print(
            f"WARNING  expected deliverable ~{expected_lines} lines exceeds the "
            f"~{MAX_SINGLE_EDIT_LINES}-line cap for one write: name the split in "
            "the payload — more files, or one file in staged sections",
            file=sys.stderr,
        )
    if flows is not None and flows > len(test_paths):
        print(
            f"WARNING  {flows} flows over {len(test_paths)} TEST_PATHS: name one "
            "path per flow — a GAP: or a vacuous test then re-spawns one flow, not "
            "the whole set, and no single Write runs long",
            file=sys.stderr,
        )
    for raw in test_paths + read_paths:
        if resolve(root, raw) is None:
            print(f"UNUSABLE  '{raw}' is not inside the root {root}")
            return 2

    findings: list[str] = []
    notes: list[str] = []

    # The Claude Code host: the tool list is read first, and it is the whole
    # boundary — no map narrows or widens it.
    if "claude" in hosts:
        assert tools is not None
        granted = ", ".join(tools)
        if read_paths and "Read" not in tools:
            findings.append(
                f"host claude: frontmatter tools (line {tools_line}: {granted}) "
                f"grants no Read — {len(read_paths)} read path(s) are dead letters: "
                f"{', '.join(read_paths)}. Every payload field naming a path is "
                "unreachable for this agent; paste the content instead (CONTRACT "
                "inline, STYLE_SAMPLE, SUPPORT)"
            )
        if test_paths and "Write" not in tools and "Edit" not in tools:
            findings.append(
                f"host claude: frontmatter tools (line {tools_line}: {granted}) "
                "grants neither Write nor Edit — no TEST_PATH can be written"
            )
        elif "Edit" not in tools:
            for raw in test_paths:
                rel = resolve(root, raw) or raw
                if not (root / rel).exists():
                    continue
                if "Read" not in tools:
                    print(
                        f"WARNING  host claude: TEST_PATH '{rel}' already exists and "
                        f"the agent has no Edit and no Read (tools: {granted}): a "
                        "Write is whole-file and refuses a file the agent has not "
                        "read, so the spawn returns GAP: with the file undelivered. "
                        "Delete it first with safe_revert.py --delete, then spawn "
                        "for a fresh whole-file Write (work-on.md Phase 4)",
                        file=sys.stderr,
                    )
                else:
                    notes.append(
                        f"host claude: TEST_PATH '{rel}' exists; with Read and no "
                        "Edit the agent re-reads it and rewrites it whole"
                    )
        if not read_paths and "Read" not in tools:
            notes.append(
                f"host claude: no read paths named, and none could be — tools "
                f"(line {tools_line}) are {granted}"
            )

    # The opencode host: the path maps are the boundary.
    if "opencode" in hosts:
        scoped = "edit" in maps
        if not scoped and any(tool in maps for tool in PATH_TOOLS):
            print(f"UNUSABLE  {agent_file} declares path maps but no edit map")
            return 2
        if not scoped:
            # No path map (integration-test-author): admission is not gated,
            # only the size and split gates above apply.
            notes.append(
                f"host opencode: {agent_file.name} has no path map, so only the "
                "size and split gates applied"
            )
        else:
            for raw in test_paths:
                rel = resolve(root, raw) or raw
                if not is_allowed(maps.get("edit"), rel):
                    families = ", ".join(allow_families(maps.get("edit"))) or "none"
                    findings.append(
                        f"host opencode: TEST_PATH '{rel}' is outside the edit map "
                        f"(admitted: {families})"
                    )
                elif not is_allowed(maps.get("read"), rel):
                    if (root / rel).exists():
                        print(
                            f"WARNING  host opencode: TEST_PATH '{rel}' exists but is "
                            "read-denied: stage its current content under "
                            ".agent-staging/ and have the author rewrite the whole file",
                            file=sys.stderr,
                        )
                    else:
                        notes.append(
                            f"host opencode: TEST_PATH '{rel}' is read-denied but does "
                            "not exist yet"
                        )
            for raw in read_paths:
                rel = resolve(root, raw) or raw
                if not is_allowed(maps.get("read"), rel):
                    families = ", ".join(allow_families(maps.get("read"))) or "none"
                    findings.append(
                        f"host opencode: read path '{rel}' is outside the read map "
                        f"(admitted: {families})"
                    )

    for note in notes:
        print(f"NOTE      {note}")
    for f in findings:
        print(f"REFUSED   {f}")
    if findings:
        print(
            "\nexit 1 — fix the split, stage or paste the content; never widen a "
            "map or a tool list to work around a refused path"
        )
        return 1
    print(
        f"OK        {len(test_paths)} test path(s), {len(read_paths)} read path(s) "
        f"admitted on {' and '.join(hosts)}"
    )
    return 0


def scan(sub_agents: Path) -> int:
    findings: list[str] = []
    checked = 0
    for agent in sorted(sub_agents.glob("*.md")):
        try:
            text = agent.read_text()
        except OSError as exc:
            print(f"UNUSABLE  cannot read {agent}: {exc}")
            return 2
        maps = parse_permission(text)
        if not maps:
            continue
        checked += 1
        name = f"sub-agents/{agent.name}"
        tools, tools_line, widened = parse_tools(text)
        findings += shape_findings(name, maps)
        findings += symmetry_findings(name, maps)
        findings += negative_control_findings(name, maps)
        findings += disagreement_findings(name, maps, tools, tools_line, widened)
        for note in dead_letter_notes(name, maps, tools, tools_line):
            print(f"NOTE      {note}")
    for f in findings:
        print(f"REFUSED   {f}")
    verdict = "PASS" if not findings else "FAIL"
    print(f"\n{verdict} — {checked} agent(s) with permission maps checked")
    return 1 if findings else 0


def selftest() -> int:
    cases: list[tuple[str, bool, bool]] = []

    def check(name: str, want: bool, got: bool) -> None:
        cases.append((name, want, got))

    # Glob semantics
    check(
        "tests/* matches nested",
        True,
        bool(compile_pattern("tests/*").match("tests/unit/test_x.py")),
    )
    check(
        "tests/* does not match sibling",
        False,
        bool(compile_pattern("tests/*").match("src/x.py")),
    )
    check(
        "*/tests/* needs a leading dir",
        True,
        bool(compile_pattern("*/tests/*").match("pkg/tests/x.py")),
    )
    check(
        "*/tests/* rejects bare tests/",
        False,
        bool(compile_pattern("*/tests/*").match("tests/x.py")),
    )
    check(
        "dot directories stay literal",
        True,
        bool(compile_pattern(".agent-staging/*").match(".agent-staging/contract.md")),
    )
    check(
        "escaped dot is not a wildcard",
        False,
        bool(compile_pattern(".agent-staging/*").match("xagent-staging/contract.md")),
    )

    good_read = {
        ".agent-staging/*": "allow",
        "*/.agent-staging/*": "allow",
        "tests/*": "allow",
        "*/tests/*": "allow",
        "*": "deny",
    }
    good_edit = {
        "tests/*": "allow",
        "*/tests/*": "allow",
        "fixtures/*": "allow",
        "*": "deny",
    }

    # Shape
    check(
        "shape: allow-list passes",
        False,
        bool(shape_findings("a", {"read": good_read})),
    )
    check(
        "shape: missing default deny fails",
        True,
        bool(
            shape_findings(
                "a", {"read": {k: v for k, v in good_read.items() if k != "*"}}
            )
        ),
    )
    check(
        "shape: flat allow fails", True, bool(shape_findings("a", {"write": "allow"}))
    )
    check("shape: flat deny passes", False, bool(shape_findings("a", {"bash": "deny"})))

    # Symmetry
    symmetric_edit = dict(good_edit)
    symmetric_edit.update({"scripts/*": "allow", "*/scripts/*": "allow"})
    check(
        "symmetry: read family editable passes",
        False,
        bool(symmetry_findings("a", {"read": good_read, "edit": symmetric_edit})),
    )
    check(
        "symmetry: staging exempt",
        False,
        bool(symmetry_findings("a", {"read": good_read, "edit": good_edit})),
    )
    broken = {"read": good_read, "edit": {"tests/*": "allow", "*": "deny"}}
    check("symmetry: missing family fails", True, bool(symmetry_findings("a", broken)))

    # parse_permission on the real agent
    repo = Path(__file__).resolve().parent.parent
    real = parse_permission((repo / "sub-agents" / "unit-test-author.md").read_text())
    real_read, real_edit = real.get("read"), real.get("edit")
    check(
        "parse: unit-test-author read map found",
        True,
        isinstance(real_read, dict),
    )
    check(
        "parse: read map carries its families",
        True,
        isinstance(real_read, dict) and len(allow_families(real_read)) >= 5,
    )
    check(
        "parse: read admits a nested test path",
        True,
        bool(is_allowed(real_read, "tests/unit/test_x.py")),
    )
    check(
        "parse: edit map carries its families",
        True,
        isinstance(real_edit, dict) and len(allow_families(real_edit)) >= 5,
    )
    check("parse: real agent symmetric", False, bool(symmetry_findings("real", real)))
    check(
        "parse: implementation path read-denied",
        False,
        bool(is_allowed(real_read, "src/flush.py")),
    )

    # Negative controls: the real map must pass them, a widened map must fail
    check(
        "controls: real agent passes",
        False,
        bool(negative_control_findings("sub-agents/unit-test-author.md", real)),
    )
    assert isinstance(real_read, dict) and isinstance(real_edit, dict)
    widened = dict(real)
    widened["read"] = dict(real_read, **{"scripts/*": "allow", "*/scripts/*": "allow"})
    check(
        "controls: scripts/* re-widened read map fails",
        True,
        bool(negative_control_findings("sub-agents/unit-test-author.md", widened)),
    )
    writable_impl = dict(real)
    writable_impl["edit"] = dict(real_edit, **{"src/*": "allow"})
    findings = negative_control_findings(
        "sub-agents/unit-test-author.md", writable_impl
    )
    check("controls: implementation path edit-allowed fails", True, bool(findings))
    check(
        "controls: deploy suite stays edit-allowed without a finding",
        True,
        bool(is_allowed(real_edit, "deploy/scripts/openbao_bootstrap.sh")),
    )

    # Document drafter: write map denies code paths, admits its own targets
    drafter = parse_permission(
        (repo / "sub-agents" / "document-drafter.md").read_text()
    )
    drafter_write = drafter.get("write")
    check(
        "drafter: write map found",
        True,
        isinstance(drafter_write, dict),
    )
    check(
        "drafter: real agent passes its controls",
        False,
        bool(negative_control_findings("sub-agents/document-drafter.md", drafter)),
    )
    assert isinstance(drafter_write, dict)
    writable_code = dict(drafter, write=dict(drafter_write, **{"src/*": "allow"}))
    check(
        "drafter: code path write-allowed fails",
        True,
        bool(
            negative_control_findings("sub-agents/document-drafter.md", writable_code)
        ),
    )
    check(
        "drafter: TARGET_PATHS under docs/adr stays write-allowed",
        True,
        bool(is_allowed(drafter_write, "docs/adr/0031-flush-drain-lock.md")),
    )
    check(
        "drafter: TARGET_PATHS under .discovery stays write-allowed",
        True,
        bool(is_allowed(drafter_write, ".discovery/pr-draft-W-014.md")),
    )

    # The integration author has no path map: size and split gates only.
    it_author = repo / "sub-agents" / "integration-test-author.md"

    def gate(paths: list[str], lines: int | None, flows: int | None) -> tuple[int, str]:
        err, out = io.StringIO(), io.StringIO()
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(out):
            rc = spawn_validation(it_author, repo, paths, [], lines, flows)
        return rc, err.getvalue()

    rc, warned = gate(["tests/integration/test_flow.py"], 700, None)
    check("it author: 700 lines warns past the cap", True, rc == 0 and "exceeds" in warned)
    rc, warned = gate(["tests/integration/test_flow.py"], 250, None)
    check("it author: 250 lines is quiet", True, rc == 0 and "exceeds" not in warned)
    rc, warned = gate(["tests/integration/test_flow.py"], None, 3)
    check("it author: three flows over one path warns", True, rc == 0 and "3 flows over 1" in warned)
    rc, warned = gate(["tests/integration/a.py", "tests/integration/b.py", "tests/integration/c.py"], None, 3)
    check("it author: one path per flow is quiet", True, rc == 0 and "flows over" not in warned)
    rc, _ = gate(["/etc/outside.py"], None, None)
    check("it author: a path outside the root is unusable", True, rc == 2)

    # The frontmatter tool list is read first: host-aware spawn validation.
    with tempfile.TemporaryDirectory() as tmp:
        troot = Path(tmp)
        (troot / "tests" / "unit").mkdir(parents=True)
        (troot / "tests" / "unit" / "test_retry.py").write_text("def test_x(): pass\n")
        source = troot / "unit-source.md"
        source.write_text(
            "---\nname: u\npermission:\n  read:\n    \"*\": deny\n"
            "    \".agent-staging/*\": allow\n    \"tests/*\": allow\n"
            "  edit:\n    \"*\": deny\n    \"tests/*\": allow\n"
            "claude:\n  model: haiku\n  tools: Write\n---\nbody\n"
        )
        generated = troot / "unit-generated.md"
        generated.write_text("---\nname: u\ntools: Write\nmodel: haiku\n---\nbody\n")

        def spawn(agent: Path, host: str | None, tests: list[str], reads: list[str]) -> tuple[int, str]:
            err, out = io.StringIO(), io.StringIO()
            with contextlib.redirect_stderr(err), contextlib.redirect_stdout(out):
                rc = spawn_validation(agent, troot, tests, reads, None, None, host)
            return rc, out.getvalue() + err.getvalue()

        new, style = ["tests/unit/test_flush.py"], ["tests/unit/test_retry.py"]
        tools, line, _ = parse_tools(source.read_text())
        check("tools: source claude.tools parsed", True, tools == ["Write"] and line == 13)
        tools, line, _ = parse_tools(generated.read_text())
        check("tools: generated top-level tools parsed", True, tools == ["Write"] and line == 3)
        rc, out = spawn(source, "claude", new, style)
        check(
            "host claude: a read path for a Read-less agent is refused, naming the line",
            True,
            rc == 1 and "grants no Read" in out and "line 13" in out and "test_retry.py" in out,
        )
        rc, out = spawn(source, "opencode", new, style)
        check("host opencode: the same read path is admitted by the map", True, rc == 0)
        rc, out = spawn(source, None, new, style)
        check("host inferred: a source with both policies refuses on claude", True, rc == 1 and "host claude" in out)
        rc, out = spawn(generated, None, new, style)
        check("host inferred: a generated file validates as claude", True, rc == 1 and "line 3" in out)
        rc, out = spawn(source, "claude", new, [])
        check("host claude: no read paths, new test path — admitted", True, rc == 0)
        rc, out = spawn(source, "claude", style, [])
        check(
            "host claude: an existing TEST_PATH for an agent with no Edit and no Read warns delete-first",
            True,
            rc == 0 and "safe_revert.py --delete" in out,
        )
        rc, out = spawn(source, "opencode", style, [])
        check("host opencode: an existing TEST_PATH inside both maps is quiet", True, rc == 0 and "safe_revert" not in out)
        rc, _ = spawn(generated, "opencode", new, [])
        check("host asked that the file does not declare is unusable", True, rc == 2)

    # Frontmatter/map disagreement is a defect in the agent file itself.
    def disagreement(text: str) -> list[str]:
        tools, line, widened = parse_tools(text)
        return disagreement_findings("fixture", parse_permission(text), tools, line, widened)

    boundary_granted = (
        "---\nname: c\npermission:\n  read:\n    \"*\": deny\n    \"tests/*\": allow\n"
        "claude:\n  tools: Read, Write\n---\n"
    )
    found = disagreement(boundary_granted)
    check("disagreement: a blindness boundary the tool list grants is a defect", True, bool(found) and "boundary" in found[0])
    flat_lost = "---\nname: c\npermission:\n  read: allow\nclaude:\n  tools: Write\n---\n"
    found = disagreement(flat_lost)
    check("disagreement: a flat allow the tool list withholds is a defect", True, bool(found) and "lost silently" in found[0])
    widened_ok = boundary_granted.replace("  tools: Read, Write\n", "  tools: Read, Write\n  widen:\n    read: reads its own targets\n")
    check("disagreement: claude.widen exempts the key", False, bool(disagreement(widened_ok)))
    consistent = "---\nname: c\npermission:\n  read:\n    \"*\": deny\n    \"tests/*\": allow\n  bash: deny\nclaude:\n  tools: Write\n---\n"
    check("disagreement: an allow-list withheld on claude is consistent", False, bool(disagreement(consistent)))
    check(
        "disagreement: an allow-list withheld on claude is a dead-letter note",
        True,
        bool(dead_letter_notes("fixture", parse_permission(consistent), ["Write"], 9)),
    )
    for name in ("unit-test-author.md", "integration-test-author.md", "document-drafter.md", "implementer.md", "reviewer.md"):
        text = (repo / "sub-agents" / name).read_text()
        check(f"disagreement: real {name} is consistent", False, bool(disagreement(text)))

    ok = all(w == g for _, w, g in cases)
    for name, want, got in cases:
        print(f"{'PASS' if want == got else 'FAIL'}  {name}: want {want}, got {got}")
    print(f"\n{'PASS' if ok else 'FAIL'} — {len(cases)} case(s)")
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--agent", metavar="FILE", help="agent file whose maps gate the spawn"
    )
    ap.add_argument(
        "--root",
        default=".",
        help="target worktree the spawn paths resolve against (default: .)",
    )
    ap.add_argument(
        "--test-paths",
        default="",
        help="comma-separated TEST_PATHS to check against the edit map",
    )
    ap.add_argument(
        "--read-paths",
        default="",
        help="comma-separated paths (STYLE_PATHS and kin) to check against the read map",
    )
    ap.add_argument(
        "--expected-lines",
        type=int,
        help="expected deliverable size; warns past the single-edit cap",
    )
    ap.add_argument(
        "--flows",
        type=int,
        help="flows (or criteria groups) the spawn covers; warns when they "
        "outnumber --test-paths — one path per flow is the default",
    )
    ap.add_argument(
        "--host",
        choices=("claude", "opencode", "both"),
        default=None,
        help="the host the spawn runs under: claude validates the frontmatter tool "
        "list, opencode the permission maps; omitted, every policy the file "
        "declares is validated",
    )
    ap.add_argument(
        "--selftest", action="store_true", help="run the built-in negative controls"
    )
    args = ap.parse_args()

    if args.selftest:
        return selftest()
    if args.agent:
        test_paths = [p for p in args.test_paths.split(",") if p]
        read_paths = [p for p in args.read_paths.split(",") if p]
        if not test_paths and not read_paths:
            print("UNUSABLE  --agent needs --test-paths and/or --read-paths")
            return 2
        return spawn_validation(
            Path(args.agent),
            Path(args.root),
            test_paths,
            read_paths,
            args.expected_lines,
            args.flows,
            args.host,
        )
    repo = Path(__file__).resolve().parent.parent
    return scan(repo / "sub-agents")


if __name__ == "__main__":
    sys.exit(main())
