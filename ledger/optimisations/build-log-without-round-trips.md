# Build log without round trips

Status: research and design only. No product code changed.

## 1. Problem

The orchestrator spends model requests that do nothing but write the build log (`dossier_edit.py log`) or post an owner note (`ask_owner.py note`). Each such request re-reads the whole cache.

Owner measurement: 1,209 of 9,670 tool-using requests (12.5%), 354 transcripts in ~/.claude-main and ~/.claude-gmail, 2026-11-09 to 11-12, about 150M cache-read tokens per week.

Re-measurement with `ledger/optimisations/scripts/bookkeeping_requests.py` (read-only, re-runnable; saved output: `scripts/bookkeeping_requests.2026-11-12.out.txt`). Window 2026-11-09 to 11-13, orchestrator requests only (sidechain and synthetic messages skipped, JSONL lines merged by message.id):

| Tier | Definition | Requests | Share | Cache-read |
|---|---|---|---|---|
| 1 pure | every tool_use is a log or note Bash call | 304 | 3.3% | 32.2M (3.8%) |
| 2 contains | a log/note call is in the request | 1,081 | 11.8% | 108.7M |
| 3 trivial | log/note plus trivial companions | 436 | - | 46.4M |

Total: 9,141 tool-using requests, 855.7M cache-read.

Finding: the owner number (12.5%) matches tier 2. It counts requests that merely contain a log or note. Most of those already share a round trip with other work. Only the 304 pure requests are extra round trips: about 56M cache-read per week.

Pure request mix: owner note present 180 (59%), log lines of judgement kind without a note 92, mechanical only 30, no-consumer only 1, mixed 1. Lines per log call: 1 line 112, 2 lines 39, 3 lines 24, 4 lines 14, 5 lines 6, 6+ lines 35. Owner notes: 467 in the window, 238 characters on average.

What follows a pure request: Bash 146, phase_checkpoint 47, another log/note 43, ToolSearch 32, end of turn 13, Read 7, Workflow 7. So 291 of 304 (96%) were followed by a tool call they could have been batched with. 181 of 304 follow a Bash request, but their text usually depends on that result, so "chain into the previous call" does not work. "Emit it in the same message as the next action" does.

Side finding: 134 ToolSearch-only requests (12.7M cache-read, 1.5%), probably loading deferred MCP schemas such as phase_checkpoint each stage. Claude Code documents `alwaysLoad` (server config) and `anthropic/alwaysLoad` (tool `_meta`) to avoid deferral (mcp.md, "exempt a server from deferral"). Unverified here: that it removes these requests.

## 2. What the build log is for

The owner's assumption "restartability is the only purpose" is false. Restart is one of about eight consumers, and it is the weakest one: the runner hand-off does not read the log.

| Consumer | Where | What it needs |
|---|---|---|
| Runner hand-off | scripts/run_dossier.py:1306-1323 (handoff), :1826 (record) | Only checkpoints (name, summary, NEXT). Does not read the log |
| No-checkpoint fallback | run_dossier.py:1256-1267 | "the build log is the hand-off" |
| Resume | run_dossier.py:1412 (mark_resume); phase-0.md:45 | `building`: read the log, find merged packages |
| Agent IDs for resume | phase-4-incidents.md:165-170 | SPAWNED and RETURNED lines |
| Compaction restore | shared/claude/common.ts:410-435 (buildLogTail), session-start.ts:54 | Last 40 lines |
| Validator gates | validate_pipeline.py:733 check_build_log; :2177 ADMISSION_RE; :2189 SUITE-RUNNER; :2196 CHECKLIST-IDS; :2217; :2357 PRE_FANOUT_LINES; :2455-2512 --pre-fanout | "Lesson:" on ARBITRATION, PROMISE_CHECKLIST for review/pr/done, DEFERRED for done |
| Blocker guard | scripts/dossier_edit.py:188-198 | BLOCKER-REMOVED line |
| Metrics | scripts/budget_report.py:379-388 QUALITY_MARKERS, :401, :429 | RETURNED, ARBITRATION, DEFECT, CORRECTION, WORKFLOW:, GAP:, CONTRACT-CHANGE:, CR-n |
| Dashboard | shared/questions/runs.ts:55-120 (deferred state machine), :192, :218, :591-616, :665; timeline.ts:36, :362, :391; server.ts:455 | DEFERRED-* and OWNER-RULING lines, last activity |
| Phase and command material | phase-6, phase-7:92, phase-8:36 (ADR), phase-9:100,193-204 (ledger, PR text); commands/deferred.md:54,64; fix-deferred.md:17,39,178; product-summary.md:21; overview-dossiers.md; after-merge.md:50 | Decisions, deferred items, review ledger |
| Gate receipt | scripts/run_gates.py:95 | A log-only commit keeps the receipt |
| Format rule | references/formats/dossier.md:256 | The log replaces the changelog, review files and anomaly log |

