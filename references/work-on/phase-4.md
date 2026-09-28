<!-- /work-on, Phase 4. Read at the start of this phase; the rules that hold
     in every phase stay in primary-agents/work-on.md. -->

## Phase 4 — Fan out: concurrent and blind

Three kinds of work run at the same time. Their blindness is structural, not a
promise you extract from a prompt:

| Agent | Cannot see | Enforced by |
|---|---|---|
| `unit-test-author` | the implementation, the dossier, every pipeline document, anything outside `.agent-staging/` and the test families | under opencode its permission map: `read` and `edit` deny every path outside the staging area and the test families; under Claude Code its tool list, `Write` alone, with the payload pasted whole — either way the tool call is refused, not merely discouraged |
| `integration-test-author` | any implementation body | the bodies are stubs on `X`; it has no Bash and cannot reach another branch |
| `implementer` | the tests | its worktree forks from `X` at this commit, and no test exists there |

**Worktrees: one per concurrent implementer, and none for the test authors.**
The test authors write into `X` directly — neither can read an implementation
body, so a separate branch buys them nothing. Only the implementer, the one
agent with both `Read` and `Bash`, needs branch isolation.

```
git worktree add ../<repo>-<ID>-P1 -b <branch>-p1 <X-head>
git worktree add ../<repo>-<ID>-P2 -b <branch>-p2 <X-head>
```

The suffix uses a dash, never a slash: git refuses `<branch>/p1` while
`<branch>` exists, because one ref cannot be both a name and a directory.

**Spawn every independent agent in one message so they run concurrently.**
Sequence only what `Depends on` in `## Work packages` forces. A dependent
package forks from **its dependency's branch head** once that implementer
returns — never from `X-head`, where the dependency's code does not exist.
That branch carries no test either, so the fork point changes nothing about
blindness.

**Name the wave in a table first, then count the spawns you actually
issued.** One row per agent — kind, package, worktree — in `## Build log`
before the fan-out message, and a count of the `Agent` calls against that
table before you read a single report. A provider limit can cut a multi-spawn
message *between* spawns: a wave that planned eight issues seven, nothing
announces the eighth, and the gap surfaces phases later as a criterion no
test covers. A difference is a spawn defect logged in `## Build log`, never
something the next message explains away.

**One call admits the wave.** The lint, the boundary gates and the admission
below are three checks per payload, and each run as its own step pays your
whole context again — the recorded runs spent 223 steps on them. Write the
wave manifest beside the payloads (one line per spawn: `kind=`, `payload=`,
and for a test author `test-paths=`, `read-paths=`, `expected-lines=`,
`flows=`, `live-dossier=`, `allow-path=` as the paragraphs below require)
and run

```bash
python3 "${PLUGIN_ROOT}/scripts/prepare_wave.py" <manifest> --host <opencode|claude> \
  --root <X> --provider <provider> --model <model> --holder <ID>-wave<k> \
  --log <live dossier>
```

It runs `check_payload.py` over every payload and `check_permission_maps.py`
for every test author, unchanged, prints only the defects and warnings,
acquires the slots only when every spawn passes, and appends the
`PAYLOAD-LINT:` and `ADMISSION:` lines. Fix what it prints and run it again.
The paragraphs below stay the rules; the script is how you run them.

**Admission before the spawn message — mandatory.** The wave table carries
a slot count beside the agent count (one slot per spawn). Acquire them from
the machine-wide ledger, which every session, checkout and plugin instance on
this machine shares:

```bash
python3 "${PLUGIN_ROOT}/scripts/spawn_admission.py" acquire \
  --provider <provider> --model <model> --n <N> --holder <ID>-wave<k>
```

