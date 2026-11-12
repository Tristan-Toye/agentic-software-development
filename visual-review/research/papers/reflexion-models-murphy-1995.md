# Software Reflexion Models: Bridging the Gap Between Source and High-Level Models

- **Source**: https://www.cs.ubc.ca/~murphy/papers/rm/fse95.html (FSE 1995, pp. 18-28)
- **PDF**: `sources/reflexion-models-murphy-1995.pdf` (file path from the repo root: `visual-review/sources/reflexion-models-murphy-1995.pdf`)
- **Relevant to**: Diagram-diff engine and drift check. Compares an intended high-level model with the code. Informs the "expected versus actual" view of the visual-review volume.

## Summary
Engineers draw high-level box-and-arrow models of a system. These models are almost always inaccurate with respect to the source code (p1). The authors let the engineer define a high-level model and a mapping from the model to source entities. A tool then computes a reflexion model. The reflexion model shows where the high-level model agrees with the source model and where it differs (p1). The paper gives a formal description of reflexion models. It also reports use on NetBSD, a Unix implementation with 250,000 lines of C. In a few hours, one engineer computed several reflexion models. These gave him a global overview of the virtual memory subsystem (p1). The technique is a lightweight way to compare a design view and the code.

## Unique insights and approaches
- The comparison is three-valued: edges that converge (model and code agree), edges that diverge (code has an edge the model lacks), and edges that are absent (model has an edge the code lacks). The abstract names agreement and difference (p1). This is the same shape as an added, removed and unchanged diff of a diagram. Use it as the base of the diff vocabulary in the visual-review volume.
- The human supplies a small mapping from the model to the code. The tool does the rest (p1). For visual-review, the mapping from a diagram box to a code path can be a stored, reviewable file. This links to Q2 and Q7.
- The method works with an inaccurate model on purpose. It uses the divergence as information (p1). A plan diagram that the code later contradicts can be shown the same way.

## Content table for lookup
| Section / topic | Pages | What you find there | Use during design of |
|---|---|---|---|
| Abstract: concept and NetBSD result | 1 | Definition of a reflexion model and the NetBSD case | Diff vocabulary; drift check (Q4, Q7) |
| Formal characterisation, practical aspects, experiences | 2-11 | Not read in detail for this document. Open the PDF for the formal definition. | Mapping file format (Q2) |

Page numbers are PDF page numbers of the file in `sources/`.
