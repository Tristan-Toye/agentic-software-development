# Spawn payloads — one skeleton per agent

Three build agents write code. Three support agents —
`stub-materialiser`, `blast-radius-scout`, `document-drafter` — carry
mechanical work off the orchestrator's context. One independent checker,
`contract-reviewer`, reviews the materialised contract before the fan-out
and derives its own checklist from the stubs alone. Copy the skeleton, fill
every line, delete nothing.

**Load only the skeletons the phase you are in spawns.** Phase 2 needs
`stub-materialiser`; Phase 3 needs `contract-reviewer`; Phase 4 needs the
three build agents; Phase 7 needs `reviewer` and `implementer MODE: fix`;
Phase 8 and 9 need `document-drafter`. Reading the whole file every run
costs the orchestrator's context for skeletons it will never fill.

A malformed payload is the likeliest silent failure in this pipeline: an agent
halts on a **missing** field, but a **misnamed** field is simply ignored. A
misnamed `OWNED_PATHS` is the worst case — the agent writes wherever it likes
and corrupts a concurrent agent's work. Run
`python3 ${PLUGIN_ROOT}/scripts/check_payload.py <file> --kind <agent>` over
every payload before it ships: it refuses a misnamed or missing field, an
absolute path that does not exist, an unexpanded `${PLUGIN_ROOT}`, and a
credential literal. A field left out is otherwise silent — the recorded miss
shipped a unit author with no style sample and paid a `GAP:` for it.

There is no `BAND`, no `TIER`, and no `RETURN_CEILING`. Every agent returns a
short structured report because its own prompt says so.

**Paste what an agent cannot open; name what its permission map already
admits.** `unit-test-author` reads and writes inside a pattern-scoped map —
`.agent-staging/` and the test families — so a payload path inside that map is
live: stage `CONTRACT` as files under `.agent-staging/` (hash-stampable as
bytes on disk) or paste it, and *name* style samples and shared idioms as
paths instead of pasting them. A path outside the map is still a dead letter.
And the dossier rule is unchanged everywhere: an agent that reads the contract
out of the dossier could read the rest of the dossier too — the contract
never comes from the dossier.

**Never describe the blindness machinery to the agent it constrains.** An agent's
payload and prompt carry the rules that bind it, and nothing about how the
pipeline keeps it blind. Do not tell an implementer that tests exist, where they
are, or that they are uncommitted; do not tell it "do not look for them." Three
reasons, and the third is the one that matters:

1. It does not need the information to do its job.
2. It is noise in a payload whose quality is the run's quality.
3. **A description of the mechanism is a map to the thing the agent must not
   find.** "The tests are not on your branch" tells a capable agent that they are
   somewhere, and that a branch is where to look.

State the rule (`never write a test`, `never change a signature`) and stop. The
mechanism lives in `primary-agents/work-on.md`, which only the orchestrator reads.
Blindness that has to be explained to stay intact is not blindness.

---

## unit-test-author

Its world is its permission map: `read` and `edit` are denied everywhere
except `.agent-staging/` and the test families, and `bash`/`glob`/`grep`/web
are denied outright. Name a path inside that map and the author opens it
itself — stage the contract as files, name the style samples, never paste
what a pointed-at path can carry. Name a path outside the map and the tool
call is refused.

