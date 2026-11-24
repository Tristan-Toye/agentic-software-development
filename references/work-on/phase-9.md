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
   commit** — read the diff (`git diff --stat`, or `git_state.py --full`) for each
   one it names. It rewrites only dossiers this branch added or modified; a
   `NOTICE` line about a dossier it left alone is informational, never a
   rewrite to chase.

   **Conflicts on code** → show the user the conflicted files, resolve them
   (through `implementer` for code, with the user for a judgement call),
   run `python3 ${PLUGIN_ROOT}/scripts/merge_guard.py` before committing the merge (it must
   exit 0; `/resolve-merge` Step 2 says how to read it), and
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

   Detail: `ref_section` with `phase-9-conditional` and the heading "Re-derive every derived id and count the sync could have moved". (when the repository derives ids or counts from position or a file census and the sync merged a target that added files.)

   Detail: `ref_section` with `phase-9-conditional` and the heading "Architecture diagrams must match the code: the c3 close gate". (for a Rust repository with `docs/architecture/c3-components-*.md`.)

   Architecture diagrams (c1 to c4, sequence) are JSON files beside their
   `.md` (`docs/architecture/<name>.json`): the JSON is the source and the
   Mermaid block in the `.md` is generated. Edit the JSON, never the block,
   then run `python3 "${PLUGIN_ROOT}/scripts/vr_mermaid.py" sync docs/architecture`;
   the prose around the block stays hand-written. A diagram still only in
   Mermaid is converted once with `vr_mermaid.py import <file>.md --write`.
   When a tool rewrote a block (`module_graph.py regen`,
   `check_diagram_anchors.py --fix`), or a sync merged code that moved
   anchored lines, run `vr_mermaid.py adopt-anchors docs/architecture` (it
   adopts every block that differs from its JSON only in `path:NNN` line
   numbers) and, for a block a tool rewrote beyond that,
   `vr_mermaid.py import --adopt --write <file>.md`, so the JSON follows; a
   `sync` before that would revert the tool's edit.

   **Diagram gate (close gate).** After the sync, still inside `X`:

   ```bash
   python3 "${PLUGIN_ROOT}/scripts/validate_pipeline.py" \
     --diagram-gate --base origin/<target> --dossier <W-NNN> [--pr-body <file>]
   ```

   It runs `vr_mermaid.py check docs/architecture` (a block that differs from
   its JSON, or an unmarked Mermaid block, is a DEFECT: **the session does not
   close** until it exits 0), then lists every diagram whose source anchors
   point into a file this branch changed and which the branch did not modify,
   as `REVIEW: <diagram> anchors <path>`, plus every diagram the dossier's
   `Diagrams affected:` line lists that the branch left alone. Pass
   `--pr-body` with the drafted PR description once it exists: a listed
   diagram it does not mention is then a DEFECT. A `SKIP` line (no
   `docs/architecture/*.json`) is a stated skip: copy it into the PR
   description.

   The gate also runs `flow_coverage.py --changed`: each new entry point in a
   changed file with no seq diagram and no exemption is a DEFECT. Anchor it in
   a `seq-*` diagram, or exempt it with a `why` in
   `docs/architecture/flow-coverage.json`. A repository with no
   `docs/architecture/seq-*.json` gets a `SKIP flow-coverage` line, stated in
   the PR description like the other `SKIP` lines.

   Every `REVIEW` line is settled before the PR opens, one of two ways. Open
   the diagram with `arch_find` (`query` the path, `diagram` the stem), and if
   the change altered what it shows, edit its JSON (never the generated block),
   `vr_mermaid.py sync docs/architecture`, commit, rerun the gate. Or the
   diagram still holds: write one line in the PR description,
   `Diagram <stem> unchanged: <why it still holds>`. A REVIEW line left
   unanswered in both places is an open defect for Phase 8's reviewers and the
   owner.

   Detail: `ref_section` with `phase-9-conditional` and the heading "New diagrams: create the stems the dossier lists". (when the dossier's `Diagrams new:` line lists a stem.)

2. **Get the final verdict on the head the PR will carry** and keep the
    evidence — it goes in the PR description and in the Jira comment. Under
    `ci` the run is CI's on the pushed final head: push, wait, and keep the
    run URL and the counts read from its log; there is no local run beside
    it. Under `local`, use the recorded full `SUITE-COMMAND` when relevant
    inputs changed since the last green run, and keep the output and coverage
    evidence as in Phase 6. Reuse that run when the relevant inputs are
    unchanged; entering Phase 9 does not require a duplicate run. A
    bookkeeping-only sync does not invalidate the earlier run: record the
    tested commit and final head with that explanation, run validators, and
    retain the existing suite evidence.

    **Produce the review packet** on the head the PR will carry: one command,
    `python3 ${PLUGIN_ROOT}/scripts/review_packet.py all --dossier <ID> --base <BASE> --log`,
    writes six JSON files under `.discovery/review-packet/<ID>/` and one
    `REVIEW-PACKET:` receipt per file into `## Build log`. It never blocks: a
    producer that cannot run writes `skipped: <reason>`.

    Detail: `ref_section` with `phase-9-review-packet` and the heading "The review packet: six data producers, run before the PR". (every run that reaches the PR.)

 3. **Write the PR description** and post it as a note (it is not an approval step). Delegate the draft to
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
   worktree names are scrubbed in both modes. When `## Build log` holds any
   `PROVISIONAL-RULING:` line, add a `## Rulings to review` section that lists
   each one with its what, why and how to reverse, so the owner can overrule
   after the fact. The PR itself is covered by the standing authority
   (`references/owner-questions.md`); nobody approves the description first.
4. **Push the branch and open the PR — or, under `ci`, finish the draft.**
   Under `ci` the branch is already on the remote and the draft PR already
   open from Phase 6: set the description as its body (`gh pr edit
   --body-file <draft>`) and mark it ready (`gh pr ready`). Under `local`,
   open it programmatically now when the host supports it. **On Bitbucket
   it does not**: push the branch, then hand the user
   the create-PR link and the description as the body, and **ask for the
   PR URL back**. Set `status: pr` while you wait — a run that ends here is
   resumable from exactly this point. Committed mode: commit the dossier
   before the push, so the PR opens with the build record in it.
   **Under `local`, wait for the PR's own CI before step 5** when the host
   runs CI on it: `python3 ${PLUGIN_ROOT}/scripts/wait_ci.py --pr <N> --log <dossier>`
   (under the runner: end the turn with `RUNNER: WAITING ci:<N>` and read the
   `CI RESULT <N>: ...` it sends back; no loop of waiting calls).
   The local suite does not run in CI's environment (a tool installed here
   but not on the runner, another OS), so a green local run does not prove
   the PR is green. Green: log `CI: green on <sha> (<run URL>)`. Red because
   of this change, its tests or its test helpers: treat it as a Phase 6
   failure. Fix it on this branch, test first, through the implementer flow,
   then push and wait again. Never declare the run done on a red PR. A red
   check this branch does not touch (a network error, or a check that is red
   on the base too): never ask the owner. Re-run its failed jobs yourself
   (`gh run rerun <id> --failed`, or `wait_ci.py --rerun-failed`), **at most 2
   re-runs per head**. A test that fails outside the files this change touched
   and passes locally is a suspected flake: log `CI: red <check> — suspected
   flake: <evidence>`, note it in the PR description (or as a deferred item).
   When the 2 re-runs are used and it still fails outside the change, end the
   CI wait with `CI: red <check> — not this change, 2 re-runs used` in the
   log and the PR description, and go on; the stub integration (`/stub`) owns
   cross-dossier red. Only a red check inside this change's own files stays a
   fix-it item (above).
