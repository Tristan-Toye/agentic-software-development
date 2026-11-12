# Breaking the Alphabet: Rethinking File Ordering in Code Review

- **Source**: https://arxiv.org/abs/2609.04207
- **PDF**: `sources/file-ordering-code-review.pdf` (file path from the repo root: `visual-review/sources/file-ordering-code-review.pdf`)
- **Relevant to**: Survey evidence on file order and cognitive load. Informs how the dashboard orders changed items.

## Summary
Most code review tools order changed files alphabetically (p1). The paper surveys 1,355 professional developers from 182 open-source projects on how file order affects review (p1). Only 10.2% consider alphabetical order optimal (p1). 57.6% report that it increases context switching, disrupts logical reasoning and adds to review fatigue (p1). 63.9% worry that the default order may cause them to miss bugs (p1). Reviewers ask for dependency-aware grouping and customisable ordering. 66% request customisable ordering (p1). The authors conclude that tools need reviewer-centric designs that fit human cognition (p1). The paper is a survey of perception, not a controlled test of review results.

## Unique insights and approaches
- Order by dependency or by feature, not by name (p1). The dashboard lists changed diagram elements by feature or by flow.
- Context switching is a named cost (p1). Group items that belong to one flow on one screen.
- The evidence is self-reported perception (p1). Treat it as design input and test it in the dashboard.

## Content table for lookup
| Section / topic | Pages | What you find there | Use during design of |
|---|---|---|---|
| Abstract and headline numbers | 1 | 10.2%, 57.6%, 63.9%, 66% | Q9 ordering |
| Method and results | 2+ | Not read in detail for this document; open the PDF for the question list | Survey design |

Page numbers are PDF page numbers of the file in `sources/`.
