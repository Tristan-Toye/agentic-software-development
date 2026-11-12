# Q3. Extraction from Rust code

- **Question**: How does the system get facts from a Rust workspace, and what may the LLM do with them?
- **Status**: PROPOSED.
- **Target**: config-harness, a Cargo workspace with 10 members (`protocol`, `fault`, `step-timing`, `harness`, `graph-runtime`, `platform`, `test-support`, `composition-root`, `sandbox-tool`, `workspace-hack`) ([current-state.md](current-state.md), A3).

## Facts: the extraction options

From [rust-extraction](../sources/rust-extraction.md):

| Option | Level | Name resolution | Macros | Output | Stability |
|---|---|---|---|---|---|
| `cargo metadata` | crate | n/a | n/a | JSON | stable |
| cargo-modules binary | module, item | yes | yes | DOT, text (no JSON) | stable CLI |
| `ra_ap_*` own helper | any | yes | yes | any, such as JSON | breaks on each release |
| rustdoc JSON | public API item | yes | yes | JSON | unstable, nightly only; no call graph |
| `syn` | item, call by name | no | no | any | stable |
| tree-sitter-rust | item, call by name | no | no | any | stable |
| rust-analyzer over LSP | symbol, call hierarchy | yes | yes | JSON-RPC | stable protocol; slow start |

More facts:

- cargo-modules pins `ra_ap_*` exactly (`ra_ap_hir = "=0.0.358"`). The releases come weekly and break often. Pin and update on a schedule ([cargo-modules analysis](../../../inspiration-repos/analysis/cargo-modules.md)).
- cargo-modules filter flags (`--no-fns`, `--no-traits`, `--no-types`, `--focus-on`, `--max-depth`, `--cfg-test`) map to abstraction levels. It has cycle detection. Its licence is MPL-2.0. Calling the binary or linking the crates is fine; copied files stay MPL ([cargo-modules analysis](../../../inspiration-repos/analysis/cargo-modules.md)).
- Syntax-only tools do not see macro-generated code or dynamic dispatch. Mark trait-object edges `dispatch` ([rust-extraction](../sources/rust-extraction.md)).
- cargo-depgraph is archived. Use `cargo metadata` instead ([rust-extraction](../sources/rust-extraction.md)).
- SCIP output from rust-analyzer may replace a `ra_ap_*` helper for the reference layer. It is untested ([semantic-code-graph-engines](../sources/semantic-code-graph-engines.md)). That source recommends skipping tree-sitter-graph, stack-graphs and CodeQL for the core.
- CodeBoarding and Understand-Anything show the same order of work: deterministic facts first, LLM naming second ([codeboarding analysis](../../../inspiration-repos/analysis/codeboarding.md), [understand-anything analysis](../../../inspiration-repos/analysis/understand-anything.md)).
- A static call graph from rust-analyzer gives caller and callee edges. Order comes from source order inside a function body. Runtime traces (OpenTelemetry) cover only exercised paths ([trace-to-sequence-and-sequence-generators](../sources/trace-to-sequence-and-sequence-generators.md)). This source is thin: see [gaps.md](../../../research/gaps.md).
- Folders are weak cues for components: ASNER adds only "weak directory cues" to its dependency graph ([asner-architecture-recovery](../papers/asner-architecture-recovery.md), p1). That crate boundaries are strong cues is an inference for Rust. ASNER does not test it. (Corrected in the audit of 2026-11-12.)

## Proposed layers

| Layer | Question | Tool | Gives |
|---|---|---|---|
| 1 Crate | Which crates, which edges? | `cargo metadata` | C4 container level |
| 2 Module | Which modules, which `use` edges? | `cargo-modules dependencies` (quick start), then an `ra_ap_*` helper that writes JSON (long term) | Component level |
| 3 API | Which public traits, structs, signatures? | rustdoc JSON on the repo nightly, if needed | Evidence for L3 |
| 4 Calls | Who calls whom, in what order? | rust-analyzer call hierarchy over LSP, asked only for the functions in the change | Flow steps |
| Fallback | Files that rust-analyzer cannot load | tree-sitter-rust | Items only |

All tool versions are pinned. This is the layered recommendation from [rust-extraction](../sources/rust-extraction.md).

**The "after" side at Phase 3 comes from stubs and text.** At the end of Phase 3 the contract exists as compiling stub files ([current-state.md](current-state.md), B1). Stub bodies are placeholders (inference: see the stub-materialiser role in the plugin agent list). So layer 4 gives no call edges out of new functions. New flow edges come from the dossier text and the work-package list. They carry `resolution: inferred`. This is a limit, and the spike must measure how many flow edges stay inferred.

## What the LLM may do

The pattern: facts from tools, abstraction by the LLM as a filter, behaviour by a fixed procedure ([sad-from-source-reverse-eng-llm](../papers/sad-from-source-reverse-eng-llm.md); [llm-recovery-ros2-multilevel](../papers/llm-recovery-ros2-multilevel.md)).

The LLM may:

1. **Select** the significant parts of an extracted graph. The filter cannot invent components ([sad-from-source-reverse-eng-llm](../papers/sad-from-source-reverse-eng-llm.md)).
2. **Name and group** modules into product features and write one-line product descriptions. The per-file "responsibility" text is the semantic signal ([asner-architecture-recovery](../papers/asner-architecture-recovery.md)). CodeBoarding names clusters the same way ([codeboarding analysis](../../../inspiration-repos/analysis/codeboarding.md)).
3. **Write the typed graph**: groups, nodes, edges. Code compiles the picture, so the LLM cannot produce a broken diagram ([gitdiagram analysis](../../../inspiration-repos/analysis/gitdiagram.md)).
4. **Propose the new nodes and edges** of the planned change from the dossier. Each one is `inferred` until a stub signature proves it.
5. **Order flow steps** when the extractor gives no order. Mark them `inferred`.

