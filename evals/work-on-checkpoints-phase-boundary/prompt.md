---
model: haiku
max_turns: 8
timeout_seconds: 180
runs: 2
allowed_tools: [Read, Glob, Grep]
expected_outcome: Calls the phase_checkpoint MCP tool with a "Phase 1" name and a hand-off summary, then stops before Phase 2.
---

/agentic-software-development:work-on W-014

Phase 1 of this run has just ended: the Build log records its outcome. Do what the command requires at the end of a phase, then stop. Do not start Phase 2.
