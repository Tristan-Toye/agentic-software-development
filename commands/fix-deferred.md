---
description: >-
  Fix a finished dossier's deferred issues (all, or the selected D-items) in
  one stacked pull request. Small single-package items are implemented
  directly, test first, through the implementer and reviewer flow with no new
  dossier; only items that deserve their own dossier, no longer apply or are
  not worth fixing go to the owner, and dossier items get one concurrent /plan
  each. Started from the dashboard's Deferred tab or `run_dossier.py`.
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
`scripts/ask_owner.py`, as `references/owner-questions.md` says ("The ask policy": design, product or
real uncertainty only, each ask passing `--kind`, never an approval), and never
pick an option of a question you asked. A bug or deferred item is fixed through
the normal flow without asking, unless the fix would change product behaviour
or a contract. Call `phase_checkpoint` at each step below, named
`Fix-deferred — Step <n> — <title>` (`Fix-deferred — Step 2 — Triage`): the
dashboard's Fix deferred issues tab groups the run by these numbers.

## Step 1 — The items

Read the ledger: the `DEFERRED:` block and every later `D-` line in
`## Build log` (`dossier_edit.py section <dossier> 'Build log'`), plus the
`/deferred` report when the runner stored one at
`~/.agentic/runs/<repo>.<ID>.deferred.md`. The report's fields (contract
boundary, blast radius, confidence, effort) are what the triage rules by;
for an item the report lacks, start from the ledger line's own `contract:`
and `effort:`, read its `path:line`, and fill the rest yourself, briefly. With a list, read only the listed items.

## Step 2 — Triage: direct fix or new dossier

The default is a **direct fix in the stacked PR**, and it is never asked
about: the owner chose to fix these items. Log each as
`DEFERRED-TRIAGE: D-n → direct — <one-line fix>` and go on.

Ask the owner only about an item you would **not** fix here, one
`ask_owner.py ask` per such item, asked together before Step 3:

- **It deserves its own dossier**: the fix changes an external contract (a
  public signature, documented promise, route, schema, event, config key or
  CLI that other code or people consume), its effort is `L`, or what it
  needs is still too uncertain to prove with one focused test.
- **It no longer applies**: the code it names is gone, or it was already
  fixed elsewhere (verify at its `path:line`).
- **It is not worth fixing**: the cost or the risk of the change outweighs
  what it buys.

The question argues the case in plain sentences: what the item is, and why
you would not fix it in this stacked PR (the contract it crosses, its blast
radius, its effort, what is still unknown, or why it no longer matters). The
options are:

- `A` — your recommendation: its own dossier (log
  `DEFERRED-TRIAGE: D-n → dossier — <reason>; seed: /plan <ID> D-n`), or
  drop it (log `DEFERRED-DROPPED: D-n — <why>`);
- `B` — fix it in this stacked PR anyway: it joins the direct items, and the
  pull request body names any contract it changes;
- `C` — leave it open for now: no log line, the item stays `open`.

Log each question as `DEFERRED-ASKED: D-n <question id>`, and the owner's
ruling once it arrives as `DEFERRED-RULING: D-n — <the ruling in a few
words>` (then the `DEFERRED-TRIAGE:`/`DEFERRED-DROPPED:` line it leads to,
if any), so the dashboard shows where each item stands.

Go on with the direct items while the questions are open; under the runner,
end the turn with `RUNNER: WAITING <ids>` only when nothing else can proceed.

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

Then prove the branch, scoped to what it changed. The dossier's
`SUITE-COMMAND` and `SUITE:` lines say its suite mode:

- **`stub`** (the integrated suite is deferred to `/stub`): run each item's
  proving test and the tests of the packages the fixes touched, through
  `run_tests.py` (`--vm` with the dossier's env script when they need the VM;
  `--detach`/`--wait` for a run over 10 minutes), and the pull request's CI
  through `wait_ci.py` (Step 6). Never run the full workspace or the `--stack`
  suite here: that is `/stub`'s job on the release stub. A service-backed
  test may hold the stack lease for its own run, through `run_tests.py`.
