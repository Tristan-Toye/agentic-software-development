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
workflows are in `phase-4.md`: `ref_section` with `phase-4` and the headings
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
`python3 ${PLUGIN_ROOT}/scripts/wait_ci.py --pr <N>`. It polls silently,
exits with the checks' verdict, and prints each failed job's log as a
bounded digest parsed from the whole log. The exit status is the runner's
and the counts are summed from the whole log by the parser, never a verdict
through `grep` or `tail`; open a failing job's full log only in slices, for
a line the digest names. Log `SUITE: ci run <url> on <sha> — <N> failed /
<N> passed`. A run
that did not start, or that failed before the suite ran — a checkout error,
a missing secret, a cache step — is not a verdict: exit-2 semantics. Fix the
CI wiring on your branch, or fall back to `local` for this head and log why.
Under `local`: run the full `SUITE-COMMAND` recorded in Phase 2 in `X`,
through `python3 ${PLUGIN_ROOT}/scripts/run_tests.py --log <file> --
<full-suite command>` (add `--vm <name> --workdir <X> --env-script <file>`
when the command itself does not manage its required VM). Under the runner, a VM
that is down or a compose service that is unhealthy is reported as the single line
`RUNNER: ENV <vm|stack|disk> <detail>`, not asked about (see runner.md). An implementer's
focused `TEST_COMMAND` is not a substitute. Keep the complete output and
record the candidate commit, target baseline, exact command, environment,
counts and log path as `SUITE: local on <sha>, base <sha> — <counts>; <log>`.
Verify that the tested tree matches that commit and that relevant inputs did
not change during the run; otherwise the evidence cannot describe that head.
Under `stub`: the same mechanics as `ci` — sync, push, draft PR,
`wait_ci.py` — and this build's own proof stays local and focused: the stub
red-run, the new tests green, the mutation check. No full local suite runs
for a verdict; the integrated verdict is the `/stub` run of the release stub
this branch merges into. Log `SUITE: stub-deferred on <sha> — CI green, new
tests <counts>, mutation <killed>/<survived or skipped>; full suite deferred
to /stub on <stub branch>`. A service-backed new test may hold the stack
lease for one suite window for its own green run, through `run_tests.py
--vm`; that run is this build's evidence, never the integrated verdict.

Under either runner, inspect the complete log for skipped or `NOT VERIFIED`
coverage and compare executed tiers with `SUITE-COVERAGE`. An aggregator's
successful exit can include skipped required tiers. Record each omission
and reason; missing required coverage leaves the verdict incomplete. A
service-free tier's opt-outs are acceptable only when the required service
and host tiers actually ran. Every later runner verdict means this same
coverage-complete run on the relevant inputs. Keep a green verdict while
those inputs are unchanged; a new phase alone does not require another run.

**Every head that leaves this machine passes the `GATES:` list first**
(Phase 2, `run_gates.py`): the first push and each arbitration fix. Call
`run_gates.py` every time: a green run on the same gates file and the same
tree — a fix-mode implementer's, on the commit you are about to push —
left a receipt, and the call prints `REUSED` and runs nothing. Any edit,
commit, merge or new file outside `.discovery/dossiers/` changes the tree,
and the list runs again. `--fresh` forces a run; never pass it to escape a
receipt you believe in. A green
default-feature compile is not the list.

**First, check whether the target branch has moved — and if it has, sync
now.** Phase 9 syncs last, which is right for a build that finishes the same
day; a build spanning more than a day absorbs other people's merges the whole
time, and a sync deferred to Phase 9 lands them *after* the suite is green,
the review is closed and the ADRs are written — exactly when a conflict can
invalidate all three. So compare the target's tip against `baseline_commit`
— in committed mode against the `FORK:` sha, the tip `X` already carries —
here, and merge it in **before** you arbitrate anything: the tests are about
to run anyway, no review has been spent, and a conflict found here costs one
suite run where the same conflict at Phase 9 costs a suite run, a re-review
of the blast radius, and possibly an ADR rewrite. Phase 9 still syncs — the
target can move again; this is an early payment, not a replacement. Log it
either way — `SYNC: target unmoved at <sha>` or `SYNC: merged
<target>@<sha> mid-run, <N> conflicts`.

