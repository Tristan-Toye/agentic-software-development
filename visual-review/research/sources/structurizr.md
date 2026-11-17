# Structurizr and the C4 model

- **Type**: tool (C4 reference implementation) and model
- **Repo / site**: https://github.com/structurizr/structurizr , https://docs.structurizr.com/ , https://c4model.com
- **License / cost**: Apache-2.0 (`inspiration-repos/structurizr/LICENSE`). Free. The hosted Structurizr cloud service is paid; this document does not rely on it (not evaluated).
- **Self-host**: yes. `structurizr local`, Docker, CLI (`inspiration-repos/structurizr/README.md`). The old repos `structurizr/lite`, `structurizr/cli` and `structurizr/java` are archived (GitHub API).

## Capabilities
- Structurizr DSL: one model, many views (system landscape, system context, container, component, dynamic, deployment) (`README.md`).
- Workspace JSON read and write (`structurizr-json/`).
- Exporters to PlantUML, Mermaid, DOT and others (`structurizr-export/README.md`).
- Auto layout (`structurizr-autolayout/`).
- Component finder from Java code by annotations and type rules (`structurizr-component/`, `structurizr-annotation/`).
- Model inspections (`structurizr-inspection/`).
- MCP server for DSL and export (`structurizr-application/src/main/java/com/structurizr/mcp/`).
- No model diff command in the code base (`structurizr-application/src/main/java/com/structurizr/command/` lists no diff command).

## Development resources
- https://docs.structurizr.com/dsl : DSL reference.
- https://docs.structurizr.com/mcp : MCP server (referenced in `changelog-application.md`).
- Deep analysis: [`../../../inspiration-repos/analysis/structurizr.md`](../../../inspiration-repos/analysis/structurizr.md).

## Alternatives
- LikeC4: [likec4.md](likec4.md). Same idea, TypeScript, has diffs.
- C4-PlantUML (https://github.com/plantuml-stdlib/C4-PlantUML): C4 macros for PlantUML; drawing only.
- Mermaid C4: [mermaid.md](mermaid.md); marked experimental.

## Recommendation
**USE** the C4 vocabulary (context, container, component, code; dynamic view) for the abstraction levels (Q1, Q5). **SKIP** Structurizr as a runtime dependency: it is Java, has no diff, and its UI is server-rendered JSP. Offer a Structurizr DSL or workspace JSON export later, so owners can open the model in Structurizr.
