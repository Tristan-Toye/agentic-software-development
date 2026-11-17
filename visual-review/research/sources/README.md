# Sources index: tools, libraries and open-source repos

22 source docs. Licences marked "check" were not re-verified; read the doc before use. Deep analyses of 10 cloned repos are in `inspiration-repos/analysis/`.

| Source | Type | License / self-host | Recommendation | One-line why |
|---|---|---|---|---|
| [likec4](likec4.md) | tool | MIT (check doc) / yes | USE ideas, BUILD own | as-is, changes, to-be views and status vocabulary |
| [structurizr](structurizr.md) | tool | see doc / yes | USE vocabulary, SKIP dependency | C4 terms are the shared language |
| [icepanel-ilograph-terrastruct](icepanel-ilograph-terrastruct.md) | paid tools | commercial / no | SKIP | hosted and paid, no core use |
| [d2](d2.md) | tool | MPL-2.0 (check) / yes | SKIP (optional export) | text-first, no diff or interactivity |
| [mermaid](mermaid.md) | library | MIT / yes | USE secondary | sequence diagrams and exports |
| [plantuml](plantuml.md) | tool | see doc / yes | SKIP | Java runtime, weak interactivity |
| [elk-elkjs](elk-elkjs.md) | library | EPL-2.0 / yes | USE default layout | nested layered layout; run on the Bun server |
| [browser-layout-engines](browser-layout-engines.md) | libraries | mixed / yes | USE ELK, d3-hierarchy, Dagre fallback | covers each layout need; skip Graphviz WASM for canvas |
| [cytoscape-js](cytoscape-js.md) | library | MIT / yes | SKIP first choice, fallback | graph-analysis oriented, heavy for our DOM UI |
| [xyflow-react-flow](xyflow-react-flow.md) | library | MIT / yes | BUILD canvas, USE `@xyflow/system` | pan, zoom, drag without React |
| [diagramming-frameworks](diagramming-frameworks.md) | libraries | mixed / yes | USE Sprotty as reference, SKIP GoJS | GoJS paid, Sprotty a model-driven reference |
| [rust-extraction](rust-extraction.md) | tools | mixed / yes | BUILD layered extractor | cargo metadata, cargo-modules, rustdoc JSON, rust-analyzer |
| [semantic-code-graph-engines](semantic-code-graph-engines.md) | engines | mixed / yes | SKIP core, evaluate SCIP | too heavy; SCIP maybe for references |
| [llm-repo-explainers](llm-repo-explainers.md) | tools | MIT, Swark AGPL / mixed | SKIP, USE typed-graph pattern | GitDiagram validate-and-repair loop |
| [understand-anything](understand-anything.md) | tool | MIT / yes | BUILD, USE design | fingerprints, staleness, diff overlay |
| [codeboarding](codeboarding.md) | tool | MIT / partial | BUILD, USE pipeline | static facts then LLM naming, incremental |
| [change-diagram-skills](change-diagram-skills.md) | tools | check / yes | USE OpenArc model, mermaidiff | direct precedents for change views |
| [code-graph-mcp-servers](code-graph-mcp-servers.md) | tools | check / yes | SKIP, spike crabmap | agent queries, not pictures |
| [code-visualisation-legacy-tools](code-visualisation-legacy-tools.md) | tools | GPL/BSD/MIT / mixed | SKIP, USE two ideas | no Rust, no diff; CodeSee shutdown unverified |
| [architecture-rule-checkers](architecture-rule-checkers.md) | tools | MIT/Apache/GPL / yes | SKIP, USE evidence status | drift detection idea |
| [trace-to-sequence-and-sequence-generators](trace-to-sequence-and-sequence-generators.md) | tools | mixed / mixed | BUILD static, OTel later | rust-analyzer call hierarchy |
| [diagram-diff-approaches](diagram-diff-approaches.md) | patterns | n/a | BUILD structured graph diff | one merged graph, one layout |
