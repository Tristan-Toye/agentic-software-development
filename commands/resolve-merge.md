---
description: >-
  Bring a dossier's pull requests up to date with the stub they merge into
  when they conflict: merge the base into each conflicting branch, resolve
  every conflict keeping both sides' intent, prove it with the affected tests
  and gates, push. Never merges into the stub, rebases or force-pushes.
  Started by the dashboard's merge queue.
argument-hint: "<dossier ID, e.g. W-128> <PR numbers, bottom to top, e.g. 427,429>"
---

# /resolve-merge — resolve the conflicts that stop the merge queue

`$ARGUMENTS` is the dossier ID and its pull requests, bottom to top (the
dossier's own pull request first, then the stacked ones on it), or one plan or
research pull request into development (its dossier's ID, or `PR-<n>` when it
holds none). The merge queue found at least one of them **conflicting** with
its base, with a **migration number** another migration already has, or with a
**check still red** after one re-run of its failed jobs;
when your run ends and the pull requests still fail, the queue starts another
run, up to three per attempt, so read the failure as it stands now.
It already renumbered every id the base holds, by script; what reaches you is
what a script cannot settle. Refuse when a
listed pull request is not open (`gh pr view <n> --json state,headRefName,baseRefName,mergeable`).

`${PLUGIN_ROOT}` is this plugin's checkout; under Claude Code take it from
`${CLAUDE_PLUGIN_ROOT}`. The repository's own rules file and `AGENTS.md`
outrank this file's defaults. Ask the owner only through
`scripts/ask_owner.py`, under `references/owner-questions.md` "The ask policy":
resolve conflicts yourself, and ask only for design, product or real
uncertainty, each ask passing `--kind`. Call `phase_checkpoint` at each step, named
`Resolve-merge — Step <n> — <title>`.

## Step 1 — The branches

`git fetch origin --prune`. For each pull request, bottom first: its head
branch, its base, and whether it conflicts. Work in one fresh worktree per
head branch, `../<repo>-<ID>-merge`, made from `origin/<head>`
(`.worktree-setup.sh` when the repository has one). Never reuse a worktree
that holds uncommitted work.

## Step 2 — Merge the base in, keep both sides

For the bottom pull request: `git merge --no-ff origin/<base>` (the stub).
For each one above it: merge its updated base (the head below it) the same
way, after that one is pushed. Then resolve each conflict:

- Read both sides and the merge base for every conflicted hunk
  (`git diff --base`, `git show :1:<path> :2:<path> :3:<path>`). The result
  keeps what each side meant: a change that only one side made survives as it
  is; two changes to the same lines are combined so both still hold.
- Generated or derived files (indexes, lock files, snapshots, ADR and dossier
  numbering) are regenerated with the repository's own tool, never
  hand-merged: `python3 ${PLUGIN_ROOT}/scripts/validate_pipeline.py
  --finalize-ids --base origin/<base>` renumbers ids that collide with ones the
  base took first.
- **Check that nothing was lost**, mechanically, before committing: every
  hunk the branch added over the merge base, and every hunk the base added,
  still appears in the result (`git diff <merge-base> origin/<base>` and
  `git diff <merge-base> <head before merge>` against the merged tree). Name
  each hunk that did not survive and why in the merge commit body; a hunk
  dropped without a reason is a defect, not a resolution.
- When two intents cannot both hold (the same behaviour changed two ways), do
  not choose: ask the owner (`--kind product`) with both versions and what each one does, and
  wait. Every other conflict you resolve yourself.

## Step 2a — A migration number already taken

Run `python3 ${PLUGIN_ROOT}/scripts/merge_dossier.py --migration-clashes <top
head> --base <stub> --root <checkout>`. For each migration it names, this stack
takes the next number free on the stub: rename the file, update whatever names
it, and never touch a migration the stub already holds. Then run Step 3.

## Step 2b — A red check, no conflict

Read why it failed (`gh pr checks <n>`, `gh run view <run> --log-failed`).
Fix it on the branch, inside what the pull request changed: a spelling or
link a docs check names, a validator's finding on a plan document, a test the
change broke. A failure the change did not cause (an outage, a flaky job that
failed twice) is put to the owner with the evidence, never patched around.

## Step 3 — Prove it

Run the gates on the touched paths (`run_gates.py`) and the tests of the
packages the conflicts touched, plus the dossier's own new tests
(`run_tests.py`; `--vm` where the repository needs it). A red result is fixed
in this merge, inside the conflicted code only, or put to the owner.

## Step 4 — Push

Commit the merge (`git commit --no-edit`, the hunks note in the body when
needed) and `git push` (never `--force`). Check each pull request reads
`MERGEABLE` (`gh pr view <n> --json mergeable`; GitHub may need a minute).
Remove the worktrees. Post one note: which pull requests conflicted, in which
files, how each was resolved, and the test and gate results. Under the runner,
end with `RUNNER: DONE`; the merge queue merges next.

## Rules

- Never merge, approve or close a pull request, never push to the stub, never
  rebase, never `--force`.
- Never change code outside the conflicted hunks, except a test or gate fix
  the merge itself made necessary.
- Never resolve by taking one side wholesale (`-X ours`, `-X theirs`,
  `checkout --ours`) unless the check in Step 2 shows the other side's change
  is already in it.
