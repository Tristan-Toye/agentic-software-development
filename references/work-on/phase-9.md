<!-- /work-on, Phase 9. Read at the start of this phase; the rules that hold
     in every phase stay in primary-agents/work-on.md. -->

## Phase 9 — PR, then remove the worktree

1. **Sync the base last.** Inside `X` (committed mode: commit the dossier
   first): `git fetch origin <target> && git merge origin/<target>`, where
   `<target>` is the branch the PR merges into — the branch `baseline_commit`
   was taken from, usually the default branch. Then
   run the bookkeeping ritual — it is mechanical, never a judgement call:

   ```bash
   python3 "${PLUGIN_ROOT}/scripts/validate_pipeline.py" \
     --finalize-ids --base origin/<target>
   python3 "${PLUGIN_ROOT}/scripts/validate_pipeline.py"
   git restore docs/adr/index.md
   git commit --no-edit    # only when finalize renumbered something
   ```

   `--finalize-ids` settles the provisional ID space: it renumbers the ADRs
   and LRNs this branch minted that a concurrent branch landed first, rewrites
   them in the branch's own files and dossiers, and regenerates the index.
   A conflict on `docs/adr/index.md` cannot happen — branches never commit
   it (Phase 8a), CI owns it on the target. The LRN ledgers carry a
   `merge=union` driver, so they merge clean locally and finalize renumbers
   what the union left duplicated.

   **Review every `updated dossier` line the finalizer prints before you
   commit** — read the diff (`git diff --stat`, or `git_state.py`) for each
   one it names. It rewrites only dossiers this branch added or modified; a
   `NOTICE` line about a dossier it left alone is informational, never a
   rewrite to chase.

   **Conflicts on code** → show the user the conflicted files, resolve them
   (through `implementer` for code, with the user for a judgement call), and
   if the resolution touched the blast radius, **get the runner's verdict on
   the merged head and re-run the three review lenses** before you go on.
   **Bookkeeping-only merges re-run nothing**: when the sync (merge plus finalize) touched only `docs/adr/**`
   and `docs/learned-rules*.md`, the suite and review evidence already in the
   dossier stands — run the validators, not the tests.

   **Git's silence at this merge is not agreement — three checks before the
   merge commit.** Compile the merged tree once per gated configuration
   before committing it: two files both sides changed can auto-merge into
   code that does not compile, and a check that skips a platform-gated file
   reports zero errors over code it never read. Run the repository's
   decision-id collision check: two branches can mint one ADR or rule number
   under two filenames, and git sees one file added on each side. Stage
   resolved files by name and grep the tree for `<<<<<<<` before committing,
   because `git add -A` stages marker-carrying files and `--diff-filter=U`
   then reports zero unmerged paths. All three were paid for in one recorded
   sync.

   **Re-derive every derived id and count the sync could have moved.**
   `--finalize-ids` settles the ids *this pipeline* mints — ADRs and LRNs.
   It knows nothing about ids and counts the **repository** derives from
   position or from a file census: row numbers keyed to file order, scenario
   or region counts, pins asserting "N tests exist". A target branch that
   added a file shifts every one of them, and the shift is silent — the
   numbers still look like numbers. Re-use the `CENSUS:` lines Phase 3
   logged before the fan-out: they already name every pin and its owner,
   so this is a re-derivation of that same inventory, not a fresh grep.
   After the sync, re-run each derivation
   over the merged tree, re-gate whatever asserts against it, and only then
   run step 2. The failure this prevents costs a whole extra verification
   round, because it surfaces as a test asserting a count nobody changed.

   Where you find one, log it: a repo whose ids are positional is a repo
   where every concurrent branch pays this, and that is `GRADUATION:`
   material for the target repo's own rules file (content-addressed ids do
   not move).
2. **Get the final verdict on the head the PR will carry** and keep the
    evidence — it goes in the PR description and in the Jira comment. Under
    `ci` the run is CI's on the pushed final head: push, wait, and keep the
    run URL and the counts read from its log; there is no local run beside
    it. Under `local`, run the acceptance criteria one final time and keep
    the output. A bookkeeping-only sync does not invalidate the earlier run:
    if step 1 moved only bookkeeping, the evidence you already kept stands
    and this step is a no-op.
 3. **Write the PR description** and show it to the user. Delegate the draft to
    `document-drafter` (`MODE: pr`, dossier excerpts verbatim, `TARGET_PATHS`
    naming a file under `.discovery/` in the main checkout — in committed
    mode under `X`'s `.discovery/`, where nothing is staged by pattern —
    `SCRUB` carrying the worktree names, and in local mode also the dossier
    ID and every `.discovery/` path) — it writes the draft file
    itself and self-checks the written bytes; then grep the written file
    yourself for every scrub token before you show it; two checks, because a
    leaked dossier id is a leaked local path. Two sections:
   `## Summary` — the problem and what this change does, from `## Problem` and
   `## Approach`, for a reviewer who has never seen the dossier.    `## What
   changed` — grouped by theme, with the non-obvious choices explained and the
   verification stated. When Phase 8b drafted a rule, add a `## Pending
   ratification` section carrying the rule text, why, and its trail. Reference
   the **Jira ticket**. **In local mode scrub the dossier ID and every
   `.discovery/` path** — they are local and gitignored. In committed mode
   the dossier is inside this PR, so the description may name it; the
   worktree names are scrubbed in both modes. Acceptance of the description
   doubles as the yes for the PR.
