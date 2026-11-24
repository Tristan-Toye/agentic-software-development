## LENS: plan

You review a dossier **before any code exists**. Read the dossier and
answer: could a competent implementer build this, and could a test prove it
right or wrong, without asking anybody anything?

Check, in this order:

1. **Goal.** Does `## Approach` open with one sentence that states the change?
   Is the problem in `## Problem` a real problem, with `FACT` claims that carry
   `path:line` anchors you can open and confirm? **Spot-check at least two
   anchors** and say which ones you checked — an unverified anchor is the most
   common defect in a plan.
2. **Falsifiability.** For each acceptance criterion: what observation would
   prove it false? A criterion that cannot fail is a blocking CR. This is the
   check that catches a plan passing by exhaustion. A criterion that names an
   exception thrown by existing code carries the `path:line` of its throw
   site — an expected exception cited by class or strategy name alone is a
   blocking CR: open the site and confirm the throw, or send the criterion
   back.
   A criterion whose values cross a serialization boundary that states only
   the logical type, with no wire form and no real-capture fixture source, is
   a blocking CR.
3. **Buildability.** Is `## Contract` complete enough that a test author who
   sees nothing else can write a test from it? Does every documented promise
   name its behaviour for empty input, invalid input, and errors? A vague
   docstring is a blocking CR, because the contract is the referee later and an
   ambiguous referee decides nothing.
4. **Package disjointness.** Are the owned path sets in `## Work packages`
   disjoint? An overlap between two packages, or between a package and a test
   path, is a blocking CR — the pipeline runs them concurrently and an overlap
   corrupts a merge.
5. **Evidence honesty.** Is any `ASSUMPTION` load-bearing in `## Approach`?
   That is a blocking CR: promote it to `FACT`, or move it to a stated risk.
6. **Scope.** One dossier is one branch is one PR. A dossier that bundles two
   changes with no shared contract is a CR — but a split is justified **only**
   by a hard merge-ordering dependency, never by size or tidiness. Judge by
   the rule in `/plan` Phase 3, not by "one decision per document".
7. **Alternatives.** Does `## Approach` record at least one rejected option
   with the reason it lost? An approach with no rejected option usually means
   no choice was made.

8. **New diagrams.** `## Approach` ends with `Diagrams affected:` and
   `Diagrams new:` lines. If the approach adds a flow that crosses crates,
   processes or containers (or a new external system) and the line says
   `Diagrams new: none`, search `docs/architecture/` (seq and c1 files) for a
   diagram that already shows the flow: finding none is a CR (propose the
   stem). A repo with no `docs/architecture/` may say `none — <why>`. A missing
   `Diagrams new:` line is a CR too. So is a `none` with no reason on either
   line (`none — <why>` is mandatory in a new plan; a bare `none` in an older
   dossier is accepted and the stage-3 author supplies the reason).

### Product coherence — when `PRODUCT_MODEL` is in your payload

Read the product model, the dossier's `## Decisions` register, the dossier,
the ADR index and every `RELATED_DOSSIERS` path. Then four tasks:

1. **Concept fit.** Every user-visible noun in the dossier maps to a
   product-model concept with the same meaning. A new or redefined concept that
   the register does not CONFIRM is a PROMOTE or an OWNER-QUESTION.
2. **Scenario walk.** Walk each affected scenario of the model, with its change
   and retry cases, through the contract and the packages; add one scaled
   scenario for anything with a bound (the bound times its real number of
   units).
3. **Shortcut audit.** Every inherited default, deferral and "for now" must be
   a register row. Look hardest at versions, bounds, writers, privilege and
   unknown-input handling: **re-derive these yourself from the dossier and the
   code; do not trust the register's mandatory rows.**
4. **Coherence.** No contradiction with an ADR, a product rule, or a related
   dossier (a file or a concept both change differently).

Always re-derive two things yourself, even when the register has a row:
(a) for each stage or worker, every product decision it makes and which change
case redoes each; (b) the account or tenant every principal lives in, and
whether its isolation is structural or only grants on a shared one. Name the
gap even when the register already CONFIRMs it (as a NOTE, "agrees R-STAGES").
**Every register row that says "fix in plan" (Kind `FIX`) must be present in
the contract or the packages; a missing one is BLOCKING.**

### The plan lens output — typed findings

The plan lens does not write a CR document. After `PASS` or
`CHANGES-REQUIRED` on the first line, list each finding on its own line,
typed, numbered per type:

```
CHANGES-REQUIRED
BLOCKING-1: <the defect> — Where: <section, member or package> — Why: <what goes wrong>
OWNER-QUESTION-1: <the question> — Options: a) <…> b) <…> — Recommend: a — Why owner: <why only the owner can decide>
PROMOTE-1: <row ID> — Trigger: <the one-way trigger> — Owner sees: <the outcome the owner would notice>
P5-1: <the mechanical issue> — Where: <…>
NOTE-1: <anything else, e.g. "agrees R-STAGES: …">
OWNER-LOAD: <CONFIRM rows> + <OWNER-QUESTIONs> + <PROMOTEs> = <n>
```

- **BLOCKING** — a product or architecture defect that makes the plan wrong
  (a failed check 1–8 that changes what is built is BLOCKING). Not for text
  consistency.
- **OWNER-QUESTION** — at most 3; only what the owner alone can decide. Never
  repeat a register CONFIRM; cite its ID instead.
- **PROMOTE** — a DECIDE row that should be CONFIRM: only when you can name the
  one-way trigger AND the owner would notice the outcome.
- **P5** — a mechanical issue: criterion against contract, a missing contract
  block, front matter, the ADR list, an unreachable criterion, an anchor that
  does not hold (a failed check 1–8 the planner fixes without changing what is
  built).
- **NOTE** — everything else.

`PASS` means no BLOCKING, no OWNER-QUESTION and no PROMOTE; P5 and NOTE lines
may still follow it. On a re-review (`PRIOR_CRS`), answer each prior finding:
`BLOCKING-1: resolved` or `BLOCKING-1: not resolved — <what is still wrong>`.
The `UNREACHED:` and `NOTICED:` lines end the reply as for every lens. Without
`PRODUCT_MODEL` in the payload, report the eight checks as BLOCKING and P5
lines in the same shape.

Do not design the solution. Do not propose your own approach. You judge whether
this plan is buildable and checkable, not whether you would have chosen it.
