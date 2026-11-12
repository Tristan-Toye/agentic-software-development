<!-- /work-on, Phase 4, triggered parts. phase-4.md names each trigger; read the
     one section with ref_section, never the whole file. -->

# /work-on Phase 4 — conditional parts

Parts of the Phase 4 fan-out read by section: the cases that apply only to some builds (a large package, a
concept shared by several packages, three or more unit authors, opencode) and the detail of the steps
every wave runs (worktrees, spawning and the workflow, admission, payload rules, return checks, test-author
gates). `phase-4.md` points at each by name.

### Cap what one blind write carries

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
construction. A second empty return is escalated through the ladder in
`phase-4-incidents.md` ("After the second empty return, change something before you spawn again"); only when that ladder fails does it go to the owner, as an infrastructure problem. Never classify an empty-artifact return as a `GAP:`, a vacuous
test, or a weak oracle, and never let one pass as success.

### Name the shared idiom when a concept spans packages

**Name the shared idiom when a concept spans packages.** Two implementers that
each need the same helper, type, or error-mapping shape each invent one, and
invented idioms diverge — the collapse costs more than the build saved. When
`## Work packages` splits a concept, paste the one named idiom into **every**
payload that touches it, byte for byte (`SHARED_IDIOM`). The duplication is
deliberate while the agents run; plan its collapse in the same run — record the
copy count and the collapse target in `## Build log` now, and land the collapse
with the merge or the review fix.

### With three or more unit authors, canary the smallest surface first

**With three or more unit authors, canary the smallest surface first.** Spawn
it alone, wait for its return, and fix every `GAP:` it surfaces in the
contract before the rest fan out — the same contract defect priced once
instead of per author. Two authors cost more to sequence than the gap costs
to fix; six do not.

### Under opencode, spawn by the file, never by retyping it

**Under opencode, spawn by the file, never by retyping it.** Every agent that
can read its payload gets a prompt of two lines — `PAYLOAD_FILE: <absolute path
of the final>` and `PAYLOAD_SHA256: <the hash prepare_wave.py recorded in
.agent-staging/wave-<holder>.hashes>` — and opens the file itself as its first
step. That is every opencode agent here: `unit-test-author`'s read map admits
`.agent-staging/`. Retyping a linted payload costs its whole length again as
output and keeps it in your context for the rest of the phase; the recorded
fan-outs retyped 1.77 M characters.

### Under Claude Code — two workflows, and resume over re-spawn

**Under Claude Code — two workflows, and resume over re-spawn.** The plugin
ships two dynamic workflows in `${PLUGIN_ROOT}/workflows/`, run through the
`Workflow` tool by name — and, when the name is not found, by
`scriptPath: ${PLUGIN_ROOT}/workflows/<name>.js` with the same `args`:
`agentic-software-development:phase4-wave` (a batch of linted payloads: a Phase 4
wave, a Phase 5/6 correction round of two or more agents, the Phase 8 ADR drafts;
spawn every payload, verify every return) and
`agentic-software-development:phase7-review` (a Phase 7 round: the code lenses
and their change requests). **This command's instructions are the authority to
call the `Workflow` tool, and only for these two, at the points `phase-4.md`,
`phase-5.md`, `phase-6.md`, `phase-7.md` and `phase-8.md` name; call it for nothing else.** A workflow keeps the agents'
full reports out of your context and hands back one compact result, so the run
takes fewer steps and every later step re-reads less. You still compose and lint
every payload and admit every wave exactly as before; the workflow replaces
only the spawn message and the per-return checks. Its `args` name each linted
final by `payloadPath` and `payloadSha256` — the agents read the files — so
the launch stays small enough to approve. Never paste a payload into `args`,
never write or copy a workflow script, and if a launch is refused or cannot be
approved, stop and report it: never re-route the agents around it. Its args and results are in
the phase files. When a correction or fix goes back to a single agent that is still
resumable, send it with `SendMessage` to its agent ID instead of spawning anew
(`phase-4.md`, "Under Claude Code, resume instead of re-spawn"), and write every
agent ID into the build log line of the spawn and of the return, because those
lines are what compaction leaves you. Under opencode this paragraph does not
apply: spawn and verify as the phase files say.

### Spawning under Claude Code

