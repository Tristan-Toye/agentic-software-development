# Benchmarking and Evaluating VLMs for Software Architecture Diagram Understanding

- **Source**: https://arxiv.org/abs/2604.04009
- **PDF**: `sources/vlm-architecture-diagram-understanding.pdf` (file path from the repo root: `visual-review/sources/vlm-architecture-diagram-understanding.pdf`)
- **Relevant to**: Benchmark of how well vision models read architecture diagrams. Informs whether a model can check its own rendered diagram, and why to keep a structured form.

## Summary
SADU is a benchmark with 154 curated diagrams of behavioural, structural and ER types (p1). It has structured annotations and 2,431 question-answer tasks for counting and retrieval (p1). The authors test 11 vision-language models from the Gemini, Claude, GPT and Qwen families (p1). The best model reaches 70.18% accuracy. The weakest, gpt-4o-mini, reaches 17.77% (p1). ER diagrams are the easiest. Behavioural diagrams are harder and show larger gaps between models (p2). The weak points are diagram reasoning and visual relation grounding (p1). The authors note that a stronger model does not always do better on every diagram type (p2).

## Unique insights and approaches
- Models misread relations in rendered diagrams (p1). Do not use a picture of the diagram as the only record. Keep the diagram as structured data (nodes and edges) and verify against that data (Q8).
- Behavioural diagrams are the hardest (p2). Sequence diagrams need extra checks, for example a trace-based check of each message.
- Counting and retrieval errors are common (p1). A reviewer or a model that reads a large diagram will err. This is a further reason for small, layered diagrams (Q1).

## Content table for lookup
| Section / topic | Pages | What you find there | Use during design of |
|---|---|---|---|
| Benchmark design | 1-3 | 154 diagrams, 2,431 questions | Q8 |
| Model results | 1-2 and later | Accuracy by model and by diagram type | Q8 |
| Analysis of failures | 2+ | Relation grounding, behavioural gap | Q5 |

Page numbers are PDF page numbers of the file in `sources/`.
