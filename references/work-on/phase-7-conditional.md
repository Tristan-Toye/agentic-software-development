<!-- /work-on, Phase 7, triggered parts. phase-7.md names each trigger; read the
     one section with ref_section, never the whole file. -->

# /work-on Phase 7 — triggered parts

Parts of the Phase 7 review cycle that apply only when their case arises. `phase-7.md` names each trigger.

### Rule on every exclusion before you spawn

**Rule on every exclusion before you spawn.** An excluded file is a file no
lens will ever see, and `ocr`'s defaults were written for a generic
repository, not this one. `exclude_reason: unsupported_ext` is the one that
bites: `ocr` carries a fixed language list, so a repository whose product is
`.md`, `.sql`, `.tf` or `.proto` watches its entire change land in
`excluded_files` and reviews nothing. Read every reason, and for each one
either accept it or pull the path back into `SCOPE` by hand — the exclusion
governs what `ocr` would have reviewed, never what you may. Record the
verdict in `## Build log`:

```
- EXCLUDED: <n> files — <path: reason>; ... — accepted | <path> pulled back into SCOPE
```

`none` is a valid value and so is `unknown — ocr unavailable`; what is not
valid is an exclusion that reached nobody. When `ocr` is missing, fall back to
`git diff --name-only`, log `unknown`, and know that nothing was filtered —
which is the safe direction, not the unsafe one.

### A lens that died mid-pass has reviewed nothing

**A lens that died mid-pass has reviewed nothing.** Its partial text reads
like a verdict — "this looks solid so far" — and is not one: a lens that
stopped before three of its five locations has reviewed neither. Resume it
from its transcript; never record a partial return as `PASS`. **A lens that
ran out of steps says so**: every reply ends with an `UNREACHED:` line
(`sub-agents/reviewer.md`, Output). Under opencode read it yourself — a `PASS`
whose line is not `UNREACHED: none`, or a reply with no such line, is
`incomplete`, and the unreached locations go to that lens again before its
`PASS` is logged. Log the line either way: `- r1 <lens>: UNREACHED none |
<locations> — resumed`.

### Prove each DEFECT before anyone fixes it

- **Prove each `DEFECT` before anyone fixes it.** A defect claims the code
  breaks a `CONTRACT` promise no test pins, so the proof is that test. Per
  defect (`defects`, or `check_crs.py`'s `prove` lines), re-spawn
  `unit-test-author` with the contract member, the defect's `Contract`, `Input`
  and `Expected` lines, and nothing about the implementation; then run
  `run_tests.py --test <name> --expect red` on the current head. `PROOF: red`
  → log `- r<n> <lens>: DEFECT-<lens>-<k> proven — <test> red on <sha>`, commit
  the test, and the fix implementer's `FAILURES` names it; the fix round is
  done when `--expect green` holds on the fixed head. `PROOF FAILED` (the test
  passes) → the defect is not real: log `not proven`, drop the section from
  the fix document, keep the test (it pins a promise). `PROOF REFUSED` (it never
  ran) is no verdict: fix the test, never the defect. A defect is never fixed
  unproven, and a reviewer's word is not proof.

### Check who may apply each CR before you route it

- **Check who may apply each CR before you route it.** A test CR that
  re-spawns an author after the merge composes its `CONTRACT` from the
  dossier's `## Contract` (`--dossier` on compose or `--refresh-contract`),
  never from the contract files, which hold implementation bodies now. The fix
  implementer owns source and never a test. **You never apply a test CR yourself either**
  — you referee tests, so you must never author them; a test diff is always
  a test agent's work. A CR against a test path routes by what it touches:

  - **Every CR on a unit-test file goes back to `unit-test-author`** — the
    cosmetic ones and the assertion-touching ones alike. Re-spawn it with the
    CR folded into its payload, by the host's mechanic (Phase 4): under
    opencode the file sits inside the author's read map, so it reads the
    current content itself and edits in place with `Edit`, nothing pasted
    and nothing deleted; under Claude Code it reads the file (its `Read` reaches the
    test families) and rewrites it whole, with the CR's tests named. Pass the finding and
    the constraints, and nothing about the implementation. A one-line CR
    costs a one-line edit; verify it regardless with
    `check_harness_edit.py --diff ORIGINAL MODIFIED` over what came back —
    exit 0 proves a non-assertion CR stayed non-assertion.
  - **CRs on an integration-test file** go back to `integration-test-author`,
    which can already read the file; no maintainer needed.
  - **A `tests` CR is proven against the risk it identified.** After the
    author returns, repeat the affected test or reproducing group under the
    relevant conditions (Phase 6), recording command and outcomes. For a
    static finding, verify the mechanism and run the affected tests. Resolve
    observed flips only after the reproduction passes every run; obtain the
    integrated verdict when the fix changes its relevant inputs. A
    `PRODUCT-RACE:` note from the `tests` lens is not a CR: it is a Phase 6 row-6 arbitration, ruled
    on its mechanism before any CR is routed. A `FLAKE-EXEMPT:` note is neither
    of these. Add it as a row of the repo's `.flake-risk-exempt.md` (create the
    file with the table header `flake_risk.py` documents) in the commit that
    carries the round's fixes. Copy the mechanism verbatim: the row is the
    ruling, and the script refuses a row that has no reason.

  Record the routing on the CR's ledger line. A conforming CR that no
  available agent may apply is a routing question: take the recommended routing and log it (the owner only if every routing changes a contract), never a
  demotion — demotion is for missing evidence only, never the merits.