For each failure, decide who was wrong. **The contract is the referee**, and the
rule is mechanical so you cannot drift toward whichever side is easier to
change:

| The failure shows | Who is wrong | What you do |
|---|---|---|
| The test asserts something the contract does not promise | **the test** | Re-spawn the test author with a corrected payload by the host's mechanic (Phase 4): in place under opencode; under Claude Code it reads the file, then rewrites it whole with one `Write`. Never edit the test yourself — you have read the implementation, so you are exactly the wrong party to fix a test. **A re-spawn of a pre-existing test's author** — its hoisted expected value is assertion-bearing, so you may not edit it — carries `FAILURES:` (the failing output, verbatim) and `CORRECTION:` (the ordered edits: read the whole file, then `Write` it back with exactly those changes, nothing else) instead of folding the fix into `DELIVERY_CHANGE` (`references/payloads/integration-test-author.md`). |
| The implementation does not do what the contract promises | **the implementation** | Re-spawn the implementer for that package in `MODE: fix`, working in `X` directly — its own worktree is gone (Phase 5) and the tests are committed, so blindness no longer applies. The failure output travels as `FAILURES:` in the payload (`references/payloads/implementer.md`); `CRS:` carries any review change requests still open for it, or is omitted. **It runs alone in `X`**: never concurrently with a test author editing uncommitted files there — commit the author's work first, or wait for it. The recorded fix implementer saw the test file change under its `TEST_COMMAND`, reverted it with `git checkout --`, and destroyed a whole uncommitted fix round. |
| The contract is ambiguous enough to justify both readings | **you** | Fix the contract (and `PROMISE_CHECKLIST`, if a unit promise is involved) in the files and the dossier, commit on `X`, re-spawn the affected test author onto its file (Phase 4), and re-spawn **both** sides. |
| The test fails on harness noise — a compile error, a missing fixture, an import | nobody | Fix the harness yourself. It is mechanical. |
| The failure reproduces on the **target branch at the merge-base**, untouched by this build | nobody — **upstream** | Prove it first (below), then fix it on your branch to keep green, log `UPSTREAM:` with the proof, and say in the PR description that the fix belongs upstream independently of this change. |
| The failure is **intermittent** — `FLAKY` in a repeat run, or a red that a rerun of the same head turns green | **unknown until the mechanism is found** | Find the race (below), then rule on the mechanism: rows 1–3, or row 5 with a repeated proof. Never "nobody", and never a rerun until green. |

Write every arbitration into `## Build log`: the failure, the ruling, and which
of the six rows applied.

**Investigate flake risk before mutation checks and review.** Run
`python3 ${PLUGIN_ROOT}/scripts/flake_risk.py <every test file this build
added or changed>`, and log `FLAKE-RISK: <n> markers in <files> | none`.
A marker that the repo's `.flake-risk-exempt.md` covers does not count (the
summary line ends `(<e> exempt)`): a review has already ruled on it, with the
mechanism. When the scan warns `stale exempt row`, this build changed the line
that row named. Delete the row in this build, and the scan reads the line
afresh.

There is no mandatory two- or three-run whole-suite stability gate. One green
run is a suite verdict, not proof that every test is stable.

When a test flips, or a changed test's risk needs runtime investigation,
repeat the affected test or smallest reproducing group under the relevant
conditions with `run_tests.py --repeat N`. Preserve the command, conditions,
logs and outcomes. Expand to the full suite only when reproducing the risk
requires it. Log one line:

```
STABILITY: not repeated — no observed flake or runtime investigation due
STABILITY: targeted <command>, <N> runs on <sha> — <outcomes and log>; FLAKY <name> (failed X/N), ...
```

Each observed `FLAKY` test is a row-6 arbitration and counts against the
budget. An untouched test still needs the repeated row-5 proof before it is
called upstream. Keep the flake-risk markers and observed flips available to
the Phase 7 tests lens. Resolve observed flakes before treating mutant kills
as evidence.

