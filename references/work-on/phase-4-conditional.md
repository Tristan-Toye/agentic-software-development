<!-- /work-on, Phase 4, triggered parts. phase-4.md names each trigger; read the
     one section with ref_section, never the whole file. -->

# /work-on Phase 4 — conditional parts

Parts of the Phase 4 fan-out that apply only to some builds: a large package, a concept shared by several
packages, three or more unit authors, and spawning under opencode. `phase-4.md` names the trigger.

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
construction. A second empty return goes to the user as an infrastructure
problem. Never classify an empty-artifact return as a `GAP:`, a vacuous
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
