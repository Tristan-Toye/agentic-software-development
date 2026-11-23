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

**A skipped test is not a green test (#488).** A subset run whose new tests
were all skipped (the digest's `skipped N (NOT VERIFIED, not passed)` line,
from `run_tests.py --skip-banner`) does not count as the new tests going green:
treat the subset as not run, record those tests NOT VERIFIED, and take the
verdict from a run that has the environment.

**Read the summary, not the log.** `run_tests.py` prints the totals, each failing
test (name, first assertion or panic with `file:line`), the failing test binary
(`failed in:`) or failing files, and deduplicated compile errors; `run_gates.py`
prints, under each `FAIL` line, the parsed first errors (`file:line  error[CODE]:
message`, rustfmt diffs per file, ruff/tsc findings, failing tests). Open the log
only when the summary carries a `truncated` or `see log` marker, and then only
the slice it points to; a green or fully listed failure needs no `grep`, `tail`
or `cat` of the log, and no second run.

**The verdict comes from the runner `SUITE-RUNNER:` names (Phase 2), once
per head, never twice.** Under `ci`: sync first (below), push `X`'s branch
(`git push -u origin <branch>`; committed mode commits the dossier first),
open the draft PR on the first push — `gh pr create --draft --title
"<JIRA-KEY>: <title>" --body "build in progress; description follows"`, or
the host's equivalent; on a host with no CLI the push alone triggers a
pipeline whose branch pattern matches — and wait for the run in one call:
`python3 ${PLUGIN_ROOT}/scripts/wait_ci.py --pr <N> --log <live dossier>` (under the
runner, end the turn with `RUNNER: WAITING ci:<N>` instead: the runner runs it and answers
`CI RESULT <N>: green|red|timeout|never-started ...` (`never-started`: CI did not run, it did not fail; never wait on that PR again, end `RUNNER: FAILED CI never started on PR <N>: ...`; when the message names a conflicting parent PR, resolve that parent first, then wait again); the rest of this paragraph then reads the same). It polls silently,
exits with the checks' verdict, and prints each failed job's log as a
bounded digest parsed from the whole log. The exit status is the runner's
and the counts are summed from the whole log by the parser, never a verdict
through `grep` or `tail`; open a failing job's full log only in slices, for
a line the digest names. With `--log` it appends `SUITE: ci run <url> on <sha> —
<N> failed / <N> passed` to `## Build log` itself; never retype it. A run
that did not start, or that failed before the suite ran — a checkout error,
a missing secret, a cache step — is not a verdict: exit-2 semantics. Fix the
CI wiring on your branch, or fall back to `local` for this head and log why.
**A repository-declared recovery is not a question.** A repo can declare, in
`.agentic/ci-recover.json`, a check that only its own command clears (the sealed
secrets backup lacking a name this change made required: the command reseals it in
the VM). Two moments, both through `python3 ${PLUGIN_ROOT}/scripts/ci_recover.py`,
and neither asks the owner:
- *Prevent*: before the push that carries a change adding a required name (a new
  entry in the repo's secrets catalogue), run `ci_recover.py prevent --worktree <WT>
  --trailer '<the commit attribution line>'`; it runs the declared `detect`, and
  reseals and commits through the hooks when it fails. The dossier PR never goes red.
- *Recover*: when the PR's only red checks are a declared check whose failed log
  matches the declared pattern (inherited from the dossier branch, a stacked PR
  included), run `ci_recover.py recover --pr <N> --worktree <WT of the dossier branch>
  [--branch <dossier branch>]`: it reseals, commits through the hooks, pushes, and you
  wait with `wait_ci.py` again. Any other red check, or a log that does not match, exits
  1: that stays the ordinary red-check path, never a reseal.

Detail: `ref_section` with `phase-6-conditional` and the heading "Under local and stub, and the coverage check on every verdict". (read it for the runner you are under.)

**Every head that leaves this machine passes the `GATES:` list first**
(Phase 2, `run_gates.py`). The first push of the branch runs the full list
(no flag); each later arbitration fix runs `--affected-since <sha of the head
the full list last passed on>`, which skips the gates no changed path touches
(printed `skipped: no changed paths`) and reuses a gate already green on the
same inputs. Call `run_gates.py <gates file> --root X --build-log <live dossier>` every time (the script appends its own
`GATES: run <green|red> on <sha> — <counts>` line to `## Build log`; do not write that line by hand): a green run on the same gates file and the same
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
| The test asserts something the contract does not promise | **the test** | Re-spawn the test author (once per package, carrying every test of the round it must correct) with a corrected payload by the host's mechanic (Phase 4): in place under opencode; under Claude Code it reads the file, then rewrites it whole with one `Write`. Never edit the test yourself — you have read the implementation, so you are exactly the wrong party to fix a test. **A re-spawn of a pre-existing test's author** — its hoisted expected value is assertion-bearing, so you may not edit it — carries `FAILURES:` (the failing output, verbatim) and `CORRECTION:` (the ordered edits: read the whole file, then `Write` it back with exactly those changes, nothing else) instead of folding the fix into `DELIVERY_CHANGE` (`references/payloads/integration-test-author.md`). Compose it again with the script: `.agent-staging/sealed-paths.txt` is rewritten, and the integration author's hook keeps refusing the implementation files the merge filled. |
| The implementation does not do what the contract promises | **the implementation** | Re-spawn the implementer for that package once in `MODE: fix`, with every failure of the round in that package, working in `X` directly — its own worktree is gone (Phase 5) and the tests are committed, so blindness no longer applies. The failure output travels as `FAILURES:` in the payload (`references/payloads/implementer.md`); `CRS:` carries any review change requests still open for it, or is omitted. **It runs alone in `X`**: never concurrently with a test author editing uncommitted files there — commit the author's work first, or wait for it. The recorded fix implementer saw the test file change under its `TEST_COMMAND`, reverted it with `git checkout --`, and destroyed a whole uncommitted fix round. |
| The contract is ambiguous enough to justify both readings | **you** | Fix the contract (and `PROMISE_CHECKLIST`, if a unit promise is involved) in the files and the dossier, commit on `X`, re-spawn the affected test author onto its file (Phase 4), and re-spawn **both** sides. |
| The test fails on harness noise — a compile error, a missing fixture, an import | nobody | Fix the harness yourself. It is mechanical. |
| The failure reproduces on the **target branch at the merge-base**, untouched by this build | nobody — **upstream** | Prove it first (below), then fix it on your branch to keep green, log `UPSTREAM:` with the proof, and say in the PR description that the fix belongs upstream independently of this change. |
| The failure is **intermittent** — `FLAKY` in a repeat run, or a red that a rerun of the same head turns green | **unknown until the mechanism is found** | Find the race (below), then rule on the mechanism: rows 1–3, or row 5 with a repeated proof. Never "nobody", and never a rerun until green. |

**A failing property test is ruled on its shrunk counterexample.** Quote the
smallest failing input the library reports against the checklist line the
test cites. An input outside the line's `domain:`, or outside what the
docstring promises for it, is row 1 (the test): the property asserted more
than the contract states. An input inside both is row 2 (the
implementation). An input the docstring leaves open is row 3 (the
contract). Expect row 1 often: about one LLM-written property in five is
unsound against its documentation (Vikram et al.).

**Row 1 is two rulings with one action.** Name which on the line: **1a** — the test
asserts beyond the contract (an exact type, the whole set, an extra order, a value the doc
comment leaves open): a contract-quality signal, reported with row 3. **1h** — the test's
fixture, environment or a stale expectation is wrong (a seam the repo refuses, a missing CI
dependency, a literal the contract commit changed) and you may not edit it because it feeds
an assertion: harness craft, not contract quality. Both re-spawn the test author, as the table
says. A bare `row 1` is the old label; write 1a or 1h.

Write every arbitration as one `ARBITRATION:` line — the failure, the ruling, and which
of the six rows applied (`row 1a`, `row 1h`, `row 3`, …; a row-3 line adds `site=<path>::<member>`,
the key of the member whose doc comment was silent or two-readable) — and hand the round's lines to its checkpoint as `log`
(below); if the round runs on past a tool call, put them in the same message as the next call.
A ruling that decides something a future agent must know (a contract choice, a
trade-off accepted) is also written as a decision record, the source Phase 8
reads for ADRs: `python3 scripts/build_records.py decision add <ID> --decision
'...' --alternatives '...' --why '...' --evidence '...' --phase 6`.

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

**Driver (`ASD_DRIVER_TEST_ROUND` is `on` or `shadow`).** The exact command, run from the build worktree X (`<ID>` is the bare dossier ID, e.g. `W-014`; `<N>` the round number): `python3 "${PLUGIN_ROOT}/scripts/test_round.py" --dossier <ID> --worktree <X> --round <N> [--gates <gates file>] -- <the same suite command you run for the round>`. It runs gates and the suite once and prints per-failure evidence (file, package, kind hint, first line, repeat-site flag); arbitrating each failure stays yours. A non-zero suite with no parseable failure exits 2. In `shadow`, run and arbitrate the round exactly as today, then run the driver once afterwards with the same command (no `--log`) and ignore its output for every decision (it re-runs the suite, so do this after the round's arbitration, never while a stack lease is wanted); evidence goes to `.git/asd-drivers/driver-shadow/`. A call with missing flags only records a surprise and tells the rollout nothing, so copy the command whole. `off` or unset: ignore this paragraph.
