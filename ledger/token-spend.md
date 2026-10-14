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
| `metrics/2026-10-14_budget-per-dossier.json` | 2026-09-14 → 2026-10-14 09:30 UTC | `budget_report.py`: every dossier on both hosts as a share of each plan's week |
| `metrics/2026-10-14_budget-per-dossier-with-quality.json` | the same window | the same, plus build-log quality per dossier and the seat's week calibrated from the usage log ($1,067): **the baseline for the next eval** |

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

## Budget per dossier across hosts

`scripts/budget_report.py` prices one dossier (a `W-<n>` id, resumes and
subagents included) on each host and divides it by each plan's week. Plan
prices and quotas live in `ledger/plans.json`, the one place to edit them.

```bash
python3 scripts/budget_report.py --since <start> --until <end> --json ledger/metrics/<date>_budget-per-dossier.json \
  [--compare ledger/metrics/2026-10-14_budget-per-dossier.json]
```

- **opencode (GLM coding plan)**: credits per request from the store, at half
  rate outside Mon–Fri 06:00–10:00 UTC (the off-peak rate; the Sept 24–26 week
  is consistent with it: 125.8k credits at the standard rate exhausted the 60k
  Pro week). Shares use these billed credits.
- **Claude Code (claude.ai seat)**: transcripts priced at API rates per model.
  The seat's week in API dollars is calibrated from `~/.claude/usage-log.jsonl`
  (the status-line sampler logs the account's weekly %) once the log spans a
  rise of 5 points or more; until then `week_usd` in `plans.json`: **≥ $1,010**,
  the local spend since the reset ($414) over the 41% shown at 2026-10-14 08:53
  UTC. Use outside Claude Code also counts toward the %, so this is a lower
  bound and each Claude share an upper bound.
- A run with fewer than 300 requests (an inquiry, or a run in progress) is
  listed but left out of the medians.
- **Quality per dossier** comes from its `## Build log`: returns, flagged returns,
  arbitrations, defects, corrections and re-spawns, `GAP:`s and `CONTRACT-CHANGE:`s
  (negated mentions such as "no `GAP:`" excluded), distinct change requests, workflow
  runs, and the `## Work packages` row count to normalise by. A build writes its log in
  its own worktree, so the copy with the longest log across the repo's worktrees is read.
  Only logs in the current format (one `RETURNED` line per return) enter the medians;
  older prose logs would count as zero. These are indicators to compare runs by, not
  exact tallies.
- A Claude dossier counts every transcript under its session directory: the main one,
  `subagents/`, and a workflow run's agent transcripts, each API response once.
- **opencode on the openai profile** (`opencode-openai`): OpenAI's own
  published Codex credit rates per model (`plans.json` → `codex`). OpenAI
  publishes no credit allowance per plan, so a seat's week is measured once:
  run one dossier, read the weekly % on chatgpt.com/codex/settings/usage before
  and after, and set `week_credits` = the dossier's credits / (% used / 100).
  From then on every openai run gets a share like the other hosts.

Result, 2026-10-14 snapshot (`metrics/2026-10-14_budget-per-dossier.json`):

| | Runs | Per dossier (median) | Share of a week (median) | Dossiers / week | Tokens in / dossier | Avg request |
|---|---|---|---|---|---|---|
| opencode, GLM-5.3, all full runs Sept 17 → Oct 14 | 15 | 12.7k credits | Lite 127%, Pro 21.2%, **Max 9.1%** | Pro 4.7, **Max 11.0** | 140M | 139k |
| opencode with context-guard (W-092, W-104, unfinished) | 2 | 10.5k–12.7k | Pro 17.5–21.2%, **Max 7.5–9.1%** | Max 11–13 | 95–123M | **75–80k** |
| Claude Code, Opus 5.5 orchestrator + Sonnet 5.5 subagents | 15 | $89 API-equivalent | **Team Premium (Max 5x) ≤ 8.8%** | **≥ 11.4** | 281M | **328k** |

Reading: the GLM Max plan ($168/month) and the Claude seat ($125/month, Max 5x
tier) hold about the same number of dossiers a week, ~11. They get there
differently: the seat's week is worth ~2–3x more tokens (≥ ~3.5B against
~1.3–1.7B), but a Claude Code dossier carries ~4x the context per request
(328k against 75–80k with context-guard, since Opus 5.5 with the 1M window
only auto-compacts at ~967k) and so uses ~2–3x the tokens. The orchestrator is
89–98% of a Claude dossier's cost. Codex Pro ($200) publishes no token
allowance; measure it with `/status` before and after one dossier.

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
  never masked. Config: `shared/context-guard.yaml`.
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
- **Result** (interim, from `ledger/metrics/2026-10-14_after-pr105-plugins-interim.json` (W-092 through Phase 7, W-104 still in Phase 5/6; window from the plugin link 2026-10-12T07:53:23Z), compared with `2026-10-12_after-pr105-no-plugins.json`): orchestrator average context 231.5k → 86k (−63%),
  max 362k → 138k, credits per step 45.8 → 18.5 (−60%) — beyond the hypothesis.
  Steps per run 777 (Sept 28: 904). But the guard moved the watermark 154 and
  89 times: once the unmaskable part passed the trigger, on every step, and ~35
  cache misses per run followed a move; the cache hit fell 97.6% → 95.1%. See
  C-108. Logged after-sizes of `~0k` were a counting bug (all masked chars, not
  the new ones), fixed with C-108.

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
- **Result** (interim, from `ledger/metrics/2026-10-14_after-pr105-plugins-interim.json` (W-092 through Phase 7, W-104 still in Phase 5/6; window from the plugin link 2026-10-12T07:53:23Z), compared with `2026-10-12_after-pr105-no-plugins.json`): 19 refusals in the two runs; 8 refused whole reads of
  `phase-4.md` (32.7 KB), the file the orchestrator is told to read whole, and
  4 refused commands that only named `limactl … cargo test` in an argument
  (`compose_payloads.py` with a TEST_COMMAND). Each cost a retry step. Fixed by
  C-110.

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
- **Result** (interim, from `ledger/metrics/2026-10-14_after-pr105-plugins-interim.json` (W-092 through Phase 7, W-104 still in Phase 5/6; window from the plugin link 2026-10-12T07:53:23Z), compared with `2026-10-12_after-pr105-no-plugins.json`): low adoption — 36 calls in two runs (`code_item` 20,
  `doc_section` 11, `code_find` 3, `code_outline` 2) against ~180 shell file
  reads (`sed -n`/`cat`/`head` on a file) and ~540 `grep`/`rg` calls. Not
  changed yet; the step count, not the read size, is now the cost driver.

