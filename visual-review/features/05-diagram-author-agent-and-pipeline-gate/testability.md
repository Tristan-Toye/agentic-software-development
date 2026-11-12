# Testability

- Gate: reuse the test style of `validate_pipeline.py` (a fixture build log with and without the line). The corpus does not describe the existing tests, so read them in the dossier.
- Validator: pure script over JSON fixtures. Build a set of bad graphs, one defect each.
- Agent behaviour: not deterministic. Test the output, not the path. Use the eval set of [Q9](../../research/synthesis/q9-measuring-value.md), copied from the mermaidiff method.
- Separation (S5) and read-only (S8): check the payload file list and the agent tool list in its definition file.
- Hard to test: whether the names are in product words. Use a reviewed sample, not an assertion. (Inference.)
