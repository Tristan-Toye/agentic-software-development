<!-- /plan, kanban delivery. Read at Phase 4 and again at Phase 8 only when
     `delivery_flows.py --root ${RUN_ROOT}` prints `"kanban": true`; a repo with
     kanban off never reads this file. -->

## Kanban delivery

A repo can ship a dossier through the **stub** flow (a weekly stub, one full
stack run) or the **kanban** flow (the PR merges into `development`, CI is its
only proof, the stack proof waits for the next `/stub`). Kanban is for small,
fast changes: bug fixes, items born from a deferred report. Which flow a dossier
takes is mechanical, never a feeling.

### Phase 4 — size the dossier, state the gates

When the plan is small enough for kanban (one implementer package, no shared
interface change), write it **sized to the change**: keep `## Problem` (with
evidence), `## Approach`, `## Contract`, `## Work packages` (one implementer row
plus the `UT` and `IT` rows), `## Acceptance criteria` and `## Build log`.
`## Decisions` is optional: omit it when the register would be seven `n/a` rows. Quality scenarios
and `Check:` fitness functions stay optional as in every plan; write one only
when a criterion needs it. Do not pad a section to fill the template.

State the three gates only you can judge, as a block list in the front matter,
one line each, `G2|G3|G4 pass|fail: <evidence>` (a gate you do not state fails):

```
delivery_gates:
  - G2 pass: no member is used outside src/flush.py
  - G3 pass: no schema, data or infra change
  - G4 pass: criteria 1-2 run in unit tests, no live-stack service
```

G2: the contract adds or changes no member consumed outside its package. G3: no
irreversible migration (schema, data, infra destroy). G4: every criterion is
provable without the full stack. Evidence is a path or a fact, not "looks fine".

### Phase 8 — classify, show it on the final card

Run it once the packages and criteria are final, before the final card:

```bash
python3 ${PLUGIN_ROOT}/scripts/classify_delivery.py --dossier <live dossier> --root ${RUN_ROOT}
```

It prints one evidence line per gate (G1 package count, G5 blockers are done or
kanban, plus your three lines) and `DELIVERY: stub|kanban`. When the repo has
both flows on, the final card carries one part `Qn (DELIVERY)`: the verdict, the
five evidence lines and the options "as classified (recommended)" and "ship as
stub". The owner may always move kanban to stub; moving stub to kanban past a
failed gate is refused by the script, so offer it only when every gate passes.
Apply the answer with `classify_delivery.py ... --override stub|kanban --write`.
With only kanban on, write the verdict with `--write` and add no card part. With
only stub on, run nothing: the dossier stays `delivery`-less. A failed gate needs
no question: the dossier is stub, `--write` records it when `delivery:` exists.
A dossier sized for kanban that ends up `stub` needs the full template back
(`## Decisions` with its register) before `ready`; the validator refuses it
otherwise.
