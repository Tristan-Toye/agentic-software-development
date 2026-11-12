# Mechanical phases: workflow, script, or Opus?

Status: research and plan only. No product code changed. Written 2026-11-12.

## 1. Problem

The /work-on orchestrator runs on Opus (`DEFAULT_MODEL = claude-opus-5-5`, `scripts/run_dossier.py:619`) for
every phase. Most turns in phases 3, 5, 6, 8, 9 are tool calls to plugin scripts. Each of those turns re-reads the
whole session context from the cache (about 100k tokens), so a one-line `git` or `run_tests.py` call costs as much
as a long thought.

### Numbers (cache-read tokens, Opus orchestrator, 2026-11-09 .. 2026-11-12T13:30, 3.56 days, 188 stage sessions)

Measured with `ledger/optimisations/scripts/measure_orchestrator_phases.py`
(raw output saved beside it: `measure_orchestrator_phases.2026-11-12.txt`).

| Phase | Requests | cache-read (M) | avg context | scripted-only | other mechanical | spawn | judgement text | bookkeeping-first (M, %) |
|---|---|---|---|---|---|---|---|---|
| P0 | 103 | 9 | 83k | 42 | 52 | 1 | 6 | 1 (16%) |
| P1 | 39 | 2 | 49k | 20 | 18 | 0 | 1 | 1 (36%) |
| P2 | 630 | 65 | 103k | 152 | 454 | 0 | 20 | 10 (15%) |
| **P3** | 627 | 63 | 101k | 209 | 357 | 8 | 48 | 15 (24%) |
| P4 | 1186 | 123 | 104k | 455 | 628 | 29 | 65 | 19 (16%) |
| **P5** | 665 | 55 | 82k | 221 | 424 | 1 | 19 | 10 (19%) |
| P6 (tests + arbitration) | 2031 | 209 | 103k | 739 | 1136 | 31 | 68 | 34 (16%) |
| P7 | 1342 | 133 | 99k | 580 | 586 | 51 | 70 | 21 (16%) |
| **P8** | 729 | 65 | 89k | 273 | 401 | 16 | 37 | 9 (14%) |
| **P9** | 580 | 45 | 77k | 276 | 242 | 13 | 38 | 11 (24%) |
| Total | 7933 | 768 | | | | | | |

Read this table with care:

- **Phase numbers differ from the brief** (P3 93M, P5 98M). Phase is not logged per request, so the script gives each
  request the phase of the next `phase_checkpoint` call, and falls back to the last phase file read. The brief's
  attribution is another approximation. Both agree on the shape: P3, P5(+P6), P8 are big. P6 (run tests, arbitrate)
  is the biggest single phase (209M) and the brief did not list it; it was probably counted inside P5. P5+P6 = 264M.
- **"Mechanical" describes what the turn writes, not what it needs.** A turn that only calls `run_tests.py` is
  mechanical. The next turn reads the result and may need Opus to judge it. Only 7% of requests carry model-authored
  text of 200+ characters (judgement), but many "mechanical" turns are the model reading a result and deciding.
  So the share that can really leave Opus is below the 91% "mechanical" share.
- **Bookkeeping turns are 14-36% of requests**: `dossier_edit.py log` (build-log lines), `ask_owner.py note`
  (owner updates), `phase_checkpoint`. Each is its own Opus turn at full context. Sampled `dossier_edit log` calls
  carry facts such as `GREEN IT (VM, ...` and `RETURN IT-fix...`: short, derived from the last script result.
- Cost scale (prices in `ledger/plans.json`, USD per Mtok cache-read): Opus 0.20, Sonnet 0.10, Haiku 0.10.
  P3+P5+P6+P8+P9 = 437M cache-read = about $87 per 3.56 days = about $175 per week, cache-read only (output and
  cache-write are not in this number). **The model price gap on cache-read is only 2x. The big lever is the
  number of turns and the size of the context each turn re-reads, not the model.**

