<!-- /work-on, Phase 4, triggered parts. phase-4.md names each trigger; read the
     one section with ref_section, never the whole file. -->

# /work-on Phase 4 — incidents

Failure handling for the Phase 4 fan-out: a rate limit, an empty return, a resume, a return that is not a
plain success, an agent that left work uncommitted, a refused tool call. `phase-4.md` names the trigger.

### Under rate pressure, narrow the wave — never re-send the same batch

**Under rate pressure, narrow the wave — never re-send the same batch.**
Concurrency is the default because it is free when the quota allows it. Once
a provider limit has killed agents in a wave, re-sending that wave buys the
same death: the limit is a property of the batch, not of any one payload.
Degrade instead, in this order, and log which step you are on:

1. **Wait out the stated reset** when the limit names one — a wave sent
   into a closed window is a wave that dies whole. `spawn_admission.py
   record-error "<the error, verbatim>"` puts the deadline in the ledger, so
   every session on the machine waits with you.
   When the user says the limit has reset before the deadline, clear it with
   `spawn_admission.py clear --reason "<what the user said>"` — the owner's
   verb, which stamps the entry rather than deleting it and refuses one
   recorded under 120 s ago unless `--force`; never edit the ledger by hand.
2. **Halve the wave.** Two batches that land beat one batch that dies.
3. **Serialise the kind that is cheapest to lose.** A killed reviewer wastes
   only its generation; a killed implementer or test author may have left a
   partial file, so every retry under pressure pays the tree-diff below
   before anything is re-spawned.

- `unit-test-author` × **one per contract surface** — the contract staged as
  files under `.agent-staging/` inside `X` (hash the staged bytes) or pasted
  verbatim — **pasted, always, under Claude Code**, where the author's `Read`
  is confined to `.agent-staging/` and the test families and a path outside
  them is a dead letter
  (`references/payloads/unit-test-author.md` carries both forms of the skeleton) — its slice
  of `PROMISE_CHECKLIST` (Phase 3, assertion-form tags included), its owned
  test paths inside `X` (every path it should produce, per the Phase 3
  split — it cannot add one), the framework, the style sample
  (`STYLE_PATHS` under opencode — existing tests inside its read map, which
  it opens itself; `STYLE_SAMPLE`, pasted, under Claude Code), the naming
  convention, the citation shape and the repo conventions (`CITATION`,
  `CONVENTIONS` — `references/payloads/unit-test-author.md`, with the four shell lines for a
  shell suite), the fixtures, every non-contract surface it names
  (`SUPPORT_PATHS` / `SUPPORT`), and `CONTRACT_HASH`. Its payload plus its
  tool set are its entire world; a thin payload produces a guessed test,
  which is why it returns `GAP:` instead of guessing.
- `integration-test-author` — a dossier **excerpt** you generate under
  `.agent-staging/` in `X` (`DOSSIER`: `## Problem`, `## Approach` and
  `## Acceptance criteria` verbatim, nothing else — never the live dossier,
  whose `## Build log` a `Read` with no limit returns whole), **regenerated
  from the live dossier in the turn that spawns, after every Phase 2–4
  edit, and linted against it** (`check_payload.py --live-dossier`) — it is
  derived state exactly like the staged contract, and the recorded excerpt
  written early in Phase 3 shipped a criterion an owner ruling had since
  changed — the contract pasted verbatim from the files on `X`
  (`CONTRACT`, never read from the dossier, so the text it builds against
  is the text `CONTRACT_HASH` stamps), its owned test paths inside `X`
  (again, all of them — one per flow), the harness, the substitutable
  boundaries, a verbatim style sample, and `CONTRACT_HASH`.

**Under Claude Code, route by the file's shape before you spawn a unit
author.** The `Write`-only author on the small model lands a pure-function
surface in one write; a **shell suite** or a **compiled-language test file**
it did not, in the recorded build — every such file failed at least once (a
`Duration` call inside a `matches!` pattern, `==` on a `Result` with no
`PartialEq`, negative controls inverted twice, a quoted heredoc that never
expanded), two extra rounds per surface, and it cannot re-read its own file
to correct it. The same surfaces routed to `integration-test-author` —
`Read` and `Write`, the larger model — in `X` while the bodies were stubs
landed on the first write every time, and stayed structurally blind because
a stub has no body to read. So the default under Claude Code: shell suites
and compiled-language test files go to `integration-test-author`, its own
skeleton plus that surface's `PROMISE_CHECKLIST` (`references/payloads/integration-test-author.md`),
spawned while `X` holds stubs only; the `Write`-only author keeps the small
pure-function surfaces. Log the routing per surface in the wave table.
- `implementer` × one per package — its own worktree, the contract verbatim, its
  package, its owned paths, its slice of the criteria, and the command that runs
  the **existing** suite. **Run that command yourself once, in your own shell,
  before the fan-out, and paste the invocation that actually worked**, with
  whatever environment setup it needed, in the payload text; a command that
  fails in your shell fails in theirs, once per agent. **When that run is not
  fully green, the payload carries the exact known-red shape**: the failed
  and passed counts, the failing-suite count, and the shared failure
  signature — `198 failed / 443 passed across 23 suites, every failure a
  panic at tests/common/mod.rs:140, DATABASE_URL absent` — plus the rule that
  any deviation from it is the implementer's defect. "The baseline held" is
  then a comparison, not a judgement: the recorded three concurrent
  implementers each held it exactly, and one proved a two-test discrepancy
  pre-existing by re-running at the contract commit. **It must also emit
  progress well inside the runtime's no-progress watchdog**: warm each
  worktree's build once yourself first, and give every worktree its own build
  directory, named in the command — a cache two worktrees share links a
  sibling's artifacts, and the recorded stub red-run passed 41 of 69 tests
  against stubs through one. A cold cross-platform or virtual-machine compile
  is your check, never an agent's; the recorded payload that asked three
  implementers for one killed all three. Its own new code has no tests yet,
  and green is not its exit condition. **When an owned path is a shell script
  that embeds a program in a heredoc — written to a file and executed — the
  payload also carries `VERIFY_EMBEDDED`**: the embedded-program parse idiom
  from `references/payloads/implementer.md`, one line per embedded program, its `sed`
  adapted to the script's real write line and marker, verified in your own
  shell exactly as `TEST_COMMAND`. `bash -n` parses only the shell text and a
  stub-backed suite never executes the embedded program; the recorded fix
  round verified green on both while the embedded program was syntactically
  dead.

