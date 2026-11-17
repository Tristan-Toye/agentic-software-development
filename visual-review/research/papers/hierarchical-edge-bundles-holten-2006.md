# Hierarchical Edge Bundles: Visualization of Adjacency Relations in Hierarchical Data

- **Source**: https://research.tue.nl/en/publications/hierarchical-edge-bundles-visualization-of-adjacency-relations-in/ (IEEE TVCG 12(5), DOI 10.1109/TVCG.2006.147)
- **PDF**: `sources/hierarchical-edge-bundles-holten-2006.pdf` (file path from the repo root: `visual-review/sources/hierarchical-edge-bundles-holten-2006.pdf`)
- **Relevant to**: Clutter control for dependency edges over a module hierarchy. Informs the abstraction slider of the architecture view.

## Summary
The paper treats a compound graph: items with relations and a hierarchy over the items (p1). A software system is a named example, with directories, files and classes and their dependency relations (p1). The method bundles the adjacency edges. Each edge follows the tree path between its end points as a B-spline (p1, p3). A parameter beta in [0,1] sets the bundling strength (p3). Low strength shows node-to-node links. High strength shows links between parent nodes that result from links between their children (p1). The method works with radial and other tree layouts. A use case shows a cluttered display. The user raises the bundling strength so that bundles appear, selects a bundle, and lowers the strength again to see detail (p7).

## Unique insights and approaches
- One continuous control changes the abstraction of the edges. High strength gives parent-to-parent flow. Low strength gives leaf-to-leaf links (p1, p3). For visual-review, this is a way to avoid a separate diagram per level. One slider moves from module-level to file-level dependencies.
- Bundles make implicit parent-level relations visible, without drawing them as separate edges (p1). In a diff view, a bundle can carry a changed-edge count, so that a reviewer sees which modules gain new coupling.
- The select-a-bundle step (p7) is an interaction pattern. It reduces clutter without removing data. This supports the focus-and-context idea in Q6.

## Content table for lookup
| Section / topic | Pages | What you find there | Use during design of |
|---|---|---|---|
| Abstract and compound graph definition | 1 | Compound graph, software example | Q1 framing |
| Bundling strength parameter beta | 3 | How beta changes the curves, resolving ambiguity | Abstraction control (Q1) |
| Use case with selection of a bundle | 7 | Cluttered display, raise strength, select, lower strength | Interaction flow (Q6) |

Page numbers are PDF page numbers of the file in `sources/`.
