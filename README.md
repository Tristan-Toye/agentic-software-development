# agentic-software-development — a contract-first build pipeline

Four commands, four agents, two file kinds.

```
/plan  <description>   →  a buildable dossier (a PR, when dossiers are committed)
/work-on <ID>          →  a PR, and the ADRs the build earned
/overview-dossiers     →  what is going on
/deferred              →  what the run noticed and did not tackle
```

## The idea

The orchestrator writes the **contract** first: the signatures and the
documentation comments, with no bodies. That one artifact is what lets three
kinds of agent work at the same time without seeing each other's output:

```
                 ┌─────────────────────────────────────────────┐
                 │  ORCHESTRATOR (the session, strongest model)│
                 │  contract · git · merges · test runs ·      │
                 │  arbitration · Jira · Tempo · ADRs · PR     │
                 └─────────────────────────────────────────────┘
                                     │
              ┌──────────────────────┼──────────────────────┐
              │                      │                      │
     unit-test-author      integration-test-author      implementer × N
     reads the staging     reads the dossier            own worktree
     area + tests only     sees stubs, never a body     never sees a test
              │                      │                      │
              └──────────── merge into the base ────────────┘
                                     │
              stub red-run · run the tests · arbitrate failures
                        (the contract is the referee)
                 scan flake risk; repeat affected tests when needed
                                     │
              ┌──────────────────────┼──────────────────────┐
        reviewer:style       reviewer:architecture   reviewer:performance
                  (+ reviewer:tests when the new tests carry flake risk)
              └──────────────── all concurrently ───────────┘
                                     │
                        extract ADRs · open the PR
```

**Blindness is structural, never a promise.** `unit-test-author` reads and
writes inside a permission map that denies everything outside the staging
area and the test families, so it cannot open an implementation even if it
wanted to — the tool call is refused. Its payload plus that map are its
entire world. `integration-test-author` finds only stubs on the
base branch. Each `implementer` works in a worktree forked before any test
exists. Nobody is asked to resist temptation.

**The contract is the referee.** When a test and an implementation disagree,
the documentation comment decides which one is wrong — and if it is ambiguous
enough to justify both readings, the *orchestrator* was wrong and fixes the
contract. The rule is mechanical, so a failing test cannot be resolved by
whichever side is easier to change.

## The commands

| Command | Does | Spawns |
|---|---|---|
| **`/plan <anything>`** | A Jira key, a stack trace, a paragraph of intent → an investigated dossier: the problem with anchored evidence, the approach, the contract, disjoint work packages, falsifiable criteria. Writes `status: ready`. In a repo that commits `.discovery/`, it works in a `plan/<KEY>` worktree and ends with a PR. | 1 (`reviewer`, `LENS: plan`) |
| **`/work-on <ID>`** | Materialises the contract as real code, fans out blind and concurrent, merges, arbitrates every test failure, investigates flake risk with focused repetitions when needed, runs three concurrent review lenses (four when the new tests carry flake risk), extracts the ADRs, opens the PR, removes the worktree. Resumable at every phase. | 3 + N, then 3 |
| **`/overview-dossiers`** | Status of every dossier from front matter alone, plus the pipeline health signals worth acting on. Read-only toward pipeline state; also renders `.discovery/analysis/open-work.html`, a self-contained animated dashboard; `--worktrees` overlays the live copies from sibling worktrees when `.discovery/` is committed. | 0 |
| **`/deferred`** | The deferred-issues report for a finished change: every product-code issue the session knew about and did not tackle, with an ID, evidence and a proposed fix. Recall, not audit; `/plan D-n` seeds a dossier from a line. | 0 |

## The agents