```
CONTRACT: /abs/path/repo-W-014/.agent-staging/flush-queue.contract.md
          # staged verbatim by the orchestrator inside the base worktree —
          # or pasted inline; either way this is the only contract text the
          # author ever sees, and the same bytes CONTRACT_HASH stamps
PROMISE_CHECKLIST: |
  flush — return meaning: number of items written
  flush — order: oldest first
  flush — empty case: returns 0
  flush — invalid case: raises ValueError when batch_size < 1
  flush — concurrency: concurrent calls coalesce; each item reaches the
          store exactly one time
TEST_PATHS: /abs/path/repo-W-014/tests/unit/test_flush_queue.py
TEST_FRAMEWORK: pytest; plain `assert`; run with `pytest tests/unit -q`
MAX_SINGLE_EDIT: 350 lines — the cap for one Write or Edit; a larger
                deliverable is named as a split at spawn, never improvised
CITATION: |
   // promise: flush/return-meaning
   One comment per test, the line above the test, in exactly this shape —
   `promise: <checklist id>/<category>`. One citation vocabulary per build;
   authors inventing their own shape diverge per file.
CONVENTIONS: |
   Derive Debug on every constructed type. The spelling checker accepts
   "coalesce", "backfill". Checklist ids are <member>/<category>, lowercase,
   hyphen-free.
STYLE_PATHS: /abs/path/repo-W-014/tests/unit/test_retry.py
             # existing tests the author reads and matches — named, not
             # pasted; they sit inside the author's read map
SUPPORT_PATHS: /abs/path/repo-W-014/.agent-staging/contract-support/repo_grant.rs
             # the signature surface of every NON-contract type a checklist
             # line's assertion constructs or calls — a repo grant, an id
             # type, a port helper — staged by the orchestrator at fan-out
             # (work-on.md Phase 3, diff 5). Omit when the checklist names
             # contract members only; check_payload.py warns otherwise.
NAMING: Subject_StateUnderTest_ExpectedBehavior — Subject is the public member
        under test, e.g. Flush_EmptyQueue_ReturnsZero
VOCABULARY: "drain", "coalesce", "batch" — the project's terms for these ideas
FIXTURES: |
  FlushQueue(store=FakeStore(), clock=FakeClock())
  FakeStore exposes .written -> list[Item] and .write_count -> int
  Build an Item with make_item(id: str) from tests/support/factories.py
SHARED_IDIOM: |
  <only when a test must construct or invoke a concept two or more agents
   write — the same bytes as every other payload that carries it, test
   authors included. Omit when no concept is shared.>
CONTRACT_HASH: 3f2611f0a91c4d8e
```

`CONTRACT_HASH` is the orchestrator's stamp of the contract the author saw —
hash the staged files (or the pasted text) at spawn time and paste the hash
bare, never explained. When the author returns, re-hash the same bytes: a
different hash means the contract changed while the author was writing, its
world is stale, and the re-spawn is unconditional (its `GAP:` analysis, if
any, is still input to the contract fix).

`TEST_PATHS` is validated before the spawn, never after an empty return.
`scripts/check_permission_maps.py --agent sub-agents/unit-test-author.md
--root <X> --test-paths ... --read-paths ...` refuses a spawn whose paths
fall outside the author's maps. The refusal names the path and lists the
admitted families; the fix is the split or the staging, never the map.

A suite in a family the read map denies — `deploy/scripts/`, the recorded
case — is still writable: the edit map carries the family, the read map
does not. Stage the file's current content under `.agent-staging/` yourself
and have the author rewrite the whole file with `Write`. It never opens the
original; the staged copy is its only view of what exists.

Absolute `TEST_PATHS` inside the base worktree. The orchestrator commits its
output — it has no `Bash`. `.agent-staging/` lives in the base worktree too:
delete it when the author returns, before the commit-time scope diff, so the
staged contract can never leak into a commit.

## integration-test-author

