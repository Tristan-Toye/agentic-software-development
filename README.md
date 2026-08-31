# backend-skills — a contract-first build pipeline

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
     Write tool ONLY       reads the dossier            own worktree
     sees the contract     sees stubs, never a body     never sees a test
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

**Blindness is structural, never a promise.** `unit-test-author` has exactly one
tool — `Write` — so it cannot read an implementation even if it wanted to. Its
payload is its entire world. `integration-test-author` finds only stubs on the
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
| `unit-test-author` | **Write only** | A test written by someone who has seen the implementation re-derives the expected value the way the code does, and then it can never disagree with the code. |
| `integration-test-author` | Read, Grep, Glob, Write | A flow test needs intent, so it reads the dossier. It still cannot read a body. |
| `reviewer` | Read, Grep, Glob | Four lenses: `plan`, `style`, `architecture`, `performance`. No write tools at all, so it cannot change what it reviews. |

## The files

```
.discovery/                      # gitignored — local working state
└── dossiers/<ID>-<slug>.md      # front matter = machine state (/open-work
                                 #   reads only this). Body = problem,
                                 #   approach, contract, packages, criteria,
                                 #   build log. Kept after the build.
docs/adr/                        # committed — ships with the PR
├── index.md                     # ID | status | title — the only file a
│                                #   future agent scans
└── NNNN-<slug>.md               # extracted by /work-on, selectively
```

ADRs are **extracted, not generated**. Zero is a correct outcome for a defect
fixed as specified. An ADR per dossier means a template got filled instead of a
decision getting recorded.

Both file kinds use **ASD-STE100** (Simplified Technical English) — one term per
concept, active voice, simple tenses, 25-word sentences. Prose an agent reads one
time and gets right.

## Reference

| File | Holds |
|---|---|
| `references/formats.md` | Both file formats, the evidence labels, the ASD-STE100 subset. Read this first. |
| `references/payloads.md` | One spawn skeleton per agent, and the field rules that matter. |
| `references/time-logging.md` | The Tempo contract: one session per run, orchestrator only. |
| `skills/standards/` | The engineering standards. They bind generation and review symmetrically. |
| `scripts/validate_pipeline.py` | Front matter, section set, **path disjointness**, contract shape, criterion falsifiability, anchors, ASD-STE100. `--selftest` checks the checker. |

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
python3 scripts/validate_pipeline.py --selftest
```

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
