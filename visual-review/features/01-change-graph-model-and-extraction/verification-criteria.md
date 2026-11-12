# Verification criteria

Each criterion can fail. "Fail" says what counts as failure.

| ID | Criterion | How to check | Fail when |
|---|---|---|---|
| VC-01 | On config-harness at one fixed commit, the crate nodes equal the workspace members from `cargo metadata`. | Compare the two sets by name. | The sets differ in any name. (At the 2026-11-12 checkout the count is 10.) |
| VC-02 | Every crate edge equals a resolved workspace dependency in `cargo metadata`. | Compare edge sets. | Any extra or missing edge. |
| VC-03 | The validator rejects one fixture for each of: `duplicate_node_id`, `unknown_group_id`, `missing_repository_path`, `missing_evidence_path`, `unknown_edge_source`, `unknown_edge_target`, `line_range_outside_file`, `over_budget`. | Run the validator on 8 fixtures. | Any fixture passes, or the printed issue name differs. |
| VC-04 | Every node and edge that is not `inferred` has evidence that exists. | Run the validator plus the config-harness anchor checker on the output. | One anchor does not resolve. |
| VC-05 | Two runs on one commit give byte-identical output. | `cmp` the two files. | Any byte differs. |
| VC-06 | For one crate, the module-graph edges agree with the c3 file that `module_graph.py check` guards. | Compare edge sets. | Agreement is below 95 percent of edges. (95 is a proposed threshold; the spike may change it.) |
| VC-07 | No node comes from a `#[cfg(test)]` module with default options. | Fixture with a test module. | A test node appears. |
| VC-08 | Crate and module layers finish within a time limit fixed after the spike. | Time on config-harness. | Time exceeds the limit. Limit: to be set by D0 (no measurement exists yet). |
| VC-09 | The output metadata holds the version of every tool used and the commit. | Read the metadata. | A tool or the commit is missing. |
| VC-10 | A trait-object call gets `resolution: dispatch`. | Fixture. | The edge says `static`. |
