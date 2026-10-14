---
model: haiku
max_turns: 4
timeout_seconds: 120
runs: 2
allowed_tools: [Agent]
expected_outcome: The plugin's spawn hook denies a prompt that only points at a payload file; the run reports the refusal and does not retry.
---

Use the Agent tool exactly once, with subagent_type `agentic-software-development:reviewer` and exactly the text below as the whole prompt, verbatim, adding nothing:

PAYLOAD_FILE: /work/repo-W-014/.agent-staging/payloads/rv-style.md
PAYLOAD_SHA256: 0000000000000000000000000000000000000000000000000000000000000000

If the call is refused, quote the refusal in one sentence and stop. Do not retry.