5. **Record the URL and remove the worktree.** Once the URL is in hand, write it
   to the front matter as `pr: [<url>]` (the canonical form: a list of full PR URLs, `dossier_edit.py set <dossier> pr=<url>` writes it) and set `status: done`. Committed mode: commit that
   final state on `X` and push it — the branch is yours, the PR picks the
   commit up, and the record would otherwise die with the worktree. Then
   remove the base worktree, and every fan-out worktree of this run, with
   `python3 ${PLUGIN_ROOT}/scripts/remove_worktree.py --root <main checkout> <path>...
   --branch` — never a bare `git worktree remove --force`, which auto mode
   refuses: the script removes only this repository's `<repo>-…` worktrees,
   refuses uncommitted work unless `--save` saves it first (under
   `~/.agentic/leftovers/`; a `--copy-to DIR` inside any checkout of the
   repository is refused), and deletes a fan-out branch only once it is merged.
   **Leftovers and staging go under `~/.agentic/`, never into the repository's
   main checkout.** The branch stays on the remote; the PR is the
   user's from here.
   **Do not wait for the merge and do not track it.** Review comments on the PR
   are new work, and they get a **fresh worktree** — a new `/work-on` run on this
   dossier, or a new dossier. Never reopen the worktree that produced the PR.

   Detail: `ref_section` with `phase-9-conditional` and the heading "Post-merge cleanup, and the rule that a cleanup never stashes another run's state". (only when the user reports the merge and asks for it, or `/after-merge` starts it; never as part of this run.)

 6. **Comment on the Jira ticket** one time: what changed, the review rounds, the
   test result, and the PR URL. Narrative only, no duration — Tempo holds the
   time. Transition the ticket only if the user confirms.
