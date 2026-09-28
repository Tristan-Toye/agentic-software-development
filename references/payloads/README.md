# Spawn payloads — one skeleton per agent

Three build agents write code. Three support agents —
`stub-materialiser`, `blast-radius-scout`, `document-drafter` — carry
mechanical work off the orchestrator's context. One independent checker,
`contract-reviewer`, reviews the materialised contract before the fan-out
and derives its own checklist from the stubs alone. Copy the skeleton, fill
every line, delete nothing.

**Load only the file the phase you are in spawns.** Phase 2 needs
`payloads/stub-materialiser.md`; Phase 3 needs
`payloads/contract-reviewer.md`; Phase 4 needs `payloads/unit-test-author.md`,
`payloads/integration-test-author.md` and `payloads/implementer.md`; Phase 7
needs `payloads/reviewer.md`, `payloads/blast-radius-scout.md` and
`payloads/implementer.md` (`MODE: fix`); Phase 8 and 9 need
`payloads/document-drafter.md`. Reading every file every run costs the
orchestrator's context for skeletons it will never fill.

| File | Agent | Phase |
|---|---|---|
| `unit-test-author.md` | `unit-test-author` | 4 |
| `integration-test-author.md` | `integration-test-author` | 4 |
| `implementer.md` | `implementer` | 4 (`MODE: build`), 7 (`MODE: fix`) |
| `contract-reviewer.md` | `contract-reviewer` | 3 |
| `reviewer.md` | `reviewer` | 7 |
| `stub-materialiser.md` | `stub-materialiser` | 2 |
| `blast-radius-scout.md` | `blast-radius-scout` | 7 |
| `document-drafter.md` | `document-drafter` | 8, 9 |

A malformed payload is the likeliest silent failure in this pipeline: an agent
halts on a **missing** field, but a **misnamed** field is simply ignored. A
misnamed `OWNED_PATHS` is the worst case — the agent writes wherever it likes
and corrupts a concurrent agent's work. Run
`python3 ${PLUGIN_ROOT}/scripts/check_payload.py <file> --kind <agent>
--host <claude|opencode>` over every payload before it ships: it refuses a
misnamed or missing field, an absolute path that does not exist, an
unexpanded `${PLUGIN_ROOT}` or `@@TOKEN@@` template placeholder, a
credential literal, any path handed to an agent whose tool list under that
host grants no `Read` — in a field or in a sentence — a path the agent may
only partly read, and (with `--live-dossier`) an integration excerpt that
differs from the live dossier. A field left out is otherwise silent — the
recorded miss shipped a unit author with no style sample and paid a `GAP:`
for it.

There is no `BAND`, no `TIER`, and no `RETURN_CEILING`. Every agent returns a
short structured report because its own prompt says so.

**Paste what an agent cannot open; name only what its tools can reach — and
the tools are the host's.** Under opencode `unit-test-author` reads and
writes inside a pattern-scoped map — `.agent-staging/` and the test families
— so a payload path inside that map is live: stage `CONTRACT` as files under
`.agent-staging/` (hash-stampable as bytes on disk) or paste it, and *name*
style samples as paths instead of pasting them. **Under Claude Code the same
agent has `Write` alone**, and every path in its payload other than
`TEST_PATHS` is a dead letter — in a field or in a sentence — so the Claude
Code form of its skeleton pastes everything: `CONTRACT` inline,
`STYLE_SAMPLE` in place of `STYLE_PATHS`, `SUPPORT` in place of
`SUPPORT_PATHS`, and no sentence that tells it to read anything.
`check_payload.py --host claude` refuses a path the agent cannot reach;
`--host opencode` leaves admission to the maps and
`check_permission_maps.py`. And the dossier rule is unchanged everywhere: an
agent that reads the contract out of the dossier could read the rest of the
dossier too — the contract never comes from the dossier.

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

## Support agents

Three flash agents carry mechanical work off the orchestrator's context. Each
returns a **guidance doc** — pointers, verbatim quotes, neutral flags — never a
ruling; the orchestrator investigates and decides. Spawn one only past its size
gate (the gate table in `primary-agents/work-on.md` names every gate and its
threshold); below the gate the orchestrator does the work itself and logs the
decision either way.

Their skeletons are `stub-materialiser.md`, `blast-radius-scout.md` and
`document-drafter.md`.

## Field rules that matter

Rules that govern a field belonging to exactly one agent kind live in that
agent's own file (`payloads/<agent>.md`, its own "Field rules that matter"
section). The rules below govern a field shared by two or more agent kinds,
or no specific field at all.

- **`${PLUGIN_ROOT}` is expanded before a payload ships.** It names this
  plugin's checkout (the directory holding `primary-agents/` and
  `references/`). The skeletons below write it for brevity; what reaches an
  agent is the expanded absolute path, because an agent cannot resolve a
  variable it was never given.
- **A template placeholder never ships.** A payload assembled from a template
  — `@@CONTRACT@@`, `@@STYLE_SAMPLE@@` substituted into a src file to make the
  final under `.agent-staging/payloads/` — is pasted from the final, re-read
  in the turn that composes the spawn message, never from the src directory.
  `check_payload.py` refuses any `@@TOKEN@@` left in a final; a token that
  reaches an agent is a transmission defect whether or not the agent recovers
  (`/work-on` Phase 4).
