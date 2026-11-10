<!-- /work-on, Phase 6, triggered parts. phase-6.md names each trigger; read the
     one section with ref_section, never the whole file. -->

# /work-on Phase 6 — triggered parts

Parts of Phase 6 that apply only when their case arises. `phase-6.md` names each trigger.

### A `--vm` suite run gets a tier-calibrated timeout floor and a post-kill sweep

**A `--vm` suite run gets a tier-calibrated timeout floor and a post-kill
sweep.** `run_tests.py --vm` tears the remote runner down on host-side exit;
that is the first line of defence, not the only one. Set the timeout from the
tier, never the default: a `--stack` tier that has once taken more than 2h gets
at least 3h plus margin. After any kill or timeout, and before any re-run,
sweep the VM: look for a living runner process, the lease `.holder` sidecar,
and the suite-window flock. Kill the runner, remove the stale `.holder`, and
release the flock (or wait for its owner) before starting again. A resumed run
never starts a suite into a window another runner still holds.

### Past three failures, build the case files; a ruling rests on a measurement

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

### Correction rounds: carry the rulings, one workflow call, resume or re-spawn

**A fix round carries its row-1 rulings to the implementers.** When you
compose a fix round, put each row-1 ruling of that round (a test ruled a test
defect) into the payload of every implementer whose owned paths that test
exercises, under `FAILURES` as a `TEST-DEFECTS:` sub-list: the test name, then
"ruled a test defect — do not change code for this test". Without it the
implementer bends the code to a test that is already wrong.

**Under Claude Code, a round of two or more corrections in rows 1–3 is one
`phase4-wave` call** with every corrected payload (`FAILURES` + `CORRECTION`
folded in, composed and linted as today; an implementer's payload carries the
`WORKTREE_DIR` it now works in). A round of one agent is a **resume** whenever the
agent's ID is known and the contract is unchanged: `SendMessage` to the test
author or the implementer with only the correction (for the implementer, the
`WORKTREE_DIR` it now works in), per `phase-4.md` ("Under Claude Code, resume
instead of re-spawn"). A re-spawned test author after the merge takes its
`CONTRACT` from the dossier's `## Contract`, never from the contract files
(they hold implementation bodies now): compose with `--dossier <live dossier>`
and `--contract-extra "<subsection heading>"` (repeatable) and no
`--contract-paths`, which pastes those `## Contract` subsections as CONTRACT and
hashes them; or `--refresh-contract` with `--dossier`, which refuses the files
route once its status is `review`. An agent spawned by a workflow has no resumable id —
not even one scavenged from its transcript directory; correct it with a full
re-spawn through the same workflow (a single one too), `FAILURES` + `CORRECTION`
folded into the payload. A row-3 contract fix changes the contract, so the
affected agents are re-spawned with the full payload and the new
`CONTRACT_HASH`.

### Rows 5 and 6: the proof and the mechanism

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
like your own defects. **For an intermittent failure one run proves
nothing**: a flaky test passes at the merge-base most of the time. Run it
there with the same `--repeat N` that showed it flip here, and rule row 5 only
when it flips there too.

**Row 6 needs a mechanism, or it is not a ruling.** An intermittent failure is
a race between two things, and a ruling names both: the interleaving or the
load condition, and where it lives. Reproduce it first with `run_tests.py
--repeat N` on the smallest reproducing test group under the relevant load;
use the whole suite when that load is necessary to reproduce it. Put the
`FLAKY name (failed X/N)` line and reproduction command in the case file. Then rule on where the race lives, **product first**:

- **In the code under test** — the contract promises an outcome and the code
  does not deliver it on every interleaving → row 2. The recorded "flaky" lock
  test failed only under full-suite load and was settled by a retry inside the
  test across five builds; the cause was a product fault — a child process
  forked by another thread inherited the lock's descriptor and held the lock.
- **In the contract** — it is silent about concurrency, ordering or when an
  effect becomes visible, and the test and the code read that silence
  differently → row 3, and the lesson names the missing promise.
- **In the test** — it sleeps to wait for something, asserts a wall-clock bound
  under one second, shares a resource, a name or global state with another
  test, depends on test order, uses unseeded randomness, or reads before the
  signal that ends the background work → row 1. The author's correction names
  the mechanism and the deterministic form (join, await, poll with a deadline,
  an own resource, a seed); `STANDARDS` rule `TCG 4001` is the citation.

A fix is proven by the same repeat: the test that failed `X/N` passes `N/N` at
a count no smaller than the one that showed it. **Never accept a fix that only
hides the race**: a retry loop in the test, a longer sleep, a bound raised to
absorb the slowness that was observed (raising a sub-second bound to one second
is the rule, not a workaround), an ignored or skipped test, or a CI rerun until
green. When a round ends with no mechanism found, take the case file to the
owner as one question (`--kind uncertainty`); "flaky" is not a ruling.

### Row 4: land your fix first, and the boundary is mechanical

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

### Row 3 is a lesson, not just a count

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
