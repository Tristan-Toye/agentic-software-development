# CodeBoarding (CodeBoarding/CodeBoarding)

- **Repo**: https://github.com/CodeBoarding/CodeBoarding (MIT for the engine and CLI, `inspiration-repos/CodeBoarding/LICENSE`, `README.md` "License"; 2679 stars, pushed 2026-11-12 per the GitHub API)
- **Clone**: `inspiration-repos/CodeBoarding/` (Python, uv)
- **Relevant to**: Q1, Q2, Q3, Q4, Q7, Q8

## What it is
"Review the change, not the diff" (`README.md`). Static analysis extracts code relationships; language models name and describe components. The result is an interactive architecture map with nested components, plus a GitHub Action that posts architecture changes on pull requests. Rust is a supported language (`README.md`, "Languages"). The web platform is a hosted product; the analysis engine and CLI are open source.

## Architecture
- `static_analyzer/`: language-server clients (`lsp_client/`), call-graph and control-flow structures (`cfg/`), clustering (`clustering/`), per-language config scanners, `analysis_cache.py`, `incremental_orchestrator.py`.
- `agents/`: LLM agents: `planner_agent.py`, `scope_analysis_agent.py`, `tree_planner_agent.py`, tools `read_file.py`, `get_method_calls.py`; `change_status.py` (added, modified, deleted, unchanged, renamed); `content_hash.py`.
- `diagram_analysis/`: `diagram_generator.py`, `incremental_update.py`, `scope_plan.py`, `analysis_json.py` (output schema), `reference_resolver.py`.
- `codeboarding_workflows/fingerprint_diff.py`: git-free change detection by diffing `{path: sha16}` maps.
- `output_generators/`, `codeboarding_cli/render.py`: render to `md`, `html`, `mdx`, `rst`, Mermaid.
- Output: `.codeboarding/analysis.json` with keys `components`, `components_relations`, `description`, `files`, `metadata`, `methods_index` (checked with `jq` on the committed file `.codeboarding/analysis.json`). Each component lists `key_entities` with `qualified_name`, `reference_file`, `reference_start_line`, `reference_end_line`.

## Ideas worth borrowing
- **Static analysis first, LLM names and groups** (`README.md` intro; `static_analyzer/`, `agents/`): the call graph is clustered (`static_analyzer/clustering/`), then the LLM labels the clusters as components. This is the "map code to features" pattern (Q2, Q3).
- **Component key entities with file and line ranges** (`.codeboarding/analysis.json`): each product component is grounded in named code entities. Copy this as the feature-to-code link (Q3).
- **Hierarchical components with scope-based analysis** (`diagram_analysis/scope_plan.py`, `agents/scope_analysis_agent.py`): a component can be expanded into a deeper level on demand (Q1, Q6).
- **Incremental update against a baseline** (`diagram_analysis/incremental_update.py`, `codeboarding_cli/commands/incremental_analysis.py`, `README.md` "Incremental needs a baseline"): changed files are found by content hash; only affected components are re-analysed (Q7).
- **Analysis committed in the repo** (`.codeboarding/` is tracked, with `fingerprint.json` and `static_analysis.sha`): the map lives beside the code and the sha says if it is stale (Q7).
- **PR architecture comparison** (`.github/workflows/codeboarding.yml`, `README.md` "Review"): changed components and dependencies are shown on the map; this is the post-hoc counterpart of visual-review (Q4).
- **Health report** (`health/`): structural checks on the code, an example of non-LLM metrics next to the map.

## What not to copy
- The LSP orchestration for many languages: very large (`static_analyzer/__init__.py` is 62 KB). For config-harness, Rust only, use rust-analyzer directly (see `cargo-modules.md`).
- The hosted web platform dependency for the review UI (`README.md`): the dashboard must be self-hosted.
- Python as the runtime: the plugin uses Python scripts already, but the dashboard is Bun and TypeScript. Read the algorithms, not the code.
- LLM-named components with no check: add a validation step like `gitdiagram.md` and `openarc.md` describe.

## License
MIT for the engine and CLI.
