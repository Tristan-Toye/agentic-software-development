<!-- /plan, Phase 4. Read at the start of this phase; the rules that hold
     in every phase stay in primary-agents/plan.md. -->

## Phase 4 — Write the dossier

Write `${RUN_ROOT}/.discovery/dossiers/W-NNN-<slug>.md` with the ID claimed in
Phase 1, the front matter, `flow: 2`, and the seven sections in order:
`## Problem`, `## Approach`, `## Decisions`, `## Contract`, `## Work packages`,
`## Acceptance criteria`, `## Build log`. **Every section is mandatory for
every plan**, whatever its size (a kanban dossier omits `## Decisions` when it has no choice to record). A provisional number is settled at Phase 8,
which re-checks it against the base tip before the push.

Kanban: when `python3 ${PLUGIN_ROOT}/scripts/delivery_flows.py --root ${RUN_ROOT}`
prints `"kanban": true`, read `${PLUGIN_ROOT}/references/plan/kanban.md`
("Phase 4") first: a small change gets a dossier sized to it.

`## Decisions` is the Phase 3 register, copied with its rulings. **Apply every
FIX row** before you write the contract: the fix lands in `## Contract` or
`## Work packages`, and the row's `Trigger or where` names it in backticks.

End `## Approach` with one line, `Diagrams affected: <file stems>` (the
`docs/architecture/` files, stem without extension, that this change makes
stale: `c4-classes-ports, seq-boot`) or `Diagrams affected: none`; a repo
without `docs/architecture/` gets `none`. Then one more line,
`Diagrams new: <proposed stem> — <one line: the flow>` (several
comma-separated: `seq-x — flow a, c1-ext — the new system`) or
`Diagrams new: none`. It lists a diagram the change must **add**: a flow that
crosses crates, processes or containers and that no existing `seq-*` diagram
shows (stem `seq-<flow>`), or a new external system (stem `c1-<name>`, an
addition to the c1 context). A change inside one component, or a flow an
existing diagram already shows (that one goes on `Diagrams affected:`), gets
`none`; so does a repo without `docs/architecture/`. The plan reviewer checks
both lines against the change, and `/work-on` Phase 9 creates every listed
diagram and gates on both (`validate_pipeline.py --diagram-gate`).

All seven matter. Two of them get extra care because **their errors are invisible
until they are expensive** — the fan-out consumes them mechanically, so a defect
there does not read as a defect, it reads as an unexplainable test failure:

| Section | Read by | If you get it wrong |
|---|---|---|
| `## Contract` | 3 agents, concurrently | **silent** — all three build against a broken spec, and the failure has no discoverable cause |
| `## Work packages` | the fan-out and the merge | **silent** — two agents write one path, one merge wins, the other's work is lost |
| `## Acceptance criteria` | the integration author, and the final gate | loud — a `GAP:` return, or a criterion no test can observe |
| `## Problem`, `## Approach`, `## Decisions` | a human, the owner, and you at arbitration time | recoverable — somebody notices and asks |
| `## Build log` | nobody yet; `/work-on` writes it | leave it empty |

### `## Contract` — the most important thing you write today

**Read the contract-craft rules before you write a docstring** — every run:

1. `${PLUGIN_ROOT}/references/formats/dossier.md` § "The observability checklist"
   — return meaning, named errors, order, the empty case, the invalid case,
   concurrency semantics, and the unmeasurable words that are never promises.
2. **This repo's own rules file** — the rules earlier runs paid for, each one
   there because an ambiguity in a contract cost a double re-spawn (`/work-on`
   Phase 8b). Look where the repo keeps them (`docs/adr/RULES.md`, a rules
   section in `CLAUDE.md`, a conventions doc); there may be none yet. Reading
   them is the whole return on that cost, and a repo rule outranks
   `skills/standards/engineering-standards.md`.

