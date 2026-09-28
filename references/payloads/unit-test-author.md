## unit-test-author

Its world is its tool set, and the tool set is the host's. **Under opencode**
`read` and `edit` are denied everywhere except `.agent-staging/` and the
test families, and `bash`/`glob`/`grep`/web are denied outright: name a path
inside that map and the author opens it itself — stage the contract as
files, name the style samples, never paste what a pointed-at path can
carry; name a path outside the map and the tool call is refused. **Under
Claude Code** the author has `Write` and nothing else — no `Read`, no `Edit`
(`claude/agents/unit-test-author.md`): every path field is a dead letter,
`CONTRACT`, `STYLE_PATHS` and `SUPPORT_PATHS` included, and so is a sentence
that tells it to read something. The recorded wave shipped four payloads
with all three as paths; two blind authors built from `PROMISE_CHECKLIST`
alone and guessed the rest — 20 compile errors. So the skeleton has two
forms, and `check_payload.py --host` refuses the wrong one for the host.

**The opencode form** — paths inside the map are live:

```
CONTRACT: /abs/path/repo-W-014/.agent-staging/flush-queue.contract.md
          # staged verbatim by the orchestrator inside the base worktree —
          # or pasted inline; either way this is the only contract text the
          # author ever sees, and the same bytes CONTRACT_HASH stamps
PROMISE_CHECKLIST: |
  flush — return meaning: number of items written → equals 3 [whole-value]
  flush — order: oldest first → store received exactly [A, B, C] [whole-value]
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

**The Claude Code form** — `Write` only, everything pasted, `TEST_PATHS` the
one path in the payload:

```
CONTRACT: |
  <the contract text, pasted verbatim from the files on X — the same bytes
   CONTRACT_HASH stamps. Never a staged path: the author cannot open it.>
PROMISE_CHECKLIST: |
  <as in the opencode form, assertion-form tags included>
TEST_PATHS: /abs/path/repo-W-014/tests/unit/test_flush_queue.py
           # the one path here. A file already at it is deleted with
           # safe_revert.py --delete BEFORE the lint and the spawn: Write is
           # whole-file and refuses a file the author has not read, and it
           # has no Read (work-on.md Phase 4)
TEST_FRAMEWORK: pytest; plain `assert`; run with `pytest tests/unit -q`
MAX_SINGLE_EDIT: 350 lines — one whole Write per file; a larger deliverable
                is split into more TEST_PATHS at spawn, never into staged
                sections the author cannot Edit together
CITATION: |
   <as in the opencode form>
CONVENTIONS: |
   <as in the opencode form — plus the four shell lines below when the
    file is a shell suite>
STYLE_SAMPLE: |
  <one existing test from this repository, verbatim — what STYLE_PATHS
   names under opencode>
SUPPORT: |
  <the signature surface of every NON-contract type a checklist line's
   assertion constructs or calls, pasted verbatim from the staged file —
   what SUPPORT_PATHS names under opencode. Omit when the checklist names
   contract members only; check_payload.py warns otherwise.>
NAMING: Subject_StateUnderTest_ExpectedBehavior
VOCABULARY: "drain", "coalesce", "batch"
FIXTURES: |
  <as in the opencode form. No sentence here, or anywhere in the payload,
   tells the author to read or open anything — it cannot.>
SHARED_IDIOM: |
  <same rule as the opencode form>
