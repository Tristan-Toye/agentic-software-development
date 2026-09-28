<!-- /work-on, Phase 3. Read at the start of this phase; the rules that hold
     in every phase stay in primary-agents/work-on.md. -->

## Phase 3 — Mechanical check before the fan-out

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
becomes `PROMISE_CHECKLIST`. Write it into
`## Build log` before you spawn and pass it verbatim in the unit author's
payload beside `CONTRACT`. A promise missing from a test traces to a thin
checklist: a Phase 3 defect to fix in the checklist and the contract
together, never a reason to ask a blind agent to re-read what its payload
never named.

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
package that moves it, or a criterion with a test-author owner. Log
`CENSUS: <path>:<line> — <owner>`, or `CENSUS: none — <what was grepped>`,
into `## Build log` before you spawn; `validate_pipeline.py --pre-fanout`
requires the line.

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
derives its **own** `PROMISE_CHECKLIST` from the stubs alone. When it
returns, diff its checklist against yours line by line:

- **Every disagreement is a contract defect caught pre-fan-out.** A promise
  it found that you missed is a `GAP:` you would have paid per author; a
  promise you listed that it cannot derive from the docstrings is a
  checklist line no blind author can satisfy. Fix the contract and your
  checklist together, commit on `X`, and only then fan out.
- **Its `AMBIGUITY:` lines go to the user as one question.** A docstring
  with two defensible readings, quoted with both, is the row-3 arbitration
  you are the wrong party to rule on — you wrote the sentence. Let the user
  pick, and write the winner into the docstring before the fan-out.
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