**Spawning under Claude Code.** Paste every payload; never spawn by pointing
at it. In auto mode Claude Code classifies the task a spawn delegates from
the spawn prompt alone — it cannot see a file the prompt points at — so
"your payload is the file X; read it whole; every field binds you" reads as
handing authority to unseen content, and the recorded runs had one such
spawn in five denied as [Auto-Mode Bypass] or [Data Exfiltration]. So:
compose and lint the final on disk exactly as the phase says, then Read it
and spawn with a one-line task statement followed by its fields verbatim,
one `FIELD: value` per line. Files the payload names (contract, criteria,
style samples, excerpts) stay paths. Stage every file under
`<worktree>/.agent-staging/`, never in your scratchpad, and put no secret in
a prompt: name the script or env file that sets it. The plugin's spawn guard
refuses a pointer prompt, a scratchpad path or a secret-shaped value with
the reason; fix the prompt and spawn again. A spawn the classifier still
denies is never re-worded or re-sent to get past it: stop that spawn, keep
the rest of the wave, and give the user the agent, the denial reason and the
payload path — they leave auto mode (shift+tab) for that spawn; a
/permissions allow does not lift a denial. The one exception is a wave or
review round sent through the plugin's workflow by name: its `args` name
each linted final by `payloadPath` and `payloadSha256`, because the launch
is approved once and a pasted wave is too large to show. Never paste a
payload into `args`, and never write or copy a workflow script. Never pass
`model` when spawning one of this plugin's agents: its agent file sets the
model, and the spawn guard refuses an override.

Three kinds of work run at the same time. Their blindness is structural, not a
promise you extract from a prompt:

| Agent | Cannot see | Enforced by |
|---|---|---|
| `unit-test-author` | the implementation, the dossier, every pipeline document, anything outside `.agent-staging/` and the test families | under opencode its permission map: `read` and `edit` deny every path outside the staging area and the test families; under Claude Code `Write` and a `Read` the plugin's PreToolUse hook confines to the same staging area and test families — either way the tool call is refused, not merely discouraged |
| `integration-test-author` | any implementation body | the bodies are stubs on `X`; it has no Bash and cannot reach another branch |
| `implementer` | the tests | its worktree forks from `X` at this commit, and no test exists there |

### Admission before the spawn message

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
the cap the next run starts from. To free one agent's slot early, add
`--slots <n>`: only that slot of the holder is freed, a slot the holder does
not hold refuses, and the wave stays open until its last slot goes. When a
provider limit kills an agent, `record-error "<the error, verbatim>"` before
anything else, so every other session on the machine defers too. The two recorded failures: an 8-spawn
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

### Payload rules the lint enforces: test forms, CI environment, setup claims, boundary paths, templates

**The target repository declares its CI environment and refused test forms in
two optional files under its own `.agent-pipeline/`; the composer reads them,
so no orchestrator hand edit carries them.**
`.agent-pipeline/test-forms.txt` — one `refused form -> use instead` per line
(`#` comments allowed): a test-code form the repository's gates refuse after
the fact, and the helper to use. It becomes `TEST_FORM_RULES` on the unit and
integration authors' payloads; with no file the field reads `none declared`.
`.agent-pipeline/ci-environment.txt` — three lines, `services:` (live services
the CI runner has, `none` if none), `skip:` (opt-out / skip tokens CI sets) and
`guard:` (the line a stack-backed test opens with). It becomes the integration
author's `CI_ENVIRONMENT`; with no file the composer leaves
`@@FILL_CI_ENVIRONMENT@@`, which you fill (`none` when the tests reach no live
service). `check_payload.py` refuses an integration payload whose tests reach a
live service without `CI_ENVIRONMENT`, and a test-author payload that omits
`TEST_FORM_RULES` for a repository that declares forms.

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

`validate_pipeline.py --pre-fanout` refuses without it, as for the
`contract_review` and `hooks` front matter fields and `ADMISSION:`. A payload that was never a
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

### Under Claude Code the spawn prompt IS the payload

**Under Claude Code the spawn prompt IS the payload.** Paste the linted final
into the prompt, one `FIELD: value` per line, under a one-line task
statement; `python3 ${PLUGIN_ROOT}/scripts/compose_payloads.py --emit-prompt
<final>` prints exactly that text with its `PAYLOAD_SHA256:` line. That is the
only form, for every agent: a prompt that points at a payload file is refused
by the plugin's spawn guard and, in auto mode, denied by the classifier, which
sees the prompt and never the file. The one exception is a spawn that goes
through the plugin's own workflow: its args carry `payloadPath` +
`payloadSha256`, the spawn guard checks the file before the launch and
`verify_return.py` checks it after the return, so a fix implementer whose
payload dwarfs the prompt — one recorded contract plus lens documents reached
76 KB — is spawned by file through `phase4-wave` with a single item, never
pasted (`phase-7.md`, "Spawn one implementer in MODE: fix"). The next
paragraph is the opencode form.