The LLM may not:

1. Add a node or edge that has no code path, no stub, and no dossier text behind it. A node needs a repository `path`. An edge needs evidence paths ([gitdiagram analysis](../../../inspiration-repos/analysis/gitdiagram.md)).
2. Cite evidence it was not shown. The validator drops a cited file the model never saw ([gitdiagram analysis](../../../inspiration-repos/analysis/gitdiagram.md)).
3. Retype code. The build copies `code` from the real file by `source.file` and `source.lines` ([openarc analysis](../../../inspiration-repos/analysis/openarc.md)).
4. Change a component assignment without traceable code evidence ([asner-architecture-recovery](../papers/asner-architecture-recovery.md)).
5. Upgrade an evidence tier. Tiers never move up ([mermaidiff analysis](../../../inspiration-repos/analysis/mermaidiff.md)).
6. Claim something the code does not prove. Put it in one `Not checked:` line ([mermaidiff analysis](../../../inspiration-repos/analysis/mermaidiff.md)).

## Checks run by script, not by the LLM

- Schema, unknown IDs, `source.lines` outside the file. The agent loops until the build passes ([openarc analysis](../../../inspiration-repos/analysis/openarc.md)).
- Typed issues (`duplicate_node_id`, `unknown_edge_source`, `missing_evidence_path`) fed back as repair prompts, with a bounded retry count ([gitdiagram analysis](../../../inspiration-repos/analysis/gitdiagram.md)).
- A validator that is a separate role from the generator ([agenticakm](../papers/agenticakm.md); [understand-anything analysis](../../../inspiration-repos/analysis/understand-anything.md), graph-reviewer).
- The config-harness anchor check: each `path:line` anchor must exist and the backticked identifier must still be on that line ([current-state.md](current-state.md), A2).
- Diagram quality is a named weakness of LLM output even in an accepted tool, so a repair loop is needed ([ciao-architecture-documentation](../papers/ciao-architecture-documentation.md)).

## Inferred-edge marking

- Draw `inferred` and `dispatch` edges differently from `static` edges. OpenArc says renderers must do this ([openarc analysis](../../../inspiration-repos/analysis/openarc.md)).
- Show the evidence tier as a badge on each element ([mermaidiff analysis](../../../inspiration-repos/analysis/mermaidiff.md)).
- Count inferred elements in the one-screen summary: "4 of 14 elements are inferred" (inference).

## Caveats

- No Rust benchmark for LLM recovery was found. The papers test C, C++, Java, Python, C++ and ROS 2 ([asner-architecture-recovery](../papers/asner-architecture-recovery.md), [sad-from-source-reverse-eng-llm](../papers/sad-from-source-reverse-eng-llm.md), [llm-recovery-ros2-multilevel](../papers/llm-recovery-ros2-multilevel.md)).
- Feature location results are a seed. Store which signal produced each link: trace, static or text ([feature-location-survey-dit-2013](../papers/feature-location-survey-dit-2013.md)). The survey predates LLM techniques.
- No benchmark was run in this research for layout or extraction speed.

## Recommendation

**BUILD** a layered extractor: `cargo metadata` for crates, `cargo-modules` (binary, then an `ra_ap_*` JSON helper) for modules, rust-analyzer call hierarchy for flow steps, rustdoc JSON only if layer 3 is needed.

**USE** tree-sitter-rust as a fallback only.

**USE** the LLM for selection, naming, grouping and planned-change proposals. **SKIP** letting it add unmarked facts.

**USE** the OpenArc build check and the GitDiagram typed-issue repair loop as the gate.

**SKIP** the code-graph MCP servers and CodeQL-class engines as dependencies. Spike crabmap on config-harness once.

## Sources

- Sources: [rust-extraction](../sources/rust-extraction.md), [semantic-code-graph-engines](../sources/semantic-code-graph-engines.md), [code-graph-mcp-servers](../sources/code-graph-mcp-servers.md), [trace-to-sequence-and-sequence-generators](../sources/trace-to-sequence-and-sequence-generators.md), [llm-repo-explainers](../sources/llm-repo-explainers.md).
- Analyses: [cargo-modules](../../../inspiration-repos/analysis/cargo-modules.md), [openarc](../../../inspiration-repos/analysis/openarc.md), [gitdiagram](../../../inspiration-repos/analysis/gitdiagram.md), [codeboarding](../../../inspiration-repos/analysis/codeboarding.md), [understand-anything](../../../inspiration-repos/analysis/understand-anything.md), [mermaidiff](../../../inspiration-repos/analysis/mermaidiff.md).
- Papers: [sad-from-source-reverse-eng-llm](../papers/sad-from-source-reverse-eng-llm.md), [asner-architecture-recovery](../papers/asner-architecture-recovery.md), [archagent-legacy-recovery](../papers/archagent-legacy-recovery.md), [llm-recovery-ros2-multilevel](../papers/llm-recovery-ros2-multilevel.md), [agenticakm](../papers/agenticakm.md), [ciao-architecture-documentation](../papers/ciao-architecture-documentation.md), [feature-location-survey-dit-2013](../papers/feature-location-survey-dit-2013.md).
- Synthesis input: [current-state.md](current-state.md).