Exit 0 grants the slots and prints the cap in force and the ceiling the
ledger has learned; log `ADMISSION: granted <N> slots — <provider>/<model>,
cap <C>, ceiling <K>` and spawn. Exit 1 is a refusal or a deferral, and the
ladder below governs what it degrades into: a **deferral** names a recorded
reset deadline — wait it out (step 1), and never spawn into a closed window
from any checkout; a **refusal** names the free slots — split the wave to fit
them (step 2), or serialise the cheapest kind (step 3). Never send the whole
wave past a refusal. When the agents return, `release --holder <ID>-wave<k>`
— a wave released with no error recorded raises the learned ceiling, which is
the cap the next run starts from. When a provider limit kills an agent,
`record-error "<the error, verbatim>"` before anything else, so every other
session on the machine defers too. The two recorded failures: an 8-spawn
wave with 4 corpses at their first API call while three sibling sessions
drew on the same account, and a quota error that named its reset in prose
nothing acted on. `ADMISSION: skipped — <reason>` is allowed for a wave of
one; `validate_pipeline.py --pre-fanout` refuses without either line, and
refuses a `granted` line while the ledger holds an unexpired deadline for
that provider and model. A local cap cannot see other machines on the
account — the provider's error stays the account-global signal, and
`ASD_ADMISSION_DIR` can point every machine at one synced directory. Agents
never learn any of this: an agent told about admission control waits on the
wrong thing.

**Under rate pressure, narrow the wave — never re-send the same batch.**
Concurrency is the default because it is free when the quota allows it. Once
a provider limit has killed agents in a wave, re-sending that wave buys the
same death: the limit is a property of the batch, not of any one payload.
Degrade instead, in this order, and log which step you are on:

1. **Wait out the stated reset** when the limit names one — a wave sent
   into a closed window is a wave that dies whole. `spawn_admission.py
   record-error "<the error, verbatim>"` puts the deadline in the ledger, so
   every session on the machine waits with you.
2. **Halve the wave.** Two batches that land beat one batch that dies.
3. **Serialise the kind that is cheapest to lose.** A killed reviewer wastes
   only its generation; a killed implementer or test author may have left a
   partial file, so every retry under pressure pays the tree-diff below
   before anything is re-spawned.

- `unit-test-author` × **one per contract surface** — the contract staged as
  files under `.agent-staging/` inside `X` (hash the staged bytes) or pasted
  verbatim — **pasted, always, under Claude Code**, where the author has
  `Write` alone and every path but `TEST_PATHS` is a dead letter
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

**Re-verify every setup claim in the same turn as the fan-out message, then
lint every payload before it ships.** "The worktree exists", "the branch was
created", "the file was written", "the command was verified": each is proven
by the cheapest command that shows it — `git worktree list`, `test -f`, the
command itself — run now, never remembered from a plan step an earlier
failure may have invalidated; a claim you cannot check that cheaply becomes
an instruction ("create it with this command if absent"). Two recorded
implementers were handed worktrees that did not exist and built their own
topology to make the payload true. Then write each payload to a file under
`.agent-staging/payloads/` in `X` — the bytes you will paste — and run
`python3 ${PLUGIN_ROOT}/scripts/check_payload.py <file> --kind <agent>
--host <claude|opencode>` (plus `--live-dossier <live copy>` for the
integration author) over each: it refuses a misnamed or missing field (a
misnamed field is ignored, never rejected, so the agent writes wherever it
likes), an absolute path that does not exist, an unexpanded `${PLUGIN_ROOT}`
or `@@TOKEN@@` template placeholder, a credential literal, a `TEST_COMMAND`
verb that contradicts the named script's shebang (`python3 <bash script>`
lints clean and dies at run time — copy the invocation you verified, never
write the verb from memory), a `DOSSIER` that is not the run's live copy,
**any path handed to an agent whose tool list under that host grants no
`Read` — in a field or in a sentence — an existing `TEST_PATHS` file for a
`Write`-only author, a path the agent may only partly read, and an excerpt
that differs from the live dossier**; it warns on a whole-value checklist
line with no assertion-form tag. Record it before the fan-out message:

```
PAYLOAD-LINT: <N> payloads, <N> defects fixed — <agent ids>
```

`validate_pipeline.py --pre-fanout` refuses without it, as for
`CONTRACT-REVIEW:`, `HOOKS:` and `ADMISSION:`. A payload that was never a
file was never linted.

