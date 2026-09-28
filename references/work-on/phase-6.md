<!-- /work-on, Phase 6. Read at the start of this phase; the rules that hold
     in every phase stay in primary-agents/work-on.md. -->

## Phase 6 — Run the tests, and arbitrate with the contract

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
Under `local`: run the full suite in `X` yourself, exactly as the
implementers' `TEST_COMMAND` runs it, through `python3
${PLUGIN_ROOT}/scripts/run_tests.py --log <file> -- <TEST_COMMAND>` (add
`--vm <name> --workdir <X> --env-script <file>` for a suite that runs only
in a Linux VM). It writes the whole output to the log and prints the summed
counts and each failure, bounded. Log `SUITE: local on <sha> — <N> failed /
<N> passed`. Every later "the runner's
verdict" in this document means exactly this — the runner's run on the new
head, read the same way — and never a local run beside a CI run of the
same head.

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

**Past three failures, build the case files before you rule on any of them.**
One fixed-field case file per failure, written into `## Build log`: the
failing assertion verbatim, the implementation region, the governing contract
promise verbatim, the output tail, an `INSIDE-ASSERTION` yes/no (yes: the
output shows an evaluated assertion; no: harness noise fired before any
assertion ran; `UNKNOWN` only when the output is silent), and a factual note.
Assembling the evidence for all of them first is what stops you ruling on
failure one with failure four's cause still unread.

**A ruling rests on a measurement only after the harness that took it was
shown to fail and shown to run.** When a ruling depends on a count, a timing
or a probe you took yourself, run the check on a known-bad input first and
watch it fail, confirm it prints one result line per case, and derive every
label it prints from the measurement rather than from the loop variable that
names a step. Prefer the repository's own verdict scripts to a throwaway
probe, and paste the negative control into the case file beside the reading.
The recorded failure ruled on labels that were never elapsed times, re-spawned
both sides twice, and reversed itself two rounds later; a ruling reversed
because the harness lied counts against the arbitration budget twice.

For each failure, decide who was wrong. **The contract is the referee**, and the
rule is mechanical so you cannot drift toward whichever side is easier to
change:

| The failure shows | Who is wrong | What you do |
|---|---|---|
| The test asserts something the contract does not promise | **the test** | Re-spawn the test author with a corrected payload by the host's mechanic (Phase 4): in place under opencode; delete-first with `safe_revert.py --delete`, then a fresh whole-file `Write`, under Claude Code. Never edit the test yourself — you have read the implementation, so you are exactly the wrong party to fix a test. **A re-spawn of a pre-existing test's author** — its hoisted expected value is assertion-bearing, so you may not edit it — carries `FAILURES:` (the failing output, verbatim) and `CORRECTION:` (the ordered edits: read the whole file, then `Write` it back with exactly those changes, nothing else) instead of folding the fix into `DELIVERY_CHANGE` (`references/payloads/integration-test-author.md`). |
| The implementation does not do what the contract promises | **the implementation** | Re-spawn the implementer for that package in `MODE: fix`, working in `X` directly — its own worktree is gone (Phase 5) and the tests are committed, so blindness no longer applies. The failure output travels as `FAILURES:` in the payload (`references/payloads/implementer.md`); `CRS:` carries any review change requests still open for it, or is omitted. **It runs alone in `X`**: never concurrently with a test author editing uncommitted files there — commit the author's work first, or wait for it. The recorded fix implementer saw the test file change under its `TEST_COMMAND`, reverted it with `git checkout --`, and destroyed a whole uncommitted fix round. |
| The contract is ambiguous enough to justify both readings | **you** | Fix the contract (and `PROMISE_CHECKLIST`, if a unit promise is involved) in the files and the dossier, commit on `X`, re-spawn the affected test author onto its file (Phase 4), and re-spawn **both** sides. |
| The test fails on harness noise — a compile error, a missing fixture, an import | nobody | Fix the harness yourself. It is mechanical. |
| The failure reproduces on the **target branch at the merge-base**, untouched by this build | nobody — **upstream** | Prove it first (below), then fix it on your branch to keep green, log `UPSTREAM:` with the proof, and say in the PR description that the fix belongs upstream independently of this change. |

