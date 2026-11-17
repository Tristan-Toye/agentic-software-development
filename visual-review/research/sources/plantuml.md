# PlantUML

- **Type**: tool (text-to-diagram, Java)
- **Repo / site**: https://github.com/plantuml/plantuml , https://plantuml.com
- **License / cost**: LGPL-3.0 per GitHub API (other licence options exist on the site; not checked). Free.
- **Self-host**: yes (JAR, or a server). Needs Java and optionally Graphviz.

## Capabilities
- Sequence, component, deployment, class, C4 (through C4-PlantUML include files), and many more types.
- Output SVG and PNG; no interactive layer of its own.
- Large installed base and many generators target it (trace tools such as https://github.com/upamune/jigsaw write PlantUML or Mermaid).

## Development resources
- https://plantuml.com/sequence-diagram : sequence syntax.
- https://github.com/plantuml-stdlib/C4-PlantUML : C4 macros.

## Alternatives
- Mermaid ([mermaid.md](mermaid.md)), D2 ([d2.md](d2.md)).

## Recommendation
**SKIP**. It needs a Java runtime, its output is static, and the owner finds raw UML cluttered. Keep it only as an optional export target. Mermaid and the product's own renderer cover the need.
