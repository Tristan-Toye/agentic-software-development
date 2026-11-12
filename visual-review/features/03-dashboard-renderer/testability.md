# Testability

- **Server layout**: unit test with fixture graphs. Check that output has `x`, `y`, `width`, `height` for each node and that positions do not overlap. Time it at 50, 100, 200 nodes. This is the D0 benchmark that the sources say has not run ([browser-layout-engines](../../research/sources/browser-layout-engines.md)).
- **Client**: the corpus does not say how the dashboard page is tested. Use a headless browser run on the page, or a DOM test of the builder function with a fake document. Decide in the dossier.
- **Page rules**: a test that fails if the diagram code path calls `innerHTML` (static scan) and a runtime test with the hostile label (S3).
- **Network rule**: run the page in a browser with an external-request blocker, or read the network log.
- **Accessibility**: DOM attributes for the second mark channel can be read by a test. Contrast checking exists in OpenArc (`tools/contrast.ts`) and may be copied ([openarc analysis](../../../inspiration-repos/analysis/openarc.md)).
- **Open risk**: elkjs on Bun is claimed but not tested in the corpus ([Q5](../../research/synthesis/q5-rendering-stack.md)).
