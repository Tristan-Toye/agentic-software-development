# Overview: visual-review research

Status: PROPOSED. This file maps the documents, lists the decisions and sizes the work. Conventions: [CONVENTIONS.md](CONVENTIONS.md).

## Document map

| Layer | Where | What |
|---|---|---|
| Papers | [../visual-review/research/papers/README.md](../visual-review/research/papers/README.md) | Paper notes |
| Sources | [../visual-review/research/sources/README.md](../visual-review/research/sources/README.md) | Tool and legacy-tool notes |
| Inspiration repos | [../inspiration-repos/CLAUDE.md](../inspiration-repos/CLAUDE.md) | 10 code analyses |
| Synthesis | [../visual-review/research/synthesis/README.md](../visual-review/research/synthesis/README.md) | Q1 to Q9, current state |
| Features | [../visual-review/README.md](../visual-review/README.md) | Six feature cases VR-01 to VR-06 |
| Links | [LINKS.md](LINKS.md) | All external URLs |
| Gaps | [gaps.md](gaps.md) | What is thin, unverified or contradicted |
| Open questions | [OPEN-QUESTIONS.md](OPEN-QUESTIONS.md) | Owner decisions OQ-01 to OQ-10 |

## Decision register

All entries are PROPOSED. None is decided. The owner accepts or changes each.

| ID | Decision | Proposal | Basis | Status |
|---|---|---|---|---|
| DR-01 | Representations | BUILD a four-level set (L0 to L3) with a feature map, a flow view and a change view; SKIP UML as the main view | [Q1](../visual-review/research/synthesis/q1-representations.md) | PROPOSED |
| DR-02 | Diff model | BUILD one merged graph with status added, changed, removed, context, and three mode filters | [Q2](../visual-review/research/synthesis/q2-diff-model.md) | PROPOSED |
| DR-03 | Rust extraction | USE layered extraction (cargo metadata first, deeper layers after a spike) with evidence tiers | [Q3](../visual-review/research/synthesis/q3-extraction-from-rust.md) | PROPOSED |
| DR-04 | Clutter control | BUILD hard budgets per view and a validator that rejects over-budget graphs | [Q4](../visual-review/research/synthesis/q4-abstraction-and-clutter.md) | PROPOSED |
| DR-05 | Rendering stack | USE ELK on the server, BUILD a hand-written SVG canvas, USE Mermaid only as export | [Q5](../visual-review/research/synthesis/q5-rendering-stack.md) | PROPOSED |
| DR-06 | Approval flow | BUILD element verdicts feeding one product question; frame it by option B for the policy conflict | [Q6](../visual-review/research/synthesis/q6-approval-and-comments.md) | PROPOSED |
| DR-07 | Pipeline placement | BUILD a `diagram-author` agent at the end of Phase 3 with a `VISUAL-REVIEW:` gate | [Q7](../visual-review/research/synthesis/q7-pipeline-placement.md) | PROPOSED |
| DR-08 | Staleness | BUILD hash-based staleness records; fresh extraction for structure, committed copy for product layer | [Q8](../visual-review/research/synthesis/q8-staleness-and-living-map.md) | PROPOSED |
| DR-09 | Measures | BUILD tool-quality and adoption measures; use late contract changes as the value measure | [Q9](../visual-review/research/synthesis/q9-measuring-value.md) | PROPOSED |

## Sizing

The work is larger than one dossier. The proposal is a spike D0 plus six dossiers, in this build order:

1. **D0 spike.** Run elkjs on the Bun server at 50, 100 and 200 nodes. Run `cargo metadata` and one deeper extractor on config-harness. No product code ships. This answers the open points in [Q3](../visual-review/research/synthesis/q3-extraction-from-rust.md) and [Q5](../visual-review/research/synthesis/q5-rendering-stack.md). Result: [d0/REPORT.md](d0/REPORT.md).
2. **D1** [VR-01](../visual-review/features/01-change-graph-model-and-extraction/README.md): graph schema, validator and extraction.
3. **D2** [VR-02](../visual-review/features/02-diff-view/README.md): diff and modes.
4. **D3** [VR-03](../visual-review/features/03-dashboard-renderer/README.md): server layout and canvas.
5. **D5** [VR-05](../visual-review/features/05-diagram-author-agent-and-pipeline-gate/README.md): agent and gate. It can start after D2 with fixtures.
6. **D4** [VR-04](../visual-review/features/04-approval-and-element-comments/README.md): verdicts. It waits for OQ-01.
7. **D6** [VR-06](../visual-review/features/06-living-product-map-and-staleness/README.md): living map and staleness.

D1 to D3 are the critical path. D6 can start as a script-only slice (the c2 members check) at any time. The corpus holds no effort figures, so this document gives an order, not durations.
