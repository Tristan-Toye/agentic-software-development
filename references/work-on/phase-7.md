<!-- /work-on, Phase 7. Read at the start of this phase; the rules that hold
     in every phase stay in primary-agents/work-on.md. -->

## Phase 7 — The review cycle: three lenses, concurrently

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

Spawn all three in **one message** so they run concurrently. There is no chain,
no short-circuit, and no restart: they review the same green state and return
their change requests together.

1. `reviewer` `LENS: style` — readability, extraction, naming, documentation
   that matches the contract.
2. `reviewer` `LENS: architecture` — cohesion, coupling, boundaries, dependency
   direction, duplicated knowledge, and **security at the trust boundaries**
   (input validation, authorisation, secrets, injection) — the security
   question lives in this lens and nowhere else.
3. `reviewer` `LENS: performance` — complexity, work amplification, allocation
   and copying, memory movement, blocking.

**Resolve `RULES` once, then narrow it three ways.** Run
`ocr delegate rule --format json <the SCOPE paths>` — it returns per-path
review checklists, grouped so files sharing a rule appear once. This is
deterministic rule resolution, not a review: it calls no model, and the rules
are keyed on file type alone, so they carry nothing about what changed and
cost the reviewers no blindness.

**The narrowing is yours and it is not optional.** `ocr` returns one combined
checklist per path — correctness, security, performance, maintainability,
tests in a single block — and the three lenses exist precisely so that one
question goes to one lens. Split the resolved text by lens before it becomes a
payload field: readability, naming and documentation items to `style`;
security, coupling, boundary and contract items to `architecture`; complexity,
allocation and concurrency items to `performance`. Drop what belongs to no
lens — test-coverage rules in particular, because you referee tests and no
lens authors them. Pass `RULES: none` when nothing resolved or `ocr` is
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
contract is yours.

**A lens that died mid-pass has reviewed nothing.** Its partial text reads
like a verdict — "this looks solid so far" — and is not one: a lens that
stopped before three of its five locations has reviewed neither. Resume it
from its transcript; never record a partial return as `PASS`.

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
- **Demote non-conforming CRs — by the fields, never the merits.** A CR that
  lacks its lens's required evidence gets `demoted (<missing evidence>)` on its
  ledger line instead of `accepted`. The rule is mechanical on purpose: you
  check whether the evidence is *stated*, and you never overrule a reviewer
  because you disagree with a CR that carries its evidence — that disagreement
  goes to the user like any other conflict.
- **Check who may apply each CR before you route it.** The fix implementer
  owns source and never a test. **You never apply a test CR yourself either**
  — you referee tests, so you must never author them; a test diff is always
  a test agent's work. A CR against a test path routes by what it touches:

  - **Every CR on a unit-test file goes back to `unit-test-author`** — the
    cosmetic ones and the assertion-touching ones alike. Re-spawn it with the
    CR folded into its payload, by the host's mechanic (Phase 4): under
    opencode the file sits inside the author's read map, so it reads the
    current content itself and edits in place with `Edit`, nothing pasted
    and nothing deleted; under Claude Code the author has `Write` alone, so
    the file is deleted first with `safe_revert.py --delete` and rewritten
    whole, with the CR's tests named. Pass the finding and
    the constraints, and nothing about the implementation. A one-line CR
    costs a one-line edit; verify it regardless with
    `check_harness_edit.py --diff ORIGINAL MODIFIED` over what came back —
    exit 0 proves a non-assertion CR stayed non-assertion.
  - **CRs on an integration-test file** go back to `integration-test-author`,
    which can already read the file; no maintainer needed.

  Record the routing on the CR's ledger line. A conforming CR that no
  available agent may apply is a routing question for the user, never a
  demotion — demotion is for missing evidence only, never the merits.
- **Detect conflicts first.** You are the only party that sees all three lenses,
  so a performance request that undoes a style request is yours to catch. Deciding
  it is the **user's**, because it is a judgement call: present both sides with
  your recommendation, get a ruling, and record it in `## Build log` as an
  arbitration. Pass every accumulated arbitration into later spawns so no
  reviewer re-litigates a settled question.
- Spawn **one** `implementer` in `MODE: fix` with the merged change requests. It
  works in `X` directly — the tests exist now, so blindness has done its job and
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
- Get the runner's verdict on the fixed head (Phase 6: under `ci`, push the
  fix commit and read CI's run; under `local`, run it). Any test that turns
  red means the fix was not behaviour-preserving: return it to the
  implementer, never patch the test.
- **Check the fix footprint against the CR footprints.** After the fix
  implementer returns, diff what it touched against the union of the CRs it
  was given: a path inside `SCOPE` that no CR named is surplus work, and
  surplus work has had no review. Do not forbid it outright and do not merge
  it blind — spawn **one scoped lens review**: a single `reviewer` over just
  the surplus locations, named as locations, one pass, same evidence bar.
  Log the surplus paths and the scoped review on the ledger.
- **Check rounds append to the same ledger.** One line per answer:
  `- r<k> <lens>: CR-<lens>-<n> resolved | not resolved (<what is still wrong>)`.
  A structural follow-up CR gets a fresh ledger line, like round 1.
### The round setup

| Round | Spawns | Who | Answers |
|---|---|---|---|
| **1 — sweep** | **3**, concurrent | all three lenses | Fresh subject matter, full `SCOPE`, **one pass**. |
| fix | 1 | `implementer MODE: fix` | the merged open CRs plus any arbitration |
| — | 0 | you | the runner's verdict on the fixed head; red means the fix was not behaviour-preserving |
| **2 — check** | **0–3**, concurrent | **only lenses with an open CR** | own CRs: resolved / not resolved |
| fix | 1 | `implementer MODE: fix` | what is still open |
| **3 — check** | **0–3**, concurrent | only lenses still open | same |
| stop | — | — | escalate with the round history |

**A lens with every CR resolved and no structural follow-up is done, and you
never re-spawn it.** A clean run costs 3 spawns; one lens filing, fixed and
confirmed costs 5; the worst case is 11. Re-spawning a satisfied lens buys
nothing — it has no question left to answer, and its one pass is already spent.

**Exit condition:** every lens at zero open CRs.
**Budget: 2 fix rounds.** After the second, stop and show the user the round
history and the unresolved tensions.

**On a check round, honour one asymmetry:**

- `style` is a pure resolved / not-resolved check. A rename cannot create new
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

**The reviewers know there is one pass** — their prompt says so, and this budget
is why. A reviewer that expects another sweep holds back marginal findings, and
there is no sweep to hold them for.
