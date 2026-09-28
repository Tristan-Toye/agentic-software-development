<!-- /work-on, Phase 8. Read at the start of this phase; the rules that hold
     in every phase stay in primary-agents/work-on.md. -->

## Phase 8 — Extract two kinds of durable output

Now, while the evidence is in front of you. A build produces two things worth
keeping, and they go to different places:

| Output | Question it answers | Where it goes |
|---|---|---|
| **ADR** | why is *the code* like this? | `docs/adr/` — committed, ships with the PR |
| **Rule** | how should we *write code here*? | this repo's rules file — committed, ships with the PR |

Both are repo-local and both ship in the PR. The plugin's
`skills/standards/` holds the cross-repo standard and this run never writes
to it: a lesson this build paid for belongs to the repo that taught it.

### 8a — the ADRs

Read `## Build log` and the review replies and ask: **what did this build decide
that would change how a future agent writes code here?**

The material is already there: the contract choices and the alternatives
`/plan` rejected, every `CONTRACT-CHANGE:` ruling, every ambiguous-contract
arbitration, and the trade-offs the architecture and performance lenses
surfaced.

- **Extract selectively.** Zero ADRs is a correct outcome for a defect fixed as
  specified, a rename, or mechanical work. An ADR per dossier means you filled a
  template instead of extracting a decision.
- Write one when a boundary moved, a contract won over a real alternative, a
  performance trade-off was accepted, a convention was set, or a constraint was
  found that future work must respect.
- **When ADRs are due, `document-drafter` drafts them** (`MODE: adr`): you
  select the decisions and their evidence, give it `TARGET_PATHS` under
  `docs/adr/` in `X`, and it writes those files itself in the validated
  format — its Write tool admits `docs/adr/`, `.discovery/`, and
  `.agent-staging/` only — then re-opens each written file and self-checks
  the scrub list over the bytes on disk. You then read each file,
  run the validator, fix every DEFECT, and commit — drafting is mechanical,
  selecting and validating never are.
- A decision an existing ADR already covers is an **amendment**: add the
  constraint to that ADR's `## Consequences` and bump its `date`. Do not mint a
  near-duplicate.
- Write `## Consequences` for the agent who will read it: state the constraint a
  future change must respect, not a summary of the work.
- Mint IDs atomically and set `jira` to the ticket (never the dossier ID — an
  ADR is read by people who never open a dossier, and in local mode the
  dossier is not even in the repository). A number minted here is provisional
  until the Phase 9 sync, where `--finalize-ids` settles a collision with a
  concurrent branch. Then run
  `python3 ${PLUGIN_ROOT}/scripts/validate_pipeline.py` with no arguments, in
  `X`, and fix every DEFECT in what you just wrote — you wrote prose under the
  plan's own language rules with no reviewer behind you, and `--write-index`
  regenerates the index and checks nothing. Only then regenerate the index
  with `--write-index`, validate once more, and record the ADR IDs in the
  dossier's `adrs` front matter field (committed mode: `X`'s copy, committed
  with the ADRs below).
- **Commit the ADRs on `X` — never the index.** Commit them with the Jira key
  prefix. Regenerate the index locally
  to validate, then `git restore docs/adr/index.md` before committing: CI
  owns the index on the target branch, so a committed index would collide
  with every concurrent branch. The ADRs ship inside the PR, so the humans
  who review the code review the decision record with it.

ADRs are the part of a build that outlives the machine it ran on, and the
reason `/plan` checks `index.md` before it investigates anything. Dossiers
stay in `.discovery/` — local by default, inside the PR in committed mode —
and the index never lists them.

### 8b — the rules this run paid for

Two kinds of evidence feed this step, and both are the same species: **a place
where something you wrote or did failed, and a rule would have prevented it.**

1. **Contract craft** — the row-3 arbitrations from Phase 6 and the `GAP:`
   returns from Phase 4: a place where your contract failed to say something
   observable, and an agent could not proceed.
2. **Orchestration** — anything **you** got wrong that a rule would have
   prevented: a payload convention that fought the toolchain, a criterion no
   test file owned, a false statement made to a resumed agent, a check skipped
   before a commit.

Neither is an ADR — an ADR says why the code is like this, and these say how
the next person should write code here. Without this bin, the lesson stops at
a `## Build log` line, which no future run reads.

Ask: *is this lesson a one-off, or a pattern that will recur on the next
dossier?*

- **A one-off** — the lesson stays in `## Build log`. Done. This is the common
  answer; most runs extract no rule at all.