- **Every path in a payload is absolute.** In local mode `.discovery/` is
  untracked and exists only in the main checkout; in committed mode the live
  dossier is the copy inside the run's worktree, and the main checkout's copy
  is stale by design. Either way a relative dossier path read from the wrong
  directory resolves to a file that does not exist or to the wrong version,
  and the agent halts or guesses. The same rule keeps `TEST_PATHS`,
  `CONTEXT_DOCS`, and `STANDARDS` unambiguous whatever the agent's working
  directory is.
- **`OWNED_PATHS` is assigned by the orchestrator from `## Work packages`**,
  never negotiated by the agent, and disjoint across every concurrent spawn.
  `scripts/validate_pipeline.py` checks the disjointness before the fan-out.
- **`CONTRACT` is identical, byte for byte, in every payload of a fan-out.** All
  three agent kinds build against the same text; a divergence between two copies
  produces test failures with no diagnosable cause.
- **`STYLE_PATHS` / `STYLE_SAMPLE` and `HARNESS` are the difference between
  a usable test and a rewritten one.** Name real test files from this
  repository — verify each path exists and sits inside the author's read
  map before the spawn — or, for an author with no `Read`, paste one
  verbatim as `STYLE_SAMPLE`; never a description of them.
- **A path never ships to an agent that cannot read it — in a field or in
  prose.** The target agent's frontmatter tool list under the host is what
  loads; a payload path it cannot reach is a dead letter whether it sits in
  `CONTRACT:`, in `STYLE_PATHS:`, or in a sentence in `FIXTURES` saying
  "read the sibling test first". Three times in one recorded run an
  orchestrator handed an agent a path instead of content, knowing the rule;
  `check_payload.py --host claude` refuses every such token, quoting it, and
  warns on a read-verb sentence. The pasted fields (`CONTRACT: |`,
  `STYLE_SAMPLE`, `SUPPORT`) carry what the path was meant to carry.
- **A path an agent may only partly read is not bounded by an instruction.**
  An agent with `Read` reaches every byte of a file it is told to read
  three sections of; the recorded read returned a paged view carrying the
  forbidden section anyway. Extract the permitted part to its own file
  under `.agent-staging/` and name that — the integration author's excerpt
  is this rule applied to the dossier. `check_payload.py` refuses a path
  beside a partial-read phrase.
- **`PROMISE_CHECKLIST` lines carry their assertion form.** A line tagged
  `[whole-value]` is asserted as one equality over the whole value; a line
  tagged `[member]` as membership of the whole element. Every line whose
  text states the whole result carries the tag (`/work-on` Phase 3, diff
  7; `references/formats/dossier.md` § "The observability checklist"), and the
  lint warns on an "exactly" line without one.
- **`TEST_PATHS` for a `Write`-only author is delete-first, every time.**
  Under Claude Code the unit author cannot read the file it wrote, and
  `Write` refuses to overwrite a file it has not read; a re-spawn onto an
  existing path returns `GAP:` with its finished draft undelivered. Delete
  with `safe_revert.py --delete` before the lint and the spawn; the lint
  refuses an existing `TEST_PATHS` file for such an author.
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
- **You format a blind author's files in the commit that lands them.** The
  test author cannot run the formatter; run it yourself over exactly
  the named files — single-file invocation, never package-wide — inside that
  commit, never a later one.
- **Exact-path ownership is checked at commit time, mechanically.** The
  author's edit map admits the whole test family; its commitment is narrower.
  After `.agent-staging/` is deleted, diff the worktree's changes against the
  named `TEST_PATHS` — every path outside them is reverted, not negotiated.
  The check sees what happened, which is stronger than what was permitted.
- **`TEST_PATHS` is checked against the host's boundary at spawn time,
  mechanically.** `scripts/check_permission_maps.py --agent ... --host
  <opencode|claude> --root <X> --test-paths ... [--read-paths ...]` reads
  the frontmatter tool list first, then the maps: it refuses the spawn when
  a named path is edit-denied, a read path is read-denied, or a read path
  is named at all for a tool list with no `Read`. A read-allowed family that
  is edit-refused bricks the spawn silently; a map validated for a host
  where it does not load reads as evidence and is not; the pre-spawn check
  turns both into a one-line refusal.
- **A resume payload is a payload.** The complete field set for the agent's
  kind binds it, and `check_payload.py` lints it as a file like any other; a
  corrective-only resume fails on a dozen missing fields, and an agent
  resumed with half a payload works from half a world. When the resume
  changes how the deliverable lands, it says so in a `DELIVERY_CHANGE`
  block — staged sections: one `Write` plus several `Edit`s, each far under
  `MAX_SINGLE_EDIT` — beside, never instead of, the full field set
  (`work-on.md` Phase 4, the escalation ladder).
- **A support agent's report is a guidance doc, not a verdict.** Pointers,
  verbatim quotes, neutral flags (`WEAK?`, `no-test-found`, `NOT-FOUND`) —
  pasted into `## Build log` and investigated by you before anything acts on
  it. A support agent that starts ruling has stopped being auditable.
- **`FAILURE_LOG` is pasted verbatim, never summarised.** The clerk's value is
  exact quotes at `path:line`; a pre-digested log would pre-digest the
  ruling too.
- **`CONTRACT_HASH` stamps which contract an author saw.** Hash the contract
  files at spawn time, paste the hash bare — never explained — into every
  test-author payload, and re-hash when the author returns. A mismatch means
  the author's world went stale mid-flight: the re-spawn against the current
  contract is unconditional, whatever the author's report says. The hash
  exists for the orchestrator's comparison, not for the agent's use.
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
