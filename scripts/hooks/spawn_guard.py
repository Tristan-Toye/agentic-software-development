#!/usr/bin/env python3
"""Claude Code PreToolUse hook: refuse a sub-agent spawn the auto mode classifier cannot judge.

In auto mode Claude Code classifies the task a spawn delegates, before the
sub-agent starts, and it sees the spawn prompt — never the files the prompt
points at. The recorded Claude Code runs of 2026-10-05..10 spawned this
plugin's agents with pointer prompts ("your complete payload is the file
<path>; read it whole first; every field binds you"), the opencode
PAYLOAD_FILE form; 22 of ~120 spawns were denied as [Auto-Mode Bypass] or
[Data Exfiltration], and the identical prompt passed on another try. A
prompt that hands all authority to an unseen file is what an injection
looks like, so the verdict is a coin toss. A prompt that carries its fields
is a task the classifier can read — so under Claude Code the payload is
pasted, and this hook is the mechanism that keeps it so.

It denies, with a reason the orchestrator reads, a spawn of one of this
plugin's agents whose prompt:

- is a pointer — it names a payload file (`PAYLOAD_FILE:`, or a path under
  `.agent-staging/`) and carries no payload field of its own (no line
  starting `FIELD_NAME:`);
- points into a Claude Code scratchpad (`/private/tmp/claude-…`,
  `/tmp/claude-…`): a session-private temp path is where exfiltration
  stages data, and a payload belongs under `<worktree>/.agent-staging/`;
- carries a secret-shaped value — credentials in a URL, or a
  `…TOKEN/SECRET/PASSWORD/API_KEY=<literal>` assignment: name the file or
  script that sets it instead.

It never allows anything: a spawn it lets through still goes to the
classifier. Spawns of other agents are not its business.

# Configuration

    ASD_SPAWN_GUARD   0 turns it off (default on)

# Usage

    spawn_guard.py            read the hook input on stdin (Claude Code)
    spawn_guard.py --selftest

The hook fails open: unreadable input allows the call.

Exit code: always 0; the decision travels as JSON on stdout.

No third-party imports: this runs wherever python3 does.
"""

from __future__ import annotations

import json
import os
import re
import sys

PLUGIN = "agentic-software-development:"

FIELD_LINE = re.compile(r"^\s*([A-Z][A-Z0-9_]{2,}):", re.M)
POINTER_FIELDS = {"PAYLOAD_FILE", "PAYLOAD_SHA256"}
STAGED_PATH = re.compile(r"\.agent-staging/\S+")
SCRATCHPAD = re.compile(r"(?:/private)?/tmp/claude-\d+/")
URL_CREDENTIALS = re.compile(r"\b[a-z][a-z0-9+.-]*://[^/\s:@<>$]+:[^/\s@<>$]+@")
SECRET_ASSIGNMENT = re.compile(
    r"\b[A-Z0-9_]*(?:TOKEN|SECRET|PASSWORD|PASSWD|API_KEY|APIKEY)\s*=\s*(?![<$\"'{(])[^\s\"']{8,}"
)


def problems(prompt: str) -> list[str]:
    found: list[str] = []
    fields = set(FIELD_LINE.findall(prompt)) - POINTER_FIELDS
    if not fields and ("PAYLOAD_FILE" in prompt or STAGED_PATH.search(prompt)):
        found.append(
            "the prompt points at a payload file instead of carrying the payload. Under Claude "
            "Code the auto mode classifier judges the spawn by its prompt alone and cannot see "
            "the file, so a pointer reads as handing authority to unseen content. Read the "
            "linted final and paste its fields into the prompt verbatim (one `FIELD: value` "
            "per line, as the skeleton has them), with a one-line task statement above them; "
            "files the payload names (contract, criteria, samples) stay paths"
        )
    if SCRATCHPAD.search(prompt):
        found.append(
            "the prompt names a path in a Claude Code scratchpad (/private/tmp/claude-…). "
            "Stage the payload and any file the agent must read under "
            "<worktree>/.agent-staging/ and name that path"
        )
    if URL_CREDENTIALS.search(prompt) or SECRET_ASSIGNMENT.search(prompt):
        found.append(
            "the prompt carries a secret-shaped value (credentials in a URL, or a "
            "TOKEN/SECRET/PASSWORD/API_KEY assignment). Name the file or script that sets it "
            "(e.g. the repo's env script) and let the agent's command read it there"
        )
    return found