**Row 5 needs its proof, or it is not row 5.** Row 4 — "it's only a compile
error, it's mechanical" — has no proof obligation and tempts every failure
that feels like somebody else's fault, so row 5 carries one: check out the
pristine merge-base in a throwaway worktree, run the failing test there —
one test, locally, under either runner — and record the result. Under `ci`
the target's own run at the merge-base commit, where CI kept one (`gh run
list --branch <target> --commit <sha>`), is the same proof at no cost; read
its log whole before you cite it. Reproduces → row 5, and the `UPSTREAM:` line names the
merge-base commit and the upstream cause. Does not reproduce → the failure is
yours, routed through rows 1–4. Never rule row 5 from the shape of the error;
a target branch that moved under you breaks code in ways that look exactly
like your own defects.

Write every arbitration into `## Build log`: the failure, the ruling, and which
of the five rows applied.

**Land your own row-4 fix before any fix agent enters `X`.** A `MODE: fix`
payload that carves out an uncommitted file of yours while also naming a
formatter conflicts with itself: `cargo fmt --all` reformats the carved-out
file, and the agent must pick which instruction to disobey — the recorded
agent chose well and said so; a quieter one reformats your work, and you find
out when your own diff is dirty for a reason you cannot place. Commit the
harness fix first, so no carve-out is needed; where one is unavoidable, the
payload says which instruction wins, and the formatter is scoped to
`OWNED_PATHS` in every case (`references/payloads/implementer.md`).

**Row 4's boundary is mechanical, not a judgement call.** The fuzzy edge of
"harness noise" is where silent test-editing hides, so settle it with
`scripts/check_harness_edit.py`: before you class a failure as row 4 and fix
it yourself, capture the fix as a diff and run
`check_harness_edit.py --diff ORIG MOD` (or `--patch` over the patch).
Exit 0: only harness lines —
imports, module setup, fixtures, collection wiring — and row 4 stands. Exit
1: an assertion-bearing line, so the failure belongs to rows 1–3 however
noise-like it looked, which means a re-spawn, never your own edit. Exit 2:
the check could not read the change, or a changed binding is read by an
assertion it cannot place — rule conservatively (rows 1–3). The
`INSIDE-ASSERTION` field on each case file asks the same question from the
output side; **when the two disagree, the conservative ruling is mandatory** —
rows 1–3, a re-spawn, never your own edit — and you read the test and the
output before you record it. **A hoisted expected value is assertion-bearing
whatever the checker returns**: `let embedded = (1..=14).collect();` one line
above `assert_eq!(applied, embedded)` is the assertion's value, and changing
the `14` rewrites what the test demands. The checker flags a changed binding
an assertion in the same function reads (exit 1) and one read elsewhere in
the file (exit 2); the rule stands where it misses. Row 5 narrows this check
rather than escaping it: an upstream failure
is still fixed by you, so the same `check_harness_edit.py` gate decides what
your fix may touch. Row 5 changes who *caused* the failure, never who may
edit an assertion.

**Row 3 is a lesson, not just a count.** When the contract was ambiguous, record
*what kind* of ambiguity it was — the promise you failed to make observable, and
the sentence that would have prevented the failure:

```
ARBITRATION 3 — row 3 (contract ambiguous).
  Failure: test asserted flush() returns the count; implementation returned None.
  Ambiguity: the docstring said "drains the queue" and never named a return value.
  Lesson: a method with a return type states what the value MEANS, not just that
          one exists.
  Fix: docstring now reads "Returns the count of items written."
```

That lesson is **not ADR material** — an ADR records a decision about the code,
and this is a decision about how we write contracts. Phase 8 routes it to the
right place. Two or more row-3 rulings in one run means the next `/plan` needs a
sharper contract, and `/overview-dossiers` surfaces the count as a health signal.

Set `status: review` once the runner's verdict on the current head is green.
**Budget: 3 arbitration rounds.**
After the third, stop and show the user the failures and your rulings;
continuing past the budget needs the user's explicit sign-off, recorded in
`## Build log`. Most budget exhaustion is one repeated ambiguity or a
criterion this machine cannot observe; the user names the substitute.

**Repeat-fingerprint — the early stop one round before the budget.** Track
the site of every arbitration: the contract member and the test file. When
the same member or the same test file is arbitrated a **second** time — even
with a different symptom — stop and take it to the user there, one round
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