**When payloads are assembled from templates, the template is the source and
the assembled file is the final: lint the finals, and compose every spawn
prompt from a final you re-read in this same turn.** Keep them apart on disk
— templates under `.agent-staging/payloads/src/`, substituted finals under
`.agent-staging/payloads/` — run `check_payload.py` over the finals only, and
paste each prompt from the final's bytes after `head` or `sha256sum` has
shown them to you beside the spawn message, never from the src directory and
never from the memory of having assembled it. A prompt that reaches an agent
carrying `@@CONTRACT@@` is a transmission defect whether or not the agent
recovers. The recorded fan-out shipped three of five prompts from src while
the substituted finals sat on disk; every affected agent disclosed and
recovered against the staged contract bytes, so the run absorbed the cost —
but the recovery was the agent's disclosure, not your control, in a design
whose blindness invariants depend on exactly which bytes a payload carries.
The defect repeats on every re-spawn composed from src, and a weaker agent
satisfies the placeholder by guessing instead of disclosing. The lint refuses
the token, so a src paste fails before it ships; the same-turn re-read keeps
the file you linted and the file you paste the same file.

**Validate `TEST_PATHS` against the host's boundary before every
`unit-test-author` spawn.** Run `python3 scripts/check_permission_maps.py
--agent <the agent file the host loads> --host <opencode|claude> --root <X>
--test-paths <comma-separated> [--read-paths <STYLE_PATHS>]` — the plugin
checkout supplies the script and the agent file
(`sub-agents/unit-test-author.md` under opencode,
`claude/agents/unit-test-author.md` under Claude Code); `X` supplies the
paths. It reads the frontmatter tool list first: under Claude Code any
`--read-paths` is refused, because the list grants no `Read`, and an
existing `TEST_PATHS` target warns delete-first. Exit 1 names the offending
path and, under opencode, the families the map admits; fix the split, stage
or paste the content, never the map or the tool list. Two failures this
prevents, both silent: a path the read map admits but the edit map refuses
makes the author read the existing file, refuse to write it, and return
empty, once per retry; and a read path validated against the opencode map
for a Claude Code spawn — the recorded check printed `OK … 2 read path(s)
admitted` for an author that could open neither — passes as evidence and is
a dead letter at run time, found only because a canary spawn disclosed it.

**Gate the `integration-test-author` the same way before it spawns.** It has
no path map, so the check is size and split, not admission: `python3
scripts/check_permission_maps.py --agent sub-agents/integration-test-author.md
--root <X> --test-paths <paths> --expected-lines <N> --flows <M>` warns past
the single-write cap and when the flows outnumber the paths. **One
`TEST_PATHS` entry per flow is the default, not a hint**: a `GAP:` or a
vacuous test then re-spawns one flow instead of the set, and no single
`Write` runs long — the recorded 646-line single-file deliverable, 1.85× the
cap, returned empty twice and took three shrink rounds and a canary to land.

**`CONTRACT_HASH` — stamp every test author's world at spawn, check it at
return** (field rules: `references/payloads/README.md`). Hash the contract bytes
each author will see before the fan-out; re-hash them when it returns. A
mismatch means its world went stale mid-flight, and the re-spawn is
**unconditional**: you never judge whether the drift touched that author's
surface, because a stale test file that happens to pass is the most expensive
coincidence there is, and judging it needs exactly the judgement the hash
removes. Its `GAP:` analysis still feeds the contract fix. Unconditional means
the re-spawn happens by the host's mechanic (below) — in place under
opencode, where the author edits its own earlier output onto the current
contract; delete-first under Claude Code, where every re-spawn is — never a
bare delete with nothing said. The recorded opencode hash re-spawn deleted
both test files and paid two regenerations for a one-sentence pin.

**Name the shared idiom when a concept spans packages.** Two implementers that
each need the same helper, type, or error-mapping shape each invent one, and
invented idioms diverge — the collapse costs more than the build saved. When
`## Work packages` splits a concept, paste the one named idiom into **every**
payload that touches it, byte for byte (`SHARED_IDIOM`). The duplication is
deliberate while the agents run; plan its collapse in the same run — record the
copy count and the collapse target in `## Build log` now, and land the collapse
with the merge or the review fix.

**With three or more unit authors, canary the smallest surface first.** Spawn
it alone, wait for its return, and fix every `GAP:` it surfaces in the
contract before the rest fan out — the same contract defect priced once
instead of per author. Two authors cost more to sequence than the gap costs
to fix; six do not.

**Commit for the test authors yourself — at Phase 5, not now.** Neither has
`Bash`, so neither can commit. Each implementer commits its own work in its own
worktree.

