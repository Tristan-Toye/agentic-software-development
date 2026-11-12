<!-- /work-on, Phase 2. Read at the start of this phase; the rules that hold
     in every phase stay in primary-agents/work-on.md. -->

## Phase 2 — The base branch, and the contract as real code

### Rules that hold in this phase

- **You own the contract.** You write it, materialise it, and referee with it;
  no agent changes it. An implementer needing a different signature returns
  `CONTRACT-CHANGE:` and stops. (Phase 2, Phase 6)

- **You write the docstrings, so you read the docstring rules first** — the
  observability checklist and this repo's rules file, every run, before the
  first documentation comment. Phase 8b writes that file and Phase 2 reads
  it; a loop with no reader is a log line pretending to be a lesson.

- **A workspace-wide hook is decided at the contract commit.** Agents stop
  and report on a hook refusal; you commit on their branch with the hook
  skipped and the fixers run by hand; no payload ever names a bypass.
  (Phase 2)

### Delegation — mechanical work only, evidence never verdicts

**Delegation — mechanical work only, evidence never verdicts.** You run on the
large model; the fan-out runs on small ones. Two flash support agents carry
mechanical work off your context — `stub-materialiser` and
`blast-radius-scout` — and each returns a **guidance doc**: a pointer
(`path:line`), a verbatim quote, a neutral flag. Never a ruling, a row label,
or a spawn recommendation; a support agent that starts deciding has stopped
being auditable. `document-drafter` drafts the end-of-run documents from
decisions you already made. `scribe` writes the prose of a dossier or document
edit from the facts and rulings you hand it — never tests, docstrings or code —
and `code-scout` answers lookup chains with `path:line` rows. Neither takes a
linted payload: give `scribe` `TARGETS`, `EDITS`, `FACTS` and `STYLE`, and
`code-scout` `ROOT` and `QUESTIONS`. Three rules govern them all:

1. **Only mechanical work is delegated** — placing stubs, listing the blast
   radius, drafting documents from decisions you already made. Every
   judgement — rulings, re-spawn triggers, contract fixes, merges, commits,
   the PR scrub check — stays with you.
2. **Investigate a flag before you act on it.** A flag is a place to read,
   not an instruction. Read windows to rule; read the whole file when in
   doubt.
3. **Gate on size, and log the decision either way.** Below a gate you do
   the work yourself — a small build stays single-model on purpose.

Residue guarantees, so the run's quality never rests on the support agents'
summaries: the primary surface's test file(s) and implementation you read
**in full**, never windowed; every support report ends with a `NOTICED:` line
(`none` allowed) that you harvest into `## Build log`; when `NOTICED` runs
thin or your sampling stops finding anything, raise the sampling. No agent
ever talks to another — work moves only as artifacts in `X`, after you commit
or merge them.

### The gate table

Every gate below is a **size** gate: past it you may delegate, below it you do
the work yourself, and **either way you write the decision into `## Build
log`**.

| Work | Agent | Gate — delegate only past it | Below the gate |
|---|---|---|---|
| Place the contract as stubs | `stub-materialiser` | `## Contract` carries **more than four members** | type the stubs yourself |
| List the blast radius | `blast-radius-scout` | `ocr delegate preview` returns **more than five reviewable files** | run `preview` and narrow it from `--stat` yourself |
| Draft ADRs / the PR description | `document-drafter` | ADRs are due, or the PR description is being written | write them yourself |
| Review the materialised contract | `contract-reviewer` | **no gate — it runs on every build** (below) | — |

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
   `## Build log`. Land the files' revision in the dossier with
   `dossier_edit.py sync-contract <dossier> --root X`: it rebuilds each
   `### <path>` block of `## Contract` from that file and fails, member by
   member, on any block that is missing or truncated against the file. A block
   keeps the members it names, plus the file's members added or changed since
   `baseline_commit` and those the dossier's work packages, criteria, approach
   or checklist mention in backticks; the rest of the file stays out. Name an
   unchanged member in its block when a blind author needs it. Never
   hand-copy a block.

   **A stub that extends a base class outside the contract inherits that
   base's decisions.** When the subclass overrides a hook of a template
   method, read the template method and trace every edge promise you wrote
   on the subclass (zero items, the empty case, duplicates, an invalid or
   missing value) through it: a case the base raises on, refuses, or returns
   early from never reaches the subclass body, so the promise must say what
   the base does, or go. Note the base's `path:start-end` for the reviewer's
   `BASE_TEMPLATES` (Phase 3).

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
   revision; rule on it as the next paragraph says.

   **This is the last cheap moment to change either.** `## Contract` and
   `## Work packages` **freeze** when Phase 4 spawns anything: after that a
   contract change costs a re-spawn of every agent that read it, and a package
   change means a merge already went wrong. A revision the plan reviewer would
   have judged differently — a new type, a changed signature, a repartitioned
   package table — is ruled before you spawn: a contract you rewrote
   here has been reviewed by nobody. Never ask the owner to approve the
   revisions as a batch. A revision with a recommended reading and a low
   impact (it changes no behaviour the dossier promises and no public
   contract another dossier depends on) is taken: write it into the dossier,
   log `PROVISIONAL-RULING: <what>, <why>, <how to reverse>` in `## Build
   log`, and list it under "Rulings to review" in the PR description. A
   high-impact or product-level revision goes to the owner as one question
   (`--kind design` or `--kind product`), under the ask policy
   (`references/owner-questions.md`, "The ask policy").

4. **Commit it on `X`.** Prefix with the Jira key. Every fan-out branch forks
   from this commit, so the contract is the one thing all of them share. In
   committed mode the dossier is in this commit too — `status: building`, the
   revised sections, and their build-log lines. In local mode commit through
   `gate_commit.py --root X --paths <contract files> -m MESSAGE --forbid-id
   <dossier id>`: the id is local, and a contract commit that carries it in
   its message or its text is refused with the file named, before any
   fan-out branch forks from it (removing it later rewrites every commit on
   top).

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
   `contract_commit=$(git rev-parse HEAD)` the same way.

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
    stub. Take it from `python3 ${PLUGIN_ROOT}/scripts/stub_target.py --root
    <repo>`: the newest `stub/<date>` with no pull request into
    `development`, or a new one it cuts from `origin/development`, but only
    when no stub is being integrated. Exit 3 (`wait <stub>`) means that stub's
    pull request into development is open: a new stub must start with its
    batch, so it is cut only once that one has merged. This is a breakpoint:
    under the runner end the turn with `RUNNER: WAITING stub` (the runner
    waits without tokens and sends `STUB READY: <branch>`); in a terminal run
    `stub_target.py --wait`, or stop and tell the user. Never cut a stub by
    hand. The worktree forks
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
   docs-only commits included; none gets a narrower one. A green run leaves
   a receipt keyed by the gates file and the working tree (build log
   excluded); the next call on the same tree reuses it (`REUSED`) instead of
   running the list twice.

   Put the repository's PATH needs on the run, never inside each line: when
   its toolchain is not on a non-interactive shell's PATH (cargo under
   `~/.cargo/bin`), pass `--path-prepend "$HOME/.cargo/bin"` (repeatable) and,
   for anything more, `--env-script <file>` on every `run_gates.py` call, the
   flags `run_tests.py` has. A gate that exits 127 is reported as "command not
   found — environment, not a gate failure": fix the flags, do not read it as
   red. A list too long for one tool call (the Bash cap is 10 minutes) runs
   with `--detach` (prints a run id, returns at once) and then
   `--wait <id>` (prints the foreground summary; exit 6 means still running,
   call it again).

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
