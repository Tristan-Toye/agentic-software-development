## reviewer

`LENS: plan` — before any code exists:

```
LENS: plan
DOSSIER: /abs/path/repo/.discovery/dossiers/W-014-flush-coalescing.md
          # from round 2 on, a copy without `## Build log` at a path outside
          # .discovery/ — a Read with no limit returns the whole file
WORKTREE_DIR: /abs/path/repo            # ${RUN_ROOT}: the main checkout in
          # local mode, the plan worktree (/abs/path/repo-plan-KEY) in
          # committed mode — both fields point into the same tree
CRITERIA: |
  <the acceptance criteria, verbatim>
STANDARDS: ${PLUGIN_ROOT}/skills/standards/engineering-standards.md
CONTEXT_DOCS: /abs/path/repo/docs/adr/0003-session-store.md
ROUND: 1
```

`LENS: style | architecture | performance` — after the suite is green. Spawn
them in one message, with `tests` when it is due, so they run concurrently:

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
  <the dossier's `## Contract` section, verbatim — the authority on what the code must do>
RUN_EVIDENCE: |
  <the full test run output; it is green>
CRITERIA: |
  <the acceptance criteria, for context>
STANDARDS: ${PLUGIN_ROOT}/skills/standards/engineering-standards.md
RULES: |
  # ocr delegate rule, narrowed to this lens. `none` when nothing resolved.
  #### Performance
  - Recomputing inside a loop a value that is invariant across iterations
  - Building a full list where a generator would avoid holding everything
CONTEXT_DOCS: /abs/path/repo-W-014/docs/adr/0003-session-store.md
ARBITRATIONS: |
  - user ruled 2026-08-10: keep the retry inside flush(); do not extract it
    (overrides architecture CR-2 from round 1)
ROUND: 2
```

`LENS: tests` — in the same message, only when Phase 6 logged a `FLAKY` test
or `FLAKE-RISK:` found a marker in this build's test files. Its `SCOPE` is the
test files and their marked lines, not the blast radius, and `RUN_EVIDENCE`
carries the `STABILITY:` line:

```
LENS: tests
WORKTREE_DIR: /abs/path/repo-W-014
SCOPE: |
  Test files this build added or changed, with the risk scan's marks:
    tests/flush_test.rs :: flush_three_parallel_calls_each_item_written_once
      tests/flush_test.rs:41  sleep  thread::sleep(Duration::from_millis(50));
      tests/flush_test.rs:44  wall-clock  assert!(t.elapsed() < Duration::from_millis(200));
    tests/reload_flow_test.rs :: reload_drains_queue (FLAKY 1/3 in STABILITY)
  Change requests must stay inside these test files.
CONTRACT: |
  <the dossier's `## Contract` section, verbatim>
RUN_EVIDENCE: |
  STABILITY: 3 runs on 4f1c2e9 — FLAKY reload_drains_queue (failed 1/3)
  <the full green run's output>
CRITERIA: |
  <the acceptance criteria, for context>
STANDARDS: ${PLUGIN_ROOT}/skills/standards/engineering-standards.md
RULES: none
CONTEXT_DOCS: none
ARBITRATIONS: |
  none
ROUND: 1
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

## Field rules that matter

- **`SCOPE` is a location list, never a diff.** Passing a diff breaks the
  reviewer's blindness, and blindness is the whole reason its verdict is worth
  anything.
- **`RULES` is rule text, never `ocr` output verbatim.** `ocr delegate rule`
  resolves one combined checklist per path — correctness, security,
  performance, maintainability, tests in a single block. Pasting that block
  into every lens hands each lens every other lens's question, and the
  duplicate CRs that follow are exactly what the `LENS` field exists to
  prevent. **The orchestrator narrows it per lens before it is a payload
  field**, and a rule that belongs to no lens is dropped, not distributed.
  It is a checklist, not a licence: a rule still becomes a CR only with that
  lens's evidence, and it loses to `STANDARDS`, `CONTEXT_DOCS` and
  `ARBITRATIONS` whenever they disagree. `RULES: none` is a valid field and
  the right one when nothing resolved or `ocr` was unavailable — the field
  stays present so a lens can tell "no rules" from "forgotten".
- **`CONTRACT` comes from the dossier's `## Contract`, never from the contract
  files.** After the merge the files hold the implementation, so a CONTRACT
  filled from them hands the lens the code under the field that is its
  authority on behaviour, and it can no longer report "the code does not do
  what the contract says". `compose_payloads.py --kind reviewer` reads the
  section itself, refuses an empty one, and refuses `--contract-paths`.
- **`RUN_EVIDENCE` is green when a code lens sees it.** A reviewer never runs
  anything and never needs to check the suite's claim.
- **Every reply ends with `UNREACHED:`** — `none`, or the `SCOPE` locations the
  lens did not read in full. A `PASS` that is not `UNREACHED: none` is
  `incomplete`, never a pass (`references/work-on/phase-7.md`).
- **`ARBITRATIONS` accumulates across rounds** and goes into every later spawn,
  so no reviewer re-litigates a question the user already settled.
