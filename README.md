# agentic-software-development — a contract-first build pipeline

Three commands, four agents, two file kinds.

```
/plan  <description>   →  a buildable dossier
/work-on <ID>          →  a PR, and the ADRs the build earned
/open-work             →  what is going on
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
                                     │
              ┌──────────────────────┼──────────────────────┐
        reviewer:style       reviewer:architecture   reviewer:performance
              └──────────── all three concurrently ─────────┘
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
| **`/plan <anything>`** | A Jira key, a stack trace, a paragraph of intent → an investigated dossier: the problem with anchored evidence, the approach, the contract, disjoint work packages, falsifiable criteria. Writes `status: ready`. | 1 (`reviewer`, `LENS: plan`) |
| **`/work-on <ID>`** | Materialises the contract as real code, fans out blind and concurrent, merges, arbitrates every test failure, runs three concurrent review lenses, extracts the ADRs, opens the PR, removes the worktree. Resumable at every phase. | 3 + N, then 3 |
| **`/open-work`** | Status of every dossier from front matter alone, plus the pipeline health signals worth acting on. Read-only toward pipeline state; also renders `.discovery/analysis/open-work.html`, a self-contained animated dashboard. | 0 |

## The agents

| Agent | Tools | Why it exists |
|---|---|---|
| `implementer` | Read, Grep, Glob, Bash, Edit, Write | Fills bodies against the contract. Never changes a signature — returns `CONTRACT-CHANGE:` and stops. |
| `unit-test-author` | Read, Write, Edit — permission-scoped to `.agent-staging/` and the test families | A test written by someone who has seen the implementation re-derives the expected value the way the code does, and then it can never disagree with the code. |
| `integration-test-author` | Read, Grep, Glob, Write | A flow test needs intent, so it reads the dossier. It still cannot read a body. |
| `reviewer` | Read, Grep, Glob | Four lenses: `plan`, `style`, `architecture`, `performance`. No write tools at all, so it cannot change what it reviews. |

Three **support agents** (flash, size-gated) carry mechanical work off the
orchestrator's context. Each returns a guidance doc — pointers, verbatim
quotes, neutral flags — and never a verdict; the orchestrator investigates
every flag and keeps every judgement:

| Agent | Tools | Carries |
|---|---|---|
| `stub-materialiser` | Read, Grep, Glob, Bash, Write | The contract verbatim into compiling stubs, past four members. |
| `blast-radius-scout` | Read, Grep, Glob, Bash | The review scope as a location list, past five changed files. File selection comes from `ocr delegate preview`, so every dropped path arrives with the reason it was dropped. |
| `document-drafter` | Read, Grep, Glob, Write | ADR and PR drafts from decisions already made, self-scrubbed. |

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
.discovery/                      # gitignored — local working state
└── dossiers/<ID>-<slug>.md      # front matter = machine state (/open-work
                                 #   reads only this). Body = problem,
                                 #   approach, contract, packages, criteria,
                                 #   build log. Kept after the build.
docs/adr/                        # committed — ships with the PR
├── index.md                     # ID | status | title — the only file a
│                                #   future agent scans. Derived; CI commits
│                                #   it on the target branch, branches never
└── NNNN-<slug>.md               # extracted by /work-on, selectively
```

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

## Reference

| File | Holds |
|---|---|
| `references/formats.md` | Both file formats, the evidence labels, the ASD-STE100 subset. Read this first. |
| `references/payloads.md` | One spawn skeleton per agent, and the field rules that matter. |
| `references/time-logging.md` | The Tempo contract: one session per run, orchestrator only. |
| `skills/standards/` | The engineering standards. They bind generation and review symmetrically. |
| `scripts/validate_pipeline.py` | Front matter, section set, **path disjointness**, contract shape, criterion falsifiability, anchors, ASD-STE100. `--selftest` checks the checker. |
| `scripts/check_permission_maps.py` | Sub-agent permission maps: allow-list shape, read-to-edit symmetry, and pre-spawn `TEST_PATHS` validation. `--selftest` checks the checker. |
| `scripts/check_payload.py` | A spawn payload before it ships: field names per agent kind (missing and misnamed), absolute paths that exist, no unexpanded `${PLUGIN_ROOT}`, no credential literal. `--selftest` checks the checker. |
| `scripts/safe_revert.py` | The only way the orchestrator reverts or deletes a path an agent may hold uncommitted: copies it outside the repository first, prints where, refuses a copy target inside the repository. `--selftest` checks the checker. |
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
   an explicit yes first.

Nothing external happens without an explicit yes: no Jira create, no Jira
transition, no PR, no push to a protected branch. No force-push, no history
rewrite, and the pipeline never merges a PR — merging is the user's.
