<!-- /work-on, Phase 7. Read at the start of this phase; the rules that hold
     in every phase stay in primary-agents/work-on.md. -->

## Phase 7 — The review cycle: the code lenses, concurrently

### Rules that hold in this phase

- **The green suite is the invariant during review.** Every change request is
  behaviour-preserving under it; a red test after a fix means the fix was
  wrong, never that the test was. (Phase 7)

- **Reviewers reply; you write.** They have no write tools. Each change
  request becomes one `## Build log` ledger line; the full document travels
  only in the fix implementer's payload, byte-for-byte.

- **You detect review conflicts; you rule the low-impact ones, the owner the rest.** You are the only party
  who sees every lens.

**Spawning and workflows.** The rules for spawning an agent and for the two plugin
workflows are in `phase-4.md`: `ref_section` with `phase-4` and the headings
"Spawning under Claude Code" and "Under Claude Code — two workflows".

**Delegation and its size gates** are in `phase-2.md`: `ref_section` with `phase-2`
and the headings "Delegation — mechanical work only, evidence never verdicts" and "The gate table".

Compute the **blast radius**. The file list comes from
`ocr delegate preview --format json --repo X --from <baseline> --to HEAD`, not
from a hand-rolled `git diff --name-only`: it returns `reviewable_files` and,
just as important, `excluded_files` with the reason each was dropped. Narrow
`reviewable_files` to the functions and regions the change touched, plus their
direct callers. When it holds more than five files, delegate the listing to
`blast-radius-scout` — it runs the same `preview`, returns the location list
marked `changed` or `caller`, one hop deep, and reports `EXCLUDED:` — then
validate it against your own `git diff --stat <baseline>..HEAD` and narrow it
before it becomes `SCOPE`. Log the gate decision either way. Pass `SCOPE`
as a **location list, never the diff**, so the reviewers stay blind to the
history.

**Mark the signals, once per sweep.** Run `python3 ${PLUGIN_ROOT}/scripts/review_signals.py
--root X --base <baseline>` and log its three summary lines (`FAILURE-SIGNALS:`,
`LOG-SIGNALS:`, `SECURITY-TRIGGERS:`) as they print. Compose the architecture payload
with `--review-base <baseline>`: `SIGNALS` is then filled by the same script, as
current-state locations, and you never retype it. A check round carries no `SIGNALS`.

Spawn the round's lenses — the three code lenses, plus `tests` when it is due
(below) — in **one message** so they run concurrently. There is no chain,
no short-circuit, and no restart: they review the same green state and return
their change requests together. Each payload's `CONTRACT` is the dossier's
`## Contract` (`compose_payloads.py` reads it; never `--contract-paths`, whose
files hold the merged code now). Under Claude Code each spawn prompt is the
payload itself, pasted (`compose_payloads.py --emit-prompt <final>`); the
`PAYLOAD_FILE` pointer is the opencode form (`references/work-on/phase-4.md`).
A field built from several documents (`ARBITRATIONS`, `PRIOR_CRS`) is either one
`--excerpt FILE:A-B ... --excerpt-field FIELD` (FILE under `<X>`, relative or
absolute; one outside `<X>` is refused) or a `--set 'FIELD=# FILE:A-B' ...`
value whose first line of each slice is a `# FILE:A-B` header, so the lens can
cite the file and lines of every pasted slice. Quote the value; the command
runs the same under bash and zsh.

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

### Triggered parts — read the section only when its trigger fires

`ref_section` with `phase-7-conditional` and the heading:

- `ocr delegate preview` returns excluded files: "Rule on every exclusion before you spawn".
- A reviewer lens that died or returned a partial text: "A lens that died mid-pass has reviewed nothing".
- A change request of kind `DEFECT`: "Prove each DEFECT before anyone fixes it".
- A change request against a test path: "Check who may apply each CR before you route it".
- A check round, or a fix that goes back to an agent that holds the context: "A fix or check round goes to the agent that holds the context".
- A fix implementer returned: "Check the fix footprint against the CR footprints".
- A check round (round 2 or 3): "On a check round, honour one asymmetry".

**Apply the change requests:**

