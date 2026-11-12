<!-- /work-on, Phase 6. Read at the start of this phase; the rules that hold
     in every phase stay in primary-agents/work-on.md. -->

## Phase 6 — Run the tests, and arbitrate with the contract

### Rules that hold in this phase

- **The contract decides every test-versus-implementation dispute**, by the
  six-row table in Phase 6. Row 4's boundary is mechanical
  (`check_harness_edit.py`: exit 0 harness, exit 1 assertion, exit 2 unknown
  and ruled conservatively); row 5 is never ruled from the shape of the error,
  only from a reproduction on the pristine merge-base; row 6 (intermittent) is
  ruled on a named mechanism, product first, and never settled by a retry, a
  longer sleep or a rerun until green. Never edit a test
  yourself — you have read the implementation, so you are the wrong party, and
  every CR on a test file is its author's work, cosmetic ones included.

- **One verdict, one run.** The suite runner is decided at the contract
  commit (`SUITE-RUNNER:`), and every full-suite verdict from Phase 6 on
  comes from that runner's coverage-complete run on the relevant inputs.
  Choose by actual coverage and repository policy: partial CI gates can run
  alongside a required full local suite. Under `SUITE-RUNNER: stub` this
  build owes no full-suite verdict: the integrated verdict is the `/stub`
  run of the release stub this branch merges into, and this build's proof
  is CI green plus the new tests and the mutation check. Reuse a green
  verdict until relevant
  inputs change; there are no unconditional whole-suite stability repeats.
  The stub red-run, targeted flake investigations, mutants, a row-5 proof and
  implementers' focused checks stay local. (Phase 2, Phase 6, Phase 7, Phase 9)

**Spawning and workflows.** The rules for spawning an agent and for the two plugin
workflows are in `phase-4-conditional.md`: `ref_section` with `phase-4-conditional` and the headings
"Spawning under Claude Code" and "Under Claude Code — two workflows".

Get the suite's verdict on `X`. **This is the first time the code and the
tests meet each other**, so expect failures. A failure here is the design
working, not the design breaking.

**The verdict comes from the runner `SUITE-RUNNER:` names (Phase 2), once
per head, never twice.** Under `ci`: sync first (below), push `X`'s branch
(`git push -u origin <branch>`; committed mode commits the dossier first),
open the draft PR on the first push — `gh pr create --draft --title
"<JIRA-KEY>: <title>" --body "build in progress; description follows"`, or
the host's equivalent; on a host with no CLI the push alone triggers a
pipeline whose branch pattern matches — and wait for the run in one call:
`python3 ${PLUGIN_ROOT}/scripts/wait_ci.py --pr <N> --log <live dossier>`. It polls silently,
exits with the checks' verdict, and prints each failed job's log as a
bounded digest parsed from the whole log. The exit status is the runner's
and the counts are summed from the whole log by the parser, never a verdict
through `grep` or `tail`; open a failing job's full log only in slices, for
a line the digest names. With `--log` it appends `SUITE: ci run <url> on <sha> —
<N> failed / <N> passed` to `## Build log` itself; never retype it. A run
that did not start, or that failed before the suite ran — a checkout error,
a missing secret, a cache step — is not a verdict: exit-2 semantics. Fix the
CI wiring on your branch, or fall back to `local` for this head and log why.
Detail: `ref_section` with `phase-6-conditional` and the heading "Under local and stub, and the coverage check on every verdict". (read it for the runner you are under.)

**Every head that leaves this machine passes the `GATES:` list first**
(Phase 2, `run_gates.py`): the first push and each arbitration fix. Call
`run_gates.py` every time: a green run on the same gates file and the same
tree — a fix-mode implementer's, on the commit you are about to push —
left a receipt, and the call prints `REUSED` and runs nothing. Any edit,
commit, merge or new file outside `.discovery/dossiers/` changes the tree,
and the list runs again. `--fresh` forces a run; never pass it to escape a
receipt you believe in. A green
default-feature compile is not the list.

Detail: `ref_section` with `phase-6-conditional` and the heading "First, check whether the target branch has moved, and sync now". (before you arbitrate anything.)

**Rule on the whole round, then correct once per package.** Collect every
failing test of the round before you rule on any; rule on each one by the
table; then group the corrections by owning package (a failing test belongs
to the work package whose `OWNED_PATHS` its subject reaches, and the test
author's file to that package's author) and send **one** fix agent per
package per round, carrying all of that package's failures in a single
payload (`FAILURES:` lists every failing test with its output; one
`CORRECTION:` for a test author). Never spawn one agent per failing test.

For each failure, decide who was wrong. **The contract is the referee**, and the
rule is mechanical so you cannot drift toward whichever side is easier to
change:

