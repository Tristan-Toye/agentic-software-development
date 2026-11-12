# Synthesis: index

This folder answers nine design questions for the visual-review sub-agent. Each document ends with a recommendation (USE, BUILD or SKIP) and a list of sources. Status of every document: PROPOSED. The owner decides.

## Documents

| Q | Question | Document |
|---|---|---|
| Q1 | Which representations at which level? | [q1-representations.md](q1-representations.md) |
| Q2 | How is a diagram diff modelled? | [q2-diff-model.md](q2-diff-model.md) |
| Q3 | How do we extract facts from Rust? | [q3-extraction-from-rust.md](q3-extraction-from-rust.md) |
| Q4 | How do we keep diagrams free of clutter? | [q4-abstraction-and-clutter.md](q4-abstraction-and-clutter.md) |
| Q5 | Which rendering stack runs on the dashboard? | [q5-rendering-stack.md](q5-rendering-stack.md) |
| Q6 | How does approval and element commenting work? | [q6-approval-and-comments.md](q6-approval-and-comments.md) |
| Q7 | Where in the pipeline does the step run? | [q7-pipeline-placement.md](q7-pipeline-placement.md) |
| Q8 | How do we detect stale diagrams and keep a living map? | [q8-staleness-and-living-map.md](q8-staleness-and-living-map.md) |
| Q9 | How do we measure value? | [q9-measuring-value.md](q9-measuring-value.md) |

## Input documents (written before this folder)

- [current-state.md](current-state.md): the pipeline, dashboard and config-harness facts.
- [representations-beyond-uml.md](representations-beyond-uml.md): the representation survey.
- [review-ux-and-feedback.md](review-ux-and-feedback.md): the review UX survey.

## Warning: two numbering schemes

The [papers README](../papers/README.md) uses an older list of questions. In the "Answers" column of its table, "Q1" means abstraction and clutter, not representations. Use this table to map.

| Papers README label | Meaning there | Synthesis document |
|---|---|---|
| Q1 | abstraction levels and clutter | [Q4](q4-abstraction-and-clutter.md), also [Q1](q1-representations.md) |
| Q2 | grounding diagrams in code | [Q3](q3-extraction-from-rust.md) |
| Q3 | mapping code to product features | [Q8](q8-staleness-and-living-map.md), [Q3](q3-extraction-from-rust.md) |
| Q4 | diagram diffs for change approval | [Q2](q2-diff-model.md) |
| Q5 | sequence and behaviour diagrams | [Q1](q1-representations.md), [Q5](q5-rendering-stack.md) |
| Q6 | interactivity and navigation | [Q5](q5-rendering-stack.md), [Q6](q6-approval-and-comments.md) |
| Q7 | keeping diagrams in sync | [Q8](q8-staleness-and-living-map.md) |
| Q8 | LLM generation accuracy and verification | [Q3](q3-extraction-from-rust.md), [Q9](q9-measuring-value.md) |
| Q9 | review and approval UX | [Q6](q6-approval-and-comments.md), [Q9](q9-measuring-value.md) |

The mapping is by topic. It is my reading, not stated in the papers README.

## Reading order

1. [current-state.md](current-state.md) for the facts.
2. Q1, then Q4, for what to draw.
3. Q3, then Q2, for how to get and compare the facts.
4. Q5 and Q6 for the dashboard.
5. Q7 and Q8 for the pipeline and upkeep.
6. Q9 for measures.

Then read [OVERVIEW.md](../../../research/OVERVIEW.md), [OPEN-QUESTIONS.md](../../../research/OPEN-QUESTIONS.md) and [gaps.md](../../../research/gaps.md).
