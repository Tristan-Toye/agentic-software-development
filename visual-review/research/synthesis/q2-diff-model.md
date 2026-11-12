# Q2. The diff model

- **Question**: How does the diagram show "before" and "after", and which words mark each node and edge?
- **Status**: PROPOSED.

## Facts

1. **Reflexion models** compare an intended model with the code. Edges are convergent (both agree), divergent (code has an edge the model lacks) or absent (model has an edge the code lacks). The model may be inaccurate on purpose, and the divergence is the information ([reflexion-models-murphy-1995](../papers/reflexion-models-murphy-1995.md)). The comparison needs a human-given or stored mapping from model boxes to code. Reflexion models compare model with code. A change diff compares two versions. The shapes match, but the two jobs differ.
2. **OpenArc** marks every component, member and edge `added | changed | removed | context`. A component status is the summary of its members. Edges carry `resolution`: `static`, `dispatch` or `inferred`. The graph covers the change plus one hop of context, not the whole codebase ([openarc](../../../inspiration-repos/analysis/openarc.md)).
3. **LikeC4** has a `diff` block for a planned change. A compare view has three modes: `as-is`, `changes` (both states, marked `added | removed | modified`) and `to-be`. It returns counts of changed nodes and edges. It validates a diff against the model: a removed element must exist, an added element must not ([likec4 analysis](../../../inspiration-repos/analysis/likec4.md)).
4. **CodeBoarding** uses five change statuses: added, modified, deleted, unchanged, renamed ([codeboarding analysis](../../../inspiration-repos/analysis/codeboarding.md)).
5. **mermaidiff** colours a sequence diff `new | changed | removed`, hides unchanged steps and caps the diagram at 12 steps. Each step has an evidence tier: green seen in a test or trace, blue backed by code, yellow inferred. A tier never moves up ([mermaidiff analysis](../../../inspiration-repos/analysis/mermaidiff.md)). mermaidiff runs in plan mode too, where the "after" side is always inferred.
6. **architecture-check** uses `verified_static_fact | hypothesis | unknown` and never reports "absent" for runtime calls ([architecture-rule-checkers](../sources/architecture-rule-checkers.md)).
7. **Layout stability is unsolved.** The survey says: a diff needs the same node in the same place in both states. ELK with fixed positions for unchanged nodes is the open question, and no tool surveyed solves it ([diagram-diff-approaches](../sources/diagram-diff-approaches.md)). ELK `considerModelOrder` keeps node order stable between runs ([elkjs analysis](../../../inspiration-repos/analysis/elkjs.md)). Caveat from the audit: OpenArc turns this option off (`NONE`) above a node-count threshold (`largeGraph`) to save time (`inspiration-repos/openarc/viewer/src/place.ts:36-43`). Stability is therefore not shown for large graphs.
8. **Patterns for a visual diff.** One merged diagram with colour (superposition plus explicit encoding), side by side (juxtaposition), overlay with slider, animation, a change list beside the diagram, impact highlight. Colour alone fails for colour-blind users, so add a second channel. Overlay needs a stable layout. Animation is easy to miss ([review-ux-and-feedback.md](review-ux-and-feedback.md), section 3). The comparison design space comes from Gleicher; the review of 127 papers is from a search summary, not the full paper.
9. **GitHub shows no structural diff of a diagram.** A search for a Mermaid diff in PRs found nothing ([review-ux-and-feedback.md](review-ux-and-feedback.md), section 2). The diff must be built.
10. Evolution storyboards step through structural change in panels. The paper gives a step-by-step reading guideline (p5). That a diff needs a legend with fixed meanings is our inference from it; the paper has no legend ([evolution-storyboards-beyer-hassan-2006](../papers/evolution-storyboards-beyer-hassan-2006.md)).
11. No paper on diagram diffing was found. See [../../../research/gaps.md](../../../research/gaps.md).

## The proposed model

**One merged change graph, one layout, three display modes.**

- Store one graph. Each node and edge has `status`: `added | changed | removed | context`. This is the OpenArc word set. LikeC4 says `modified` and CodeBoarding says `modified` or `deleted`. Pick `changed` and `removed` and map the others on import. (Inference: the corpus does not say which word set to pick.)
- Compute layout once, on the union of before and after nodes. Unchanged nodes keep their position in all modes. This is the "same node, same place" rule.
- Modes (LikeC4 names):
  - `as-is`: nodes and edges with status `changed`, `removed` or `context`, in their before state.
  - `changes`: everything, with status marks. Removed items stay visible.
  - `to-be`: nodes and edges with status `added`, `changed` or `context`.