### C-104 — Checkpoints inside long phases (this PR)

- **Change**: `references/work-on/phase-4.md` and `phase-6.md` — a checkpoint
  after each wave and each arbitration round, when the build log holds the
  round's outcome.
- **Why**: Phases 4 and 6 grew back to 300–450k tokens over 170–340 steps.
- **Hypothesis**: more than 10 checkpoints per run; peak context −30%.
- **Verify**: `runs.*.checkpoints`, `runs.*.max_ctx_k`.
- **Result** (interim, from `ledger/metrics/2026-10-14_after-pr105-plugins-interim.json` (W-092 through Phase 7, W-104 still in Phase 5/6; window from the plugin link 2026-10-12T07:53:23Z), compared with `2026-10-12_after-pr105-no-plugins.json`): 8 and 7 checkpoints (hypothesis: more than 10); peak
  context −62%. Phases without a named round grew long: W-092's Phase 6 ran 224
  steps, W-104 is 281 steps past its last checkpoint. See C-109.

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

### C-108 — Batched mask moves, old inputs masked, a lower trigger

- **Change**: `context-guard.ts` — a move must free `min_gain_tokens` (4k) and
  the next move waits until the request has regrown `regrow_tokens` (10k) past
  the size the last move (or a check that found too little) left; a check that
  finds nothing worth freeing logs `floor`. Past the watermark the long inputs
  of `bash`, `task`, `write` and `edit` keep their first 300 chars. The trigger
  drops from 100k to 70k. The mask log line now counts only what a move adds.
- **Why**: C-101's trigger rule moved the watermark on every step once the part
  it cannot mask (old call inputs, subagent returns, phase files, the newest
  steps, the system prompt) passed 100k — 154 moves in W-104. In W-104's open
  segment the masked region still held ~34k tokens of bash inputs, 14k of task
  prompts and 7.5k of write content.
