# 1. The dossier — `.discovery/dossiers/<ID>-<slug>.md`

`<ID>` is `W-NNN`. `<slug>` is kebab-case, 6 words maximum, never renamed.

## Front matter — the machine state, first bytes of the file

`/overview-dossiers` reads **only** this block. Keep it parseable and keep it current;
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
blocked_by: []         # dossier IDs that must reach `done` first; a done
                       # blocker stays (removal needs BLOCKER-REMOVED: in the log)
adrs: []               # ADR IDs this build produced; /work-on fills it
---
```

Status transitions, and the only writer of each:

| Status | Meaning | Written by |
|---|---|---|
| `planned` | The plan exists. The plan review has not passed. | `/plan`; `/work-on`, when it returns a dossier mid-build |
| `ready` | The plan review passed. Buildable. Committed mode: lands when the plan PR merges. | `/plan` |
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
  the contract decides which one is wrong (`primary-agents/work-on.md`, the
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
| Are the trait bounds an assertion needs **derived**? | `expect_err` on a type without `Debug`, `assert_eq!` without `PartialEq`, a second move without `Clone` — the stub compiles, the test does not, and a blind author cannot see the derive set |

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
flush — derives: Result<Written, FlushError> → expect_err needs Written:
        Debug, assert_eq on Written needs PartialEq — the stub derives both
```

**Derive-sets are part of the checklist.** For every line whose strong form
asserts through `expect_err`/`unwrap_err` (`Debug` on the Ok type),
`assert_eq!` or any equality (`PartialEq`, often `Eq`), or moves a value
twice (`Clone`), the line names the bound the assertion requires, and
`/work-on` Phase 3 checks the stub's derive set satisfies it — or the
payload's `FIXTURES` names the house idiom to use instead (`match` on
`Err(e)` where an `Arc<dyn Tool>` field blocks `Debug`). The recorded
`Validated` derived only `Clone`; the integration tests called `expect_err`,
and the file failed to compile at the Phase 5 commit — one corrective resume
after two blind authors had done nothing wrong.

**A line that states the whole result carries its assertion form as a
tag.** `[whole-value]` on a line that states the whole result — "gives
exactly", "gives `[A, B]`", "gives `Ok(vec![])`", a full list — means ONE
equality over the whole value; a length check, a membership test (`any`,
`contains`, `find`) or a per-field check on such a line is a weak oracle
that passes against the correct body and survives the mutant. `[member]` on
a line that states membership only ("holds …") means membership of the
WHOLE element, never of one field of it. The tag rides the line into
`PROMISE_CHECKLIST`, where the unit author asserts by it; `check_payload.py`
warns on an "exactly" line with no tag, and `/work-on` Phase 3's seventh
diff checks every whole-result line carries one. Six recorded mutation
survivors from three blind authors sat on untagged "gives exactly" lines:
three delete-first rewrite rounds and six mutants re-run.

```
flush — order: oldest first → queue A, B, C; assert the store received
        exactly [A, B, C] [whole-value]
flush — return meaning: returns the written ids → assert the result equals
        exactly [id_a, id_b] [whole-value] — never len() == 2 plus any(...)
index — membership: holds every seen id → assert the whole element
        (id, seen_at) is in the index [member] — never the id alone
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

`scripts/validate_pipeline.py --pre-fanout` requires seven lines before the
fan-out: `CONTRACT-REVIEW:`, `HOOKS:`, `PAYLOAD-LINT:`, `ADMISSION:`,
`SUITE-RUNNER:`, `CENSUS:` (a pin the contract shifts, with its owner, or
`CENSUS: none — <what was grepped>`), and `CHECKLIST-IDS:` (the repo's
promise-coverage script's parse count against the draft `PROMISE_CHECKLIST`,
or `CHECKLIST-IDS: none — no coverage script`; a `parsed 0/` line is
refused).

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
