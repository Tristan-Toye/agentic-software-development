# Token-spend ledger

Every change made to cut what the pipeline spends on model tokens, why it was
made, what it should do, and how to check that it did. A hypothesis here is a
claim about a number `scripts/token_report.py` prints. Results are measured,
never estimated; an estimate is marked as one.

## How to measure

```bash
# one window, printed and saved
python3 scripts/token_report.py --since <start> --until <end> --json ledger/metrics/<date>_<name>.json
# the same, compared with an earlier snapshot
python3 scripts/token_report.py --since <start> --compare ledger/metrics/<earlier>.json
```

The report reads the opencode store (`~/.local/share/opencode/opencode.db`,
read-only) and the plugin logs (`phase-checkpoint/phase-checkpoint.log`,
`context-guard/context-guard.log` under `~/.local/share/opencode/`). Credits use
the Z.ai coding-plan multipliers per 10,000 tokens: glm-5.3 input 6.9, cached
input 1.7, output 24; glm-5.3-flash 2.3 / 0.56 / 8; reasoning counts as output.
An orchestrator run is a top-level `work-on` session of 50 steps or more.

Saved snapshots:

| File | Window | What it is |
|---|---|---|
| `metrics/2026-09-21_baseline-before-pr86.json` | 2026-09-21 → 2026-09-28 19:29:50 (cutoff) | Baseline: 3 runs (W-067, W-072, W-086) |
| `metrics/2026-09-28_after-pr86-to-92.json` | cutoff → 2026-10-04 23:59 | After PRs #86–#92: 4 runs (W-069, W-073, W-091, W-095 inquiry), run in parallel |

## Results so far (baseline → after #86–#92)

| Metric | Before | After | Change |
|---|---|---|---|
| Credits per orchestrator run | 28,739 | 26,619 | −7% |
| Steps per orchestrator run | 700 | 904 | **+29%** |
| Credits per orchestrator step | 40.8 | 29.5 | −28% |
| Orchestrator average context | 214k | 153k | −28% |
| Orchestrator peak context (mean of runs) | 367k | 348k | −5% |
| Subagent credits per run | 8,706 | 3,616 | −58% |
| First-step context, `work-on` | 71.0k | 32.9k | −54% |
| First-step context, subagents | 41–48k | 12–26k | −45 to −70% |
| `sed`/`cat`/`head`/`tail` reads per run | 313 | 453 | +45% |
| Raw `limactl … cargo test` per run | 19 | 19 | 0% |

Orchestrator carried context by kind, after: reasoning 34.9%, read output
24.3%, bash output 14.8%, bash input 7.2%, task input 4.4% (from `carried` in
the after snapshot).

Reading: every step got cheaper, but runs took more steps, so a run cost only
7% less. The orchestrator is 86% of all spend. Four runs in parallel spent
123k credits in three days against a 60k weekly cap.

## Changes

Each entry: what changed, the evidence it answered, the hypothesis as a
measurable claim, how to verify, and the result.

### C-001 — Phase-boundary context reset (PR #86)

- **Change**: `opencode/plugins/phase-checkpoint.ts` — the orchestrator calls
  `phase_checkpoint` at each phase end; later requests drop every earlier step
  and keep the task plus the phase summaries.
- **Why**: baseline runs grew to 441k tokens and never compacted; steps past
  number 400 cost 39% of a week.
- **Hypothesis**: orchestrator average context −40 to −50%; credits per run −35
  to −50%.
- **Verify**: `runs.*.avg_ctx_k`, `runs.*.credits`, `runs.*.checkpoints`.
- **Result**: partly confirmed. Cuts work (326k → 36k at a cut). Average
  context −28%, credits per run −7%: context grows back inside long phases
  (Phase 4 and Phase 6 cost 47k of 106k), and steps per run rose 29%.
  Phases 0–1 were never checkpointed in 3 of 4 runs.

### C-002 — Split `work-on.md`, `payloads.md`, `formats.md` (PR #86)

- **Change**: the 118 KB command became a 17 KB core plus one file per phase;
  payloads and formats became one file per reader.
- **Why**: the fixed prompt (29k tokens) rode on every one of ~2,100 steps.
- **Hypothesis**: `work-on` first-step context below 40k.
- **Verify**: `agents.work-on.first_step_ctx_k`.
- **Result**: confirmed — 71.0k → 32.9k.

### C-003 — Subagent tool and skill diet; Twipe DevKit scoped; opencode-mem off (PR #86)

