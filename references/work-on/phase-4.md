<!-- /work-on, Phase 4. Read at the start of this phase; the rules that hold
     in every phase stay in primary-agents/work-on.md. -->

## Phase 4 — Fan out: concurrent and blind

### Rules that hold in this phase

- **Blindness is structural, and informational where a tool set cannot
  reach.** The unit author's `read` and `edit` maps deny everything outside
  `.agent-staging/` and the test families under opencode, and under Claude
  Code it has `Write` and a `Read` the plugin's hook confines to the same
  boundary, its payload pasted or staged as a `PAYLOAD_FILE`; the
  integration author sees stubs
  and has no `Bash`; the implementer's worktree has no tests. Never hand an
  agent something its blindness depends on not having, never rely on an
  instruction where an absence will do, and never widen a permission map to
  work around a problem. (Phase 4)

- **No wave spawns without admission.** Its slots come from the machine-wide
  ledger before the spawn message; a refusal splits or serialises the wave,
  a deferral waits out the recorded reset, and the whole wave is never sent
  into a closed window. Agents never learn the mechanism. (Phase 4)

Detail: `ref_section` with `phase-4-conditional` and the heading "Under Claude Code — two workflows, and resume over re-spawn". (read it before the first spawn under Claude Code.)

Detail: `ref_section` with `phase-4-conditional` and the heading "Spawning under Claude Code". (paste every payload, never `model`; read it before the first spawn.)

### Triggered parts — read the section only when its trigger fires

`ref_section` with `phase-4-incidents` and the heading:

- An admission refusal, a 429 or an empty return that looks like rate pressure: "Under rate pressure, narrow the wave — never re-send the same batch".
- A second empty return from the same agent: "After the second empty return, change something before you spawn again".
- A correction going back to an agent that may still be resumable: "Under Claude Code, resume instead of re-spawn".
- An agent that died or returned with work in its worktree that is not committed: "Uncommitted agent work has no second copy".
- A return that is `CONTRACT-CHANGE:`, `GAP:` or a failure: "Handle two returns immediately, never silently".
- A tool call an agent was refused at load time: "Sub-agent permission maps are load-time".

`ref_section` with `phase-4-conditional` and the heading:

- A package whose blind write is large (a big test file or a long implementation body): "Cap what one blind write carries".
- A concept that spans more than one package: "Name the shared idiom when a concept spans packages".
- Three or more unit authors in the wave: "With three or more unit authors, canary the smallest surface first".
- Spawning under opencode (never under Claude Code): "Under opencode, spawn by the file, never by retyping it".

Detail: `ref_section` with `phase-4-conditional` and the headings "Worktrees: one per concurrent implementer, and none for the test authors" and "Then let the repository prepare each new worktree" (before creating the implementers' worktrees.)

### Spawn every independent agent in one message so they run concurrently

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

### One call admits the wave

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

Detail: `ref_section` with `phase-4-conditional` and the heading "Admission before the spawn message". (`prepare_wave.py` runs it; read it on a refusal or a deferral.)

### Compose each payload with the script, then write only the judgement

**Compose each payload with the script, then write only the judgement.**
Most fields are copies — the contract bytes and their hash, the package row
and its owned paths, the criteria, the dossier excerpt, the worktree and
branch, the Jira key, the standards path — and typing them is the most
expensive output of the run. Run

```bash
python3 "${PLUGIN_ROOT}/scripts/compose_payloads.py" --kind <agent> \
  --dossier <live dossier> --root <X> --host <opencode|claude> \
  --out <X>/.agent-staging/payloads/<id>.md --package <P> \
  --contract-paths '<f1,f2>' --test-command '<verified command>' \
  --hooks '<HOOKS>' --manifest <X>/.agent-staging/payloads/wave<k>.txt
```

It fills every mechanical field from the files, adds each contract member's
`path:line` to the owned paths so an implementer starts where the stub is,
regenerates the integration author's excerpt from the live dossier, and
leaves every judgement field as `@@FILL_<FIELD>@@` with a one-line hint.
Replace each `@@FILL_…@@` yourself — `check_payload.py` refuses any that
is left — or pass it with `--set FIELD=VALUE`. `--contract-paths` is for this
phase's kinds, whose files are still stubs; a Phase 7 reviewer or fix
implementer takes CONTRACT from the dossier instead. Pass `--contract-paths` as
one comma-separated string, and quote every `--set` value: the command must
run the same under bash and zsh, which differ on unquoted word splitting, so
never feed it an unquoted variable or an array. A `--set WORKTREE_DIR=<path>`
is used as given; only the derived value gets the `-<package>` suffix. Each
pasted contract file opens with a `# path:` line, and a Python file under a
package with a `# import:` line. The manifest line it appends
is the `prepare_wave.py` input. A composed payload is a final: the lint and
the same-turn re-read below apply to it unchanged.

Detail: `ref_section` with `phase-4-conditional` and the heading "Payload rules the lint enforces: test forms, CI environment, setup claims, boundary paths, templates". (read it when writing the judgement fields of a test-author payload.)

Detail: `ref_section` with `phase-4-conditional` and the heading "Under Claude Code the spawn prompt IS the payload".

### Check every return with one call

**Check every return with one call.** When an agent returns, run
`python3 ${PLUGIN_ROOT}/scripts/verify_return.py --root <its worktree> --branch
<branch> --base <fork sha> --owned <OWNED_PATHS> [--contract-hash …
--contract-files …] --release <ID>-wave<k> --provider … --model … --log <live
dossier> --agent <id>`: state, commits, paths touched beyond ownership, stubs
left, the contract hash and the slot release, with the mechanical half of the
`RETURNED` line written to the build log. You add the ruling, not the facts.
Add `--report <file holding the agent's return> --kind <agent kind>` and it
also checks the report against the kind's tool list: `UNVERIFIED: <kind> claims
a test run but has no Bash; re-run it yourself` flags the return. A kind
without Bash cannot have run what it reports — that verdict is fabricated, so
run the claimed suite or diff yourself and log your result, never the claim.

Detail: `ref_section` with `phase-4-conditional` and the heading "A whole-file rewrite is diffed first; a payload-unread return is discarded". (read it when a blind author returns a whole file, or a return is flagged `payload-unread`.)

Detail: `ref_section` with `phase-4-conditional` and the heading "Under Claude Code, send the wave through the workflow, once". (every wave under Claude Code.)

### Checkpoint after each wave

**Checkpoint after each wave.** When every agent of a wave has returned and
its `RETURNED` lines and rulings are in `## Build log`, call `phase_checkpoint`
with `Phase 4 — wave <k>` and the hand-off: the waves still to spawn, the
packages that returned and their SHAs, the open `GAP:` and `CONTRACT-CHANGE:`
items. The next wave starts from that summary and the dossier, not from the
last wave's transcript.

Detail: `ref_section` with `phase-4-conditional` and the heading "Validate TEST_PATHS against the host's boundary before every test-author spawn". (before any test author spawns.)

Detail: `ref_section` with `phase-4-conditional` and the heading "Gate the integration-test-author the same way before it spawns". (also holds `CONTRACT_HASH`: stamp every test author's world at spawn.)

### Commit for the test authors yourself — at Phase 5, not now

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