CONTRACT_HASH: 3f2611f0a91c4d8e
```

A pasted payload grows — a support block of ~1,000 lines is recorded — and
the temptation is to condense while transcribing. Never: the final under
`.agent-staging/payloads/` is the artifact that ships (`work-on.md` Phase
4), so paste each block into the final from the file it came from (`cat
>>`, never a retyping), lint the final, and spawn from a re-read of it — a
byte the lint saw is a byte the agent sees. Where the pasted world would
exceed what one payload carries well, split the surface across more
authors, or route it to the `Read` + `Write` shape (`work-on.md` Phase 4).

**Shell suites — four `CONVENTIONS` lines a blind author cannot discover**
(the recorded build paid two rounds per surface before adding them):

1. Capture output and exit status in two steps — `out=$(cmd)` on one line,
   `status=$?` on the next — never `|| true` inside the capture, which
   zeroes every status it was meant to record.
2. Pass values to an embedded program through argv, never by shell
   expansion inside a quoted heredoc (which never expands) and never a
   heredoc plus a here-string.
3. One check function shared by a case and its negative control, so the
   control cannot invert what the case asserts.
4. A refusal helper compares the SUBJECT's exit status, never the test
   helper's own 0/1.

`CONTRACT_HASH` is the orchestrator's stamp of the contract the author saw —
hash the staged files (or the pasted text) at spawn time and paste the hash
bare, never explained. When the author returns, re-hash the same bytes: a
different hash means the contract changed while the author was writing, its
world is stale, and the re-spawn is unconditional (its `GAP:` analysis, if
any, is still input to the contract fix).

`TEST_PATHS` is validated before the spawn, never after an empty return.
`scripts/check_permission_maps.py --agent <the file the host loads> --host
<opencode|claude> --root <X> --test-paths ... [--read-paths ...]` reads the
frontmatter tool list first: under opencode it refuses a spawn whose paths
fall outside the author's maps, naming the path and the admitted families;
under Claude Code it refuses any `--read-paths` at all, because the tool
list grants no `Read`, and warns when a named `TEST_PATHS` target already
exists for an author with neither `Read` nor `Edit`. The fix is the split,
the staging or the paste, never the map or the tool list.

Under opencode, a suite in a family the read map denies — `deploy/scripts/`,
the recorded case — is still writable: the edit map carries the family, the
read map does not. Stage the file's current content under `.agent-staging/`
yourself and have the author rewrite the whole file with `Write`. It never
opens the original; the staged copy is its only view of what exists. Under
Claude Code every `TEST_PATHS` file that already exists is that case,
unconditionally: delete it with `safe_revert.py --delete` (which copies it
out first), then spawn for a fresh whole-file `Write` with the changed tests
named — never an in-place edit, which `Write` refuses for a file the author
has not read, and the author cannot read (`work-on.md` Phase 4).

Absolute `TEST_PATHS` inside the base worktree. The orchestrator commits its
output — it has no `Bash`. `.agent-staging/` lives in the base worktree too:
delete it when the author returns, before the commit-time scope diff, so the
staged contract can never leak into a commit.

## Field rules that matter

- **`CITATION` names its exact shape, and one vocabulary serves the whole
  build.** A test's citation comment is the evidence Phase 5 diffs against
  `PROMISE_CHECKLIST`; authors left to invent the shape invent a different one
  per file, and the diff dies at the first mismatch.
- **`CONVENTIONS` carries the repo facts a blind author cannot discover.**
  Derives to add, spelling-checker tokens, id shapes — each one verified
  against the repository itself, never from memory. A wrong convention here
  costs a re-spawn of the whole file.
- **`MAX_SINGLE_EDIT` caps one write from a blind author.** Around 300–400
  lines per single `Write` or `Edit`; above that, the payload names the split
  — more files, or one file in staged sections — instead of hoping the stream
  holds. Pass `--expected-lines N` to the pre-spawn check and it warns when
  the expected deliverable exceeds the cap.
- **`SUPPORT_PATHS` / `SUPPORT` carries what the checklist names and the
  contract does not.** Every non-contract type a `PROMISE_CHECKLIST` line's
  assertion must construct or call — repo grants, id types, port helpers, a
  credential constructor — has its signature surface staged under
  `.agent-staging/contract-support/` in `X` before the fan-out and named
  here (opencode), or pasted verbatim under `SUPPORT` (Claude Code). A
  docstring that names `RepoGrant::mint` gives a blind author the name and
  nothing else; the recorded run paid three `GAP:` round trips before the
  surface was staged. `check_payload.py` warns when a checklist type is in
  neither `CONTRACT` nor `SUPPORT_PATHS` / `SUPPORT`.
