# LikeC4 (likec4/likec4)

- **Repo**: https://github.com/likec4/likec4 (MIT, `inspiration-repos/likec4/LICENSE`; 6065 stars, pushed 2026-11-11 per the GitHub API)
- **Clone**: `inspiration-repos/likec4/` (pnpm monorepo, TypeScript)
- **Relevant to**: Q1 abstraction levels, Q4 diagram diffs, Q5 sequence diagrams, Q6 interactivity, Q7 living sync

## What it is
LikeC4 is an architecture-as-code tool. A DSL describes a model (elements, relationships, views). Tools generate interactive diagrams from the model (`inspiration-repos/likec4/README.md`). It has a language server, a CLI, a VS Code extension, a React diagram component, a static-site build and an MCP server.

## Architecture (package map)
- `packages/core`: model types, view computation (`src/compute-view/`), model diff (`src/compute-view/model-diff/`).
- `packages/language-server`, `packages/language-services`: Langium grammar and services for the DSL.
- `packages/layouts`: Graphviz layout (WASM or binary) in `src/graphviz/`; own sequence-diagram layouter in `src/sequence/layouter.ts` (889 lines).
- `packages/diagram`: React diagram built on `@xyflow/react` (`packages/diagram/package.json` dependencies; `src/likec4diagram/xyflow-diagram/`, `xyflow-sequence/`).
- `packages/likec4`: CLI (`start`, `build`, `export`, `gen`, `diff`), Vite-based static build (`apps/docs/src/content/docs/tooling/cli.mdx`).
- `packages/mcp`: MCP server with tools such as `query-graph`, `find-relationship-paths`, `list-diffs`, `removal-impact`, `render-view`, `open-view` (`packages/mcp/src/tools/`).

## Ideas worth borrowing
- **Model diffs as a first-class concept** (`apps/docs/src/content/docs/dsl/diff.mdx`): a named `diff` block with `remove` and `add` sections describes a planned change. The model stays "as is" until the diff is applied. This is the closest existing design for "diagram of a planned change" (Q4).
- **Compare view with three modes** (`packages/core/src/compute-view/model-diff/compare-view.ts`): `as-is`, `changes` (both states, nodes and edges marked `added | removed | modified`), `to-be`. Counts of changed nodes and edges are returned per view (`DiffViewSummary`, `packages/core/src/types/model-diff.ts`).
- **Diff validation against the model** (`diff.mdx`, "Validation"): a removed element must exist; an added element must not exist. Use the same check to detect a stale plan (Q7).
- **Removal impact** (`packages/core/src/compute-view/model-diff/removal-impact.ts`): reports relationships, views and orphans affected by a removal. Useful for the "what breaks" part of review.
- **Views as predicates over one model** (`apps/docs/src/content/docs/dsl/Views/predicates.mdx`): one model, many views, each view chooses which elements to include. This is the clutter control for Q1.
- **Nested levels and `view of <element>` scoping** (`apps/docs/src/content/docs/dsl/Views/index.mdx`): drill-down by navigating from an element to its own view (Q1, Q6).
- **Dynamic views** (`apps/docs/src/content/docs/dsl/Views/dynamic.mdx`) with a sequence layout (`packages/layouts/src/sequence/`): sequence diagrams are a view of the same model, not a separate drawing (Q5).
- **MCP tools over the model** (`packages/mcp/src/tools/`): the agent queries the model instead of guessing (Q8).
- **Agent guidance files** (`AGENTS.md`, `skills/likec4-dsl/`): a worked example of a repo that tells agents how to write its DSL.
- **Static build** (`likec4 build`, `likec4 export png`): the output can be a static site, so the dashboard can embed or link it.

## What not to copy
- The Langium grammar and language server: a large dependency. The dashboard needs only a JSON model. Consider the JSON model API (`apps/docs/src/content/docs/tooling/model-api/`) and skip the DSL.
- The React and Mantine UI stack in `packages/diagram`: the dashboard UI is hand-written DOM.
- The assumption that humans author the model. In visual-review an agent extracts it from code, so keep a generated-model path (`packages/generators/`).

## License
MIT. Layout uses `@hpcc-js/wasm-graphviz` (`packages/likec4/package.json`); check its license before bundling.
