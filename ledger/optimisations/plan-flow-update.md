# Plan-flow update: v9 rules and the owner's P1–P8 decisions

Status: **agreed, not yet built** (2026-11-21). This file holds the plan. The
build lands as follow-up PRs, listed under "Build order" below. The source of
`/plan` is `primary-agents/plan.md` (`claude/commands/plan.md` is generated).

## Why

We replayed past `/plan` runs against later rework to see which product defects
the plan flow let through. Each target below is a defect a later dossier had to
fix. A catch means the plan flow would have raised it before the build.

| Dossier | Missed defect (fixed later by) | What counts as a catch |
|---|---|---|
| W-013 (CH) | Postgres major pinned to pg16 with no ADR; one-way once data landed (W-027) | CONFIRM / owner question on the DB major pin |
| W-044 (CH) | Memo bounded per backend value, but the number of values was never stated; real total unbounded (W-047) | Bound written as units × per-unit with units unknown |
| W-001 (PE) | `RunRequested.Formats` came from demo vocabulary; the product is one template → one episode (W-030) | Concept-fit flag on formats / one-to-many |
| W-003 (PE) | The plan worker also picks sources; retrying or editing sources needed a split (W-010) | Stage-granularity flag on plan + source selection |
| W-068 (CH) | Workspace runs as uid 0, write tools assume a confined root; git as root runs repo hooks (W-101) | Ownership / privilege conflict flagged |
| W-065 (CH) | The risk gate matches text against a verb table; it can be bypassed, and it should fail closed based on the action's effect (W-087) | Text matching / unknown program allowed / bypass flagged |
| W-079 (CH) | Only some track writers go through the fence; nothing enforces it (W-084) | Unfenced writers / no enforcement flagged |
| W-075 (CH) | All callout users share one MESH account; isolation is by subject grants only (W-108) | Single-account isolation raised as CONFIRM |
| W-093 (CH) | Control: no defect | BLOCKING + OWNER-QUESTION count as noise |

The v9 rules below are the version that caught these targets without flooding
the control case.

## Rules (v9)

### P2 pin scan
For each file the plan touches or creates, list:
- image tags and digests;
- version pins (language, runtime, DB, library);
- numeric limits (sizes, counts, TTLs, caps).

For each item, name the ADR that records it, or write "none".

### P3 decision register
Every choice the plan makes is a row with an ID, the chosen default and the
rationale. Any default taken from existing code, any "for now" and any deferral
is a row. There are three kinds.

**CONFIRM (one-way door).** The row MUST name its one-way trigger: the event
after which undoing the choice costs one of:
- a migration;
- a breaking change for another dossier or user;
- money;
- an owner-visible behaviour change.

No nameable trigger means it is DECIDE. More rules for CONFIRM rows:
- Typical CONFIRMs: a public contract or schema shape, a data migration, a
  cross-dossier dependency, an external cost, owner-visible product behaviour,
  and a pin that turns one-way once data lands.
- NOT CONFIRM (use DECIDE instead): additive fields with a default, internal
  names, test-only choices, sizing that is a template value, and anything one
  file change can revert.
- CONFIRM is only for a genuine owner choice between acceptable options. A
  defect with one right fix is not CONFIRM: fix it in the plan.
- Cap: at most 6 CONFIRM rows. Over the cap, first demote rows (see the
  self-check below), then split the dossier.

**DECIDE (two-way door).** The planner picks, records why, and the choice stands
unless overturned. A deferral is a valid DECIDE row only if it names who
decides and when.

**FYI.** Recorded only.

**Self-check before finishing:** for each CONFIRM, write its trigger in one line
and re-read the NOT CONFIRM list. Demote any row that matches it.

#### Seven mandatory rows
Every register has these rows. "n/a, because …" is a valid answer.

- **R-PINS:** every pin from the scan that has no ADR. A pin that turns one-way
  once data lands names that trigger and is CONFIRM.
- **R-BOUNDS:** every bound states its unit and how many units exist. A claimed
  ceiling is written as "total = units × per-unit". Units unknown → CONFIRM.
- **R-STAGES:**
  1. For every worker, stage or task, list EVERY distinct product decision it
     makes (choosing inputs or sources, ordering, generating, formatting, …),
     not only what it stores.
  2. Walk each change or retry case in the product model ("editor changes X",
     "re-run after edit") and name which decision must be redone.
  3. Two decisions in one stage that different cases redo separately → split,
     or CONFIRM.
  4. When a case redoes a whole stage, name the decisions in it that did NOT
     need to change. Each one is a split candidate and gets its own row.

  "Splits adequate" without this table is not an answer.
- **R-NAMES:** each contract name maps to a product-model term or is listed as
  new. A name taken from demo or example vocabulary is CONFIRM.
