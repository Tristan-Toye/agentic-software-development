# Representations beyond UML class diagrams (Q1, Q3, Q5, Q9)

- **Design questions**: Q1 abstraction levels and clutter; Q3 code-to-feature mapping; Q5 sequence diagrams; Q9 approval.
- **Evidence note**: example links were checked for a 200 response on 2026-11-12. Claims about effects come from the search results named in each row. Rows without a study say "practitioner source" or "inference".
- Cross-links: [review-ux-and-feedback.md](review-ux-and-feedback.md), [current-state.md](current-state.md).

## 1. Why not UML class diagrams

The owner finds class and dependency diagrams cluttered. The data agrees in a general way: node-link diagrams of graphs with more than about 20 vertices lose to matrices on most tasks, except path finding ([Ghoniem et al. 2004, cited in a search summary of matrix vs node-link studies](https://oro.open.ac.uk/18627)). Another source reports that a node-link advantage shrinks at 50 nodes ([Ren et al., arXiv 1709.00293](https://arxiv.org/pdf/1709.00293.pdf), as summarised in the search result). Mermaid itself hits limits on large graphs: default edge caps, text-size errors and slow layout (vendor and forum sources: [websequencediagrams blog](https://www.websequencediagrams.com/blog/how-to-fix-maximum-text-size-diagram-exceeded-in-mermaidjs); [Obsidian forum on maxEdges](https://forum.obsidian.md/t/mermaid-allow-more-edges-over-500/85114/1)). In config-harness the c4 class files are the largest and most code-shaped diagrams (8 files; `CH:docs/architecture/c4-classes-*.md`, see [current-state.md](current-state.md) A1). They show names and types, not what a user can do.

For approval before a build, the owner needs behaviour and scope, not type structure. The representations below are ranked for that use in section 4.

## 2. Comparison by representation

Columns: what it shows best; clutter at scale; fit for approving a change before implementation (High, Medium, Low; this rating is my inference unless stated); example link.

| # | Representation | Shows best | Clutter at scale | Fit for pre-build approval | Example |
|---|---|---|---|---|---|
| 1 | **C4 levels** (context, container, component; code level optional) | Who and what exists, at four fixed zoom levels. The model is separate from the views. | Low at levels 1 and 2. Level 3 grows with module count: config-harness c3 files carry module graphs from `use` lines ([current-state.md](current-state.md) A1). Level 4 is the UML-like level and is optional in C4. | Medium. Good for "which parts does this touch". Weak for "what will the user see". | [c4model.com](https://c4model.com/); [LikeC4, diagrams as code with zoom levels](https://likec4.dev/) |
| 2 | **Feature or capability map** (tree or grid of product capabilities, each linked to code areas) | What the product can do, in the owner's words. Fits the feature book (H-01 to H-13). | Low: one box per feature, grouped. Grows with feature count, not code size. | High for scope. Shows "which features does this change add or alter". Needs a feature-to-code map, which config-harness lacks ([current-state.md](current-state.md) A4). | Practitioner pattern. Feature book entry: `CH:config-harness/features/harness/TRACEABILITY.md`. |
| 3 | **User-journey map and story map** (steps left to right, detail down) | What the user does, in order, and which steps a change touches. | Low to medium: width grows with steps. | High for product changes with a user-visible flow. Low for internal refactors. | [Story mapping (Patton)](https://www.jpattonassociates.com/user-story-mapping/); [NN/g journey mapping](https://www.nngroup.com/articles/customer-journey-mapping/) |
| 4 | **Event storming** (domain events, commands, aggregates on a timeline) | The business flow as events. Good for finding gaps in a flow. | Medium: sticky-note boards get large; made for workshops, not for generated reading. | Medium. Good vocabulary ("what happens"). A generated board is hard to read without a facilitator. | [eventstorming.com](https://www.eventstorming.com/); comparison with story mapping: practitioner source, [Cucumber blog](https://cucumber.io/blog/hiptest/how-to-use-example-mapping-event-storming) |
| 5 | **Domain storytelling** (actors, work objects and numbered activities in sentences) | One scenario as a readable story: "Actor does X with object, to actor". One scenario at a time. | Low: one scenario per picture. | High for a single new flow. Domain storytelling follows one scenario; event storming explores parallel ones (practitioner comparison in search results). | [domainstorytelling.org](https://domainstorytelling.org/); [Thoughtworks Radar](https://thoughtworks.com/radar/techniques/domain-storytelling) |
| 6 | **Data-flow diagram** (processes, stores, flows between them) | Where data comes from and goes to, and at what trust boundary. | Medium. Levelled DFDs (level 0, 1, 2) control it. | Medium. Strong for security and data changes (the config-harness threat model is of this kind: `CH:docs/architecture/threat-model-risk-gate.md`). | [Wikipedia: data-flow diagram](https://en.wikipedia.org/wiki/Data-flow_diagram) |
| 7 | **State machine / statechart** | Lifecycles: what states a thing has and what moves it. Good for tracks, runs, approvals. | Low with hierarchy (statecharts nest states). Flat state machines explode. | High when a change adds or alters a lifecycle (for example the track states claim, park, done). | [statecharts.dev](https://statecharts.dev/); [Mermaid state diagrams](https://mermaid.js.org/) |
| 8 | **Dependency structure matrix (DSM)** | Coupling and layering of many modules at once. Cycles show as marks above the diagonal. | Lowest clutter of the structural views. Matrices beat node-link for graphs over about 20 nodes on most tasks, but lose on path finding (see section 1). Hard for non-experts to read. | Low to medium for product approval. High for an architecture-health gate ("does the change add a cycle"). | [DSM web site](https://dsmweb.org/); [Wikipedia: DSM](https://en.wikipedia.org/wiki/Design_structure_matrix) |
| 9 | **Treemap and code city** (area = size, colour = a metric or a change) | Where code is and where change lands. A change heat map over the whole product. | Scales to large systems by design. Labels crowd at the leaf level. | Low for approving a design (it shows code, not behaviour). Medium as a "blast radius" overview. A controlled experiment with 41 participants found higher correctness and shorter time with CodeCity for comprehension tasks (structural understanding and design problem assessment) ([Wettel, Lanza, Robbes, ICSE 2011](https://www.inf.unibz.it/~rrobbes/p/ICSE2011-codecity.pdf), via search summary). | [Treemap history, UMD HCIL](https://www.cs.umd.edu/hcil/treemap-history/) |
| 10 | **Interaction or swimlane flow** (lanes per actor or component, steps across) | Who does what, in order, with hand-offs between lanes. | Medium: width grows with steps, height with lanes. A cap of about 5 lanes keeps it readable (inference). | High for flows that cross components. Similar to a UML sequence diagram, but lanes read better for non-experts (inference). IcePanel Flows is a step overlay on a C4 view ([IcePanel](https://icepanel.medium.com/comparison-icepanel-flows-vs-uml-sequence-diagrams-4b3b190051a0)). | [Mermaid sequence diagrams](https://mermaid.js.org/syntax/sequenceDiagram.html); config-harness uses 17 of them (`CH:docs/architecture/seq-*.md`). |
| 11 | **Narrative walkthrough** (scrollytelling or step-by-step animation of a flow) | One story, one step at a time, with the matching part of the diagram highlighted. | Lowest per screen: only the current step shows. Cost: the reader cannot see the whole at once. | High when paired with a static overview. Good for "walk me through the new flow". | [The Pudding: how to implement scrollytelling](https://pudding.cool/process/how-to-implement-scrollytelling/); IcePanel flows (above) |
| 12 | **Before and after flow comparison** | The change itself: same flow, two versions, differences marked. | Low if the layout is stable; see [review-ux-and-feedback.md](review-ux-and-feedback.md) section 3. | High. This is the representation that the owner asked for ("git diff of the diagram"). | GitHub image modes: [docs](https://docs.github.com/en/enterprise/2.17/user/github/managing-files-in-a-repository/rendering-and-diffing-images); IcePanel drafts: [versioning](https://docs.icepanel.io/advanced-features/versioning) |

## 3. Mapping to the abstraction levels (Q1)

A layered scheme that avoids class diagrams. Each level answers one question. The reader zooms down only on request.

| Level | Question | Representation | Elements per view (budget, inference) |
|---|---|---|---|
| L0 Product | What can the product do, and what does this change touch? | Feature map (row 2), changed features marked | up to 15 features |
| L1 Flow | How does a user or caller experience the change? | Journey or swimlane flow (rows 3, 10, 11), before and after (row 12) | up to 12 steps, 5 lanes |
| L2 Structure | Which containers and components take part? | C4 container and component (row 1), changed parts marked | up to 12 nodes |
| L3 Evidence | Which code backs this element? | `path:line` list per element, from the contract stubs | no diagram; a list |
| Lifecycle (side view) | Does a state change? | Statechart (row 7), only when a lifecycle changes | up to 8 states |
| Health (side view) | Does the change add coupling? | DSM (row 8) or a cycle count | one matrix |

The density budget above is a design proposal. A third-party tool uses a budget of 9 nodes and 12 edges for visual density ([diagram-design complexity budgets](https://instagit.com/cathrynlavery/diagram-design/what-are-the-complexity-budgets-for-different-diagram-types.md); a practitioner tool, not a study). Test the numbers with the owner.

## 4. Recommendation (USE / BUILD / SKIP)

**USE** three representations as the core for approving a product change before the fan-out:

1. **Before and after flow comparison (row 12), built on a swimlane or sequence flow (row 10).** It shows the change itself, and the owner asked for a "git diff" of the diagram. Keep layout stable, and give each step a stable ID for anchored comments. Config-harness already has 17 sequence diagrams with checked code anchors, which gives the "before" side for free ([current-state.md](current-state.md) A2).
2. **Feature map with changed features marked (row 2), over a C4 container view (row 1).** The feature map gives the owner the scope in product words. The C4 view gives the touched parts. Both need a feature-to-code table, which must be **BUILT** (config-harness has only partial links; [current-state.md](current-state.md) A4).
3. **Statechart (row 7), only when the change alters a lifecycle.** It is the most compact way to show "what states exist now". It adds little otherwise, so it is conditional.

Narrative walkthrough (row 11) is a **USE** as a mode of 1, not a separate diagram: step through the flow one message at a time with a verdict button per step.

**SKIP** UML class diagrams as an approval artefact. Keep config-harness's c4-classes files as reference and as code evidence at L3 only.

**SKIP** event storming boards and treemap/code city as generated approval artefacts: the first is a workshop tool, and the second shows size, not behaviour. A treemap change heat map may be a later **BUILD** for the living-map goal (Q7).

**BUILD** a DSM or cycle-count check as a mechanical gate, not as an owner view. Config-harness already checks crate edges with `scripts/check_dep_direction.sh` (named in `CH:docs/architecture/c2-containers.md:6-8`).

Self-host note: all of these can be produced as Mermaid or SVG text without a paid service. IcePanel and Structurizr cloud are paid and are not required ([Structurizr cloud review is paid](https://docs.structurizr.com/cloud/diagram-review)); Mermaid and D2 ([d2lang.com](https://d2lang.com/)) are open. Rendering to SVG on the server fits the dashboard rule of no external resources (`shared/questions/page.ts:1`).

## 5. Open points for the other agents

- Q8: how accurate an LLM is at producing a flow diagram from stubs, and a check that every drawn element has code evidence. The existing anchor checker is a model for that check (`CH:scripts/check_diagram_anchors.py`).
- Q3: how to derive the feature map from code. Not covered here.
- No controlled study was found that compares swimlane flows with UML sequence diagrams for approval tasks. Row 10 claims about readability are inference.
