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