- **A pattern** — one this run hit more than once, or that you recognise from
  an earlier dossier's build log — draft a rule and **present it to the user
  for ratification**. You never write one unratified.

**Ratification rides the PR.** The drafted rule is committed on the branch
and shown in the PR description under a `## Pending ratification` section:
the rule text, why it exists, and the trail that bought it. PR approval
ratifies the rule with the code; PR rejection means you remove the section —
and the rule — before anything merges. The humans already reviewing the code
are the ratifiers, and the record of their yes is the PR itself, so a rule
never waits on a conversation that may never happen.

**Where it goes.** The repo's own rules file, committed with the ADRs. Look
for one first (`docs/adr/RULES.md`, a rules section in `CLAUDE.md`, an
existing conventions doc). If the repo has none, **ask the user where it
should live, suggesting `docs/adr/RULES.md`** — next to the ADRs it is
extracted alongside — and record the answer in the dossier's front matter so
later runs on this repo do not ask again. Never create the file silently, and
never write to the plugin's `skills/standards/`.

**Write it minimal, and in this repo's language.** One rule is:

```
### <rule, imperative, one line>
<The testable statement — precise enough to cite in a blocking CR and to
conform to without asking. Define any ambiguous term inline.>

<A NO/YES example pair, in the language this repo is written in — never
the plugin's C# if the repo is Python, TypeScript, or anything else.>

Why: <the failure, with its trail: the dossier ID, arbitration number, or
GAP: return — enough to reconstruct it.>
```

Nothing else. No priority table, no scope field, no supersedes bookkeeping —
the repo's file is read by whoever is about to write code in it, not
audited.

**The bar is high, and it is what keeps the file worth reading**: a rule
earns its place only by being non-obvious *for this repo*, or by having a
real failure behind it. A rule any competent engineer already follows is not
a learned rule — do not draft it. Before drafting, check the file for
overlap: an entry covering part of this lesson gets rewritten to cover both,
never joined by a near-duplicate.

Recurring `GAP:` returns of the same shape are the same signal from the
cheaper side — a test author told you the contract was thin *before* any code
was written — and that closes the loop: a failure that cost a double re-spawn
today makes the next contract in this repo sharper, shipped in the PR where
the humans reviewing the code see the rule it bought.

**Then ask the graduation question: does this rule stop at this repo?** A
rule about this codebase (`use the Lima VM for database tests`) stays. A rule
about the *craft* — a payload field the skeleton lacks, a contract shape that
always fails, an orchestration step that always pays — will be re-learned by
every repo this pipeline touches, and belongs in the plugin's flow documents
instead. You never write the plugin from inside a build. Record a
`GRADUATION: <one line>` entry in `## Build log`, and at Phase 9 report time
**draft a graduation issue** for the plugin repository:

- Title: `[graduation] <the rule, one line>`; label: `graduation`.
- Body sections, in order: `## Lesson` — the imperative one-liner plus the
  testable statement. `## Failure shape` — the `GAP:` or row-3 shape it
  prevents and what it cost in re-spawns. `## Trail` — target repo, date,
  phase; **never the dossier ID** — the plugin repository has no use for it. `##
  Proposal` — which flow document, payload field, or script the lesson
  should land in. `## Acceptance` — what must change in the plugin for the
  issue to close.

One issue per lesson, so each closes independently. **Create the issue only
on the user's explicit yes** — it is an external action like any other.
Log lines are not schedulable and nobody closes a log line; the issue is the
forcing function that makes the graduation loop actually close.

**Then write the outcome back onto the `GRADUATION:` line in `## Build log`,
before you leave Phase 8b.** A resolved line ends in exactly one of three
suffixes:

- `— issue <owner>/<repo>#<n>` — the user said yes and `gh issue create`
  returned that number. Owner and repo, never a bare `#<n>`: the line is read
  from a different repository than the one the issue lives in.
- `— absorbed: <commit>` — a later flow change already landed every clause of
  the lesson, so no issue is owed. Not hypothetical, and the reason an issue
  link alone cannot close the loop: a lesson can be settled by a change that
  never knew it was owed.
- `— declined: <one line>` — the user said no, and why, so the next run does
  not re-ask.

A line carrying none of the three means the decision is still open, and that
— not the existence of an issue — is what keeps it visible:
`/overview-dossiers` mines bare `GRADUATION:` lines as a health signal, so an
awaiting-yes graduation appears on every overview until its line is closed.
Improvise no fourth form. A suffix a grep cannot match reads as no suffix,
and the signal then reports a finished lesson on every run until it is
skipped for being wrong every time.
