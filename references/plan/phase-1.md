<!-- /plan, Phase 1. Read at the start of this phase; the rules that hold
     in every phase stay in primary-agents/plan.md. -->

## Phase 1 — Frame: what is asked, and is it worth building

Phase 1 ends at a stop: the owner sees the frame and approves it, changes it,
or confirms a no-go. Nothing is investigated in depth before that answer — a
wrong reading of the intent is the most expensive mistake available to you,
and a frame costs the owner one click when it is right.

### Intake


`$ARGUMENTS` is a description of any kind. Classify it and gather what it
points at:

- **A Jira key** (`PROJ-142`) → fetch it with the Atlassian Rovo tools
  (`getAccessibleAtlassianResources` once for the `cloudId`, then
  `getJiraIssue`). Read the description and the comments. Record the key.
- **A deferred-issue ID** (`D-3`, from `/deferred`) → find its ledger line:
  the `DEFERRED:` ledger in the `## Build log` of the dossier that captured
  it, or `.discovery/deferred-ledger.md` when no dossier covers it. The line
  is the problem statement — its `path:line` is your first anchor, its risk
  and proposed fix seed `## Problem` and `## Approach`. Show the user the
  restatement as usual, quoting the ledger line. With the source given
  (`<source ID> D-3`, as `/fix-deferred` and the dashboard's Plan it start
  it), read that dossier's ledger. Once the new dossier has its ID, log
  `DEFERRED-SOURCE: <source ID> D-3` in **the new dossier's own** `## Build
  log` (`dossier_edit.py log`): the dashboard links the item to it from
  there, and no other run's copy of the source is touched. Log
  `DEFERRED-DOSSIER: D-3 → <new ID>` in the source dossier's `## Build log`
  too when its copy is yours to edit (no live run on it).
- **A dossier ID** (`W-014`) → a `planned` dossier that needs this command
  again: `/work-on` returned it with a `RETURNED-TO-PLAN:` line, or an
  earlier `/plan` run stopped at its review budget. Read the last lines of
  its `## Build log` first — the note or the unresolved change requests are
  your brief. Re-enter at Phase 2 with what the note questions (the frame card is skipped while the frame stands, below); the existing
  sections are yours to fix, not to rewrite. In committed mode find the live
  copy before you read it: an existing plan worktree for this ID (re-plan
  there, on its branch), else a build worktree `../<repo>-<ID>` — the copy
  `/work-on` returned lives there; copy that one file into a fresh plan
  worktree made as below and leave the build worktree for the user to
  remove. Never plan on a build branch.
- **A brief file** (`the description in <path>`, as the runner's `--seed`
  passes it) → read the file: it is the free-form description, classified
  like any other. It is not code to trace.
- **A stack trace or a log excerpt** → the frames are your first anchors.
- **A file path or a symbol** → your starting point for the trace.
- **A free-form description** → an intent. Restate it in one sentence and show
  the user your restatement before you invest in investigation. A wrong reading
  of the intent is the most expensive mistake available to you here.

**Committed mode: make the plan worktree now**, once the ticket or the slug
is known and before any investigation — the anchors you write in Phase 2 must
be true in the tree the PR is made from. `<base>` is the branch checked out in
the checkout `/plan` was started from (`git rev-parse --abbrev-ref HEAD`;
detached → ask). Fork from its up-to-date remote tip, so the plan starts
current without touching the user's checkout:

```bash
git fetch origin <base>
git worktree add ../<repo>-plan-<KEY> -b plan/<KEY> origin/<base>
```

`<KEY>` is the Jira key when one exists, else the slug — never a dossier ID,
which is not minted yet. A `<base>` with no remote forks from the local
branch instead; say so. This worktree is `${RUN_ROOT}` from here. In local
mode this step does not exist.

### The product model

