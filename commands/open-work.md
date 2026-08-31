---
description: >-
  The status of every dossier, in chat. Reads the YAML front matter of
  .discovery/dossiers/*.md and reports what is ready to build, what is in
  flight, what is blocked, and what is waiting on a merge — plus the pipeline
  health signals worth acting on. Read-only toward pipeline state: it spawns
  nothing and never writes a dossier; all it produces is the generated HTML
  overview, which can be left running as a live page that follows the pipeline.
argument-hint: "[dossier ID for one-dossier detail, or empty for everything]"
---

# /open-work — what is going on

Read-only toward pipeline state. No spawns; never write a dossier, an ADR, or
`state`. You read front matter and report, then render the same overview as an
HTML file (the one file this command writes — see "The HTML overview" below).

Read `${CLAUDE_PLUGIN_ROOT}/references/formats.md` for the status lifecycle if
you need it.

## What to read

**Front matter only, for the overview.** Every `.discovery/dossiers/*.md`
carries its whole machine state in its YAML front matter — `id`, `title`,
`status`, `updated`, `jira`, `branch`, `worktree`, `pr`, `blocked_by`, `adrs`.
Do not read the bodies to build the table; reading six sections of prose per
dossier to render one row is exactly the waste this format exists to avoid.

Read a `## Build log` only when you need a health signal (below) or when
`$ARGUMENTS` asks for one dossier.

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

## Health signals — the part worth acting on

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
- **Reviews that hit the budget without resolving.** Name them — they are
  waiting on a user ruling, and nothing else will move them.

Keep this section to what fired. A clean pipeline reports one line: no signals.

## The HTML overview — generated on every overview run

After the chat report (overview runs only, not the detail view), render the
same picture as a self-contained HTML dashboard:

1. If health signals fired, write them to a JSON file OUTSIDE `.discovery`
   (a scratch directory) as a list of
   `{"signal": …, "dossier": …, "detail": …, "severity":
   "good|warning|serious|critical"}` objects — the same findings you just
   reported, one object per fired signal.
2. Run the generator:

   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/generate_open_work.py" \
     --root .discovery [--signals <scratch>/signals.json]
   ```

3. Tell the user the output path it printed
   (`.discovery/analysis/open-work.html`) so they can open it in a browser.
   Pass `--open` only when the user asked for the report to be opened.

The generator is deterministic and reads only front matter — the same seven
overview sections defined above, computed by the script from the same fields.
It never reads dossier bodies; the signals JSON is how your Build-log mining
reaches the page. Its template is
`references/templates/open-work.html`; regenerate the report, never hand-edit
it.

Beside the HTML it writes `open-work-state.json`, holding a fingerprint of
everything the page shows apart from the generation timestamp. That file is
what makes a live report possible, and it is the second and last file this
command produces.

Independent dependency chains are drawn as one rectangle each. Two dossiers
that block nothing in common are not one flow, and a single box would imply a
shared left-to-right ordering they do not have.

## The live report — for a user who wants it to keep itself current

`/open-work` is a snapshot: it is accurate when it runs. Two supported ways to
make the page follow the pipeline instead, both user-invoked setup rather than
part of a report run — suggest them, do not install them unasked:

```bash
# a live page for as long as the command runs — Ctrl-C to stop
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/generate_open_work.py" \
  --root .discovery --serve --open

# always on: git hooks plus a Claude Code PostToolUse hook
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/generate_open_work.py" \
  --root .discovery --install
```

`--serve` regenerates on dossier changes and serves the report on localhost;
the page polls its state file and reloads only when the fingerprint changes,
so an unchanged pipeline never disturbs a reader. A report opened as a
`file://` path cannot poll — the browser refuses the request — so it says
`static` in the header rather than pretending to be live. `--install` covers
the always-on case: the Claude Code hook fires the moment `/plan` or
`/work-on` writes a dossier, and the git hooks catch what arrives without a
tool call (a pull, a branch switch, someone else's commit). It writes to
`.claude/settings.local.json`, never the tracked `settings.json`, because the
hook holds absolute paths from one machine. `--uninstall` reverses it.

Both triggers call `.claude/hooks/open-work-report.py`, which `--install`
writes: a launcher that resolves the current plugin install on every run.
Neither may name the plugin's install directory, because Claude Code gives every
marketplace commit its own directory and the generator finds its template
relative to itself — a hook naming one keeps rendering the report through that
commit's template after the plugin has moved on, so the page looks right when
`/open-work` renders it and reverts the next time the hook fires. Re-running
`--install` also repairs a hook an older version pinned that way; if a user
reports the report reverting to an older look, that is the fix.

One honesty constraint to pass on: **health signals do not auto-update.** They
are mined from `## Build log` prose by you, not by the script, so an
auto-regenerated page carries the signals from the last `/open-work` run — or
none. The status table, the counts, the dependency flows and the attention rows
are always current.

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
  files this command produces are the HTML overview under
  `.discovery/analysis/` and its scratch signals JSON.
- Front matter for the table; bodies only for health signals and the detail view.
- Report what the files say. A `status` that disagrees with the disk — a missing
  worktree, a merged PR still marked `pr` — is reported as a discrepancy for the
  user to resolve, and never quietly corrected here. `/work-on` fixes state;
  `/open-work` observes it.
- End with one suggested next action, not a menu.
