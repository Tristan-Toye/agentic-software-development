---
description: >-
  Fix a finished dossier's deferred issues in one stacked pull request, all of
  them or the D-items the owner selected. Every item that is small and stays
  inside one package is implemented directly, test first, through the
  implementer and reviewer flow, with no new dossier. An item that crosses a
  contract boundary, or is too large or too uncertain for a direct fix, is put
  to the owner with the argument for a dossier of its own; the owner rules.
  Started from the dashboard's Deferred tab ("Fix selected", "Fix all
  deferred") or `run_dossier.py <ID> --command fix-deferred [--items D-1,D-3]`.
argument-hint: "<dossier ID, e.g. W-117> [D-1,D-3,...]"
---

# /fix-deferred — fix what the run deferred, in one stacked PR

`$ARGUMENTS` is the dossier ID, optionally followed by a comma-separated list
of D-ids: the items the owner selected. With a list, only those items are in
scope; every other item stays as it is. Refuse when it names no dossier, when
the dossier's `## Build log` holds no `DEFERRED:` ledger, or when a listed
D-id is not in the ledger: there is nothing to fix.

The dossier's own copy is on its build branch once `/work-on` removed the
worktree (`git show <branch>:.discovery/dossiers/<file>`); the checked-out
branch has the pre-build copy until the pull request merges.
`${PLUGIN_ROOT}` is this plugin's checkout; under Claude Code take it from
`${CLAUDE_PLUGIN_ROOT}`. The repository's own rules file and `AGENTS.md`
outrank this file's defaults.

Progress posts itself (the plugin's hook). Ask the owner only through
`scripts/ask_owner.py`, as `references/owner-questions.md` says, and never
pick an option yourself. Call `phase_checkpoint` at each step below, named
`Fix-deferred — <step>`.

## Step 1 — The items

Read the ledger: the `DEFERRED:` block and every later `D-` line in
`## Build log` (`dossier_edit.py section <dossier> 'Build log'`), plus the
`/deferred` report when the runner stored one at
`~/.agentic/runs/<repo>.<ID>.deferred.md`. The report's fields (contract
boundary, blast radius, confidence, effort) are what the triage rules by;
for an item the report lacks, start from the ledger line's own `contract:`
and `effort:`, read its `path:line`, and fill the rest yourself, briefly. With a list, read only the listed items.

## Step 2 — Triage: direct fix or new dossier

An item is a **direct fix** only when all of these hold:

- **Contract boundary** is `none`, or `internal` within one work package's
  owned paths: no public signature, documented promise, route, schema,
  event, config key or CLI changes.
- **Effort** is `S` or `M`.
- **Confidence** is `verified`, or the one check that settles it is cheap and
  you run it now.
- The fix can be proved: a focused test fails before it and passes after.

An item that **no longer applies** (the code it names is gone, or it was
already fixed elsewhere; verify at its `path:line`) is closed, not fixed:
log `DEFERRED-DROPPED: D-n — <why it no longer applies>`.

Every other item **may deserve a dossier**. Do not decide it alone: put it
to the owner, one `ask_owner.py ask` per item, asked together before Step 3.
The question argues the case in plain sentences: what the item is, why it is
too big or too risky for this stacked PR (the contract it crosses, its blast
radius, its effort, what is still unknown), and what a dossier of its own
would settle first. The options are:

- `A` — its own dossier (recommend it when a contract changes or the effort
  is `L`): log `DEFERRED-TRIAGE: D-n → dossier — <reason>; seed: /plan <ID> D-n`;
- `B` — fix it in this stacked PR anyway: it joins the direct items, and the
  pull request body names the contract it changes;
- `C` — leave it open for now: no log line, the item stays `open`.

Go on with the direct items while the questions are open; under the runner,
end the turn with `RUNNER: WAITING <ids>` only when nothing else can proceed.
Log each direct item as `DEFERRED-TRIAGE: D-n → direct — <one-line fix>`.

## Step 3 — The stacked branch

Stack on the dossier's own work:

- Its pull request is open: branch from its head branch, and the new pull
  request's base is that branch.
- Its pull request is merged: branch from the branch it merged into (the
  stub), and the base is that branch.

The branch is `fix/<ID>-deferred`, in a worktree `../<repo>-<ID>-deferred`
(Phase 2's worktree rules, `.worktree-setup.sh` included). Log
`DEFERRED-FIX: branch fix/<ID>-deferred on <base> @ <sha>`.

## Step 4 — Fix, test first

Write the direct items as one change-request document, one `## CR-n` section
per item: `Location` is its `path:line`, `Observed` and `Required` come from
the report, `Rule` is the item's kind, `Severity` is `should-fix`, and
`Siblings` lists the other sites the report names. Then reuse the Phase 7
fix machinery exactly:

1. `check_crs.py` on the document;
2. `compose_payloads.py --crs` routes each item to the package owning its
   path (rule on `UNROUTED` lines only);
3. one `phase4-wave` workflow call runs the fix implementers.

Each implementer first writes a focused test that fails on the defect, then
fixes it, so every item ends with its own proving test.

Run the gates and the affected suites through `run_gates.py` and
`run_tests.py`, as Phase 6 does. A suite that stays red after one fix round
for an item turns that item into a dossier seed: revert its commits with
`safe_revert.py`, log `DEFERRED-TRIAGE: D-n → dossier — fix did not hold`,
and go on with the rest.

## Step 5 — Review

Run one review round with the `style` and `architecture` lenses through
`phase7-review`, scoped to this branch's diff. A blocking change request goes
back through Step 4 once. One still open after that is reported in the pull
request, never hidden.

## Step 6 — The pull request

Push the branch and open the pull request against the base from Step 3. The
title is `fix(<ID>): deferred issues D-…`. The body has:

- one row per item: D-ID, kind, title, `fixed (<sha>)` or
  `→ dossier (/plan <ID> D-n)`;
- each fixed item's proving test;
- the gates and suite summary.

Log `DEFERRED-FIX: PR <url> — fixed <n>, to dossier <m>`, and append
`DEFERRED-FIXED: D-n <sha>` for each fixed item, so the dossier records
which items are closed. Remove the worktree once the pull request URL is in
hand. Under the runner, end with `RUNNER: DONE`.

## Rules

- Never widen a fix past its item. What you notice on the way becomes a new
  `NOTICED:` line, not a change.
- Never change a contract to make a fix fit, unless the owner ruled `B` for
  that item; then the pull request names the change.
- Never fix an item outside the owner's list when a list was given.
- The dossier's own pull request is never edited, rebased or merged by this
  command. The stacked pull request is the only thing it opens, and the
  owner merges it.
