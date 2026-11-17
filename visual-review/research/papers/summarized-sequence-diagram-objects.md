# Constructing Object Groups Corresponding to Concepts for Recovery of a Summarized Sequence Diagram

- **Source**: https://arxiv.org/abs/2003.03237 (J. Information Processing 29, DOI 10.2197/ipsjjip.29.305)
- **PDF**: `sources/summarized-sequence-diagram-objects.pdf` (file path from the repo root: `visual-review/sources/summarized-sequence-diagram-objects.pdf`)
- **Relevant to**: Summarising a reverse-engineered sequence diagram by grouping objects into domain concepts. Informs the horizontal abstraction of sequence diagrams.

## Summary
Reverse-engineered sequence diagrams from execution traces become very large (p1). The authors propose an object grouping technique that summarises the diagram horizontally (p1). Object groups follow Pree's meta patterns, and each group corresponds to a concept in the domain of the system (p1). The diagram shows interactions only among important groups (p1). The technique outperforms a state-of-the-art trace summarisation method in reducing the horizontal size (p1). With fewer than 30 lifelines, the F-score is 0.670 and the recall is 0.793, against 0.421 and 0.670 for the earlier method (p1). The paper also reports the runtime overhead (p1).

## Unique insights and approaches
- Reduce the number of lifelines by grouping objects into concepts. Do not hide messages (p1). In a sequence diagram of a feature, one lifeline can stand for a subsystem.
- A target of fewer than 30 lifelines is used as the size limit (p1). Set an explicit size budget for each diagram and abstract until it fits.
- Concept groups are domain concepts, so the diagram matches the language of the product (p1). This links to Q3.

## Content table for lookup
| Section / topic | Pages | What you find there | Use during design of |
|---|---|---|---|
| Abstract and results | 1 | Technique and F-score | Q5 |
| Method and evaluation | 2-16 | Grouping by meta patterns; comparison | Q1, Q5 |

Page numbers are PDF page numbers of the file in `sources/`.