### After the second empty return, change something before you spawn again

**After the second empty return, change something before you spawn again.**
Three recorded runs sent the same agent shape eleven, six and six times.
Escalate in this order and log the step: (1) canary the shape with a trivial
payload — one small read, a one-line write — so one cheap spawn tells
infrastructure from workload; (2) if the canary lands, **resume the same
session with staged-sections delivery**: a `DELIVERY_CHANGE` block telling
it to land the file as one `Write` plus several `Edit`s, each far under
`MAX_SINGLE_EDIT`, appended to the **complete field set** its kind demands
— a corrective-only resume fails `check_payload.py` on a dozen missing
fields, and an agent resumed with half a payload works from half a world;
the recorded run landed twice this way where an unchanged re-spawn died
twice; (3) if that dies too, shrink the deliverable once, by splitting
`TEST_PATHS`; (4) if that dies too, re-route to an agent shape that has
landed in this run for the same file family, state its blindness
constraint, require disclosure, and log the routing; (5) failing that, write
the harness machinery yourself and have authors produce only cases, in
small standalone files. Never a fourth spawn of one shape at one size.

**Prefer a resume over a re-spawn whenever the transport failed and the work
did not.** The agent's transcript carries its finished reasoning, so a
resume lands the remaining work instead of regenerating all of it. A resume
message is a payload like any other, and every payload rule binds it — it is
not a place to be terse about what changed.

### Under Claude Code, resume instead of re-spawn

**Under Claude Code, resume instead of re-spawn.** A correction, a
`DELIVERY_CHANGE`, a `GAP:` fix for one author, a coverage gap, a Phase 6
fix round and a Phase 7 fix round all go back to an agent of the same kind that
already holds the context. Resume that agent: `SendMessage` with `to` set to its
agent ID and a message that carries **only the correction** — the changed
field, the named tests or change requests, and (for a fix round in `X`) the
`WORKTREE_DIR` it now works in. It keeps its history and its warm prompt cache,
so the message is short where a re-spawn repeats the whole payload and pays the
cache cold. A resume message is not a payload, so `check_payload.py` does not
run on it; the rule that a message never describes the agent's own file back
to it stays (state the delta). A resumed `unit-test-author` reads its file again
before it rewrites it: the resume names the changed tests. **A correction round
of two or more agents (Phase 5, Phase 6) is one `phase4-wave` call instead:**
every corrected payload (`FAILURES` + `CORRECTION` folded in, composed and linted
as any payload) goes in one call. A resume keeps the agent's context; a batch saves
one orchestrator turn per agent and keeps the reports out of your context. A
round of one agent may still resume by ID. **Re-spawn with
the full payload, linted and pasted, only when** the agent's ID is unknown (it
was spawned by a workflow, whose compact result carries none, or the line that
held it is gone), the agent has ended and `SendMessage` reports it unreachable,
or the contract changed: — and an agent spawned by a workflow is corrected by a
**full re-spawn through the same workflow** (a test author with `FAILURES` +
`CORRECTION` folded into its payload; an implementer with the fix payload,
spawned by file per `phase-7.md`). An agent id scavenged from the workflow's
transcript directory is not resumable: `No transcript found for agent ID` is
the permanent refusal, not a retry signal — each of five recorded corrective
rounds burned a refused resume before the re-spawn: a `CONTRACT_HASH` mismatch, an accepted
`CONTRACT-CHANGE:` or a contract fix after a `GAP:` makes the agent's world
stale, and a stale world is rebuilt, never patched. **Where the IDs live:**
every `Agent` spawn's result carries an agent ID; write it into the build log in
the spawn line (`SPAWNED: P1 (agent <id>) — implementer, wave <k>`) and keep it
in the `RETURNED` line (`P1 (agent <id>) RETURNED: …`) — the build log is what
the compaction hook restores (it prints the last 40 lines), and the in-flight
list that hook prints also carries every ID since the last checkpoint. After a
`phase_checkpoint`, only the build log still has them, so a checkpoint summary
names the agents a later round will resume. `validate_pipeline.py` reads none of
these lines, so their wording is free; keep it greppable.

