# Spawn payloads — one skeleton per agent

Four agents write code: three build agents and one test maintainer. Five
flash support agents carry mechanical work off the orchestrator's context.
Two independent checkers — `contract-reviewer` before the fan-out,
`mutation-tester` after the suite goes green — verify what the orchestrator
cannot verify alone. Copy the skeleton, fill every line, delete nothing.

A malformed payload is the likeliest silent failure in this pipeline: an agent
halts on a **missing** field, but a **misnamed** field is simply ignored. A
misnamed `OWNED_PATHS` is the worst case — the agent writes wherever it likes
and corrupts a concurrent agent's work.

There is no `BAND`, no `TIER`, and no `RETURN_CEILING`. Every agent returns a
short structured report because its own prompt says so.

**Paste, never reference, anything an agent's blindness depends on.**
`unit-test-author` has no `Read` tool, so a path in its payload is a dead
letter — `CONTRACT` and `STYLE_SAMPLE` must be the text itself. The same holds
for `CONTRACT` everywhere: an agent that reads the contract out of the dossier
could read the rest of the dossier too.

**Never describe the blindness machinery to the agent it constrains.** An agent's
payload and prompt carry the rules that bind it, and nothing about how the
pipeline keeps it blind. Do not tell an implementer that tests exist, where they
are, or that they are uncommitted; do not tell it "do not look for them." Three
reasons, and the third is the one that matters:

1. It does not need the information to do its job.
2. It is noise in a payload whose quality is the run's quality.
3. **A description of the mechanism is a map to the thing the agent must not
   find.** "The tests are not on your branch" tells a capable agent that they are
   somewhere, and that a branch is where to look.

State the rule (`never write a test`, `never change a signature`) and stop. The
mechanism lives in `commands/work-on.md`, which only the orchestrator reads.
Blindness that has to be explained to stay intact is not blindness.

---

## unit-test-author

Its payload is its entire world. It has exactly one tool, `Write`. Every field
here must be literal text — a path it cannot open buys nothing.

```
CONTRACT: |
  class FlushQueue:
      def flush(self, batch_size: int) -> int:
          """Drain queued items to the store, oldest first.

          Drains at most `batch_size` items per call. Concurrent calls
          coalesce: each queued item reaches the store exactly one time.
          Returns the number of items written. Returns 0 when the queue is
          empty. Raises ValueError when batch_size < 1.
          """
PROMISE_CHECKLIST: |
  flush — return meaning: number of items written
  flush — order: oldest first
  flush — empty case: returns 0
  flush — invalid case: raises ValueError when batch_size < 1
  flush — concurrency: concurrent calls coalesce; each item reaches the
          store exactly one time
TEST_PATHS: /abs/path/repo-W-014/tests/unit/test_flush_queue.py
TEST_FRAMEWORK: pytest; plain `assert`; run with `pytest tests/unit -q`
CITATION: |
   // promise: flush/return-meaning
   One comment per test, the line above the test, in exactly this shape —
   `promise: <checklist id>/<category>`. One citation vocabulary per build;
   authors inventing their own shape diverge per file.
CONVENTIONS: |
   Derive Debug on every constructed type. The spelling checker accepts
   "coalesce", "backfill". Checklist ids are <member>/<category>, lowercase,
   hyphen-free.
STYLE_SAMPLE: |
  <one existing test from this repo, verbatim, imports included>
NAMING: Subject_StateUnderTest_ExpectedBehavior — Subject is the public member
        under test, e.g. Flush_EmptyQueue_ReturnsZero
VOCABULARY: "drain", "coalesce", "batch" — the project's terms for these ideas
FIXTURES: |
  FlushQueue(store=FakeStore(), clock=FakeClock())
  FakeStore exposes .written -> list[Item] and .write_count -> int
  Build an Item with make_item(id: str) from tests/support/factories.py
CONTRACT_HASH: 3f2611f0a91c4d8e
```

`CONTRACT_HASH` is the orchestrator's stamp of the contract text above —
paste the hash and never explain it to the author. When the author returns,
re-hash the contract files: a different hash means the contract changed while
the author was writing, its world is stale, and the re-spawn is unconditional
(its `GAP:` analysis, if any, is still input to the contract fix).

Absolute `TEST_PATHS` inside the base worktree. The orchestrator commits its
output — it has no `Bash`.