- **Change**: subagents deny envsitter, worktree, ctx (read-only agents) tools
  and all skills but `standards`; Twipe DevKit tools and skills only in the
  default agent; opencode-mem removed.
- **Why**: 27 unused tools and a 64-skill catalogue rode on every subagent step.
- **Hypothesis**: subagent first-step context below 30k; subagent credits per
  run −40%.
- **Verify**: `agents.<subagent>.first_step_ctx_k`, subagent credits per run.
- **Result**: confirmed — 41–48k → 12–26k; 8,706 → 3,616 credits per run (−58%).

### C-004 — DCP `compress` off (PR #86)

- **Change**: `opencode/dcp.jsonc` — `compress.permission: deny`.
- **Why**: 212 model-called compressions cost ~11k credits for an ~8k-token
  drop that grew back in 5–10 steps.
- **Hypothesis**: no `compress` calls; no rise in average context from it.
- **Verify**: no tool part named `compress` in the window (`tools`).
- **Result**: confirmed — no `compress` call after the cutoff.

### C-005 — Tool output cap 20 KB (PR #86)

- **Change**: `tool_output.max_bytes` 50 KB → 20 KB.
- **Why**: single test logs and case lists entered context at ~12k tokens.
- **Hypothesis**: fewer large outputs; no more than +10% read steps.
- **Verify**: `tools.read calls`, `bypass:sed/cat/head/tail reads` per run.
- **Result**: side effect larger than expected — paging reads per run +45%.
  The cap without a structural read tool turned one read into several steps.
  C-103 answers it.

### C-006 — Mechanical scripts (PR #86)

- **Change**: `dossier_edit`, `prepare_wave`, `compose_payloads`, `git_state`,
  `run_tests`, `wait_ci`, `run_gates`.
- **Why**: ~25% of orchestrator steps were deterministic rituals.
- **Hypothesis**: each script used; raw equivalents fall.
- **Verify**: `tools.script:*`, `tools.bypass:*`.
- **Result**: adopted (dossier_edit 220, run_tests 88, prepare_wave 70,
  compose_payloads 57, wait_ci 32 calls) but raw `limactl … cargo test` stayed at
  19 per run: `run_tests.py` could not run detached and printed a command that
  expanded `$HOME` in the wrong shell. C-102 and C-105 answer it.

### C-007 — Runaway guards (PR #86, #90)

- **Change**: subagent `steps` limits, `doom_loop: deny`, bounded context7 rule;
  Claude Code `maxTurns` and a repeat-guard hook.
- **Why**: two runaway sessions cost 9.7k credits.
- **Hypothesis**: no subagent session above its step limit.
- **Verify**: steps per subagent session (not yet in `token_report.py`; the
  per-agent max context is a proxy).
- **Result**: no runaway session in the after window (largest implementer
  context 177k, against 463k before).

### C-101 — Batched masking of old tool output and reasoning (this PR)

- **Change**: `opencode/plugins/context-guard.ts` — once a request passes
  `trigger_tokens` (100k), tool outputs older than the newest 10 steps and
  reasoning older than the newest 20 steps become short stubs in the outgoing
  request; the masked set only moves at the next trigger, so the provider
  cache holds between moves. Phase files, subagent returns and checkpoints are
  never masked. Config: `opencode/plugins/context-guard.yaml`.
- **Why**: carried context after C-001 was 34.9% reasoning, 24.3% read output,
  14.8% bash output. A replay of the four runs (masking after 10 steps)
  estimated −33% carried context for tool outputs alone and −62% with
  reasoning (estimate; the fixed prompt is excluded).
- **Hypothesis**: orchestrator average context −35 to −50% and credits per
  step −30 to −45% against the after-#86 snapshot; steps per run rise by less
  than 10% (re-fetching a masked output costs a step).
- **Verify**: `runs.*.avg_ctx_k`, `runs.*.credits_per_step`, `runs.*.mask_moves`
  > 0, `carried.reasoning` and `carried.read output` lower, `runs.*.steps`.
- **Risk to watch**: reasoning masking may cost coherence (Z.ai asks for
  earlier thinking to be returned). If steps per run rise by more than 10% or
  a run repeats work, set `keep_reasoning_steps: 0` and compare again.
- **Result**: —

### C-102 — Read guard (this PR)