Then write the signatures and the documentation comments, **with no bodies**.
Three agents will build against this text concurrently and blind: a unit test
author that can read nothing else, an integration test author, and an implementer
that will never see the tests. The contract is the only thing they share.
- **Define every term the signatures lean on** ("the track's rows", "the
  window start") in a `### Definitions` list under `## Contract`, one
  `- **term** — meaning` per term. `/work-on` copies each into the stubs'
  documentation; prose defined anywhere else never reaches them.
- **Never name the dossier's own id in `## Contract`.** `/work-on` copies the
  contract into source files, so the id would land in the contract commit;
  validation reports it as a DEFECT. Describe the behaviour, or cite the Jira
  key.
- **Never cite a comment that starts with `(inferred)` as the contract.** It is
  a guess about the code. A member the contract relies on needs an authored
  comment: you write it, and it replaces the marker.
- **A new event or typed-payload variant states, in its own documentation,
  every member it carries and every member it leaves empty.**
- Use the language's own documentation form — XML doc comments for C#, rustdoc
  for Rust, docstrings for Python, TSDoc for TypeScript, javadoc for Java.
- State each promise so a test can observe it: the return value, the error
  raised, the ordering guaranteed, the behaviour for empty input, the
  behaviour for invalid input. Write the concrete expected values where you
  can.
- **State the visibility of every member a test must reach** — `pub`,
  `public`, exported. A blind test author compiles against this text alone:
  a seam named in a promise but never made reachable is a compile error, and
  the arbitration will rule it your ambiguity.
- **Error and edge behaviour is mandatory for every public member and every
  member another package calls**: the error it raises or returns, the empty
  case, the invalid case. A private member may leave it out — and a private
  member that later becomes public meets the same standard, through a
  contract update.
- **Delete any promise no test can observe.** `Efficiently drains the queue` is
  not a promise. `Drains at most `batchSize` items per call, oldest first` is.
- Read it once more with one question: *if I only had this, could I write the
  test?* You are about to hand it to an agent for whom that is literally true.
- **The contract is the referee.** During the build, when a test and an
  implementation disagree, this text decides which one is wrong. An ambiguous
  documentation comment therefore makes you the wrong party — and costs a
  re-spawn of both sides.

### `## Work packages` — assign disjoint paths

One row per implementer package, plus one row for the unit tests and one for the
integration tests. Owned path sets must be **disjoint across every row**. Use
`Depends on` to sequence anything that cannot be concurrent. Keep a package
small enough that one spawn can finish it; a package that needs the whole change
is not a package.

**A shape change leaves no source call-site packages.** When the contract
changes a type other code builds or matches on, the type-forced edits at the
non-test call sites are contract-commit items, not rows. Call-site test
packages `Depends on` nothing: the libraries compile at that commit.

### `## Acceptance criteria`

Numbered, and each one names the observation that would prove it false. Write
the concrete numbers. Every criterion must be reachable from the contract — a
criterion the contract cannot express is a gap in the contract, so fix the
contract. Each criterion takes one of two shapes (`formats/dossier.md` §
"Criterion shapes"; Phase 5 enforces them):

- **EARS**, for behaviour — `The <system> shall <response>.` (always),
  `When <trigger>, the <system> shall <response>.` (event), `While <state>, …`
  (state), `If <unwanted condition>, then the <system> shall <response>.`
  (unwanted behaviour), `Where <feature>, …` (optional feature).
- **Quality scenario**, for performance and reliability —
  `Quality scenario: stimulus <what arrives, how much>; response <what the
  system does>; measure <the number that proves it, with its unit>.`

**Fitness functions are check commands.** A structural property the change must
keep (no import from layer A into B, a size budget, a forbidden call) is a
criterion that ends with `Check: \`<command>\`` — a concrete command that exits
non-zero when the property breaks. `/work-on` implements the check where it is
missing and its gate runs it. Phase 8 may propose promoting a check to the
repository's own gates, as a DECIDE row.

contract.

A criterion that names an exception thrown by **existing** code cites the
throw site: a `path:line` of the `throw`/`raise` it relies on, read in the
target branch — never inferred from a class name or a strategy name. An
expected exception without its throw site is a guess about where the code
fails, and the plan reviewer flags it as a change request.

Every criterion ends with an owner and an environment:
`(owner: <test path>; env: <local|vm|container|ci>)`. The owner is a test
path from the `## Work packages` table — you just built it, so take the path
from there, never from memory. The env names where that test runs. A
criterion whose env names services this machine cannot provide is a defect to
fix in this pass, not at `/work-on` Phase 3: name the substitute environment,
or mark the criterion `UNVERIFIABLE-LOCALLY` with its substitute in the same
line.

A criterion whose observation needs an environment this machine lacks — a
live host, a provisioned database, a deploy — is marked
`UNVERIFIABLE-LOCALLY` **with its agreed substitute named in the same
criterion**: the documented command that runs it elsewhere (a VM, a
container), or the deploy-check it defers to. An environment-gated criterion with no
substitute is not a criterion; it is a future arbitration that no amount of
code will settle, and the plan reviewer treats it as a defect.

Leave `## Build log` empty. Set `status: planned`.
