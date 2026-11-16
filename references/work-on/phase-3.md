<!-- /work-on, Phase 3. Read at the start of this phase; the rules that hold
     in every phase stay in primary-agents/work-on.md. -->

## Phase 3 — Mechanical check before the fan-out

### The contract-reviewer has no size gate, and its skip must be auditable

**`contract-reviewer` has no size gate, and its skip must still be
auditable** (the why is in Phase 3). Record exactly one of these in the
dossier front matter before the fan-out, every run:

```
dossier_edit.py set <dossier> 'contract_review=<contract commit SHA the review read>'
dossier_edit.py set <dossier> 'contract_review=skipped — <reason>'
```

The SHA is the contract commit the reviewer's stubs came from. If the contract
changes afterwards the SHA is stale, and `scripts/validate_pipeline.py
--dossier <ID> --pre-fanout` refuses until the review runs again and the new
SHA is recorded. It also refuses without the `hooks:` field (Phase 2), the
`PAYLOAD-LINT:` line and the `ADMISSION:` line (Phase 4), and
the `CENSUS:` and `CHECKLIST-IDS:` lines (Phase 3). A
skip stays possible; a *silent* skip does not.

### Rules that hold in this phase

- **Owned paths are disjoint**, checked mechanically before the fan-out. A
  conflict between two packages is a defect in the package table. Every
  implementer report ends with `TOUCHED_BEYOND`; a path outside `OWNED_PATHS`
  it did not list is an implementer defect, and two are never accepted
  however justified: another package's owned paths, and the contract files.
  (Phase 3, Phase 5)

- **The checks check the author.** You write the contract, derive the
  checklist and arbitrate the failures, so each is verified by a pass that did
  not come from the same place: `contract-reviewer` on the stubs alone before
  the fan-out, the mutation check on the checklist after green (a survivor is
  a weak oracle, routed like a `GAP:`). A test author's world is stamped with
  `CONTRACT_HASH` at spawn, and a contract that changed underneath it is
  invalidated on the hash, without judgment. (Phase 3, Phase 6)

Run `python3 ${PLUGIN_ROOT}/scripts/validate_pipeline.py --dossier <ID>`.
It checks the work packages' owned paths are **disjoint**, the `## Contract`
section carries documentation comments and no bodies, and every criterion names
a concrete observable and carries its `(owner: <test path>; env: <where it
runs>)` annotation.

Fix every DEFECT before you spawn anything. Overlapping owned paths are the one
failure mode in this design that corrupts work silently: two agents write the
same file on two branches, one merge wins, and the loser's tests then fail for a
reason nobody can diagnose.

**Then map every acceptance criterion to the test file that will own it**, and
write the map into `## Build log` before you spawn. A criterion with no owner
is a spawn defect, not a Phase 5 finding: both Phase 5 diffs (the unit
author's map against `PROMISE_CHECKLIST`, the integration author's against the
*criteria*) run after the fan-out, where the cheapest fix is already a
re-spawn, and a criterion the integration author disclaims and the contract
under-specifies is checked by neither. `/plan` wrote each criterion's
`(owner: …; env: …)` annotation from the package table it built; re-check it
against the table you are spawning from, and treat the **env** half as a hard
flag: a criterion whose env names services this machine cannot provide — a
live database, a deployed host — is a `/plan`-shaped defect. Fix it now: name
the substitute environment in the criterion (the VM, the container, the CI
job), or send the dossier back to `/plan` with a note.

**Derive `unit-test-author`'s promise checklist mechanically, now — not by
hand at Phase 5.** The observability pass you already owe every documented
member (`references/formats/dossier.md` § "The observability checklist") is the
derivation: keep its output. One line per member per category the docstring
actually states — return meaning, named error, order, empty case, invalid
case, concurrency semantics, and the derive-set each assertion shape needs —
becomes `PROMISE_CHECKLIST`. Write it as the artefact
`python3 scripts/build_records.py checklist write <dossier>` (one
`member — category: promise` line per promise, from stdin; it lands in
`.discovery/checklists/<ID>.md`) before you spawn; the gate checks that the
file exists and covers the contract's members, and its failure names the
missing IDs. Pass it verbatim in the unit author's
payload beside `CONTRACT`. A promise missing from a test traces to a thin
checklist: a Phase 3 defect to fix in the checklist and the contract
together, never a reason to ask a blind agent to re-read what its payload
never named. A line whose expected outcome is negative (refused, `None`,
`Ok(None)`, no event) carries an `input:` that passes every other guard the
contract documents on that path, so a mutant that deletes or weakens the one
guard under test turns that test red. `check_payload.py` warns on a negative
line with no `input:` before the fan-out.

