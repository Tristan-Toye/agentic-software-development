# Formats — the files this pipeline keeps

This pipeline has three commands and two file kinds, plus one local scratch
ledger. There is no changelog, no review file, no analysis site, no memory
trace, no state file, no anomaly log, and no template directory.

```
.discovery/                 # gitignored — local to this machine
├── dossiers/
│   └── <ID>-<slug>.md      # One work item: its contract, its packages, its
│                           #   criteria, its build log. Front matter carries
│                           #   the machine state. KEPT after the build.
└── deferred-ledger.md      # /deferred's fallback ledger, when no dossier
                            #   covers the changeset. Append-only, one block
                            #   per changeset. Local, never committed.
docs/
└── adr/                    # COMMITTED — ADRs ship with the PR that made them
    ├── index.md            # ID | title — the ONLY file a future agent scans
    └── NNNN-<slug>.md      # Extracted by /work-on after the build.
```

`.discovery/` is gitignored; `docs/adr/` is committed. `/plan` writes a
dossier. `/work-on` builds it and extracts the ADRs. `/open-work` reads
dossier front matter and renders status.

**Both files are kept. They answer different questions.** A dossier answers
"what did we build, and what happened while we built it" — it stays as the build
record, and `/open-work` mines its `## Build log` for pipeline health signals
long after the work ships. An ADR answers "why is the code like this", which is
the question a future agent asks constantly and cannot answer from a build
record.

The difference is **audience, not lifetime**: a dossier is read by whoever asks
about *this* work item, and only ever by ID — so it stays local. An ADR is read
by every future agent, through `docs/adr/index.md`, without knowing it exists —
so it is committed and travels with every clone. That is why the ADR
format is strict about titles and consequences, and why extraction is selective:
the index is a scan surface, and a near-duplicate ADR costs every future reader.

---

# 1. The dossier — `.discovery/dossiers/<ID>-<slug>.md`

`<ID>` is `W-NNN`. `<slug>` is kebab-case, 6 words maximum, never renamed.

## Front matter — the machine state, first bytes of the file

`/open-work` reads **only** this block. Keep it parseable and keep it current;
every field is a fact, so a command may rewrite it without touching the prose.

```yaml
---
id: W-014
title: Coalesce concurrent flush calls in FlushQueue
status: ready          # planned | ready | building | review | pr | done | dropped
created: 2026-08-10
updated: 2026-08-10
anchors:               # path:line evidence the plan depends on
  - src/flush.py:41
baseline_commit: 3f2611f   # HEAD when the anchors were last checked
jira: PROJ-142         # null when no ticket exists
branch: fix/PROJ-142-flush-coalescing
worktree: ../repo-W-014
pr: null
blocked_by: []         # dossier IDs that must reach `done` first
adrs: []               # ADR IDs this build produced; /work-on fills it
---
```

Status transitions, and the only writer of each:

| Status | Meaning | Written by |
|---|---|---|
| `planned` | The plan exists. The plan review has not passed. | `/plan`; `/work-on`, when it returns a dossier mid-build |
| `ready` | The plan review passed. Buildable. | `/plan` |
| `building` | Agents are working in worktrees. | `/work-on` |
| `review` | Tests are green. The review cycle is running. | `/work-on` |
| `pr` | The branch is pushed. The run waits for the PR URL from the user. | `/work-on` |
| `done` | The PR URL is recorded and the worktree is removed. Terminal. | `/work-on` |
| `dropped` | Abandoned. The reason is in `## Build log`. | either |

`done` means **this build is finished**, not that the PR merged. Whether the PR
merges, and when, is the user's business and the pipeline does not track it.
Review comments on the PR are new work: they get their own worktree, through a
new `/work-on` run on the same dossier or a new dossier, never by reopening the
one that produced the PR.

## Body — fixed headings, fixed order

An agent seeks to a heading instead of reading the file. Emit the headings
verbatim. Do not add sections. A `## ` inside a code fence is quoted text, not
a section — the validator reads it that way — but the rule above still holds:
whole documents never land in the dossier, only their outcomes as lines.