```
WORKTREE_DIR: /abs/path/repo-W-014
DOSSIER: /abs/path/repo-W-014/.agent-staging/W-014.excerpt.md
          # a file YOU generate: ## Problem, ## Approach and ## Acceptance
          # criteria copied verbatim, nothing else — never the dossier itself
CONTRACT: |
  <the same contract text as every other author's, pasted verbatim from the
   files on the base worktree — never read out of the dossier>
TEST_PATHS: /abs/path/repo-W-014/tests/integration/test_flush_flow.py
           # ONE PATH PER FLOW — the default, not a hint. A GAP: or a vacuous
           # test then re-spawns one flow, not the whole set, and no single
           # Write runs long: the recorded 646-line single-file deliverable
           # returned empty twice and took three shrink rounds to land. Gate
           # it before the spawn — check_permission_maps.py --agent
           # sub-agents/integration-test-author.md --test-paths … --flows N
           # --expected-lines N warns when flows outnumber paths or the size
           # passes the single-write cap.
TEST_FRAMEWORK: pytest; run with `pytest tests/integration -q`
HARNESS: |
  The `app_client` fixture in tests/integration/conftest.py stands up the real
  app against a throwaway Postgres from testcontainers. Seed with
  `seed_publication(client, items=N)`.
STYLE_SAMPLE: |
  <one existing integration test, verbatim>
BOUNDARIES: IClock (time), IPaymentClient (external API) — substitute these two
            only. Everything else runs for real.
SHARED_IDIOM: |
  <same rule as the unit author: present, byte-identical, whenever a test
   touches a concept two or more agents write. Omit otherwise.>
CONTRACT_HASH: 3f2611f0a91c4d8e
```

Same `CONTRACT_HASH` rule as the unit author: stamp at spawn, re-hash at
return, re-spawn unconditionally on a mismatch.

`DOSSIER` is an excerpt you generate under `.agent-staging/` — `## Problem`,
`## Approach` and `## Acceptance criteria`, verbatim and nothing else — never
the live dossier. An instruction not to read `## Build log` in a file the
agent can open is not blindness: a `Read` with no limit returns the whole
file, and the recorded dossier past 900 lines handed its build log to every
reader and voided two review rounds. The contract never comes from the
dossier either: `CONTRACT` is pasted verbatim from the materialised files —
the same text every other author builds against, and the same text
`CONTRACT_HASH` stamps — so the dossier section can never drift from what the
tests were written against. The excerpt goes with the rest of
`.agent-staging/` before the commit. The orchestrator commits its output; it
has no `Bash`.

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

## The independent checker

One agent closes a loop the orchestrator cannot close alone: the
contract-reviewer derives its own promise checklist from the materialised
stubs, so a thin contract is caught before the fan-out instead of at
arbitration. The other half of that pair — turning `PROMISE_CHECKLIST` into
mutants so a weak oracle is caught after the build — is the orchestrator's
own Phase 6 pass, not a spawn.

### contract-reviewer

Spawned between Phase 3 and Phase 4, on the materialised stubs — never on
the dossier or the orchestrator's checklist.

```
WORKTREE_DIR: /abs/path/repo-W-014
CONTRACT_PATHS: src/flush.py
CRITERIA: |
  <the acceptance criteria, verbatim>
CHECKLIST_RULES: |
  <the observability checklist from references/formats.md, pasted verbatim —
   the six categories, the visibility question, the unmeasurable-words rule>
```

It returns its own full checklist plus `AMBIGUITY:` and `DEFECT:` lines. The
orchestrator diffs the two checklists: every disagreement is a contract
defect caught pre-fan-out. An `AMBIGUITY:` line carries both readings
verbatim — take the two readings to the user as one question, never pick a
side silently.

## reviewer

`LENS: plan` — before any code exists:

```
LENS: plan
DOSSIER: /abs/path/repo/.discovery/dossiers/W-014-flush-coalescing.md
          # from round 2 on, a copy without `## Build log` at a path outside
          # .discovery/ — a Read with no limit returns the whole file
WORKTREE_DIR: /abs/path/repo            # ${RUN_ROOT}: the main checkout in
          # local mode, the plan worktree (/abs/path/repo-plan-KEY) in
          # committed mode — both fields point into the same tree
CRITERIA: |
  <the acceptance criteria, verbatim>
STANDARDS: ${PLUGIN_ROOT}/skills/standards/engineering-standards.md
CONTEXT_DOCS: /abs/path/repo/docs/adr/0003-session-store.md
ROUND: 1
```

`LENS: style | architecture | performance` — after the suite is green. Spawn all
three in one message so they run concurrently:

```
LENS: performance
WORKTREE_DIR: /abs/path/repo-W-014
SCOPE: |
  Blast radius — a location list, never the diff:
    src/flush.py :: FlushQueue.flush, FlushQueue._drain (changed)
    src/reload.py :: on_reload (changed)
    src/store.py :: Store.write_batch (direct caller, unchanged)
  Change requests must stay inside it.
