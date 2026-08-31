# Spawn payloads — one skeleton per agent

Four agents exist. Copy the skeleton, fill every line, delete nothing.

A malformed payload is the likeliest silent failure in this pipeline: an agent
halts on a **missing** field, but a **misnamed** field is simply ignored. A
misnamed `OWNED_PATHS` is the worst case — the agent writes wherever it likes
and corrupts a concurrent agent's work.

There is no `BAND`, no `TIER`, and no `RETURN_CEILING`. Every agent is sonnet;
every agent returns a short structured report because its own prompt says so.

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
STYLE_SAMPLE: |
  <one existing test from this repo, verbatim, imports included>
NAMING: Subject_StateUnderTest_ExpectedBehavior — Subject is the public member
        under test, e.g. Flush_EmptyQueue_ReturnsZero
VOCABULARY: "drain", "coalesce", "batch" — the project's terms for these ideas
FIXTURES: |
  FlushQueue(store=FakeStore(), clock=FakeClock())
  FakeStore exposes .written -> list[Item] and .write_count -> int
  Build an Item with make_item(id: str) from tests/support/factories.py
```

Absolute `TEST_PATHS` inside the base worktree. The orchestrator commits its
output — it has no `Bash`.

## integration-test-author

```
WORKTREE_DIR: /abs/path/repo-W-014
DOSSIER: /abs/path/repo/.discovery/dossiers/W-014-flush-coalescing.md
TEST_PATHS: /abs/path/repo-W-014/tests/integration/test_flush_flow.py
TEST_FRAMEWORK: pytest; run with `pytest tests/integration -q`
HARNESS: |
  The `app_client` fixture in tests/integration/conftest.py stands up the real
  app against a throwaway Postgres from testcontainers. Seed with
  `seed_publication(client, items=N)`.
STYLE_SAMPLE: |
  <one existing integration test, verbatim>
BOUNDARIES: IClock (time), IPaymentClient (external API) — substitute these two
            only. Everything else runs for real.
```

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
OWNED_PATHS: src/flush.py
CRITERIA: |
  1. With 3 parallel flush(batch_size=10) calls and 5 queued items, the store
     receives each item one time.
  2. flush() on an empty queue returns 0 and writes nothing.
TEST_COMMAND: pytest -q            # the EXISTING suite, for collateral damage
STANDARDS: ${CLAUDE_PLUGIN_ROOT}/skills/standards/engineering-standards.md
JIRA_KEY: PROJ-142
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
STANDARDS: ${CLAUDE_PLUGIN_ROOT}/skills/standards/engineering-standards.md
JIRA_KEY: PROJ-142
```

In `fix` mode the suite is the invariant, not a collateral-damage check: a red
test after a fix means the fix was not behaviour-preserving. Never widen
`OWNED_PATHS` to include a test path — the implementer never edits a test.

## reviewer

`LENS: plan` — before any code exists:

```
LENS: plan
DOSSIER: /abs/path/repo/.discovery/dossiers/W-014-flush-coalescing.md
WORKTREE_DIR: /abs/path/repo            # the main checkout, to check anchors
CRITERIA: |
  <the acceptance criteria, verbatim>
STANDARDS: ${CLAUDE_PLUGIN_ROOT}/skills/standards/engineering-standards.md
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
STANDARDS: ${CLAUDE_PLUGIN_ROOT}/skills/standards/engineering-standards.md
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
