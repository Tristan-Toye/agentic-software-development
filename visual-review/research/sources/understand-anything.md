# Understand-Anything

- **Type**: tool (agent plugin with dashboard)
- **Repo / site**: https://github.com/Egonex-AI/Understand-Anything
- **License / cost**: MIT. Free. First full run costs many tokens (README warns).
- **Self-host**: yes. Local dashboard; graph stored in `.ua/` in the repo.

## Capabilities
- `/understand` builds a knowledge graph with layers, domains and tours; later runs are incremental (README, "Quick Start").
- `/understand-diff` maps current changes onto the graph and shows affected nodes (`inspiration-repos/Understand-Anything/understand-anything-plugin/src/diff-analyzer.ts`).
- Ten sub-agents: scanner, file analyser, architecture analyser, domain analyser, tour builder, graph reviewer, assemble reviewer (`understand-anything-plugin/agents/`).
- Tree-sitter parsing for many languages including Rust (`packages/core/package.json`).
- Dashboard with React, xyflow, ELK, Dagre, d3-force; layer and domain views; staleness banner; search; export (`packages/dashboard/`).
- Hooks update the graph after `git commit` (`hooks/post-tool-use-auto-update.mjs`).

## Development resources
- Deep analysis: [`../../../inspiration-repos/analysis/understand-anything.md`](../../../inspiration-repos/analysis/understand-anything.md).
- `inspiration-repos/Understand-Anything/CLAUDE.md` : repo guidance.

## Alternatives
- [codeboarding.md](codeboarding.md), [llm-repo-explainers.md](llm-repo-explainers.md).

## Recommendation
**BUILD** our own, **USE** its design for fingerprints, staleness, graph-reviewer and diff overlay (Q4, Q7, Q8). Reason: it targets whole-codebase exploration and is a React app; the owner wants change-focused, feature-level views inside the existing dashboard. It has no sequence diagrams (Q5).