**Match the checklist's ids to the repo's own coverage script, before
deriving it.** When the target repo owns a promise-coverage script, read its
promise-line regex first and derive `PROMISE_CHECKLIST`'s ids in that
grammar, not a free-form one. Log `CHECKLIST-IDS: <script> parsed <N>/<N>
lines`, or `CHECKLIST-IDS: none — no coverage script`, into `## Build log`
before you spawn; `validate_pipeline.py --pre-fanout` requires the line and
refuses a `parsed 0/` result — that means the ids don't match the grammar,
so re-derive them in it, never fall back to a Phase 5 manual diff.

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
prep will explain a failure the contract never predicted. Log
`CENSUS: <path>:<line> — <owner>`, or `CENSUS: none — <what was grepped>`,
into `## Build log` before you spawn; `validate_pipeline.py --pre-fanout`
requires the line. In local mode — the dossier validated from the main
checkout while the stubs live in the package worktree X — pass
`--worktree <X>` (or set the dossier's `worktree:` front-matter field, which
the check resolves itself); a package that owns a directory is walked whole,
every file under it.

**Every prose promise in `## Contract` maps to a criterion.** Each sentence of
the contract's prose (outside code blocks) that promises observable behaviour
of a binary, a wiring step, an environment variable, a router mount or a
deployment input names a token: an ALL_CAPS env var, a route path in backticks, a `bin/x` path or
"the `x` binary". Some acceptance criterion must contain that same token; a
promise with none is a pre-fan-out gap — add the criterion before you
spawn, or state in the build log why none is needed. Log `PROSE-PROMISES: <N>, <M> without a criterion` into `## Build
log`; `validate_pipeline.py --pre-fanout` runs the same check on the dossier
and lists each uncovered token as a WARNING; it does not block the fan-out.

**A frozen count or oracle carries its derivation command beside the
number.** Write `12 tables (grep -c 'CREATE TABLE' migrations/*.sql)`, never a
bare `12`. Where the command and the frozen number disagree, the mechanism
wins: record an erratum in `## Build log` and fix the number; no test is bent
to match a stale total. An oracle never derives ground truth by running the
subject afterwards, because it then asserts whatever the subject does; it must
be constructible without invoking the subject.

**Seven diffs over the checklist, before anything spawns:**

1. **Slice it per surface, and make the slices add up.** Each unit author gets
   exactly the lines its surface can observe — but every line lands with
   exactly one author, and the slices reassembled must equal the whole. A line
   nobody owns is a promise no test will assert.
2. **Diff it against the acceptance criteria.** Every criterion must trace to
   at least one checklist line *at least as strong as the criterion*. A
   checklist line that cites a criterion while observing less than it passes
   the Phase 5 coverage gate and ships an unobserved criterion — the most
   expensive defect this gate can hide, because everything reports green.
3. **Audit the seams it names.** Every member a checklist line exercises must
   appear in the contract with its **visibility stated** — `pub`, `public`,
   exported. A blind author compiles against the contract text alone; a seam
   with unstated visibility is a compile error it cannot resolve, and a
   guaranteed `GAP:` return or row-3 arbitration.
4. **Diff it against the tests that already exist.** On a surface that already
   has a test file, grep the tree for a test that observes each line; where
   one does, cite the line on that test in place, drop it from the new
   author's slice, and name in the payload the test names it must not reuse.
   A blind author told to cover a promise an existing test owns writes a
   second test for it — the recorded case duplicated half a file under three
   identical names and passed the coverage gate throughout, because the gate
   asks whether a line has an owner and cannot ask whether it has two.
5. **Stage every non-contract surface it names.** Collect the types a line's
   assertion must construct or call that live outside the contract files — a
   repo grant, an id type, a port helper, a credential constructor — and
   stage their signature surface under `.agent-staging/contract-support/` in
   `X` at fan-out, named in the payload as `SUPPORT_PATHS`
   (`references/payloads/unit-test-author.md`). A blind author cannot invent `RepoGrant::mint`
   from a docstring that names it: the recorded run paid three `GAP:` round
   trips before the surface was staged mid-flight. `check_payload.py` warns
   when a checklist line names a type found in neither `CONTRACT` nor
   `SUPPORT_PATHS`.
6. **Check the derive-sets.** Every bound a checklist line names — `Debug`
   for `expect_err`, `PartialEq` for equality, `Clone` for a second move —
   is on the stub's type, or `FIXTURES` names the house idiom to use instead.
   The stub's derives compile without any of them, so nothing but this diff
   catches it before the Phase 5 commit fails to compile a blind author's
   file (`references/formats/dossier.md` § "The observability checklist").
