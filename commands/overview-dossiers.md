---
description: >-
  The status of every dossier, in chat. Reads the YAML front matter of
  .discovery/dossiers/*.md and reports what is ready to build, what is in
  flight, what is blocked, and what is waiting on a merge — plus the pipeline
  health signals worth acting on. Read-only toward pipeline state: it spawns
  nothing and never writes a dossier; all it produces is the generated HTML
  overview, which the pipeline dashboard embeds and keeps current.
argument-hint: "[dossier ID for one-dossier detail, or empty for everything]"
---

# /overview-dossiers — what is going on

Read-only toward pipeline state. No spawns; never write a dossier, an ADR, or
`state`. You read front matter and report in chat, then regenerate the HTML
overview (the one file this command writes — see "The HTML overview" below).

`${PLUGIN_ROOT}` is this plugin's checkout — the directory holding
`primary-agents/` and `references/`. Under Claude Code it is the absolute path
already substituted into `${CLAUDE_PLUGIN_ROOT}`; take it from there. Resolve
it once and expand it in every path below; a payload always carries the
expanded absolute path, never the variable.

Read `${PLUGIN_ROOT}/references/formats/dossier.md` for the status lifecycle if
you need it.

## What to read

**Front matter only, for the overview.** Every `.discovery/dossiers/*.md`
carries its whole machine state in its YAML front matter — `id`, `title`,
`status`, `updated`, `jira`, `branch`, `worktree`, `pr`, `blocked_by`, `adrs`.
Do not read the bodies to build the table; reading six sections of prose per
dossier to render one row is exactly the waste this format exists to avoid.

Read a `## Build log` only when you need a health signal (below) or when
`$ARGUMENTS` asks for one dossier.

**Mode first.** `python3 ${PLUGIN_ROOT}/scripts/validate_pipeline.py --mode`
in this checkout (`formats/README.md` § "Two modes for `.discovery/`"); report a
`conflict` as a discrepancy and stop. In committed mode this
checkout's dossiers show what has **merged**: a `ready` here may be building
in `../<repo>-<ID>`, and a plan under review lives in `../<repo>-plan-<KEY>`
with no copy here at all. So run `git worktree list` and read the front
matter of each such worktree's copy as the live state for that ID — say per
row which copy you read. Two files here sharing one ID is a plan collision
that landed; report it as a discrepancy. Pass `--worktrees` to the HTML
generator in committed mode, so the page shows the same live copies: an
overlaid row carries a `live from` flag.

## `$ARGUMENTS` empty — the overview

Report these, and omit any section that is empty rather than printing a header
with nothing under it:

1. **Ready to build** — `status: ready` with every `blocked_by` entry `done`.
   One line each: `W-014 — <title> — <jira>`. Name the first one as the
   suggested next `/work-on`.
2. **In flight** — `status: building` or `review`. One line each with the branch
   and how long since `updated`. Flag anything untouched for more than 24 hours
   as possibly abandoned.
3. **Waiting for a PR URL** — `status: pr`. The branch is pushed and the run is
   waiting for the user to hand back the PR link. One line each with the branch
   and the worktree path, and say that `/work-on <ID>` resumes from there to
   record the URL and remove the worktree. Whether the PR merged is not tracked
   here and is not this pipeline's business.
4. **Blocked** — `status: ready` with a `blocked_by` entry that is not `done`.
   Name the blocker and its status.
5. **Not planned yet** — `status: planned`. These need `/plan` to finish; the
   plan review has not passed.
6. **Counts** — one line: total, and the count per status.
7. **Stale worktrees** — a `worktree` path recorded on a dossier that is `done`
   or `dropped`, or a path that no longer exists on a dossier that is
   `building`. Both mean the recorded state and the disk disagree.

## Health signals — the part worth acting on (chat only)

A status table says what is happening. These say whether the pipeline is
working. Read `## Build log` for the dossiers that reached `review` or later,
and report only the signals that actually fired:

- **Ambiguous-contract arbitrations.** Phase 6 of `/work-on` records which of
  four rulings applied to each test failure. Repeated *"the contract was
  ambiguous, so the orchestrator was wrong"* rulings mean `/plan` is shipping
  contracts that are not observable enough — the fix is a sharper `## Contract`,
  not more review. Report the count per dossier when it is 2 or more.
- **Merge conflicts between packages.** Owned paths are supposed to be disjoint,
  so a logged conflict means a package table was wrong. Report every one; each
  is a concrete lesson for the next `/plan`.
- **`CONTRACT-CHANGE:` rulings.** A high count means the contract was designed
  without enough code reading. Report the count and which packages raised them.
- **Review rounds spent.** A dossier that used its whole budget of 2 fix rounds
  is worth naming; the round history says which lens is finding the most.
- **Per-lens CR yield.** Once 5 or more dossiers carry review rounds, count per
  lens: CRs filed, CRs demoted for missing evidence, CRs resolved and kept.
  A lens whose kept-rate is low across dossiers is a candidate to demote to
  notes-only — that decision is the user's, so bring the numbers, not the
  verdict.
- **Vacuous tests caught by the stub red-run.** Phase 5 logs them. A repeat
  offender pattern (same promise shape guessed wrong) is a contract-craft
  lesson for the repo's rules file, from the cheap side.
- **`GAP:` returns.** These are contract defects caught by a test author. A
  pattern across dossiers points at one weak part of the contract format.
- **Graduation lessons awaiting a yes.** `/work-on` Phase 8b records
  `GRADUATION:` lines and closes each one by writing a suffix onto the line
  itself: `— issue <owner>/<repo>#<n>`, `— absorbed: <commit>`, or
  `— declined: <one line>`. That suffix is the whole membership test — report
  every `GRADUATION:` line carrying none of the three, and never try to decide
  whether an issue exists: you cannot see the plugin's issue list from here,
  so text-matching `GRADUATION:` reports lessons that were closed months ago
  and goes on reporting them forever. Each line you do report is a finished
  lesson parked on a decision, and the overview is where the user remembers to
  give it. **Give the counts both ways** — `N awaiting, M closed` — so
  `0 awaiting, 0 closed` (no graduations), `0 awaiting, 12 closed` (the loop
  is closing) and `9 awaiting, 0 closed` (the write-back step is not
  happening) are three different readings rather than one.
- **Reviews that hit the budget without resolving.** Name them — they are
  waiting on a user ruling, and nothing else will move them.

Keep this section to what fired. A clean pipeline reports one line: no signals.
These live in chat only; the HTML page does not carry them.

## The HTML overview

After the chat report (overview runs only, not the detail view), regenerate the
page:

```bash
python3 "${PLUGIN_ROOT}/scripts/generate_open_work.py" \
  --root .discovery [--worktrees]
```

`--worktrees` in committed mode only. Tell the user the output path it printed
(`.discovery/analysis/open-work.html`); pass `--open` only when asked.

The generator is deterministic and reads only front matter — the same seven
sections as above, plus the dependency graph, with no attention lines and no
health signals. Its template is `references/templates/open-work.html`;
regenerate, never hand-edit. The graph's **Simplify** checkbox hides done and
dropped history except prerequisites of unfinished work; it never changes the
recorded `blocked_by` fields.

**The live page belongs to the dashboard.** The page does not refresh itself.
The pipeline dashboard at http://127.0.0.1:7317 (`references/owner-questions.md`)
embeds it and re-runs the generator on every change, so point a user who wants
a live view there. Suggest nothing else; there are no hooks to install. A user
who ran an older `--install` can remove those hooks with
`python3 "${PLUGIN_ROOT}/scripts/generate_open_work.py" --root .discovery --uninstall`.

## `$ARGUMENTS` names a dossier — the detail view

For one dossier, report: the front matter as a compact block; `## Problem` and
`## Approach` in one sentence each; the work packages with their owned paths and
which have merged; the acceptance criteria with what is known about each; the
`## Build log` condensed to its rounds, arbitrations, and rulings; any `UNKNOWN`
still standing; the ADRs it produced; and the concrete next action — `/work-on
W-NNN` to build, to resume, or to close out, or `/plan W-NNN` if it is still
`planned`.

## Invariants

- Read-only toward pipeline state. Never write a dossier, never write an ADR,
  never touch `state`, never spawn an agent, never run a validator. The only
  file this command produces is the HTML overview under
  `.discovery/analysis/`. In committed mode
  `.discovery/analysis/` must be gitignored: when
  `git check-ignore -q .discovery/analysis/open-work.html` fails, say so —
  the next `/plan` run adds the pattern — and never stage the report.
- Front matter for the table; bodies only for chat health signals and the detail view.
- Report what the files say. A `status` that disagrees with the disk — a missing
  worktree, a merged PR still marked `pr` — is reported as a discrepancy for the
  user to resolve, and never quietly corrected here. `/work-on` fixes state;
  `/overview-dossiers` observes it.
- End with one suggested next action, not a menu.