Set `status: review` once the runner's verdict on the current relevant inputs
is green and the flake-risk scan and any required investigation are logged.

### Triggered parts — read the section only when its trigger fires

`ref_section` with `phase-6-conditional` and the heading:

- A suite run with `--vm`: "A `--vm` suite run gets a tier-calibrated timeout floor and a post-kill sweep".
- More than three failures in a round, or a ruling that rests on a count, a timing or a probe: "Past three failures, build the case files; a ruling rests on a measurement".
- A round with corrections to send (rows 1-3): "Correction rounds: carry the rulings, one workflow call, resume or re-spawn".
- A ruling of row 5 (upstream) or row 6 (intermittent), the proofs the table calls below: "Rows 5 and 6: the proof and the mechanism".
- A ruling of row 4 (harness noise): "Row 4: land your fix first, and the boundary is mechanical".
- A ruling of row 3 (ambiguous contract): "Row 3 is a lesson, not just a count".

**Checkpoint after each arbitration round.** A round ends when its rulings are
logged, its re-spawns have returned and been verified, and the runner's
verdict on the new head is in `## Build log`. Then call `phase_checkpoint` with
`Phase 6 — round <n>` and the hand-off: the head SHA, the verdict counts, every
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

**Mutation check — default on, scale it honestly.** The stub red-run proves
every test *can* fail; this check proves the suite catches *faults* — and
weak oracles are the known weakness of LLM-written tests, so skipping needs a
reason, not the other way round. Default on the **primary surface** (the
surface with the most `PROMISE_CHECKLIST` lines, or the one with branching,
concurrency, or a security or money path) every build; skip only mechanical
work, and log the reason.

**Run it yourself, the moment the suite first goes green.** On a throwaway
branch off `X` (`<branch>-MUT`, never merged, never pushed, removed when the
table is in — mutants run locally under either suite runner, because a
mutant is not a head anyone wants CI to remember),
derive the mutants from `PROMISE_CHECKLIST` in its strong form: **one mutant
per checklist line on the primary surface** — a return-meaning line gets a
wrong constant, an order line a swap, a named-guard line a dropped guard —
each a fault a real body could plausibly hide, never line noise a formatter
would catch, and — where a schema binds the surface — one the schema's own
constraints permit: a mutant the database rejects is killed by the
constraint, not by the test's assertion, and says nothing about the oracle.
Run the suite once per mutant, locally, and record the kill table. A
surface with more lines than one sitting is comfortable is **split across
sittings, never truncated**: cap how many mutants you apply before you stop
and record, never how many the surface is entitled to, and write the
unreached lines into `## Build log` — an unproven line is exactly what this
check exists to find.

Route each result:

- **`SURVIVED`** — first ask whether any reachable state distinguishes the
  mutant from the original. **None does → an equivalent mutant**: evidence
  about the code, not about the tests. Record it in `## Build log` with the
  proof — the constraint or invariant that makes the two behaviours
  identical; the recorded case dropped a `status = 'running'` conjunct under
  a check constraint tying `status` to `claimed_by` — and route nothing: a
  blind author asked to distinguish two behaviours that cannot differ
  returns `GAP:` by construction, and the round is spent. **Some state does
  → a missing or weak checklist line**: route it to the owning test author
  exactly like a `GAP:`, with the mutant and the surviving test named (a
  refused tool call on the way back follows the load-time rule: Phase 4).
  **Unclear → route it**: a wasted round beats an unexamined survivor.
- **A baseline that is not green** — the check did not run. That is exit-2
  semantics and never a pass: fix the baseline, or log why the check could
  not run.
- **Killed** — one `## Build log` line and done.

**Revert every mutant as you go, and prove the tree clean before you leave
the branch.** Nothing ever commits there, so a clean status is the whole
check. A mutant left behind by an interrupted run silently corrupts whatever
runs next — a red baseline read as a broken harness, or a kill table computed
against an already-mutated body, with no mechanical trace. One `## Build log`
line either way: mutants killed and survived, or skipped and the reason.