CONTRACT: |
  <verbatim — the authority on what the code must do>
RUN_EVIDENCE: |
  <the full test run output; it is green>
CRITERIA: |
  <the acceptance criteria, for context>
STANDARDS: ${PLUGIN_ROOT}/skills/standards/engineering-standards.md
RULES: |
  # ocr delegate rule, narrowed to this lens. `none` when nothing resolved.
  #### Performance
  - Recomputing inside a loop a value that is invariant across iterations
  - Building a full list where a generator would avoid holding everything
CONTEXT_DOCS: /abs/path/repo-W-014/docs/adr/0003-session-store.md
ARBITRATIONS: |
  - user ruled 2026-08-10: keep the retry inside flush(); do not extract it
    (overrides architecture CR-2 from round 1)
ROUND: 2
```

On a re-review, add `PRIOR_CRS` with that lens's **own** change requests from the
previous round — never another lens's. The reviewer answers `CR-n: resolved` or
`CR-n: not resolved — <what is still wrong>`, and opens no new subject.

```
PRIOR_CRS: |
  CR-1: replace the linear scan in _drain with a set membership check
  CR-2: hoist the serialiser construction out of the per-item loop
```

**Mark a structural request as such.** `LENS: architecture` and
`LENS: performance` may review the new structure their own accepted request
created, inside that request's footprint, one level deep — because applying a
structural change request produces code no lens has ever seen. `LENS: style`
never gets that permission: a rename cannot create new structure. Say which
requests were structural and what the fix added, so the reviewer knows what it
may look at:

```
PRIOR_CRS: |
  CR-1: [structural — the fix added ItemIndex, a new dict-backed lookup type in
         src/flush.py] replace the linear scan in _drain with a set membership
         check
  CR-2: [local] hoist the serialiser construction out of the per-item loop
```

## Support agents

Three flash agents carry mechanical work off the orchestrator's context. Each
returns a **guidance doc** — pointers, verbatim quotes, neutral flags — never a
ruling; the orchestrator investigates and decides. Spawn one only past its size
gate (the gate table in `primary-agents/work-on.md` names every gate and its
threshold); below the gate the orchestrator does the work itself and logs the
decision either way.

### stub-materialiser

```
WORKTREE_DIR: /abs/path/repo-W-014
CONTRACT: |
  <verbatim — byte-identical to every other payload this fan-out>
OWNED_PATHS: src/flush.py
STUB_STYLE: |
  unimplemented!()      # the repo's own placeholder, verified against the repo
BUILD_CHECK: cargo check
```

### blast-radius-scout

```
WORKTREE_DIR: /abs/path/repo-W-014
BASELINE: 3f2611f
HEAD: e09ab77
HINT: |
  FlushQueue.flush and its drain loop — the orchestrator's prior belief,
  checked first, overridden by what the diff actually shows
```

### document-drafter

`MODE: adr`:

```
MODE: adr
DECISIONS: |
  - decision: <one sentence, the orchestrator's words>
    evidence: |
      <verbatim quote + path:line, selected by the orchestrator>
FORMAT: |
  <the ADR shape from references/formats.md, pasted verbatim — sections,
   front matter fields, validator rules>
TARGET_PATHS: /abs/path/repo-W-014/docs/adr/0031-flush-drain-lock.md
SCRUB: W-014, .discovery, repo-W-014
```

`MODE: pr`:

```
MODE: pr
DOSSIER-EXCERPTS: |
  <## Problem, ## Approach, ## Acceptance criteria — verbatim>
FORMAT: |
  <the PR description shape this repo uses>
