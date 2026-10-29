<!-- /work-on, Phase 2. Read at the start of this phase; the rules that hold
     in every phase stay in primary-agents/work-on.md. -->

## Phase 2 — The base branch, and the contract as real code

This is the step everything else depends on.

1. **Make the base worktree.** `X` is the shared base that every fan-out branch
   forks from and merges back into:

   ```
   git worktree add ../<repo>-<ID> -b <type>/<JIRA-KEY>-<slug> <baseline>
   ```

   `fix/` for a defect, `feat/` for new behaviour, `chore/` for mechanical work.
   The branch keys off the **Jira key**, never the dossier ID; fall back to the
   dossier ID only when no ticket exists. Record `worktree` and `branch` in the
   front matter.

   **Committed mode forks from the target tip, not from `<baseline>`.** The
   dossier reached the target through its own PR, after `baseline_commit`
   was stamped, so a worktree at `<baseline>` has no dossier in it. Run
   `git fetch origin <target>` and fork from `origin/<target>`; log
   `FORK: <target>@<sha>, baseline <baseline>, <N> commits behind`, then run
   the validator on `X`'s copy (`--root <X> --dossier <ID>`) — an anchor the
   target moved is a defect to fix before you write a docstring. Then write
   `status: building`, `worktree` and `branch` into `X`'s copy; it is the
   live dossier from here.

2. **Write the contract into real files.** You write the docstrings that every
   downstream agent builds against, so **read the contract-craft rules first** —
   every run, before you type a documentation comment:

   - `${PLUGIN_ROOT}/references/formats/dossier.md` § "The observability
     checklist" — return meaning, named errors, order, the empty case, the
     invalid case, concurrency semantics, and the unmeasurable words that are
     never promises.
   - **This repo's own rules file** (`docs/adr/RULES.md`, a rules section in
     `CLAUDE.md`, a conventions doc — there may be none yet) — the rules
     earlier runs paid for, each one bought with a row-3 arbitration or a
     `GAP:` return. **This is the read side of Phase 8b.** A repo rule
     outranks `skills/standards/engineering-standards.md`.

    Then materialise `## Contract` in `X` as actual source: the types, the
    signatures, and the documentation comments, in the language's own
    documentation form. Bodies are stubs that fail loudly —
    `raise NotImplementedError`, `unimplemented!()`,
    `throw new NotImplementedException()` — and nothing else; `todo!()` reads
    as unfinished work, so the failing-loud marker is the default unless this
    repo's rules file names another.

    **Carry the definitions.** Every term `### Definitions` (under `## Contract`)
    defines goes into the documentation of each type or member that uses it: a
    blind author and the contract reviewer read the stubs alone. A term meant
    for the dossier only is logged `DOSSIER-ONLY-TERMS: <term>, <term>`.
    `--pre-fanout` refuses a term found in no owned stub file.

3. **Refine the contract AND the packages while you write.** Materialising a
   contract exposes what a text section hides: a missing type, an unstated
   error, a return value that cannot express the promise, or a file that turns
   out to belong to a different package than `/plan` guessed. Fix both sections
   now — in the files and in the dossier — and note the change in
   `## Build log`.

   **Re-check the `Depends on` column specifically.** `/plan` wrote it against
   a prose contract, where package B genuinely needed package A's types to
   exist. Once you materialise the contract they DO exist, as stubs that
   compile — so a dependency survives only when one package cannot be
   **written** without another's implementation, which is rare. A serial chain
   inherited from the prose costs one full agent round trip per link and buys
   nothing.

   **A shape change compiles every library at the contract commit.** When
   `## Contract` changes a type other code builds or matches on (a field's
   type, a trait method's signature, a constructor's arity), the contract
   commit carries the type-forced edits at every non-test call site, so the
   libraries, bins and examples compile (the tests need not) on every gate
   below. A package that owned only such edits leaves the table, and call-site
   test packages depend on nothing: they fork in the same wave. This is a
   revision; it goes to the user (below).

   **This is the last cheap moment to change either.** `## Contract` and
   `## Work packages` **freeze** when Phase 4 spawns anything: after that a
   contract change costs a re-spawn of every agent that read it, and a package
   change means a merge already went wrong. A revision the plan reviewer would
   have judged differently — a new type, a changed signature, a repartitioned
   package table — goes to the user before you spawn: a contract you rewrote
   here has been reviewed by nobody.

