#!/usr/bin/env python3
"""Claude Code PreToolUse hook: refuse a tool call that repeats itself in a row.

The recorded opencode runs had one agent make the same call 277 times in a
row, each time paying its whole context again, and the host's own repeat
guard never stopped it. Claude Code has no built-in guard against this, so
this hook is the mechanism: when the same tool is called with the same input
more than LIMIT times in a row in one session, the next identical call is
denied with a reason the model reads. Any different call resets the count,
so a re-run after an edit is never refused.

# Configuration

    ASD_REPEAT_GUARD_LIMIT   identical calls in a row that are allowed
                             (default 3: the 4th is denied); 0 turns it off

State is one small JSON file per session under $CLAUDE_PLUGIN_DATA (or the
system temp directory when that is unset).

# Usage

    repeat_guard.py            read the hook input on stdin (Claude Code)
    repeat_guard.py --selftest

The hook fails open: unreadable input or state allows the call, because a
guard that breaks a session costs more than the loop it stops.

Exit code: always 0; the decision travels as JSON on stdout.

No third-party imports: this runs wherever python3 does.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import tempfile

DEFAULT_LIMIT = 3


def limit() -> int:
    try:
        return max(0, int(os.environ.get("ASD_REPEAT_GUARD_LIMIT", DEFAULT_LIMIT)))
    except ValueError:
        return DEFAULT_LIMIT


def state_path(session_id: str) -> str:
    base = os.environ.get("CLAUDE_PLUGIN_DATA") or os.path.join(tempfile.gettempdir(), "asd-repeat-guard")
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", session_id)[:120] or "no-session"
    return os.path.join(base, f"{safe}.json")


def fingerprint(tool: str, tool_input: object) -> str:
    raw = json.dumps([tool, tool_input], sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode()).hexdigest()


def decide(event: dict, allowed: int) -> dict | None:
    """The hook output for one PreToolUse event, or None to allow silently."""
    if allowed == 0:
        return None
    tool = str(event.get("tool_name", ""))
    fp = fingerprint(tool, event.get("tool_input"))
    path = state_path(str(event.get("session_id", "")))
    try:
        with open(path, encoding="utf-8") as fh:
            state = json.load(fh)
    except (OSError, ValueError):
        state = {}
    count = state.get("count", 0) + 1 if state.get("fingerprint") == fp else 1
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"fingerprint": fp, "count": count, "tool": tool}, fh)
    except OSError:
        return None
    if count <= allowed:
        return None
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": (
                f"repeat guard: this exact {tool} call ran {allowed} times in a row already "
                f"(this is call {count}), and its result will not change. Use the result you "
                "have, change the input, or take a different step."
            ),
        }
    }


def main() -> int:
    if sys.argv[1:] == ["--selftest"]:
        return selftest()
    try:
        event = json.load(sys.stdin)
        out = decide(event if isinstance(event, dict) else {}, limit())
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

    with tempfile.TemporaryDirectory() as tmp:
        os.environ["CLAUDE_PLUGIN_DATA"] = tmp
        call = {"session_id": "s1", "tool_name": "Bash", "tool_input": {"command": "git status"}}
        results = [decide(call, 3) for _ in range(5)]
        check("three identical calls pass", results[:3] == [None, None, None])
        check("the 4th is denied", bool(results[3]) and results[3]["hookSpecificOutput"]["permissionDecision"] == "deny")
        check("the 5th stays denied", bool(results[4]))
        check("reason names the count", "call 5" in results[4]["hookSpecificOutput"]["permissionDecisionReason"])
        other = {"session_id": "s1", "tool_name": "Edit", "tool_input": {"file_path": "/a"}}
        check("a different call resets", decide(other, 3) is None and decide(call, 3) is None)
        check("sessions are separate", decide({**call, "session_id": "s2"}, 3) is None)
        key_order = {"session_id": "s3", "tool_name": "Read", "tool_input": {"b": 1, "a": 2}}
        swapped = {"session_id": "s3", "tool_name": "Read", "tool_input": {"a": 2, "b": 1}}
        for _ in range(3):
            decide(key_order, 3)
        check("key order does not hide a repeat", bool(decide(swapped, 3)))
        check("limit 0 turns it off", all(decide(call, 0) is None for _ in range(10)))
        check("a hostile session id stays in the state dir",
              os.path.dirname(state_path("../../etc/x")) == tmp)
    for item in failures:
        print("SELFTEST FAIL  " + item)
    print(f"selftest: {len(failures)} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