### Uncommitted agent work has no second copy

**Uncommitted agent work has no second copy.** Never run `git checkout --`,
`git restore`, `git stash` or `git clean` yourself over a path an in-flight
or recently dead agent may hold uncommitted; the recorded cases destroyed a
whole fix round, and broke the rule again in the run that ratified it. Every
revert, and every delete made so an author can be re-spawned, goes through
the guard:

```bash
python3 "${PLUGIN_ROOT}/scripts/safe_revert.py" --repo <X or worktree> \
  --copy-to <dir outside every repository> [--delete] <path> ...
```

It copies each path outside the repository first, prints where (log it),
then reverts or, with `--delete`, removes — and refuses a `--copy-to` inside
the repository,
because a copy the next revert can take is not a second copy. A commit while
blind agents run would put the file in the shared object store and spend the
blindness invariant for nothing the copy does not give. An agent that died
between staging and committing is committed by you on its branch, never
reset. Recovery is the copy, or `git checkout <commit> -- <path>` against a
commit that holds the file.

### Handle two returns immediately, never silently

**Handle two returns immediately, never silently:**

- **`GAP:`** — the contract, or `PROMISE_CHECKLIST`, did not tell a test author
  enough. That is your defect, not the agent's. **Collect every `GAP:` from
  all in-flight authors first, then fix the contract and the checklist once**
  — in the files and in the dossier — and re-spawn the affected authors
  together. A fix applied per return often invalidates a payload that is
  still writing. Commit the fix on `X`, and if the gap changes a signature,
  re-spawn every side that read it.
- **`CONTRACT-CHANGE:`** — an implementer cannot satisfy a signature. **You
  decide.** Accept it and you must re-spawn the affected test author, because it
  built against the old signature. Refuse it and say what to do instead. Record
  the ruling in `## Build log` either way. Never let an implementer change a
  signature quietly.

**Re-spawning `unit-test-author` onto a path it already wrote — the
mechanic is the host's.** Under opencode the test families are inside the
author's read **and** edit maps, so a fresh instance opens the existing
`TEST_PATHS` file itself and folds the corrected payload into it with
`Edit` — no delete step, no pasted base material. One exception: a family
the read map denies while the edit map admits it — `deploy/scripts/` is the
recorded case — cannot be opened; stage the file's current content under
`.agent-staging/` yourself and have the fresh instance rewrite the whole
file with `Write`. **Under Claude Code every re-spawn onto an existing path
is read-then-rewrite** — corrective rounds, coverage gaps, review CRs and
hash mismatches alike. The author has `Write` and a hook-confined `Read`, and
Claude Code's `Write` refuses to overwrite a file the agent has not read, its
own earlier output included: the author reads the file, then lands the
whole file in one `Write`. (When it had `Write` alone, that refusal cost the
recorded repo two rounds; the confined `Read` is what removed the
delete-first step.) A file outside the test families is the staged-copy
exception above. Resume the same session where you hold its ID — it reuses
the finished reasoning — or re-spawn with the complete payload and the
changed tests named. Two limits on either host. First, the payload names the
complete intent, never a diff against what the earlier instance wrote: a
fresh instance knows the file only as it reads it on disk, or not at all.
Second, under opencode delete first only for a clean slate — the wholesale
cases in the vacuous-test table (Phase 5) — and the delete is
`safe_revert.py --delete`, so a stalled re-spawn leaves a copy instead of
nothing. Never widen the map or the tool list to work around a problem:
implementation paths, the dossier and the pipeline documents stay outside
it, because the blindness is the permission map or the tool list, not an
instruction not to look.

Under Claude Code every payload field is still pasted, whatever the
confined `Read` could reach: the paste is what the per-spawn classifier
judges, and the only path a workflow spawn reads besides its test families
is its own `PAYLOAD_FILE`.

### Sub-agent permission maps are load-time

Maps load when the host session starts. A patch to `sub-agents/*.md`
mid-run changes nothing for this session: a spawn from the same host still
carries the old map, the refused tool call stays refused, and no amount of
re-spawning reads the new file. Treat the in-flight session as frozen, and
work a refusal in this order:

1. **The same refusal after a map patch means the patch is not loaded.**
   Stop re-spawning into it; every spawn repeats the refusal and costs a run.
2. **Resume the affected agent's session.** Its transcript carries the old
   map and, often, the finished reasoning — the `MAX_SINGLE_EDIT` recovery
   above is this move. The corrective payload works inside the old map.
3. **Re-home the work on your side of the boundary.** Stage the content
   under `.agent-staging/` so the old map suffices, and log the transport
   in `## Build log`: what was staged, why, and which map motivated it.
4. **Restart the host session last.** The flow is resumable — the dossier
   and `## Build log` carry the state — but the restart drops every
   in-flight session at once.
