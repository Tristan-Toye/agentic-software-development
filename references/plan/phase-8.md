<!-- /plan, Phase 8. Read at the start of this phase; the rules that hold
     in every phase stay in primary-agents/plan.md. -->

## Phase 8 — Refresh, the final card, `ready`, and the PR

### 1. Refresh the anchors and the contract targets

The base may have moved while the plan waited on the owner. Check it against
the commit the plan investigated (the fork point of Phase 1, or the checkout's
`HEAD` at Phase 2 in local mode), written as a provisional `baseline_commit`
when it is not set yet:

```bash
git fetch origin <base>     # committed mode; local mode refreshes against HEAD
python3 ${PLUGIN_ROOT}/scripts/plan_flow.py refresh --root ${RUN_ROOT} --dossier W-NNN --ref origin/<base>
```

- `refresh: clean` → go on.
- `anchor gone` / `anchor moved?` → merge the base into the plan worktree
  (committed mode), open the anchors again and fix them in the dossier.
- `contract target changed <path>: <names>` (exit 3) → the code a contract
  member builds on changed. Re-check those members against the new base: fix
  the contract where needed, re-materialise the stubs on a fork of the new base
  (Phase 5), and pre-check the changed members (Phase 6, `hashes --diff`).

`/work-on` runs the same refresh when it starts, and sends the dossier back to
`/plan` when a contract target changed after this point.

### 2. Run the overlap search again

```bash
python3 ${PLUGIN_ROOT}/scripts/plan_flow.py overlap --root ${RUN_ROOT} --dossier W-NNN
```

Compare it with the Phase 1 result. A **new file overlap** (another dossier now
touches the same files) is a DECIDE row: the build order, and `blocked_by`
when one must merge first (Phase 3, "Scope"). A **new concept overlap** (another
dossier now changes the same product concept) is an owner question on the final
card.

### 3. Propose the product rules

A product rule this plan introduces or relies on that `docs/product/model.md`
does not hold yet is a proposal on the final card, one part per rule, with its
source. Add it to the model only after the owner approves it there; a rejected
rule is a contract or approach question to fix before `ready`. A fitness check
(`Check:` criterion) worth promoting to the repository's own gates is a DECIDE
row, not a card question.

### 4. The final card — one card, or none

`final-card.md` now holds every owner question of the plan after the CONFIRM
card: the product ambiguities of Phase 6, the OWNER-QUESTIONs and PROMOTEs of
Phase 7, a BLOCKING finding still open, the new concept overlaps and the
product-rule proposals. When it is empty, skip the card and log
`FINAL-CARD: none`. Otherwise ask once:

```bash
python3 ${PLUGIN_ROOT}/scripts/ask_owner.py ask --root ${RUN_ROOT} --dossier W-NNN \
  --kind product --topic plan-final --blocks "status: ready" --build-log <live dossier> \
  --question "$(printf '%s\n' 'Rule on these 3 items as recommended?' \
      '**Recommendation:** A — <why, in one line>' \
      'Q1 (AMBIGUITY Flush.drain): a) … (recommended) b) …' 'Q2 (PROMOTE DR-4): …' 'Q3 (rule): …')" --recommend A \
  --option "A|All as recommended|<one line>" --option "B|Change some|answer per Q in the reply"
```

Each part `Qn`, on a line of its own under the decision and the recommendation
(`owner-questions.md`, "The card layout"), says what it is (its row, member or finding), the options with
their consequences, and your recommendation. Run `ask_owner.py wait <id>` as a
background command and end the turn. Apply every answer: a ruling into the
row's `Ruling` column or an `OWNER-RULING (<date>):` build-log line, a contract
change through Phase 5 and the Phase 6 pre-check of the changed members, an
approved product rule into `docs/product/model.md` with the dossier ID as its
source. A changed contract or approach re-runs the Phase 5 check; it does not
re-open the review unless a change is a new subject.

Kanban: when the repo has kanban on (`delivery_flows.py --root ${RUN_ROOT}`),
read `${PLUGIN_ROOT}/references/plan/kanban.md` ("Phase 8") now: it classifies
the dossier and puts the verdict and gate evidence on this card.

### 5. Write `ready`

Run the last check, then write the state:

```bash
python3 ${PLUGIN_ROOT}/scripts/plan_flow.py register --root ${RUN_ROOT} --dossier W-NNN --ready
python3 ${PLUGIN_ROOT}/scripts/validate_pipeline.py --root ${RUN_ROOT} --dossier W-NNN
```

