# cargo-modules (regexident/cargo-modules)

- **Repo**: https://github.com/regexident/cargo-modules (MPL-2.0, `inspiration-repos/cargo-modules/LICENSE.md`; 1285 stars, pushed 2026-11-09 per the GitHub API)
- **Clone**: `inspiration-repos/cargo-modules/` (version 0.28.0, `Cargo.toml`)
- **Relevant to**: Q2 grounding in code (Rust), Q1 clutter control (filters)

## What it is
A cargo plugin with three commands: `structure` (module tree as text), `dependencies` (item and module dependency graph as Graphviz DOT) and `orphans` (files not linked into the crate) (`README.md`, "Usage"). It uses rust-analyzer as a library, so it sees the same resolved names as the editor.

## Architecture
- `src/analyzer.rs`: loads the Cargo workspace through `ra_ap_load_cargo` and `ra_ap_project_model`, returns an `hir::Crate` and an `AnalysisHost`. rust-analyzer crates are pinned exactly (`Cargo.toml`: `ra_ap_hir = "=0.0.358"` and others).
- `src/graph/builder.rs`: `GraphBuilder::build` walks the crate and builds a `petgraph` graph of `Node` and `Edge`; `src/graph.rs` holds `Relationship` (owns, uses).
- `src/item.rs`: `Item` wraps `hir::ModuleDef` with visibility, attributes, kind name.
- `src/command/dependencies/`: `filter.rs`, `options.rs`, `printer.rs` (DOT output), `cycles/` (cycle detection, tri-colour DFS).
- `src/command/structure/`: tree printer with `--focus-on` and `--max-depth`.
- Tests with sample projects in `tests/projects/`.

## Ideas worth borrowing
- **Use `ra_ap_*` crates as the Rust extraction engine** (`src/analyzer.rs`): a small Rust helper can load the config-harness workspace and emit resolved items and `use` edges as JSON. This is the most accurate extraction for Rust (Q2).
- **Filter flags as the clutter control** (`README.md`, `dependencies --help`): `--no-fns`, `--no-traits`, `--no-types`, `--no-externs`, `--no-sysroot`, `--focus-on <path>`, `--max-depth <n>`. These map to abstraction levels: crate, module, item (Q1).
- **`--focus-on` with `--max-depth`** (`README.md`, `structure --help`): a drill-down query on the graph. Copy the semantics for the dashboard's "expand this node" action (Q6).
- **Cycle detection** (`src/command/dependencies/cycles/`): flags dependency cycles. Good for a risk note on architecture diagrams.
- **`--cfg-test` switch** (`src/analyzer.rs`, `LoadOptions`): excludes or includes test code. Needed because feature diagrams should omit tests by default.
- **Output to DOT with theme** (`src/command/dependencies/theme.rs`, `printer.rs`): shows the node and edge attributes that make a readable graph.

## What not to copy
- The output (DOT only, no JSON): write a JSON emitter on the same `petgraph` graph instead of parsing DOT.
- Item-level graphs as the default view: they are the cluttered view the owner dislikes. Use them only as the lowest drill-down level.
- Pinned rust-analyzer API: `ra_ap_*` releases weekly and breaks often; pin and update on a schedule (Q7).

## Related
cargo-depgraph (jplatte/cargo-depgraph) draws crate-level graphs from `cargo metadata`; the repo is archived (GitHub API, `archived: true`). Use `cargo metadata` directly instead. See `../../visual-review/research/sources/rust-extraction.md`.

## License
MPL-2.0 (file-level copyleft). Calling the binary or linking the crates is fine. Copied files must stay MPL.
