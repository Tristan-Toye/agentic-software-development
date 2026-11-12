# Q6. Approval flow and element-anchored comments

- **Question**: How does the owner approve the diagrams on the dashboard, and how does a comment attach to one diagram element?
- **Status**: PROPOSED. The policy conflict below is open. This document does not decide it.

## Facts: the existing owner-question flow

- The agent asks with `ask_owner.py ask --option "A|label|consequence" ... --recommend A --blocks TEXT --kind design|product|uncertainty`. A question has 2 to 4 options ([current-state.md](current-state.md), B2).
- A question is an append-only JSONL file. `Question` has `options`, `recommendation`, `blocks`, `qkind`, `lines`, `answer`. The first owner answer is final. Later lines throw `AnsweredError` (`shared/questions/store.ts:105-127`, from [current-state.md](current-state.md), B2).
- The owner answers with `POST /api/questions/:id/messages`, `kind` `followup` or `answer`. A follow-up line lets the owner ask the agent without closing the question. Slack mirrors the same store ([current-state.md](current-state.md), B2).
- `Line` already allows extra keys (`[k: string]: unknown`, `store.ts:6-12`). An `anchor` key needs no store change. The UI work is new ([current-state.md](current-state.md), B3).
- Today a question card shows text and options only (`server.ts:794-830`). The page needs a diagram panel keyed by the run ([current-state.md](current-state.md), B3).
- Under the runner, a pending owner answer blocks the run. A time-boxed default exists as a pattern in provisional rulings ([current-state.md](current-state.md), B3).
- A block ruling is a permission flow, not a design-review flow ([current-state.md](current-state.md), B2).

## Fact: the policy conflict

The ask policy says "Never ask for approval" (`references/owner-questions.md:96`). Product questions are allowed (`owner-questions.md:93`). A visual review is an approval gate, and the owner asked for it ([current-state.md](current-state.md), B3).

Options. The owner chooses. See [../../../research/OPEN-QUESTIONS.md](../../../research/OPEN-QUESTIONS.md), OQ-01.

| Option | What changes | Cost |
|---|---|---|
| A. Named exception | Add one rule to `owner-questions.md`: the visual-review question may ask "is this the scope you want". | Small text change. The "never approval" rule gets an exception that others may copy. |
| B. Frame as a product question | The question asks "Is this what the feature should do?" with options that choose scope. It never says "approve". | No policy text change. Risk: the wording hides what is an approval gate. |
| C. No gate | Show the diagrams on the dashboard. Do not block the run. The owner comments if wanted. | No block. Loses the point of reviewing before the fan-out. |

Proposed resolution (for the owner to accept or reject): B for the question wording, and add one sentence to the policy that names the visual-review question as a product question. This keeps the rule and makes the case explicit. This is a proposal, not a decision.

## Facts: review UX ideas

- Gerrit: a stable change identity survives rebase. Give each element a stable ID that survives regeneration ([review-ux-and-feedback.md](review-ux-and-feedback.md), section 2).
- Figma branch review is all-or-nothing per branch. Per-element verdicts need a design of their own ([review-ux-and-feedback.md](review-ux-and-feedback.md), section 2).
- Structurizr review has comments with no threads and no resolve. The dashboard already has threads through follow-up lines ([review-ux-and-feedback.md](review-ux-and-feedback.md), section 2).
- Three ADR outcomes (accept, rework, reject) map to the dashboard options. The pipeline extracts ADRs at Phase 8 ([review-ux-and-feedback.md](review-ux-and-feedback.md), section 2).
- Reviewers scope their sessions and want to mark what is reviewed ([code-review-comprehension-strategies](../papers/code-review-comprehension-strategies.md)).
- A node toolbar pattern ("ask about this node") exists in xyflow ([xyflow analysis](../../../inspiration-repos/analysis/xyflow.md)).
- Notes with kind `risk | behaviour | note` and `refs` are a ready model for findings ([openarc analysis](../../../inspiration-repos/analysis/openarc.md)).
- mermaidiff: asking for an edit in chat updates the open tab ([mermaidiff analysis](../../../inspiration-repos/analysis/mermaidiff.md)).

## Proposed design

This is a proposal, not an evidence-backed result ([review-ux-and-feedback.md](review-ux-and-feedback.md), section 4).

1. Every element has a stable ID, a one-line description in product words, and a list of `path:line` evidence.
2. The owner clicks an element and picks one verdict: **OK**, **Question** (opens a follow-up line), **Change** (text says what), **Remove from scope**.
3. Unreviewed elements stay visible as "not yet looked at". The summary shows "12 of 14 elements checked, 1 question, 1 change".
4. The overall decision is one question with 2 to 4 options, for example: "A. Scope is right. B. Scope too large (pick elements to drop). C. Scope too small (say what is missing)." Element verdicts feed the text of the answer.
5. Because the first answer is final, the owner sends changes as one batch at the end. A "Question" verdict uses the follow-up line, which does not close the question.
6. A "Change" verdict is a re-plan input, not a code edit. The orchestrator updates the contract, then regenerates the diagram. Regeneration keeps element IDs, so old comments stay attached.
7. A comment line carries `anchor: {diagram, element}`. No store change.
8. The option `consequence` shows the changed work packages ([review-ux-and-feedback.md](review-ux-and-feedback.md), section 6).
9. A time-boxed default ("continue with the recommendation after N hours, and log it") needs an owner decision because of the policy rule ([review-ux-and-feedback.md](review-ux-and-feedback.md), section 6).

## Open points

- Order of events when the owner picks "Change": the orchestrator edits the contract, and Phase 2 and 3 checks may run again. The inputs do not describe a loop back. Cost of a contract change after fan-out is high; before fan-out it is the cheapest ([current-state.md](current-state.md), B3).
- Slack mirroring. The question store is mirrored to Slack. Element verdicts cannot be given in Slack. The text of the question must stand alone. (Inference.)
- Small changes. The gate may be skipped for changes below a size gate, if the owner wants one. The owner has not said ([current-state.md](current-state.md), B4).

## Recommendation

**BUILD** element verdicts (OK, Question, Change, Remove from scope) that feed one product question with 2 to 4 options, using the existing JSONL store and an `anchor` key.

**USE** stable element IDs, a "not yet looked at" state and a batch send at the end.

**USE** the existing follow-up line for "Question" verdicts.

**USE** option B for the policy conflict as a proposal, and put the final choice to the owner.

**SKIP** per-element approval that closes the question early, and SKIP Structurizr-style comment-only review.

## Sources

- Synthesis input: [current-state.md](current-state.md), [review-ux-and-feedback.md](review-ux-and-feedback.md).
- Papers: [code-review-comprehension-strategies](../papers/code-review-comprehension-strategies.md), [modern-code-review-bacchelli-bird-2013](../papers/modern-code-review-bacchelli-bird-2013.md).
- Analyses: [xyflow](../../../inspiration-repos/analysis/xyflow.md), [openarc](../../../inspiration-repos/analysis/openarc.md), [mermaidiff](../../../inspiration-repos/analysis/mermaidiff.md).
- Sources: [structurizr](../sources/structurizr.md), [icepanel-ilograph-terrastruct](../sources/icepanel-ilograph-terrastruct.md).