7. **Tag every line that states the whole result.** A line whose text
   states the whole value — "gives exactly", "gives `[A, B]`", a full list
   — carries `[whole-value]` and is asserted as one equality over the whole
   value; a line that states membership only carries `[member]` and is
   asserted on the whole element (`references/formats/dossier.md` § "The
   observability checklist"). An untagged whole-result line reads as
   description to a blind author, which then writes a length check plus an
   `any(...)` — green on the correct body, and found only by the Phase 6
   mutation check: six survivors from three authors in one recorded build,
   every one on such a line, three delete-first rewrite rounds to fix.
   `check_payload.py` warns on an untagged "exactly" line.

**The independent contract review — spawn `contract-reviewer` now, before the
fan-out.** You wrote the contract and derived the checklist; a defect in
either is invisible to you for the same reason. `contract-reviewer` never
sees the dossier, the plan, or your checklist — its payload is the
materialised contract files (`CONTRACT_PATHS`), the acceptance criteria
verbatim, and the observability-checklist rules (`CHECKLIST_RULES`), and it
derives its **own** `PROMISE_CHECKLIST` from the stubs alone. **When a stub
extends a base class that is not in the contract, stage that base's template
method in `BASE_TEMPLATES`** (`path:start-end`, the template method and what
it runs before the hook): the reviewer checks every edge promise on the
subclass against it. One build promised an empty output map for zero formats
and single handling of duplicate targets, but the sealed base raised and
refused first; two arbitrations, two re-spawns and the last arbitration round
paid for it. When it
returns, diff its checklist against yours line by line:

- **Every disagreement is a contract defect caught pre-fan-out.** A promise
  it found that you missed is a `GAP:` you would have paid per author; a
  promise you listed that it cannot derive from the docstrings is a
  checklist line no blind author can satisfy. Fix the contract and your
  checklist together, commit on `X`, and only then fan out. Land the revised
  stubs in the dossier with `dossier_edit.py sync-contract <dossier> --root X`
  (exit 0 means every member equals its file's), not by hand-copying blocks.
- **Its `AMBIGUITY:` lines the dossier settles, you settle by citation; a
  low-impact rest you rule provisionally; only a high-impact rest goes to the
  owner.** The reviewer is blind to the dossier, so many of its ambiguities
  are already decided there: the approach, a criterion, a recorded ruling. For
  each, quote the dossier sentence that picks one reading, write that reading
  into the docstring, and log `AMBIGUITY-SETTLED: <member> — <reading>
  (dossier § <section>)`. That is not you ruling on your own sentence: the
  dossier is the referee, and the owner already approved it. A reading no
  dossier sentence picks, whose reviewer-marked `IMPACT:` is `low`, is a
  provisional ruling: take the recommended reading (the simpler one, the
  repo's convention, or the cheaper to reverse), write it into the docstring,
  log `PROVISIONAL-RULING: <member>, <reading and why>, <how to reverse>` in
  `## Build log`, and list it under "Rulings to review" in the PR description
  (Phase 9). Only a reading no dossier sentence picks and whose impact is
  `high`, or that decides what the feature does, is the row-3 arbitration you
  are the wrong party to rule on — you wrote the sentence. Put those to the
  owner as one question (`--kind design` or `--kind product`), and write the
  winner into the docstring before the fan-out; keep working on what does not
  depend on it. Never ask the owner to approve readings the dossier settles
  or that are low-impact (`references/owner-questions.md`, "The ask policy").
- **Its `DEFECT:` lines you fix immediately** — unstated visibility,
  unmeasurable words, criteria the contract cannot express.

This review runs on every build; it is the counterweight to self-refereeing,
and it is cheap next to one blind re-spawn.

**Run the repository's own text gates over the materialised contract, now.**
Every check that reads source text — lint, spelling, policy-value checks —
runs in CI over your contract files eventually. Run them here, over `X`,
before the fan-out: a gate that first fails after the merge fails with no
test output naming the cause, and you pay a full review round to find what a
ten-second check would have said.

**Decide the test files themselves, not just their owners — no test author can
add one.** An author writes exactly the paths you name and nothing else:
`unit-test-author` reads only what its permission map admits — nothing at
all under Claude Code — so it cannot
see that the one file you gave it is becoming a thousand lines over four
unrelated classes, and `integration-test-author` owns only the paths you
named. The split you hand out is the split you get. Plan it before you spawn:

- **One test file per contract surface** — per class, per module, per protocol —
  and one `unit-test-author` per surface to own it. Two surfaces pointed at one
  path produce a file carrying tests for unrelated types, and that file is
  expensive twice over: nobody can read it, and a `GAP:` on one surface re-spawns
  an author that rewrites the other surface's tests along with its own.
- **A surface that needs more than one file must be given more than one path.**
  A wide error enum, a table-driven case set, fixtures worth isolating on their
  own — name every path up front in that author's payload. Three named paths
  produce three files; one named path produces one long file, and the author had
  no way to know you wanted otherwise.
- **Keep every blind-authored file small — one flow, one surface, or a few
  criteria.** A blind author composes with no formatter and no compiler, in
  messages that race the runtime's stream timeout as they grow; the smaller
  the file, the sooner it lands and the less one `GAP:`, vacuous test, or
  row-3 re-spawn throws away.

Size is your call to make here because it is the only place it can be made. The
same holds for `integration-test-author`: split by flow, not by dossier, when a
dossier describes more than one.

The unit test author never sees the criteria — its payload and its read map
are its whole world. So a criterion that states a scale the contract does not
(a count, a size, a concurrency level) must travel into that author's payload
as an explicit strength requirement — in `FIXTURES` or `TEST_FRAMEWORK`,
phrased as a property of the contract surface, never as a criterion number —
or the contract itself must be fixed to state the scale.

**The visual review — the last step of Phase 3, before any Phase 4 spawn; every
build, no size gate.** The owner sees what the change does to the system
before an implementer starts. It is its own step: the `contract-reviewer`
payload holds no diagram file and no dossier text from this step.

1. Mark the dossier: `dossier_edit.py set <dossier> 'visual_review=required'`
   (this switches the gate on for this dossier). Then run `python3 scripts/vr_prepare.py <repo> <ID> --base <baseline_commit> --head X`
   (`X` = the contract commit). It writes `.discovery/visual-review/<ID>/work/`
   and prints a compact JSON summary. A non-zero exit stops the step: log
   `VISUAL-REVIEW: skipped-error — <step the JSON names>` as a build-log
   line, report it, and stop; never invent the facts by hand.
2. Spawn `diagram-author` (payload file, linted like every payload): `DOSSIER_ID`,
   the dossier's `## Problem` and `## Approach`, the contract, `WORK_DIR`,
   `OUT_DIR`, `SCRIPTS_DIR`, `REPO`. It writes `l0.json`, `l1.json` and `l2.json`
   in `OUT_DIR`; it only names and groups, and marks what no stub proves
   `inferred`.
3. Validate each file yourself:
   `python3 scripts/vr_graph.py validate <OUT_DIR>/l<N>.json --root <repo>`
   (budgets L0 15, L1 12, L2 12). On a defect re-spawn the agent with the
   issue list in `ERRORS`; **at most 2 repair attempts**, then stop, log the
   open issues in `## Build log` and report. A graph that fails validation
   creates **no** owner question.
4. Ask the owner ONE product question with
   `ask_owner.py ask --kind product --topic visual-review`
   (`references/owner-questions.md`). The `--topic` marker is what makes it
   the visual-review question: the dashboard canvas and the answer path
   accept only a question that carries it (the two option labels below are a
   secondary guard), so a question without it gets the ordinary buttons: "Does this picture match what the
   feature should do?", options `A|Approved|fan-out starts` and
   `B|Changes requested|back to the contract`, the dashboard view of the
   three files linked in the question text, `--blocks` the Phase 4 fan-out.
   Then `ask_owner.py wait`; the run waits for the answer, with no time-boxed
   default (OQ-08 = A). The owner gives a verdict per element on the
   dashboard canvas (OK, Question, Change, Remove from scope) and sends the
   review in one action: the answer is `A` when every element is OK, else `B`
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
5. Record the outcome as a `## Build log` line, exactly one of:

   ```
   VISUAL-REVIEW: approved — L0 <n>, L1 <n>, L2 <n> (<dashboard link>)
   VISUAL-REVIEW: changes requested — <element ids of the review.json lines with verdict change or remove>
   ```

   The counts are the node counts of the three files; `validate_pipeline.py
   --dossier <ID> --pre-fanout` checks them, the three files and the last
   line (the gate holds for a dossier with the `visual_review:` field that
   step 1 sets; a build that never ran this step is not flagged).
   **`changes requested` goes back to the contract revision above, never
   forward:** fix the contract, re-run the contract review, then run this
   step again and record a new line. Fan-out waits for `approved`.

**Driver (`ASD_DRIVER_PRE_FANOUT_CHECK` is `on` or `shadow`).** `python3 scripts/pre_fanout_check.py --dossier ID` runs the pre-fan-out mechanics (validation, receipts, criteria owner/env map, census grep, frozen counts, gates, slices vs checklist) and prints evidence only; you rule on it. Exit 2 means a surprise: do the step by hand. In `shadow`, do the manual steps exactly as today, then run the driver once afterwards with the same arguments (no `--log`) and ignore its output for every decision; it saves evidence to `.git/asd-drivers/driver-shadow/` for the rollout report. Unset or `off` means ignore this paragraph.