| Agent | Tools | Why it exists |
|---|---|---|
| `implementer` | Read, Grep, Glob, Bash, Edit, Write | Fills bodies against the contract. Never changes a signature — returns `CONTRACT-CHANGE:` and stops. |
| `unit-test-author` | Read, Write, Edit — permission-scoped to `.agent-staging/` and the test families | A test written by someone who has seen the implementation re-derives the expected value the way the code does, and then it can never disagree with the code. |
| `integration-test-author` | Read, Grep, Glob, Write | A flow test needs intent, so it reads the dossier. It still cannot read a body. |
| `reviewer` | Read, Grep, Glob | Five lenses: `plan`, `style`, `architecture`, `performance`, and `tests` (test stability under load, only when due). No write tools at all, so it cannot change what it reviews; every reply names the scope it did not reach. |

Three **support agents** (flash, size-gated) carry mechanical work off the
orchestrator's context. Each returns a guidance doc — pointers, verbatim
quotes, neutral flags — and never a verdict; the orchestrator investigates
every flag and keeps every judgement:

| Agent | Tools | Carries |
|---|---|---|
| `stub-materialiser` | Read, Grep, Glob, Bash, Write | The contract verbatim into compiling stubs, past four members. |
| `blast-radius-scout` | Read, Grep, Glob, Bash | The review scope as a location list, past five changed files. File selection comes from `ocr delegate preview`, so every dropped path arrives with the reason it was dropped. |
| `document-drafter` | Read, Grep, Glob, Write | ADR and PR drafts from decisions already made, self-scrubbed. |
| `scribe` | Read, Edit, Write — scoped to `.discovery/`, `docs/`, `.agent-staging/` | Prose edits to a dossier or a document from the facts and rulings it is handed. Never tests, docstrings or code. |
| `code-scout` | Read, Grep, Glob, the code-navigation tools | Lookup chains — where a symbol is, what calls it — answered as `path:line` rows, never file contents. |

One **independent checker** runs on every build, with no size gate:

| Agent | Tools | Carries |
|---|---|---|
| `contract-reviewer` | Read, Glob | Its **own** promise checklist, derived from the materialised stubs alone. Every disagreement with the orchestrator's is a contract defect caught before the fan-out. |

Two checks that were once agents are now the orchestrator's own passes: the
**coverage index** (one line per checklist line, assertions quoted) and the
**mutation check** (one mutant per checklist line on the primary surface).
Both were delegated in fewer than one build in five, and the orchestrator ran
them itself in every other — the spawn was the unused path, never the check.

### open-code-review, at the two places that are not blind

