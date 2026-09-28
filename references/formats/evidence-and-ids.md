# 3. Evidence labels

Mandatory in `## Problem`, `## Context`, and `## Consequences`. Prefix each
claim with exactly one:

- `FACT` — checked in this repository, with a `path:line` anchor or a command
  output.
- `INFERENCE` — derived from facts. Name the facts it derives from.
- `ASSUMPTION` — believed, not checked. Name what would prove it wrong.
- `UNKNOWN` — an open question. Name who or what answers it.

An `ASSUMPTION` that is load-bearing in `## Approach` blocks `status: ready`.
Promote it to `FACT`, or move it to a stated risk.

---

# 4. ID minting

Dossier IDs (`W-NNN`) and ADR IDs (`ADR-NNNN`) are sequential within their own
kind. Mint one atomically so two sessions never collide: open the new file with
exclusive-create semantics — `set -o noclobber` on the redirect, or
`python3 -c "open(p,'x')"` — and on a collision take the next number and retry.
Never scan for the highest number and then write.

Dossier IDs follow the ADR rule when `.discovery/` is committed: the plan
branch mints against the base tip its worktree forked from, so a sibling plan
PR can hold the same number. `/plan` Phase 7 merges the base and runs
`--finalize-ids`, which renumbers the dossiers this branch added exactly as
it renumbers ADRs and LRNs — file name, `id:`, and every mention in the
branch's own dossiers. Two dossiers that land with one ID are a DEFECT from
`validate_pipeline.py --all`, which `/work-on` Phase 0 stops on; the later
one renumbers in its own PR the same way.

The exclusive-create guard works per working copy. ADRs and LRNs are committed,
so two concurrent branches can still mint the same number, and the collision
only becomes visible at merge time. That is expected and handled: **a number
minted on a branch is provisional until the branch's sync merge.** The
serialization point is Phase 9's sync step (or the post-landing refresh, when
several PRs land together): after merging the target branch, run

```
scripts/validate_pipeline.py --finalize-ids --base origin/development
```

It detects collisions against the merged tree, renumbers only the ADRs and
LRNs this branch added, rewrites their references inside the branch's own
files and dossiers, and regenerates the index. Never merge two decisions
under one number, and never hand-renumber: the finalize step owns renumbering.

---
