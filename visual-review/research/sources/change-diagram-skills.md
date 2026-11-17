# Change-diagram tools: OpenArc, mermaidiff, diff-glance, ArchiLens, LikeC4 diff action, Visual-Explainer

- **Type**: tool (group; diagrams of a code change)
- **Repo / site**: https://github.com/hvess/openarc (see `inspiration-repos/openarc`), https://github.com/osmangoninahid/mermaidiff (see `inspiration-repos/mermaidiff`), diff-glance, https://github.com/saurabh-oss/archilens , likec4-architecture-diff-action, https://github.com/nicobailon/visual-explainer (see `inspiration-repos/visual-explainer`)
- **License / cost**: see each analysis doc; diff-glance and the LikeC4 action licences were not re-verified in this pass; Visual-Explainer is MIT (verified from its LICENSE, 2026-11-13). Free to run besides LLM cost.
- **Self-host**: yes (CLI, Action or skill).

## Capabilities
- **OpenArc**: change graph with node status `added|changed|removed|context`, edge resolution, typed LLM output, repair loop. Analysis: [`../../../inspiration-repos/analysis/openarc.md`](../../../inspiration-repos/analysis/openarc.md).
- **mermaidiff**: diff of two Mermaid sequence diagrams. Analysis: [`../../../inspiration-repos/analysis/mermaidiff.md`](../../../inspiration-repos/analysis/mermaidiff.md).
- **diff-glance**: turns a PR diff into a quick visual summary (README fetched earlier; details not re-read).
- **ArchiLens**: architecture diff on PRs, layers L0 to L3 ([architecture-rule-checkers.md](architecture-rule-checkers.md)).
- **LikeC4 diff action**: GitHub Action that draws LikeC4 views with changes between two revisions ([likec4.md](likec4.md)).
- **Visual-Explainer** (MIT, v0.15.0): a general agent skill that writes hand-drawn SVG explainer pages (diff review, plan review, state machines, slides) and checks them in a headless browser. No change graph, ids or layout engine. Little to reuse; three small ideas recorded as FU-02 to FU-04 in `references/visual-review/FOLLOW-UPS.md`. Analysis: [`../../../inspiration-repos/analysis/visual-explainer.md`](../../../inspiration-repos/analysis/visual-explainer.md).

## Development resources
- [diagram-diff-approaches.md](diagram-diff-approaches.md) : how each draws the diff.

## Alternatives
- Whole-repo explainers: [llm-repo-explainers.md](llm-repo-explainers.md).

## Comparison
| Tool | Level | Diff source | Sequence | Status colours | Reuse for us |
|---|---|---|---|---|---|
| OpenArc | architecture | git diff to graph | no | yes | data model |
| mermaidiff | sequence | two diagrams | yes | yes | sequence diff algorithm |
| diff-glance | file | git diff | no | not checked | UX idea |
| ArchiLens | layers | tree-sitter diff | no | yes | drill-down |
| LikeC4 action | model views | two revisions | no | yes | as-is, changes, to-be |

## Recommendation
**USE** OpenArc's change-graph model and mermaidiff's sequence diff approach; **BUILD** the rest. Do not depend on any of them: they are young and single-language.
