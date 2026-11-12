# Techniques for Reducing the Complexity of Object-Oriented Execution Traces

- **Source**: https://webhome.cs.uvic.ca/~mstorey/vissoft2003/submissions/HamouLhadjReducingComplexity.pdf (VISSOFT 2003 submission)
- **PDF**: `sources/trace-complexity-reduction-hamou-lhadj-2003.pdf` (file path from the repo root: `visual-review/sources/trace-complexity-reduction-hamou-lhadj-2003.pdf`)
- **Relevant to**: Early work on trace compression: remove utilities and repeated call subtrees. Informs how to keep sequence views short.

## Summary
Understanding object-oriented behaviour from source alone is hard, and traces are very large (p1, p2). The paper presents trace compression techniques. They preprocess the trace and remove utilities, which are widely used methods (p2). They also use object-oriented concepts to group call subtrees as instances of the same comprehension unit (p2, p3). The experiment uses several traces (p3). Some traces have over 100,000 lines but only a small number of distinct methods. For example, trace 6 has only 65 distinct methods (p4). The authors conclude that traces contain many repetitions from loops and repeated call sequences (p4).

## Unique insights and approaches
- Repeated sequences dominate traces (p4). Collapse repeated call sequences into one counted element.
- Utility removal is a stated step (p2). Filter helper calls such as logging and conversion from a feature-level sequence view.
- The compression ratio is defined over comprehension units (p2). This gives a measurable target for a summary step.

## Content table for lookup
| Section / topic | Pages | What you find there | Use during design of |
|---|---|---|---|
| The size problem | 2 | Why traces grow | Q5 |
| Trace compression techniques | 2-3 | Preprocessing, utility removal, OO-based grouping | Q5, Q1 |
| Experiment and results | 3-4 | Traces and ratios | Q5 |

Page numbers are PDF page numbers of the file in `sources/`.
