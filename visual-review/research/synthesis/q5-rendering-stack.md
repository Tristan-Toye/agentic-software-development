# Q5. Rendering stack on the dashboard

- **Question**: How does the dashboard draw an interactive diagram when its page loads no external resources?
- **Status**: PROPOSED.

## Facts: the dashboard constraints

- The dashboard page is one inline HTML, JS and CSS string with "no external resources". Dynamic text goes through `textContent`. The only `innerHTML` is the server's escaped Markdown render of a dossier (`shared/questions/page.ts:1-3`, from [current-state.md](current-state.md), B2).
- The server is Bun and TypeScript. The product summary is Markdown only and has no diagram support. The run-detail API returns one JSON object; a `diagrams` field would sit next to `summary` and `blocks` ([current-state.md](current-state.md), B2).
- The dashboard UI is hand-written DOM ([xyflow analysis](../../../inspiration-repos/analysis/xyflow.md), [likec4 analysis](../../../inspiration-repos/analysis/likec4.md) "what not to copy").

## Facts: the options

| Option | What it gives | Limit | Source |
|---|---|---|---|
| ELK (elkjs) | Layered layout with nested nodes, ports, edge routing. JSON in, positions out. Model order keeps layouts stable. EPL-2.0. | Not a drawing library. Default settings are slow on large graphs: OpenArc switches to a cheaper mode above a size threshold. Bun use is claimed, not tested here. | [elk-elkjs](../sources/elk-elkjs.md), [elkjs analysis](../../../inspiration-repos/analysis/elkjs.md) |
| Run ELK on the server | OpenArc runs ELK in Node at build time and ships positions. The browser needs no layout engine. | The elkjs README documents Node through a Worker shim. Bun is not documented in the corpus. | [openarc analysis](../../../inspiration-repos/analysis/openarc.md), [elkjs analysis](../../../inspiration-repos/analysis/elkjs.md) |
| `@xyflow/system` | Framework-free pan, zoom, drag, minimap logic. MIT. | No layout, no rendering. React Flow itself needs React. | [xyflow analysis](../../../inspiration-repos/analysis/xyflow.md), [xyflow-react-flow](../sources/xyflow-react-flow.md) |
| `@xyflow/react` with ELK | Fastest path with most precedent: LikeC4 and Understand-Anything use it. | Needs React as a "React island" in the dashboard. | [xyflow-react-flow](../sources/xyflow-react-flow.md) |
| Mermaid | Many diagram types, LLMs write it, GitHub renders it. MIT. | No levels, no drill-down, no diff, no expand. Browser use needs the library bundle. | [mermaid](../sources/mermaid.md) |
| Dagre | Small and fast. MIT. Weak on large nested graphs. | Fallback for flat graphs. | [browser-layout-engines](../sources/browser-layout-engines.md) |
| d3-hierarchy | Tree, treemap, pack layouts. | No edge routing. Licence ISC not verified. | [browser-layout-engines](../sources/browser-layout-engines.md) |
| Graphviz WASM | Good edge routing, clusters. | WASM load cost. LikeC4 uses `@hpcc-js/wasm-graphviz`; licence not checked. | [browser-layout-engines](../sources/browser-layout-engines.md), [likec4 analysis](../../../inspiration-repos/analysis/likec4.md) |
| Cytoscape.js | Graph analysis oriented. | Heavy for a DOM UI. | [cytoscape-js](../sources/cytoscape-js.md) |
| GoJS | Commercial. | Paid. | [diagramming-frameworks](../sources/diagramming-frameworks.md) |
| LikeC4 static build | A static site from a model. | Little control of the review UI. | [likec4](../sources/likec4.md) |

**Suspect claim.** The ELK source says "Mermaid v12 uses ELK as its default layout (Context7)". The browser-layout-engines source says Dagre was the old Mermaid default. I could not verify which default is current. Do not rely on the ELK-default claim. See [gaps.md](../../../research/gaps.md).

## Two ways to put a picture in a page with no external resources

