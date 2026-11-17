# xyflow / React Flow (xyflow/xyflow)

- **Repo**: https://github.com/xyflow/xyflow (MIT, `inspiration-repos/xyflow/LICENSE`)
- **Clone**: `inspiration-repos/xyflow/` (React Flow 12.12.1 at `packages/react/package.json`)
- **Relevant to**: Q1 abstraction levels, Q6 interactivity

## What it is
A monorepo for node-based UIs: `@xyflow/react` (React), `@xyflow/svelte`, and `@xyflow/system`, a shared framework-agnostic helper library (`inspiration-repos/xyflow/README.md`). It does not lay out nodes. It renders, pans, zooms, drags and connects them. Layout comes from ELK, Dagre or similar (see `elkjs.md`).

## Architecture
- `packages/system/src/`: framework-free code: `xypanzoom/` (pan and zoom), `xydrag/` (drag), `xyhandle/` (connections), `xyminimap/`, `xyresizer/`, `utils/` (graph and edge-path helpers), `types/`.
- `packages/react/src/`: React components (`components/`, `container/`, `hooks/`, `store/`) and `additional-components/` (`Controls`, `MiniMap`, `NodeToolbar`, `NodeResizer`, `Background`, `EdgeToolbar`).
- `examples/react/`: many Vite examples. Look here for sub-flows, collapsible groups and layout integration.

## Ideas worth borrowing
- **`@xyflow/system` is DOM-only logic**: pan and zoom (`packages/system/src/xypanzoom/`), drag (`xydrag/`) and minimap (`xyminimap/`) work without React. A hand-written DOM UI can reuse them, or copy the interaction design (Q6).
- **Node and edge model**: nodes with `parentId` for sub-flows, edges with handles. The same shape fits "component inside a group" (Q1).
- **Node toolbar and edge toolbar** (`packages/react/src/additional-components/NodeToolbar`, `EdgeToolbar`): a pattern for an "ask a question about this node" button (Q9).
- **Used by LikeC4**: `inspiration-repos/likec4/packages/diagram/package.json` depends on `@xyflow/react` and `@xyflow/system`. This shows it can carry a C4 diagram UI with a diff overlay.

## What not to copy
- The React renderer, if the dashboard stays hand-written DOM. React Flow needs React as a peer dependency; `@xyflow/system` does not.
- The Pro examples. Some React Flow examples are paid; the commercial-usage note in `README.md` asks organisations to subscribe, but the code is MIT.

## License
MIT. Core code free to use; paid tier is optional support and Pro examples (`README.md`, "Commercial usage").
