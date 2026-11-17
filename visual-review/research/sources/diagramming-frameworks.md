# Diagramming frameworks: Sprotty, GoJS, and others

- **Type**: framework
- **Repo / site**: https://github.com/eclipse-sprotty/sprotty , https://gojs.net
- **License / cost**: Sprotty EPL-2.0 (GitHub API). GoJS is commercial; its page is titled "Software License Agreement" (https://www.gojs.net/latest/doc/license.html); price not read in this session. Free evaluation mode is known from the vendor site (not verified here).
- **Self-host**: yes for both.

## Capabilities
- **Sprotty**: web diagram framework on SVG, with a model-view split, client-side and server-side model, and ELK integration through `sprotty-elk` (https://github.com/eclipse-sprotty/sprotty). Supports hierarchical diagrams, animations, and a language-server protocol (not read in detail).
- **GoJS**: commercial JS library with groups, expand and collapse, layouts, palettes, and many sample apps (https://gojs.net).
- **Other candidates, not evaluated**: JointJS (MPL-2.0 core, paid Rappid), maxGraph (Apache-2.0, successor of mxGraph), Rete.js, tldraw (https://github.com/tldraw/tldraw, custom licence).

## Development resources
- https://sprotty.org : docs and examples.
- https://gojs.net/latest/samples/ : sample list.

## Alternatives
- [xyflow-react-flow.md](xyflow-react-flow.md), [cytoscape-js.md](cytoscape-js.md), [elk-elkjs.md](elk-elkjs.md).

## Comparison
| Option | Free | Nested nodes | Expand and collapse | Weight | Fit |
|---|---|---|---|---|---|
| Sprotty | yes (EPL-2.0) | yes | yes | TypeScript, inversify | strong match for a hand-written SVG UI; small community (890 stars, GitHub API) |
| GoJS | no | yes | yes | one file | paid; core must not depend on it |
| xyflow | yes (MIT) | yes | by own code | React or system | most precedent |

## Recommendation
**SKIP** GoJS (paid). **USE** Sprotty as a reference for the model-view split and for ELK integration; do not adopt it unless the spike shows xyflow or hand-written SVG is too costly. Reason: Sprotty is the closest free framework to "SVG UI plus ELK plus hierarchy", but its small community and inversify-based design add risk.