4. **Commit it on `X`.** Prefix with the Jira key. Every fan-out branch forks
   from this commit, so the contract is the one thing all of them share. In
   committed mode the dossier is in this commit too — `status: building`, the
   revised sections, and their build-log lines.

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
   any package's `OWNED_PATHS`. Log `HOOKS: none`
   or `HOOKS: <hook> — <policy>` before the fan-out;
   `validate_pipeline.py --pre-fanout` refuses without it.

   **Decide the suite runner by coverage, beside the hook policy.** Inspect
   the target's repository instructions, full-suite entry point and CI
   commands. Check the feature axes, service and host requirements, and
   explicit skips; the presence of a workflow alone establishes nothing.
   Choose **CI** only when its run covers the required integrated suite.
   Push and open the draft PR at Phase 6 with the authorization taken at the
   gates. Choose **local** when repository policy requires it, CI covers only
   part of the suite, CI is absent, or the user constrains its minutes.
    Keep partial CI gates running, but take the integrated verdict from the
    full local command. For example, a repository whose CI opts out of
    services can require its local full command with `--stack` instead.
    Choose **stub** when the repository defers its integrated verdict to a
    weekly release stub: the pull request then merges into the accepting
    stub — the newest `stub/<date>` on the remote with no open pull request
    into `development`, cut from `origin/development` when none exists;
    push that new branch only, creation is idempotent. The worktree forks
    from the accepting stub, and `/stub` proves the whole batch later with
    one full run. This build owes green CI on its pull request, the new
    tests and the mutation check. A service-backed new test may hold the
    stack lease for one suite window when its criterion demands runtime
    proof; log the window in the build log.

   Record the exact full-suite command separately from implementers' focused
   `TEST_COMMAND`s, plus its required coverage, before the fan-out.
   `validate_pipeline.py --pre-fanout` requires the runner line:

   ```
   SUITE-RUNNER: ci — <workflow file>, <trigger>, required coverage; push and draft PR: yes
   SUITE-RUNNER: local — <reason CI cannot supply the required verdict>
   SUITE-RUNNER: stub — <policy source>; target <stub branch>; integrated verdict deferred to the /stub run; push and draft PR: yes
   SUITE-COMMAND: <exact full-suite command, working directory and VM/env options>
   SUITE-COVERAGE: <required feature, service and host axes; expected opt-outs and where they are covered>
   ```

   Run one full suite for a verdict on an integrated candidate. Reuse that
   verdict until relevant code, tests, dependencies, deployment inputs or a
   target merge change; do not repeat the suite just to enter another phase.
   Focused implementation checks, the stub red-run, mutant runs, merge-base
   proofs and targeted flake investigations still run locally. They do not
   replace the full-suite verdict.

   **Record the gate list from the same read of CI.** One gate per feature
   axis a CI job compiles (`cargo check --workspace --all-targets --locked`,
   and once more per `--features` set a job uses) plus every doc and lint gate
   CI runs (`cargo doc` under its `RUSTDOCFLAGS`, clippy, fmt) — never the
   repo's local aggregator alone, which covers fewer. Generate them with
   `run_gates.py --from-workflow <workflow files>` (it lists every skipped step),
   or write them by hand in `run_gates.py`'s format to `.discovery/analysis/gates-<ID>.txt`, run
   `python3 ${PLUGIN_ROOT}/scripts/run_gates.py <file> --root <X>` before the
   contract commit, and log one line; `--pre-fanout` refuses without it:

   ```
   GATES: <gates file> — <N> gates; axes: <default, fault-inject, ...>
   GATES: none — <why CI builds nothing to gate>
   ```

   The list is fixed for the branch. The first push (Phase 6), every
   arbitration fix and every review fix round (Phase 7) run this same list,
   docs-only commits included; none gets a narrower one.

You write this yourself. Do not delegate it: the contract is what you will
referee with in Phase 6, and a contract you did not write is one you cannot
referee with.

The contract stays yours; the transcription need not. Past the gate (more
than four members) pick the route by the contract's shape:

- **Signature-only code** — every member a `;`-terminated signature with
  documentation comments (C# declarations): run
  `python3 ${PLUGIN_ROOT}/scripts/materialise_stubs.py --root X
  --contract-paths <the contract files> --owned <stub targets> --check
  [--definitions-from <live dossier>] [--build-check '<compile>']`. It copies
  every contract line byte-for-byte, gives each member that needs one a
  throwing body, places `### Definitions` terms (refusing one in no contract
  byte), and runs the mechanical diff itself — no agent, no pasted payload,
  and a 53 KB contract is as cheap as a 5 KB one.
- **Anything else**: spawn `stub-materialiser` (payload in
  `references/payloads/stub-materialiser.md`) to place the contract verbatim as
  compiling stubs in `X` while you refine the packages.

Below the gate, type the stubs yourself. Either way, compare every signature
**and every documentation-comment block** mechanically against `## Contract`
before you commit: each contract doc sentence lands verbatim or is reconciled
with a `## Build log` note, because you referee with this text and a byte of
drift is a defect, not a style issue. Log the gate decision and the route:

```
STUB-GATE: transform — <N> members, signature-only, materialise_stubs.py
STUB-GATE: stub-materialiser — <N> members, <why the transform cannot run>
STUB-GATE: self — <N> members, under the gate
```
