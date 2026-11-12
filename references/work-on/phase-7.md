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
workflows are in `phase-4-conditional.md`: `ref_section` with `phase-4-conditional` and the headings
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
--root X --base <baseline> --log <live dossier>`, which appends its three summary lines
(`FAILURE-SIGNALS:`, `LOG-SIGNALS:`, `SECURITY-TRIGGERS:`) to `## Build log` as they print. Compose the architecture payload
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

Detail: `ref_section` with `phase-7-conditional` and the heading "Under Claude Code, send the round through the workflow, once". (every review round under Claude Code.)

Detail: `ref_section` with `phase-7-conditional` and the heading "Resolve RULES once, narrow it three ways, and the evidence bars". (before the first lens spawns.)

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

- Collect all three replies. Record each reply as a
  **one-line-per-CR ledger**, appended to `## Build log` through the round's checkpoint `log` — one line per change request, in this form:

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
Detail: `ref_section` with `phase-7-conditional` and the heading "Spawn the implementer in MODE: fix with the merged change requests". (routing, payloads and the fix-round limits.)

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
returned and been verified, and the round's ledger lines are written,
call `phase_checkpoint` with `Phase 7 — round <n>`, `dossier`, the ledger lines as `log`, the
round's owner note as `note`, and the hand-off: the head
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
