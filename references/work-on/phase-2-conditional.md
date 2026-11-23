<!-- /work-on, Phase 2, triggered parts. phase-2.md names each trigger; read the
     one section with ref_section, never the whole file. -->

# /work-on Phase 2 — triggered parts

Parts of Phase 2 that apply only when their case arises. `phase-2.md` names each trigger.

### Making X when stacked on a plan PR, or when the plan already committed the stubs

**Stacked on an open plan PR (`plan-branch=` in `$ARGUMENTS`).** The re-plan
is on `plan/<KEY>`, not merged yet. Fork `X` from the target as above, then
bring in only the plan's own commits, never the base it forked from:

```
git fetch origin <plan-branch>
git -C <X> cherry-pick -x $(git rev-list --reverse --no-merges origin/<plan-branch> --not origin/<plan-base>)
```

`<plan-base>` is the branch the plan PR targets
(`gh pr view <plan-branch> --json baseRefName`, else `development`). The
commits touch only `.discovery/` and `.gitignore`; a conflict there is the
target's older copy of this dossier — keep the plan branch's side. Log
`PLAN-STACK: cherry-picked <n> plan commit(s) <shas> from <plan-branch>`
and name the plan PR in this build's PR body ("stacked on #<n>: merge it
first, or with this one"). The plan PR and this branch then carry the same
dossier change, which merges cleanly into development whichever lands
first. The live copy is `X`'s from here, as above.

**The plan already committed the stubs (`flow: 2` with `contract_commit`
set).** `/plan` Phase 5 materialised the contract on the dossier's own
`branch:` and recorded that commit; the contract-reviewer checked it
there. Reuse it — never materialise a second time. Keep the branch name
the dossier records (do not derive a new one):

- **Local mode:** the branch is in this checkout with the stubs commit on
  top. Check it out as `X`: `git worktree add ../<repo>-<ID> <branch>`.
- **Committed mode:** the plan PR pushed the branch. Fork from the target
  and bring in the stubs commit (after the plan commits, when stacked):

  ```
  git fetch origin <target> <branch>
  git worktree add ../<repo>-<ID> -B <branch> origin/<target>
  git -C <X> cherry-pick -x <contract_commit>
  ```

Log `CONTRACT-REUSED: <contract_commit> from /plan`. A cherry-pick
conflict means the target changed a stub file after the plan (the Phase 0
refresh did not see it as a contract change): `git cherry-pick --abort`,
log `CONTRACT-REUSED: conflict — materialised again`, and materialise
from `## Contract` as below. When `contract_commit` is `null` or the
dossier has no `flow: 2`, make `X` as above and materialise.

### A new test file's runner registration is contract wiring you own

**A new test file's runner registration is contract wiring you own (#464).**
When the contract creates a test file that the target's gates require to be
registered (a nextest test group for a process-wide guard static, a CI
test-axis pin, a spec list a self-test compares), name the registration in
the contract and commit it in the contract commit, beside the empty test
file. The blind test author cannot edit the runner config and never runs the
gates, so a registration left to the merge fails as a row-4 (harness wiring)
arbitration in Phase 6. Run the registration gates in Phase 3 against a tree
where the guard idiom the payload's `FIXTURES` prescribe is assumed present,
not against the empty stub alone (an empty test file keeps them green). Log
`RUNNER-REG: <file> -> <group, pin or list> — <config file>` into
`## Build log`, or `RUNNER-REG: none — <why the target registers nothing>`.

### A stub that extends a base class outside the contract inherits that base's decisions

**A stub that extends a base class outside the contract inherits that
base's decisions.** When the subclass overrides a hook of a template
method, read the template method and trace every edge promise you wrote
on the subclass (zero items, the empty case, duplicates, an invalid or
missing value) through it: a case the base raises on, refuses, or returns
early from never reaches the subclass body, so the promise must say what
the base does, or go. Note the base's `path:start-end` for the reviewer's
`BASE_TEMPLATES` (Phase 3).

### A shape change compiles every library at the contract commit

**A shape change compiles every library at the contract commit.** When
`## Contract` changes a type other code builds or matches on (a field's
type, a trait method's signature, a constructor's arity), the contract
commit carries the type-forced edits at every non-test call site, so the
libraries, bins and examples compile (the tests need not) on every gate
below. A package that owned only such edits leaves the table, and call-site
test packages depend on nothing: they fork in the same wave. This is a
revision; rule on it as the next paragraph says.

### Read the repository's commit hooks before the contract commit, and decide the hook policy

