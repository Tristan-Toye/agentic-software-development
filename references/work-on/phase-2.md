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
   the implementer to stage and report without committing. Log `HOOKS: none`
   or `HOOKS: <hook> — <policy>` before the fan-out;
   `validate_pipeline.py --pre-fanout` refuses without it.

   **Decide the suite runner now, beside the hook policy.** From Phase 6 on,
   every verdict about the suite — the first green, each arbitration round,
   each review fix, the Phase 9 sync — needs one full run, and running it
   here and then again in the target's CI is the same run paid twice. So
   look at the target's CI once: a workflow that runs the suite on a push or
   a pull request (`.github/workflows/*.yml` with `on: push` or
   `pull_request`, `bitbucket-pipelines.yml`, `.gitlab-ci.yml`), the
   command it runs, and what it needs that this branch will carry. Where one
   exists, **CI is the runner**: from Phase 6 the branch is pushed and a
   draft PR opened (with the yes taken at the gates), and every full-suite
   verdict is read from CI's run on the pushed head — never run locally as
   well. Where none exists, or CI does not run the suite, or the user says
   its minutes are constrained, **local is the runner** and you run the
   suite yourself. Log one line before the fan-out; `validate_pipeline.py
   --pre-fanout` refuses without it:

   ```
   SUITE-RUNNER: ci — <workflow file>, <trigger>; push and draft PR: yes
   SUITE-RUNNER: local — <reason>
   ```

   The decision moves verdict runs only. Runs that are not verdicts stay
   local under either runner, because CI cannot make them: the stub red-run
   of the new tests alone (Phase 5), a mutant run on the throwaway branch
   (Phase 6), the one failing test at the merge-base for a row-5 proof, the
   command verification an implementer's payload needs (Phase 4), and the
   agents' own runs in their worktrees.

You write this yourself. Do not delegate it: the contract is what you will
referee with in Phase 6, and a contract you did not write is one you cannot
referee with.

The contract stays yours; the transcription need not. Past the gate (more
than four members) spawn `stub-materialiser` (payload in
`references/payloads/stub-materialiser.md`) to place the contract verbatim as compiling stubs
in `X` while you refine the packages; below it, type the stubs yourself.
Either way, compare every signature **and every documentation-comment block**
mechanically against `## Contract` before you commit: each contract doc
sentence lands verbatim or is reconciled with a `## Build log` note, because
you referee with this text and a byte of drift is a defect, not a style
issue. Log the gate decision.
