# GitDiagram (ahmedkhaleel2004/gitdiagram)

- **Repo**: https://github.com/ahmedkhaleel2004/gitdiagram (MIT, `inspiration-repos/gitdiagram/LICENSE`; 18870 stars, pushed 2026-11-11 per the GitHub API)
- **Clone**: `inspiration-repos/gitdiagram/`
- **Relevant to**: Q2 grounding, Q8 LLM accuracy (validation and repair loop)

## What it is
A web service that turns a GitHub repository into an interactive Mermaid architecture diagram, plus narrated videos and an MCP server (`README.md`). It is a single Next.js 16 application on Bun (`CLAUDE.md`, "What this is"). It is a product with sponsors and a paid tier, so much of the code is not relevant.

## Architecture (the relevant part)
The model does not write Mermaid directly. The pipeline in `src/server/generate/`:
1. `repository-context.ts`, `source-context.ts`, `source-excerpt.ts`, `source-references.ts`: build the prompt context from the file tree, README and excerpts of read files, plus a per-file list of imported files (`createReferenceResolver`, `MAX_REFERENCES_PER_FILE = 40`).
2. `graph-planner.ts`: asks the model for a structured graph (`DiagramGraph`: groups, nodes with `path`, edges with evidence paths) with retries (`MAX_GRAPH_ATTEMPTS`).
3. `graph.ts`: `validateDiagramGraph` checks the graph and returns typed issues: `duplicate_node_id`, `unknown_group_id`, `missing_repository_path`, `missing_evidence_path`, `unknown_edge_source`, `unknown_edge_target`. Issues are fed back to the model for a repair attempt (`formatGraphValidationFeedback`, `isRepairableWithoutRetry`).
4. `edge-evidence.ts`: `applyEdgeEvidence` drops a cited file the model never saw, and fills a missing citation with a file that imports both sides.
5. A deterministic compiler turns the validated graph into Mermaid; the output is validated again (`mermaid-validator.ts`). `CLAUDE.md` says diagram safety has three layers: server validation, deterministic compiler, client sanitising.

## Ideas worth borrowing
- **LLM emits a typed graph, code emits the diagram** (`graph-planner.ts`, `graph.ts`): the model cannot produce a broken diagram, only a graph that fails validation. Same direction as OpenArc (Q8).
- **Every node has a repository `path`, every edge has evidence paths** (`graph.ts`, `edge-evidence.ts`): grounding is part of the schema (Q2).
- **Typed validation issues fed back as repair prompts** (`graph.ts`, `graph-planner.ts`): a bounded retry loop with specific errors (Q8).
- **Citation must be something the model was shown** (`edge-evidence.ts`, `EdgeEvidenceContext.readPaths`): a cheap defence against invented evidence.
- **Cost and attempt audit** (`GenerationSessionAudit`, `src/server/generate/session-audit.ts`): records tokens and attempts per run. Matches the plugin's token-spend interest.
- **Markdown twin and MCP** (`README.md`): `gitdiagram.com/owner/repo.md` returns the diagram as Markdown for agents.

## What not to copy
- The Next.js, Cloudflare, Stripe and sponsor machinery (`CLAUDE.md`, "Hard rules"): not relevant to a self-hosted dashboard.
- Output as one Mermaid graph with click events: no abstraction levels, no diff (Q1, Q4).
- Its repo-wide, one-shot approach: it reads a file tree and excerpts, not resolved code relationships.

## License
MIT. The hosted service and sponsor assets are separate; do not copy branding.