- **`ci`**: the same focused local runs, and CI is the verdict.
- **`local`**: Phase 6's full `SUITE-COMMAND`, through `run_tests.py`.

Run the gates through `run_gates.py`, scoped to the changed paths. Every test
and gate run goes through these two scripts, never a script of your own: their
result lines are how the owner's timeline shows the suite. Those runs are also
the evidence Step 5's reviewers get. A suite that stays red after one fix round
for an item turns that item into a dossier seed: revert its commits with
`safe_revert.py`, log `DEFERRED-TRIAGE: D-n → dossier — fix did not hold`,
and go on with the rest.

## Step 5 — Review

Run one review round with the `style` and `architecture` lenses through
`phase7-review`, scoped to this branch's diff. A blocking change request goes
back through Step 4 once. One still open after that is reported in the pull
request, never hidden.

## Step 6 — The pull request

Push the branch and open the pull request against the base from Step 3 as
**a GitHub stack** with the dossier's own pull request, never as a loose PR:
`gh stack link --open --base <the dossier PR's base> <dossier PR number> fix/<ID>-deferred`
(bottom to top; `--open` marks them ready, because the merge queue refuses a
stack with a draft in it; it creates the PR with the right base and links both into one
stack, or adds it to the dossier's existing stack). Never leave out `--base`:
without it, linking moves the dossier's PR to the repository's default branch,
so a code PR would skip the stub. Then set its title and body with
`gh pr edit`. The owner's Merge button merges the whole stack at once
(`gh stack merge`). The title is `fix(<ID>): deferred issues D-…`. The body has:

- one row per item: D-ID, kind, title, `fixed (<sha>)` or
  `→ dossier (/plan <ID> D-n)`;
- each fixed item's proving test;
- the gates and suite summary.

Log `DEFERRED-FIX: PR <url> — fixed <n>, to dossier <m>`, and append
`DEFERRED-FIXED: D-n <sha>` for each fixed item, so the dossier records
which items are closed. Remove the worktree once the pull request URL is in
hand.

## Step 7 — Plan the items routed to their own dossier

Every item logged `DEFERRED-TRIAGE: D-n → dossier` (the owner ruled it, or a
fix that did not hold) gets its dossier planned now, not left as a seed. One
`/plan` run per item, all at once, each a session of its own under the
runner (`/plan` spawns its own reviewer and scouts, which a sub-agent cannot):

```bash
python3 ${PLUGIN_ROOT}/scripts/run_dossier.py <ID> --root <repo> --command plan --items D-n
```

Each runs `/plan <ID> D-n` in the background under `<repo>.<ID>.plan-D-n`,
mints its own dossier id (atomically, so concurrent planners never collide),
asks the owner through `ask_owner.py`, and writes
`DEFERRED-SOURCE: <ID> D-n` in the new dossier's build log, which links the
item to it on the dashboard. Before starting them:

- Skip an item that already has its dossier: a `DEFERRED-DOSSIER: D-n →`
  line here, or a dossier whose build log names `DEFERRED-SOURCE: <ID> D-n`.
- An item that is the same issue as another dossier's item (its seed names
  `/plan <other> D-m`) is planned once, from that source; log
  `DEFERRED-SAME: D-n = <other> D-m` here, and the dashboard follows the link.
- Log `DEFERRED-PLANNING: D-n → <repo>.<ID>.plan-D-n` for each run started.

The planners are separate runs: this one does not wait for them. Under the
runner, end with `RUNNER: DONE`.

## Rules

- Never widen a fix past its item. What you notice on the way becomes a new
  `NOTICED:` line, not a change.
- Never change an external contract to make a fix fit, unless the owner
  ruled `B` for that item; then the pull request names the change.
- Never fix an item outside the owner's list when a list was given.
- The dossier's own pull request is never edited, rebased or merged by this
  command. The stacked pull request is the only thing it opens, and the
  owner merges it.