The plan is checked against the repository's product model,
`${RUN_ROOT}/docs/product/model.md` (`formats/product-model.md` has the
shape). It holds the product's **concepts** (`- **term** — meaning`), its
**scenarios** — each with the change and retry cases that redo work ("the
editor changes X", "re-run after an edit") — and its **product rules**. Read
the concepts and the scenarios the request names; open the rest only when a
later phase needs it.

**When the file does not exist, bootstrap it once for the repository.** Write
a draft from what the repository already records: the ADRs
(`docs/adr/index.md`), `README` and `docs/`, the research notes under
`.discovery/analysis/`, and the owner's earlier rulings (`OWNER-RULING` lines
in the dossiers' `## Build log`). Every concept and rule cites its source;
mark the file `status: draft` on its first line. The draft goes on this
phase's frame step as a card of its own (below). After the owner approves it,
change the first line to `status: confirmed`. Later plans do not rewrite it:
Phase 8 proposes each new product rule on the final card and adds it only
after the owner approves it there. In committed mode the file goes in the plan
PR; in local mode it is a change in the checkout, which the report names.

### The overlap search

Before you frame, look for work that already covers the request:

```bash
python3 ${PLUGIN_ROOT}/scripts/plan_flow.py overlap --root ${RUN_ROOT} \
  --files <paths the request names> --terms <product-model concepts it touches>
```

It lists every dossier (in flight, done and deferred items too) that touches
the same files (`file …`) or the same concepts (`concept …`). Then list the
open pull requests whose title names a dossier
(`gh pr list --state open --search "W- in:title"`) and the open `plan/*`
branches. When a dossier that is not `done`, or an open pull request, already
addresses the same problem — even by another approach — the frame recommends
**no-go: covered by W-NNN (<status or PR>)**: extend that dossier through its
own flow instead of minting a second one, and name any part it leaves out. A
`done` dossier on the same problem is not a no-go when the defect is still in
the tree: cite it in `## Problem` later as the change that did not hold. An
existing ADR that already settles the question is a no-go too (amend the ADR
instead of planning duplicate work).

### The frame

Write `${RUN_ROOT}/.agent-staging/plan-<key>/frame.md` (`<key>` as in
Phase 2): at most one page, for a person reading on a phone.

- **Intent** — the request in one sentence, in product terms.
- **Concepts** — the product-model concepts it touches, and any concept it
  adds or redefines (a new or redefined concept is a CONFIRM row later).
- **Scenarios** — the product-model scenarios it changes, with their change
  and retry cases.
- **Scope** — in and out, one line each.
- **Overlap** — what the overlap search found, or `none`.
- **Unknowns** — what could change the outcome, and how each is answered. An
  unknown that could turn the plan into a no-go or change its approach gets a
  **spike**: a timeboxed experiment that runs inside this run, at the start of
  Phase 2 (no new dossier). Name it, its question and its timebox.
- **Recommendation** — go, or no-go with the reason.

### The frame card — the owner approves before you continue

Put the frame to the owner as one card, in the card layout
(`owner-questions.md`, "The card layout"): the decision first, the
recommendation and its reason under it, the frame's strongest points as at
most six bullets, and the rest of the frame under `**Details**`, which the
dashboard folds. The dashboard and an interactive run show the same card;
only where the owner clicks differs:

```bash
python3 ${PLUGIN_ROOT}/scripts/ask_owner.py ask --root ${RUN_ROOT} --dossier <key> \
  --kind product --topic plan-frame --blocks "the whole plan" \
  --question "$(cat <<'CARD'
Plan <the intent, in a few words> as framed?
**Recommendation:** A — <why go, or C — why no-go, in one line>
- **Intent:** <one sentence>
- **Scope:** in <…>; out <…>
- **Overlap:** <W-NNN and how it relates, or none>
- **Spike:** <name, question, timebox, or none>
**Details**
**Concepts.** <…>
**Scenarios.** <…>
**Unknowns.** <…>
CARD
)" \
  --option "A|Go|plan it as framed" --option "B|Change|<what the owner may correct>" \
  --option "C|No-go|<the no-go and its reason>" --recommend A
```

When Phase 1 bootstrapped the product-model draft, ask its approval as a
second `plan-frame` card of its own (one decision per card), with the same
layout: "Adopt this product model as the repository's draft?", the
recommendation, the concepts and rules it adds as bullets, the full draft
under `**Details**`. Then run `ask_owner.py wait <id>` for each card as a
background command and end the turn; nothing in this run is unblocked before
the answers.

- **Go** → claim the dossier ID now (Phase 4 writes it):
  `python3 ${PLUGIN_ROOT}/scripts/claim_id.py --repo <repo> --kind W --for plan/<KEY>`
  (`formats/evidence-and-ids.md` §4: final, and no open sibling plan PR holds
  it; only when the claim exits 1 — no remote, no push access — mint
  provisionally as §4 says). Copy the frame into the findings file.
- **Change** → apply the owner's correction to the frame and continue; ask
  again only when the correction changes the recommendation.
- **No-go** → a no-go needs the owner's approval: it is never your call
  alone. On approval, write no dossier; report the reason and the dossier or
  ADR that covers the request, then remove the plan worktree (committed mode).

**A re-plan of an existing dossier does not stop again** while its frame
stands: log `FRAME: unchanged since <date of the earlier frame approval>` and
continue. It stops at the frame card when the note that sent it back changes
the intent, the concepts or the scope.
