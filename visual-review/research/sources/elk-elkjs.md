# ELK / elkjs (Eclipse Layout Kernel)

- **Type**: library (graph layout engine)
- **Repo / site**: https://github.com/kieler/elkjs , https://github.com/eclipse-elk/elk , https://eclipse.dev/elk/
- **License / cost**: EPL-2.0 (`inspiration-repos/elkjs/LICENSE.md`; GitHub reports NOASSERTION). Free.
- **Self-host**: yes. One npm package; Web Worker or Node.

## Capabilities
- Layered layout for directed diagrams, with ports, edge routing and labels (`inspiration-repos/elkjs/README.md`).
- Compound (nested) graphs: `hierarchyHandling: INCLUDE_CHILDREN` lays out a node and all descendants in one run (https://eclipse.dev/elk/reference/options/org-eclipse-elk-hierarchyHandling.html).
- Other algorithms: stress, force, radial, mrtree, box, rectpacking (https://eclipse.dev/elk/reference/algorithms.html, not read in detail).
- Input and output are JSON; positions are `x`, `y`, `width`, `height` (`README.md`, "Example").
- Layout options are string keys (`README.md`, "Layout Options"); model order can keep results stable (`inspiration-repos/openarc/viewer/src/place.ts`).
- Mermaid v12 uses ELK as its default layout (Context7 `/mermaid-js/mermaid`).
- Used with Sprotty, OpenArc, Understand-Anything, Mermaid (see their `package.json` and sources).

## Development resources
- https://eclipse.dev/elk/reference.html : algorithm and option reference.
- https://rtsys.informatik.uni-kiel.de/elklive/ : ELK Live demo.
- `inspiration-repos/openarc/tools/layout.ts` : ELK in Node at build time.
- Context7 library `/kieler/elkjs` : API snippets.

## Alternatives
See the table in [browser-layout-engines.md](browser-layout-engines.md).

## Recommendation
**USE** elkjs as the default layout engine. It is the only free engine in this list that handles nested compound nodes well, and nesting is the layout side of abstraction levels (Q1). Run it on the Bun server and send positions to the browser, as OpenArc does. Keep model order on, so "before" and "after" layouts stay similar (Q4). Note EPL-2.0: depend on the npm package; do not fork its files into the product.
