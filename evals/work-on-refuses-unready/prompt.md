---
model: haiku
max_turns: 8
timeout_seconds: 180
runs: 2
allowed_tools: [Read, Glob, Grep]
expected_outcome: Refuses, names the status `planned`, tells the user to run /plan W-014 first. No spawn, no claim.
---

/agentic-software-development:work-on W-014