### A fix or check round goes to the agent that holds the context

- **Under Claude Code, a fix or check round goes to the agent that holds the
  context** — the original reviewer for its own check round, the original
  author for a test CR, a fix implementer you spawned yourself (one the workflow
  spawned is re-routed, below) —
  by `SendMessage` to its agent ID with only the new CRs or the `PRIOR_CRS`
  answers, per `phase-4.md` ("Under Claude Code, resume instead of
  re-spawn"). Reviewers a workflow spawned have no ID in its result, and an
  id scavenged from the workflow's transcript directory is refused all the
  same: a check round re-spawns them with the full payload, by the workflow
  again. A fix round after a contract change is a full re-spawn.

### Check the fix footprint against the CR footprints

- **Check the fix footprint against the CR footprints.** After the fix
  implementer returns, diff what it touched against the union of the CRs it
  was given: a path inside `SCOPE` that no CR named is surplus work, and
  surplus work has had no review. Do not forbid it outright and do not merge
  it blind — spawn **one scoped lens review**: a single `reviewer` over just
  the surplus locations, named as locations, one pass, same evidence bar.
  Log the surplus paths and the scoped review on the ledger.

### On a check round, honour one asymmetry

**On a check round, honour one asymmetry:**

- `style` and `tests` are pure resolved / not-resolved checks. A rename cannot create new
  structure, so there is nothing new for it to see.
- `architecture` and `performance` may **also** review the structure their own
  accepted request created — a new interface, a new type, a changed algorithm, a
  moved boundary, a new cache — inside that request's footprint only, **one level
  deep**. Their requests are structural by nature, so applying one produces code
  no lens has ever seen. Without this, a fix that trades one problem for another
  ships unreviewed. Mark which prior CRs were structural in `PRIOR_CRS`, and say
  what the fix added (`references/payloads/reviewer.md`).
- Neither may open a subject outside its own requests. That budget went on the
  one pass.

### Under Claude Code, send the round through the workflow, once

**Under Claude Code, send the round through the workflow, once.** Compose and
lint the round's payloads as above, then call the `Workflow` tool once with
`name: "agentic-software-development:phase7-review"` and `args`, an array with
one object per lens in this round:

```
{ "lens": "style | architecture | performance | tests", "label": "r1 style",
  "payloadPath": "<the linted reviewer final, absolute, under <X>/.agent-staging/>",
  "payloadSha256": "<its hash>", "round": 1,
  "reportDir": "<X>/.agent-staging/reviews" }
```

Each reviewer reads its final itself, as in Phase 4's workflow wave: never put
a payload into `args` or a script, and never write or copy a workflow script.

The reviewers run concurrently, each returns its change requests as a
structured object, and a cheap agent keeps each verbatim document in
`reportDir`. The call returns a run ID at once (log `WORKFLOW: phase7-review
r<n> run <runId>`); the result arrives as the run's completion, one object per
lens:

```
{ "label": "r1 architecture", "lens": "architecture", "round": 1,
  "verdict": "PASS | CHANGES-REQUIRED | incomplete | died",
  "crs": [ { "id": "CR-1", "ledger_id": "CR-architecture-1",
             "severity": "blocking | should-fix", "location": "path:line",
             "claim": "<the heading's one-line requirement>" } ],
  "answers": [ { "id": "CR-1", "status": "resolved | not-resolved", "detail": "…" } ],
  "unreached": [ "<a SCOPE location the lens did not read in full>" ],
  "notes": "…", "document_path": "<the verbatim document, empty on PASS>" }
```

Write the `## Build log` ledger from `crs` — one line per change request,
`ledger_id` and `claim` and `location` as the line below says — and, on a check
round, from `answers`. **Demotion is the script's**: a CR whose `missing` is
non-empty is `demoted (<missing>)`, one with an empty `missing` is `accepted`, and
you never read the document to decide either (under opencode, run
`check_crs.py <document>` on each reply: it prints the same verdict per section).
Read a CR's section of `document_path` only to check a CR whose evidence you
cannot judge from its claim. The document is read whole once more,
when you compose the fix implementer's payload, which carries it byte for byte
(compose it from the file: never retype or edit it). A `died` lens has reviewed
nothing: resume it (`phase-4.md`, "Under Claude Code, resume instead of
re-spawn") or call the workflow again with that lens alone; it is never
`PASS`. An `incomplete` lens replied `PASS` with locations in `unreached`:
the workflow never reports that as a pass, and neither do you — resume it, or
call the workflow again with that lens alone and `SCOPE` narrowed to the
`unreached` locations. A `CHANGES-REQUIRED` with a non-empty `unreached` keeps
its CRs and gets the same follow-up for the rest. A check round calls the
workflow with only the lenses that have an open CR, each payload carrying its
`PRIOR_CRS`. Under opencode there is no workflow:
spawn the lenses in one message as written.

1. `reviewer` `LENS: style` — readability, extraction, naming, documentation
   that matches the contract.
2. `reviewer` `LENS: architecture` — cohesion, coupling, boundaries, dependency
   direction, duplicated knowledge, and **security at the trust boundaries**
   (input validation, authorisation, secrets, injection) — the security
   question lives in this lens and nowhere else.
3. `reviewer` `LENS: performance` — complexity, work amplification, allocation
   and copying, memory movement, blocking.
4. `reviewer` `LENS: tests` — **only when it is due**: Phase 6 logged a
   `FLAKY` test, or `FLAKE-RISK:` found a marker in a test file this build
   added or changed. Its `SCOPE` is not the blast radius: it is those test
   files, each with its marked lines and the names of any test that flipped,
   and its `RUN_EVIDENCE` carries the `STABILITY:` line. It reviews whether
   each test observes its promise reliably under load — never coverage, never
   what is asserted. When it is not due, log `TESTS-LENS: not due —
   FLAKE-RISK none, no observed flake` and spawn three.

### Resolve RULES once, narrow it three ways, and the evidence bars

**Resolve `RULES` once, then narrow it three ways.** Run
`ocr delegate rule --format json <the SCOPE paths>` — it returns per-path
review checklists, grouped so files sharing a rule appear once. This is
deterministic rule resolution, not a review: it calls no model, and the rules
are keyed on file type alone, so they carry nothing about what changed and
cost the reviewers no blindness.

**The narrowing is yours and it is not optional.** `ocr` returns one combined
checklist per path — correctness, security, performance, maintainability,
tests in a single block — and the lenses exist precisely so that one
question goes to one lens. Split the resolved text by lens before it becomes a
payload field: readability, naming and documentation items to `style`;
security, coupling, boundary and contract items to `architecture`; complexity,
allocation and concurrency items to `performance`. Drop what belongs to no
lens — test-coverage rules in particular, because you referee tests and no
lens authors them. Rules on test determinism and isolation go to `tests` when
it is due, and are dropped when it is not. Pass `RULES: none` when nothing resolved or `ocr` is
absent; never leave the field out, and never paste the ungrouped block.

`RULES` ranks below `STANDARDS`, `CONTEXT_DOCS` and `ARBITRATIONS`: it knows
the file's language and nothing about this repo. A reviewer that sets a rule
aside for one of those says so in its notes.

**Each lens carries its own evidence bar** (`sub-agents/reviewer.md`): performance
states the input scale that makes the cost matter, style states the observable
reading cost, architecture states the concrete future change or misuse. A CR
without its lens's evidence is a note, not a change request — **a matched
`RULES` item is not evidence and never substitutes for it.**

**The green suite is the invariant.** Every change request must be
behaviour-preserving under it. A change request that needs a test changed to
pass is out of bounds, and a reviewer that thinks the *behaviour* is wrong files
a contract objection in its notes — the contract owns behaviour, and the
contract is yours. The `tests` lens is the one lens whose CRs change test
files, and its bound is the same in kind: each CR keeps what the test asserts
and changes only how reliably it observes it.

### Spawn the implementer in MODE: fix with the merged change requests

Spawn the `implementer` in `MODE: fix` with the merged change requests.
Under opencode that is **one** implementer, its payload composed and linted
as above. Under Claude Code the fix round is mechanical, never composed by
hand: `check_crs.py <document> --drop <ids> --out <fix doc>` per lens, then
`compose_payloads.py --kind implementer --dossier <live> --root <X> --host claude
--crs <fix doc>[,<fix doc>...] --out <X>/.agent-staging/payloads/fix-wave.json
--test-command <cmd> --hooks <text>` routes each CR to the work package owning
its `Location`, writes and lints one `MODE: fix` payload per package carrying
exactly its CRs, and writes the `phase4-wave` args. A CR no package owns, or
two own, prints `UNROUTED: <id> <path> — <why>` and nothing is written: rule
on those lines only, then re-run with `--assign <id>=<package>`. **A fix
package owns every path its CRs name**, across projects: its row's
`OWNED_PATHS` plus each `Location` and `Siblings` path the row does not
cover, so an implementer never has to return half a CR as unowned. The
fix packages stay disjoint, as Phase 4's are: a path two fix packages
would both own prints `CONFLICT: <path> is named by CRs routed to <A> and
<B>` and nothing is written — merge the two packages' CRs into one with
`--assign`, or re-route the CR. Then call
`phase4-wave` **once** with that JSON file's array as `args`. The spawn guard
checks every file before the launch and `verify_return.py` checks every return,
so a fix payload never passes through the prompt. Fix implementers that share
`X` run one after another inside the workflow. A workflow-spawned fix
implementer has no ID to resume, so a second fix round routes its CRs the
same way and calls `phase4-wave` again. Every fix implementer works in `X` directly — the tests exist now, so blindness has done its job and
keeping them green is the point — **and alone**: never while a test author
is editing uncommitted files in `X` (a CR routed to an author runs before
or after the fix implementer, never beside it), and never over an
uncommitted harness fix of your own — commit that first (Phase 6), so the
payload needs no carve-out. `VERIFY_EMBEDDED` applies in fix mode
exactly as at the fan-out: a CR that touches an embedded program still gets
the extract-and-parse line in the payload — `bash -n` stayed green through
the recorded escape. Its `TOUCHED_BEYOND` section applies in fix
mode exactly as at the merge (Phase 5): rule on every listed path, and two
hard limits hold — never another package's owned paths, never the contract
files.
