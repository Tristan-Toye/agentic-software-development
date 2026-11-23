<!-- /plan, Phase 3. Read at the start of this phase; the rules that hold
     in every phase stay in primary-agents/plan.md. -->

## Phase 3 — The decision register, and one batched CONFIRM card

Every choice the plan makes is a row in the dossier's `## Decisions` section
(`formats/dossier.md` § "## Decisions"), written before the contract, so the
owner sees each one-way door before anything is built on it. Write the
register into `${RUN_ROOT}/.agent-staging/plan-<key>/register.md` now; Phase 4
copies it into the dossier.

```
| ID | Kind | Choice | Why | Trigger or where | Ruling |
|---|---|---|---|---|---|
| R-PINS | DECIDE | postgres 16 stays, no ADR yet | the schema uses no 17 feature | — | |
| DR-1 | CONFIRM | the export stream is keyed by issue id | one key per edition | once readers store the key, a change is a migration | |
```

### The kinds

- **CONFIRM** — a one-way door. The row MUST name its one-way trigger in
  `Trigger or where`: the event after which undoing it costs a migration, a
  breaking change for another dossier or user, money, or an owner-visible
  behaviour change. No nameable trigger → it is DECIDE. Typical CONFIRMs: a
  public contract or schema shape, a data migration, a cross-dossier
  dependency, an external cost, owner-visible product behaviour, a pin that
  turns one-way once data lands.
  **NOT CONFIRM (use DECIDE):** additive columns or fields with a default,
  internal names, test-only choices, sizing that is a template value,
  anything reverted by a one-file change.
  CONFIRM is only for a genuine owner choice between acceptable options. A
  defect with one right fix is not CONFIRM: fix it in the plan (a FIX row).
- **DECIDE** — a two-way door: you pick and record why; it stands unless the
  owner overturns it. A deferral is a valid DECIDE row only when it names who
  decides and when.
- **FIX** — a defect the plan fixes. `Trigger or where` names, in backticks,
  the contract member or work-package path that carries the fix; Phase 5
  checks the token is there.
- **FYI** — recorded only.

Any default taken from existing code, any "for now", any deferral is a row.

### The seven mandatory rows

Always present; `n/a, because …` is a valid Choice.

- **R-PINS** — every pin from the Phase 2 scan with `adr: none`. A pin that
  turns one-way once data lands names that trigger and is CONFIRM.
- **R-BOUNDS** — every bound states its unit and how many units exist; a
  claimed ceiling writes `total = units x per-unit`. Units unknown → CONFIRM.
- **R-STAGES** — for every worker, stage or task, list EVERY distinct product
  decision it makes (choosing inputs or sources, ordering, generating,
  formatting …), not only its stored outputs. Then walk each change and retry
  case of the product model and name which decision must redo. Two decisions
  in one stage that different cases redo separately → split or CONFIRM.
  "Splits adequate" without this table is not an answer. When a case redoes a
  stage whole, name the decisions in it that did NOT need to change; each is a
  split candidate and gets its own row. The table goes in a `### R-STAGES`
  subsection.
- **R-NAMES** — each contract name maps to a product-model term or is listed
  as `new:`; a name taken from demo or example vocabulary is CONFIRM.
- **R-WRITERS** — for every stored datum, file or stream the plan guards,
  validates or makes consistent, list EVERY writer that exists at the baseline
  (grep the repository, not just the plan) and say how each is forced through
  the guard. A writer that bypasses it → FIX or CONFIRM. Also name the
  ENFORCEMENT that forces a FUTURE writer through the guard (private module,
  type, database constraint or grant, lint, test). "By convention" →
  CONFIRM. The list goes in a `### R-WRITERS` subsection.
- **R-TRUST** — who runs as which uid, identity or account; which untrusted or
  agent-written content (files, hooks, configs, commands) is read or executed
  with more privilege than its author; what the isolation unit is and whether
  it is a structural boundary (separate account, tenant, uid, namespace) or
  only a grant list on a shared one. Two mechanisms that assume different
  ownership of the same path are a conflict → FIX or CONFIRM. State the uid or
  identity each writer of a shared path runs as. Name the account or tenant
  that holds every principal: several principals in ONE account isolated only
  by grants → CONFIRM, with the structural alternative named. The answer goes
  in a `### R-TRUST` subsection.
