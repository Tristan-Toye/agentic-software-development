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
  into all three lenses hands every lens every other lens's question, and the
  duplicate CRs that follow are exactly what the `LENS` field exists to
  prevent. **The orchestrator narrows it per lens before it is a payload
  field**, and a rule that belongs to no lens is dropped, not distributed.
  It is a checklist, not a licence: a rule still becomes a CR only with that
  lens's evidence, and it loses to `STANDARDS`, `CONTEXT_DOCS` and
  `ARBITRATIONS` whenever they disagree. `RULES: none` is a valid field and
  the right one when nothing resolved or `ocr` was unavailable — the field
  stays present so a lens can tell "no rules" from "forgotten".
- **`RUN_EVIDENCE` is green when a code lens sees it.** A reviewer never runs
  anything and never needs to check the suite's claim.
- **`ARBITRATIONS` accumulates across rounds** and goes into every later spawn,
  so no reviewer re-litigates a question the user already settled.
