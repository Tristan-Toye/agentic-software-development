<!-- /plan, Phase 7. Read at the start of this phase; the rules that hold
     in every phase stay in primary-agents/plan.md. -->

## Phase 7 — The product-coherence review

Spawn `reviewer` with `LENS: plan` (`references/payloads/reviewer.md`). It
reads the product model, the register, the dossier, the ADR index and the
related dossiers, and answers two questions: does this plan fit the product
(concepts, scenarios, shortcuts, coherence), and could a competent implementer
build it and a test prove it, without asking anybody anything? It has no write
tools. Its `DOSSIER` and `WORKTREE_DIR` both point into `${RUN_ROOT}`, so the
anchors it spot-checks are the tree the dossier was written against.

Fill the payload's plan fields:

- `PRODUCT_MODEL` — `${RUN_ROOT}/docs/product/model.md`.
- `ADR_INDEX` — `${RUN_ROOT}/docs/adr/index.md`.
- `RELATED_DOSSIERS` — every dossier `plan_flow.py overlap --dossier W-NNN`
  lists (in flight, done and the sources of deferred items), one absolute path
  per line, or `none`.
- `READER` — `first`, or `second` for the second reader.

### The second reader

```bash
python3 ${PLUGIN_ROOT}/scripts/plan_flow.py second-reader --root ${RUN_ROOT} --dossier W-NNN
```

`second-reader: yes — <reasons>` (the plan adds or redefines a concept, two or
more packages cross a module boundary, or R-STAGES or R-WRITERS is CONFIRM) →
spawn a second, independent `reviewer` with the same payload and
`READER: second`, in the same message as the first, so they run concurrently
and neither sees the other. `second-reader: no` → one reader; you may still
add the second when you judge the plan needs it, recorded as a DECIDE row
(`DR-n | DECIDE | second reader | <why>`). Merge the two outputs; a finding
both readers raise counts once.

### The typed output

The reviewer returns typed findings (the `LENS: plan` section of `sub-agents/reviewer.md`):

- **BLOCKING** — a product or architecture defect that makes the plan wrong,
  and every register row that says "fix in plan" but is missing from the
  contract or the packages. Fix it yourself in the fix pass.
- **OWNER-QUESTION** — at most 3; only what the owner alone can decide. Add
  each to `final-card.md`.
- **PROMOTE** — a DECIDE row that is really a one-way door the owner would
  notice. Change it to CONFIRM; it goes on the final card (the CONFIRM card is
  past). The CONFIRM cap still holds.
- **P5** — a mechanical issue (criterion against contract, a missing contract
  block, front matter, the ADR list, an unreachable criterion). Fix it and
  re-run the Phase 5 check; never send it to the owner.
- **NOTE** — everything else. Log it.

**Owner load** = the CONFIRM rows + the OWNER-QUESTIONs + the PROMOTEs. Aim for
8 or fewer; above that, look again for rows to demote and questions you can
answer from the code or an ADR.

### One fix pass, then the check round

Append the findings to `## Build log` verbatim, then **fix them yourself** —
you hold the pen. A contract change goes through Phase 5 ("When the contract
changes later") and gets a contract pre-check of the changed members only
(`plan_flow.py hashes --diff`, then `--write` after the review). Re-run the
Phase 5 check, then re-spawn the reader(s) with `PRIOR_CRS` and `ROUND: 2`, so
they answer only whether each finding is resolved; they must not open new
subjects. **In round 2, `DOSSIER` names a copy of the dossier without
`## Build log`, written outside `.discovery/`** — the reviewer's `Read` has no
limit, so on a long dossier the log arrives whole and the reviewer reads its
own prior verdict and your rulings; the recorded case voided two rounds. An
instruction not to read a section of a file the agent can open is not
blindness.

**A BLOCKING finding still open after the fix pass goes on the final card** as
a question with options (the fixes you see, and their consequences); the plan
waits for that answer. Never mark a plan ready by exhaustion. In committed
mode the dossier stays `planned` and uncommitted in the plan worktree until
the answer — name its path; `/plan W-NNN` resumes there.

**A finding you disagree with is not yours to overrule.** When a recommended
reading exists and the impact is low (no behaviour the dossier promises, no
public contract another dossier depends on), take it and log
`PROVISIONAL-RULING: <what>, <why>, <how to reverse>` in `## Build log`; the
dossier lists it for the owner's review. A design or product disagreement goes
on the final card: present both positions, then record the ruling.

Post a note when the review is done (`ask_owner.py note`): the verdict, the
owner load, and what goes on the final card.