- **R-FAILS** — for every guard, policy, allow-list or classifier: what
  happens on input it does not recognise (it must fail closed, or say why
  not), and is it keyed on syntax (names, first word, patterns) or on effect
  (what the action can change)? A syntax-keyed security policy → CONFIRM with
  the bypass named.

A contradiction between the code and the product model found in Phase 2 is a
CONFIRM row too.

### The cap and the self-check

**At most 6 CONFIRM rows.** Before you finish: for each CONFIRM, write its
trigger in one line and re-read the NOT CONFIRM list; demote every row that
matches it. Still over the cap → split the dossier (the R-STAGES table shows
where), by the rule below.

### Scope: one dossier by default

**Your job is to produce one dossier.** One dossier is one branch is one PR, so
the test for a split is narrow:

> Split **only** when part B cannot start until part A has merged.

A hard merge-ordering dependency is the one thing a single dossier cannot
express, because a dossier has one branch. Nothing else forces a split:

- **Several decisions?** One dossier. A feature makes a dozen small decisions
  and still ships as one PR.
- **Many files, or parallel workstreams?** One dossier. That is what
  `## Work packages` is for — the fan-out runs them concurrently inside this
  one dossier.
- **A large change?** One dossier, more packages. Size is a packaging question,
  not a scoping question.
- **Paths that cannot be made disjoint?** One dossier. Sequence the packages
  with `Depends on` instead.

When a split really is forced, make the prerequisite its own dossier and put its
ID in the dependent's `blocked_by`. State the split and the merge-ordering
dependency that forced it, and get the user's agreement — a split doubles the
Jira tickets, the branches, the reviews, and the PRs, so it needs a reason
better than tidiness.

**A blocker stays in `blocked_by` after it is done.** It no longer gates
anything, but it records what this dossier builds on, and the open-work
report draws its dependency graph from those links: a recorded plan dropped
a done blocker and left that dossier in a box of its own. Remove a blocker
only when the dependency itself was wrong. The guard decides from facts: a
blocker whose dossier is `done` may leave unasked; any other leaves only on
the same call that carries the reason, `dossier_edit.py remove|set ...
--reason '<why>'`, which records it (`.discovery/blocker-removals/`) and
writes the one build-log line itself. `validate_pipeline.py --dossier` reads
that record, not a log line.

## Phase 4 — Write the dossier
### The CONFIRM card — one batched question

When the register holds at least one CONFIRM row, put all of them to the owner
as **one** card at the end of this phase:

```bash
python3 ${PLUGIN_ROOT}/scripts/ask_owner.py ask --root ${RUN_ROOT} --dossier <ID> \
  --kind product --topic plan-confirm --blocks "the contract and the packages" \
  --build-log <live dossier, once it exists> \
  --question "$(printf '%s\n' 'Confirm these <N> one-way choices as recommended?' \
      '**Recommendation:** A — <why, in one line>' \
      'Q1 (DR-1): <choice, trigger, why>; a) keep (recommended) b) <alternative> …' 'Q2 (R-PINS): …')" --recommend A \
  --option "A|All as recommended|<one line>" --option "B|Change some|answer per Q in the reply"
```

Each part `Qn`, on a line of its own under the decision and the recommendation
(`owner-questions.md`, "The card layout"), names its row ID, the choice, the one-way trigger, your
recommendation and every acceptable alternative with its consequence. Run
`ask_owner.py wait <id>` as a background command and end the turn. Write each
answer into the row's `Ruling` column (`owner <date>: <answer>`). No CONFIRM
row → no card; log `CONFIRM-CARD: none`.

**A DECIDE row can be overturned until `/work-on` starts.** When the owner
overturns one (on a later card, or by a message), change the row and redo
Phases 4–7 for what that row touches.