## integration-test-author

```
WORKTREE_DIR: /abs/path/repo-W-014
DOSSIER: /abs/path/repo/.discovery/dossiers/W-014-flush-coalescing.md
TEST_PATHS: /abs/path/repo-W-014/tests/integration/test_flush_flow.py
           # one path per flow — a GAP: or a vacuous test then re-spawns one
           # flow, not the whole set; and no single Write runs long
TEST_FRAMEWORK: pytest; run with `pytest tests/integration -q`
HARNESS: |
  The `app_client` fixture in tests/integration/conftest.py stands up the real
  app against a throwaway Postgres from testcontainers. Seed with
  `seed_publication(client, items=N)`.
STYLE_SAMPLE: |
  <one existing integration test, verbatim>
BOUNDARIES: IClock (time), IPaymentClient (external API) — substitute these two
            only. Everything else runs for real.
CONTRACT_HASH: 3f2611f0a91c4d8e
```

Same `CONTRACT_HASH` rule as the unit author: stamp at spawn, re-hash at
return, re-spawn unconditionally on a mismatch.

The agent reads `## Problem`, `## Approach`, `## Contract` and
`## Acceptance criteria` from `DOSSIER`, and never `## Build log`.
The orchestrator commits its output; it has no `Bash`.

## implementer

`MODE: build` — one work package, in its own worktree, blind to every test:

```
WORKTREE_DIR: /abs/path/repo-W-014-P1
BRANCH: fix/PROJ-142-flush-coalescing-p1
MODE: build
CONTRACT: |
  <the same contract text, verbatim — not a path>
PACKAGE: P1 flush coalescing — make concurrent flush() calls coalesce behind a
         single drain, so each queued item reaches the store one time.
SHARED_IDIOM: |
  <when two or more agents will write the same concept — a type, a helper, a
   error-mapping shape — paste the ONE named idiom here, identically in every
   such payload. Blind agents cannot converge; invented idioms diverge, and
   the collapse is paid for later. Omit when no concept is shared.>
OWNED_PATHS: src/flush.py
CRITERIA: |
  1. With 3 parallel flush(batch_size=10) calls and 5 queued items, the store
     receives each item one time.
  2. flush() on an empty queue returns 0 and writes nothing.
TEST_COMMAND: pytest -q            # the EXISTING suite, for collateral damage
STANDARDS: /Users/tristan.toye/Documents/personal/repos/agentic-software-development/skills/standards/engineering-standards.md
JIRA_KEY: PROJ-142
# the report must end with a TOUCHED_BEYOND section (see the field rules)
```

`MODE: fix` — apply the review change requests, in the base worktree, with the
tests present and green:

```
WORKTREE_DIR: /abs/path/repo-W-014
BRANCH: fix/PROJ-142-flush-coalescing
MODE: fix
CONTRACT: |
  <verbatim>
CRS: |
  <the merged change-request documents from the three lenses, verbatim,
   plus any user arbitration that overrides one of them>
OWNED_PATHS: src/flush.py, src/reload.py
TEST_COMMAND: pytest -q            # now the invariant: it is green, keep it green
STANDARDS: /Users/tristan.toye/Documents/personal/repos/agentic-software-development/skills/standards/engineering-standards.md
JIRA_KEY: PROJ-142
```

In `fix` mode the suite is the invariant, not a collateral-damage check: a red
test after a fix means the fix was not behaviour-preserving. Never widen
`OWNED_PATHS` to include a test path — the implementer never edits a test.

**Both modes end their report with a `TOUCHED_BEYOND` section** listing every
path they changed outside `OWNED_PATHS`, one line per path with a one-line
reason — `TOUCHED_BEYOND: none` when the diff is clean. The orchestrator
diffs mechanically and never trusts the section alone, but the section turns
drift into a stated claim: a path the implementer touched, did not list, and
cannot justify is a defect in the implementer's work, not just package-table
noise. Two hard limits the section cannot excuse: another package's owned
paths, and the contract files.

## test-maintainer

For a review change request against a unit-test file whose fix stays outside
the assertions — a rename, a citation comment, an import, a doc comment. The
orchestrator copies the file to a scratch directory **outside the repo** (an
OS temp dir), points the maintainer at the copy, and copies the result back
only when `scripts/check_harness_edit.py --diff` exits 0 against the
original. One spawn per file, per review round.

