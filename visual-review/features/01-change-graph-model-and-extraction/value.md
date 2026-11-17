# Value

- **For the owner**: every box on a later diagram points to code. The owner can ask "where is this?" and get a `path:line`.
- **For the pipeline**: a stable, checked input for the diagram-author agent. The agent does not invent facts.
- **For the cost**: the deterministic layer is free of LLM tokens. CodeBoarding and Understand-Anything use the same order: static first, LLM second ([Q3](../../research/synthesis/q3-extraction-from-rust.md)).
- **For the risk**: vision models misread diagrams (best of 11 scored 70.18 percent), so the structured graph, not the picture, is the record ([Q1](../../research/synthesis/q1-representations.md), fact 7).

Not claimed: that the graph makes review better. That is measured in [Q9](../../research/synthesis/q9-measuring-value.md).
