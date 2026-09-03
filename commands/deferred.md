---
description: >-
  The deferred-issues report for a finished change: every product-code issue
  the session is already aware of but did not tackle — bugs, latent bugs,
  architecture, performance, duplication, multiple sources of truth — each
  with an ID, evidence, a proposed fix, the contract boundary it crosses, and
  its blast radius. A recall report, not an audit: no new research happens
  here, so it stays cheap at the end of every run. Recall comes first from
  working memory, then from the ledger `/work-on` captured at close, then
  from the build artifacts.
argument-hint: "[optional dossier ID or branch/PR ref; empty = this session's changeset]"
---

# /deferred — what you noticed but did not tackle

Produce the **deferred-issues report** for the work this session just finished,
or for the changeset `$ARGUMENTS` names — a dossier ID or a branch/PR ref;
empty means this session's changeset. If `$ARGUMENTS` is non-empty but names
neither, say so in the header and fall back to this session's changeset;
never guess a changeset out of prose. This is a **recall report, not an
audit**: report only what you are already aware of. You may briefly verify what
you already suspect — open the one file, confirm the line, re-read the one
hunk. You may not go looking for new issues. Deeper research is the user's
call, triggered from this report by ID: `/plan D-3` seeds a new dossier from
the ledger line — its `path:line` becomes the first anchor and its risk the
problem statement.

## Recall first, from working memory — before you touch any file

Answer the question directly, the way you would if the user asked you in
plain words: *are you aware of any issues related to the product code that
you did not tackle?*

Sweep the session's reading, in the order it happened: every file you opened
in full, every hunk you arbitrated, every review reply you held, every
suggestion you declined, every "later" you thought. **Write every item down
before judging any of it** — recall and judgement are separate passes, and an
item filtered out during recall is the important one this report exists to
catch. Only after the list exists do you score it against "What qualifies"
below.

This pass is the report's main source. The artifact harvest below is the
cross-check, not the substitute: a report built from artifacts alone names
only what mechanical agents noticed, and misses everything you saw yourself.

You normally run right after `/work-on` finished, in this same session: your
memory is warm and the capture ledger is minutes old. Treat the two as two
passes over the same awareness — an item one of them dropped is still
reported. The ledger anchors what memory let go; your recall recovers what
the capture pass let go.

## Then harvest the artifacts

Merge these into the recalled list, then deduplicate:

1. **The captured ledger.** If a dossier covers the run — the run's own
   dossier ID, or the `.discovery/dossiers/*.md` whose `branch` matches the
   changeset — read the `DEFERRED:` lines in its `## Build log`. `/work-on`
   Phase 9 captured them at the moment the run's awareness was highest, so
   they outrank your current memory: a ledger item your recall dropped goes
   back in; a recalled item the ledger never caught stays in, and once
   verified you append it to the ledger as its next `D-` line, so the
   dossier keeps the complete set.
2. The dossier's `## Build log` beyond the ledger: every `NOTICED:` line,
   every `SHARED_IDIOM` collapse that never landed, every change request
   demoted for missing evidence, every arbitration whose evidence pointed at
   product code, every accepted `TOUCHED_BEYOND` path.
3. Sub-agent reports you received — declined suggestions, open questions.
4. **The fallback ledger** `.discovery/deferred-ledger.md`, when no dossier
   covers the changeset — a prior report on the same changeset minted its IDs
   there. Keep those numbers.

Name the sources you harvested in the header, so a "none" can be defended.
When the session is cold — no live memory of the changeset, no dossier — say
so in the header instead of padding: a report built from a cold session names
only what the diff itself shows.

## What qualifies

Product code only, in code this session touched or read. One of six kinds:

- `bug` — a defect you verified.
- `latent-bug` — correct only while an outside assumption holds, and nothing
  verifies the assumption. This is the silent-failure risk.
- `architecture` — a cohesion or coupling problem.
- `performance` — a known, avoidable cost. State the input scale that makes
  the cost matter.
- `duplication` — the same logic in two or more places.
- `ssot` — one fact with more than one source of truth.

Test gaps, process problems, and tooling nits do not qualify. If you noticed
one anyway, it goes in the footer — one line, no ID.

## Output — exact structure

1. Header:

   ```
   ## Deferred issues — <changeset ref>
   Scope: <diff range, e.g. a..b>. Sources harvested: <list>. Count: N.
   ```

2. Index table, omitted when the count is zero:

   | ID | Kind | Title | Risk | Origin | Contract | Effort |
   |---|---|---|---|---|---|---|

3. One block per issue, worst risk first:

   ```
   ### D-1 — <imperative title>
   - **Kind** — one of the six.
   - **Issue** — what is wrong. One or two sentences.
   - **Current** — what the code does today, with `path:line` evidence.
   - **Proposed fix** — the concrete change: files, approach. Write
     `unknown — <the analysis needed>` when you genuinely do not know.
   - **Contract boundary** — `none`, or `internal: <seam>` (a signature, a
     module boundary), or `external: <route, schema, event, config, CLI — and
     who the consumers are>`.
   - **Blast radius** — files the fix touches → callers affected → tests to
     update.
   - **Origin** — `pre-existing` | `introduced-by-this-change` |
     `exposed-by-this-change`.
   - **Risk** — `high`, `medium`, or `low`, and one clause saying what breaks
     and how likely it is.
   - **Confidence** — `verified` (you re-read it just now) or
     `suspected — <the one check that would settle it>`.
   - **Effort** — `S` (under an hour), `M` (hours), `L` (days).
   - **Why deferred** — one line.
   ```

4. Footer, omitted when empty:

   ```
   ## Noticed, out of scope
   ```

   One line each. No IDs.

## Rules

- IDs are `D-1, D-2, …` — stable handles, not a sort. Unique across the whole
  conversation, not just this report: IDs the captured ledger already minted
  keep their numbers in every later report, and new items take the next free
  number after every ID minted in this conversation, ledger and fallback
  ledger included. Present the blocks worst-risk-first, but expect
  non-monotonic numbers when a ledger minted them in another order. Never
  renumber inside one conversation — the user references them by ID
  afterwards.
- Every issue carries `path:line` evidence. No evidence means Confidence is
  `suspected` — never that the issue is dropped.
- Never omit a field. `none` and `unknown` are answers; silence is not.
- Zero issues is a real result: state the sources harvested, print `**None.**`,
  and do not pad or invent.
- You change no code and no tracked file. The only writes you perform are
  ledger appends: to the dossier's `## Build log` when a dossier covers the
  run, and otherwise to `.discovery/deferred-ledger.md` — one block per
  changeset (`## <ref> — <date>`, then the `DEFERRED:` lines), created on
  first use, so a dossier-less report persists beyond the session. Report
  only.