- Collect all three replies. Record each reply in `## Build log` as a
  **one-line-per-CR ledger** — one line per change request, in this form:

  ```
  - r1 <lens>: CR-<lens>-<n> <one-line requirement> — <path:line> — accepted | demoted (<missing evidence>)
  ```

  Never paste a reviewer document into the dossier — a document has its own
  headings, and the dossier body has six fixed sections. The full document is
  the **fix implementer's payload**: pass it there byte-for-byte, unparaphrased
  and unsoftened — it is the reviewer's work order, not yours to edit. The
  dossier records the ledger; the payload carries the transcript.
- **Demote non-conforming CRs — by the fields, never the merits.** The fields
  are checked by script (`missing`, or `check_crs.py`), `Siblings` included; you
  never overrule a reviewer because you disagree with a CR that carries its
  evidence — a disagreement about design or product goes to the owner like any other conflict. The
  fix implementer gets the document without the demoted sections:
  `check_crs.py <document> --drop <ids> --out <fix doc>`, every kept byte as
  the reviewer wrote it.
- **Detect conflicts first.** You are the only party that sees every lens,
  so a performance request that undoes a style request is yours to catch. A conflict
  with a recommended side and a low impact (no behaviour or contract changes)
  is yours: take the recommendation, record it in `## Build log` as an
  arbitration, and log `PROVISIONAL-RULING:` for it. A conflict that changes
  behaviour or a contract is a design question for the owner: present both
  sides with your recommendation (`--kind design`), keep working on what does
  not depend on it, and record the ruling as an arbitration. Pass every accumulated arbitration into later spawns so no
  reviewer re-litigates a settled question.
- Spawn the `implementer` in `MODE: fix` with the merged change requests.
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
- **Run the whole `GATES:` list (Phase 2) on the fixed head before you push**,
  including gates no local aggregator covers. A fix round never gets a
  narrower list than the contract commit; a doc link or a feature-gated
  compile is what a small fix breaks.
- Get the runner's verdict on the fixed head (Phase 6: under `ci`, push the
  fix commit and read CI's run; under `local`, run it). Any test that turns
  red means the fix was not behaviour-preserving: return it to the
  implementer, never patch the test.
- **Check rounds append to the same ledger.** One line per answer:
  `- r<k> <lens>: CR-<lens>-<n> resolved | not resolved (<what is still wrong>)`.
  A structural follow-up CR gets a fresh ledger line, like round 1.
### The round setup

| Round | Spawns | Who | Answers |
|---|---|---|---|
| **1 — sweep** | **3–4**, concurrent | the three code lenses, plus `tests` when due | Fresh subject matter, full `SCOPE`, **one pass**. |
| proof | 0–k | `unit-test-author`, one per `DEFECT` | a failing test, `--expect red`, before the fix |
| fix | 1 | `implementer MODE: fix` | the merged open CRs, proven defects, any arbitration |
| — | 0 | you | the runner's verdict on the fixed head; red means the fix was not behaviour-preserving |
| **2 — check** | **0–4**, concurrent | **only lenses with an open CR** | own CRs: resolved / not resolved |
| fix | 1 | `implementer MODE: fix` | what is still open |
| **3 — check** | **0–4**, concurrent | only lenses still open | same |
| stop | — | — | escalate with the round history |

**A lens with every CR resolved and no structural follow-up is done, and you
never re-spawn it.** A clean run costs 3 spawns (4 with `tests`
due); one lens filing, fixed and confirmed costs 5; the worst case is 11, or 14
with `tests` open every round. Re-spawning a satisfied lens buys
nothing — it has no question left to answer, and its one pass is already spent.

**Checkpoint after each round.** When a round's CRs are ruled, its fixes have
returned and been verified, and the round's ledger lines are in `## Build log`,
call `phase_checkpoint` with `Phase 7 — round <n>` and the hand-off: the head
SHA, the open CRs per lens with their ledger ids, the rounds spent against the
budget, and which lenses the next round re-spawns. The next round reads the
CRs from the ledger, not from this round's transcript.

**Exit condition:** every lens at zero open CRs.
**Budget: 2 fix rounds.** After the second, when one more fix round is your recommendation, take it once
and log it; if that round fails too, put the round history and the unresolved
tensions to the owner as one question (`--kind uncertainty`).

**The reviewers know there is one pass** — their prompt says so, and this budget
is why. A reviewer that expects another sweep holds back marginal findings, and
there is no sweep to hold them for.
