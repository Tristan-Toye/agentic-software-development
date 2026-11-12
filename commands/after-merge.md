---
description: >-
  Cleanup after the owner merged a dossier's pull request into its stub:
  settle the dossier's record, clean the stale local branches and worktrees,
  update still-open PRs against the stub where mechanical. State work only:
  never merges, never edits a merged PR, resolves a conflict itself and asks only on a product-level one.
  Started by the dashboard's merge detector.
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

Ask the owner only through `scripts/ask_owner.py`, under
`references/owner-questions.md` "The ask policy": design, product or real
uncertainty only, each ask passing `--kind`. Post one short note per
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
- **Conflicting, and the stub is being integrated** (`gh pr list --head <stub>
  --base development --state open` lists a pull request): do nothing and ask
  nothing. The merge queue reaches that pull request itself and resolves it
  then; a refresh now would go stale at the next merge. Say so in the note.
- **Conflicting, otherwise**: refresh it here without asking (a fresh worktree on
  its branch, `git merge origin/<stub>`, resolve keeping both sides' intent, the
  repository's gates on the touched paths, push; Phase 9's refresh ritual with
  `validate_pipeline.py --finalize-ids` where the repository has ADRs). Never
  rebase, never force-push. A merge conflict is resolved, not asked about; only
  when the two intents conflict at product level, ask the owner, one
  `ask_owner.py ask --kind product` per pull request, naming the files in
  conflict, and leave it for its own run. When the gates stay red after one
  honest attempt, abort the merge, leave the branch as it was and name it in
  the note: the merge queue's `/resolve-merge` reaches it when its dossier is
  merged. End the turn with `RUNNER: WAITING <ids>` when nothing else is left.

## Step 5 — Close

One note: what merged, what was cleaned, which pull requests changed or were left. The dashboard regenerates the open-work report itself
once the refs move. Under the runner, end with `RUNNER: DONE`.

## Rules

- Never merge, close or approve a pull request, and never push to the stub.
- Never change code except in a refresh, and then only to resolve the conflict.
- Never delete a branch that is not merged, a dirty worktree, or anything on
  the remote.
