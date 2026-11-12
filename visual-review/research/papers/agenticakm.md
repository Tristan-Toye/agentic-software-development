# AgenticAKM: Enroute to Agentic Architecture Knowledge Management

- **Source**: https://arxiv.org/abs/2602.04445
- **PDF**: `sources/agenticakm.pdf` (file path from the repo root: `visual-review/sources/agenticakm.pdf`)
- **Relevant to**: Multi-agent extraction, retrieval, generation and validation of architecture knowledge (ADRs). Informs the keep-in-sync loop and the verification step.

## Summary
Architecture knowledge management is laborious and developers rarely adopt it (p1). A single-prompt LLM approach is ineffective because of context limits and distributed knowledge (p1). AgenticAKM splits the problem into sub-tasks. Specialised agents do extraction, retrieval, generation and validation (p1). The first instance generates Architecture Decision Records (ADRs) from code repositories (p1). A user study with 29 repositories shows that the agentic approach generates better ADRs than the baseline (p1). The authors treat the approach as an early step.

## Unique insights and approaches
- Validation is a separate agent role (p1). Give the diagram pipeline a validator that is independent of the generator.
- Decomposition beats one big prompt for distributed knowledge (p1). Generate per feature and per crate, then merge.
- ADRs are a knowledge unit tied to code (p1). The existing ADR index in the plugin can link to diagram elements.

## Content table for lookup
| Section / topic | Pages | What you find there | Use during design of |
|---|---|---|---|
| Abstract and approach | 1 | Four agent roles | Q7, Q8 |
| Introduction | 1-2 | AKM problem | Q7 |
| Evaluation, 29 repositories | 3+ | Not read in detail for this document | Q8 |

Page numbers are PDF page numbers of the file in `sources/`.