TARGET_PATHS: /abs/path/repo/.discovery/pr-draft-W-014.md
          # committed mode: under X — /abs/path/repo-W-014/.discovery/pr-draft-W-014.md
SCRUB: W-014, .discovery/dossiers, repo-W-014
          # committed mode: repo-W-014 only — the dossier is inside the PR
```

The drafter writes `TARGET_PATHS` itself — its `Write` map admits
`docs/adr/`, `.discovery/`, and `.agent-staging/` only, so the files land
without an orchestrator hand-placement. Its scrub check runs over the
written bytes. You still re-grep the files for the `SCRUB` tokens before
anything leaves the machine: two checks, because a leaked dossier id is a
leaked local path.

---

## Field rules that matter

- **`${PLUGIN_ROOT}` is expanded before a payload ships.** It names this
  plugin's checkout (the directory holding `primary-agents/` and
  `references/`). The skeletons below write it for brevity; what reaches an
  agent is the expanded absolute path, because an agent cannot resolve a
  variable it was never given.
- **Every path in a payload is absolute.** In local mode `.discovery/` is
  untracked and exists only in the main checkout; in committed mode the live
  dossier is the copy inside the run's worktree, and the main checkout's copy
  is stale by design. Either way a relative dossier path read from the wrong
  directory resolves to a file that does not exist or to the wrong version,
  and the agent halts or guesses. The same rule keeps `TEST_PATHS`,
  `CONTEXT_DOCS`, and `STANDARDS` unambiguous whatever the agent's working
  directory is.
- **`SCOPE` is a location list, never a diff.** Passing a diff breaks the
  reviewer's blindness, and blindness is the whole reason its verdict is worth
  anything.
- **`RULES` is rule text, never `ocr` output verbatim.** `ocr delegate rule`
  resolves one combined checklist per path — correctness, security,
  performance, maintainability, tests in a single block. Pasting that block
  into all three lenses hands every lens every other lens's question, and the
  duplicate CRs that follow are exactly what the `LENS` field exists to
  prevent. **The orchestrator narrows it per lens before it is a payload
  field**, and a rule that belongs to no lens is dropped, not distributed.
  It is a checklist, not a licence: a rule still becomes a CR only with that
  lens's evidence, and it loses to `STANDARDS`, `CONTEXT_DOCS` and
  `ARBITRATIONS` whenever they disagree. `RULES: none` is a valid field and
  the right one when nothing resolved or `ocr` was unavailable — the field
  stays present so a lens can tell "no rules" from "forgotten".
- **`OWNED_PATHS` is assigned by the orchestrator from `## Work packages`**,
  never negotiated by the agent, and disjoint across every concurrent spawn.
  `scripts/validate_pipeline.py` checks the disjointness before the fan-out.
- **`CONTRACT` is identical, byte for byte, in every payload of a fan-out.** All
  three agent kinds build against the same text; a divergence between two copies
  produces test failures with no diagnosable cause.
- **`RUN_EVIDENCE` is green when a code lens sees it.** A reviewer never runs
  anything and never needs to check the suite's claim.
- **`ARBITRATIONS` accumulates across rounds** and goes into every later spawn,
  so no reviewer re-litigates a question the user already settled.
- **`STYLE_PATHS` and `HARNESS` are the difference between a usable test and a
  rewritten one.** Name real test files from this repository — verify each
  path exists and sits inside the author's read map before the spawn — never
  a description of them.
- **A convention, fixture or precedent you hand a blind author is proven
  under the target's whole shape first.** `NAMING`, `STYLE_PATHS`, `FIXTURES`
  and `HARNESS` are executable instructions, not prose. Write the idiom into a
  scratch file that carries the **target** file's harness flavour (its test
  attribute and runtime), its dependency surface (what it reaches — a
  database, a store) and its concurrency (as many concurrent consumers as
  the target has), run it there, and only then put it in a payload: an idiom
  proven under a sibling's `#[tokio::test]` panicked all ten tests under the
  target's plain `#[test]`. Before hand-writing a helper, read the crate's
  own test harness for one that already exists — the recorded hand-written
  accessor cost a full blind re-spawn of a 427-line file. Name a precedent
  by what it **exercises**, never by which file it sits in. An author with
  no compiler cannot discover that your convention fights the language, and
  it will fight it once per test.
