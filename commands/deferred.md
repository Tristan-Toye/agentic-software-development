---
description: >-
  The deferred-issues report for a finished change: every product-code issue
  the session is already aware of but did not tackle — bugs, latent bugs,
  architecture, performance, duplication, multiple sources of truth — each
  with an ID, evidence, a proposed fix, the contract boundary it crosses, and
  its blast radius. A recall report, not an audit: no new research happens
  here, so it stays cheap at the end of every run.
argument-hint: "[optional dossier ID or branch/PR ref; empty = this session's changeset]"
---

# /deferred — what you noticed but did not tackle

Produce the **deferred-issues report** for the work this session just finished,
or for the changeset `$ARGUMENTS` names. This is a **recall report, not an
audit**: report only what you are already aware of. You may briefly verify what
you already suspect — open the one file, confirm the line, re-read the one
hunk. You may not go looking for new issues. Deeper research is the user's
call, triggered from this report by ID.

## Where your awareness lives

Harvest these sources, then merge duplicates:

1. Your own residue — everything you noticed while working and did not act on.
2. If a dossier covers the run, its `## Build log`: every `NOTICED:` line,
   every `SHARED_IDIOM` collapse that never landed, every change request
   demoted for missing evidence, every arbitration whose evidence pointed at
   product code.
3. Sub-agent reports you received — declined suggestions, open questions.

Name the sources you harvested in the header, so a "none" can be defended.

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

- IDs are `D-1, D-2, …`, ordered by risk, unique within the report. The user
  references them by ID afterwards. Never renumber inside one conversation.
- Every issue carries `path:line` evidence. No evidence means Confidence is
  `suspected` — never that the issue is dropped.
- Never omit a field. `none` and `unknown` are answers; silence is not.
- Zero issues is a real result: state the sources harvested, print `**None.**`,
  and do not pad or invent.
- You fix nothing here. Report only.
