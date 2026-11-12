# CodeBoarding

- **Type**: tool (static analysis plus LLM architecture maps, CLI and GitHub Action)
- **Repo / site**: https://github.com/CodeBoarding/CodeBoarding , https://codeboarding.org
- **License / cost**: MIT for the engine and CLI (README, "License"). The web platform is a hosted product; its terms are not evaluated. LLM calls cost money.
- **Self-host**: partial. The CLI runs locally (`codeboarding incremental --local`, README); the review UI is a hosted platform.

## Capabilities
- Static analysis with language servers builds a call graph; clustering groups it; LLM agents name and describe nested components (README intro; `static_analyzer/`, `agents/`).
- Output `.codeboarding/analysis.json` with components, relations and key entities with file and line ranges.
- Incremental analysis from content hashes against a baseline (`README.md` "Incremental needs a baseline"; `codeboarding_workflows/fingerprint_diff.py`).
- GitHub Action posts architecture-change maps on PRs; "Review the change, not the diff" (README).
- Languages include Rust (README, "Languages").
- Render to md, html, mdx, rst, Mermaid (`codeboarding_cli/render.py`).

## Development resources
- Deep analysis: [`../../../inspiration-repos/analysis/codeboarding.md`](../../../inspiration-repos/analysis/codeboarding.md).
- https://codeboarding.org/getting-started : getting started.

## Alternatives
- [understand-anything.md](understand-anything.md), [llm-repo-explainers.md](llm-repo-explainers.md).

## Recommendation
**BUILD** our own, **USE** its pipeline shape: static facts first, clustering, LLM naming, key entities as grounding, content-hash incremental update (Q2, Q3, Q7). Run it once on config-harness as a baseline to compare component quality (a spike, not a dependency). Reason: Python and LSP orchestration across many languages is more than a Rust-only first target needs, and the review UI is hosted.
