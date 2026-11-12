# Testability

## Test levels

- **Unit**: schema validation with small fixture graphs (one fixture per issue type). No tool needed.
- **Integration**: the extractor on small fixture workspaces. `cargo-modules` keeps sample projects in `tests/projects/` ([cargo-modules analysis](../../../inspiration-repos/analysis/cargo-modules.md)); the same idea fits here.
- **System**: the extractor on the real config-harness checkout, compared with `cargo metadata` output and with the c3 files that `module_graph.py check` already guards ([current-state](../../research/synthesis/current-state.md), A2).

## What is easy to test

- Crate layer: the oracle is `cargo metadata`, a stable JSON output ([rust-extraction](../../research/sources/rust-extraction.md)).
- Validator: pure function of the graph.

## What is hard to test

- The call layer. It needs rust-analyzer over LSP. Start-up time is slow, and the results depend on the rust-analyzer version. Pin the version and keep the expected output beside it.
- Macro-generated code. Syntax-only tools do not see it ([rust-extraction](../../research/sources/rust-extraction.md)).
- Stub bodies have no calls, so layer 4 gives no outgoing edges for new functions. A test must show that those edges are absent, not guessed ([Q3](../../research/synthesis/q3-extraction-from-rust.md)).

## Spike D0 inputs

Run `cargo-modules` and crabmap on config-harness and compare. Measure extractor time per layer. These numbers fix the time limit in VC-08.
