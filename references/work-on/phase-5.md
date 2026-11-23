<!-- /work-on, Phase 5. Read at the start of this phase; the rules that hold
     in every phase stay in primary-agents/work-on.md. -->

## Phase 5 — Test strength gate, then merge into the base branch

**Spawning and workflows.** The rules for spawning an agent and for the two plugin
workflows are in `phase-4-conditional.md`: `ref_section` with `phase-4-conditional` and the headings
"Spawning under Claude Code" and "Under Claude Code — two workflows".

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
   opencode; read-then-rewrite under Claude Code) — or record the
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
     `bash` is denied. Commit through `gate_commit.py --root X --paths
     <TEST_PATHS> -m MESSAGE`, adding `--forbid-id <dossier id>` in local
     mode so no committed test cites the dossier. `gate_commit.py` snapshots the
     authors' bytes and compares them with what was committed: exit 3 with
     `HOOK-REWROTE-TEST: <file>: \`old\` -> \`new\`` means a commit hook (a
     spell fixer with `--write-changes`, a formatter) changed a test file
     after its author wrote it. A changed literal is a changed oracle, and a
     green suite after it is no evidence: restore the author's bytes, exempt
     them in the hook's config by the narrowest pattern, and commit again
     (#262). Then run the new tests there — the new files only,
     locally, under either suite runner (Phase 2): this run is not a verdict
     on the suite, and CI cannot run a subset against stubs. The bodies are
     still stubs that fail loudly, so **every new test must fail, judged one test at
     a time — never by a failure count, which a collection error also
     satisfies**. For a Terraform target, judge each run block alone:
      `terraform test` skips every run after a failed `expect_failures` run,
      so a file's counts cannot be read at all — run
      `python3 ${PLUGIN_ROOT}/scripts/tftest_runs.py <file.tftest.hcl>`,
      which copies the module to a scratch dir per run block (keeping the
      mock_provider and variables blocks), inits and tests each, and prints
      one `PASS|FAIL <run name>` line per run. A test that passes against a stub is vacuous — it asserts
     nothing the implementation controls — and blocks a real failure from
     being noticed later; route it by the rule below. Fix pure harness noise
     (imports, fixtures, collection errors) yourself now, so Phase 6
     arbitrates real disagreements only.

   **Judge each red by its panic site (#523).** A failure counts as red for the
   stub only when its first frame inside the test file is the call into the
   code under test (or an assertion after it). A panic or error raised before
   that call, in a fixture, a builder or a parse of a literal the type's parser
   rejects (`parse().unwrap()` on a typed identifier), proves nothing about the
   stub: record that test as **UNPROVEN (fixture-site)**, route the fixture to
   its author by the table below with the offending literal named, and prove
   the test red again — on the stubs, or with a mutant — before its later pass
   counts. A failure count that includes such tests overstates the red-run (one
   build counted 67 fixture-parse panics as red and lost the proof).

   **A skipped test is NOT VERIFIED, never green and never a keep (#488).** A
   test that returned early through the repository's stack-or-skip gate (no
   database, env script withheld, a `*_TESTS=skip` switch) is neither red nor
   green. Run with `run_tests.py --skip-banner REGEX` (the line the test prints
   when it returns early; a stack config's `skip_banner` joins it): the digest
   then reports `skipped N (NOT VERIFIED, not passed)` apart from `passed N`,
   and `--expect green` refuses. A test that is "green before the
   implementation" because it was skipped is not "real contract code": rule it
   NOT VERIFIED in the strength-gate record and the red-run record, and defer
   its criterion to the run that has the environment.

**Routing a vacuous test — one rule, everywhere it comes up.** A vacuous test
is found at the stub red-run, by the weak-pattern grep, or at a Phase 6
arbitration, and the mechanic is the same in all three places. It turns on
**how much of the file is wrong**, never on which check found it:

| What is vacuous | What you do |
|---|---|
| **Individual tests** in a file whose other tests are sound | re-spawn the author onto that file with **the tests named** and the complete intent. Under opencode it reads its own earlier output and edits in place — no delete, nothing pasted. Under Claude Code it reads its earlier output (its `Read` reaches the test families) and rewrites the file whole with the changed tests named — no delete; `compose_payloads.py` raises `MAX_SINGLE_EDIT` to cover the largest existing `TEST_PATHS` file plus a margin (a whole-file `Write` is its only edit), so no hand-raised limit. |
| **The whole file** — a wholesale contract change, or every test in it asserts nothing | **delete it with `safe_revert.py --delete`** (Phase 4), which copies it outside the repository first; say so in the payload, and let a fresh `Write` recreate it — a re-spawn that stalls after a bare delete leaves the run at zero tests, and the recorded case was saved only by an unrelated commit. The same on either host. |

**Under Claude Code, a round that corrects two or more authors is one
`phase4-wave` call** with every corrected payload (tests named, composed and
linted as today), per `phase-4.md` ("Under Claude Code, resume instead of
re-spawn"). A round of one author may instead name it by its agent ID and resume
it with the tests named; a changed contract is a full re-spawn.

Deleting a file to fix one weak test throws away every sound test beside it
and pays a full blind regeneration for them, on either host — so name the
tests instead.

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

Then merge every implementer branch into `X` with one call, run from the build worktree `X` (`<ID>` is the bare dossier ID, e.g. `W-014`):

```bash
python3 "${PLUGIN_ROOT}/scripts/merge_packages.py" --worktree <X> --dossier <ID> \
  --branch <P1>=<its implementer branch> --branch <P2>=<its implementer branch> \
  --log <live dossier>
```

Give one `--branch PKG=BRANCH` per package you merged (the branch is the payload's `BRANCH:` field), plus `--package-worktree <PKG>=<path>` to have a merged package's worktree removed, and `--expected-hook-output <GLOB>` for each `EXPECTED_HOOK_OUTPUTS` glob. Always pass `--worktree`, `--dossier` and the full `--branch` list yourself; a call with missing flags stops without merging. The script merges the branches one at a time in `Depends on` order. Before each merge it checks scope mechanically: the branch's diff against `X` must list only paths inside that package's `OWNED_PATHS`. A path outside them is the same signal as a conflict (the package table or the implementer drifted), so the script stops before that merge, merges nothing blind, prints the paths and exits 2: you take over. A conflict also stops it with exit 2: it names the conflicted paths and aborts that merge. Accepted hook outputs are listed, not judged. Earlier merges stay in place when a later one stops the run; once you have ruled, run the call again for the packages not yet merged.

**A path outside `OWNED_PATHS` is a `TOUCHED_BEYOND` decision, and the
implementer's report already carries its side** (the section's shape and its
two hard limits: `references/payloads/implementer.md`). Diff its list against your
mechanical diff and rule on every path that appears: **accept** — the touch
was legitimate, so update the package table in the dossier to own it and note
that it enters the blast radius (Phase 7) — or **reject**, and have the
implementer revert it or raise `CONTRACT-CHANGE:` if the contract itself
forced the touch. A path outside `OWNED_PATHS` that the report does not list
is an implementer defect, the same signal as a conflict. The exception is a
path listed as hook output that matches an `EXPECTED_HOOK_OUTPUTS` glob
recorded at Phase 2: accept it without a judgement call, no package-table
change. You still run the repository's own checker over it (the anchor checker,
for a doc-anchor fixer) before the merge.

Disjoint owned paths mean a conflict should be impossible. **A conflict is
therefore a signal, not a chore**: it proves the package table was wrong. Log
it, resolve it, and fix `## Work packages` so the next run does not repeat it.

Remove each implementer worktree once its branch has merged.

**Driver (`ASD_DRIVER_STRENGTH_GATE_PREP` is `on` or `shadow`).** When `on`, run `python3 "${PLUGIN_ROOT}/scripts/strength_gate_prep.py" --dossier <ID> --worktree <X> --test-path <TEST_PATHS file> [--test-path <next file> ...] [--report <unit author report>] [--test-cmd '<command that runs the new tests only>']` (one `--test-path` per file in the test authors' payloads' `TEST_PATHS:`, relative to X; omitted, the driver collects them from `<X>/.agent-staging/payloads/*.md`) as evidence for the strength gate (coverage, weak-pattern flags, per-test stub red-run); the ruling stays yours. In `shadow`, do the strength gate by hand exactly as today, then run that driver once afterwards with the same command (no `--log`) and ignore its output for every decision; it works in a throwaway worktree and saves evidence for the rollout report. A call with missing flags only records a surprise and tells the rollout nothing, so copy the command whole. `off` or unset: ignore this paragraph.
