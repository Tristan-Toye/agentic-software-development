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
the dossier has no deferred record (`.discovery/deferred/<ID>.yaml`) and its
`## Build log` holds no `DEFERRED:` ledger either (an older run), or when a
listed D-id is not in the record: there is nothing to fix.

The dossier's own copy is on its build branch once `/work-on` removed the
worktree (`git show <branch>:.discovery/dossiers/<file>`); the checked-out
branch has the pre-build copy until the pull request merges.
`${PLUGIN_ROOT}` is this plugin's checkout; under Claude Code take it from
`${CLAUDE_PLUGIN_ROOT}`. The repository's own rules file and `AGENTS.md`
outrank this file's defaults.

Every script this flow uses is in the table under "The commands": call them
exactly as written there and never run `--help` on one (196 `--help` calls in
23 recorded runs cost more than the work they preceded).

Progress posts itself (the plugin's hook). Ask the owner only through
`scripts/ask_owner.py`, as `references/owner-questions.md` says ("The ask policy": design, product or
real uncertainty only, each ask passing `--kind`, never an approval), and never
pick an option of a question you asked. A bug or deferred item is fixed through
the normal flow without asking, unless the fix would change product behaviour
or a contract. Call `phase_checkpoint` at each step below, named
`Fix-deferred — Step <n> — <title>` (`Fix-deferred — Step 2 — Triage`): the
dashboard's Fix deferred issues tab groups the run by these numbers.

## The commands

`<R>` is the repository's main checkout (the working directory), `<WT>` the
worktree `fix_deferred_prep.py` prints, `<ID>` the dossier ID, `<DOSSIER>` the
dossier file it names (`.discovery/dossiers/<ID>-*.md` in `<WT>`), `<S>` is
`${PLUGIN_ROOT}/scripts`.

| Work | One call |
|---|---|
| Triage, stacked branch and worktree, change requests, payloads (Steps 1 to 3) | `python3 <S>/fix_deferred_prep.py --root <R> --dossier <ID> [--items D-1,D-3] [--direct D-4] [--test-command '<cmd>'] [--env-script <file>]` |
| Ask the owner about one `ask` item | `python3 <S>/ask_owner.py ask --root <R> --dossier <ID> --kind design --question '<text>' --option 'A|<label>|<consequence>' --option 'B|…|…' --option 'C|…|…' --recommend A --blocks '<what waits>' --build-log <DOSSIER>` (prints the question id) |
| Wait for the answer, only when nothing else can proceed | `python3 <S>/ask_owner.py wait <question id> --build-log <DOSSIER>` |
| Log a line to the dossier's `## Build log` | `python3 <S>/dossier_edit.py log <DOSSIER> '- DEFERRED-RULING: D-n — <ruling>' '- …'` |
| Read the record | `python3 <S>/build_records.py deferred show <ID> --root <R>` |
| Set an item's state (only when no script above does it) | `python3 <S>/build_records.py deferred set <ID> D-n --state fixing\|fixed\|dossier\|wontfix [--note <sha>] --root <R> --log <DOSSIER>` |
| Check a change-request document | `python3 <S>/check_crs.py <CRS file>` (drop a demoted one: `--drop CR-2 --out <file>`) |
| Route the change requests to fix payloads | `python3 <S>/compose_payloads.py --kind implementer --dossier <DOSSIER> --root <WT> --host claude --crs <CRS file> --out <WT>/.agent-staging/payloads/fix-wave.json --hooks '<text>' [--test-command '<cmd>'] [--assign CR-2=P1]` |
| Run the proving tests and the touched packages, once | `python3 <S>/run_tests.py --log <WT>/.agent-staging/fix-tests.log --cwd <WT> [--vm <name> --workdir <WT> --env-script <file>] -- <one command naming every proving test and package>`; a run over 10 minutes: add `--detach`, then `python3 <S>/run_tests.py --wait <WT>/.agent-staging/fix-tests.log` |
| Run the gates | `python3 <S>/run_gates.py <gates file> --root <WT> [--only a,b] [--env-script <file>]` (gates file: the dossier's `GATES:` line, else `.discovery/analysis/gates-<ID>.txt`) |
| Revert a fix that did not hold | `python3 <S>/safe_revert.py --repo <WT> <path> [<path> …]` (uncommitted), or `git -C <WT> revert --no-edit <sha>` (committed) |
| The worktree's state after the wave | `python3 <S>/git_state.py <WT> --base <base>` |
| Open the PR, close the records, remove the worktree (Step 6) | `python3 <S>/fix_deferred_pr.py --root <R> --dossier <ID> --test 'D-1=<test>' [--test …] --suite '<summary>' --gates '<summary>' [--fixed D-1=<sha>] [--to-dossier D-3] [--dropped D-5] [--dry-run]` |
| Wait for the PR's CI and read its failures | `python3 <S>/wait_ci.py --pr <N>` (`--rerun-failed` once for a flaky job; `--log <DOSSIER>` records the run) |
| Plan an item routed to its own dossier (Step 7) | `python3 <S>/run_dossier.py <ID> --root <R> --command plan --items D-n` |

## Steps 1 to 3 — The items, the triage and the stacked branch

Call `phase_checkpoint` for `Fix-deferred — Step 1 — The items`, `Fix-deferred
— Step 2 — Triage` and `Fix-deferred — Step 3 — The stacked branch` (one call
each, as the work below passes them: the dashboard groups by these numbers).

One call does all three; read nothing by hand first:

```bash
python3 ${PLUGIN_ROOT}/scripts/fix_deferred_prep.py --root <R> --dossier <ID> [--items D-1,D-3] --test-command '<cmd>'
```

It reads the record (`.discovery/deferred/<ID>.yaml`), triages, makes the
stacked branch and worktree, logs the lines the dashboard reads
(`DEFERRED-FIX: branch …`, one `DEFERRED-TRIAGE: D-n → direct — …` per direct
item), writes the change-request document and routes it to payloads. Pass
`--test-command` unless the dossier's `GATES:` line names the gates file (the
payload's `TEST_COMMAND`); it refuses when there is no record, no dossier or an
unknown D-id: then there is nothing to fix (a run older than the record keeps a
`DEFERRED:` ledger in `## Build log`: `dossier_edit.py section <dossier> 'Build
log' --all`, and the same triage below applies by hand).

**The triage is the script's.** The default is a **direct fix in the stacked
PR**, never asked about: the owner chose to fix these items. The script flags
`ask` only for an item that

- changes an **external contract** (a public signature, documented promise,
  route, schema, event, config key or CLI that other code or people consume),
- has effort `L`, or
- **no longer applies**: the `path:line` it names is gone.

Read only the `ask` lines of its output. For each, judge whether the script's
reason holds (a `no longer applies` line is a fact; an `external` or `L` line is
yours to confirm in one look at the item). An `ask` item you judge fine to fix
here joins the direct items, no question. Ask the owner only about an item you
would **not** fix here (it deserves its own dossier, it no longer applies, or it
is not worth fixing: the cost or the risk of the change outweighs what it
buys), one `ask_owner.py ask` per item, all together, before Step 4. The
question argues the case in plain sentences: what the item is, and why you
would not fix it in this stacked PR (the contract it crosses, its blast radius,
its effort, what is still unknown, or why it no longer matters). The options
are:

- `A` — your recommendation: its own dossier (log
  `DEFERRED-TRIAGE: D-n → dossier — <reason>; seed: /plan <ID> D-n`), or
  drop it (log `DEFERRED-DROPPED: D-n — <why>`);
- `B` — fix it in this stacked PR anyway: it joins the direct items, and the
  pull request body names any contract it changes;
- `C` — leave it open for now: no log line, the item stays `open`.

Log each question as `DEFERRED-ASKED: D-n <question id>`, and the owner's
ruling once it arrives as `DEFERRED-RULING: D-n — <the ruling in a few words>`
(then the `DEFERRED-TRIAGE:`/`DEFERRED-DROPPED:` line it leads to, if any), so
the dashboard shows where each item stands. A ruling `B` is one more
`fix_deferred_prep.py --root <R> --dossier <ID> --items D-n --direct D-n --test-command '<cmd>'`:
it reuses the branch and worktree (never recreates them), logs only what is
new, and composes that item's payload for one more wave of Step 4.

Go on with the direct items while the questions are open; under the runner,
end the turn with `RUNNER: WAITING <ids>` only when nothing else can proceed.
The output also gives `UNROUTED: <id> <path> — <why>` when a change request has
no single owning package: rule on those lines only, then re-run
`compose_payloads.py` with `--assign <id>=<package>` (the row in the table).

## Step 4 — Fix, test first

Call `phase_checkpoint` for `Fix-deferred — Step 4 — Fix, test first`.

Everything the Phase 7 fix round does is already done by Steps 1 to 3 except
the spawn; here is the recipe, so no phase file is read:

1. `check_crs.py` on the document: `fix_deferred_prep.py` ran it and printed the
   summary. A `demote` line is a change request missing a field: drop it with
   `--drop` and treat the item as a dossier seed only if it cannot be fixed
   without it.
2. `compose_payloads.py --crs` routed each item to the work package owning its
   path and wrote one `MODE: fix` payload per package, linted, plus the
   `phase4-wave` args file `<WT>/.agent-staging/payloads/fix-wave.json`. **A fix
   package owns every path its CRs name**, across projects. A CR no package
   owns, or two own, is an `UNROUTED:` line (above); a path two fix packages
   would both own is `CONFLICT: <path> is named by CRs routed to <A> and <B>`:
   merge the two packages' CRs into one with `--assign`. The fix payload is
   never composed or edited by hand, and never passes through your prompt.
3. Call the `phase4-wave` workflow **once**, with the array in that JSON file
   as `args`. The spawn guard checks every payload before the launch and
   `verify_return.py` checks every return, so there is nothing to verify by
   hand. Fix implementers that share `<WT>` run one after another inside the
   workflow, each **alone** in `<WT>`: the tests exist only in what each writes
   now, so never run anything that edits `<WT>` beside the wave, and never over
   an uncommitted change of your own. A returned implementer's
   `TOUCHED_BEYOND` path is ruled on by you: never another package's owned
   paths, never the contract files. A second wave (a held-back item, a ruling
   `B`) is a new `fix_deferred_prep.py` call and a new `phase4-wave` call.

Each implementer first writes a focused test that fails on the defect, then
fixes it, so every item ends with its own proving test and its own commit
(`git_state.py` lists them; the return names each item's test).

**Prove the batch once, after the whole wave** (never item by item: the
recorded runs spent 146 `run_gates` and 93 `run_tests` calls on per-item
proving). The dossier's `SUITE-COMMAND` and `SUITE:` lines say its suite mode:

- **`stub`** (the integrated suite is deferred to `/stub`): ONE `run_tests.py`
  call whose command runs every item's proving test plus the tests of the
  packages the fixes touched (`--vm` with the dossier's env script when they
  need the VM; `--detach` then `--wait` for a run over 10 minutes), and the
  pull request's CI through `wait_ci.py` (Step 6). Never run the full workspace
  or the `--stack` suite here: that is `/stub`'s job on the release stub. A
  service-backed test may hold the stack lease for its own run, through
  `run_tests.py`.
- **`ci`**: the same one focused local run, and CI is the verdict.
- **`local`**: Phase 6's full `SUITE-COMMAND`, through `run_tests.py`.

Then ONE `run_gates.py` call over the paths the branch changed (the gates file,
`--only` the gates that cover those paths). Every test and gate run goes
through these two scripts, never a script of your own: their result lines are
how the owner's timeline shows the suite. Those runs are also the evidence
Step 5's reviewers get. When the batch is red, read the failure digest, find
which item's change broke it, and send only that item through one more wave
(Step 4.3, with that item's change request). A suite that stays red after
one fix round for an item turns that item into a dossier seed: revert its
commits with `safe_revert.py` (or `git revert`), log
`DEFERRED-TRIAGE: D-n → dossier — fix did not hold`, pass it to Step 6 as
`--to-dossier`, run the batch once more for the rest, and go on.

## Step 5 — Review

Call `phase_checkpoint` for `Fix-deferred — Step 5 — Review`.

Run one review round with the `style` and `architecture` lenses through
`phase7-review`, scoped to this branch's diff. A blocking change request goes
back through Step 4 once. One still open after that is reported in the pull
request, never hidden.

## Step 6 — The pull request

Call `phase_checkpoint` for `Fix-deferred — Step 6 — The pull request`.

One script call pushes the branch, opens the pull request as **a GitHub stack**
with the dossier's own pull request (never as a loose PR), sets its title and
body, closes the items in the record, logs, and removes the worktree:

```bash
python3 ${PLUGIN_ROOT}/scripts/fix_deferred_pr.py --root <R> --dossier <ID> \
  --test 'D-1=<proving test>' --test 'D-3=<proving test>' \
  --suite '<the suite mode and the batch result>' --gates '<the gates result>' \
  [--fixed D-1=<sha>,D-3=<sha>] [--to-dossier D-2] [--dropped D-5]
```

Run it once with `--dry-run` only when you doubt an input. Without `--fixed`,
every direct item counts as fixed at the branch head; give each item's own
commit sha when you have it. It does what this step always required:

- the link is `gh stack link --open --base <the dossier PR's base> <dossier PR
  number> fix/<ID>-deferred` (bottom to top; `--open` marks them ready, because
  the merge queue refuses a stack with a draft in it), **never without
  `--base`**: without it, linking moves the dossier's PR to the repository's
  default branch, so a code PR would skip the stub. When the dossier's PR is
  already merged there is no stack to join: it opens the PR against the base
  from Step 3;
- the title is `fix(<ID>): deferred issues D-…`; the body has one row per item
  (D-ID, kind, title, `fixed (<sha>)` or `→ dossier (/plan <ID> D-n)`), each
  fixed item's proving test, and the gates and suite summary; the owner's
  Merge button merges the whole stack at once (`gh stack merge`);
- `build_records.py deferred set` for every item (`fixed` with the sha,
  `dossier`, `wontfix`; `fixing` was set at the triage), the build log lines
  `DEFERRED-FIXED: D-n <sha>` and `DEFERRED-FIX: PR <url> — fixed <n>, to
  dossier <m>`;
- the worktree is removed once the pull request URL is in hand.

Under `stub` and `ci` mode, wait for the PR's CI with `wait_ci.py --pr <N>`
after it (the number is in the printed URL): green ends the step; a red job
is read through its digest and goes back through Step 4 once.

## Step 7 — Plan the items routed to their own dossier

Call `phase_checkpoint` for `Fix-deferred — Step 7 — Plan the routed items`.

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
- Prove the branch once, after the whole wave: one `run_tests.py` call and one
  `run_gates.py` call per batch, never one per item. A script prints what it
  decided: read its output, do not re-derive it with `cat`, `grep` or `--help`.
