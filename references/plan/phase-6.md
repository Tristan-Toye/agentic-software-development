<!-- /plan, Phase 6. Read at the start of this phase; the rules that hold
     in every phase stay in primary-agents/plan.md. -->

## Phase 6 — The contract pre-check

Spawn `contract-reviewer` on the stubs Phase 5 committed, the same review
`/work-on` Phase 3 runs before its fan-out (`references/payloads/contract-reviewer.md`):
`WORKTREE_DIR` is the contract worktree, `CONTRACT_PATHS` the stub files,
`CRITERIA` the acceptance criteria verbatim, `CHECKLIST_RULES` the
observability checklist pasted verbatim, `BASE_TEMPLATES` for any base class
outside the contract that a stub extends. Lint the payload with
`check_payload.py` first. It sees only the stubs and the criteria — never the
dossier or your own checklist — so a thin contract is caught before anything
is built on it.

Diff its checklist against yours. Every disagreement is a contract defect:

- **`DEFECT:`** → fix the contract, amend the stubs (Phase 5, "When the
  contract changes later"), re-run the Phase 5 check.
- **`AMBIGUITY:`** that the dossier already settles → cite where, and log
  `AMBIGUITY-SETTLED: <member> — <reading>, settled by <section>`.
- **`AMBIGUITY:`** that is yours to settle (two readings, one right answer by
  the code or an ADR) → fix the documentation comment, log it.
- **`AMBIGUITY:`** that is a **product choice** (both readings are acceptable
  products) → it is an owner question. Do not ask now: add it to
  `${RUN_ROOT}/.agent-staging/plan-<key>/final-card.md` with both readings
  verbatim, the consequence of each and your recommendation. Phase 8 puts the
  whole list to the owner as one card. Continue with your recommended reading
  meanwhile, and mark it in the comment's build-log line
  (`AMBIGUITY-OWNER: <member> — pending the final card`).

One re-review round at most, on the changed members only. Then record the
verdict and store a hash per contract member:

```bash
python3 ${PLUGIN_ROOT}/scripts/dossier_edit.py set <dossier> "contract_review=<contract_commit the review read>"
python3 ${PLUGIN_ROOT}/scripts/plan_flow.py hashes --root ${RUN_ROOT} --dossier W-NNN --write
```

The hashes (`.discovery/contract-review/W-NNN.json`) let `/work-on` re-review
only what changed: when the build changes the contract, `hashes --diff` names
the changed members and the members that use them, and every other member
keeps this verdict. In committed mode the file goes in the plan PR with the
dossier.

Remove the contract worktree now (`git worktree remove ../<repo>-<ID>-contract`);
the branch stays.