- **How it was tuned**: `opencode/plugins/test/replay-guard.ts` replays a recorded
  session through context-guard with its checkpoint cuts, feeding back the
  simulated request size. The old plugin replayed to 160 moves on W-104 (154
  recorded), so the replay is faithful on cadence. Cost model: request size ×
  cache-read rate + re-billed tokens at the fresh-input premium (glm-5.3).

  | W-104 (838 steps) | moves | breaks >20k | re-billed | avg request | ~credits |
  |---|---|---|---|---|---|
  | old (C-101) | 130 | 17 | 546k | 83k | 12.1k |
  | new, inputs not masked | 47 | 26 | 744k | 80k | 11.8k |
  | **new (70k / 10k / 4k)** | 48 | 23 | 646k | 73k | **10.7k** |

  W-092: 10.3k → 9.3k. A 100k trigger with a 30k regrow made fewer moves but
  carried more (14.2k on W-104): batching alone does not pay; masking inputs
  and an earlier trigger do.
- **Hypothesis**: orchestrator credits per run −8 to −12% at equal steps;
  average context 86k → 70–75k; mask moves per run under 60; cache hit back
  above 96%.
- **Verify**: `runs.*.avg_ctx_k`, `runs.*.mask_moves`, `runs.*.cache_hit_pct`,
  `runs.*.uncached_credits`, `plugin_events.context-guard:floor`.
- **Result**: —

### C-109 — Stage checkpoints and a reminder after 80 calls

- **Change**: `phase_checkpoint` closes a phase or a stage inside one (a wave,
  a test or review round, an investigation) once its outcome is in the build
  log, named `Phase <N> — <stage>`; the tool text no longer forbids it "to save
  space". After 80 tool calls without a checkpoint (and every 80 after) a
  `work-on` tool result carries a reminder (`OPENCODE_PHASE_CHECKPOINT_NUDGE_CALLS`).
  `phase-7.md` gains a checkpoint per review round; the work-on core states the
  stage rule. After a stage cut the summary says to re-read only the section of
  the phase file it needs.
- **Why**: masking cannot remove what the orchestrator must keep; only a cut
  does. The runs went 224 and 281 steps without one, and the tool's own text
  ("never to save space") kept the orchestrator from cutting.
- **Hypothesis**: 12 or more checkpoints per run; no stretch over 120 steps
  without one; max context under 110k.
- **Verify**: `runs.*.checkpoints`, `runs.*.max_ctx_k`, `plugin_events.phase-checkpoint:nudge`.
- **Result**: —

### C-110 — Read guard: phase files allowed, `limactl` only as the command run

- **Change**: the read guard never refuses a file in `protect_paths` (the phase
  files and the work-on core). The VM-test rule matches `limactl shell` only at
  the start of a command or after a separator, `then`, `do`, `exec` or `time`.
- **Why**: 12 of the 19 recorded refusals were wrong (C-102 result), each a
  wasted step and a chunked re-read.
- **Hypothesis**: no refusal of a phase file or of a command that only names
  `limactl`; fewer reads of `phase-4.md` per run (16 in W-104).
- **Verify**: `plugin_events.context-guard:refused`, the refused lines in
  `context-guard.log`.
- **Result**: —

### C-111 — The report separates file reads from output trims, and prices cache misses

- **Change**: `token_report.py` counts `bypass:sed/cat/head/tail file reads` (the
  command reads a file) apart from `output trims (| head/tail)`, and adds
  `uncached_credits` and `cache_hit_pct` per run (and to `--compare`; older
  snapshots show `n/a`).
- **Why**: the old count (914) mixed 742 output trims with 173 real file reads,
  and cache breaks had no number of their own.
- **Result**: n/a (measurement).

### C-112 — Claude Code: the read guard, as a PreToolUse hook

- **Change**: `shared/read-guard.ts` is the one read guard (C-102, C-110 rules and
  messages unchanged), and `shared/claude/pre-tool-use.ts` runs it for `Read` and
  `Bash` in the main session of a `/work-on` run (no `agent_id`; detected once per
  session from the transcript head, cached under
  `~/.local/share/agentic-software-development/claude/<session>.json`). A refusal is
  the documented PreToolUse deny with the reason. Config: `shared/context-guard.yaml`,
  the same file opencode reads. Each refusal is a `refused` line in `claude-hooks.log`.
- **Why**: the Claude orchestrator averages 328k tokens per request against 75-80k on
  opencode, and whole reads of dossiers and large files stay in its context until
  compaction. Only opencode refused them.
