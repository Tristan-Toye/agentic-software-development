# Cytoscape.js

- **Type**: library (graph theory and graph rendering)
- **Repo / site**: https://github.com/cytoscape/cytoscape.js , https://js.cytoscape.org
- **License / cost**: MIT (GitHub API). Free.
- **Self-host**: yes. Browser (canvas) and Node (headless).

## Capabilities
- Graph model with compound nodes (parent and child), selectors, styling by class, events (Context7 `/cytoscape/cytoscape.js`).
- Many built-in layouts and extensions: `cytoscape-elk`, `cytoscape-dagre`, `cytoscape-expand-collapse`, `cytoscape-popper` (https://js.cytoscape.org/#extensions; extension list not re-read in this session).
- Canvas renderer; good performance on thousands of nodes. Graph analysis algorithms built in (BFS, shortest path, centrality) (https://js.cytoscape.org/#collection/graph-manipulation).
- Headless mode for server-side analysis.

## Development resources
- https://js.cytoscape.org : docs.
- Context7 library `/cytoscape/cytoscape.js` : API snippets.

## Alternatives
- xyflow: [xyflow-react-flow.md](xyflow-react-flow.md). Card-like nodes with HTML content.
- Sprotty, GoJS: [diagramming-frameworks.md](diagramming-frameworks.md).

## Comparison
| Option | Node look | Nested nodes | Collapse | Rendering | License |
|---|---|---|---|---|---|
| Cytoscape.js | styled shapes, canvas | yes | via extension | canvas | MIT |
| xyflow system | any DOM or SVG | via `parentId` | by own code | DOM or SVG | MIT |

## Recommendation
**SKIP** as the first choice, **USE** as the fallback. Canvas nodes make rich cards (text, badges, diff marks, buttons) harder than DOM or SVG nodes. It is the best choice if a graph grows past what a DOM canvas can draw, for example the long-term whole-product map with thousands of nodes (Q7).