`--ready` also checks that every CONFIRM row carries its ruling.

Only after both checks pass and every owner answer is applied: set `status: ready`, bump `updated`, stamp
`baseline_commit` — the commit at which your anchors are true. Stamp it last,
so a run that fails part way never installs a baseline for code nobody
checked. In committed mode stamp it **before** the dossier commit below: the
base tip is the commit that survives a squash merge, and the plan commit is
not.

**Stamp against the repository the dossier will build in.** `baseline_commit`
and every `anchors:` path must resolve in the checkout `/work-on` will fork
from — normally `${RUN_ROOT}` itself: stamp its current `HEAD`. When you are
planning in a different checkout than the build repository (a plugin clone, a
sibling repo), resolve the sha and the anchor paths in the build repository —
`git -C <build repo>` for the sha, the build repo's tree for the paths — and
add `planning_repo: <that checkout>` to the front matter, so the validator
treats an unresolved anchor as a re-point to make at Phase 2, not a plan
defect. A `baseline_commit` that exists in no repository of the build
checkout was stamped against the wrong one.

### 6. Committed mode: commit, push, PR

In local mode you are done — go to step 7. In committed mode the dossier is
a change to review, so finish it as one:

1. **Commit the plan**: the dossier, the `.gitignore` lines Phase 0 added, the
   contract hashes and, when this plan bootstrapped or extended it, the
   product model: `git add .discovery/dossiers/W-NNN-<slug>.md .gitignore
   .discovery/contract-review/W-NNN.json docs/product/model.md`, message
   `<KEY>: plan — <title>`. Nothing else belongs on this branch; read
   `git status` before you commit, and never `git add -A`.
2. **Settle the ID against the base tip.** A sibling plan PR may have landed
   your number first. Inside the plan worktree, after the commit above:

   ```bash
   git fetch origin <base> && git merge origin/<base>
   python3 ${PLUGIN_ROOT}/scripts/validate_pipeline.py --finalize-ids --base origin/<base>
   git commit --no-edit    # only when finalize renumbered something
   ```

   `--finalize-ids` renumbers the dossiers this branch added whose `id:` a
   file on the base already carries — the file name, `id:`, and every
   mention inside this branch's own dossiers — and leaves the sibling alone.
   Re-run the validator on the renamed file. With a claimed ID (Phase 1)
   nothing renumbers here. A provisional ID colliding with a plan PR
   still open cannot be seen from here: once both land,
   `validate_pipeline.py --all` names the two files, `/work-on` Phase 0
   stops on them, and the later one renumbers in its own PR the same way.
   When the dossier ID is renumbered and the build branch is named after it
   (no Jira key), rename the branch and the `branch:` field the same way.
3. **Ask once, then push both branches and open the PR.** One yes covers it:
   "push `plan/<KEY>` and the contract branch `<branch>`, and open a PR from
   `plan/<KEY>` against `<base>`?" Then:

   ```bash
   git push -u origin plan/<KEY>
   git push -u origin <branch>      # the contract commit; /work-on builds on it
   gh pr create --base <base> --head plan/<KEY> \
     --title "<KEY>: plan — <title>" --body-file <scratch>/plan-pr.md
   ```

   Not a draft: the plan is reviewed and final; a draft is skipped by the
   dashboard's Merge all. The contract branch gets no PR of its own: `/work-on`
   opens the build PR from it.

   You write the body yourself — no drafter spawn. Five parts: `## Problem`
   and `## Approach` verbatim, the decisions with their rulings, the
   acceptance criteria as a list, the review rounds and the owner load, and
   the handoff line — `/work-on W-NNN` once merged and pulled. Reference the
   Jira ticket. Write the body outside the repository (a scratch directory).
   On a host `gh` cannot reach (Bitbucket), push, hand the user the create-PR
   link and the body, and ask for the URL back.
4. **Remove the plan worktree** once the URL is in hand:
   `git worktree remove ../<repo>-plan-<KEY>`. The branch stays on the
   remote; the PR is the user's from here. A no at step 3 leaves the worktree
   and the branch in place — name both, so the user can push later.


### 7. Report

The mode, the dossier ID and path, the problem in one sentence, the approach
in one sentence, the decisions (CONFIRM rows with their rulings, the owner
load), the work-package count with the concurrency it allows, the contract
branch and its commit, and the handoff. Local mode: `/work-on W-NNN`.
Committed mode: the plan branch and the PR URL — or, after a no, the worktree
path — and `/work-on W-NNN` once the PR has merged and `<base>` is pulled.
