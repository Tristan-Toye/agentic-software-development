# xyflow (React Flow, Svelte Flow, @xyflow/system)

- **Type**: library (node-based UI)
- **Repo / site**: https://github.com/xyflow/xyflow , https://reactflow.dev
- **License / cost**: MIT (`inspiration-repos/xyflow/LICENSE`). Free; an optional paid "Pro" tier with extra examples (`inspiration-repos/xyflow/README.md`, "Commercial usage").
- **Self-host**: yes.

## Capabilities
- Pan, zoom, drag, select, connect, minimap, controls, node and edge toolbars (`inspiration-repos/xyflow/packages/react/src/additional-components/`).
- Custom nodes: any React component, so cards can hold text, badges and buttons.
- Sub-flows with `parentId` for nested nodes (docs: https://reactflow.dev/learn/layouting/sub-flows; not re-read).
- `@xyflow/system` holds the framework-free logic: `xypanzoom/`, `xydrag/`, `xyhandle/`, `xyminimap/` (`inspiration-repos/xyflow/packages/system/src/`).
- No layout of its own; integrate ELK or Dagre (https://reactflow.dev/learn/layouting/layouting).
- Proven for architecture diagrams: LikeC4 uses it (`inspiration-repos/likec4/packages/diagram/package.json`); Understand-Anything uses it (`packages/dashboard/package.json`).

## Development resources
- https://reactflow.dev/learn : guides.
- `inspiration-repos/xyflow/examples/react/` : examples.
- Deep analysis: [`../../../inspiration-repos/analysis/xyflow.md`](../../../inspiration-repos/analysis/xyflow.md).

## Alternatives
- Cytoscape.js: [cytoscape-js.md](cytoscape-js.md). Sprotty, GoJS: [diagramming-frameworks.md](diagramming-frameworks.md).

## Recommendation
**BUILD** the canvas by hand on SVG and DOM, and **USE** `@xyflow/system` helpers (pan and zoom, drag) or copy their behaviour. Reason: the dashboard UI is hand-written DOM; React Flow needs React. If the owner accepts a React island for the diagram view, `@xyflow/react` plus ELK is the fastest path and has the most precedent (LikeC4, Understand-Anything). Decide in the spike.
