<!-- /work-on, Phase 3, triggered parts. phase-3.md names each trigger; read the
     one section with ref_section, never the whole file. -->

# /work-on Phase 3 — triggered parts

Parts of Phase 3 that apply only when their case arises. `phase-3.md` names each trigger.

### A leaves-state-unchanged promise is before-equals-after, never an absolute value

**Word a "leaves state unchanged" promise as before-equals-after, never as an
absolute value.** When the contract says sourcing or calling a member leaves
the caller's state as it was (shell traps, shell options, umask, working
directory, environment), the checklist line reads `<state> after equals
<state> before [whole-value]`, both captured the same way in one process.
Never `<state> is empty` or a fixed literal: a CI runner starts each step with
inherited state (GitHub's ubuntu runner ignores SIGPIPE, which bash 5 lists in
`trap -p`), so a test green on a local shell goes red there. Example:
`source_lib — shell traps: trap -p after equals trap -p before [whole-value]`.
No script checks this wording: an absolute "unchanged" claim has no reliable
textual signature, so a validator warning would be noise.

### Grep for census pins the contract shifts, and log CENSUS

**Grep for census pins the contract shifts.** When the contract adds a
migration, a table, a counted file, or moves a file, grep the tree for pins
that count or list it — a table-name census, a migration count `(1..=N)`, a
`[&str; N]` array, an inventory row count — and give each one an owner: the
package that moves it, or a criterion with a test-author owner. The same census runs when the contract moves code from an existing file into a
new one: grep the whole
tree outside the source crates (scripts, `*.tsv` registers, allowlists,
inventories) for the old file's path: a register that classifies files by path
(a seam-file TSV, an allowlist keyed by path) gets an owner who adds the new
file's row, and the suite that pins that register's row count gets an owner
who re-derives the count — a second pin, owned like the first. Also grep for **exact-output
assertions over changed members**: a test that compares the whole result of a
member this contract changes (full-map or collection equality) breaks when the
result gains a key or an item. Recipe per language: Rust
`grep -rnE 'assert_eq!\(.*(<member>)'`, Python `grep -rnE 'assert .*<member>.* == [\[{(]'`,
TS/JS `grep -rnE '<member>.*(toEqual|toStrictEqual|toBe)\('`, C#/Java
`grep -rnE '(Assert\.(Equal|AreEqual)|assertEquals)\(.*<member>'`. Each hit
gets an owner or a criterion before the fan-out, like any other pin. Ask the same
question of the **checkout shape**: which existing suite cases depend on the
shape this contract changes? A contract that adds a worktree-detecting guard
(or any checkout-sensitive behaviour) makes a case that runs past the argument
check behave differently in a secondary worktree than in the main checkout —
and every package worktree IS a secondary worktree; each such case gets an
owner and a fixture (a main-checkout copy) before the fan-out, or the wave
prep will explain a failure the contract never predicted. **A shape change by rule gets a literal-search census, not only compile errors
(#466).** When the contract changes the shape of a string-valued identifier by
rule (work keys, stored names, labels), tests that assert the old literal still
compile, so a build error never names them; they surface one test project at a
time as re-spawns. Put one line in the dossier's `## Contract`,
`SHAPE-CHANGE: <old-shape pattern> -> <new shape>`, grep the whole tree for the
old pattern (`grep -rnE '<pattern>'`, tests, fixtures, scripts, registers), turn
every hit into a `CENSUS:` row (a catch-all row per file for stale literals),
and log `CENSUS-LITERAL: <pattern> — <N> hits, <M> census rows` (or `CENSUS-LITERAL:
none — <pattern searched, zero hits>`). `validate_pipeline.py --pre-fanout`
refuses a contract with a `SHAPE-CHANGE:` line and no `CENSUS-LITERAL:` line.
**A tightened value gets a census of its producers, not only compile errors
(#518).** When the contract tightens what a value may be (a member becomes
validated against a catalogue, a constructor parameter becomes required, a
defaults table gains an entry), a test that builds the old shape may still
compile. Search for the record's constructor calls, or the defaults' member
names, across every test project, integration fakes and expected-defaults
dumps (Python and shell included) included, and turn every producer into a
`CENSUS:` row before the fan-out: a test builder, a fake model, a dump of the
defaults, a test constructing a handler. A run that tightens a member and lists
no integration fake in `CENSUS` has not searched.
Log
`CENSUS: <path>:<line> — <owner>`, or `CENSUS: none — <what was grepped>`,
into `## Build log` before you spawn. **A row whose path is an existing test
file or shell check is a census package, spawned as a plugin agent, never a
general-purpose one:** `unit-test-author` (Python, Rust, C#, TS test files) or
`integration-test-author` (existing shell checks), with `CENSUS:` carrying the
package's rows in its payload (`references/payloads/unit-test-author.md`, "A
census round"), `TEST_PATHS` the existing files and no `CORRECTION`. Both have
`Edit` under Claude Code, so the change is a scoped in-place edit under the same
read and edit confinement; `prepare_wave.py` admits the payload, and
`verify_return.py --census-paths <TEST_PATHS>` checks the return stayed on the
census paths and was not a whole-file rewrite. The same package form serves a
Phase 6 correction on a long test file.
`validate_pipeline.py --pre-fanout` requires the `CENSUS:` line. In local mode — the dossier validated from the main
checkout while the stubs live in the package worktree X — pass
`--worktree <X>` (or set the dossier's `worktree:` front-matter field, which
the check resolves itself); a package that owns a directory is walked whole,
every file under it.

### Tag the lines that hold over a whole domain as properties (Eight diffs, item 8)

8. **Tag the lines that hold over a whole domain as properties.** When
   `PROPERTY_LIBRARY` (which `compose_payloads.py` fills from the
   repository's manifests) names a library, give every line whose promise
   the docstring states for a whole domain a `[property: <kind>]` tag and a
   `domain:` its generator builds by construction, boundaries included
   (`references/formats/dossier.md` § "The observability checklist" holds the
   five kinds and what each must not do). Tag only what the docstring
   states: a property the contract does not justify fails a correct body,
   and about one generated property in five is unsound even from frontier
   models (Vikram et al., 79% sound). Never tag a line whose only property
   would recompute the expected value the way the body does. The line keeps
   its concrete example. With no library, tag nothing. `check_payload.py`
   warns on an unknown kind, a missing `domain:`, or a tag with no library.

### A contract the plan already reviewed: re-review only what changed

**A contract `/plan` already reviewed — re-review only what changed.** When
`.discovery/contract-review/<ID>.json` exists beside the live dossier (`/plan`
Phase 6 wrote it, with one hash per contract member), compare first:

```bash
python3 ${PLUGIN_ROOT}/scripts/plan_flow.py hashes --root <live dossier's root> --dossier <ID> --diff
```

- `re-review: none` → no member changed since the plan's review. Do not
  spawn; record the current contract commit as reviewed
  (`dossier_edit.py set <dossier> 'contract_review=<contract commit SHA>'`)
  and log `CONTRACT-REVIEW: none changed since the plan review — <the
  json's sha> (plan_flow.py hashes)`, so the skip stays auditable.
- `re-review: <members>` → spawn `contract-reviewer` as below with
  `CONTRACT_PATHS` limited to the files that hold those members and the
  members that use them; the unchanged rest keeps the plan's verdict. Log
  `CONTRACT-REVIEW: partial — <members>`.
- `re-review: all (no stored hashes)`, or no json → the full review below.

After the review, run `plan_flow.py hashes … --write` so the next change is
compared against this one.

### Settling the contract-reviewer's return: disagreements, AMBIGUITY and DEFECT lines

- **Every disagreement is a contract defect caught pre-fan-out.** A promise
  it found that you missed is a `GAP:` you would have paid per author; a
  promise you listed that it cannot derive from the docstrings is a
  checklist line no blind author can satisfy. Fix the contract and your
  checklist together, commit on `X`, and only then fan out. Land the revised
  stubs in the dossier with `dossier_edit.py sync-contract <dossier> --root X`
  (exit 0 means every member equals its file's), not by hand-copying blocks.
- **Its `AMBIGUITY:` lines you settle yourself, by citation or by ruling;
  only a product-level rest goes to the owner.** The reviewer is blind to the
  dossier, so many of its ambiguities are already decided there: the
  approach, a criterion, a recorded ruling. For each, quote the dossier
  sentence that picks one reading, write that reading into the docstring,
  and log `AMBIGUITY-SETTLED: <member> — <reading> (dossier § <section>)`.
  That is not you ruling on your own sentence: the dossier is the referee,
  and the owner already approved it. A reading no dossier sentence picks you
  rule provisionally, **whatever its `IMPACT:` mark**: take the recommended
  reading (the simpler one, the repo's convention, the one the neighbouring
  code already follows, or the cheaper to reverse), write it into the
  docstring, log `PROVISIONAL-RULING: <member>, <reading and why>, <how to
  reverse>` in `## Build log` (add `IMPACT high` when the reviewer marked
  it), and list it under "Rulings to review" in the PR description (Phase 9),
  the `IMPACT high` ones first. The reviewer's `IMPACT: high` means a blind
  test author will observe the difference, which is why the docstring must
  name one reading before the fan-out; it does not make the choice the
  owner's. Internal semantics stay yours however costly they are to change
  later: counts and snapshots, wake-up and retry behaviour, clamping versus
  refusing a bad internal setting, call order, test doubles, how tests
  assert errors, library or broker defaults. Ask the owner only when the
  two readings differ in what the product does for someone outside the code
  (a user-visible behaviour; an event, API, file or stored format another
  system or team consumes; a cost, security or data-retention choice) and no
  dossier sentence decides it. Put only those readings to the owner, as one
  `--kind product` question, and write the winner into the docstring before
  the fan-out; keep working on what does not depend on it. Never ask the
  owner to confirm or review a list of readings you already ruled
  (`references/owner-questions.md`, "The ask policy").
- **Its `DEFECT:` lines you fix immediately** — unstated visibility,
  unmeasurable words, criteria the contract cannot express.

### Visual review, steps 2 to 6: author, validate, intent, owner question, record

2. Spawn `diagram-author` (payload file in `.agent-staging/payloads/`): `DOSSIER_ID`,
   the dossier's `## Problem` and `## Approach`, the contract, `WORK_DIR`,
   `OUT_DIR`, `SCRIPTS_DIR`, `REPO`, `STEP_ROUTING: all` only when
   `ASD_DRIVER_VR_PROMOTE_PREVIEW` is `shadow` or `on` (omit the field otherwise: the author then
   writes `to` and `call_kind` only for `Diagrams new:` flows; with it, on every added or changed
   L1 step, so the shadow run below builds real drafts), and `DIAGRAMS_NEW` (the stems of the
   `Diagrams new:` line, one `<stem> — <what>` per line, verbatim; `none` when the dossier has no such
   line), and `DECISION: yes|no`, the branch rule's answer
   (`python3 ${PLUGIN_ROOT}/scripts/validate_pipeline.py --root <repo> --dossier <ID> --branch-rule`: yes when the
   dossier's `## Acceptance criteria` name a condition, an error end or a retry; the lint compares it). Lint it first:
   `python3 ${PLUGIN_ROOT}/scripts/check_payload.py <file> --kind diagram-author --host <claude|opencode> --live-dossier <live dossier>`
   (it refuses a missing field, a `DIAGRAMS_NEW` that differs from the dossier's `Diagrams new:` stems or
   names a stem not starting `seq-`, `c1-` or `c4-`, an `OUT_DIR` outside `REPO`, and a path that does not
   exist); a repair re-spawn adds `ERRORS` and is linted the same way. It writes `l0.json`,
   `l1.json` and `l2.json` in `OUT_DIR`, with `DECISION: yes` also `decision.json` (the decision flow: start,
   steps, diamonds, ends; validated like the others; never promoted), and for every `c1-*` and `c4-*` stem a
   draft `OUT_DIR/arch/<stem>.json`; it only names and groups, and marks what
   no stub proves `inferred`.
3. Validate each file yourself:
   `python3 scripts/vr_graph.py validate <OUT_DIR>/l<N>.json --root <repo>`
   (budgets L0 15, L1 12, L2 12; `decision.json` with `--l1 <OUT_DIR>/l1.json`, 14 nodes and 5 decisions). On a defect re-spawn the agent with the
   issue list in `ERRORS`; **at most 2 repair attempts**, then stop, log the
   open issues in `## Build log` and report. A graph that fails validation
   creates **no** owner question.
   When the dossier's `Diagrams new:` line lists a stem, the author was told
   (it reads the line in `APPROACH`) to write that flow into `l1.json` with
   `new_stem` on the flow and `to` and `call_kind` on every step; `validate`
   rejects a `new_stem` flow whose steps lack them. A `c1-*` or `c4-*` stem
   has its own draft: validate it too,
   `python3 scripts/vr_graph.py validate <OUT_DIR>/arch/<stem>.json --root <repo>`.
   Then prove the promotion works before the owner looks, per stem:
   `python3 scripts/vr_promote.py --dossier-id <ID> --stem <stem> --repo <repo> --preview`
   (for a `seq-*` stem it writes the draft `.discovery/visual-review/<ID>/arch/<stem>.json`
   from the `l1.json` flow; for `c1-*` and `c4-*` it validates the author's
   draft; nothing is written under `docs/`). The dashboard shows each draft as
   a tab of the fourth part, "Architecture impact", with the same verdicts as
   L0, L1 and L2. A non-zero exit is a defect in the draft or in `l1.json`:
   re-spawn the author with the output in `ERRORS`, same two-attempt limit.
   When `ASD_DRIVER_VR_PROMOTE_PREVIEW` is `shadow`: run
   `python3 "${PLUGIN_ROOT}/scripts/vr_promote.py" --shadow-all --dossier-id <ID> --repo <repo>`
   once; read nothing from its output, it only records evidence. (The author got `STEP_ROUTING: all`
   for this reason: without `to` and `call_kind` on the steps of a flow with no `Diagrams new:` stem
   every shadow build fails by construction.)
   The owner's verdicts are recorded in `review.json` against the hash of the
   file they saw; any later edit of `l1.json` or of a draft (a contract
   revision that re-runs the author) changes that hash, and Phase 9's
   promotion then requires the owner's fresh approval of the new file.
4. Write `<OUT_DIR>/intent.md` yourself: the owner's context for the review,
   shown on the canvas as the **Intent** tab before L0. 60 to 150 words of
   plain prose from the dossier's Problem and Approach, for a reader who has
   not read the dossier: what the change sets out to accomplish and why (the
   problem in a sentence or two), what changes and for whom, and what stays
   the same or is out of scope. Short `- ` bullets are fine; no code
   identifiers unless the change is about one, no evidence anchors, no
   contract detail. Rewrite it whenever a contract revision changes the
   approach.
5. Ask the owner ONE product question with
   `ask_owner.py ask --kind product --topic visual-review`
   (`references/owner-questions.md`). The `--topic` marker is what makes it
   the visual-review question: the dashboard canvas and the answer path
   accept only a question that carries it (the two option labels below are a
   secondary guard), so a question without it gets the ordinary buttons: "Does this picture match what the
   feature should do?", options `A|Approved|fan-out starts` and
   `B|Changes requested|back to the contract`, the dashboard view of the
   three files (and the Decisions tab when `decision.json` exists) linked in the question text, `--blocks` the Phase 4 fan-out.
   Then `ask_owner.py wait`; the run waits for the answer, with no time-boxed
   default (OQ-08 = A). The owner reviews L0, L1, L2, the Decisions tab when `decision.json` exists, and every architecture
   tab; the answer `A` needs every element of all of them OK. The owner gives a verdict per element on the
   dashboard canvas (OK, Question, Change, Remove from scope) and sends the
   review in one action (or presses **Approve all**, which marks every element
   OK and answers `A` in one click, refused while any element is flagged; the
   question card's own Approved option does the same): the
   answer is `A` when every element is OK, else `B`
   with one line per flagged element (verdict, diagram, element id, comment)
   and the elements not yet looked at. A Question verdict reaches you as a
   follow-up (`FOLLOWUP`, the question stays open; answer it with
   `ask_owner.py reply`).
   When the answer is `B`, read
   `.discovery/visual-review/<ID>/review.json` (schema
   `references/visual-review/review.schema.json`) for the element ids and
   comments: each line's `anchor.diagram` and `anchor.element` name the
   element, `verdict` is `change`, `remove` (out of scope) or `question`,
   and a line with a different `anchor.graph_hash` than the file now has was
   made on an older version of the picture. These ids and comments are the
   input of the contract revision below.
   `anchor.diagram` of an architecture element reads `arch:<stem>`.
   When the answer is `A` and the dossier's `Diagrams new:` line lists
   stems (`seq-*`, `c1-*` and `c4-*` alike), first run per stem
   `python3 scripts/vr_promote.py --dossier-id <ID> --stem <stem> --repo <repo> --check-approval`
   (the approval rule only: no writes). An `A` typed as a chat answer has no
   per-element `ok` lines in `review.json`, and Phase 9 would refuse it hours
   later. On exit 1 do **not** record `VISUAL-REVIEW: approved`: ask the owner
   again (`ask_owner.py`, same topic) naming the element ids it printed, who
   `ok` them on the dashboard canvas; repeat until it exits 0.
6. Record the outcome as a `## Build log` line, exactly one of:

   ```
   VISUAL-REVIEW: approved — L0 <n>, L1 <n>, L2 <n>, decision <n>, arch <stem> <n>, arch <stem> <n> (<dashboard link>)
   VISUAL-REVIEW: changes requested — <element ids of the review.json lines with verdict change or remove>
   VISUAL-REVIEW: skipped — <reason>   (step 1 only: unsupported repository, that is a language with no extractor, or the owner said go on)
   ```

   The counts are the node counts of the three files, then `decision <n>` when `decision.json` exists
   (required whenever the branch rule says yes; when its words are no condition of the changed behaviour and the
   author drew none, record `decision none — <reason>` instead), then one
   `arch <stem> <n>` entry per architecture draft in `arch/` (its node count;
   none, and no comma, when the dossier lists no `Diagrams new:` stems).
   `validate_pipeline.py --dossier <ID> --pre-fanout` checks them, the three
   files, `decision.json` (valid, its count equal to the `decision <n>` entry, every node `ok` in `review.json` on
   its current hash; missing while the branch rule says yes is a defect unless the line records `decision none — <reason>`;
   no file and no branches: unchanged), every draft (each `Diagrams new:` stem needs its draft, each draft
   its entry; the PR's `Diagram <stem> not added:` escape does not excuse a
   missing draft) and the last line (the gate holds for a dossier with the `visual_review:` field that
   step 1 sets; a build that never ran this step is not flagged; a `skipped — <reason>` last line, or a
   `visual_review` value starting `skipped`, passes with no view files, `skipped-error` does not).
   **`changes requested` goes back to the contract revision above, never
   forward:** fix the contract, re-run the contract review, then run this
   step again and record a new line. Fan-out waits for `approved`.