- **`CITATION` names its exact shape, and one vocabulary serves the whole
  build.** A test's citation comment is the evidence Phase 5 diffs against
  `PROMISE_CHECKLIST`; authors left to invent the shape invent a different one
  per file, and the diff dies at the first mismatch.
- **`CONVENTIONS` carries the repo facts a blind author cannot discover.**
  Derives to add, spelling-checker tokens, id shapes — each one verified
  against the repository itself, never from memory. A wrong convention here
  costs a re-spawn of the whole file.
- **`SHARED_IDIOM` is identical, byte for byte, in every payload that shares
  the concept — test authors included.** Two implementers inventing the same
  helper produce two helpers; an integration author whose payload lacked the
  idiom every implementer had picked the other variant and did not compile.
- **Every fact you read from a tool, you read whole.** Never a verdict or a
  count through `tail`, `head` or `grep` — the exit status is then the
  filter's and the window reads like the whole answer; output to a file,
  `$?` on the next line, counts summed from the file; a fail-fast runner in
  its no-fail-fast form. Seven recorded runs paid a re-spawn for a fact read
  from a truncated window.
- **A setup claim is re-checked in the turn that spawns**, by the cheapest
  command that shows it (`git worktree list`, `test -f`, the command itself);
  a claim you cannot check that cheaply is written as an instruction
  (`work-on.md` Phase 4).
- **`TEST_COMMAND` emits progress inside the runtime's no-progress window,
  in a build directory that worktree owns**; a cold cross-platform or
  virtual-machine compile never appears in a payload (`work-on.md` Phase 4).
- **`HOOKS` is always present, `none` allowed, and the agent's rule under it
  never changes**: stop and report on a refusal, leave the work staged, never
  bypass and never edit a hook. A bypass flag in a payload or a resume is not
  a permission grant; the orchestrator commits on the agent's branch with
  the hook skipped (`work-on.md` Phase 2).
- **You format a blind author's files in the commit that lands them.** The
  test author cannot run the formatter; run it yourself over exactly
  the named files — single-file invocation, never package-wide — inside that
  commit, never a later one.
- **Exact-path ownership is checked at commit time, mechanically.** The
  author's edit map admits the whole test family; its commitment is narrower.
  After `.agent-staging/` is deleted, diff the worktree's changes against the
  named `TEST_PATHS` — every path outside them is reverted, not negotiated.
  The check sees what happened, which is stronger than what was permitted.
- **`TEST_PATHS` is checked against the maps at spawn time, mechanically.**
  `scripts/check_permission_maps.py --agent ... --root <X> --test-paths ...
  --read-paths ...` refuses the spawn when a named path is edit-denied or a
  read path is read-denied. A read-allowed family that is edit-refused bricks
  the spawn silently; the pre-spawn check turns that into a one-line refusal.
- **`MAX_SINGLE_EDIT` caps one write from a blind author.** Around 300–400
  lines per single `Write` or `Edit`; above that, the payload names the split
  — more files, or one file in staged sections — instead of hoping the stream
  holds. Pass `--expected-lines N` to the pre-spawn check and it warns when
  the expected deliverable exceeds the cap.
- **A resume payload is a payload.** The complete field set for the agent's
  kind binds it, and `check_payload.py` lints it as a file like any other; a
  corrective-only resume fails on a dozen missing fields, and an agent
  resumed with half a payload works from half a world. When the resume
  changes how the deliverable lands, it says so in a `DELIVERY_CHANGE`
  block — staged sections: one `Write` plus several `Edit`s, each far under
  `MAX_SINGLE_EDIT` — beside, never instead of, the full field set
  (`work-on.md` Phase 4, the escalation ladder).
