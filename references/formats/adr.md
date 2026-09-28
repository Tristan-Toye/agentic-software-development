# 2. The ADR — `docs/adr/NNNN-<slug>.md`

`/work-on` extracts ADRs after the build, from what the build actually decided:
the contract choices, the rejected alternatives, the test-versus-implementation
arbitrations, the `CONTRACT-CHANGE:` rulings, and the trade-offs the
architecture and performance lenses surfaced.

**Extract selectively.** One ADR per decision that would change how a future
agent writes code in this repository. A build may produce zero ADRs, one, or
several. A dossier is not entitled to an ADR, and an ADR per dossier is a sign
the extraction step was skipped and a template was filled instead.

Write an ADR when: a boundary moved, a contract was chosen over a real
alternative, a performance trade-off was accepted, a convention was set, or a
constraint was discovered that future work must respect. Do not write one for:
a bug fixed as specified, a rename, a mechanical change, or a decision already
recorded in an existing ADR — amend that ADR's `## Consequences` instead.

## The index contract — why titles are what they are

An agent that needs recorded knowledge reads `docs/adr/index.md` and nothing
else, then opens only the ADRs it selected. It never globs `adr/*.md`, and it never
opens an ADR to find out whether the ADR is relevant. That puts one hard rule
on titles:

> **A title states the subject AND the decision, in that order.** A reader must
> be able to skip the ADR from the title alone.

- Correct: `Coalesce concurrent flush calls behind a single drain`
- Correct: `Reject Redis for the session store; use Postgres`
- Wrong: `Flush queue` — subject only, so it forces an open.
- Wrong: `Concurrency improvements` — states neither.

`index.md` is a derived view. Regenerate it with
`scripts/validate_pipeline.py --write-index`; never hand-edit a row.

Because every branch appends rows at the table tail, a committed index
collides on every concurrent merge. So branches **never commit `index.md`**:
regenerate it locally to validate (`--write-index`, then run the validators),
then `git restore docs/adr/index.md` before committing. CI regenerates and
commits it on the target branch after each merge. The index a future agent
scans is always the one on the target branch, always current, and never a
merge-conflict surface.

```markdown
# ADR index

<!-- generated from adr/*.md front matter — regenerate, never hand-edit -->

| ID | Status | Title |
|---|---|---|
| ADR-0007 | accepted | Coalesce concurrent flush calls behind a single drain |
| ADR-0003 | superseded | Reject Redis for the session store; use Postgres |
```

## Front matter

```yaml
---
id: ADR-0007
title: Coalesce concurrent flush calls behind a single drain
status: accepted          # accepted | superseded | rejected
date: 2026-08-10
jira: PROJ-142            # the ticket of the build that produced this
                          #   decision. Never a dossier ID: the ADR is
                          #   committed and the dossier is local.
anchors:                  # where the decision lives in the code
  - src/flush.py:41
supersedes: []
superseded_by: null
relates_to: [ADR-0003]
tags: [concurrency, api]
---
```

## Body — four sections, fixed order

| # | Heading | Content |
|---|---|---|
| 1 | `## Context` | The forces that made a decision necessary. Every claim carries an evidence label. |
| 2 | `## Decision` | **One sentence**, active voice, present tense. Then the reasons. |
| 3 | `## Consequences` | What becomes easy. What becomes hard. What a future change must respect. |
| 4 | `## Alternatives` | Each rejected option and the one reason it lost. |

`## Consequences` is the section future agents actually read, so write it for
them: state the constraint a future change must respect, not a summary of the
work. `Any new caller must go through Drain(); direct writes to the store
bypass the coalescing` is useful. `This improves performance` is not.

---