- Marks use two channels: colour plus a second channel (outline style, icon, or a "NEW" label). Source: [review-ux-and-feedback.md](review-ux-and-feedback.md), section 3.
- A change list sits beside the diagram: "3 added, 1 changed, 2 removed" with links both ways ([review-ux-and-feedback.md](review-ux-and-feedback.md), section 3).
- Impact highlight: one hop from a changed element, matching the one-hop blast radius that the pipeline computes ([review-ux-and-feedback.md](review-ux-and-feedback.md), section 3).
- The status of a parent node summarises its members (OpenArc rule).

**Two fields mark how sure the claim is.** Both are proposed to be kept:

| Field | Values | Source |
|---|---|---|
| `resolution` (edges) | `static`, `dispatch`, `inferred` | [openarc analysis](../../../inspiration-repos/analysis/openarc.md) |
| `evidence` (nodes and edges) | `verified_static_fact`, `hypothesis`, `unknown`; plus a tier for runtime proof | [architecture-rule-checkers](../sources/architecture-rule-checkers.md), mermaidiff tiers |

The architecture-rule-checkers source recommends merging the two. The exact merged set is a design task for the build.

**Two uses of reflexion words.** Keep them separate from `status`.

1. Plan versus code, after the build: the planned "to-be" graph is the model, and the graph extracted from the merged code is the source model. Edges are `converge`, `diverge` or `absent`. This is a candidate check at the end of `/work-on` (inference, not in the inputs). It detects when the build did not follow the plan.
2. Declared map versus derived map for staleness: see [q8-staleness-and-living-map.md](q8-staleness-and-living-map.md).

**At approval time the "after" side is a plan.** The stubs compile but have no bodies. Every "after" edge that no stub signature proves has `resolution: inferred` and `evidence: hypothesis`. This follows the mermaidiff plan-mode rule. (Inference about stubs: the stub-materialiser copies signatures and uses placeholder bodies; see [current-state.md](current-state.md), B1.)

## Open points

- No tool solves layout stability. Spike: ELK with model order on, then measure how far unchanged nodes move between before and after. See [q5-rendering-stack.md](q5-rendering-stack.md).
- Rename handling. CodeBoarding has `renamed`; OpenArc has no such status. A rename shows as one removed and one added node unless the extractor matches them. Not solved in the inputs.
- The word set `changed` versus `modified` is a naming choice for the owner. See [../../../research/OPEN-QUESTIONS.md](../../../research/OPEN-QUESTIONS.md).

## Recommendation

**BUILD** one merged change graph with `status: added | changed | removed | context`, one layout over the union, and three modes `as-is`, `changes`, `to-be`.

**USE** the OpenArc status and `resolution` words, the LikeC4 mode names and its validation rule (removed must exist, added must not exist).

**USE** the reflexion words `converge | diverge | absent` only for the plan-versus-code check and the declared-versus-derived drift check, not for the node status.

**USE** two mark channels, and mark every plan-side element `inferred` until code proves it.

**SKIP** a pure Mermaid colour-class diff and an animated diff as the main view. They lose layout stability and are easy to miss.

## Sources

- Papers: [reflexion-models-murphy-1995](../papers/reflexion-models-murphy-1995.md), [evolution-storyboards-beyer-hassan-2006](../papers/evolution-storyboards-beyer-hassan-2006.md).
- Analyses: [openarc](../../../inspiration-repos/analysis/openarc.md), [likec4](../../../inspiration-repos/analysis/likec4.md), [mermaidiff](../../../inspiration-repos/analysis/mermaidiff.md), [codeboarding](../../../inspiration-repos/analysis/codeboarding.md), [elkjs](../../../inspiration-repos/analysis/elkjs.md).
- Sources: [diagram-diff-approaches](../sources/diagram-diff-approaches.md), [change-diagram-skills](../sources/change-diagram-skills.md), [architecture-rule-checkers](../sources/architecture-rule-checkers.md), [likec4](../sources/likec4.md).
- Synthesis input: [review-ux-and-feedback.md](review-ux-and-feedback.md), [current-state.md](current-state.md).
