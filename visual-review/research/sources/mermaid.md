# Mermaid

- **Type**: tool (text-to-diagram library)
- **Repo / site**: https://github.com/mermaid-js/mermaid , https://mermaid.js.org , https://github.com/mermaid-js/mermaid-live-editor
- **License / cost**: MIT (GitHub API). Free.
- **Self-host**: yes. Runs in the browser or in Node through `mermaid-cli` (https://github.com/mermaid-js/mermaid-cli).

## Capabilities
- Many diagram types: flowchart, sequence, class, state, ER, C4, architecture, and more (https://mermaid.js.org/syntax/architecture.html menu).
- Sequence diagrams: participants, messages, `alt`/`opt`/`loop`/`par` blocks, notes (https://mermaid.js.org/syntax/sequenceDiagram.html).
- `architecture-beta`: groups, services, edges, junctions; the page says the syntax "may still change in a backwards-incompatible way" (https://mermaid.js.org/syntax/architecture.html).
- C4 diagrams: present in the menu; the page marks them experimental (https://mermaid.js.org/syntax/c4.html; the exact wording was not extracted in this session).
- Layout: ELK is the default layout in v12 and is bundled; Dagre is selectable with `config.layout: dagre` (Context7, `/mermaid-js/mermaid`, `docs/syntax/flowchart.md`).
- Nested subgraphs work in flowcharts with ELK (Context7 example, `e2e/platform/knsv2.html`).
- Interaction: click callbacks and links on nodes. No built-in expand and collapse, no diff.
- LLMs write Mermaid well, so it is the common output of agent tools: `inspiration-repos/mermaidiff/`, GitDiagram, Swark.

## Development resources
- https://mermaid.js.org/intro/ : docs.
- https://mermaid.live : live editor (source: mermaid-live-editor repo).
- `inspiration-repos/gitdiagram/src/server/generate/mermaid-validator.ts` : how to validate model-written Mermaid.

## Alternatives
- PlantUML: [plantuml.md](plantuml.md). D2: [d2.md](d2.md). LikeC4: [likec4.md](likec4.md).

## Comparison
| Option | Diagram types | Browser render | Abstraction levels | Diff support |
|---|---|---|---|---|
| Mermaid | many | yes | none built in | none; colour classes by hand (mermaidiff, likec4 diff action) |
| PlantUML | many | server or WASM | none | none |
| D2 | one language | SVG output | layers | none |

## Recommendation
**USE** Mermaid as the secondary renderer for sequence diagrams (Q5) and as the export format for PR comments and Markdown, because GitHub renders it and LLMs know it. **SKIP** it as the main architecture renderer: it has no levels, no drill-down and no diff (Q1, Q4, Q6). Keep one internal model and compile to Mermaid, as GitDiagram does ([`gitdiagram.md`](../../../inspiration-repos/analysis/gitdiagram.md)).