`ocr` ([open-code-review](https://github.com/alibaba/open-code-review)) is an
**optional** dependency, used in delegation mode only: it resolves files and
rules and calls no model, so no LLM endpoint is configured for it.

| Step | Command | Who runs it |
|---|---|---|
| Which files changed, and which were dropped and why | `ocr delegate preview --format json` | `blast-radius-scout`, or the orchestrator below the gate |
| The per-path review checklists | `ocr delegate rule --format json` | the orchestrator, at Phase 7 |

It deliberately stops there. **The `reviewer` never runs `ocr` and never sees
a diff** — its blindness protocol is the reason its verdict is worth anything,
and every OCR delegate step past rule resolution is diff-driven. The rules
reach a lens as the `RULES` payload field, already narrowed to that lens by
the orchestrator, because `ocr` resolves one combined checklist per path and
three lenses given the same checklist file the same CR three times.

Two things to know before trusting it:

- **`RULES` ranks last**, below `ARBITRATIONS`, `CONTEXT_DOCS` and
  `STANDARDS`. It knows the file's language and nothing about the repo.
- **`exclude_reason: unsupported_ext` is not a verdict.** `ocr` carries a
  fixed language list; a repository whose product is `.md`, `.sql`, `.tf` or
  `.proto` sees its whole change excluded. Phase 7 makes the orchestrator
  rule on every exclusion and log it, rather than inherit it.

Without `ocr` installed the pipeline runs unchanged: the scout falls back to
`git diff --name-only`, the orchestrator logs `EXCLUDED: unknown — ocr
unavailable`, and `RULES: none` goes to every lens. Nothing is filtered, which
is the safe direction.

```bash
npm install -g @alibaba-group/open-code-review   # needs v1.9.0+ for --format
```

## The files

```
.discovery/                      # local by default; a repo may commit it
└── dossiers/<ID>-<slug>.md      # front matter = machine state (/overview-dossiers
                                 #   reads only this). Body = problem,
                                 #   approach, contract, packages, criteria,
                                 #   build log. Kept after the build.
docs/adr/                        # committed — ships with the PR
├── index.md                     # ID | status | title — the only file a
│                                #   future agent scans. Derived; CI commits
│                                #   it on the target branch, branches never
└── NNNN-<slug>.md               # extracted by /work-on, selectively
```

**`.discovery/` has two modes**, decided by one check at the top of every
command: `scripts/validate_pipeline.py --mode`. Untracked (the default)
means local working state that never lands in a commit. Tracked means the
repository reviews its plans: `/plan` writes the dossier in its own
`plan/<KEY>` worktree and ends with a PR, `/work-on` keeps the live copy in
its base worktree and ships the build record inside the build PR, and the
main checkout's copy changes only when a PR merges. `references/formats/README.md`
§ "Two modes" holds the full table; the overview HTML, the PR draft and the
deferred ledger stay local in both.

ADRs are **extracted, not generated**. Zero is a correct outcome for a defect
fixed as specified. An ADR per dossier means a template got filled instead of a
decision getting recorded.

Rules that outgrow one repo **graduate**: `/work-on` Phase 8b asks whether a
ratified rule stops at this repo; a rule that does not becomes a plugin change
as its own work item, so the next repo starts with the lesson.

Both file kinds use **ASD-STE100** (Simplified Technical English) — one term per
concept, active voice, simple tenses, 25-word sentences. Prose an agent reads one
time and gets right.

## Two harnesses, one source

The pipeline runs under opencode and under Claude Code. Every file stays where
opencode expects it; Claude Code reaches the same files through the path fields
in `.claude-plugin/plugin.json`.

| Claude Code loads | From |
|---|---|
| commands | `commands/` and the generated `claude/commands/` |
| agents | the generated `claude/agents/` |
| skills | `skills/` |
| marketplace | `.claude-plugin/marketplace.json` — the repo hosts itself |

```
/plugin marketplace add <path or git URL of this repo>
/plugin install agentic-software-development@agentic-software-development
```

Two front matter keys cannot serve both harnesses. `model` wants
`provider/model` in opencode and a Claude model name in Claude Code. Tool
policy is a `permission:` map in opencode — per path, per command — and a
whole-tool `tools:` list in Claude Code, which ignores `permission:` entirely.
opencode also types `tools` as an object, so a Claude-style `tools: Read, Write`
string breaks its schema.

So there is **one source and one derived artifact, never two maintained
copies**. `sub-agents/*.md` and `primary-agents/*.md` keep their opencode front
matter and add one `claude:` block that states what Claude Code needs:

```yaml
claude:
  model: haiku
  effort: low
  tools: Write
```

`scripts/build_claude_plugin.py` writes `claude/agents/` and
`claude/commands/` from those sources, copies each body byte-identical, and
regenerates the manifest's `agents` list. **Never edit a file under `claude/`.**
The next build overwrites it.

```
python3 scripts/build_claude_plugin.py            # write claude/
python3 scripts/build_claude_plugin.py --check    # exit 1 when claude/ is stale
python3 scripts/build_claude_plugin.py --selftest
```

**Drift is checked, not documented.** `--check` regenerates into memory and
fails when a committed file does not match its source. Run it with the other
validators before any plugin change lands, and after Phase 8b graduates a rule.

### Model profiles under opencode

An agent's opencode `model:` names its tier: `zai-coding-plan/glm-5.3` is the
large tier, `glm-5.3-flash` the flash tier. `opencode/model-profiles.json`
maps each tier to a model per profile (`glm`, `openai`), with provider options
such as OpenAI's `reasoningEffort` beside it; the file records why each choice.
`scripts/opencode_profile.py <profile>` reads the agents opencode loads (the
links in `~/.config/opencode/agent/`) and prints the config that moves every
one onto its tier's model in that profile. The output goes in
`OPENCODE_CONFIG_CONTENT`, the one config layer that overrides an agent file's
`model:`; an `OPENCODE_CONFIG` file does not. Two shell functions wrap it:

```
opencode-glm       # every agent on GLM (glm-5.3 / glm-5.3-flash)
opencode-openai    # every agent on OpenAI gpt-6.1-sol at reasoningEffort high, after /connect → OpenAI
python3 scripts/opencode_profile.py --check   # every linked agent has a tier, no link dangles,
                                              # every repo agent is linked
```

A subagent without its own `model:` would inherit the orchestrator's model, but
then every subagent runs on the large tier; the profile keeps the flash tier on
each provider.

### What Claude Code cannot express

Claude Code grants or withholds a whole tool. Agent front matter has no
per-path and no per-command scope. The generator makes that gap loud instead of
silent:

- A permission entry that denies `*` is a blindness boundary. The generator
  **refuses** to grant that tool. `unit-test-author` gets `tools: Write` and
  nothing else — the same blindness, by a different mechanism.
- An agent that still needs the tool records the widening under
  `claude.widen.<key>`, with a reason. The generator copies the reason into the
  generated file and into the agent's own prompt, so nothing runs wider than
  its opencode twin without saying so.
- A narrowing inside an otherwise-allowed tool — `implementer` denying
  `git push` — cannot survive. The generated file carries a comment that names
  the loss. Put a `Bash(git push *)` deny rule in the project `settings.json`
  when the repo needs that guarantee back.

Every generated agent ends with a footer that names its real tool list, so body
prose about permission maps cannot mislead the agent that reads it.

### Runaway guards under Claude Code

opencode stops a runaway agent with `steps` and `doom_loop: deny`. Claude Code
gets the same two stops from this plugin:

- **A turn cap per agent.** The generator writes each agent's opencode `steps`
  as Claude Code `maxTurns`, so one number caps both hosts; `claude.maxTurns`
  overrides it. At the cap Claude Code stops the subagent and marks its
  output partial.
- **A repeat guard.** `hooks/hooks.json` runs `scripts/hooks/repeat_guard.py`
  before every tool call: the 4th identical call in a row in one session is
  denied with a reason the model reads, and any different call resets the
  count. `ASD_REPEAT_GUARD_LIMIT` sets the allowed run (default 3; 0 turns it
  off). The hook fails open — bad input or state allows the call.

### Spawns the auto mode classifier can judge

In auto mode Claude Code classifies the task a spawn delegates from the spawn
prompt alone; it never sees a file the prompt points at. The opencode spawn
form — `PAYLOAD_FILE: <path>`, "read it whole; every field binds you" — hands
authority to content the classifier cannot see, and in the recorded Claude
Code runs (2026-10-05..10) about one such spawn in five was denied as
`[Auto-Mode Bypass]` or `[Data Exfiltration]`, while the identical prompt
passed on another try. So under Claude Code the payload is pasted:

- **The rule.** The generator appends it to every command that spawns
  (`claude/commands/work-on.md`, `plan.md`): a one-line task statement, then
  the linted final's fields verbatim; files the payload names stay paths;
  staged files live under `<worktree>/.agent-staging/`, never a scratchpad;
  no secret in a prompt. Each generated agent says its payload arrives
  pasted. A spawn the classifier still denies goes to the user, never
  re-worded to get past it.
- **The check.** `hooks/hooks.json` runs `scripts/hooks/spawn_guard.py`
  before every `Agent` call to one of this plugin's agents and denies, with
  the fix as the reason, a pointer prompt (a payload file named and no
  `FIELD:` line of its own), a path in a Claude Code scratchpad, or a
  secret-shaped value. It never allows anything — what it lets through still
  goes to the classifier. Replayed over the 127 recorded spawns it refuses 20
  of the 22 the classifier denied; the other 2 were pasted payloads.
  `ASD_SPAWN_GUARD=0` turns it off; it fails open.
- **Your settings.** The classifier reads `autoMode.environment` from
  `~/.claude/settings.json` (never a plugin or project file). One line naming
  this plugin's spawns and the `.agent-staging/` payloads as the intended
  design gives it the context the prompt cannot.

opencode has no classifier and keeps spawning by the file.

## Token spend

A `/work-on` orchestrator re-sends its whole context on every step, so what it
carries is what it costs. Each mechanism has one source of truth, run by bun,
and a thin adapter per host:

| Mechanism | One source | opencode adapter | Claude Code adapter |
|---|---|---|---|
| Config | `shared/context-guard.yaml`, loaded by `shared/config.ts` | `opencode/plugins/context-guard.ts` | `shared/claude/*.ts` (same file) |
| Read guard — whole reads of a dossier or of a large file (never a phase file) and VM test runs that bypass `run_tests.py` are refused with the tool to use instead | `shared/read-guard.ts` | `tool.execute.before` throws | `shared/claude/pre-tool-use.ts`, PreToolUse `Read\|Bash`, a deny, for the `/work-on` main session only |
| Checkpoints — the orchestrator closes a phase or a stage (a wave, a test or review round, an investigation) with `phase_checkpoint`; after 80 tool calls without one a result reminds it | `shared/checkpoint.ts` | `opencode/plugins/phase-checkpoint.ts`: the tool cuts every earlier step from later requests and keeps the summaries | the MCP tool records (the call is the record); `post-tool-use.ts` counts and reminds; `session-start.ts` (matcher `compact`) restores the task and the summaries from the transcript after a compaction, plus the spawns and workflow runs in flight since the last checkpoint (with their agent and run IDs) and the last 40 lines of the dossier's `## Build log` |
| Code tools — `code_outline`, `code_item`, `code_find`, `doc_section`, `code_replace`, so a read returns the item, not the file | `shared/tools.ts`, wrapping `scripts/code_nav.py` | `opencode/plugins/code-nav.ts` | `shared/claude/mcp.ts`, a stdio MCP server registered by `.mcp.json`; tools are named `mcp__plugin_agentic-software-development_nav__<tool>` |

The `shared/` modules need bun and no npm packages; the Claude hooks and the MCP
server are guarded, so a machine without bun runs the plugin as before. Every
hook fails open (exit 0, no output). Hook events — `refused`, `nudge`,
`recorded`, `restored` — are one line each in
`~/.local/share/agentic-software-development/claude/claude-hooks.log`.
Tests: `bun --no-install shared/test/<name>.test.ts` (shared modules and the
symlink proof, the hooks, the MCP server, the workflows) and `opencode/plugins/test/`.

**Claude Code only: two dynamic workflows** (`workflows/`, run through the
`Workflow` tool as `agentic-software-development:phase4-wave` and
`agentic-software-development:phase7-review`). `/work-on` still composes, lints
and admits every payload; it then calls the workflow once per wave or review
round, which spawns the agents, verifies each return with `verify_return.py`
through a cheap agent, and returns one compact object per agent, so the
orchestrator's context holds a result line and an excerpt instead of every full
report. Corrections and fix rounds resume the original agent with `SendMessage`
to the agent ID the build log holds. opencode keeps its current flow.

What Claude Code cannot mirror:

- **Masking.** `opencode/plugins/context-guard.ts` also turns old tool output,
  old reasoning and long old inputs into one-line stubs. Claude Code has no
  hook that rewrites the conversation history. Set `autoCompactWindow: 200000`
  in the project settings of the repo that runs dossiers instead.
- **The cut.** The model cannot trigger compaction, so `phase_checkpoint`
  cannot drop earlier steps there. The checkpoint is recorded, and compaction
  keeps it.

Masking and the opencode read guard are tuned in `shared/context-guard.yaml`,
against recorded sessions with `opencode/plugins/test/replay-guard.ts`.

**The fixed prompt under opencode.** Before any work, a request carries the
agent's prompt, `AGENTS.md`, and the schema of every tool the agent may call; a
tool opencode lets through costs its schema on every request. opencode drops a
tool from the request when the agent's effective permission for it is a blanket
`deny` (`opencode debug agent <name>` lists it as disabled; the denied call is
not merely refused). So the tool set is a permission map: `opencode/opencode.jsonc`
denies, for every agent but the default, the plugin and MCP tools the pipeline
never calls; `work-on` and `dossier` scope their skill catalogue in its `agent`
section; each sub-agent's frontmatter denies the rest.
`scripts/check_permission_maps.py` fails when a pipeline agent regains one
(item 7 of its header), and `opencode` has no per-agent switch for `AGENTS.md`,
so its Context7 and context-mode text stays. Numbers: C-130 to C-133 in the
ledger.

**Plugin evals.** `evals/` holds three starter cases for `claude plugin eval`
(`/work-on` refuses an unready dossier, the spawn hook refuses a payload-file
pointer, the orchestrator calls `phase_checkpoint` at a phase boundary). They
call the model, so nothing runs them by default: `claude plugin eval . --scaffold
--trust-plugin --max-cost-usd 1`. `--scaffold` runs each case's `fixture.sh`,
which only writes a dossier into the empty workspace.

Every change, the evidence behind it, its hypothesis and its measured result
are in `ledger/token-spend.md`; `scripts/token_report.py` produces the numbers.

## Reference

| File | Holds |
|---|---|
| `references/formats/` | Both file formats, the evidence labels, the ASD-STE100 subset. Read `README.md` first. |
| `references/payloads/` | One spawn skeleton per agent, and the field rules that matter. Read `README.md` first. |
| `references/work-on/` | `/work-on`'s instructions, one file per phase. `primary-agents/work-on.md` holds what is true in every phase and points at each file; the orchestrator reads a phase file when the phase starts. |
| `references/time-logging.md` | The Tempo contract: one session per run, orchestrator only. |
| `skills/standards/` | The engineering standards. They bind generation and review symmetrically. |
| `scripts/validate_pipeline.py` | Front matter, section set, **path disjointness**, contract shape, criterion falsifiability, anchors, ASD-STE100, and two dossiers sharing one ID. `--mode` settles which of the two `.discovery/` modes a checkout is in; `--finalize-ids` renumbers the ADRs, LRNs and dossiers a concurrent branch landed first. `--selftest` checks the checker. |
| `scripts/check_docs.py` | The flow documents' cross-references: section numbers and titles, command names, plugin paths, phase references. Catches the dangling pointer an agent would otherwise follow into nothing. `--selftest` checks the checker. |
| `scripts/check_permission_maps.py` | Sub-agent permission maps: allow-list shape, read-to-edit symmetry, and pre-spawn `TEST_PATHS` validation against the host's boundary (`--host`) — the frontmatter tool list is read first, so a read path for a tool list with no `Read` is refused, an existing `TEST_PATHS` target for a `Write`-only author warns delete-first, and a tool list that disagrees with a map is a defect in the agent file; the size and one-path-per-flow gates for both test authors (`--expected-lines`, `--flows`). `--selftest` checks the checker. |
| `scripts/check_payload.py` | A spawn payload before it ships: field names per agent kind (missing and misnamed), absolute paths that exist, no unexpanded `${PLUGIN_ROOT}` or `@@TOKEN@@` template placeholder, no credential literal, a `TEST_COMMAND` verb that matches the script's shebang, a `DOSSIER` that is the run's live copy, no path handed to an agent whose tool list grants no `Read` — in a field or in prose (`--host claude`) — no read an agent cannot bound, an integration excerpt equal to the live dossier (`--live-dossier`), and a warning on a whole-value checklist line with no assertion-form tag. `--selftest` checks the checker. |
| `scripts/check_harness_edit.py` | The row-4 boundary at Phase 6: exit 0 harness-only, exit 1 assertion-bearing — a hoisted expected value the assertion reads included — exit 2 unknown, ruled conservatively. `--selftest` checks the checker. |
| `scripts/spawn_admission.py` | Admission control for a wave: a machine-wide slot semaphore and a quota ledger every session shares, so a wave is split or deferred before it spawns, a provider's named reset closes the window for every checkout, and the next run starts from the ceiling the last one learned. `--selftest` checks the checker. |
| `scripts/safe_revert.py` | The only way the orchestrator reverts or deletes a path an agent may hold uncommitted: copies it outside the repository first, prints where, refuses a copy target inside the repository. `--selftest` checks the checker. |
| `scripts/dossier_edit.py` | A dossier write without a dossier read: append to `## Build log`, set or extend a front matter field, print one section with line numbers. `--selftest` checks the checker. |
| `scripts/prepare_wave.py` | One call per fan-out wave: every payload lint and boundary gate through the scripts that own them, only the defects printed, then the admission slots and the `PAYLOAD-LINT:` / `ADMISSION:` lines. `--selftest` checks the checker. |
| `scripts/compose_payloads.py` | A spawn payload with every mechanical field filled from the dossier and the contract files (a `MODE: fix` implementer's TEST_COMMAND from the run's gates file), and every judgement field left as a `@@FILL_<FIELD>@@` placeholder the lint refuses. `--selftest` checks the checker. |
| `scripts/git_state.py` | A worktree's state in one bounded call: branch, HEAD, ahead/behind, status counts and paths, diff stat, in-progress merge. `--selftest` checks the checker. |
| `scripts/run_tests.py` | Runs a suite (optionally inside a Lima VM) with the whole output in a log, and prints the summed counts and each failure, bounded; cargo, nextest, pytest and TAP parsers, plus a suite-aggregator parser (`<suite> PASS|FAIL (exit N)|SKIP: …` lines and an `N passed, N failed, N skipped` summary, as a repository's `run_all_tests.sh` prints) that pulls each failing suite's section out of the aggregator's full log, lists every SKIP, and names the slowest steps from its `.durations.tsv`. `--repeat N` supports targeted flake investigations: it names each test that failed in some runs but not all. Whole-suite repetitions are not mandatory. `--expect red|green` proves a named test fails before a fix and passes after it (a test that never ran is no proof). `--selftest` checks the checker. |
| `scripts/flake_risk.py` | The lines in test files that make a test likely to flake under load — a sleep, a sub-second bound, a wall-clock read, threads or tasks, shared state, a fixed port or path, unseeded randomness. It skips the stable forms (a sleep under a deadline, a joined spawn, a lower bound, a unit-mutex lock). It reads the repo's `.flake-risk-exempt.md` for lines a review ruled harmless, each with its mechanism. Decides whether the Phase 7 `tests` lens is due, and gives it its scope. `--selftest` checks the checker. |
| `scripts/review_signals.py` | The build's added lines a Phase 7 lens should look at: swallowed errors, dropped causes, calls with no timeout, unbounded retries, log lines an operator cannot act on or that name a secret, and the security categories (secrets, auth, CI, infra, dependencies, migrations, exec, SQL) the change reaches, after the repo's `.review-triggers.md`. Fills the architecture lens's `SIGNALS` (`compose_payloads.py --review-base`). `--selftest` checks the checker. |
| `scripts/check_crs.py` | A reviewer's change-request document by its fields (Siblings included): `ok` or `demote (missing: ...)` per CR, the `DEFECT` sections to prove, and `--drop` to hand the fix implementer the document without the demoted or unproven sections. `--selftest` checks the checker. |
| `scripts/wait_ci.py` | Waits for a PR's checks without output, then prints each check and a bounded digest of every failed job's log. Exits 4 for a green on a superseded head and 5 for a conflicting PR (checks will never start). `--selftest` checks the checker. |
| `scripts/run_gates.py` | Runs a list of gate commands, one log per gate, and prints one line per gate plus the failing lines, bounded; exits 3 when a merge, rebase, cherry-pick or bisect is in progress; `--from-workflow FILE...` derives the gates file from a CI workflow's `run:` steps. `--selftest` checks the checker. |
| `scripts/code_nav.py` | Structural reads and edits for Rust, Python and markdown: an outline with line ranges, one item (whole, contract, doc or signature), where a symbol is defined, one markdown section by heading or id, and a replace of one item. Exposed to opencode as `code_*` and `doc_section` tools by `opencode/plugins/code-nav.ts`. `--selftest` checks the checker. |
| `scripts/tftest_runs.py` | Terraform `.tftest.hcl` judged one run block at a time: a scratch module copy per run (shared mock_provider/variables kept), `terraform init -backend=false` + `test`, one `PASS\|FAIL <run name>` line each — because `terraform test` skips every run after a failed `expect_failures` run. `--selftest` checks the checker. |
| `scripts/materialise_stubs.py` | Compiling stubs from a signature-only contract, deterministically: every contract line byte-for-byte, a throwing body per member that needs one, `### Definitions` placed, the mechanical diff run in-process — the no-agent route beside `stub-materialiser`. `--selftest` checks the checker. |
| `scripts/gate_commit.py` | Gates, stage only the named paths, commit (signed when asked), push when asked; at most 12 lines out. `--selftest` checks the checker. |
| `scripts/verify_return.py` | One call after a subagent returns: worktree state, its commits, paths touched beyond ownership, stubs left, the contract hash and the slot release, with the mechanical half of the build-log line. `--selftest` checks the checker. |
| `scripts/token_report.py` | Where an opencode setup spends its tokens in a time window: per agent, per orchestrator run, carried context by kind, tool adoption and bypasses, plugin events. `--json` snapshots and `--compare` diffs them; the evidence behind `ledger/token-spend.md`. `--selftest` checks the checker. |
| `scripts/build_claude_plugin.py` | The Claude Code surface, derived from the opencode sources. `--check` fails on drift; `--selftest` checks the checker. |

## Why the validator matters more than it looks

Three agents write to one repository concurrently. Two work packages that own
the same path corrupt a merge, and the loser's tests then fail for a reason
nobody can diagnose. `validate_pipeline.py` proves disjointness **before** the
fan-out, which is the one failure mode in this design that is expensive and
silent.

```
python3 scripts/validate_pipeline.py --dossier W-014
python3 scripts/validate_pipeline.py --all
python3 scripts/validate_pipeline.py --write-index
python3 scripts/validate_pipeline.py --finalize-ids --base origin/development
python3 scripts/validate_pipeline.py --selftest
```

Branches never commit `docs/adr/index.md` (regenerate to validate, then
`git restore`); `--finalize-ids`, run after the sync merge, renumbers the
IDs a concurrent branch landed first.

## Gates

Both DevKit gates apply, and only the top-level session handles them — never a
sub-agent:

1. **Time logging** — offered at the top of every command, including read-only
   ones. Never logged silently. One Tempo session covers the run.
2. **Worktree** — `/work-on` creates worktrees by design, so it says so and gets
   an explicit yes first. `/plan` does the same when `.discovery/` is
   committed, and asks once more before it pushes the plan branch and opens
   its PR.
3. **Choose the suite runner by coverage** — CI supplies the integrated
   verdict only when it covers the required suite. Repository policy or
   partial CI can require a full local command, while CI's partial gates
   remain useful. Record `SUITE-RUNNER:`, the full `SUITE-COMMAND` and required
   coverage in Phase 2; `validate_pipeline.py --pre-fanout` requires the
   runner line. When CI is the runner, `/work-on` gets authorization at the
   worktree gate to push and open a draft PR at Phase 6. Keep the tested
   commit, baseline, full log and omissions for the verdict. Reuse it until
   relevant inputs change; flake investigations repeat affected tests only
   when needed.

Nothing external happens without an explicit yes: no Jira create, no Jira
transition, no PR, no push to a protected branch. No force-push, no history
rewrite, and the pipeline never merges a PR — merging is the user's.
