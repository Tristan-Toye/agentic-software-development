<!-- /work-on, Phase 5. Read at the start of this phase; the rules that hold
     in every phase stay in primary-agents/work-on.md. -->

## Phase 5 — Test strength gate, then merge into the base branch

**Before any merge, prove the new tests can fail.** Blindness guarantees the
tests are independent; this gate is what checks they are strong. Both checks
are cheap, and both run while the bodies in `X` are still stubs:

1. **Promise coverage.** The unit author's report maps each test to a line in
   `PROMISE_CHECKLIST`. Diff that map against the checklist you wrote at
   Phase 3 — not a fresh re-read of the contract, so both sides of the diff
   come from the one derivation. An uncovered line means Phase 3's checklist
   should have caught it and did not: fix the checklist (and the contract, if
   the gap traces back that far), re-spawn the author onto its file with the
   corrected checklist by the host's mechanic (Phase 4: in place under
   opencode; delete-first under Claude Code) — or record the
   accepted gap in `## Build log`. Do the same for the integration author's
   map against the acceptance criteria. **When the repository owns a
   coverage script, discover its real CLI before the first invocation** —
   read its `--help` and use the flags it actually takes. The plugin's flag
   shape (`--dossier`, `--root`) is a default, not a contract: the recorded
   run invoked the repository's own `check_promise_coverage.py` with the
   plugin's flags twice, and the second run resolved the test paths against
   the wrong checkout and reported 0 covered / 29 uncovered — a coverage
   FAIL that read like a fan-out defect.
2. **The stub red-run.** Commit the test authors' work on `X`: delete
     `.agent-staging/` first; check scope mechanically — every path in `X`'s
     working tree (`git status --porcelain`) is a named `TEST_PATHS` entry or
     a harness fix you logged, and anything else is reverted, not negotiated;
     format each author's files with the repository's own formatter, over
     exactly the named files and inside that same commit, because the author's
     `bash` is denied. Then run the new tests there — the new files only,
     locally, under either suite runner (Phase 2): this run is not a verdict
     on the suite, and CI cannot run a subset against stubs. The bodies are
     still stubs that fail loudly, so **every new test must fail, judged one test at
     a time — never by a failure count, which a collection error also
     satisfies**. A test that passes against a stub is vacuous — it asserts
     nothing the implementation controls — and blocks a real failure from
     being noticed later; route it by the rule below. Fix pure harness noise
     (imports, fixtures, collection errors) yourself now, so Phase 6
     arbitrates real disagreements only.

**Routing a vacuous test — one rule, everywhere it comes up.** A vacuous test
is found at the stub red-run, by the weak-pattern grep, or at a Phase 6
arbitration, and the mechanic is the same in all three places. It turns on
**how much of the file is wrong**, never on which check found it:

| What is vacuous | What you do |
|---|---|
| **Individual tests** in a file whose other tests are sound | re-spawn the author onto that file with **the tests named** and the complete intent. Under opencode it reads its own earlier output and edits in place — no delete, nothing pasted. Under Claude Code the author has `Write` alone, so even this row is delete-first: `safe_revert.py --delete` the file (it copies it out), then a fresh whole-file `Write` with the changed tests named (Phase 4). |
| **The whole file** — a wholesale contract change, or every test in it asserts nothing | **delete it with `safe_revert.py --delete`** (Phase 4), which copies it outside the repository first; say so in the payload, and let a fresh `Write` recreate it — a re-spawn that stalls after a bare delete leaves the run at zero tests, and the recorded case was saved only by an unrelated commit. The same on either host. |

Deleting a file to fix one weak test throws away every sound test beside it
and pays a full blind regeneration for them — under opencode, so name the
tests instead. Under Claude Code the regeneration is paid regardless,
because the author cannot read what it wrote; naming the tests still tells
it what changed, and the split into small files (Phase 3) is what keeps that
regeneration cheap.

Log all results in `## Build log`: promises covered, tests red, vacuous
tests caught and how each was routed.

**Weak-pattern flags — a sampling guide, not a gate.** Grep the committed test
files for the assertion shapes that pass while asserting almost nothing:
`is_some(`, `is_ok(`, `len() > 0`, a bare `assert!`/`assert` with no
comparison behind it. Each hit is a place to read, not a defect on its own:
open that test, hold its assertion against the `PROMISE_CHECKLIST` line's
strong form (Phase 3), and if the test cannot state the concrete value the
line names, treat it as vacuous and route it by the table above. The grep
tells you where to point your reading; a clean grep proves nothing on its own.

**Audit the audit — index every checklist line yourself.** Read the
now-committed test files and write one index line per `PROMISE_CHECKLIST`
line — `covered`, `WEAK?`, `no-test-found`, `vacuous?` — each with the
assertion quoted verbatim. Paste the index into `## Build log`, check the
arithmetic (every checklist line exactly once), and diff its counts against
the author's self-report: a disagreement is a forced full read of that file.
The primary surface's test file(s) you read **in full**, never windowed.

Then merge each implementer branch into `X`, one at a time, in `Depends on`
order. Before each merge, check scope mechanically: `git diff --name-only
X..<branch>` must list only paths inside that package's `OWNED_PATHS`. A path
outside them is the same signal as a conflict — the package table or the
implementer drifted — so resolve the drift first; never merge it blind.

**A path outside `OWNED_PATHS` is a `TOUCHED_BEYOND` decision, and the
implementer's report already carries its side** (the section's shape and its
two hard limits: `references/payloads/implementer.md`). Diff its list against your
mechanical diff and rule on every path that appears: **accept** — the touch
was legitimate, so update the package table in the dossier to own it and note
that it enters the blast radius (Phase 7) — or **reject**, and have the
implementer revert it or raise `CONTRACT-CHANGE:` if the contract itself
forced the touch. A path outside `OWNED_PATHS` that the report does not list
is an implementer defect, the same signal as a conflict.

Disjoint owned paths mean a conflict should be impossible. **A conflict is
therefore a signal, not a chore**: it proves the package table was wrong. Log
it, resolve it, and fix `## Work packages` so the next run does not repeat it.

Remove each implementer worktree once its branch has merged.
