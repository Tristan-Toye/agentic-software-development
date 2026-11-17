# Code Review Comprehension: Reviewing Strategies Seen Through Code Comprehension Theories

- **Source**: https://arxiv.org/abs/2503.21455
- **PDF**: `sources/code-review-comprehension-strategies.pdf` (file path from the repo root: `visual-review/sources/code-review-comprehension-strategies.pdf`)
- **Relevant to**: Empirical model of how reviewers understand a change. Informs what the approval page must support.

## Summary
The authors observed and interviewed ten experienced reviewers during 25 reviews from their own queues (p1). They applied Letovsky's code comprehension model in a theory-driven thematic analysis (p1). They extend the model into a Code Review Comprehension Model (p1). Reviewers use opportunistic strategies. These begin with a context-building phase, then code inspection by reading, testing and discussion management (p1). Reviewers build a mental model of the change as an extension of their model of the system. They compare expected and ideal solutions with the actual implementation (p1). Section V.B lists tool recommendations (p10). Reviewers scope their sessions and prioritise activities. Tools can let them view only some aspects of the pull request (p10). The main code reading strategies were chunking and difficulty-based reading (p10).

## Unique insights and approaches
- Review starts with context building (p1). A diagram of the whole feature before the diff serves this phase. The plan diagram should come before the file list.
- Reviewers compare an expected solution with the actual one (p1). A plan diagram and an implementation diagram shown side by side support this comparison.
- Tools should let the reviewer view only certain aspects of a change and state the scope of the review (p10). The dashboard can offer per-feature filters and let the owner mark what was reviewed.
- Chunking is a main reading strategy (p10). Diagram groups by feature are natural chunks.

## Content table for lookup
| Section / topic | Pages | What you find there | Use during design of |
|---|---|---|---|
| Method: interviews and observation | 2-4 | Ten reviewers, 25 reviews, Letovsky model | Evidence base |
| Results: strategies | 5-10 | Context building, inspection, discussion | Q9 flow |
| Implications and recommendations | 10-11 | Tool recommendations | Approval UX (Q9) |

Page numbers are PDF page numbers of the file in `sources/`.
