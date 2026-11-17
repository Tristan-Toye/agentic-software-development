# Verification criteria

Each criterion can fail. "Fail" says what counts as failure.

| ID | Criterion | How to check | Fail when |
|---|---|---|---|
| VC-01 | On config-harness at one fixed commit, the crate nodes equal the workspace members from `cargo metadata`. | Compare the two sets by name. | The sets differ in any name. (At the 2026-11-12 checkout the count is 10.) |
| VC-02 | Every crate edge equals a resolved workspace dependency in `cargo metadata`. | Compare edge sets. | Any extra or missing edge. |
| VC-03 | The validator rejects one fixture for each of: `duplicate_node_id`, `unknown_group_id`, `missing_repository_path`, `missing_evidence_path`, `unknown_edge_source`, `unknown_edge_target`, `line_range_outside_file`, `over_budget`. | Run the validator on 8 fixtures. | Any fixture passes, or the printed issue name differs. |
| VC-04 | Every node and edge that is not `inferred` has evidence that exists. | Run the validator plus the config-harness anchor checker on the output. | One anchor does not resolve. |
| VC-05 | Two runs on one commit give byte-identical output. | `cmp` the two files. | Any byte differs. |
| VC-06 | For one crate, the module-graph edges agree with the c3 file that `module_graph.py check` guards. Parsing requirements: cargo-modules runs for the linux target with all features and without tests (R1-R3); a module whose node a cfg gate drops is read from a second `--cfg-test` run (R4); an inline module rolls up to its declared file module (R5); the lib target only (R6); the c3 side resolves every `use` form and re-export to the defining module, counts types named in signatures and fields, and ignores fn bodies, `cfg(test)` and `cfg(doc)`; a const, static or macro edge is `-.->` and not counted. | `vr_c3_agree.py REPO --crate NAME` compares edge sets. A /work-on session on a Rust repo with c3 files runs it on every crate its diff touched, with the generator freshness check, as `validate_pipeline.py --c3-gate` before it closes. | Agreement is below 95 percent of edges, or a touched crate's c3 file is not what `module_graph.py` prints. The fix is `module_graph.py regen` or a parser repair, never a lower threshold. (95 is the threshold; all six config-harness crates reach 100.) |
| VC-07 | No node comes from a `#[cfg(test)]` module with default options. | Fixture with a test module. | A test node appears. |
| VC-08 | Crate and module layers finish within a time limit fixed after the spike. | Time on config-harness. | Time exceeds the limit. Limit: to be set by D0 (no measurement exists yet). |
| VC-09 | The output metadata holds the version of every tool used and the commit. | Read the metadata. | A tool or the commit is missing. |
| VC-10 | A trait-object call gets `resolution: dispatch`. | Fixture. | The edge says `static`. |
