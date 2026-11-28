---
description: >-
  Bring a dossier's pull requests up to date with the stub they merge into
  when they conflict: merge the base into each conflicting branch, resolve
  every conflict keeping both sides' intent, prove it with the affected tests
  and gates, push (the stub's own PR through an auto-merging sync PR). Never
  pushes to the stub, rebases or force-pushes.
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
branch, its base, and whether it conflicts. GitHub's `mergeable` compares each
pull request only with its own base, so a stack can read `MERGEABLE` on every
pull request and still conflict once the base reaches the top: the queue
merges the whole stack. The test that counts is the queue's own, run on the
top of the stack: `git merge-tree --write-tree --name-only origin/<stub>
origin/<top head>`. Only when it exits 0 is there nothing to resolve; otherwise
do Step 2 for every pull request, bottom first, even those GitHub calls
mergeable, and the conflict surfaces in the merge of an updated head into the
one above it. Work in one fresh worktree per
head branch, `../<repo>-<ID>-merge`, made from `origin/<head>`
(`.worktree-setup.sh` when the repository has one). Never reuse a worktree
that holds uncommitted work.

## Step 2 — Merge the base in, keep both sides

For the bottom pull request: `before=$(git rev-parse HEAD)` (Step 4 needs it), then `git merge --no-ff origin/<base>` (the stub).
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
- Architecture diagrams (`docs/architecture/*.json` beside their `.md`): the
  JSON is the source, the Mermaid block is generated. A conflict in a block is
  resolved in the JSON, then `vr_mermaid.py sync docs/architecture`. After the
  merge and after any re-anchor (`check_diagram_anchors.py --fix`, which moves
  `path:NNN` line numbers in the blocks), run `python3
  ${PLUGIN_ROOT}/scripts/vr_mermaid.py adopt-anchors docs/architecture` so the
  JSON takes the new line numbers, and commit what it changed with the merge;
  a `sync` before it would revert the re-anchor. A file it leaves alone holds
  a real edit: fix that JSON, then `sync`. `vr_mermaid.py check
  docs/architecture` passes before you push.
- **Check that nothing was lost**, mechanically, before committing: run
  `python3 ${PLUGIN_ROOT}/scripts/merge_guard.py` in the worktree while the merge is still
  pending. It compares every changed file's lines across base, both sides and
  your result. `lost` (a line both sides kept) is always a defect: restore
  it. `gone` (a line only one side had, no version of it left) needs a
  `Dropped: <path> — <why>` line in the merge message (`.git/MERGE_MSG`);
  `replaced` (another version kept, e.g. renumbered) needs nothing. Do not
  commit until it exits 0; a line dropped without a reason is a defect, not
  a resolution.
- When two intents cannot both hold (the same behaviour changed two ways), do
  not choose: ask the owner (`--kind product`) with both versions and what each one does, and
  wait. Every other conflict you resolve yourself.

## Step 2a — A migration number already taken

Run `python3 ${PLUGIN_ROOT}/scripts/merge_dossier.py --migration-clashes <top
head> --base <stub> --root <checkout>`, with bare branch names (`fix/W-1`, never
`origin/fix/W-1`). Exit 2 with `refs unreadable` means the check did not run:
fix the names, never read it as "no clash". For each migration it names, this
stack takes the number the line ends with ("next number free on the base and
every open pull request head"): rename the file, update whatever names it, and
never touch a migration the stub already holds. Then run Step 3.

The queue's check reads **the bottom pull request's base** and **the top pull
request's head** (`gh pr view <n> --json baseRefName,headRefName`), never a
middle branch's base and never your local checkout. When the queue named a
clash (its timeline line "migration numbers clash: ..."), re-run the check
exactly so. You never end "nothing to do" while the queue-named clash exists: if
your run reads clean where the queue read a clash, state the exact command and
its output in your final message and ask the owner (`--kind product`); a clean
read of different refs proves nothing.

## Step 2b — A red check, no conflict

Read why it failed (`gh pr checks <n>`, `gh run view <run> --log-failed`).
Fix it on the branch, inside what the pull request changed: a spelling or
link a docs check names, a validator's finding on a plan document, a test the
change broke. A failure the change did not cause (an outage, a flaky job that
failed twice) is never put to the owner: re-run its failed jobs
yourself (`gh run rerun <id> --failed`, at most 2 re-runs per head), record a
test that fails outside the touched files and passes locally as a suspected
flake in the PR note, and when the budget is used up end with the failure
noted (`references/owner-questions.md`, "A red CI check outside the change").
Do not patch around it.

## Step 3 — Prove it

Run the round check on the touched paths (`run_gates.py --round
--affected-since <head before the merge>`; never the full list, the PR's CI runs it) and the tests of the
packages the conflicts touched, plus the dossier's own new tests
(`run_tests.py`; `--vm` where the repository needs it). After merging the base
in, also run the tests of every package that both the base and this stack
changed since they split (`git diff --name-only <merge-base>..origin/<base>`
against the same diff of the head). This applies even where no line
conflicted, because the base's new code and tests meet this change there. A
test the base added can break on this stack's change without a text conflict.
A red result is fixed in this merge (inside the conflicted code, or the test or
wiring the merge made necessary) or put to the owner.
A digest that heads with `STACK-ENV: skipped` means the shared stack's settings
are behind this branch and cannot be brought up to it yet (for example the
persistent checkout is still on the branch before the stub): the stack tests
were skipped on purpose. That is not a red result and not an owner question.
Push on the gates and the tests that ran, and say in the PR note and the build
log which stack tests did not run and why, quoting the line; the stub's full
suite runs them. Never run `up.sh`, reset a database or switch the persistent
checkout to get them to run.

## Step 4 — Push

Note the commit you started the merge on (`before=$(git rev-parse HEAD)`, taken before `git merge`). Commit the merge (`git commit --no-edit`, keeping the `Dropped:` lines in the body; `merge_guard.py` exits 0). Then prove it landed, never assume it:
`python3 ${PLUGIN_ROOT}/scripts/merge_guard.py --landed --before $before` (HEAD moved, no MERGE_HEAD, nothing staged). When it exits 1 the commit did
not happen, usually a hook (lefthook) refused it: read the output of `git commit`, fix what it names, commit again, and re-run the check. A hook failure is never skipped: no `--no-verify`, and no owner question asking to allow it. An error that looks platform-only (a Linux-only module on macOS, E0432) is a defect like any other, often an attribute such as `#[cfg(target_os = "linux")]` that a merge or an inserted line moved off the item it gated: compare the file with the base (`git diff <base> -- <file>`) and put it back. Say "the
merge is committed" only after exit 0. A MERGE_HEAD, staged files or a `.git/index.lock` in a worktree this session created is its own unfinished
merge, never "a second process changing the worktree": call it another process only when `ps` or a lock file names one (a live pid), and never ask
the owner about your own pending merge. Then `git push` (never `--force`), except for the stub's own pull request (next paragraph). Check each pull request reads
`MERGEABLE` (`gh pr view <n> --json mergeable`; GitHub may need a minute).
Then wait for each pushed pull request's CI, bottom first, in one call each:
`python3 ${PLUGIN_ROOT}/scripts/wait_ci.py --pr <n>`. Do not end while it is
running. The queue gives this dossier two runs in all, and a check that turns
red after you stop uses up the next one. A red check is fixed in this run, the
way Step 2b fixes one, then pushed and waited for again. A check that is still
pending at the timeout, or red for a cause the change did not make, goes into
the note. Remove the worktrees. Post one note: which pull requests conflicted, in which
files, how each was resolved, and the test and gate results. Under the runner,
end with `RUNNER: DONE`; the merge queue merges next.

### The stub's own pull request

When the head branch is the stub itself (the stub's pull request into
development, whose head is `stub/<date>`), the stub's branch rule refuses a
direct push (GH013: changes must go through a pull request). That refusal is
expected, never an owner question. Push the merge to a side branch
`sync/<stub-branch-with-dashes>-<YYYY-MM-DD>` (`git push origin HEAD:refs/heads/sync/...`,
a fresh name, never `--force`), open a pull request from it into the stub
(`gh pr create --base <stub> --head sync/...`, title `Sync <stub> with
development`, body: the conflicted files and how each was resolved, the test
and gate results), and turn on auto-merge with a merge commit:
`gh pr merge <n> --auto --merge` (never `--squash` or `--rebase`, so the
merge of development stays a real merge and the stub PR's conflict goes away).
Then wait for the sync pull request's CI with `wait_ci.py --pr <n>` as above; when
the checks pass GitHub merges it and the stub's pull request picks the merge up.
A red check is fixed on the sync branch and pushed again. When the repository
refuses auto-merge (its `allow_auto_merge` is off), leave the pull request open,
say so in one line of the note, and end: the owner merges it. The owner answered
this once (config-harness Q12, 2026-11-28); never ask it again.

## Rules

- Never merge, approve or close a pull request, never push to the stub, never
  rebase, never `--force`. The one merge you start is the auto-merge of your
  own sync pull request into the stub (Step 4), which GitHub performs only
  after the required checks pass.
- Never change code outside the conflicted hunks, except a test or gate fix
  the merge itself made necessary.
- Never resolve by taking one side wholesale (`-X ours`, `-X theirs`,
  `checkout --ours`) unless the check in Step 2 shows the other side's change
  is already in it.