Known weakness of the measure: dedupe is by message id; the "phase" attribution can be off by a few turns at each
phase boundary; "judgement" uses a text-length proxy (200 chars), not a quality measure.

## 2. Per-phase step table

M = mechanical/scriptable, J = needs judgement. Executor column is the proposal. "Script exists" names what is in
`scripts/` today. Phase docs: `references/work-on/phase-N.md`.

### P3 (63M) - weakest target: about a third is scriptable

| Step | M/J | Script exists | Proposed executor | Quality risk |
|---|---|---|---|---|
| Write the `CONTRACT-REVIEW:` line, run `validate_pipeline.py --dossier --pre-fanout` | M | validate_pipeline.py | script (driver) | none |
| Owned paths disjoint, no bodies in contract | M | validate_pipeline.py | script | none |
| Match checklist ids to the repo's coverage-script grammar | M | repo script | script reads regex, Opus applies | low |
| Grep for census pins the contract shifts | M | grep | script, prints hits; Opus reads | low |
| Frozen count carries its derivation command; run it | M | none | script (`check_frozen_counts`) | low |
| Run repo text gates over materialised contract | M | run_gates.py | script | none |
| Slice checklist per surface; slices add up to the whole | M (arithmetic) | none | script (set check) | none, strictly safer |
| Criterion -> test-file map: completeness check | M | validate_pipeline (part) | script checks every criterion has an owner | none |
| Criterion -> test-file map: choosing the owner | J | none | **Opus** | high if wrong: spawn defect |
| Derive `PROMISE_CHECKLIST` from docstrings | J (semi-M) | none | **Opus** (a script can list documented members as a skeleton) | high: this is the author-checks-author step |
| Seven diffs over the checklist (seams, derive-sets, "whole result" tags) | J | none | **Opus** | high |
| Independent contract review | J | sub-agent contract-reviewer (already Sonnet) | stays | already off Opus |
| Prose promise -> criterion mapping | J | check_docs.py (prose-promise warning) | script warns, Opus rules | medium |
| Decide test files and sizes | J | none | **Opus** | high |
| Settle `AMBIGUITY:` / fix `DEFECT:` lines | J | none | **Opus** | high |

### P5 (55M) and P6 (209M) - the largest pool; P6 added because the brief's P5 number most likely includes it