| # | Heading | Content | Writer |
|---|---|---|---|
| 1 | `## Problem` | What is wrong or missing, with evidence labels. | `/plan` |
| 2 | `## Approach` | One sentence, then the reasons. Then the rejected options. | `/plan` |
| 3 | `## Contract` | Signatures and documentation comments. No bodies. | `/plan`, then `/work-on` until the fan-out |
| 4 | `## Work packages` | Table: package, owned paths, depends on. | `/plan`, then `/work-on` until the fan-out |
| 5 | `## Acceptance criteria` | Numbered. Each one falsifiable. | `/plan` |
| 6 | `## Build log` | Append-only: spawns, merges, arbitrations, rounds, SHAs. | `/work-on` |

### Who owns sections 3 and 4, and when

`## Contract` and `## Work packages` are the fan-out's two inputs, so they share
one lifecycle:

1. **`/plan` writes both.** The plan reviewer checks the contract for
   buildability and the packages for path disjointness, so both must exist
   before the review.
2. **`/work-on` may revise both in Phase 2**, while it materialises the contract
   as real files. Writing the contract as code exposes what prose hides — a
   missing type, an unstated error, a path that turns out to belong to another
   package. Fix it in the files and in the dossier, and note the change in
   `## Build log`.
3. **Both freeze at the fan-out.** After Phase 4 spawns anything, a change to
   either costs a re-spawn of every agent that read it. From that point a
   contract change arrives only as a `CONTRACT-CHANGE:` ruling or a `GAP:` fix,
   and a package change only as a merge-conflict post-mortem.

No agent ever writes either section. An implementer that cannot satisfy a
signature returns `CONTRACT-CHANGE:` and stops.

### `## Contract` — the load-bearing section

The contract is the reason this pipeline runs three agents concurrently and
blind. The unit test author, the integration test author, and the implementer
all build against these signatures and these documentation comments, so none of
them needs to see the others.

- Signatures and documentation comments only. **No bodies** — a body is the
  implementer's work.
- Documentation uses the language's own form: XML doc comments for C#, rustdoc
  for Rust, docstrings for Python, TSDoc for TypeScript, javadoc for Java.
  Never a bare `//` where the language has a documentation form.
- Each documented member states its promise in observable terms: what it
  returns, what it raises, what it guarantees about order, and what it does with
  empty or invalid input. **A promise no test can observe is not a promise** —
  delete it or make it observable.
- **The contract is the referee.** When a test and an implementation disagree,
  the contract decides which one is wrong (`commands/work-on.md`, the
  arbitration rule). So an ambiguous documentation comment makes the
  orchestrator the wrong party, not the agent that read it.
- Only the orchestrator writes this section. An implementer that cannot satisfy
  a signature returns `CONTRACT-CHANGE:` and stops.

#### The observability checklist

Every failure this pipeline can suffer that has no diagnosable cause starts with
a promise a test could not observe. Run each documented member past these before
the fan-out:

| Ask | A failure looks like |
|---|---|
| Does the return value say what it **means**? | `"drains the queue"` with a return type and no stated meaning — two test authors guess differently |
| Is every error named, with its trigger? | `"raises on bad input"` — which error, and what is bad? |
| Is order stated when order exists? | `"returns the items"` — in what order? |
| Is the empty case stated? | zero items: empty collection, null, or an error? |
| Is the invalid case stated? | negative size, absent key, closed handle |
| Are concurrency words defined? | `"coalesces"` — what does the second caller receive? |
| Is the member's **visibility** stated? | `header_of` named as the read seam with no `pub` — the blind author cannot even compile against the contract |
| Are the words in the docstring measurable? | `"efficiently"`, `"properly"`, `"safely"` are never promises — delete them |

A promise that survives this list is one a blind test author can turn into an
assertion. A promise that does not is a row-3 arbitration waiting to happen
(`work-on.md` Phase 6), and a recurring one becomes a ratified rule in
a rule in this repo's own rules file (Phase 8b).

