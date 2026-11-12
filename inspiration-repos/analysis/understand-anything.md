# Understand-Anything (Egonex-AI/Understand-Anything)

- **Repo**: https://github.com/Egonex-AI/Understand-Anything (MIT; 92307 stars, pushed 2026-11-11 per the GitHub API)
- **Clone**: `inspiration-repos/Understand-Anything/` (plugin code in `understand-anything-plugin/`)
- **Relevant to**: Q1, Q2, Q3, Q4 (impact view), Q6, Q7, Q8

## What it is
A multi-platform agent plugin. `/understand` builds an interactive knowledge graph of a codebase (files, functions, classes, layers, domains, tours). `/understand-dashboard` opens a web dashboard. Other skills: `understand-diff` (impact of current changes), `understand-domain` (domains, flows, steps), `understand-chat`, `understand-onboard` (`README.md`, "Quick Start").

## Architecture
- `understand-anything-plugin/agents/`: ten sub-agent prompts: `project-scanner`, `file-analyzer`, `architecture-analyzer`, `domain-analyzer`, `tour-builder`, `graph-reviewer`, `assemble-reviewer`.
- `packages/core/src/`: `types.ts` (graph schema), `analyzer/graph-builder.ts`, `analyzer/layer-detector.ts`, `analyzer/llm-analyzer.ts`, `fingerprint.ts`, `staleness.ts`, `change-classifier.ts`, `schema.ts` (validation).
- Tree-sitter parsing via `web-tree-sitter` with grammars including `tree-sitter-rust` (`packages/core/package.json`).
- `packages/dashboard/`: React and `@xyflow/react` dashboard, with `elkjs`, `@dagrejs/dagre` and `d3-force` in `package.json`.
- `src/diff-analyzer.ts`: `buildDiffContext` maps changed files to graph nodes and finds affected nodes, edges and layers.
- `hooks/post-tool-use-auto-update.mjs`, `hooks/session-start-stale-check.mjs`: auto-update after `git commit` and stale checks.

## Ideas worth borrowing
- **Deterministic scan plus LLM summary** (`agents/file-analyzer.md`, `packages/core/src/analyzer/`): tree-sitter extracts structure, the LLM adds names and summaries (Q2, Q8).
- **Graph reviewer agent with a validation script** (`agents/graph-reviewer.md`): the agent writes and runs a script that checks schema, ids and completeness, then approves or rejects. A good template for the visual-review checker (Q8).
- **Fingerprints for incremental updates** (`packages/core/src/fingerprint.ts`): per-file content hash plus function, class and import signatures; `change-classifier.ts` decides whether a change is structural or cosmetic. This is a ready design for keeping a living map in sync cheaply (Q7).
- **Staleness banner and freshness** (`packages/dashboard/src/components/StalenessBanner.tsx`, `src/freshness.ts`, `packages/core/src/staleness.ts`): the dashboard says when the map is behind the code (Q7).
- **Layers and domains as abstraction levels** (`analyzer/layer-detector.ts`, `agents/domain-analyzer.md`, `LayerClusterNode.tsx`, `DomainGraphView.tsx`): a structural view and a business-domain view of the same graph (Q1, Q3).
- **Diff overlay and toggle** (`src/diff-analyzer.ts`, `DiffToggle.tsx`; `diff-overlay.json` in `README.md`, "Share the Graph"): changed and affected nodes highlighted on the graph (Q4).
- **Tour builder** (`agents/tour-builder.md`, `analyzer/tour-generator.ts`): ordered guided walk through the graph (Q9).
- **Breadcrumb, path finder, search, export** (`components/Breadcrumb.tsx`, `PathFinderModal.tsx`, `SearchBar.tsx`, `ExportMenu.tsx`): drill-down UX pieces (Q6).
- **Graph committed to the repo** (`README.md`, "Share the Graph with Your Team": `.ua/` except scratch files): the living map is a tracked artifact.

## What not to copy
- Whole-codebase graphs of every file and function as the default view: the owner finds this cluttered. Use layers and domains first.
- The React dashboard: reuse the idea, not the code.
- The token cost of the first full run (`README.md` warns about it): feature-level diagrams need a cheaper path.
- No sequence diagrams: it has flows and tours, not sequences (Q5).

## License
MIT.
