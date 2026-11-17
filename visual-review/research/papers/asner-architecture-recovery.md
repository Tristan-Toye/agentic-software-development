# ASNER: Software Architecture Recovery with Adaptive Semantic Neighborhoods and Evidence-Grounded Refinement

- **Source**: https://arxiv.org/abs/2610.36894
- **PDF**: `sources/asner-architecture-recovery.pdf` (file path from the repo root: `visual-review/sources/asner-architecture-recovery.pdf`)
- **Relevant to**: Recovery of components from files by structure plus LLM-derived responsibilities, with evidence-grounded refinement. Informs the code-to-component step.

## Summary
Existing methods combine structural and semantic signals, but the relations do not always reflect architectural responsibility (p1). ASNER uses an LLM to derive a structured responsibility for each file (p1). It builds adaptive semantic neighbourhoods. It adds selected semantic relations and weak directory cues to a static dependency graph (p1). Community detection then recovers the component backbone (p1). A responsibility-ownership-aware agent revisits uncertain boundary files. It changes an assignment only when traceable code evidence supports it (p1). On 12 open-source projects in C, C++, Java and Python it has the best macro average on five metrics. It improves ARI by 19.5% and c2c@66 by 32.9% over GAER-GAT (p1). A downstream test shows better code localisation for an agent that uses the recovered architecture (p1).

## Unique insights and approaches
- Responsibility text per file is the semantic signal (p1). It is also usable as the feature label that maps files to product features (Q3).
- The LLM may change an assignment only with traceable evidence (p1). Use the same rule for diagram claims: every box and edge keeps a pointer to code evidence.
- Directory structure is a weak cue, not a rule (p1). This matters for a Cargo workspace, where crate boundaries are strong cues and folders are weak ones.
- Recovered architecture helps agents localise code (p1). This supports using the map as context for the fan-out agents.

## Content table for lookup
| Section / topic | Pages | What you find there | Use during design of |
|---|---|---|---|
| Abstract: method and results | 1 | Pipeline and metrics | Q2, Q8 |
| Method, experiments, ablations | 2+ | Not read in detail for this document | Q8 metrics (ARI, c2c) |

Page numbers are PDF page numbers of the file in `sources/`.
