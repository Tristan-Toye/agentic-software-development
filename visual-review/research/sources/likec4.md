# LikeC4

- **Type**: framework (architecture-as-code: DSL, model API, diagram component, CLI, MCP)
- **Repo / site**: https://github.com/likec4/likec4 , https://likec4.dev/
- **License / cost**: MIT (`inspiration-repos/likec4/LICENSE`, GitHub API). Free. Layout uses `@hpcc-js/wasm-graphviz` (`inspiration-repos/likec4/packages/likec4/package.json`).
- **Self-host**: yes. `likec4 build` writes a static site; `likec4 start` runs a local server (`inspiration-repos/likec4/apps/docs/src/content/docs/tooling/cli.mdx`).

## Capabilities
- Model: elements, relationships and views in a DSL, with custom element kinds and any number of nested levels (README, "What is LikeC4").
- Views: predicates choose which elements a view shows; `view of <element>` scopes a view; dynamic views show ordered interactions (`apps/docs/src/content/docs/dsl/Views/predicates.mdx`, `dynamic.mdx`).
- Diff: `diff` blocks describe a planned change. Compare views show `as-is`, `changes` and `to-be` with `added`, `removed`, `modified` marks. CLI: `likec4 diff list|apply|discard` (`dsl/diff.mdx`, `packages/core/src/compute-view/model-diff/compare-view.ts`, `.changeset/model-diffs-*.md`).
- Removal impact and safe delete (`packages/core/src/compute-view/model-diff/removal-impact.ts`).
- Layout: Graphviz (WASM or binary) in `packages/layouts/src/graphviz/`; own sequence layout in `packages/layouts/src/sequence/layouter.ts`.
- UI: React diagram on `@xyflow/react`, with navigation panel, search, overlays (`packages/diagram/src/`).
- Export: PNG, JPG, React component, web component, static site (`cli.mdx`).
- MCP server: graph queries, `list-diffs`, `removal-impact`, `render-view` (`packages/mcp/src/tools/`).
- GitHub Action by a third party posts a Mermaid diff of changed views on a PR: https://github.com/novibe-org/likec4-architecture-diff-action (Apache-2.0).

## Development resources
- https://likec4.dev/ : docs for DSL, views, tooling.
- https://playground.likec4.dev/ : online playground.
- `inspiration-repos/likec4/AGENTS.md` : how the repo guides agents; links to model, webapp and sequence rules.
- Deep analysis: [`../../../inspiration-repos/analysis/likec4.md`](../../../inspiration-repos/analysis/likec4.md).

## Alternatives
- Structurizr DSL: [structurizr.md](structurizr.md). Reference C4 tool; no model diff.
- D2, Mermaid, PlantUML: [d2.md](d2.md), [mermaid.md](mermaid.md), [plantuml.md](plantuml.md). Drawing languages, not models with many views.
- IcePanel, Ilograph: [icepanel-ilograph-terrastruct.md](icepanel-ilograph-terrastruct.md). Paid.

## Recommendation
**USE** the LikeC4 ideas and the LikeC4 JSON model shape as the main reference; **BUILD** the product's own model and renderer. Reasons: LikeC4 is the only open tool with a model diff and as-is, changes, to-be views (Q4), view predicates for abstraction (Q1), dynamic views and sequence layout (Q5). Its DSL, Langium language server and React and Mantine UI are too heavy for a hand-written DOM dashboard. Two paths exist. Path A: the extractor writes LikeC4 DSL, and the dashboard embeds `likec4 build` output. This is fast but gives little control of the review UI (Q9). Path B: the extractor writes its own JSON model with LikeC4's status vocabulary, and the dashboard renders it. Path B is the recommendation. Test path A as a spike for the "browse all diagrams" long-term map (Q7).
