# Structurizr (structurizr/structurizr)

- **Repo**: https://github.com/structurizr/structurizr (Apache-2.0, `inspiration-repos/structurizr/LICENSE`; 534 stars, pushed 2026-11-07 per the GitHub API). The older split repos `structurizr/java`, `structurizr/cli` and `structurizr/lite` are archived (GitHub API, `archived: true`).
- **Clone**: `inspiration-repos/structurizr/` (Java, Maven multi-module)
- **Relevant to**: Q1 (C4 levels), Q2 (model from code), Q4 (what it lacks), Q7

## What it is
The reference implementation of the C4 model ("models as code", `README.md`). You write Structurizr DSL; one model produces many views (system context, container, component, dynamic, deployment). It has a web server, a CLI, exporters to PlantUML, Mermaid and others, and an MCP server.

## Architecture (module map)
- `structurizr-core`: the workspace model (people, software systems, containers, components, relationships, views).
- `structurizr-dsl`: the DSL parser (`com/structurizr/dsl/*Parser.java`).
- `structurizr-json`: workspace JSON read and write. This is the portable model format.
- `structurizr-export`: renders views as PlantUML, Mermaid, DOT, and other formats (`structurizr-export/README.md`).
- `structurizr-autolayout`: automatic layout of views.
- `structurizr-component`, `structurizr-annotation`: build a component model from Java code (annotations, type matching).
- `structurizr-inspection`: model quality checks; shown in the web UI by `InspectionsController`.
- `structurizr-application`: the app with `command/` (CLI), `mcp/` (MCP tools: `DslTools`, `MermaidTools`, `PlantUMLTools`, `ServerReadTools`), and the web UI.

## Ideas worth borrowing
- **One model, many views, four C4 levels** (`README.md`): context, container, component, code. A clean vocabulary for Q1.
- **Dynamic views** are the C4 way to show a sequence of calls through the same model as the static views (Q5). See https://docs.structurizr.com/dsl/views.
- **Workspace JSON as an exchange format** (`structurizr-json`): a stable schema a generator can write and a UI can read.
- **Inspections** (`structurizr-inspection/`): model-quality rules such as missing descriptions. Reuse the idea for a diagram quality gate (Q8).
- **Component finder from code** (`structurizr-component/`): shows how a tool maps code types to C4 components by rules. It is Java-only, but the rule idea carries to Rust modules (Q3).
- **MCP server over the model** (`structurizr-application/src/main/java/com/structurizr/mcp/`).

## What not to copy
- The Java stack and the JSP web UI (`structurizr-application/src/main/webapp/`).
- It has no diff of two models in the code base (no diff command in `command/`). Use LikeC4 `diff` ideas for Q4.
- Hand-authored models: the model must be generated from code.

## License
Apache-2.0.
