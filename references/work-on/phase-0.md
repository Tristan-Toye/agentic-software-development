<!-- /work-on, Phase 0. Read at the start of this phase; the rules that hold
     in every phase stay in primary-agents/work-on.md. -->

## Phase 0 — Select the dossier and route the run

`$ARGUMENTS` names a dossier ID, or is empty. Empty → read the front matter of
every `.discovery/dossiers/*.md`, and take the first with `status: ready` whose
`blocked_by` entries are all `done`. State your pick and continue on
confirmation or silent assent. In committed mode this checkout's copies show
only what has merged, so list the worktrees first (`git worktree list`): a
`../<repo>-<ID>` present means that dossier is in flight and its live front
matter is in that worktree, not here. Route on the live copy. Run
`python3 ${PLUGIN_ROOT}/scripts/validate_pipeline.py --all` once: two files
sharing one ID is a plan collision that landed — stop and say which; the
later one renumbers in its own PR with `--finalize-ids`.

**Route on `status`** — this command is resumable:

| `status` | What this run does |
|---|---|
| `ready` | The full build, from Phase 1. |
| `building` | Resume: read `## Build log`, find which packages have merged into the base branch, and continue the fan-out from the rest. |
| `review` | Resume at Phase 6 — get the suite's verdict on the current head first, from the runner `SUITE-RUNNER:` names (CI's run on the pushed head, or a local run), and never trust a log line over the actual run. |
| `pr` | Ask the user for the PR URL, then finish Phase 9: record it and remove the worktree. |
| `done` | Ask what the user wants. A follow-up on finished work — PR review comments, a second pass — starts a **fresh worktree**, either as a new run on this dossier or as a new dossier. Never reopen the worktree that produced the PR. |
| `dropped` | Stop and say so. |
| `planned` | Refuse. Run `/plan W-NNN` first — the plan review has not passed. |

`/work-on` never writes `ready` and never re-plans. If the plan turns out to be
wrong once you are in the code, that is a finding for the review cycle or
grounds to send the dossier back to `/plan` with a note: set `status: planned`
and append a `RETURNED-TO-PLAN: <note>` line to `## Build log`, so the
returning `/plan` run finds the note and no later `/work-on` run resumes a
dossier whose plan was found wrong — never a reason to silently re-plan
inline.

**Claim it.** Write `updated` and set `status: building` now, before any other
write. Check `worktree` and `branch` in the front matter: if they are already
set and the paths exist, this is a resume, so never delete or force-recreate
one. In committed mode the claim is `X` itself: you cannot write this
checkout's copy, so the `building` write lands in `X`'s copy the moment
Phase 2 makes it, and an existing `../<repo>-<ID>` is the resume signal
whatever this checkout's front matter says.