> **The tests stay uncommitted until Phase 5.** That rule is what keeps the
> implementer blind: the tests are uncommitted files in `X`'s working tree,
> and a separate worktree has its own, so there is nothing to find. Committing
> them early puts them **into git**, where the shared object store makes them
> reachable from any worktree with `git show` — and the implementer's `Bash`
> cannot be denied, so blindness here is informational, not permissional: the
> information is not where the agent can reach it, which is stronger than a
> rule against looking. If you must commit a test early, every implementer
> already spawned is no longer blind: say so in `## Build log`, and treat its
> tests as implementation-aware.

**Cap what one blind write carries.** `MAX_SINGLE_EDIT` — around 300–400
lines — bounds a single `Write` or `Edit` from the author; a deliverable
above it is split at spawn time into more files or staged sections, each
under the cap. The failure is silent and repeatable: an oversized generation
dies at the same boundary every time — the stream ends mid-work with no tool
call — while smaller siblings land fine. Recovery: resume the author's
session from its transcript **once**, which carries the finished reasoning —
with staged-sections delivery and the complete field set (the ladder below)
— and lands the file; a repeated death at the same boundary indicts the
payload, not the transport — split it.

**A report is never the evidence — the tree is. Diff the tree after EVERY
spawn outcome, not only after the successes.** Before you read a single
word of any agent's report, look at what is on disk: `test -f` each of its
declared paths, and read `git status --porcelain` in its worktree. This
applies to every agent kind and every outcome — returned, empty, errored,
killed by a provider limit.

The reason is the expensive one: **a spawn that reports failure has very
often already done the work** — an agent dies at its reporting step with the
file complete, a fix agent after folding two of four cases, a stub placer
after landing every file. Re-spawning on the failure message pays the whole
generation again for work you already own, and a fresh instance can collide
with a file its predecessor half-wrote. Recover by **looking**, and the three
states are easy to tell apart:

| What the tree shows | What it means | What you do |
|---|---|---|
| Files complete and coherent | the agent finished and died reporting | keep the work, verify it against the payload as if it had reported, log the recovery |
| Files partial or half-written | the agent died mid-write | `safe_revert.py` the paths (below): it copies each fragment outside the repository, then reverts — a fresh instance must not inherit a fragment, and an uncommitted fragment has no other copy |
| Nothing written at any path | the work never happened | retry the spawn once, immediately and unchanged |

An empty report **with no file at any of its paths** is a retryable
infrastructure failure, not a judgement on the contract or the payload — the
runtime's stream timeout races a long generation and the loss is silent by
construction. A second empty return goes to the user as an infrastructure
problem. Never classify an empty-artifact return as a `GAP:`, a vacuous
test, or a weak oracle, and never let one pass as success.

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
is delete-first, unconditionally** — corrective rounds, coverage gaps,
review CRs and hash mismatches alike. The author has `Write` alone, and
Claude Code's `Write` refuses to overwrite a file the agent has not read,
its own earlier output included, formatted or not: an in-place instruction
returns "The Write tool requires Read to be called first" or a `GAP:` with
the finished draft undelivered, and the round is lost — the recorded repo
paid it twice across two builds, once after a formatter pass and once on a
file nobody had touched. Delete the file with `safe_revert.py --delete` (it
copies the file out first), then resume the same session or re-spawn with
the complete payload and the changed tests named, and let one whole-file
`Write` land it; the resume reuses the finished reasoning where the draft
already exists. Two limits on either host. First, the payload names the
complete intent, never a diff against what the earlier instance wrote: a
fresh instance knows the file only as it reads it on disk, or not at all.
Second, under opencode delete first only for a clean slate — the wholesale
cases in the vacuous-test table (Phase 5) — and the delete is
`safe_revert.py --delete`, so a stalled re-spawn leaves a copy instead of
nothing. Never widen the map or the tool list to work around a problem:
implementation paths, the dossier and the pipeline documents stay outside
it, because the blindness is the permission map or the tool list, not an
instruction not to look.

The strict variant — `read` flatly denied, so nothing can be opened and
every file is a single fresh `Write` — **is the Claude Code default**, not
an option: every payload field is pasted, because a path in its payload is
a dead letter, and every re-spawn is the delete-first mechanic above.

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