## 3. Classification

(a) mechanical from a tool event, (b) needs the model's judgement text, can ride on a call it makes anyway, (c) no code consumer found (humans only).

| Log kind | Class | Mechanism |
|---|---|---|
| RETURNED | a | `verify_return.py --log DOSSIER --agent ID` (exists, :637, :708-710), run by the verifier |
| WORKFLOW | a | PostToolUse hook on the Workflow tool; tool_response has the run id (completion-hook behaviour unverified) |
| SUITE, SUITE-RUN, RED-RUN, CI, SYNC, MERGE, PR, COMMIT, WORKTREES | a | `--log DOSSIER` on run_tests, wait_ci, git_state, merge_dossier |
| FAILURE-SIGNALS, LOG-SIGNALS, SECURITY-TRIGGERS, BLAST-RADIUS, EXCLUDED, RULES, FLAKE-RISK, TESTS-LENS | a | `--log` on review_signals.py (prints the lines, :21) and flake_risk.py |
| PAYLOAD-LINT, ADMISSION | a | already self-logged by prepare_wave.py `--log` (:223, :267, :298) |
| CHECKLIST-IDS | a | compose_payloads / validator output |
| REVIEW-LENS-ROUND | a | phase7 workflow returns crs and answers |
| QUESTION, OWNER-RULING | a | ask_owner.py ask and reply write the line |
| DEFERRED-FIXED, DEFERRED-FIX, DEFERRED-SOURCE, NOTICED (from agent returns) | a | phase4-wave.js:98 NOTED_LINE |
| ARBITRATION, AMBIGUITY-SETTLED, CONTRACT-REVISION, CONTRACT-FIX, CONTRACT-REVIEW, RULING, CENSUS, GATES, MUTATION, CASE, HOOKS | b | `log` parameter on phase_checkpoint |
| NOTICED (own), DEFERRED header, ledger items, TRIAGE | b | same |
| Owner notes | b | `note` parameter on phase_checkpoint, or a haiku writer from the checkpoint summary |
| ENV, PHASE, WAVE, STABILITY, narrative prose | c | validate_pipeline reads none of it (phase-4-incidents.md). Keep greppable, drop or batch |

Share of 2,215 log lines: a 41.1% (911), b 54.2% (1,200), c 4.7% (104). Pure requests are dominated by owner notes (59%) and class b, so hooks alone cannot remove most of them.

## 4. Options

| # | Mechanism | Effort | Risk to restart and quality | Saves (of 304 pure) |
|---|---|---|---|---|
| 1 | Prompt rule: put log/note in the same message as the next action | very low (phase files, work-on.md) | model may ignore it; no loss of content | up to 96% in theory; real share lower, measure |
| 2 | `dossier`, `log`, `note` parameters on phase_checkpoint (one call the model makes anyway, 47 pure requests precede one) | low-medium: shared/tools.ts, shared/checkpoint.ts, mcp.ts, phase-checkpoint.ts | none if validators stay | removes b lines and notes that sit at stage ends |
| 3 | Scripts self-log with `--log` (run_tests, wait_ci, review_signals, flake_risk, ask_owner, merge) | medium, pattern exists | needs flock (race, see below) | removes class a, about 10% of pure requests, more of the lines |
| 4 | PostToolUse hook writes the line (Workflow, Agent) | medium: dossier path unknown to the hook | silent when bun missing, hook timeout 5, PostToolUse never fires on failure | small; fragile |
| 5 | Haiku writer for owner notes from the checkpoint summary | medium | note quality, extra cost per note (small model) | removes the 59% owner-note share |
| 6 | Drop class c | trivial | none found | 1% |

Hooks are the weakest option: the hook does not know the dossier path (the key is `<repo>.<ID>`; the live dossier is in a worktree, and budget_report.dossier_path picks the copy with the longest log), and there is no run-updates hook on opencode (the runner launches only claude). Scripts and the shared checkpoint tool work on both hosts.

## 5. Recommended design

