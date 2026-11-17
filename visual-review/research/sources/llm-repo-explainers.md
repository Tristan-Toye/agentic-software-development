# LLM repository explainers: GitDiagram, DeepWiki-Open, Swark

- **Type**: tool and service (group)
- **Repo / site**: https://github.com/ahmedkhaleel2004/gitdiagram , https://github.com/AsyncFuncAI/deepwiki-open , https://github.com/swark-io/swark , https://deepwiki.com (Cognition; closed service, not evaluated)
- **License / cost**: GitDiagram MIT; DeepWiki-Open MIT; Swark AGPL-3.0 (all GitHub API). The hosted GitDiagram has paid and sponsor features (`inspiration-repos/gitdiagram/CLAUDE.md`). LLM API calls cost money.
- **Self-host**: GitDiagram partial (Next.js app, Docker file in repo); DeepWiki-Open yes; Swark yes (VS Code extension).

## Capabilities
- **GitDiagram**: GitHub repo to interactive Mermaid diagram; the model writes a typed graph, code validates and compiles it (`inspiration-repos/gitdiagram/src/server/generate/graph.ts`, `graph-planner.ts`). Deep analysis: [`../../../inspiration-repos/analysis/gitdiagram.md`](../../../inspiration-repos/analysis/gitdiagram.md).
- **DeepWiki-Open**: open implementation of DeepWiki; builds a wiki with diagrams for a repo and a "codemap for code-centric guided tours" (https://github.com/AsyncFuncAI/deepwiki-open README). Uses retrieval over the code and supports several model providers (README; details not re-read).
- **Swark**: VS Code extension; reads the repo, asks an LLM (through GitHub Copilot) for a Mermaid architecture diagram; "Free, Open Source, Powered by LLMs" (https://github.com/swark-io/swark README). Last push 2025-03-21 (GitHub API).
- Common limits: whole-repo scope, one picture, no diff, no abstraction levels, accuracy depends on prompt and file sampling.

## Development resources
- https://gitdiagram.com : hosted demo and MCP.
- https://marketplace.visualstudio.com/items?itemName=swark.swark : Swark extension.

## Alternatives
- Graph-first explainers: [understand-anything.md](understand-anything.md), [codeboarding.md](codeboarding.md).
- Change-first explainers: [change-diagram-skills.md](change-diagram-skills.md).

## Comparison
| Tool | Input | Output | Grounding | Validation | Diff |
|---|---|---|---|---|---|
| GitDiagram | repo tree and excerpts | Mermaid | edge evidence paths | schema and path checks | no |
| DeepWiki-Open | repo, retrieval | wiki and diagrams | retrieval | not confirmed | no |
| Swark | repo files | Mermaid | prompt only | not confirmed | no |

## Recommendation
**SKIP** as dependencies. **USE** GitDiagram's pattern: the model returns a typed graph with evidence paths, a validator returns typed issues, and a bounded repair loop runs before render (Q2, Q8). Swark's AGPL-3.0 licence blocks copying code into the plugin; read it for ideas only.