| The failure shows | Who is wrong | What you do |
|---|---|---|
| The test asserts something the contract does not promise | **the test** | Re-spawn the test author (once per package, carrying every test of the round it must correct) with a corrected payload by the host's mechanic (Phase 4): in place under opencode; under Claude Code it reads the file, then rewrites it whole with one `Write`. Never edit the test yourself — you have read the implementation, so you are exactly the wrong party to fix a test. **A re-spawn of a pre-existing test's author** — its hoisted expected value is assertion-bearing, so you may not edit it — carries `FAILURES:` (the failing output, verbatim) and `CORRECTION:` (the ordered edits: read the whole file, then `Write` it back with exactly those changes, nothing else) instead of folding the fix into `DELIVERY_CHANGE` (`references/payloads/integration-test-author.md`). |
| The implementation does not do what the contract promises | **the implementation** | Re-spawn the implementer for that package once in `MODE: fix`, with every failure of the round in that package, working in `X` directly — its own worktree is gone (Phase 5) and the tests are committed, so blindness no longer applies. The failure output travels as `FAILURES:` in the payload (`references/payloads/implementer.md`); `CRS:` carries any review change requests still open for it, or is omitted. **It runs alone in `X`**: never concurrently with a test author editing uncommitted files there — commit the author's work first, or wait for it. The recorded fix implementer saw the test file change under its `TEST_COMMAND`, reverted it with `git checkout --`, and destroyed a whole uncommitted fix round. |
| The contract is ambiguous enough to justify both readings | **you** | Fix the contract (and `PROMISE_CHECKLIST`, if a unit promise is involved) in the files and the dossier, commit on `X`, re-spawn the affected test author onto its file (Phase 4), and re-spawn **both** sides. |
| The test fails on harness noise — a compile error, a missing fixture, an import | nobody | Fix the harness yourself. It is mechanical. |
| The failure reproduces on the **target branch at the merge-base**, untouched by this build | nobody — **upstream** | Prove it first (below), then fix it on your branch to keep green, log `UPSTREAM:` with the proof, and say in the PR description that the fix belongs upstream independently of this change. |
| The failure is **intermittent** — `FLAKY` in a repeat run, or a red that a rerun of the same head turns green | **unknown until the mechanism is found** | Find the race (below), then rule on the mechanism: rows 1–3, or row 5 with a repeated proof. Never "nobody", and never a rerun until green. |

Write every arbitration as one `ARBITRATION:` line — the failure, the ruling, and which
of the six rows applied — and hand the round's lines to its checkpoint as `log`
(below); if the round runs on past a tool call, put them in the same message as the next call.

Detail: `ref_section` with `phase-6-conditional` and the heading "Investigate flake risk before mutation checks and review". (once the verdict is green; it sets `status: review`.)

### Triggered parts — read the section only when its trigger fires

`ref_section` with `phase-6-conditional` and the heading:

- A suite run with `--vm`: "A `--vm` suite run gets a tier-calibrated timeout floor and a post-kill sweep".
- More than three failures in a round, or a ruling that rests on a count, a timing or a probe: "Past three failures, build the case files; a ruling rests on a measurement".
- A round with corrections to send (rows 1-3): "Correction rounds: carry the rulings, one workflow call, resume or re-spawn".
- A ruling of row 5 (upstream) or row 6 (intermittent), the proofs the table calls below: "Rows 5 and 6: the proof and the mechanism".
- A ruling of row 4 (harness noise): "Row 4: land your fix first, and the boundary is mechanical".
- A ruling of row 3 (ambiguous contract): "Row 3 is a lesson, not just a count".

**Checkpoint after each arbitration round.** A round ends when its rulings are
written, its re-spawns have returned and been verified, and the runner's
verdict on the new head is in `## Build log` (`wait_ci.py --log` or
`run_tests.py --build-log` put it there). Then call `phase_checkpoint` with
`Phase 6 — round <n>`, `dossier`, the round's `ARBITRATION:` lines as `log`, the round's owner
note as `note`, and the hand-off: the head SHA, the verdict counts, every
ruling so far with its site (the repeat-fingerprint needs them), the rounds
spent against the budget, and what the next round runs. The next round reads
its failures from the runner, not from the last round's transcript.

**Budget: 3 arbitration rounds.**
After the third, when one more correction round is your recommendation, take
it once without asking and log `BUDGET-EXTENDED: <why>` in `## Build log`; if
that round fails too, stop and put the failures and your rulings to the owner
as one question (`--kind uncertainty`). Most budget exhaustion is one repeated
ambiguity or a criterion this machine cannot observe; the owner names the
substitute.

**Repeat-fingerprint — the early stop one round before the budget.** Track
the site of every arbitration: the contract member and the test file. When
the same member or the same test file is arbitrated a **second** time — even
with a different symptom — stop and take it to the owner there as one question (`--kind uncertainty`), one round
before the budget. Distinct failures across rounds are healthy convergence;
the same site twice means something is stuck — a promise that cannot be made
observable, a criterion this machine cannot check — which is a `/plan`
question wearing an arbitration costume.

Detail: `ref_section` with `phase-6-conditional` and the heading "Mutation check: default on, scale it honestly". (the moment the suite first goes green.)
