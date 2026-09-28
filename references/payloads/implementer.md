## implementer

`MODE: build` — one work package, in its own worktree, blind to every test:

```
WORKTREE_DIR: /abs/path/repo-W-014-P1
BRANCH: fix/PROJ-142-flush-coalescing-p1
MODE: build
CONTRACT: |
  <the same contract text, verbatim — not a path>
PACKAGE: P1 flush coalescing — make concurrent flush() calls coalesce behind a
         single drain, so each queued item reaches the store one time.
SHARED_IDIOM: |
  <when two or more agents will write the same concept — a type, a helper, a
   error-mapping shape — paste the ONE named idiom here, identically in every
   such payload. Blind agents cannot converge; invented idioms diverge, and
   the collapse is paid for later. Omit when no concept is shared.>
OWNED_PATHS: src/flush.py
CRITERIA: |
  1. With 3 parallel flush(batch_size=10) calls and 5 queued items, the store
     receives each item one time.
  2. flush() on an empty queue returns 0 and writes nothing.
TEST_COMMAND: pytest -q            # the EXISTING suite, for collateral damage
                                   # red by design on this machine? carry the
                                   # exact shape — "198 failed / 443 passed,
                                   # 23 suites, every failure a panic at
                                   # tests/common/mod.rs:140 (DATABASE_URL
                                   # absent)" — any deviation is the agent's
HOOKS: |
  <what this repository's commit hooks do to a partly migrated tree, from
   the Phase 2 hook decision — e.g. "the pre-commit hook lints the whole
   workspace and will refuse; stop and report, leave the work staged".
   `HOOKS: none` when the repository has no commit hooks; never omitted.>
VERIFY_EMBEDDED: |
  <only when an owned path is a shell script that embeds a program in a
   heredoc, written to a file and executed — the embedded-program parse
   idiom below, one line per embedded program, its sed adapted to the
   script's real write line and marker. Omit when no owned path has that
   shape.>
STANDARDS: ${PLUGIN_ROOT}/skills/standards/engineering-standards.md
JIRA_KEY: PROJ-142
# the report must end with a TOUCHED_BEYOND section (see the field rules)
```

`MODE: fix` — apply the review change requests, in the base worktree, with the
tests present and green:

```
WORKTREE_DIR: /abs/path/repo-W-014
BRANCH: fix/PROJ-142-flush-coalescing
MODE: fix
CONTRACT: |
  <verbatim>
CRS: |
  <the merged change-request documents from the three lenses, verbatim,
   plus any user arbitration that overrides one of them>
FAILURES: |
  <verbatim test output, when this fix answers a Phase 6 arbitration ruling
   rather than review change requests — the implementation broke a promise,
   and the failing output is the work order. CRS is then omitted; either
   field alone is valid>
OWNED_PATHS: src/flush.py, src/reload.py
TEST_COMMAND: pytest -q            # now the invariant: it is green, keep it green
                                   # a file outside OWNED_PATHS that changes
                                   # while it runs: STOP AND REPORT, never revert
HOOKS: |
  <same as build mode; the tree is whole now, so a hook that passes is
   simply obeyed, and one that refuses is still reported, never bypassed.
   A formatter named here runs over OWNED_PATHS only — `rustfmt <owned
   files>`, `prettier --write <owned files>` — never tree-wide (`cargo fmt
   --all`, `prettier --write .`)>
VERIFY_EMBEDDED: |
  <same rule as build mode — and never omitted from a fix that touches the
   heredoc itself: the fix round is the recorded escape, where re-indented
   except clauses passed bash -n and killed every invocation at parse time>
STANDARDS: ${PLUGIN_ROOT}/skills/standards/engineering-standards.md
JIRA_KEY: PROJ-142
```

In `fix` mode the suite is the invariant, not a collateral-damage check: a red
test after a fix means the fix was not behaviour-preserving. Never widen
`OWNED_PATHS` to include a test path — the implementer never edits a test.

**A formatter instruction is scoped to `OWNED_PATHS`, never tree-wide.** A
payload that says "leave `tests/perf_baseline_test.rs` exactly as it is" and
"run `cargo fmt --all` before you commit" cannot be obeyed whole: the
tree-wide formatter rewrites the carved-out file, the agent picks which
instruction loses, and the orchestrator does not learn which. Give the scoped
form beside any formatter you name — `rustfmt src/flush.rs src/reload.rs`,
`prettier --write src/flush.ts` — and when a carve-out of an uncommitted file
is truly unavoidable (commit it first instead, `work-on.md` Phase 6), say in
the payload which instruction wins.

