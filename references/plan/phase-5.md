<!-- /plan, Phase 5. Read at the start of this phase; the rules that hold
     in every phase stay in primary-agents/plan.md. -->

## Phase 5 — The stubs, and the mechanical check

The contract stops being prose here: it becomes compiling stub files on the
branch `/work-on` will build on, so the contract pre-check (Phase 6) reads
real code and the build starts from a commit that already compiles.

### 1. The mechanical check

```bash
python3 ${PLUGIN_ROOT}/scripts/validate_pipeline.py --root ${RUN_ROOT} --dossier W-NNN
python3 ${PLUGIN_ROOT}/scripts/plan_flow.py register --root ${RUN_ROOT} --dossier W-NNN
```

The validator checks the front matter, the section set (with `## Decisions`
for `flow: 2`), path disjointness, the criterion shapes (EARS, quality
scenario, a non-empty `Check:` command), anchor existence and the ASD-STE100
rules. `register` checks the decision register: the seven mandatory rows and
their subsections, the CONFIRM cap and triggers, every deferral naming who
decides and when, and every FIX row's token present in `## Contract` or
`## Work packages`. Fix every DEFECT either reports. Never spend a review on
mechanically broken input.

**You fix; you have three rounds.** A format, wording or anchor defect is
yours to fix, never the owner's. When a check still fails after three rounds
of fixes, it is a tooling failure or a defect you cannot see: log
`MECHANICAL-FAIL: <check> — <the defect lines>` and stop the run as failed;
never ask the owner about it. Only a real issue with product or design impact
that the check exposed (two criteria that cannot both hold, a FIX that needs
an owner choice) goes to the owner, as `ask_owner.py ask --kind design` or
`--kind uncertainty`.

### 2. Materialise the stubs on the build branch

Name the branch now, by the rule `/work-on` Phase 2 uses:
`<type>/<JIRA-KEY>-<slug>` (`fix/` for a defect, `feat/` for new behaviour,
`chore/` for mechanical work; the dossier ID only when no ticket exists).
Write it as the dossier's `branch:`. It is the dossier's final build branch:
`/work-on` builds on it under this name, so nothing is renamed later.

Make a short-lived worktree for it, forked from the commit the plan is
anchored to (committed mode: `origin/<base>`, the plan worktree's fork point;
local mode: the checkout's `HEAD`):

```bash
git worktree add ../<repo>-<ID>-contract -b <branch> <fork point>
```

Place the contract there as stubs, by the route `/work-on` Phase 2 uses
(`references/work-on/phase-2.md`, "Write the contract into real files" and the
`STUB-GATE` routes): `materialise_stubs.py` for a signature-only contract,
`stub-materialiser` (payload in `references/payloads/stub-materialiser.md`,
linted by `check_payload.py`) for anything else past four members, your own
typing below the gate. Compile it with the repository's build check. Compare
every signature and documentation-comment block mechanically against
`## Contract`: a byte of drift is a defect. A `GAP:` from the materialiser is a
contract defect: fix the contract and materialise again.

Commit the stubs alone on the branch, with the Jira key and no dossier ID:

```bash
python3 ${PLUGIN_ROOT}/scripts/gate_commit.py --root ../<repo>-<ID>-contract \
  --paths <stub files> -m "<KEY>: contract — <title>" --forbid-id W-NNN
```

Read the repository's commit hooks first (`/work-on` Phase 2, step 4): the
stubs compile, so a compiling hook passes; revert anything an auto-fixer wrote
into a stub. Then record both facts in the dossier and log the route:

```bash
python3 ${PLUGIN_ROOT}/scripts/dossier_edit.py set <dossier> "branch=<branch>"
python3 ${PLUGIN_ROOT}/scripts/dossier_edit.py set <dossier> "contract_commit=<sha>"
```

```
STUB-GATE: <transform | stub-materialiser | self> — <N> members, <why>
CONTRACT-BRANCH: <branch> @ <sha>, forked from <fork point>
```

Keep the contract worktree until Phase 6 has read it, then remove it
(`git worktree remove`); the branch stays. In committed mode Phase 8 pushes it
with the plan branch, under the same yes. In local mode it stays a local
branch.

**When the contract changes later in this run** (a Phase 6 ambiguity, a Phase 7
fix, an overturned DECIDE row), amend the stubs on the branch with a new
commit, record the new `contract_commit`, and re-run this phase's check.

**A re-plan of a dossier whose branch already carries build commits** (a
dossier `/work-on` sent back) leaves that branch alone: set
`contract_commit: null` and log `CONTRACT-BRANCH: none — <branch> carries
build commits`; `/work-on` then materialises the contract itself. A branch
that carries only an earlier plan's contract commit is reset to the new one.
