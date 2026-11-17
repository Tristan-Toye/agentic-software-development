# Expectations, Outcomes, and Challenges of Modern Code Review

- **Source**: https://2013.icse-conferences.org/content/expectations-outcomes-and-challenges-modern-code-review.html (ICSE 2013, DOI 10.1109/ICSE.2013.6606617)
- **PDF**: `sources/modern-code-review-bacchelli-bird-2013.pdf` (file path from the repo root: `visual-review/sources/modern-code-review-bacchelli-bird-2013.pdf`)
- **Relevant to**: Foundational study at Microsoft: understanding the change is the main review challenge. Gives the problem statement for visual aids.

## Summary
The authors study tool-based review at Microsoft. They observe, interview and survey developers and manually classify review comments (p1-p4, per the ICSE page). Developers state that finding defects is the main motivation. Reviews give fewer defect outcomes than expected. They give knowledge transfer and team awareness instead (p4-p7). The paper reports that understanding is the main challenge. Interviewees say the hardest part is understanding the reason of the change (p7). One tester says the biggest information need is what instigated the change (p7). Section VI.A has the evidence for this theme (p7). The study states that the mechanisms developers use to understand are mostly not supported by tools (abstract, p1).

## Unique insights and approaches
- Understanding, not defect finding, is the main review difficulty (p7). A pre-build diagram targets this directly: it shows why and what before the code.
- Reviewers need the reason for the change (p7). Each diagram change should link to the feature or requirement that causes it. This links to Q3.
- Knowledge transfer and awareness are real outcomes (p4-p7). A living map of the product can serve the same purpose for the owner.

## Content table for lookup
| Section / topic | Pages | What you find there | Use during design of |
|---|---|---|---|
| Methodology | 2-4 | Observation, interviews, affinity diagram, survey | Evidence base |
| Why programmers review | 4-6 | Defects, improvement, alternatives, knowledge transfer | Q9 |
| Outcomes of review | 6-7 | Expectation versus reality | Q9 |
| Challenges: review is understanding | 7-8 | Quotes on understanding the change | Problem statement |

Page numbers are PDF page numbers of the file in `sources/`.
