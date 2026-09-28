## integration-test-author

```
WORKTREE_DIR: /abs/path/repo-W-014
DOSSIER: /abs/path/repo-W-014/.agent-staging/W-014.excerpt.md
          # a file YOU generate: ## Problem, ## Approach and ## Acceptance
          # criteria copied verbatim, nothing else — never the dossier itself.
          # Regenerated from the live dossier in the turn that spawns, and
          # linted against it: check_payload.py --live-dossier <live copy>
CONTRACT: |
  <the same contract text as every other author's, pasted verbatim from the
   files on the base worktree — never read out of the dossier>
PROMISE_CHECKLIST: |
  <only when this Read + Write author is the routed shape for a UNIT surface
   — under Claude Code, a shell suite or a compiled-language test file
   (work-on.md Phase 4): that surface's checklist, tags included, exactly as
   the unit author would receive it. Omit for a flow. SUPPORT_PATHS / SUPPORT
   travel with it, same rule as the unit author's: check_payload.py flags a
   checklist line naming a type in neither CONTRACT nor SUPPORT_PATHS /
   SUPPORT (nor STYLE_SAMPLE, SHARED_IDIOM) as a defect, naming the line.>
TEST_PATHS: /abs/path/repo-W-014/tests/integration/test_flush_flow.py
           # ONE PATH PER FLOW — the default, not a hint. A GAP: or a vacuous
           # test then re-spawns one flow, not the whole set, and no single
           # Write runs long: the recorded 646-line single-file deliverable
           # returned empty twice and took three shrink rounds to land. Gate
           # it before the spawn — check_permission_maps.py --agent
           # sub-agents/integration-test-author.md --test-paths … --flows N
           # --expected-lines N warns when flows outnumber paths or the size
           # passes the single-write cap.
TEST_FRAMEWORK: pytest; run with `pytest tests/integration -q`
HARNESS: |
  The `app_client` fixture in tests/integration/conftest.py stands up the real
  app against a throwaway Postgres from testcontainers. Seed with
  `seed_publication(client, items=N)`.
STYLE_SAMPLE: |
  <one existing integration test, verbatim>
BOUNDARIES: IClock (time), IPaymentClient (external API) — substitute these two
            only. Everything else runs for real.
SHARED_IDIOM: |
  <same rule as the unit author: present, byte-identical, whenever a test
   touches a concept two or more agents write. Omit otherwise.>
CONTRACT_HASH: 3f2611f0a91c4d8e
```

Same `CONTRACT_HASH` rule as the unit author: stamp at spawn, re-hash at
return, re-spawn unconditionally on a mismatch.

`DOSSIER` is an excerpt you generate under `.agent-staging/` — `## Problem`,
`## Approach` and `## Acceptance criteria`, verbatim and nothing else — never
the live dossier. An instruction not to read `## Build log` in a file the
agent can open is not blindness: a `Read` with no limit returns the whole
file, and the recorded dossier past 900 lines handed its build log to every
reader and voided two review rounds. The contract never comes from the
dossier either: `CONTRACT` is pasted verbatim from the materialised files —
the same text every other author builds against, and the same text
`CONTRACT_HASH` stamps — so the dossier section can never drift from what the
tests were written against. The excerpt goes with the rest of
`.agent-staging/` before the commit. The orchestrator commits its output; it
has no `Bash`.

**The excerpt is derived state, regenerated at spawn time.** Like the staged
contract and its `CONTRACT_HASH`, it equals the live dossier's three
sections at the moment the payload ships — not at the moment the file was
first written. Regenerate it from the live dossier (`X`'s copy in committed
mode) in the same turn as the spawn, after every Phase 2–4 edit: the
recorded excerpt was written early in Phase 3, an owner ruling then moved
one criterion from three measured shapes to five, the contract and the
criteria were updated and the excerpt was not, and the author received
"three lines" beside a `CONTRACT` saying five. `check_payload.py --kind
integration-test-author --live-dossier <live copy>` compares the three
sections byte for byte and refuses any difference; linted without the flag,
the payload warns that the excerpt was not compared.

**A corrective round on a pre-existing test** carries `FAILURES` and
`CORRECTION` instead of folding the fix into `DELIVERY_CHANGE`, which is
written for a delivery-shape change, not a correction:

```
FAILURES: |
  <the failing run's output, verbatim>
CORRECTION: |
  1. <the first edit, exact and ordered>
  2. <the next edit>
  Read the whole file, then Write it back with exactly these changes,
  nothing else — never a partial Edit, and never a new test.
```

Routing note: a Phase 6 row-1 ruling on a pre-existing test — its hoisted
expected value is assertion-bearing, so the orchestrator may not edit it,
and the implementer never edits a test — or a census literal a sync merge
moved onto the same test, is this author's round, every time.
