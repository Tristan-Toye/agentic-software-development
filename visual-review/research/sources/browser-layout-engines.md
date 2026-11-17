# Browser layout engines: Dagre, d3-hierarchy, Graphviz WASM, ELK

- **Type**: library (graph layout)
- **Repo / site**: https://github.com/dagrejs/dagre , https://github.com/d3/d3-hierarchy , https://github.com/mdaines/viz-js , https://github.com/kieler/elkjs
- **License / cost**: Dagre MIT; viz-js MIT (GitHub API). d3-hierarchy ISC (not verified in this session; see its repo). ELK EPL-2.0. All free.
- **Self-host**: yes, all.

## Capabilities
- **Dagre**: layered layout for directed graphs, small and fast, JS only. Supports clusters through its graph library; layout quality drops on large nested graphs (https://github.com/dagrejs/dagre). Used by Understand-Anything (`inspiration-repos/Understand-Anything/understand-anything-plugin/packages/dashboard/package.json`) and as the old Mermaid default.
- **d3-hierarchy**: tree, cluster, treemap, pack and partition layouts for hierarchical data (https://github.com/d3/d3-hierarchy). Good for "containment" views such as a crate, module, file tree. No edge routing.
- **Graphviz in WASM (viz-js)**: runs `dot`, `neato` and others in the browser or Node (https://github.com/mdaines/viz-js). LikeC4 uses Graphviz through `@hpcc-js/wasm-graphviz` (`inspiration-repos/likec4/packages/likec4/package.json`). Good edge routing; clusters supported; the output has less control over fixed sizes.
- **ELK**: see [elk-elkjs.md](elk-elkjs.md).

## Development resources
- https://github.com/dagrejs/dagre/wiki : Dagre options.
- https://observablehq.com/@d3/d3-hierarchy : d3-hierarchy examples.
- https://github.com/mdaines/viz-js : Viz.js docs and API.

## Alternatives
- WebCola / cola.js (constraint-based, interactive force layout): not evaluated.
- Cytoscape layouts: [cytoscape-js.md](cytoscape-js.md).

## Comparison
| Engine | Nested nodes | Edge routing | Edge labels | Speed on 200 nodes | License | Fit |
|---|---|---|---|---|---|---|
| ELK | strong | orthogonal, polyline, splines | yes | measured by OpenArc bench: layered mode has a "thorough" and a fast setting (`inspiration-repos/openarc/viewer/src/place.ts`) | EPL-2.0 | default |
| Dagre | basic clusters | polyline | yes | fast | MIT | fallback for flat graphs |
| Graphviz WASM | clusters | splines, good | yes | medium; WASM load cost | MIT (viz-js) | export and static images |
| d3-hierarchy | tree only | none | none | very fast | ISC | containment or treemap view |

No benchmark was run in this research. Run one in the spike (see synthesis).

## Recommendation
**USE** ELK as the default, d3-hierarchy for a containment view (module and file trees), and Dagre only if ELK size or EPL-2.0 becomes a problem. **SKIP** Graphviz WASM for the interactive canvas; keep it as an optional exporter.