- **A. Positions as data, client builds SVG.** The server runs ELK and sends JSON (`x`, `y`, `width`, `height`, edge sections). The inline client script builds SVG elements with `createElementNS` and sets text with `textContent`. This keeps the dashboard rule on `innerHTML` and gives full interactivity (select, comment, collapse).
- **B. Server sends a finished SVG string.** The client sets `innerHTML`. This breaks the rule that only escaped Markdown uses `innerHTML`, unless the server sanitises the SVG. GitDiagram uses three layers for diagram safety: server validation, deterministic compiler, client sanitising ([gitdiagram analysis](../../../inspiration-repos/analysis/gitdiagram.md)). Option B suits static exports, not the interactive view.

Option A is a proposal (inference). The corpus does not compare A and B.

## Sequence and flow drawing

- LikeC4 treats a sequence diagram as a view of the same model. It has its own sequence layouter of 889 lines ([likec4 analysis](../../../inspiration-repos/analysis/likec4.md)). This is a sign of the cost of building a swimlane renderer.
- OpenArc models flows as named ordered ID lists over the same graph, so no second diagram type is needed ([openarc analysis](../../../inspiration-repos/analysis/openarc.md)).
- Mermaid sequence rendering is recommended as secondary, for exports and Markdown ([mermaid](../sources/mermaid.md)). Config-harness diagrams are Mermaid, so an export in Mermaid fits the committed copy ([current-state.md](current-state.md), A1).
- Mermaid has size limits: edge caps, text-size errors, slow layout ([representations-beyond-uml.md](representations-beyond-uml.md), section 1). The budgets in [q4-abstraction-and-clutter.md](q4-abstraction-and-clutter.md) keep the export small.

## Open points for a spike

1. Does elkjs run on the Bun server? Measure time for 50, 100 and 200 nodes in `layered` mode with model order on.
2. How far do unchanged nodes move between before and after? ([q2-diff-model.md](q2-diff-model.md)).
3. How big is the inlined client (ELK is server side; pan and zoom helpers only)? The dashboard page is one string, so a bundling step is a new build cost. The corpus does not say how `page.ts` is built.
4. Accept a React island or not? This is a decision for the owner ([../../../research/OPEN-QUESTIONS.md](../../../research/OPEN-QUESTIONS.md)).

## Recommendation

**USE** ELK as the layout engine, run on the Bun server, with positions sent as JSON.

**BUILD** the canvas as hand-written SVG and DOM (option A), and **USE** `@xyflow/system` pan, zoom, drag and minimap helpers or copy their behaviour.

**USE** Mermaid only as an export format (committed copy, PR comment, Markdown), compiled from the one internal model.

**USE** Dagre as a fallback for flat graphs only if ELK size or EPL-2.0 is a problem.

**SKIP** Graphviz WASM, Cytoscape.js, GoJS, PlantUML and D2 for the interactive view.

**SKIP** a client-side layout engine.

Fallback if the spike fails: `@xyflow/react` plus ELK as a React island.

## Sources

- Sources: [elk-elkjs](../sources/elk-elkjs.md), [browser-layout-engines](../sources/browser-layout-engines.md), [xyflow-react-flow](../sources/xyflow-react-flow.md), [mermaid](../sources/mermaid.md), [cytoscape-js](../sources/cytoscape-js.md), [diagramming-frameworks](../sources/diagramming-frameworks.md), [likec4](../sources/likec4.md), [d2](../sources/d2.md), [plantuml](../sources/plantuml.md).
- Analyses: [elkjs](../../../inspiration-repos/analysis/elkjs.md), [xyflow](../../../inspiration-repos/analysis/xyflow.md), [openarc](../../../inspiration-repos/analysis/openarc.md), [likec4](../../../inspiration-repos/analysis/likec4.md), [gitdiagram](../../../inspiration-repos/analysis/gitdiagram.md).
- Synthesis input: [current-state.md](current-state.md), [representations-beyond-uml.md](representations-beyond-uml.md).