**Keep this pass's output — it does not end at a checked box.** One line per
member per category that survives becomes `PROMISE_CHECKLIST` (`work-on.md`
Phase 3): the list `unit-test-author` covers, and the list Phase 5 diffs its
report against. Running the checklist and discarding the result means the
unit author re-derives it by hand from the docstrings, and Phase 5 re-derives
it again to check — two more freehand passes over the same six categories,
each free to land on a different answer than this one.

**Each line carries its strong form — the concrete assertion shape, not the
promise's topic.** A checklist line that names a category without naming the
assertion still leaves the author free to write a weak oracle: `returns the
count of items written` invites `assert result.is_some()` as easily as it
invites the real check. So every line states the assertion a test makes of
it, with the concrete value stated wherever the docstring states a value:

```
flush — return meaning: returns the count written → with 3 items queued and
        batch_size=10, assert the return value equals 3 (never is_some)
flush — order: oldest first → queue items A, B, C; assert the store received
        them in exactly [A, B, C]
flush — empty case: returns 0 → assert the return value equals 0 and the
        store received nothing
```

A line whose strong form cannot be written is a promise no test can observe,
and the observability checklist should have deleted it one step earlier.

### `## Work packages` — path ownership is assigned, never discovered

```markdown
| Package | Owned paths | Depends on |
|---|---|---|
| P1 flush coalescing | src/flush.py | — |
| P2 reload guard | src/reload.py | P1 |
| UT unit tests | tests/unit/test_flush.py | — |
| IT integration tests | tests/integration/test_flush_flow.py | — |
```

Owned path sets are **disjoint across every row**, including the test rows.
Concurrency is safe only because of that disjointness. Two rows that want the
same file are one row, or they are sequenced with `Depends on`.
`scripts/validate_pipeline.py` checks this mechanically before any fan-out.

### `## Acceptance criteria` — falsifiable or absent

Each criterion names the observation that would prove it false. `The flush is
efficient` is not a criterion. `With 3 parallel FlushAsync calls and 5 queued
items, the store receives each item one time` is a criterion.

**Every criterion ends with an owner and an environment**, in this shape:

```
1. With 3 parallel `flush(batch_size=10)` calls and 5 queued items, the store
   receives each item one time. (owner: tests/integration/test_flush_flow.py;
   env: local)
```

`owner` is the test file that will prove the criterion — a `## Work packages`
test row — and `env` is where that test runs: `local`, a VM or container
name, or `ci`. The validator rejects a criterion with no annotation, so a
criterion nobody owns cannot reach the fan-out. `/plan` writes the annotation
from its own package table; `/work-on` Phase 3 re-checks it against the real
split, and a criterion whose `env` names services the machine cannot provide
is a `/plan`-shaped defect — fix it before anything spawns, not after a red
suite that cannot even run.

A criterion this machine cannot observe carries `UNVERIFIABLE-LOCALLY` plus
the agreed substitute in the same line — the command that observes it
elsewhere, or the deploy-check it defers to. The validator rejects the marker
without a substitute.

### `## Build log`

Append-only. One line per event, newest last. Record: each spawn and its
outcome, each worktree merge, each test-versus-implementation arbitration and
its ruling, each `CONTRACT-CHANGE:` decision, each review round and its change
requests, the deferred-issues ledger `/work-on` Phase 9 captures at close
(one `DEFERRED:` header plus one line per untackled issue, `DEFERRED: none`
allowed), and anything that surprised the orchestrator. This section replaces
the changelog, the review files, and the anomaly log.

**One line per event means no embedded documents.** A reviewer reply lands as
a ledger — one line per change request:

```
- r1 style: CR-style-1 <one-line requirement> — <path:line> — accepted
- r1 perf: CR-perf-1 <one-line requirement> — <path:line> — demoted (no input scale stated)
- r2 style: CR-style-1 resolved
```

The full reviewer document travels only in the fix implementer's spawn
payload. A short quoted excerpt, when one is needed, goes inside a code fence
— fenced content is never a section.

---

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

The exclusive-create guard works per working copy. ADRs are committed, so two
concurrent branches can still mint the same ADR number and collide at merge
time. The rule: the branch that merges second renumbers its ADR, regenerates
the index with `--write-index`, and updates its dossier's `adrs` field. Never
merge two decisions under one number.
