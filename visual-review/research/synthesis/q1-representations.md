# Q1. Which representations, and when

- **Question**: Which diagram types does the agent draw for a product change, and when does it pick each one?
- **Status**: PROPOSED. The owner has not ruled.
- **Numbering note**: this volume numbers the design questions Q1 to Q9 as in [README.md](README.md). The older papers index uses a different Q list. See the mapping in the README.

## Facts

1. The owner finds UML cluttered. The class diagram is the most code-shaped and the largest diagram type in config-harness: 8 of its 32 diagram files are c4 class files ([current-state.md](current-state.md), A1).
2. Node-link diagrams with more than about 20 vertices lose to matrices on most tasks, except path finding. This claim comes from search summaries, not from the full studies ([representations-beyond-uml.md](representations-beyond-uml.md), section 1).
3. Config-harness has 16 sequence files (22 Mermaid `sequenceDiagram` blocks) with code anchors. (Corrected in the audit of 2026-11-12: an earlier version said 17.) The anchor script reports 748 of 748 anchors valid on 2026-11-12. These give a ready "before" flow for many features ([current-state.md](current-state.md), A1 and A2).
4. Config-harness diagrams are organised by crate and by code flow. Only 6 of 32 diagram files carry a feature ID. There is no feature-to-code map in either direction ([current-state.md](current-state.md), A4).
5. Understanding the change is the main review difficulty. Reviewers need the reason for a change ([modern-code-review-bacchelli-bird-2013](../papers/modern-code-review-bacchelli-bird-2013.md)). Reviewers first build context, then compare the expected solution with the actual one ([code-review-comprehension-strategies](../papers/code-review-comprehension-strategies.md)).
6. Reviewers group by chunk. Feature groups are natural chunks ([code-review-comprehension-strategies](../papers/code-review-comprehension-strategies.md)). File order by name is disliked: only 10.2 percent of 1,355 surveyed developers think it is optimal. This is perception data, not a controlled test ([file-ordering-code-review](../papers/file-ordering-code-review.md)).
7. A vision model reads relations in rendered diagrams poorly. The best of 11 models scored 70.18 percent on the SADU benchmark. Behavioural diagrams are the hardest ([vlm-architecture-diagram-understanding](../papers/vlm-architecture-diagram-understanding.md)). The record must be structured data, not only a picture.

## Representation choice

The synthesis input ranks 12 representations for pre-build approval. The rating is the input author's inference unless the input says otherwise ([representations-beyond-uml.md](representations-beyond-uml.md), section 2). The result is a layered scheme. Each level answers one question. The reader goes down only on request.

| Level | Question the owner asks | Representation | Budget (inference) |
|---|---|---|---|
| L0 Product | What can the product do, and what does this change touch? | Feature map, changed features marked | up to 15 features |
| L1 Flow | How does the user or caller experience the change? | Flow with lanes (swimlane or sequence), before and after | up to 12 steps, 5 lanes |
| L2 Structure | Which containers and components take part? | C4 container or component view, changed parts marked | up to 12 nodes |
| L3 Evidence | Which code backs this element? | List of `path:line`, no diagram | n/a |
| Side: lifecycle | Does a state change? | Statechart | up to 8 states |
| Side: health | Does the change add coupling? | Cycle count or matrix, as a mechanical gate | one number |

Source of the table: [representations-beyond-uml.md](representations-beyond-uml.md), section 3. The budgets are a proposal. A practitioner tool uses 9 nodes and 12 edges as a density budget. That source is not a study. Test the numbers with the owner.

## When to draw each

| Change type | Draw | Reason |
|---|---|---|
| Any change | Feature map (L0), changed features marked | Gives scope in product words. Grows with feature count, not code size ([representations-beyond-uml.md](representations-beyond-uml.md), row 2). |
| Behaviour crosses 2 or more components, or the order of steps changes | Flow (L1), before and after | This is the "git diff of the diagram" the owner asked for ([representations-beyond-uml.md](representations-beyond-uml.md), row 12). |
| A thing gains, loses or renames a state (for example a track: claim, park, done) | Statechart | Most compact for lifecycles. Adds little otherwise ([representations-beyond-uml.md](representations-beyond-uml.md), row 7). |
| New or removed crate, or new dependency edge between crates | C4 container (L2), with a cycle check | Cheap: `cargo metadata` gives it ([rust-extraction](../sources/rust-extraction.md)). |
| Internal refactor with no product effect | Feature map only, with a "no product change" mark | Inference. A flow would show nothing new. |

Rule for a new element on a diagram: it needs a one-line description in product words and a list of `path:line` evidence. See [q3-extraction-from-rust.md](q3-extraction-from-rust.md).

## Walkthrough mode

Walkthrough is a mode of the flow, not a separate diagram. The owner steps through one message at a time. Each step has a verdict button ([representations-beyond-uml.md](representations-beyond-uml.md), row 11 and section 4). IcePanel Flows is the product model for this. IcePanel is paid, so the idea is reusable but the tool is not ([icepanel-ilograph-terrastruct](../sources/icepanel-ilograph-terrastruct.md)).

## What not to draw

- UML class diagrams as an approval artefact. Keep config-harness c4 class files as L3 code evidence only ([representations-beyond-uml.md](representations-beyond-uml.md), section 4).
- Event storming boards. They are a workshop tool ([representations-beyond-uml.md](representations-beyond-uml.md), row 4).
- Treemap or code city as an approval view. It shows size, not behaviour. A change heat map may be a later addition for the living map ([representations-beyond-uml.md](representations-beyond-uml.md), row 9; [codecity-vissoft-2007](../papers/codecity-vissoft-2007.md)).
- A 3D view. The CodeCity authors say 3D has navigation problems and 2D is proven ([codecity-vissoft-2007](../papers/codecity-vissoft-2007.md)).

## Caveats

- No controlled study was found that compares swimlane flows with UML sequence diagrams for approval tasks. Readability claims for lanes are inference ([representations-beyond-uml.md](representations-beyond-uml.md), section 5).
- A feature map needs a feature-to-code table. Config-harness lacks it. It must be built ([current-state.md](current-state.md), A4). See [q8-staleness-and-living-map.md](q8-staleness-and-living-map.md).
- C4 vocabulary (context, container, component, code, dynamic view) is the shared language for levels ([structurizr](../sources/structurizr.md)).

## Recommendation

**USE** three representations as the core: the feature map with changed features marked (L0), the before and after flow with lanes (L1), and the C4 container view for structure (L2).

**USE** a statechart only when the change alters a lifecycle.

**USE** walkthrough as a mode of the flow.

**SKIP** UML class diagrams, event storming boards and treemap or 3D city views as approval artefacts.

**BUILD** the feature-to-code table that L0 needs. No tool in the corpus provides it for Rust.

## Sources

- Synthesis input: [representations-beyond-uml.md](representations-beyond-uml.md), [current-state.md](current-state.md), [review-ux-and-feedback.md](review-ux-and-feedback.md).
- Papers: [modern-code-review-bacchelli-bird-2013](../papers/modern-code-review-bacchelli-bird-2013.md), [code-review-comprehension-strategies](../papers/code-review-comprehension-strategies.md), [file-ordering-code-review](../papers/file-ordering-code-review.md), [vlm-architecture-diagram-understanding](../papers/vlm-architecture-diagram-understanding.md), [codecity-vissoft-2007](../papers/codecity-vissoft-2007.md).
- Sources: [structurizr](../sources/structurizr.md), [rust-extraction](../sources/rust-extraction.md), [icepanel-ilograph-terrastruct](../sources/icepanel-ilograph-terrastruct.md).