def decide(event: dict) -> dict | None:
    """The hook output for one PreToolUse event, or None to let it through."""
    if os.environ.get("ASD_SPAWN_GUARD", "1").strip().lower() in ("0", "false", "off", "no"):
        return None
    if event.get("tool_name") not in ("Agent", "Task"):
        return None
    tool_input = event.get("tool_input") or {}
    if not str(tool_input.get("subagent_type", "")).startswith(PLUGIN):
        return None
    found = problems(str(tool_input.get("prompt", "")))
    if not found:
        return None
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": "spawn guard: " + "; and ".join(found) + ". Fix the prompt and spawn again.",
        }
    }


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

    def spawn(prompt: str, agent: str = PLUGIN + "reviewer", tool: str = "Agent") -> dict | None:
        return decide({"tool_name": tool, "tool_input": {"subagent_type": agent, "prompt": prompt}})

    os.environ.pop("ASD_SPAWN_GUARD", None)
    recorded = (
        "Your complete payload is the file /Users/x/repo-W-093/.agent-staging/payloads/rv-architecture.md. "
        "Read it whole first. Its fields — LENS, WORKTREE_DIR, SCOPE — bind you."
    )
    check("a recorded pointer prompt is denied", bool(spawn(recorded)))
    check("the opencode two-line form is denied",
          bool(spawn("PAYLOAD_FILE: /r/.agent-staging/p.md\nPAYLOAD_SHA256: abc")))
    pasted = (
        "Review the merged implementation through one lens.\n"
        "LENS: architecture\nWORKTREE_DIR: /Users/x/repo-W-093\n"
        "CONTRACT: /Users/x/repo-W-093/.agent-staging/contract/llm.rs\nROUND: 1\n"
    )
    check("a pasted payload naming staged files passes", spawn(pasted) is None)
    scratch = pasted + "CONTEXT_DOCS: /private/tmp/claude-501/-Users-x/abc/scratchpad/rev/architecture.md\n"
    check("a scratchpad path is denied", bool(spawn(scratch)))
    check("credentials in a URL are denied",
          bool(spawn(pasted + "HARNESS: DATABASE_URL=postgres://app:hunter22@localhost/db\n")))
    check("a token literal is denied", bool(spawn(pasted + "HOOKS: GH_TOKEN=ghp_abcdefghijklmnop\n")))
    check("a named env variable without a value passes",
          spawn(pasted + "HARNESS: DATABASE_URL comes from deploy/host/test-env.sh\n") is None)
    check("a placeholder assignment passes", spawn(pasted + "HOOKS: API_KEY=<from the env file>\n") is None)
    check("a placeholder URL passes", spawn(pasted + "HARNESS: postgres://<user>:<pass>@host/db\n") is None)
    check("other agents are not its business", spawn(recorded, agent="Explore") is None)
    check("other tools are not its business", spawn(recorded, tool="Bash") is None)
    check("the legacy Task name is covered", bool(spawn(recorded, tool="Task")))
    reason = spawn(recorded)["hookSpecificOutput"]["permissionDecisionReason"]
    check("the reason says how to fix it", "paste" in reason)
    os.environ["ASD_SPAWN_GUARD"] = "0"
    check("ASD_SPAWN_GUARD=0 turns it off", spawn(recorded) is None)
    os.environ.pop("ASD_SPAWN_GUARD", None)
    for item in failures:
        print("SELFTEST FAIL  " + item)
    print(f"selftest: {len(failures)} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