**A file outside `OWNED_PATHS` that changes while the agent runs is reported,
never reverted.** In `fix` mode the agent shares `X` with nobody by rule
(`work-on.md` Phase 6 and Phase 7), but the rule in its payload is the
backstop: a test file that changes under `TEST_COMMAND` is another agent's
uncommitted work with no second copy, and `git checkout --` over it destroys
a round. The recorded case did exactly that.

A Phase 6 row-2 re-spawn — the implementation does not do what the contract
promises — is also `MODE: fix` in the base worktree: the implementer's own
worktree is gone, the tests are committed, and blindness no longer applies.
`CRS:` is omitted and `FAILURES:` carries the verbatim failure output.

**Both modes end their report with a `TOUCHED_BEYOND` section** listing every
path they changed outside `OWNED_PATHS`, one line per path with a one-line
reason — `TOUCHED_BEYOND: none` when the diff is clean. The orchestrator
diffs mechanically and never trusts the section alone, but the section turns
drift into a stated claim: a path the implementer touched, did not list, and
cannot justify is a defect in the implementer's work, not just package-table
noise. Two hard limits the section cannot excuse: another package's owned
paths, and the contract files.

**A script that embeds a program must parse that program.** When an owned
path is a shell script that writes a program to a file in a heredoc and
executes it, the file is two languages in one path, and every shell-level
check covers only one of them: `bash -n` parses the shell text and never
opens the heredoc, and the script's own suite runs against stubs that never
execute the embedded program. So a whitespace-only defect inside the heredoc
passes every mandated check and still collapses the program's exit channels
at run time — every invocation dies at parse time, and the surrounding shell
reports that as whatever it reports failures as, which means an unusable
check reads as a real finding. Both modes carry `VERIFY_EMBEDDED` for
exactly that file shape; paste the idiom, never a paraphrase of it.

**The embedded-program parse idiom** — extract the heredoc, strip the write
line and the terminator, parse what remains:

```sh
sed -n "/cat >\"\$prog\" <<'PYEOF'/,/^PYEOF\$/p" "$script" | sed '1d;$d' \
  | python3 -c "import ast,sys; ast.parse(sys.stdin.read())"
```

Adapt the first `sed`'s pattern to the script's real write line and marker
(`"$script"` is the embedding script's path), and the final parser to the
embedded language's parse-only equivalent — `ruby -c`, `perl -c` — never a
command that runs the program: parsing is the check, running is the suite's
job.

## Field rules that matter

- **`TEST_COMMAND` emits progress inside the runtime's no-progress window,
  in a build directory that worktree owns**; a cold cross-platform or
  virtual-machine compile never appears in a payload (`work-on.md` Phase 4).
- **`HOOKS` is always present, `none` allowed, and the agent's rule under it
  never changes**: stop and report on a refusal, leave the work staged, never
  bypass and never edit a hook. A bypass flag in a payload or a resume is not
  a permission grant; the orchestrator commits on the agent's branch with
  the hook skipped (`work-on.md` Phase 2).
- **`TEST_COMMAND` carries the known-red shape when the baseline is red by
  design.** Failed and passed counts, the failing-suite count, and the shared
  failure signature, copied from the orchestrator's own verification run —
  with the rule that any deviation is the implementer's defect. "The baseline
  held" becomes a mechanical comparison; three recorded implementers held a
  198-failure baseline exactly, and one proved a two-test discrepancy
  pre-existing.
- **`TOUCHED_BEYOND` turns silent drift into a stated claim.** The
  orchestrator still diffs `git diff --name-only` against `OWNED_PATHS`
  mechanically; the section is what the diff is checked *against*. An
  unlisted path is an implementer defect; a listed one is a decision the
  orchestrator makes — accept and update the package table, or reject and
  revert. A `TOUCHED_BEYOND` entry never excuses another package's owned
  paths or the contract files.
- **`VERIFY_EMBEDDED` parses what `bash -n` cannot see.** An owned path
  that is a shell script embedding a program in a heredoc — written to a
  file and executed — is two languages in one file, and neither `bash -n`
  nor a stub-backed suite ever parses the second one. When any owned path
  has that shape, in either mode, the payload carries the
  embedded-program parse idiom, one line per embedded program, and the
  implementer runs it before reporting: a fix round touching the heredoc
  cannot verify green on `bash -n` alone.