```
FILE: /var/folders/.../T/opencode/test_flush_queue.py   # the copy — its
      # entire world; never name the repo path it came from
CRS: |
  <the non-assertion change requests for this file, verbatim>
CITATION: |
  // promise: flush/return-meaning
  <the citation shape, when a CR is about citation comments>
```

The maintainer's world is exactly `FILE` plus `CRS` — informational
blindness, the same mechanism that keeps the implementer blind. An
assertion-touching or restructuring CR is not maintainer work: it is
authorship, and routes to `unit-test-author` with the current file content
pasted into the payload as base material.

## Independent checkers

Two agents close loops the orchestrator cannot close alone. The
contract-reviewer derives its own promise checklist from the materialised
stubs, so a thin contract is caught before the fan-out instead of at
arbitration. The mutation-tester turns `PROMISE_CHECKLIST` into mutants, so
a weak oracle is caught after the build instead of in production.

### contract-reviewer

Spawned between Phase 3 and Phase 4, on the materialised stubs — never on
the dossier or the orchestrator's checklist.

```
WORKTREE_DIR: /abs/path/repo-W-014
CONTRACT_PATHS: src/flush.py
CRITERIA: |
  <the acceptance criteria, verbatim>
CHECKLIST_RULES: |
  <the observability checklist from references/formats.md, pasted verbatim —
   the six categories, the visibility question, the unmeasurable-words rule>
```

It returns its own full checklist plus `AMBIGUITY:` and `DEFECT:` lines. The
orchestrator diffs the two checklists: every disagreement is a contract
defect caught pre-fan-out. An `AMBIGUITY:` line carries both readings
verbatim — take the two readings to the user as one question, never pick a
side silently.

### mutation-tester

Spawned when the suite first goes green, on a throwaway worktree the
orchestrator creates first — it never branches. It runs while the review
lenses work.

```
WORKTREE_DIR: /abs/path/repo-W-014-MUT   # pre-created throwaway worktree
CONTRACT_PATHS: src/flush.py
PROMISE_CHECKLIST: |
  <verbatim, strong form — the mutants are derived from these lines>
SURFACE: FlushQueue — the primary surface under mutation
TEST_COMMAND: pytest -q
MAX_MUTANTS: 3
```

It returns a kill table. A `SURVIVED` row is a missing or weak
`PROMISE_CHECKLIST` line: route it to the owning test author exactly like a
`GAP:`. `UNUSABLE` (baseline not green) is exit-2 semantics — the check did
not run, which is never a pass.

## reviewer

`LENS: plan` — before any code exists:

```
LENS: plan
DOSSIER: /abs/path/repo/.discovery/dossiers/W-014-flush-coalescing.md
WORKTREE_DIR: /abs/path/repo            # the main checkout, to check anchors
CRITERIA: |
  <the acceptance criteria, verbatim>
STANDARDS: /Users/tristan.toye/Documents/personal/repos/agentic-software-development/skills/standards/engineering-standards.md
CONTEXT_DOCS: /abs/path/repo/docs/adr/0003-session-store.md
ROUND: 1
```

`LENS: style | architecture | performance` — after the suite is green. Spawn all
three in one message so they run concurrently:

```
LENS: performance
WORKTREE_DIR: /abs/path/repo-W-014
SCOPE: |
  Blast radius — a location list, never the diff:
    src/flush.py :: FlushQueue.flush, FlushQueue._drain (changed)
    src/reload.py :: on_reload (changed)
    src/store.py :: Store.write_batch (direct caller, unchanged)
  Change requests must stay inside it.
CONTRACT: |
  <verbatim — the authority on what the code must do>
RUN_EVIDENCE: |
  <the full test run output; it is green>
CRITERIA: |
  <the acceptance criteria, for context>
STANDARDS: /Users/tristan.toye/Documents/personal/repos/agentic-software-development/skills/standards/engineering-standards.md
CONTEXT_DOCS: /abs/path/repo-W-014/docs/adr/0003-session-store.md
ARBITRATIONS: |
  - user ruled 2026-08-10: keep the retry inside flush(); do not extract it
    (overrides architecture CR-2 from round 1)
ROUND: 2
```

