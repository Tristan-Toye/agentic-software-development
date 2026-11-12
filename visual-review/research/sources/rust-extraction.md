# Rust code extraction: cargo metadata, cargo-modules, rust-analyzer, rustdoc JSON, syn, tree-sitter

- **Type**: tool (group of extraction options for a Rust Cargo workspace)
- **Repo / site**: https://github.com/regexident/cargo-modules , https://github.com/rust-lang/rust-analyzer , https://doc.rust-lang.org/cargo/commands/cargo-metadata.html , https://github.com/trtyr/crabmap , https://github.com/jplatte/cargo-depgraph
- **License / cost**: cargo-modules MPL-2.0; rust-analyzer Apache-2.0 (and MIT); crabmap MIT; cargo-depgraph has no licence in GitHub metadata and is archived (all GitHub API). All free.
- **Self-host**: yes; all run locally.

## Capabilities
- **`cargo metadata`**: JSON of the workspace: packages, targets, features, resolved dependency graph (https://doc.rust-lang.org/cargo/commands/cargo-metadata.html). Gives the crate level (C4 container level) with no extra tool. config-harness has 10 workspace members (`/Users/tristan.toye/Documents/personal/repos/config-harness/Cargo.toml`).
- **cargo-modules**: module tree, item and `use` dependency graph as DOT, orphan files. Built on `ra_ap_*` crates; filters `--focus-on`, `--max-depth`, `--no-fns`, `--no-traits` (`inspiration-repos/cargo-modules/README.md`, `src/analyzer.rs`). No JSON output.
- **cargo-depgraph**: crate dependency graph as DOT; archived, so skip (GitHub API, `archived: true`).
- **rust-analyzer as a library (`ra_ap_*` crates)**: full name resolution, trait and impl info, call hierarchy, through `ra_ap_load_cargo`, `ra_ap_hir`, `ra_ap_ide` (`inspiration-repos/cargo-modules/Cargo.toml`, versions pinned with `=`). Most accurate, breaks often between releases.
- **rust-analyzer over LSP**: call hierarchy, references and symbols through the language server protocol. Slower per query, no build against internal crates. CodeBoarding uses LSP clients for Rust (`inspiration-repos/CodeBoarding/static_analyzer/lsp_client/`, `README.md` "Languages").
- **rustdoc JSON**: `cargo rustdoc -- -Z unstable-options --output-format json` writes items, signatures, impls and trait relations; unstable, nightly only (https://doc.rust-lang.org/rustdoc/unstable-features.html#-w--output-format-json; crate `rustdoc-types`). Good for the public API; has no call graph.
- **`syn` parse**: pure-Rust AST of each file; no name resolution. Crabmap uses `syn` plus optional rust-analyzer enrichment (https://github.com/trtyr/crabmap README).
- **tree-sitter-rust**: fast, error-tolerant syntax trees from any language host, no name resolution (used by Understand-Anything: `inspiration-repos/Understand-Anything/understand-anything-plugin/packages/core/package.json`).
- **Call graphs**: `cargo-call-stack` builds a whole-program call graph from LLVM IR for embedded targets (https://github.com/japaric/cargo-call-stack; not evaluated). Not suited to this use.
- **Macros and trait objects**: syntax-only tools do not see macro-generated code or dynamic dispatch. rust-analyzer expands macros. Mark trait-object edges as `dispatch` (OpenArc vocabulary, `inspiration-repos/openarc/spec/graph.md` section 6).

## Development resources
- https://rust-analyzer.github.io/book/ : rust-analyzer manual.
- https://crates.io/crates/ra_ap_hir : library crates (version pinned in cargo-modules).
- `inspiration-repos/cargo-modules/src/analyzer.rs` : how to load a workspace.
- Deep analysis: [`../../../inspiration-repos/analysis/cargo-modules.md`](../../../inspiration-repos/analysis/cargo-modules.md).

## Alternatives
- Whole-language LLM or LSP tools: [llm-repo-explainers.md](llm-repo-explainers.md), [code-graph-mcp-servers.md](code-graph-mcp-servers.md).
- Query engines: [semantic-code-graph-engines.md](semantic-code-graph-engines.md).

## Comparison
| Option | Level | Name resolution | Macros | Output | Stability | Cost to adopt |
|---|---|---|---|---|---|---|
| `cargo metadata` | crate | n/a | n/a | JSON | stable | none |
| cargo-modules (binary) | module, item | yes | yes | DOT, text | stable CLI | low; parse DOT |
| `ra_ap_*` own helper | any | yes | yes | any (JSON) | breaks on each release | medium |
| rustdoc JSON | public API item | yes | yes | JSON | unstable, nightly | low to medium |
| `syn` | item, call by name | no | no | any | stable | medium |
| tree-sitter-rust | item, call by name | no | no | any | stable | medium |
| rust-analyzer LSP | symbol | yes | yes | JSON-RPC | stable protocol | medium; start-up time |

## Recommendation
**BUILD** a layered extractor and **USE** existing pieces for each layer. Layer 1, crate level: `cargo metadata` (free, stable). Layer 2, module level: `cargo-modules dependencies` with `--no-fns --no-traits --no-types` per crate, or a small Rust helper on `ra_ap_*` that writes JSON (the helper is the long-term choice; DOT parsing is the quick start). Layer 3, public API and traits: rustdoc JSON, pinned to the repo's nightly if needed. Layer 4, function calls for sequence diagrams: rust-analyzer call hierarchy over LSP, queried only for the functions in a planned change (Q2, Q5). Use tree-sitter only as a fallback for files rust-analyzer cannot load. An LLM then names features and groups modules (Q3), and every edge keeps its `static`, `dispatch` or `inferred` mark. Pin all tool versions (Q7).