- **Hypothesis**: no whole read of a dossier or of a file over 20 KB in a Claude run;
  with C-113 to C-115, the Claude median `avg_request_k` goes 328k → ≤150k and the
  median share of the seat's week per dossier goes 8.8% → ≤5%.
- **Verify**: `python3 scripts/budget_report.py` (`avg_request_k`, share of the seat's
  week per dossier, Claude runs); `grep -c ' refused ' claude-hooks.log`.
- **Result**: —

### C-113 — Claude Code: checkpoints are recorded, and restored after compaction

- **Change**: `phase_checkpoint` is an MCP tool of the plugin
  (`mcp__plugin_agentic-software-development_nav__phase_checkpoint`, served by
  `shared/claude/mcp.ts`). Claude Code cannot be cut, so the call records: it is the
  tool call in the transcript. `shared/claude/session-start.ts` (SessionStart,
  matcher `compact`) reads every call back from the transcript, streaming, and injects
  the original task and the checkpoint summaries (`shared/checkpoint.ts`, the text
  opencode injects after a cut). `shared/claude/post-tool-use.ts` counts the
  orchestrator's tool calls since its last checkpoint and adds the reminder every 80
  (`PHASE_CHECKPOINT_NUDGE_CALLS`; 0 = off). `work-on.md` no longer tells Claude Code to
  skip the call.
- **Why**: compaction was the only thing that shrank the Claude orchestrator's context,
  it fires mid-stage, and its summary kept what the model chose to keep. A checkpoint
  made at a stage boundary is the hand-off that compaction then preserves verbatim.
- **Hypothesis**: 12 or more `phase_checkpoint` calls per Claude run; every compaction
  after the first checkpoint is followed by a `restored` line; Claude median
  `avg_request_k` ≤150k (with C-112, C-114, C-115).
- **Verify**: `claude-hooks.log` counts of `recorded`, `nudge` and `restored`;
  `python3 scripts/budget_report.py`.
- **Result**: —

### C-114 — Claude Code: the code tools

- **Change**: `code_outline`, `code_item`, `code_find`, `doc_section` and
  `code_replace` are served to Claude Code by the same MCP server, from the same
  definitions as opencode's (`shared/tools.ts`). `code-scout` is granted the four
  read-only ones.
- **Why**: the Claude orchestrator had to page large files with `sed -n`, whole reads
  and grep chains, which the work-on core already told it not to; the tools did not
  exist for it.
- **Hypothesis**: fewer whole-file reads and `sed`/`cat` file reads per Claude run;
  Claude median `avg_request_k` ≤150k (with C-112, C-113, C-115).
- **Verify**: `python3 scripts/token_report.py` tool counts for `code_*` in Claude
  sessions (names carry the `mcp__plugin_agentic-software-development_nav__` prefix);
  `python3 scripts/budget_report.py`.
- **Result**: —

### C-115 — Claude Code: `autoCompactWindow` 200000 in the dossier repo

- **Change**: the repo that runs dossiers sets `autoCompactWindow: 200000` in its
  project settings (added by hand, not by this plugin). Claude Code has no hook that
  rewrites history, so masking (C-101) is not mirrored; this caps what the orchestrator
  carries instead, and C-113 makes each compaction lossless for the checkpoints.
- **Why**: the Claude orchestrator's context grew to 328k on average per request before
  the host compacted.
- **Hypothesis**: the Claude median `avg_request_k` goes 328k → ≤150k, and the median
  share of the seat's week per dossier goes 8.8% → ≤5%, together with C-112 to C-114.
- **Verify**: `python3 scripts/budget_report.py` (Claude median `avg_request_k`, median
  share of the seat's week per dossier); the number of `restored` lines in
  `claude-hooks.log` against compactions.
- **Result**: —

## Next measurement

After the next runs, from the time the C-108–C-111 merge is pulled into the
checkout opencode loads (restart opencode first):

```bash
python3 scripts/token_report.py --since <pull time> --json ledger/metrics/<date>_after-c108.json \
  --compare ledger/metrics/2026-10-14_after-pr105-plugins-interim.json
```

Fill the **Result** of C-108–C-110 from that output, and the final results of
C-101–C-107 once W-092 and W-104 have finished (re-run the interim window with
`--until <pull time>`). Overall so far: credits per orchestrator run 26.6k
(Sept 28) → 14.4k (interim, runs unfinished).
