# Purpose

The pipeline needs one agreed data shape for "what the product looks like" before any diagram is drawn. Without it, each later feature invents its own. The shape must carry grounding (where in the code), sureness (how the fact was found) and status (added, changed, removed, context).

The purpose of VR-01 is:

1. Define that shape once. It follows the OpenArc graph schema words for status and `resolution`, and the architecture-check words for evidence ([Q2](../../research/synthesis/q2-diff-model.md)).
2. Fill the shape with facts from tools, not from the LLM ([Q3](../../research/synthesis/q3-extraction-from-rust.md)).
3. Refuse any graph that breaks the shape. The LLM cannot make a broken diagram when the validator is the gate ([Q3](../../research/synthesis/q3-extraction-from-rust.md), GitDiagram pattern).