4. **Push the branch and open the PR — or, under `ci`, finish the draft.**
   Under `ci` the branch is already on the remote and the draft PR already
   open from Phase 6: set the approved description as its body (`gh pr edit
   --body-file <draft>`) and mark it ready (`gh pr ready`). Under `local`,
   open it programmatically now when the host supports it. **On Bitbucket
   it does not**: push the branch, then hand the user
   the create-PR link and the approved description as the body, and **ask for the
   PR URL back**. Set `status: pr` while you wait — a run that ends here is
   resumable from exactly this point. Committed mode: commit the dossier
   before the push, so the PR opens with the build record in it.
5. **Record the URL and remove the worktree.** Once the URL is in hand, write it
   to the front matter and set `status: done`. Committed mode: commit that
   final state on `X` and push it — the branch is yours, the PR picks the
   commit up, and the record would otherwise die with the worktree. Then
   `git worktree remove` the base worktree and prune any fan-out branch
   already merged into it. The branch stays on the remote; the PR is the
   user's from here.
   **Do not wait for the merge and do not track it.** Review comments on the PR
   are new work, and they get a **fresh worktree** — a new `/work-on` run on this
   dossier, or a new dossier. Never reopen the worktree that produced the PR.

   **Post-merge cleanup runs only when the user reports the merge and asks for
   it** — state work, not code work, so no fresh worktree. Update the dossiers
   first (local mode: settle whatever the merge decides in the front matter;
   committed mode: `git pull` brings the `done` dossier in, and a discrepancy
   goes through a PR, never a write on the target branch; in both, prune the
   merged branch locally if it lingers), **then regenerate the overview HTML
   report** with the generator `/overview-dossiers` uses, so the dashboard
   reflects the merged state without a full overview pass:

   ```bash
   python3 "${PLUGIN_ROOT}/scripts/generate_open_work.py" \
     --root .discovery
   ```

    Tell the user the output path (`.discovery/analysis/open-work.html`).
    Status table, counts and dependency flows are current after regeneration;
    the health-signal cards carry whatever the last `/overview-dossiers` run
    mined. Regenerate the report; never hand-edit it.

    **When several PRs are open and the user merges one**, the remaining PR
    branches are stale against the new target tip. Offer the refresh ritual
    for each remaining branch (a fresh worktree checked out on it):

    ```bash
    git fetch origin && git merge origin/<target>
    python3 "${PLUGIN_ROOT}/scripts/validate_pipeline.py" \
      --finalize-ids --base origin/<target>
    python3 "${PLUGIN_ROOT}/scripts/validate_pipeline.py"
    git restore docs/adr/index.md
    git commit --no-edit && git push    # only when finalize renumbered something
    ```

    The merge is local, so the union drivers apply; finalize renumbers only
    what the landed branch's numbers displace; the PR button goes green
    again. A conflict on code here is the real signal — resolve it through
    `implementer` and re-run the suite if the blast radius moved, exactly as
    in step 1; a bookkeeping-only refresh re-runs nothing.

    **A cleanup never stashes another run's state.** This checkout is shared:
    a sibling run may hold uncommitted `.discovery/` work here right now. A
    recorded failure: a cleanup stashed "pre-existing local modifications" to
    fast-forward, and a concurrent build's dossier reverted to its seeded
    state — a thousand lines gone mid-run. The rules:

    - Before any fast-forward, pull, or clean-tree operation, run
      `git status` and read it. Foreign `.discovery/` modifications mean
      another run is active: **abort with a diagnostic**, do not stash, do
      not proceed. Tell the user what you saw.
    - Never create a stash that carries another run's files. Never drop a
      stash without `git stash list` plus
      `git show --name-only 'stash@{N}'` — inspect first, drop only your own.
    - Restore foreign-run files verbatim, no merge, no re-type:
      `git restore --source='stash@{N}' -- <path>`.
    - A dossier that "reverts" mid-run is a possible sibling stash. Check
      `git stash list` **first** — one step — before any forensics.
 6. **Comment on the Jira ticket** one time: what changed, the review rounds, the
   test result, and the PR URL. Narrative only, no duration — Tempo holds the
   time. Transition the ticket only if the user confirms.
7. **Finalize the Tempo session** (`references/time-logging.md`) and report the
   block. If finalize refuses — below the floor, or across a day boundary — say
   so and let the user hand-log. Never invent a duration.
 8. **Capture the deferred issues.** Your awareness of the code is at its
    highest now, and chat dies with the session; a later `/deferred` cannot
    reconstruct what you read here. From working memory: *what product-code
    issues are you aware of that this run did not tackle?* Sweep everything
    the run laid eyes on — the primary-surface files you read in full, every
    arbitration's evidence, every review reply, the `NOTICED:` harvest, every
    change request demoted for missing evidence, every `TOUCHED_BEYOND` path
    you accepted — write every item down before judging any, verify each
    `path:line`, and append the ledger to `## Build log`:

    ```
    DEFERRED: <N> — sources: <own reads, NOTICED harvest, review replies>
    - D-1 <kind> <title> — <path:line> — <risk> — <why deferred>
    ```

    `DEFERRED: none — sources: …` is a real answer; defend it or do not
    write it. The D-IDs are minted here, once; the `/deferred` run that
    follows — normally the very next command — reuses them and appends what
    its own recall adds. Capture only; `/deferred` renders the report.
 9. **Report**: the ticket, the branch, the PR URL, the suite runner and
    its final run (the CI run URL, or `local`), the arbitration count by
    kind, the review rounds spent, the mutation kill table (mutants killed and
    survived, or skipped and why), the ADRs extracted, the drafted rules
    awaiting ratification, any graduation issues (created or awaiting your
    yes), the admission ledger's learned ceiling per provider and model
    (`spawn_admission.py status`), the Tempo block, which dossiers this
    unblocks, and the deferred
    count from step 8 — `N deferred issues captured; /deferred renders them`
    — with the worst risk named in one clause when N is not zero.
