# ArchAgent: Scalable Legacy Software Architecture Recovery with LLMs

- **Source**: https://arxiv.org/abs/2601.13007
- **PDF**: `sources/archagent-legacy-recovery.pdf` (file path from the repo root: `visual-review/sources/archagent-legacy-recovery.pdf`)
- **Relevant to**: LLM plus static analysis to produce multi-view Mermaid architecture diagrams from large repositories. Informs the extraction pipeline.

## Summary
ArchAgent is an agent framework that combines static analysis, adaptive code segmentation and LLM synthesis. It builds multi-view, business-aligned architectures from cross-repository code (p1). The pipeline has a File Summarizer, a Repo Manager, a Readme Generator and an Architect (p2). The Architect writes partial architecture diagrams in Mermaid and merges them into one diagram (p2). An AST gives reference graphs at file, class and function level as context for the summarizer (p3). Adaptive grouping splits the repository summary into G = ceil(T/M) groups with 10% overlap along a depth-first file traversal (p2, p3). The paper compares ArchAgent with DeepWiki by F1 score on eight named repositories (p4, Fig. 2). It reports a maximum gap of +0.21 (p4). An ablation with 30 participants shows that dependency context improves accuracy (p4).

## Unique insights and approaches
- Static reference graphs feed the LLM as context. The LLM does not read raw code alone (p3). For config-harness, cargo metadata and syn-level references can fill this role.
- Partial diagrams are merged after generation (p2). A per-crate diagram can merge into a product diagram.
- Context pruning keeps diagrams within limits (abstract, p1). This is a clutter control at generation time.
- The evaluation uses human participants and a public benchmark (p1, p4). A verification step must exist for each generated diagram (Q8).

## Content table for lookup
| Section / topic | Pages | What you find there | Use during design of |
|---|---|---|---|
| Approach and pipeline | 2-3 | Agents, adaptive grouping, reference graphs | Q2 extraction |
| Scalability evaluation | 3-4 | F1 against DeepWiki | Q8 |
| Ablation on dependency context | 4 | 30 participants | Q8 |
| Case study | 4-5 | Legacy business logic | Q3 |

Page numbers are PDF page numbers of the file in `sources/`.