- **R-WRITERS:** for every stored datum, file or stream the plan guards,
  validates or keeps consistent:
  - list EVERY writer that exists at the baseline (grep the repo, not just the
    plan) and say how each is forced through the guard. A writer that bypasses
    the guard → fix it in the plan, or CONFIRM;
  - name the enforcement that forces a FUTURE writer through the guard: a
    private module, a type, a DB constraint or grant, a lint, or a test. "By
    convention" → CONFIRM.
- **R-TRUST:**
  - who runs as which uid, identity or account;
  - which untrusted or agent-written content (files, hooks, configs, commands)
    is read or executed with more privilege than its author has;
  - what the isolation unit is, and whether it is a structural boundary
    (separate account, tenant, uid or namespace) or only a grant list on a
    shared one;
  - the uid or identity of each writer of a shared path. Two mechanisms that
    assume different owners of the same path are a conflict → fix it, or
    CONFIRM;
  - the account or tenant that holds every principal. Several principals in ONE
    account, isolated only by grants → CONFIRM, with the structural alternative
    named.
- **R-FAILS:** for every guard, policy, allow-list or classifier:
  - what happens on input it does not recognise (it must fail closed, or say
    why not);
  - whether it is keyed on syntax (names, first word, patterns) or on effect
    (what the action can change). A syntax-keyed security policy → CONFIRM, with
    the bypass named.

### P7 product-coherence review
The reader gets the product model, the register, the dossier and the ADR index,
and reads EVERY input in full, including the ADR index; no keyword filtering.

It has four tasks:
1. **Concept fit:** every user-visible noun maps to a product-model concept
   with the same meaning. A new or redefined concept is CONFIRM.
2. **Scenario walk:** each affected scenario, including the model's change and
   retry cases, plus one scaled scenario for anything with a bound.
3. **Shortcut audit:** inherited defaults, deferrals and "for now" must be rows.
   Look hardest at versions, bounds, writers, privilege and unknown-input
   handling. Re-derive these from the dossier yourself; do not trust the
   register's mandatory rows.
4. **Coherence:** no contradiction with ADRs, product rules or other dossiers.

Even when the register has a row, the reader always re-derives two things:
- for each stage, the product decisions it makes and which change case redoes
  each;
- the account or tenant every principal lives in, and whether isolation is
  structural.