- **Change**: `context-guard.ts` refuses, for `work-on`: a whole read of a
  dossier, a whole read of a file over 20 KB, a plain `cat` of such a file,
  and `cargo test` through `limactl` without `run_tests.py`. The refusal names
  the tool to use.
- **Why**: whole dossier reads continued against the written rule; raw VM test
  commands stayed at 19 per run.
- **Hypothesis**: `bypass:whole-file read > 20 KB`, `bypass:whole dossier read`
  and `bypass:raw limactl cargo test` near 0; refusals below 20 per run.
- **Verify**: `tools.bypass:*`, `plugin_events.context-guard:refused`.
- **Result**: —

### C-103 — Code and document navigation tools (this PR)

- **Change**: `scripts/code_nav.py` plus `opencode/plugins/code-nav.ts` —
  `code_outline`, `code_item` (whole item, contract, doc or signature),
  `code_find`, `doc_section`, `code_replace` for Rust, Python and markdown.
- **Why**: Rust reads and `sed` paging were ~1.9M chars in four runs; grep
  lookup chains 10% of credits; `RULES.md` and `learned-rules.md` read whole.
- **Hypothesis**: paging reads per run −50%; `read output` share of carried
  context −30%; steps per run −10 to −20%.
- **Verify**: `tools.tool:code_*` > 0, `tools.bypass:sed/cat/head/tail reads`,
  `carried.read output`, `runs.*.steps`.
- **Result**: —

### C-104 — Checkpoints inside long phases (this PR)

- **Change**: `references/work-on/phase-4.md` and `phase-6.md` — a checkpoint
  after each wave and each arbitration round, when the build log holds the
  round's outcome.
- **Why**: Phases 4 and 6 grew back to 300–450k tokens over 170–340 steps.
- **Hypothesis**: more than 10 checkpoints per run; peak context −30%.
- **Verify**: `runs.*.checkpoints`, `runs.*.max_ctx_k`.
- **Result**: —

### C-105 — Mechanical batch (this PR)

- **Change**: `run_tests.py --detach/--wait/--digest/--repeat/--test` and the
  `$HOME` fix; `wait_ci.py --branch/--idle-first/--job-log/--rerun-failed`;
  `gate_commit.py`; `verify_return.py`; `prepare_wave.py --lint-only/--wait-slot`
  and payload hashes; `compose_payloads.py` duplicate-CRITERIA fix,
  `--refresh-contract`, `--failures-from`, `--excerpt`; `dossier_edit.py
  remove/set list/replace-section`.
- **Why**: the deterministic-work inventory of the four runs: return checks
  5.8k credits, hand-run admission and lint 7.2k, raw CI log fetches 3k,
  hand-run VM tests 3.6k (estimates from the inventory).
- **Hypothesis**: steps per run −10%; `script:gate_commit`,
  `script:verify_return` used; raw `gh run`/`spawn_admission` calls fall.
- **Verify**: `tools.script:*`, `runs.*.steps`.
- **Result**: —

### C-106 — Spawn by payload file (this PR)

- **Change**: under opencode the orchestrator spawns with `PAYLOAD_FILE: <path>`
  and the agent reads its linted payload itself, instead of retyping it.
- **Why**: 1.77M chars of spawn prompts were retyped as output tokens (×24
  cost) and then carried; task input was 4.4% of carried context.
- **Hypothesis**: `carried.task input` below 1.5%.
- **Verify**: `carried.task input`.
- **Result**: —

### C-107 — Flash `scribe` and `code-scout` subagents (this PR)

- **Change**: `sub-agents/scribe.md` (dossier and document prose edits from an
  instruction; never tests, docstrings or code) and `sub-agents/code-scout.md`
  (read-only lookups returning `path:line` rows), both on glm-5.3-flash.
- **Why**: lookup chains cost 10% of credits; dossier prose edits ran on the
  orchestrator's model and context.
- **Hypothesis**: orchestrator `bash grep/rg` per run −40%; scribe and scout
  credits below 10% of the orchestrator credits they replace.
- **Verify**: `tools.bash grep/rg`, `agents.scribe`, `agents.code-scout`.
- **Result**: —

## Next measurement

After the next runs, from the merge time of this PR:

```bash
python3 scripts/token_report.py --since <merge time> --json ledger/metrics/<date>_after-context-guard.json \
  --compare ledger/metrics/2026-09-28_after-pr86-to-92.json
```

Fill each **Result** above from that output. Overall hypothesis for this PR:
credits per orchestrator run 26.6k → 12–16k, steps per run 904 → 750 or fewer.