1. phase_checkpoint gets optional `dossier`, `log` (string array) and `note`. The side effect lives once in shared/checkpoint.ts, called from shared/claude/mcp.ts callTool (:40-56) and opencode/plugins/phase-checkpoint.ts:171. It runs dossier_edit.py log, then ask_owner.py note. Host parity by construction.
2. dossier_edit.py: add `fcntl.flock` around write and append_log (now mkstemp + os.replace without a lock, :87-93, :144) and skip an identical duplicate of the last line. Add selftests. This makes hooks, verifiers and parallel calls safe.
3. Add `--log DOSSIER` to run_tests.py, wait_ci.py, review_signals.py, flake_risk.py, ask_owner.py ask/reply and the merge scripts. The workflow verifier calls verify_return `--log`.
4. Optional PostToolUse(Workflow) hook in shared/claude/ writing the WORKFLOW line. The dossier path comes from a state file written by dossier_edit.py or compose_payloads. Add a PostToolUseFailure entry (the plugin hooks.json has none), because a failed gate or test is never mirrored today.
5. Prompt rule in primary-agents/work-on.md and phase files: the log/note call goes in the same assistant message as the next action. An optional nudge hook feeds back when a pure bookkeeping request occurs. Keep `validate_pipeline.py --pre-fanout` as the safety net: it detects missing lines.

Failure modes:
- Hook not firing: bun missing, timeout 5, `|| true` (all hooks are best effort). Mitigation: scripts and the checkpoint tool carry the write; hooks only mirror.
- PostToolUse fires only on success (hooks.md); failures need PostToolUseFailure (`error` starts "Exit code N").
- Hooks fire for the orchestrator only (no agent_id), so sub-agent logs must come from scripts.
- Races without flock lose lines.
- Duplicates when the model also logs: dedupe identical lines.
- opencode: tool.execute.after exists (phase-checkpoint.ts:199 uses it) but there is no run-updates store hook; put logic in scripts and the shared tool.
- Workflow completion is asynchronous. A background launch returns `async_launched`; whether a hook fires on completion is unverified. Do not depend on it.

## 6. Migration and verification

1. Land the flock and dedupe in dossier_edit.py first (`dossier_edit.py --selftest`, :790). Add a parallel-append test.
2. Add `--log` to each script one at a time, each with its own selftest; add a test that the produced line still satisfies validate_pipeline.
3. Add the checkpoint parameters; extend shared/test/claude-hooks.test.ts and shared/test/questions-server.test.ts; test both hosts call the same shared function.
4. Change the prompts (rule 5) last, so measurement separates the effects.
5. Run `run_dossier.py --selftest` (:2991) plus the verify_return, budget_report and validate_pipeline selftests.
6. Crash-resume test: start a run, let the stage log lines through the new paths, kill the stage after a checkpoint and again before one. Check (a) the hand-off built from checkpoints, (b) `no_checkpoint` fallback still finds the lines in the log, (c) the compaction restore tail shows the lines, (d) `--pre-fanout` passes.
7. Keep the dossier text identical (same kinds, same wording) so dashboard, budget_report and validators need no change.

## 7. Expected savings and re-measure

- Strict: 304 pure requests in 4 days, 32.2M cache-read, about 56M per week (not 150M: the owner figure counts every request that contains a log or note).
- Class a only (scripts and hooks): about 10% of pure requests, more of the lines.
- Owner notes and class b through the checkpoint parameters and the same-message rule: most of the other 90%. Realistic target 60-90% of 56M, so about 35-50M cache-read per week. Upper bound 96% if the model always batches.
- Side: ToolSearch-only requests, 12.7M in the window, a separate fix (alwaysLoad), unverified.
- Re-measure: `python3 ledger/optimisations/scripts/bookkeeping_requests.py --since <rollout date> --until <date>` and compare tier 1 counts and cache-read to the 2026-11-09..13 baseline.

## 8. Sources

- https://code.claude.com/docs/en/hooks.md (PostToolUse, PostToolUseFailure, PostToolBatch, SubagentStop, PreCompact)
- https://code.claude.com/docs/en/mcp.md (tool search, alwaysLoad, `anthropic/alwaysLoad`)
- opencode plugins: https://opencode.ai/docs/plugins (context7 /websites/opencode_ai_plugins): tool.execute.before/after, event, session.idle, session.compacted
- Repo: scripts/dossier_edit.py:87-93,96,144,188-198,709,790; scripts/run_dossier.py:1256-1267,1306-1326,1412,1826,2991,4383; scripts/verify_return.py:637,708-710; scripts/prepare_wave.py:223,267,298; scripts/review_signals.py:21; scripts/validate_pipeline.py:733,2177,2189,2196,2217,2357,2455-2512; scripts/budget_report.py:379-388,401,429; scripts/run_gates.py:95
- shared/tools.ts (phase_checkpoint ~:183); shared/claude/mcp.ts:40-56; opencode/plugins/phase-checkpoint.ts:171,199; shared/claude/post-tool-use.ts:32-48; shared/claude/common.ts:410-435; shared/claude/session-start.ts:54; shared/questions/runs.ts; shared/questions/timeline.ts; workflows/phase4-wave.js:98; phase-4.md:427,433; phase-4-incidents.md:165-170; references/formats/dossier.md:256
- Measurement: ledger/optimisations/scripts/bookkeeping_requests.py