Name the gap even when the register already CONFIRMs it (as a NOTE "agrees
D-xx"). Every register row that says "fix in plan" must be present in the
dossier's contract or packages. A missing one is BLOCKING.

Output, typed:
- **BLOCKING:** a product or architecture defect that makes the plan wrong. Not
  for text consistency.
- **OWNER-QUESTION:** at most 3, only what the owner alone can decide. Never
  repeat a register CONFIRM; cite its ID instead.
- **PROMOTE (DECIDE → CONFIRM):** only when the reader can name the one-way
  trigger AND the owner would notice the outcome.
- **P5:** mechanical issues: a criterion that does not match the contract, a
  missing contract block, front matter, the ADR list, an unreachable criterion.
  Listed, never sent to the owner.
- **NOTE:** everything else.

Owner load = CONFIRM rows + OWNER-QUESTION + PROMOTE. Aim for 8 or fewer.

### Replay notes carried into the build
- A P3 "fix in plan" row must land in the contract text (W-044 private type,
  W-079 wake). P4 applies every plan-fix row before P5. P7 checks that each one
  is present; a missing one is BLOCKING.
- A demotion to a plan fix must also land in the contract, or P7 flags it as P5.

## Owner decisions, phase by phase (walkthrough 2026-11-21)

These apply everywhere:
- The dashboard and interactive runs show the same cards; only where you click
  differs.
- Only questions with real impact, or decisions that matter for the product,
  reach the owner. Format, wording, anchor and tooling problems are the
  planner's to fix.

### P1 — Frame
- If `docs/product/model.md` is absent, the first plan in the repo bootstraps a
  draft from ADRs, research and past answers. The owner reviews it at the P1
  stop.
- Q1 (a): every plan stops after P1 for frame approval, one click when it is
  fine.
- Q2: a no-go needs the owner's approval. A spike runs inside the plan run; it
  is not a new dossier.
- Q3 (a): the model is bootstrapped once per repo and reviewed once. P8 appends
  confirmed product rules.

### P2 — Investigate
- Q1 (c): scope is the frame's code + 1 hop + ADRs + overlapping in-flight
  dossiers.
  - By default the planner reads signatures and docstrings through the nav MCP.
  - It reads bodies only to anchor a behaviour FACT, logged per claim.
  - A behaviour claim backed by a docstring only is an INFERENCE.
- Q2 (a): when the code contradicts the product model, it becomes a CONFIRM row
  in P3.
- Q3: the spike runs at the start of P2, in parallel with the independent
  scouts.
  - A disproof that changes the outcome goes back to the P1 stop.
  - A disproof of a detail becomes a P3 row.
- The pin scan (above) runs here.

### P3 — Decision register
- Q1 (a): cap of 6 CONFIRM rows; more means a split (R-STAGES).
- Q2 (b): one batched CONFIRM card at the end of P3.
- Q3 (b): a DECIDE row can be overturned until `/work-on` starts. Overturning
  it reruns P4–P7 for that row.

### P4 — Write the dossier
- Q1 (a): every section is mandatory for every plan, for now. Sizing sections
  to the change returns with the owner's kanban plan.
- Q2 (b): behaviour criteria use EARS, enforced at P5. Performance and
  reliability criteria are quality scenarios.
- Q3 (b): each fitness function is written as a concrete check command.
  - `/work-on` implements it and the gate runs it.
  - Phase 8 may propose promoting a check to the repo's gates, as a DECIDE row.
- Apply every "fix in plan" register row before P5.

### P5 — Contract stubs + mechanical check
- Q1 (b): stubs go on an unmerged branch that already carries the dossier's
  final build name (its `branch:`), so nothing is renamed. `/work-on` creates
  its worktree from that branch.
- Q2 (a): the planner fixes issues itself, up to 3 rounds. It escalates to the
  owner ONLY for a real issue with impact, never for format, wording or anchor
  problems.
- Q3 (b): error and edge behaviour is required for public and cross-package
  members. A private member that later becomes public must meet the same
  standard, through a contract update.

### P6 — Contract pre-check
- Q1 (a): an AMBIGUITY that is a product choice becomes an owner question on
  ONE final card after P7. That card combines the P6 and P7 owner questions and
  is skipped when there are none.
- Q2 (b): when `/work-on` changes the contract, only the changed members and
  the members that use them are re-reviewed. The rest keeps its P6 verdict
  (contract hash per member).

### P7 — Product-coherence review
- Q1 (a+): a script rule triggers the second reader when any of these holds:
  - the dossier adds or redefines a concept;
  - 2 or more work packages cross a stage or module boundary;
  - there is an R-STAGES or R-WRITERS CONFIRM row.

  When the script says no, the planner may still add the reader, recorded as a
  DECIDE row.
- Q2 (a): a BLOCKING finding left after the one fix pass goes on the final
  owner card as a question with options. The plan waits.
- Q3 (b): the reader sees in-flight dossiers, plus done or deferred ones that
  overlap by product-model concept or by touched files (reusing the P1 overlap
  search).

### P8 — Ready + baseline refresh
- Q1 (b): the anchor and contract-target refresh runs at P8 AND again when
  `/work-on` starts. If a contract target changed by the time `/work-on`
  starts, the dossier goes back to re-plan instead of being built.
- Q2 (c): product-rule additions to `docs/product/model.md` are proposed at P8
  and shown on the final owner card. Each is added only after the owner
  approves it there.
- Q3 (a): P8 reruns the P1 overlap search.
  - A new file overlap becomes a DECIDE row (build order, `depends_on`).
  - A new concept overlap becomes an owner question on the final card.

## Build order (follow-up PRs)

Each PR keeps `/plan` working on its own; none depends on a later one.

1. **Prompt split.** Move the phase bodies of `primary-agents/plan.md` into
   `references/plan/` (one file per phase, read at its checkpoint) and renumber
   the flow to P1–P8. No behaviour change.
2. **Register + pin scan (P2/P3).** The register format with the seven
   mandatory rows, the CONFIRM cap and self-check, and the batched CONFIRM card.
   A script lints the register: rows present, CONFIRM triggers named, cap.
3. **Dossier writing (P4/P5).** EARS criteria and quality scenarios,
   fitness-function check commands, applying plan-fix rows, stubs on the final
   build branch, the 3-round self-fix limit, and the error/edge rule for
   public and cross-package members.
4. **Reviews (P6/P7).** The P7 reader prompt above, the second-reader trigger
   script, the overlap search shared with P1 and P8, per-member contract hashes
   for partial re-review, and the ONE final owner card (P6 + P7 + P8
   proposals).
5. **Ready + refresh (P8, `/work-on` start).** The anchor and contract-target
   refresh in `run_dossier.py` at `/work-on` start (re-plan on change), the P8
   overlap rerun, and the gated product-rule appends.
6. **P1 frame stop + model bootstrap.** The P1 stop card (dashboard and
   interactive), the `docs/product/model.md` bootstrap, and the no-go approval
   path.

After each PR: rerun the replay targets above with the PR's prompts and check
that the catches hold and the W-093 control stays quiet.
