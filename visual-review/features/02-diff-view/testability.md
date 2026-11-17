# Testability

- Pure functions of two graphs. Table-driven tests with small JSON fixtures. No tool, no network.
- Property tests: for any pair of graphs, `to-be` of the merged graph equals the planned graph (minus context-only nodes), and `as-is` equals the before graph restricted to the change scope.
- Layout stability needs a layout engine (VR-03). Test S5 there with the ELK output; in VR-02 test only that the merged graph is one structure.
- Flow diff: mermaidiff has an eval harness with 10 cases and expected results ([mermaidiff analysis](../../../inspiration-repos/analysis/mermaidiff.md)). Copy the method for flows.
- Hard to test: rename detection. The corpus has no solution ([Q2](../../research/synthesis/q2-diff-model.md), open points). A test must pin the current behaviour (one removed, one added).