On a re-review, add `PRIOR_CRS` with that lens's **own** change requests from the
previous round — never another lens's. The reviewer answers `CR-n: resolved` or
`CR-n: not resolved — <what is still wrong>`, and opens no new subject.

```
PRIOR_CRS: |
  CR-1: replace the linear scan in _drain with a set membership check
  CR-2: hoist the serialiser construction out of the per-item loop
```

**Mark a structural request as such.** `LENS: architecture` and
`LENS: performance` may review the new structure their own accepted request
created, inside that request's footprint, one level deep — because applying a
structural change request produces code no lens has ever seen. `LENS: style`
never gets that permission: a rename cannot create new structure. Say which
requests were structural and what the fix added, so the reviewer knows what it
may look at:

```
PRIOR_CRS: |
  CR-1: [structural — the fix added ItemIndex, a new dict-backed lookup type in
         src/flush.py] replace the linear scan in _drain with a set membership
         check
  CR-2: [local] hoist the serialiser construction out of the per-item loop
```

## Support agents

Five flash agents carry mechanical work off the orchestrator's context. Each
returns a **guidance doc** — pointers, verbatim quotes, neutral flags — never a
ruling; the orchestrator investigates and decides. Spawn one only past its size
gate (`commands/work-on.md` names the gates); below the gate the orchestrator
does the work itself.

### stub-materialiser

```
WORKTREE_DIR: /abs/path/repo-W-014
CONTRACT: |
  <verbatim — byte-identical to every other payload this fan-out>
OWNED_PATHS: src/flush.py
STUB_STYLE: |
  unimplemented!()      # the repo's own placeholder, verified against the repo
BUILD_CHECK: cargo check
```

### coverage-auditor

```
PROMISE_CHECKLIST: |
  <verbatim, with ids>
TEST_PATHS: /abs/path/repo-W-014/tests/unit/test_flush_queue.py
CONTRACT: |
  <verbatim>
```

### arbitration-clerk

```
FAILURE_LOG: |
  <the full test output, verbatim — never a summary>
FAILURES: |
  - test_flush_returns_count
  - test_flush_empty_queue
TEST_PATHS: /abs/path/repo-W-014/tests
SOURCE_PATHS: /abs/path/repo-W-014/src
CONTRACT: |
  <verbatim>
```

### blast-radius-scout

```
WORKTREE_DIR: /abs/path/repo-W-014
BASELINE: 3f2611f
HEAD: e09ab77
HINT: |
  FlushQueue.flush and its drain loop — the orchestrator's prior belief,
  checked first, overridden by what the diff actually shows
```

### document-drafter

`MODE: adr`:

```
MODE: adr
DECISIONS: |
  - decision: <one sentence, the orchestrator's words>
    evidence: |
      <verbatim quote + path:line, selected by the orchestrator>
FORMAT: |
  <the ADR shape from references/formats.md, pasted verbatim — sections,
   front matter fields, validator rules>
TARGET_PATHS: /abs/path/repo-W-014/docs/adr/0031-flush-drain-lock.md
SCRUB: W-014, .discovery, repo-W-014
```

`MODE: pr`:

```
MODE: pr
DOSSIER-EXCERPTS: |
  <## Problem, ## Approach, ## Acceptance criteria — verbatim>
FORMAT: |
  <the PR description shape this repo uses>
TARGET_PATHS: /abs/path/repo/.discovery/pr-draft-W-014.md
SCRUB: W-014, .discovery/dossiers, repo-W-014
```

---

## Field rules that matter

- **Every path in a payload is absolute.** `.discovery/` is untracked, so it
  exists only in the main checkout — a relative dossier path read from inside
  a worktree resolves to a file that does not exist, and the agent halts or
  guesses. The same rule keeps `TEST_PATHS`, `CONTEXT_DOCS`, and `STANDARDS`
  unambiguous whatever the agent's working directory is.
- **`SCOPE` is a location list, never a diff.** Passing a diff breaks the
  reviewer's blindness, and blindness is the whole reason its verdict is worth
  anything.
- **`OWNED_PATHS` is assigned by the orchestrator from `## Work packages`**,
  never negotiated by the agent, and disjoint across every concurrent spawn.
  `scripts/validate_pipeline.py` checks the disjointness before the fan-out.
- **`CONTRACT` is identical, byte for byte, in every payload of a fan-out.** All
  three agent kinds build against the same text; a divergence between two copies
  produces test failures with no diagnosable cause.
