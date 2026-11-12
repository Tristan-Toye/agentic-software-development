<!-- /work-on, Phase 0. Read at the start of this phase; the rules that hold
     in every phase stay in primary-agents/work-on.md. -->

## Phase 0 — Select the dossier and route the run

**Gates — before anything else.** Follow
`${PLUGIN_ROOT}/references/time-logging.md` for the time-logging gate.
This command creates worktrees by design, so state that plainly and get the
user's yes before Phase 2. Where the target has CI that covers the required full suite, this
command also **pushes the base branch and opens a draft PR at Phase 6**, so
that CI is the suite runner and no verdict costs a local run beside it
(Phase 2 decides it): state that in the same breath and get the yes for it
here, so no agent is ever spawned into a run that will stall on a question
with a merged tree waiting. Only you handle these gates, never a sub-agent. Under the runner the standing authority (`references/owner-questions.md`) is that yes, so ask nothing.

**Mode — settled once, right after the gates.** Run
`python3 ${PLUGIN_ROOT}/scripts/validate_pipeline.py --mode` in this checkout
(`formats/README.md` § "Two modes for `.discovery/`"). `mode: conflict` (exit 1)
stops the run — show the user what it printed. `mode: local`: the
dossier is untracked, lives only in this checkout, and every write below
goes to it by absolute path. `mode: committed`: the dossier is a
tracked file, so **the live copy is the one inside `X`** from the moment `X`
exists; this checkout's copy is never written (it changes only when a PR
merges), and the build record ships in the PR. Committed mode adds one rule
to every phase that touches git: **commit the dossier on `X` before `X`
merges or pushes** — a dirty tracked dossier blocks a merge that touches it,
and an unpushed record dies with the worktree. Add it by name, never with
`git add -A`. State the mode in one line.

`$ARGUMENTS` names a dossier ID, or is empty. Empty → read the front matter of
every `.discovery/dossiers/*.md`, and take the first with `status: ready` whose
`blocked_by` entries are all `done`. State your pick and continue. In committed mode this checkout's copies show
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
| `building` | Resume: find which packages have merged from git, not from the log — `git -C <X> branch --merged HEAD` lists the package branches already merged into `X`, `git -C <X> branch --no-merged HEAD` with `git worktree list` the rest — and continue the fan-out from the rest. The log still holds the rulings and agent IDs. |
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