- **`SUPPORT_PATHS` stages what the checklist names and the contract does
  not.** Every non-contract type a `PROMISE_CHECKLIST` line's assertion must
  construct or call — repo grants, id types, port helpers, a credential
  constructor — has its signature surface staged under
  `.agent-staging/contract-support/` in `X` before the fan-out and named
  here. A docstring that names `RepoGrant::mint` gives a blind author the
  name and nothing else; the recorded run paid three `GAP:` round trips
  before the surface was staged. `check_payload.py` warns when a checklist
  type is in neither `CONTRACT` nor `SUPPORT_PATHS`.
- **`TEST_COMMAND` carries the known-red shape when the baseline is red by
  design.** Failed and passed counts, the failing-suite count, and the shared
  failure signature, copied from the orchestrator's own verification run —
  with the rule that any deviation is the implementer's defect. "The baseline
  held" becomes a mechanical comparison; three recorded implementers held a
  198-failure baseline exactly, and one proved a two-test discrepancy
  pre-existing.
- **A support agent's report is a guidance doc, not a verdict.** Pointers,
  verbatim quotes, neutral flags (`WEAK?`, `no-test-found`, `NOT-FOUND`) —
  pasted into `## Build log` and investigated by you before anything acts on
  it. A support agent that starts ruling has stopped being auditable.
- **`STUB_STYLE` and `BUILD_CHECK` are verified against the repo before the
  spawn.** The placeholder must be the repo's own, and the compile command
  must run in `WORKTREE_DIR`; a stub spawn with an invented style produces
  stubs the fan-out cannot build on.
- **`FAILURE_LOG` is pasted verbatim, never summarised.** The clerk's value is
  exact quotes at `path:line`; a pre-digested log would pre-digest the
  ruling too.
- **`SCRUB` lists every token that must not leave the machine.** The drafter
  greps its own output and you grep again before the description reaches the
  PR — two checks, because a leaked dossier id is a leaked local path.
- **`CONTRACT_HASH` stamps which contract an author saw.** Hash the contract
  files at spawn time, paste the hash bare — never explained — into every
  test-author payload, and re-hash when the author returns. A mismatch means
  the author's world went stale mid-flight: the re-spawn against the current
  contract is unconditional, whatever the author's report says. The hash
  exists for the orchestrator's comparison, not for the agent's use.
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
- **A payload that names a call carries its signature, not its name.** In
  `FIXTURES`, `HARNESS`, `STYLE_SAMPLE`, `SHARED_IDIOM` and `CONTRACT`: give
  the signature verbatim — **parameter types and return type** — never a
  parenthesised list of parameter *names*. For a helper the payload itself
  defines, paste the definition, not a description. An agent with no
  compiler cannot discover that `bytes` means `&[u8]` rather than `Vec<u8>`,
  and it will guess plausibly and wrongly, once per test.
- **A contract VIEW carries everything the test must NAME, not only what it
  calls.** Where a build changes existing code, generate a view per file the
  author needs: module docs, public types, public signatures, doc comments —
  with **every body and every private item removed** (a private helper's name
  and doc hand over the design as surely as its body would). The view must
  still carry every constant, every enum's exact variants, and every import
  path the test has to write, because a name the author cannot see is a name
  it invents.
- **A contract that changes a shared type is checked across the whole
  workspace before it ships.** A single-crate check proves the contract
  compiles where it is defined and says nothing about the callers that will
  break; run the workspace-wide, all-targets form of the repo's check
  command before the contract commit, because every downstream implementer
  forks from it.
- **A payload fact is verified only when the artifact that SHIPS is the one
  that ran.** Verifying a command in one shell and pasting a retyped,
  reformatted or path-adjusted variant into the payload verifies nothing —
  the difference is where the failure lives. Copy the invocation that
  actually ran, byte for byte.
- **`NOTICED:` is harvested into `## Build log`.** Every support report ends
  with one, `none` allowed; the Phase 9 deferred-issues capture draws on your
  own reads plus this harvest.