| Step | M/J | Script exists | Proposed executor | Quality risk |
|---|---|---|---|---|
| Promise coverage: diff unit-author report ids vs checklist ids | M | check coverage in verify_return.py (partly) | script returns uncovered / extra ids | none |
| Stub red-run: commit tests, run on stubs | M | run_tests.py | script | none |
| Read red-run result: "fails for the right reason?" | J | none | **Opus** (script lists failing test names and the assertion line; Opus rules) | high: vacuous tests pass the gate |
| Weak-pattern grep (`is_some(`, `len() > 0`, bare assert) | M (flags) | none | script (`weak_patterns.py`) | none; flags are a guide |
| Route vacuous test (named tests vs whole file) | J | phase4-wave workflow for 2+ authors | **Opus** | high |
| Audit the audit: one index line per checklist line with quoted assertion | J | none | **Opus** at first; later Sonnet draft + Opus spot-check of the `WEAK?` / `no-test-found` lines | high if moved: this is the test-strength gate |
| Merge branches in `Depends on` order, scope check vs `OWNED_PATHS` | M | verify_return.py, git_state.py | script (`merge_packages.py`): loops, stops on first conflict or out-of-scope path | none if it stops and escalates |
| Rule on `TOUCHED_BEYOND`; resolve merge conflict | J | none | **Opus** | high |
| Remove implementer worktrees | M | remove_worktree.py | script (inside merge driver) | none |
| P6: run suite and gates, parse result | M | run_tests.py, run_gates.py | script, prints compact failure table | none |
| P6: arbitrate each failure with the contract as referee | J | none | **Opus** (never moved: this is the pipeline's quality core) | very high |
| P6: build correction payloads, lint, admit, spawn, verify | M + workflow | compose_payloads.py, prepare_wave.py, spawn_admission.py, verify_return.py, phase4-wave.js | already scripted/workflow | none |

### P8 (65M)

| Step | M/J | Script exists | Proposed executor | Quality risk |
|---|---|---|---|---|
| Pick ADR candidates from build log, rulings, CRs | J | none | **Opus** (zero ADRs is valid; this is selection) | high |
| Draft ADR files from selected decisions | J-light | document-drafter (Sonnet), phase4-wave | already off Opus | low |
| `validate_pipeline.py`, index regen, commit | M | validate_pipeline.py, gate_commit.py | script (driver) | none |
| 8b: decide if a lesson is a pattern, draft rule text | J | none | **Opus** | high |
| Graduation question + `gh issue create` + write `GRADUATION:` outcome line | J then M | dossier_edit.py | Opus decides; script files issue and writes line from one command | low |

### P9 (45M)

| Step | M/J | Script exists | Proposed executor | Quality risk |
|---|---|---|---|---|
| Fetch + merge target into X | M | git, git_state.py | script (driver) | none; non-zero exit on conflict |
| `--finalize-ids`, validate, restore index, commit | M | validate_pipeline.py | script | none |
| Review each `updated dossier` line the finalizer prints | J-light | git_state.py | script prints the diff stat per line; Opus reads | medium |
| Conflict on code | J | implementer | **Opus** (driver stops, escalates) | high |
| Compile merged tree once per gated configuration | M | run_gates.py | script | none |
| Re-derive repo-derived ids and counts the sync could move | J-light | none | Opus reads script's list of candidates | medium |
| Open PR (gh), Jira comment | M | gh, MCP | script for gh; Jira stays a tool call | none |
| PR description from dossier excerpts | J-light | document-drafter / scribe | Sonnet/haiku agent with facts; Opus reads once | low-medium |
| `wait_ci`, `generate_open_work.py`, `remove_worktree.py` | M | all exist | script (driver) | none |
| `DEFERRED:` list | J | none | **Opus** (needs working memory) | high |

### P0-P2, P7 (quick)

- P0 (9M, 16% bookkeeping): `validate_pipeline.py --mode`, claim, route by status. Almost all M. Small pool.
- P1 (2M): one Jira lookup. Leave.
- P2 (65M): 454 "mechanical" requests, mostly `Read`, `grep`, `git`, `run_gates`. Authoring the contract is J and
  stays on Opus. Stub placement is already delegated (stub-materialiser, Haiku); `materialise_stubs.py` exists.
  Same driver idea fits the base-worktree, hook-policy and gate-list reads.
- P7 (133M): already `phase7-review` workflow; the remaining cost is applying CRs (J) and bookkeeping.
- P4 (123M): already `phase4-wave` workflow.

## 3. Options compared

First fact about the Claude Code Workflow tool: a workflow script is plain JavaScript with **no filesystem and no
shell** (workflow-authoring reference). It only has `agent()`, `parallel()`, `pipeline()`, `phase()`, `log()`.
To run a command it must spawn an agent that runs the command. The existing `phase4-wave.js` does exactly that:
it spawns a Haiku agent with a schema to run `verify_return.py` (lines 225-226). Subagents inherit the session
model unless `model` is set; the plugin pins it from a table (lines 7-23) and the fallback is Sonnet (line 23),
which matches the owner policy.

| | A. Workflow tool (JS, agents run the commands) | B. Driver scripts (Python, called by Opus in one Bash turn) | C. Runner-side script between stages (+ `claude -p --model sonnet` for light J-steps) | D. Per-stage model switch (Sonnet orchestrates mechanical phases) |
|---|---|---|---|---|
| What changes | New `workflows/*.js`; `phase-*.md` tell Opus to call it | New `scripts/*.py` that chain existing scripts and print one compact JSON result | `run_dossier.py` runs a script after a stage ends, starts the next stage with its result in the hand-off | `run_dossier.py` starts the stage with `--model sonnet` |
| Turns removed from Opus | The call itself is 1 Opus turn; inside, each command is a Haiku agent turn (small context) | Many turns collapse to 1 | All of them for the covered steps | None; the same turns run cheaper |
| Saving per replaced turn | Opus 100k ctx (about $0.02) vs Haiku about 20k ctx (about $0.002 plus cache write) | A whole chain: N turns -> 1 | 100% of those turns | 50% of cache-read price (0.20 -> 0.10), same turns |
| Quality risk | A Haiku agent can mis-report a command result. Needs `schema` and a script that prints exit code; the agent only relays | None for pure chains: same scripts, same order. Risk only in what the driver decides to skip, so it must stop on any non-zero | Needs a judgement hand-back path; the runner stage boundary is model-chosen today | Highest: P3, P5, P6 contain the arbitration and test-strength decisions; a smaller model referees the contract |
| opencode parity | **Lost.** opencode has no Workflow tool; `phase-4.md:49` already says the paragraph does not apply there, so every workflow doubles the doc paths | **Kept.** Scripts in `scripts/` are shared by both hosts and the agent just runs a command | Kept for the runner (it is host-independent); opencode runs via its own path | Needs the opencode profile mapped too (`opencode/model-profiles.json`) |
| Build cost | High: script, tests (`shared/test/workflows.test.ts`, `workflow-models.test.ts`), doc rules (`phase-4.md:34-44` allows the Workflow tool only for the two named workflows) | Low: one script and one doc line each; fits existing `check_*` tests | Medium-high: runner change, stage-boundary logic (`MARKER`, `run_dossier.py:349`; stage ends when the model prints `RUNNER: STAGE-DONE`, line 107) | Medium: runner needs a stage-to-model map; `model_ok` (`run_dossier.py:741`) and `DEFAULT_MODEL` pin must allow it |
| Savings estimate (P3,5,6,8,9 = 437M per 3.56 days) | Low-medium: wins only for fan-out + verify, which is already built | **Medium: 20-30% of those turns, about 90-130M, about $18-26 per window, about $45-65 per week (cache-read only)** | Adds another 5-10% for P9 and P5 merge loops | 10-15% overall if used on the "safe" phases only; unsafe for the rest |
| Verdict | Keep for fan-out only (already done). Do not extend. | **Do first.** | Do second, only for P9 finalisation | Reject for P3, P5, P6. Maybe P9 later |

The owner's wording was "make it a workflow". The finding is that the useful kind of workflow is a **deterministic
script, not an agent graph**: an agent graph still spends one model turn per command, only a cheaper one. A script
spends none. The Workflow tool is best at what is already built (spawn-and-verify). The savings come from
removing turns; `claude -p --model sonnet` is for the few light judgement steps (PR text), not for the chain.

Estimate method, so it can be challenged: scripted-only + other-mechanical requests in the five phases are 399M
of 437M. A driver can remove only the turns whose output the model does not need to read before the next step.
I assume 30% of that pool (120M) and 18-25% in the cheaper case; the A/B in section 5 replaces the assumption
with a measured value.

## 4. Recommended plan (phased, smallest safe first)

Principle: **no judgement moves off Opus.** Every J-step in the table stays Opus. A script never decides; it
runs a fixed chain, prints a compact result, and exits non-zero on anything unexpected. Opus then reads one
result instead of ten.

### Step 0 - decide the target with data (1 day, no risk)
Sample 30 `dossier_edit log` + `ask_owner note` turns from `ledger` transcripts and tag: derivable from the last
script result (fold in) or model-authored (leave). Without this tag, step 1 may be sized wrong.

### Step 1 - smallest safe step: scripts write their own bookkeeping (about 80M, 18% of the pool)
- `run_tests.py`, `run_gates.py`, `verify_return.py`, `gate_commit.py`, `validate_pipeline.py` get an optional
  `--log-dossier <path> --log-prefix <TAG>` that appends the build-log line they already print (result line,
  counts, exit code) through `dossier_edit.py log`. Opus no longer spends a turn to copy it.
- `ask_owner.py note` calls move into the same scripts for the fixed events (suite green/red, wave returned).
  Judgement notes (an arbitration ruling) stay manual.
- Doc change: `phase-N.md` lines that say "log X in `## Build log`" gain "(the script logs it with `--log-dossier`)".
- Quality guard: the line text is the script's own output, so nothing is paraphrased; a unit test per script
  compares the line to the existing format that `validate_pipeline.py` already parses.
- Works on opencode and Claude Code: both just run the script.

### Step 2 - phase drivers for the pure chains (about 40-50M more)
In this order (each is shipped alone, in shadow mode first):
1. `finalize_pr.py` (P9): fetch + merge target, `--finalize-ids`, validate, compile per gated configuration, print
   the `updated dossier` diff stats, stop on conflict (exit 2 = code conflict, Opus takes over).
2. `merge_packages.py` (P5): merge in `Depends on` order with the `OWNED_PATHS` check; stop at the first conflict or
   `TOUCHED_BEYOND` path; remove worktrees that merged.
3. `pre_fanout_check.py` (P3): validate, disjointness, census grep, frozen-count commands, repo text gates, slice
   arithmetic, criterion-owner completeness. Prints findings; Opus fixes and decides.
4. `strength_gate_prep.py` (P5): promise coverage diff, stub red-run, weak-pattern flags; prints failing tests with
   the assertion line. Opus rules on vacuous tests.
5. `test_round.py` (P6): suite + gates + failure table. Arbitration stays Opus.
Rules for every driver: it calls the existing scripts (no copy of their logic), prints JSON of at most 40 lines,
exit 0 clean / 1 findings / 2 needs Opus, and refuses to continue past a failed step.

### Step 3 - runner-side hook for P9 only (optional, after 1-2 prove out)
After `STAGE-DONE Phase 8`, `run_dossier.py` may run `finalize_pr.py` itself and put its result in the hand-off
message for the next stage. Exit 2 starts the stage as today. This saves the stage's context re-read entirely
for those steps. The light J step (PR description) can use `claude -p --model sonnet --json-schema ...`
(`--model` and `--json-schema` are documented for print mode; see Sources) with facts only, and Opus checks it once.

### What not to do
- No new Workflow-tool scripts for mechanical steps (no shell, parity loss, doc rules forbid extra workflows).
- No Sonnet orchestrator for P3, P5, P6, P8 judgement (contract arbitration, test-strength, ADR and rule selection).
- No agent summarising a test result for Opus: an agent that summarises can drop the one line that matters.

### Quality guardrails (all steps)
1. **Escalate on anything.** Any non-zero exit, any unexpected output shape, any "unknown" field: the driver stops
   and Opus does that step by hand, as today.
2. **Existing verifiers stay.** `verify_return.py`, `validate_pipeline.py --pre-fanout` and the `PAYLOAD-LINT:`,
   `ADMISSION:`, `CONTRACT-REVIEW:` lines still refuse to pass when missing. Drivers write those lines through the
   same code path.
3. **Shadow first.** For the first 3 dossiers the driver runs and Opus still does the old steps; the driver's
   result is logged next to Opus's and any difference is read.
4. **Read-only on judgement.** Drivers print evidence; the ruling text is Opus's.
5. **Both hosts.** Each driver has a test in `scripts/` style and a line in the opencode phase docs.

## 5. Measurement plan

- Baseline: re-run `measure_orchestrator_phases.py --since <date>` on the current window (done above), and
  `scripts/token_report.py` per dossier (see `ledger/token-spend.md`). Save as
  `ledger/metrics/<date>_before-drivers.json`.
- A/B: 6 dossiers per arm (baseline vs drivers), matched by repo and package count; run the baseline arm from the
  existing history (dossiers built since 2026-11-09), the driver arm as new runs. Compare per dossier:
  Opus requests per phase, cache-read per phase, cost via `budget_report.py`, and the quality counters below.
- Quality counters (must not worsen): `validate_pipeline.py` DEFECT count at P3 and at P9; `GAP:` returns and
  `CONTRACT-CHANGE:` count; vacuous tests found at P5 vs later; P6 arbitration rows and re-spawns; review CRs
  per lens at P7; red CI on first push; owner questions (`ask_owner`); stage stalls and re-runs.
- Success: cache-read in P3/P5/P6/P8/P9 down at least 20%, every quality counter equal or better, zero escalations
  lost (a "needs Opus" case handled silently).
- Stop rule: one quality counter worse in 2 consecutive dossiers -> driver goes back to shadow mode.
- Note: the measuring script's "judgement" class is a text-length proxy. Add a second measure: share of Opus
  turns that are bookkeeping-first (current 14-36%); target under 8% after step 1.

## 6. Risks

- **Savings smaller than the 91% "mechanical" figure.** A turn that writes a script call still has to read the
  result next. Mitigation: size steps from the sampled data (step 0), and measure.
- **Silent skip.** A driver that continues after a failed sub-step corrupts the build. Mitigation: fail-fast, tests
  for each failure path.
- **Self-check lost.** P3 and P5 are the "author checks the author" phases. Moving the *grep* is safe; moving the
  *derivation or audit* removes the check. Mitigation: those stay Opus.
- **Doc drift between hosts.** Each new driver needs both phase-doc variants; `check_docs.py` and the opencode docs
  must be updated together.
- **Cache effects.** A stage is already a new process (`run_dossier.py:107`), so a runner-side hook does not lose a
  warm cache; an in-stage model switch would. Keepalive pings (`run_dossier.py:141` area) assume one model.
- **Measure error.** Phase attribution is approximate; a 10-20% error in per-phase numbers does not change the
  ranking, but changes the dollar estimate.
- Pricing used is in `ledger/plans.json` (checked 2026-10-14); recheck before quoting dollars.

## 7. Sources

Code and docs in this repo:
- `scripts/run_dossier.py:619` (DEFAULT_MODEL), `:731` (FORBIDDEN_MODEL), `:734-738` (runner_model), `:741` (model_ok),
  `:1282` (`--model` passed to `claude`), `:1303` (stage command `/<plugin>:work-on <ID>`), `:107` (STAGE-DONE rule),
  `:349` (MARKER), `:2112` (run_stage).
- `references/work-on/phase-3.md` (checklist derivation, seven diffs, contract review), `phase-5.md` (stub red-run,
  audit the audit, merge order), `phase-8.md` (ADRs, rules, graduation), `phase-9.md` (sync, finalize-ids, PR).
- `references/work-on/phase-4.md:24-50` (two workflows; opencode excluded; "call it for nothing else").
- `workflows/phase4-wave.js:7-23` (model table), `:215`, `:225-226` (Haiku verify agent with schema),
  `workflows/phase7-review.js`.
- `sub-agents/document-drafter.md:11,47`, `sub-agents/scribe.md:12,56` (models per host).
- `opencode/model-profiles.json`, `opencode/plugins/phase-checkpoint.ts`.
- `ledger/plans.json` (prices), `ledger/token-spend.md` (token_report, budget_report usage).
- `ledger/optimisations/scripts/measure_orchestrator_phases.py` and its saved output.

External (fetched 2026-11-12 with curl):
- https://code.claude.com/docs/en/cli-reference : `--model` (aliases `sonnet`, `opus`, `haiku`), `--json-schema`
  (print mode), `--max-turns`, `--max-budget-usd`, `--bare`.
- https://code.claude.com/docs/en/headless : `-p`, `--output-format json` with cost fields, `--json-schema`,
  `--bare` recommended for scripted calls.
- https://code.claude.com/docs/en/sub-agents : subagent model resolution (inherits the main model unless set;
  per-invocation `model` alias).
- Workflow tool: the `workflow-authoring` skill reference in Claude Code (script API: `agent()`, `parallel()`,
  `pipeline()`, `schema`, `model` per call; scripts have no filesystem or shell access). Not on a public docs page I
  could fetch; verify against the tool description before building anything.
