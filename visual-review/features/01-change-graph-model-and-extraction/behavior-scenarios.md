# Behaviour scenarios

**S1. Crate layer.**
Given a Cargo workspace with 10 members,
when the extractor runs the crate layer,
then the graph has one crate node per workspace member and one edge per resolved workspace dependency, each with `resolution: static` and evidence pointing to the `Cargo.toml` line.

**S2. Module layer with test code excluded.**
Given a crate with a `#[cfg(test)]` module,
when the module layer runs with default options,
then the graph has no node from the test module.

**S3. Dynamic dispatch.**
Given a call through a trait object,
when the call layer runs,
then the edge has `resolution: dispatch`, not `static`.

**S4. Bad graph is refused.**
Given a graph with an edge whose target ID does not exist,
when the validator runs,
then it exits with a non-zero code and prints the issue `unknown_edge_target` with the edge ID.

**S5. Line outside the file.**
Given a node whose `source.lines` range ends after the last line of the file,
when the validator runs,
then it reports `line_range_outside_file` and exits non-zero.

**S6. Over budget.**
Given a view with 16 feature nodes and a budget of 15,
when the validator runs,
then it reports `over_budget` for that view.

**S7. Tool missing.**
Given `cargo-modules` is not installed,
when the module layer runs,
then the extractor stops with a message that names the missing tool and its pinned version. It does not fall back silently to a weaker layer.

**S8. Determinism.**
Given the same commit and the same pinned tool versions,
when the extractor runs twice,
then both outputs are byte-identical.
