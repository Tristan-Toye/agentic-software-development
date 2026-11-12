# elkjs / Eclipse Layout Kernel (kieler/elkjs)

- **Repo**: https://github.com/kieler/elkjs (EPL-2.0, `inspiration-repos/elkjs/LICENSE.md`); core is https://github.com/eclipse-elk/elk
- **Clone**: `inspiration-repos/elkjs/` (version 0.13.0, `package.json`)
- **Relevant to**: Q1 abstraction levels (hierarchical layout), Q6 interactivity

## What it is
ELK compiled from Java to JavaScript (GWT). It computes positions and edge routes for node-link diagrams. It is a layout engine only: "elkjs is not a diagramming framework itself" (`README.md`). Its main algorithm is a layered (Sugiyama-style) layout with ports and nested nodes.

## Architecture
- `src/java`, `src/java-additional`: Java sources of the JS build; `src/js`: the JS wrapper.
- Files in the npm package (`README.md`, "Files"): `elk-api.js` (API only), `elk-worker.js` (the engine), `elk.bundled.js` (both, for a `<script>` tag).
- Web Worker support out of the box; Node uses a `Worker` shim (`README.md`, "Usage", "node.js").
- Input is a JSON graph: `children`, `edges`, `layoutOptions`. Output adds `x`, `y`, `width`, `height` and edge sections (`README.md`, "Example").

## Ideas worth borrowing
- **Hierarchical (compound) graphs**: nodes can contain `children`; ELK lays out the nesting. This is the layout side of abstraction levels (Q1).
- **Layout options as plain strings** (`README.md`, "Layout Options"): `elk.algorithm`, `elk.direction`, `elk.layered.*`, `elk.spacing.*`. Example of the options in practice: `inspiration-repos/openarc/viewer/src/place.ts` and `tools/layout.ts`.
- **Run layout at build or server time**: OpenArc runs ELK in Node and ships the positions (`inspiration-repos/openarc/tools/layout.ts`). The Bun server can do the same, so the browser needs no layout engine.
- **Stable layout for diffs**: `elk.layered.considerModelOrder.strategy` keeps node order stable between runs (used in `inspiration-repos/openarc/viewer/src/place.ts`). Needed so the "before" and "after" diagrams look alike (Q4).
- **Self-hostable in a browser or Bun**: one file, no service. Fits the no-paid-service rule.

## What not to copy
- The Java build (`build.gradle`): use the published npm bundle.
- Default layered settings for large graphs: OpenArc switches to a cheaper mode above a size threshold (`place.ts`). Measure before using thorough settings.

## License
EPL-2.0 (weak copyleft, file level). Using the unmodified npm package as a dependency is normal. Do not copy and modify its files into the product without checking EPL terms. GitHub reports `NOASSERTION`; read `LICENSE.md`.
