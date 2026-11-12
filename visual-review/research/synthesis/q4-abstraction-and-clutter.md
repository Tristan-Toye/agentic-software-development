# Q4. Abstraction and clutter control

- **Question**: How does the diagram stay readable for the owner, who finds UML cluttered?
- **Status**: PROPOSED.

## Facts

1. **Grouping.** Compound (nested) graphs let a node hold children. ELK lays out the nesting in one run with `hierarchyHandling: INCLUDE_CHILDREN` ([elk-elkjs](../sources/elk-elkjs.md)). Views are predicates over one model: each view chooses which elements to show ([likec4 analysis](../../../inspiration-repos/analysis/likec4.md)).
2. **Change scope.** An OpenArc graph covers the change plus one hop of context, not the whole codebase ([openarc analysis](../../../inspiration-repos/analysis/openarc.md)). mermaidiff hides unchanged steps and caps the diagram at 12 steps ([mermaidiff analysis](../../../inspiration-repos/analysis/mermaidiff.md)).
3. **Filters.** cargo-modules flags map to levels: `--no-fns`, `--no-traits`, `--no-types`, `--focus-on <path>`, `--max-depth <n>`, `--cfg-test` for test code ([cargo-modules analysis](../../../inspiration-repos/analysis/cargo-modules.md)).
4. **Lifeline limit.** The summarised-sequence-diagram paper groups objects into domain concepts to cut lifelines and does not hide messages. It reports F-score 0.670 and recall 0.793 "with fewer than 30 lifelines", against 0.421 and 0.670 for the earlier method ([summarized-sequence-diagram-objects](../papers/summarized-sequence-diagram-objects.md)). **Caveat:** the paper states this as the range of its evaluation. The corpus paper doc reads it as a size limit. The paper does not say that 30 is a readable maximum for a human. For the owner, a budget of 5 lanes for a flow is a proposal (inference) in [representations-beyond-uml.md](representations-beyond-uml.md), section 3.
5. **Trace compression.** Repeated call sequences dominate traces. One trace has over 100,000 lines and only 65 distinct methods. The paper removes utilities and collapses repeated subtrees ([trace-complexity-reduction-hamou-lhadj-2003](../papers/trace-complexity-reduction-hamou-lhadj-2003.md)).
6. **Edge bundling.** One parameter beta in [0,1] sets bundling strength. High strength shows parent-to-parent links. Low strength shows leaf-to-leaf links. The user can raise the strength, select a bundle, then lower it ([hierarchical-edge-bundles-holten-2006](../papers/hierarchical-edge-bundles-holten-2006.md)). The paper uses radial and other tree layouts. The paper doc does not show that bundling works with ELK layered layout.
7. **Semantic zoom.** The representation changes with zoom level. A mini-map keeps context. Two user studies found both useful, most of all for large landscapes and shared exploration ([semantic-zoom-minimaps-software-cities](../papers/semantic-zoom-minimaps-software-cities.md)). The paper is about 3D cities in ExplorViz, not 2D diagrams.
8. **Matrix versus node-link.** Matrices beat node-link diagrams for graphs above about 20 nodes on most tasks, except path finding. Source: search summaries ([representations-beyond-uml.md](representations-beyond-uml.md), section 1).
9. **Too much in one view is a named limit** of the software-city review prototype. Filtering is the minimum ([software-city-code-review](../papers/software-city-code-review.md)).
10. **Generation-time pruning.** ArchAgent prunes context to keep diagrams within limits. Partial diagrams are merged afterwards ([archagent-legacy-recovery](../papers/archagent-legacy-recovery.md)).
11. **Metric on nodes.** CodeCharta puts a metric (size, churn) on nodes as colour or size ([code-visualisation-legacy-tools](../sources/code-visualisation-legacy-tools.md)).
12. **Size gate before the owner.** The C4 checklist (title, legend, scope, element names, level of abstraction) can run mechanically. A diagram that fails does not go to the owner ([review-ux-and-feedback.md](review-ux-and-feedback.md), sections 2 and 6).
13. **Drill-down UX parts.** Breadcrumb, search, path finder and export exist in Understand-Anything ([understand-anything analysis](../../../inspiration-repos/analysis/understand-anything.md)). Its default whole-codebase graph is the cluttered view the owner dislikes.