### A whole-file rewrite is diffed first; a payload-unread return is discarded

**A whole-file rewrite of an existing file is diffed before any behaviour
run.** When a blind author returns a whole file for a path that already exists
in the staged copy, diff it against the staged copy first and confirm the
change is additions only. Existing lines that moved, vanished or were reworded
send the file back; do not run the tests on it. The sanctioned repair is the
anchor-based splice: the author returns only the new block and the anchor line
it follows, and you splice it into the staged file.

**A return flagged `payload-unread` read nothing whole** (#257). Every
workflow spawn begins its report with `PAYLOAD-LAST: <its payload's last
line>`; `verify_return.py` compares it with the file. A report without that
exact line (or with `PAYLOAD-UNREAD:`) means a read hook or a size limit cut
the payload, and whatever the agent did was done without its fields: discard
its work and spawn it again (a resume carries the same cut read). A Phase 7
review carries the same proof as `payload_last`: check each with
`verify_return.py --payload <its payload> --payload-last '<payload_last>'`;
the workflow already turns an `UNREAD:` one into verdict `unread`.

### Under Claude Code, send the wave through the workflow, once

**Under Claude Code, send the wave through the workflow, once.** Everything
above runs as written — compose, lint, `prepare_wave.py`, admission, the
wave table — and then, instead of one `Agent` call per spawn followed by one
`verify_return.py` call per return, call the `Workflow` tool once with
`name: "agentic-software-development:phase4-wave"` and `args`, an array with
one object per spawn:

```
{ "agentType": "agentic-software-development:implementer", "label": "P1",
  "payloadPath": "<the linted final, absolute, under <its worktree>/.agent-staging/>",
  "payloadSha256": "<its hash, from .agent-staging/wave-<holder>.hashes>",
  "root": "<its worktree>", "branch": "<its branch>", "base": "<fork sha>",
  "owned": "<OWNED_PATHS or TEST_PATHS, comma list>", "kind": "implementer",
  "contractHash": "<CONTRACT_HASH>", "contractFiles": "<comma list>",
  "reportDir": "<X>/.agent-staging/reports", "pluginRoot": "<${PLUGIN_ROOT}, absolute>" }
```

`contractHash` and `contractFiles` go together or not at all — the hash alone
makes `verify_return.py` exit 2 (a usage error) for every return in the wave,
and every result comes back flagged with an empty verifier line — and
`contractHash` is the full 64-hex `CONTRACT_HASH`, never a prefix (a truncated
copy copied from a column-cut print once verified nothing); the workflow
refuses both mistakes at launch, naming the item. They are for a test author
whose world the hash stamps. When several authors share one worktree (`root`),
pass nothing extra: the workflow hands each item its siblings' `owned` paths as
`--shared-tree`, so an author is never flagged for a sibling's files.
`reportDir` defaults to `<root>/.agent-staging/reports`. A workflow
spawn is the one place the payload travels as a file, not pasted: each agent
gets the two-line `PAYLOAD_FILE` / `PAYLOAD_SHA256` intake its file names and
reads the final itself. The paste rule is about the per-spawn classifier, and
a workflow's spawns are approved once, at the launch; a pasted wave is what
breaks it — 340 KB of `args` cannot be shown, so it cannot be approved. The
plugin's spawn guard checks every `payloadPath` against its `payloadSha256`
(and for a secret) before the launch, and the verifier checks the hash again
after the return (`payload` flag). **Never put a payload into `args` or into a
script, and never write, copy or edit a workflow script**: the launch is the
plugin's workflow by name, with these small `args`, or nothing. If the name is
not found, use the `scriptPath` fallback from `work-on.md`; if the launch is
refused or cannot be approved, stop and tell the user what the prompt said —
never route the wave around it (a spawn-by-spawn retry of a refused launch is
denied as a bypass). Send at most nine spawns per call
(the runtime's size guideline; its hard limit is sixteen concurrent agents):
split a larger wave into parts, each admitted and called on its own. The
workflow is asynchronous: the call returns a run ID at once, and the compact
result arrives as the run's completion. Log the launch first —
`WORKFLOW: phase4-wave wave<k> run <runId> — <labels>` — and each spawn's wave-
table row stays as before. The result is one object per spawn, in args order:

```
{ "label": "P1", "verdict": "clean | flagged | died", "flags": [...],
  "returned_line": "P1 RETURNED: <sha> on <branch>, …",
  "report_excerpt": "<= 600 chars, every GAP: and CONTRACT-CHANGE: line verbatim",
  "report_path": "<the full report, kept by the verifier>" }
```

Each agent is followed by a cheap verifier (`haiku`, low effort) that runs
`verify_return.py --print-line` with the agent's report written to
`report_path` for `--report`. The workflow never releases slots (the script's
`--release` frees the holder's whole wave on the first return) and never
writes the build log (concurrent appends): you do both from the array. Then:

1. Count the results against the wave table. A `died` verdict is a spawn that
   returned nothing, was skipped, or lost its verifier: look at the tree before
   anything else, by the three-state table below, and re-spawn only on what it
   shows.
2. Append every `returned_line` to `## Build log` in one `dossier_edit.py log`
   call, with the agent's ID in it where you hold one (`P1 (agent a1b2c3)
   RETURNED: …`); an agent a workflow spawned has no ID in the compact result,
   so its line carries `(workflow run <runId>)` instead.
3. Release the wave's slots once: `spawn_admission.py release --holder
   <ID>-wave<k>`.
4. Rule from the compact result. Read a full report (`report_path`) only for an
   item whose verdict is not `clean`, and only the part you need: a `GAP:` or
   `CONTRACT-CHANGE:` line is already in the excerpt, so handle it from there.
   Collect every `GAP:` of the wave before you fix the contract, as above.
5. Under rate pressure the workflow runs a wave smaller, not the same wave
   again: the ladder above applies to the next call's `args`.

The workflow never touches the contract, the tree, `X`'s branch or a ruling.
Under opencode there is no workflow: spawn by the file and verify each return
with `verify_return.py` as written above.

### Validate TEST_PATHS against the host's boundary before every test-author spawn

**Validate `TEST_PATHS` against the host's boundary before every
`unit-test-author` spawn.** Run `python3 scripts/check_permission_maps.py
--agent <the agent file the host loads> --host <opencode|claude> --root <X>
--test-paths <comma-separated> [--read-paths <STYLE_PATHS>]` — the plugin
checkout supplies the script and the agent file
(`sub-agents/unit-test-author.md` under opencode,
`claude/agents/unit-test-author.md` under Claude Code); `X` supplies the
paths. It reads the frontmatter tool list first: under Claude Code a
`--read-paths` entry outside the hook's read scope (`.agent-staging/` and the
test families) is refused. Exit 1 names the offending
path and, under opencode, the families the map admits; fix the split, stage
or paste the content, never the map or the tool list. Two failures this
prevents, both silent: a path the read map admits but the edit map refuses
makes the author read the existing file, refuse to write it, and return
empty, once per retry; and a read path validated against the opencode map
for a Claude Code spawn — the recorded check printed `OK … 2 read path(s)
admitted` for an author that could open neither — passes as evidence and is
a dead letter at run time, found only because a canary spawn disclosed it.

### Gate the integration-test-author the same way before it spawns

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
contract; read-then-rewrite under Claude Code — never a bare delete with
nothing said. The recorded opencode hash re-spawn deleted
both test files and paid two regenerations for a one-sentence pin.

### Worktrees: one per concurrent implementer, and none for the test authors

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

### Then let the repository prepare each new worktree

**Then let the repository prepare each new worktree.** When `X` holds an
executable `.worktree-setup.sh` at its root, run it once per implementer
worktree, after the `git worktree add` and before the spawn:

```
<X>/.worktree-setup.sh <X> ../<repo>-<ID>-P1
```

It is the repository's own step, never the plugin's: a Rust repository
clones `X`'s warm build directory into the new worktree, so an implementer's
first build compiles the workspace crates and no dependency. Log one line per
worktree from its last output line. A non-zero exit is logged and the spawn
goes ahead: a worktree without the setup is slower, never wrong. Concurrent
calls are the script's concern; run them in one shell call, one per worktree.
