#!/usr/bin/env python3
"""Claude Code PreToolUse hook: refuse an auto-fixer that NAMES a path under `.discovery/` (LRN-0149).

`_typos.toml` excludes `.discovery/` so a fixer never rewrites recorded evidence, but the
exclusion is a TRAVERSAL exclusion: `typos --write-changes .` skips the record, while
`typos --write-changes .discovery/dossiers/W-095.md` checks the named file anyway and
"corrects" every quoted short SHA in it (`ff49daa4` becomes `ff49data4`), not reversible
from memory. The same command that is safe over the tree corrupts the record when it is
pointed at one file inside it.

It denies a Bash command whose segment runs `typos`, `taplo`, `cargo fix` or a `--fix` /
`--write-changes` fixer and names an argument at or under `.discovery/`, unless the same
segment passes `--force-exclude` (the one safe named run; prove `git diff -- .discovery/`
empty afterwards). Read-only runs (`typos <path>` without a write flag, `taplo check`) pass.

# Configuration

    ASD_FIXER_GUARD   0 turns it off (default on)

# Usage

    fixer_guard.py            read the hook input on stdin (Claude Code)
    fixer_guard.py --selftest

The hook fails open: unreadable input allows the call. Exit code: always 0; the decision
travels as JSON on stdout.

No third-party imports: this runs wherever python3 does.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import sys

SEGMENT_SPLIT = re.compile(r"&&|\|\||[;|\n]")
DISCOVERY = re.compile(r"(?:^|/)\.discovery(?:/|$)")


def fixer_segment(tokens: list[str]) -> bool:
    """True when the segment runs a fixer in a mode that WRITES."""
    names = [os.path.basename(t) for t in tokens]
    if not names:
        return False
    if names[0] == "typos":
        return any(t in ("--write-changes", "-w") for t in tokens)
    if names[0] == "taplo":
        return "fmt" in tokens or "format" in tokens
    if names[0] == "cargo":
        return "fix" in tokens[1:3] or ("--fix" in tokens and "clippy" in tokens)
    return "--fix" in tokens


def refusal(command: str) -> str | None:
    for segment in SEGMENT_SPLIT.split(command):
        try:
            tokens = shlex.split(segment)
        except ValueError:
            continue
        if not fixer_segment(tokens) or "--force-exclude" in tokens:
            continue
        named = [t for t in tokens[1:] if not t.startswith("-") and DISCOVERY.search(t)]
        if named:
            return (
                "fixer guard: `%s` names %s under .discovery/. The exclusion in _typos.toml guards "
                "traversal only; a NAMED path is fixed anyway and corrupts recorded evidence (quoted "
                "short SHAs become words). Run the fixer as traversal from the config root "
                "(`typos --write-changes .`), or add --force-exclude and prove `git diff -- "
                ".discovery/` empty afterwards (LRN-0149)." % (tokens[0], named[0])
            )
    return None


def decide(event: dict) -> dict | None:
    if os.environ.get("ASD_FIXER_GUARD", "1") == "0" or event.get("tool_name") != "Bash":
        return None
    tool_input = event.get("tool_input")
    reason = refusal(str(tool_input.get("command", ""))) if isinstance(tool_input, dict) else None
    if not reason:
        return None
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                   "permissionDecisionReason": reason}}


def main() -> int:
    if sys.argv[1:] == ["--selftest"]:
        return selftest()
    try:
        event = json.load(sys.stdin)
        out = decide(event if isinstance(event, dict) else {})
    except Exception:  # fail open, always
        return 0
    if out:
        print(json.dumps(out))
    return 0


def selftest() -> int:
    failures: list[str] = []

    def check(name: str, ok: bool) -> None:
        if not ok:
            failures.append(name)

    def run(command: str, tool: str = "Bash") -> dict | None:
        return decide({"tool_name": tool, "tool_input": {"command": command}})

    os.environ.pop("ASD_FIXER_GUARD", None)
    check("a named dossier under typos --write-changes is denied",
          bool(run("typos --write-changes /r/.discovery/dossiers/W-095.md")))
    check("the reason names LRN-0149", "LRN-0149" in run("typos -w .discovery/x.md")["hookSpecificOutput"]["permissionDecisionReason"])
    check("traversal from the root passes", run("typos --write-changes .") is None)
    check("--force-exclude passes", run("typos --force-exclude --write-changes .discovery/x.md") is None)
    check("a read-only typos run passes", run("typos .discovery/x.md") is None)
    check("taplo fmt of a named record is denied", bool(run("taplo fmt .discovery/a.toml")))
    check("cargo fix over a named record is denied", bool(run("cargo fix --allow-dirty .discovery/x.rs")))
    check("a fixer in the second segment is judged", bool(run("cd /r && typos -w .discovery/a.md")))
    check("a path that merely contains the word passes", run("typos -w src/my.discovery.rs") is None)
    check("other commands are not its business", run("cat .discovery/x.md") is None)
    check("other tools are not its business", run("typos -w .discovery/x.md", tool="Read") is None)
    os.environ["ASD_FIXER_GUARD"] = "0"
    check("ASD_FIXER_GUARD=0 turns it off", run("typos -w .discovery/x.md") is None)
    os.environ.pop("ASD_FIXER_GUARD", None)
    for item in failures:
        print("SELFTEST FAIL  " + item)
    print(f"selftest: {len(failures)} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
