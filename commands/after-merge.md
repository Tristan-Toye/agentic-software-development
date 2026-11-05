---
description: >-
  The cleanup after the owner merged a dossier's pull request into its stub:
  settle the dossier's record, clean the local branches and worktrees the
  merge made stale, and bring the pull requests still open against the stub
  up to date where that is mechanical. State work, not code work: it never
  merges, never edits a merged pull request, and asks before resolving a
  conflict. The dashboard's merge detector starts it under the runner
  (`run_dossier.py <ID> --command after-merge --items <PR numbers>`).
argument-hint: "<dossier ID, e.g. W-128> [PR numbers, e.g. 429,430]"
---

# /after-merge — settle what a merge into the stub changed

`$ARGUMENTS` is the dossier ID, optionally followed by a comma-separated list
of pull request numbers: the merged pull requests the detector saw (the
dossier's own, its stacked `/fix-deferred` one, or both). Without a list, take
every merged pull request whose head branch names the ID. Refuse when a listed
pull request is not merged (`gh pr view <n> --json state,mergedAt,baseRefName,headRefName`):
nothing to settle.

`${PLUGIN_ROOT}` is this plugin's checkout; under Claude Code take it from
`${CLAUDE_PLUGIN_ROOT}`. The repository's own rules file and `AGENTS.md`
outrank this file's defaults. This is Phase 9's post-merge cleanup
(`references/work-on/phase-9.md`), run on the merge instead of on request.

Ask the owner only through `scripts/ask_owner.py`. Post one short note per
step that changes something, in plain sentences.

## Step 1 — Fetch and confirm

`git fetch origin --prune`. For each pull request: its base (the stub), head
branch, merge commit and time. A base that is not a stub (`stub/*`, or a
branch with a `/stub` record file) is still settled, but say so in the note.

## Step 2 — The dossier's record

The dossier's own copy merged with the pull request (committed mode), so the
stub's copy is the record. Read it from the stub
(`git show origin/<stub>:.discovery/dossiers/<file>`):

- `status: done` and `pr:` set: nothing to write.
- A stacked `/fix-deferred` pull request: its `DEFERRED-FIXED:` lines came
  with it; nothing to write.
- A discrepancy (status not `done`, `pr:` missing or another number): never
  write on the stub. Report it in the note; the owner decides.

Local mode (dossiers not committed): settle the front matter in the local
dossier, `status: done`, and log `MERGED: PR <n> into <stub> @ <sha>` with
`dossier_edit.py log`.

## Step 3 — Local cleanup

- The main checkout on the stub and clean: `git pull --ff-only`. On another
  branch, or dirty: leave it, and say so.
- A worktree still on the merged head branch: remove it when it is clean
  (`git worktree remove`), else leave it and name it. Then `git worktree prune`.
- The merged head branch locally: delete it with `git branch -d` (never `-D`)
  when no worktree uses it. Never delete a remote branch.

## Step 4 — The pull requests still open against the stub

List them (`gh pr list --base <stub> --state open --json number,headRefName,baseRefName,mergeable`),
plus any open pull request whose base was the merged head branch (a stacked
one):

- **A stacked pull request on the merged branch**: retarget it to the stub
  (`gh pr edit <n> --base <stub>`), if GitHub has not already.
- **Mergeable**: nothing to do; it is the owner's to merge.
- **Conflicting**: ask the owner, one `ask_owner.py ask` per pull request,
  naming the files in conflict: `A` refresh it here (a fresh worktree on its
  branch, `git merge origin/<stub>`, resolve keeping both sides' intent,
  the repository's gates on the touched paths, push; Phase 9's refresh ritual
  with `validate_pipeline.py --finalize-ids` where the repository has ADRs),
  `B` leave it for its own run. End the turn with `RUNNER: WAITING <ids>`
  when nothing else is left.

## Step 5 — Close

One note: what merged, what was cleaned, which pull requests changed or are
waiting on the owner. The dashboard regenerates the open-work report itself
once the refs move. Under the runner, end with `RUNNER: DONE`.

## Rules

- Never merge, close or approve a pull request, and never push to the stub.
- Never change code except in an `A`-ruled refresh, and then only to resolve
  the conflict.
- Never delete a branch that is not merged, a dirty worktree, or anything on
  the remote.