## Proposed controls

| Control | How | Default value (inference unless cited) |
|---|---|---|
| Group | Nest items: crate, module, item. Draw a group as one box until the owner opens it. | Open to module level at most. |
| Scope | Show changed items plus one hop. Hide the rest. | One hop. |
| Budget | Fail the build when a view exceeds the budget. | L0 15 features, L1 12 steps and 5 lanes, L2 12 nodes ([representations-beyond-uml.md](representations-beyond-uml.md), section 3). |
| Collapse | Merge repeated step runs into one counted step. Drop utility calls (logging, conversion). | Always on in flows. |
| Lane grouping | Put several objects of one concept on one lane. | Group by crate, then by feature. |
| Bundle | One strength slider moves edges between module level and item level. A bundle shows its count of changed edges. | Strength high. |
| Semantic zoom | Zoom in on a box shows its sub-diagram, not a bigger box. | Three levels: feature, flow or container, item. |
| Mini-map | A small overview stays visible while zoomed. | On for graphs above one screen. |
| Test code | Exclude test code from product diagrams. | Excluded. Source: `--cfg-test` switch in [cargo-modules analysis](../../../inspiration-repos/analysis/cargo-modules.md). |
| Order | List changed elements by feature or flow, not by file name. | By flow. Source: [file-ordering-code-review](../papers/file-ordering-code-review.md). |

## Caveats

- The budget numbers are not validated. Test them with the owner on three real changes ([representations-beyond-uml.md](representations-beyond-uml.md), section 3).
- No benchmark ran for ELK speed on 200 nodes. Run one in the spike ([browser-layout-engines](../sources/browser-layout-engines.md)).
- Bundling and semantic zoom evidence comes from tree layouts and 3D cities. Transfer to a 2D layered layout is inference.
- Self-reported preference data on file order is design input, not proof ([file-ordering-code-review](../papers/file-ordering-code-review.md)).

## Recommendation

**USE** grouping by compound nodes (ELK), one-hop change scope, a hard size budget with a mechanical gate, run collapse and utility removal in flows, and a mini-map.

**USE** one bundling-strength control and semantic zoom in the first dashboard build only if the spike shows they fit ELK output. Otherwise ship group open and close first.

**BUILD** the budget gate as part of the validator, so the owner never sees an over-budget diagram.

**SKIP** a default whole-codebase graph and item-level graphs as the default view.

## Sources

- Papers: [summarized-sequence-diagram-objects](../papers/summarized-sequence-diagram-objects.md), [trace-complexity-reduction-hamou-lhadj-2003](../papers/trace-complexity-reduction-hamou-lhadj-2003.md), [hierarchical-edge-bundles-holten-2006](../papers/hierarchical-edge-bundles-holten-2006.md), [semantic-zoom-minimaps-software-cities](../papers/semantic-zoom-minimaps-software-cities.md), [software-city-code-review](../papers/software-city-code-review.md), [archagent-legacy-recovery](../papers/archagent-legacy-recovery.md), [file-ordering-code-review](../papers/file-ordering-code-review.md).
- Sources: [elk-elkjs](../sources/elk-elkjs.md), [browser-layout-engines](../sources/browser-layout-engines.md), [code-visualisation-legacy-tools](../sources/code-visualisation-legacy-tools.md).
- Analyses: [likec4](../../../inspiration-repos/analysis/likec4.md), [openarc](../../../inspiration-repos/analysis/openarc.md), [mermaidiff](../../../inspiration-repos/analysis/mermaidiff.md), [cargo-modules](../../../inspiration-repos/analysis/cargo-modules.md), [understand-anything](../../../inspiration-repos/analysis/understand-anything.md).
- Synthesis input: [representations-beyond-uml.md](representations-beyond-uml.md), [review-ux-and-feedback.md](review-ux-and-feedback.md).
