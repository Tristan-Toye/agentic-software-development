# CIAO - Code In Architecture Out: Automated Software Architecture Documentation with Large Language Models

- **Source**: https://arxiv.org/abs/2604.08293
- **PDF**: `sources/ciao-architecture-documentation.pdf` (file path from the repo root: `visual-review/sources/ciao-architecture-documentation.pdf`)
- **Relevant to**: LLM workflow from a GitHub repository to C4-based architecture documents. Includes a 22-developer evaluation. Informs the living-documentation output.

## Summary
CIAO is a structured LLM workflow that takes a repository and produces system-level architecture documentation (p1). The template derives from ISO/IEC/IEEE 42010, SEI Views and Beyond, and the C4 model (p1, p3). The output can be added to the target repository (p1). 22 developers each reviewed the documentation for a repository they had contributed to (p1, p5). They found it valuable, comprehensible and broadly accurate. They also found limits in diagram quality, high-level context modelling and deployment views (p1). A complete document takes a few minutes and is cheap to make (p1). Sections cover the template, the workflow, prompt engineering and model selection (p3-p4), results (p6-p10) and threats to validity (p10).

## Unique insights and approaches
- Diagram quality was a named weakness even in an otherwise accepted result (p1). Plan for review of each generated diagram and a repair loop.
- A fixed template guides the LLM to coherent system-level output (p1, p3). A fixed set of views per product keeps diagrams comparable across runs.
- The output lives in the repository (p1). This supports keeping diagrams under version control, next to the code (Q7).
- Generation costs minutes (p1). Regeneration on each change is affordable.

## Content table for lookup
| Section / topic | Pages | What you find there | Use during design of |
|---|---|---|---|
| Template (42010, Views and Beyond, C4) | 3 | Document structure | Q1, Q7 |
| Workflow, prompts, models | 4 | Pipeline steps | Q2, Q8 |
| Evaluation design | 5 | 22 developers | Q8 |
| Results | 6-10 | Accuracy and diagram limits | Q8 |

Page numbers are PDF page numbers of the file in `sources/`.