7. **Finalize the Tempo session** (`references/time-logging.md`) and report the
   block. If finalize refuses — below the floor, or across a day boundary — say
   so and let the user hand-log. Never invent a duration.
 8. **Capture the deferred issues.** Your awareness of the code is at its
    highest now, and chat dies with the session; a later `/deferred` cannot
    reconstruct what you read here. Start from the `NOTICED:` lines in
    `## Build log` (`dossier_edit.py section <dossier> 'Build log' --all`; every stage logged them as it went, so a run whose stages
    were separate processes keeps them), then ask working memory: *what
    product-code issues are you aware of that this run did not tackle?* Sweep everything
    the run laid eyes on — the primary-surface files you read in full, every
    arbitration's evidence, every review reply, the `NOTICED:` harvest, every
    change request demoted for missing evidence, every `TOUCHED_BEYOND` path
    you accepted — write every item down before judging any, verify each
    `path:line`, and record each one in the structured record
    `.discovery/deferred/<ID>.yaml`:

    ```
    python3 scripts/build_records.py deferred add <ID> --kind <kind> --title '<title>' \
        --loc <path:line> --evidence '<path:line — what you saw>' --fix '<proposed fix>' \
        --boundary '<none|internal|external>' --blast-radius '<risk>' \
        --note 'effort: <S|M|L>' --source '<own reads|NOTICED harvest|review replies>' --log <dossier>
    ```

    Every item carries `boundary` and `effort`, test tooling and other
    non-product items included: you judge them best now, and the dashboard
    and `/fix-deferred` triage on them. `boundary` is what a fix crosses
    (`none`; `internal` — a signature or module seam; `external` — a route,
    schema, event, config key or CLI with consumers); `effort` is `S` (under
    an hour), `M` (hours) or `L` (days), as `/deferred` defines them. `--log`
    appends one summary line to the build log for a human reader; nothing
    reads it.

    No issue is a real answer: `build_records.py deferred none <ID>
    --source 'own reads' --log <dossier>` writes `items: []` plus the
    sources. Defend it or do not write it. The D-IDs are minted here, once; the `/deferred` run that
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

**Finalize.** `python3 scripts/finalize_pr.py --dossier ID --worktree PATH --target BRANCH` does the merge of the target, finalize-ids, validation, conflict-marker and duplicate-ADR checks and prints evidence; a merge conflict aborts and exits 2 for you to resolve. Act on its output instead of repeating those steps by hand.