**Read the repository's commit hooks before this commit, and decide the
hook policy for the whole fan-out now.** From this commit until the last
package merges the tree is mid-migration by construction, so a hook that
compiles, lints or auto-fixes **workspace-wide** cannot pass on it, and an
auto-fixer that runs anyway rewrites files nobody owns — the recorded cases
renamed a stub's parameters inside the contract commit and reformatted a
blind author's test in a fix round. So: diff this commit against your own
edits before any worktree forks from it and revert what the hook wrote;
skip a workspace-wide hook on your own commits with the repository's bypass
flag, run its fixers by hand over the files you changed, and say so in the
message. Every implementer payload carries `HOOKS`
(`references/payloads/implementer.md`): **the agent stops and reports when a hook
refuses its commit**, and you commit on its branch. Never put the bypass in
a payload or a resume — a sub-agent's permission is not granted by an
instruction, and the recorded attempt paid a round trip to learn it. When
the hook's own runtime approaches the runtime's no-progress window, tell
the implementer to stage and report without committing. When a hook step
writes the same paths outside the code on every commit (a doc-anchor fixer
over `docs/architecture/`), record its globs here once, as `<step>: <glob>`
— every payload inherits them as `EXPECTED_HOOK_OUTPUTS` (pass
`--hook-outputs STEP:GLOB` to `compose_payloads.py`), and an implementer
whose hook rewrote only those paths commits through. A glob must not touch
any package's `OWNED_PATHS`. Record the policy as front matter before the
fan-out: `dossier_edit.py set <dossier> 'hooks=none'` or
`'hooks=<hook> — <policy>'`; `validate_pipeline.py --pre-fanout` refuses
without it. Right after the contract commit, record
`contract_commit=$(git rev-parse HEAD)` the same way. With the plan's
stubs reused, the cherry-picked commit (plus any step-3 revision, as a
commit on top) is the contract commit: record its new sha, so
`contract_commit` names the commit on `X` the fan-out forks from.

### The repository's PATH needs on a gates run

Put the repository's PATH needs on the run, never inside each line: when
its toolchain is not on a non-interactive shell's PATH (cargo under
`~/.cargo/bin`), pass `--path-prepend "$HOME/.cargo/bin"` (repeatable) and,
for anything more, `--env-script <file>` on every `run_gates.py` call, the
flags `run_tests.py` has. A gate that exits 127 is reported as "command not
found — environment, not a gate failure": fix the flags, do not read it as
red. A list too long for one tool call (the Bash cap is 10 minutes) runs
with `--detach` (prints a run id, returns at once) and then
`--wait <id>` (prints the foreground summary; exit 6 means still running,
call it again: one call blocks at most 270 s, so a sub-agent's 5-minute prompt
cache stays warm). `--wait` is the one way to wait: a blocking call that
returns when the run ends, or at its cap with exit 6. Never poll instead (no `sleep`/`pgrep`/`until`
loop, no repeated status turns, no reading the run's state directory); the
only second call is `--wait <id>` again after an exit 6.

### Kanban delivery

A dossier with front matter `delivery: kanban` ships through `development` with CI as its only proof; the
integrated stack proof waits for the next `/stub` run (which lists this PR as a suspect). The rest of the flow
is unchanged: contract commit, fan-out, merge, review, ADRs, PR.

- **Base.** Fork `X` from `origin/development`; never run `stub_target.py`. Phase 6 opens the draft PR with
  `--base development` and Phase 9 syncs `origin/development` (`<target>` = `development`).
- **Runner.** Record `SUITE-RUNNER: ci — <workflow file>, <trigger>; base development (kanban delivery); push and
  draft PR: yes` whatever the CI coverage is, and `SUITE-COMMAND:` as the focused command the dossier's criteria
  own. Take no stack lease, run no `--stack` or `--vm` suite, record no `STACK-ENV:` run: a criterion that needs the
  live stack is a reclassification (below), not a skip.
- **Reviews.** `dossier_edit.py set <dossier> 'visual_review=skipped — kanban delivery'`. When the contract adds no
  member used outside its package, `dossier_edit.py set <dossier> 'contract_review=skipped — kanban delivery,
  package-internal contract'`; otherwise run the contract review as usual.
- **Reclassification.** A `CONTRACT-CHANGE:` on a member used outside its package, or a diff past 400 changed
  lines of product code (tests, docs and `.discovery/` excluded), stops the build: log
  `RECLASSIFY: stub — <reason>` in `## Build log` and ask the owner once with `ask_owner.py ask --kind product
  --topic reclassify --blocks "build"` (options: A move to stub, B continue as kanban anyway), then end the turn with
  `RUNNER: WAITING <id>`. Never continue on your own. On A, `classify_delivery.py --override stub --write` and
  re-plan the missing sections; the build restarts as a stub build.
