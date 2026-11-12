# Gaps in the research

This file lists what is thin, unverified or contradicted. Treat each item as a caveat on any claim that depends on it.

## Thin literature

- **Diagram diffing.** Few papers cover diffs of architecture diagrams. The diff model rests on tool analyses (OpenArc, LikeC4, mermaidiff) and on reflexion models ([Q2](../visual-review/research/synthesis/q2-diff-model.md)). No paper tests layout stability across a diff.
- **LLM sequence diagrams.** Few papers cover LLM-generated sequence diagrams from code. The flow design rests on OpenArc and LikeC4 ([Q5](../visual-review/research/synthesis/q5-rendering-stack.md)).
- **Review outcomes.** No evidence was found that diagram review catches more defects ([Q9](../visual-review/research/synthesis/q9-measuring-value.md)).

## Papers not downloaded

- **Garcia et al., ASE 2013** (architecture recovery comparison). The PDF was not downloaded. Claims about it are second-hand.
- **CodeCity, ICSE 2011** (controlled experiment, 41 participants). The PDF was not downloaded. The result is second-hand ([papers README](../visual-review/research/papers/README.md)).

## Unverified

- **Licences.** The licences of d3-hierarchy (ISC) and Graphviz WASM were not verified ([Q5](../visual-review/research/synthesis/q5-rendering-stack.md)). Check each before use.
- **CodeSee shutdown.** The owner reports that CodeSee shut down. No primary source confirms it ([code-visualisation-legacy-tools](../visual-review/research/sources/code-visualisation-legacy-tools.md)).
- **elkjs on Bun.** The corpus claims it, and it has not been tested ([Q5](../visual-review/research/synthesis/q5-rendering-stack.md)).
- **Layout stability.** How far unchanged nodes move between before and after is not measured ([Q2](../visual-review/research/synthesis/q2-diff-model.md)).
- **First-run cost** of a whole-repo product map. No measurement ([Q8](../visual-review/research/synthesis/q8-staleness-and-living-map.md)).
- **Link checks.** Only some URLs were checked for a 200 response ([LINKS.md](LINKS.md)).

## Thin source documents

These source notes are short. Do not build a decision on them alone.

- [llm-repo-explainers](../visual-review/research/sources/llm-repo-explainers.md)
- [change-diagram-skills](../visual-review/research/sources/change-diagram-skills.md)
- [code-graph-mcp-servers](../visual-review/research/sources/code-graph-mcp-servers.md)
- [trace-to-sequence-and-sequence-generators](../visual-review/research/sources/trace-to-sequence-and-sequence-generators.md)
- [architecture-rule-checkers](../visual-review/research/sources/architecture-rule-checkers.md)
- [code-visualisation-legacy-tools](../visual-review/research/sources/code-visualisation-legacy-tools.md)

## Contradictions found in inputs

1. **Tool URLs (resolved).** One source document gave wrong URLs for OpenArc and mermaidiff. They are corrected to the URLs of the cloned repos: github.com/hvess/openarc and github.com/osmangoninahid/mermaidiff ([LINKS.md](LINKS.md)).
2. **Mermaid default layout.** The ELK source says Mermaid v12 uses ELK by default. The layout-engines source says Dagre was the old default. I could not verify which is current. Do not rely on either ([Q5](../visual-review/research/synthesis/q5-rendering-stack.md)).
3. **The "fewer than 30 lifelines" reading.** An input gives a lifeline limit for sequence diagrams. Its reading differs between documents. Treat the number as unverified and use the flow cap of 12 steps in [Q4](../visual-review/research/synthesis/q4-abstraction-and-clutter.md) as a proposal.
4. **Status vocabulary.** OpenArc uses `added | changed | removed | context`. Reflexion models use `converge | diverge | absent`. architecture-check uses `verified_static_fact | hypothesis | unknown`. These are three different axes (change, conformance, evidence). The synthesis keeps them apart ([Q2](../visual-review/research/synthesis/q2-diff-model.md)).
5. **Old versus new question numbers.** The papers README uses a different Q list ([synthesis README](../visual-review/research/synthesis/README.md)).
6. **Stale diagrams in config-harness.** The c2 text says six members. The workspace has 10 ([Q8](../visual-review/research/synthesis/q8-staleness-and-living-map.md)). This is a finding about the target repo, not a source error.
