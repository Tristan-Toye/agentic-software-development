# inspiration-repos

External repos kept for reference when you design the visual-review capability. Clones are
gitignored and local only; the analysis documents and this file are tracked. Each
subdirectory is its own independent project. Do not edit a clone.

**Read the analysis first.** Each repo has a deep-analysis document in `analysis/`. It maps
each idea to exact file paths in the clone. Open the clone only when the analysis points you
there. To restore a missing clone, run `git clone --depth 1 <repo link>` into this folder.

Design-question IDs: Q1 abstraction levels and clutter control; Q2 grounding in code;
Q3 mapping code to features; Q4 diagram diffs; Q5 sequence diagrams; Q6 interactivity;
Q7 living sync; Q8 LLM accuracy; Q9 review and approval UX.

## Routing table

| Repo | What it is | Why it is here | Read when | Analysis |
|---|---|---|---|---|
| `openarc/` (hvess/openarc, MIT) | Agent plugin: a code change becomes one interactive architecture HTML page | Closest match to the goal: change graph with added, changed, removed status; build-time checks; ELK layout | You design the diagram data model, the diff status, or the agent-to-checker loop (Q2, Q4, Q8, Q9) | [`analysis/openarc.md`](analysis/openarc.md) |
| `mermaidiff/` (osmangoninahid/mermaidiff, MIT) | Agent skill: a change becomes a Mermaid sequence diff | Working sequence-diff format, evidence tiers, a scored eval harness | You design sequence diagrams, diff colouring, the false-positive rules, or an eval (Q4, Q5, Q8) | [`analysis/mermaidiff.md`](analysis/mermaidiff.md) |
| `likec4/` (likec4/likec4, MIT) | Architecture-as-code: model, views, React diagram, CLI, MCP | Has model diffs, compare views (as-is, changes, to-be), view predicates, sequence layout | You design views, abstraction levels, or the diff of a model (Q1, Q4, Q5, Q6) | [`analysis/likec4.md`](analysis/likec4.md) |
| `Understand-Anything/` (Egonex-AI/Understand-Anything, MIT) | Agent plugin: codebase knowledge graph, layers, domains, dashboard | Fingerprints, staleness, graph-reviewer agent, diff overlay, domain view | You design the living map, incremental update, or LLM graph validation (Q3, Q7, Q8) | [`analysis/understand-anything.md`](analysis/understand-anything.md) |
| `CodeBoarding/` (CodeBoarding/CodeBoarding, MIT) | Static analysis plus LLM: nested component maps and PR architecture diffs | Components grounded in entities with file and line ranges; hash-based incremental update | You design feature-to-code mapping or the sync step (Q1, Q3, Q7) | [`analysis/codeboarding.md`](analysis/codeboarding.md) |
| `gitdiagram/` (ahmedkhaleel2004/gitdiagram, MIT) | Web service: repo to Mermaid diagram | The LLM writes a typed graph, code validates and compiles it; evidence paths on edges | You design the LLM output schema and the repair loop (Q2, Q8) | [`analysis/gitdiagram.md`](analysis/gitdiagram.md) |
| `cargo-modules/` (regexident/cargo-modules, MPL-2.0) | Cargo plugin on rust-analyzer: module tree, dependency graph, orphans | Shows how to load a Cargo workspace with `ra_ap_*` crates and filter the graph | You write the Rust extractor (Q1, Q2) | [`analysis/cargo-modules.md`](analysis/cargo-modules.md) |
| `elkjs/` (kieler/elkjs, EPL-2.0) | Eclipse Layout Kernel for JavaScript | The layout engine for nested, directed diagrams; runs in Bun or the browser | You choose or tune layout (Q1, Q6) | [`analysis/elkjs.md`](analysis/elkjs.md) |
| `xyflow/` (xyflow/xyflow, MIT) | Node-based UI library; `@xyflow/system` is framework-free | Pan, zoom, drag, minimap code that a hand-written DOM UI can reuse or copy | You build the interactive canvas (Q6) | [`analysis/xyflow.md`](analysis/xyflow.md) |
| `structurizr/` (structurizr/structurizr, Apache-2.0) | C4 reference implementation: DSL, JSON model, exporters, MCP | Vocabulary of C4 levels, dynamic views, workspace JSON, inspections | You name abstraction levels or pick a model format (Q1, Q5) | [`analysis/structurizr.md`](analysis/structurizr.md) |

## Related research

Source documents for tools that are not cloned are in
`../visual-review/research/sources/` (index in `README.md` there).