- **`RUN_EVIDENCE` is green when a code lens sees it.** A reviewer never runs
  anything and never needs to check the suite's claim.
- **`ARBITRATIONS` accumulates across rounds** and goes into every later spawn,
  so no reviewer re-litigates a question the user already settled.
- **`STYLE_SAMPLE` and `HARNESS` are the difference between a usable test and a
  rewritten one.** Paste real code from this repository, not a description of it.
- **A convention you hand a blind author must compile and lint clean first.**
  `NAMING`, `STYLE_SAMPLE` and `FIXTURES` are executable instructions, not
  prose. Write one throwaway example of the naming shape, run the repository's
  own linter over it, and only then put it in a payload. An author with no
  compiler cannot discover that your convention fights the language, and it
  will fight it once per test.
- **`CITATION` names its exact shape, and one vocabulary serves the whole
  build.** A test's citation comment is the evidence Phase 5 diffs against
  `PROMISE_CHECKLIST`; authors left to invent the shape invent a different one
  per file, and the diff dies at the first mismatch.
- **`CONVENTIONS` carries the repo facts a blind author cannot discover.**
  Derives to add, spelling-checker tokens, id shapes — each one verified
  against the repository itself, never from memory. A wrong convention here
  costs a re-spawn of the whole file.
- **`SHARED_IDIOM` is identical, byte for byte, in every payload that shares
  the concept.** Two implementers inventing the same helper produce two
  helpers, and the collapse is paid for at review time.
- **You format a blind author's files in the commit that lands them.** A
  Write-only author cannot run the formatter; run it yourself over exactly
  the named files — single-file invocation, never package-wide — inside that
  commit, never a later one.
- **A support agent's report is a guidance doc, not a verdict.** Pointers,
  verbatim quotes, neutral flags (`WEAK?`, `no-test-found`, `NOT-FOUND`) —
  pasted into `## Build log` and investigated by you before anything acts on
  it. A support agent that starts ruling has stopped being auditable.
- **`STUB_STYLE` and `BUILD_CHECK` are verified against the repo before the
  spawn.** The placeholder must be the repo's own, and the compile command
  must run in `WORKTREE_DIR`; a stub spawn with an invented style produces
  stubs the fan-out cannot build on.
- **`FAILURE_LOG` is pasted verbatim, never summarised.** The clerk's value is
  exact quotes at `path:line`; a pre-digested log would pre-digest the
  ruling too.
- **`SCRUB` lists every token that must not leave the machine.** The drafter
  greps its own output and you grep again before the description reaches the
  PR — two checks, because a leaked dossier id is a leaked local path.
- **`CONTRACT_HASH` stamps which contract an author saw.** Hash the contract
  files at spawn time, paste the hash bare — never explained — into every
  test-author payload, and re-hash when the author returns. A mismatch means
  the author's world went stale mid-flight: the re-spawn against the current
  contract is unconditional, whatever the author's report says. The hash
  exists for the orchestrator's comparison, not for the agent's use.
- **`TOUCHED_BEYOND` turns silent drift into a stated claim.** The
  orchestrator still diffs `git diff --name-only` against `OWNED_PATHS`
  mechanically; the section is what the diff is checked *against*. An
  unlisted path is an implementer defect; a listed one is a decision the
  orchestrator makes — accept and update the package table, or reject and
  revert. A `TOUCHED_BEYOND` entry never excuses another package's owned
  paths or the contract files.
- **A test-maintainer spawn names exactly one world: `FILE`.** The path is a
  copy in a scratch directory outside the repo, and the payload never names
  the repo path it came from — the maintainer must not be able to read the
  implementation around it. Copy back only on `check_harness_edit.py --diff`
  exit 0; anything else is authorship and routes to the test author.
- **A mutation-tester worktree is pre-created and throwaway.** The
  orchestrator makes the branch before the spawn and deletes it after the
  report; the agent's own bash denies commit and push, so a mutant can never
  reach a real branch by accident. `UNUSABLE` is not a verdict on the tests —
  it means the check did not run.
- **`NOTICED:` is harvested into `## Build log`.** Every support report ends
  with one, `none` allowed; the Phase 9 deferred-issues capture draws on your
  own reads plus this harvest.
