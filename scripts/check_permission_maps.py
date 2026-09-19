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

Usage:
    check_permission_maps.py
        scan every sub-agents/*.md for shape and symmetry
    check_permission_maps.py --agent sub-agents/unit-test-author.md \\
        --root /abs/path/target-worktree \\
        --test-paths tests/unit/test_flush.py,tests/util/helpers.py \\
        --read-paths tests/unit/test_retry.py --expected-lines 620
        validate a spawn before it happens
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
) -> int:
    try:
        maps = parse_permission(agent_file.read_text())
    except OSError as exc:
        print(f"UNUSABLE  cannot read the agent file: {exc}")
        return 2
    scoped = "edit" in maps
    if not scoped and any(tool in maps for tool in PATH_TOOLS):
        print(f"UNUSABLE  {agent_file} declares path maps but no edit map")
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
    findings: list[str] = []
    if not scoped:
        # No path map (integration-test-author): admission is not gated, only
        # the size and split gates above apply.
        for raw in test_paths + read_paths:
            if resolve(root, raw) is None:
                print(f"UNUSABLE  '{raw}' is not inside the root {root}")
                return 2
        print(
            f"OK        {len(test_paths)} test path(s) named; {agent_file.name} has "
            "no path map, so only the size and split gates applied"
        )
        return 0
    for raw in test_paths:
        rel = resolve(root, raw)
        if rel is None:
            print(f"UNUSABLE  '{raw}' is not inside the root {root}")
            return 2
        if not is_allowed(maps.get("edit"), rel):
            families = ", ".join(allow_families(maps.get("edit"))) or "none"
            findings.append(
                f"TEST_PATH '{rel}' is outside the edit map (admitted: {families})"
            )
        elif not is_allowed(maps.get("read"), rel):
            if (root / rel).exists():
                print(
                    f"WARNING  TEST_PATH '{rel}' exists but is read-denied: stage its "
                    "current content under .agent-staging/ and have the author "
                    "rewrite the whole file",
                    file=sys.stderr,
                )
            else:
                print(
                    f"NOTE      TEST_PATH '{rel}' is read-denied but does not exist yet"
                )
    for raw in read_paths:
        rel = resolve(root, raw)
        if rel is None:
            print(f"UNUSABLE  '{raw}' is not inside the root {root}")
            return 2
        if not is_allowed(maps.get("read"), rel):
            families = ", ".join(allow_families(maps.get("read"))) or "none"
            findings.append(
                f"read path '{rel}' is outside the read map (admitted: {families})"
            )
    for f in findings:
        print(f"REFUSED   {f}")
    if findings:
        print(
            "\nexit 1 — fix the split or stage the content; never widen the map "
            "to work around a refused path"
        )
        return 1
    print(
        f"OK        {len(test_paths)} test path(s), {len(read_paths)} read path(s) admitted"
    )
    return 0


def scan(sub_agents: Path) -> int:
    findings: list[str] = []
    checked = 0
    for agent in sorted(sub_agents.glob("*.md")):
        try:
            maps = parse_permission(agent.read_text())
        except OSError as exc:
            print(f"UNUSABLE  cannot read {agent}: {exc}")
            return 2
        if not maps:
            continue
        checked += 1
        name = f"sub-agents/{agent.name}"
        findings += shape_findings(name, maps)
        findings += symmetry_findings(name, maps)
        findings += negative_control_findings(name, maps)
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
        )
    repo = Path(__file__).resolve().parent.parent
    return scan(repo / "sub-agents")


if __name__ == "__main__":
    sys.exit(main())
