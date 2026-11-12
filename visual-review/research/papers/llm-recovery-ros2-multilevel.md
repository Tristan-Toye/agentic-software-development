# Towards LLM-Assisted Architecture Recovery for Real-World ROS 2 Systems: An Agent-Based Multi-Level Approach to Hierarchical Structural Architecture Reconstruction

- **Source**: https://arxiv.org/abs/2605.20055
- **PDF**: `sources/llm-recovery-ros2-multilevel.pdf` (file path from the repo root: `visual-review/sources/llm-recovery-ros2-multilevel.pdf`)
- **Relevant to**: Staged, multi-level LLM recovery with fixed intermediate representations. Informs how to build abstraction levels bottom-up.

## Summary
The paper extends a blueprint-guided, LLM-assisted pipeline for ROS 2 systems (p1). Structure is often implicit across source code and launch files, so hierarchical recovery is hard (p1). Existing work covers node-level entities and wiring, with limited support for hierarchy across abstraction levels (p1). The authors add refined prompting for consistency and controllability. They add a staged strategy with multi-level intermediate architectural representations. These include the atomic node list and launch-file dependencies, which constrain the reconstruction (p1). They test on an automated product disassembly system with cooperating robot arms (p1). Results show better structural consistency and scalability. Dynamic integration semantics remain a challenge (p1). The paper cites C4, arc42 and the 4+1 view model as level frameworks (p2).

## Unique insights and approaches
- Facts from deterministic parsing come first (the node list and launch dependencies). The LLM synthesises within those limits (p1). Use cargo metadata and module trees as the fixed layer.
- Each level has its own intermediate representation (p1). Store each abstraction level as data, so a level can be regenerated alone.
- Dynamic semantics are not recovered well (p1). A sequence view needs another source, such as tests or traces.

## Content table for lookup
| Section / topic | Pages | What you find there | Use during design of |
|---|---|---|---|
| Abstract and contributions | 1 | Staged strategy | Q1, Q2 |
| Introduction: C4, arc42, 4+1 | 2 | Level frameworks | Q1 |
| Method and evaluation | 3+ | Not read in detail for this document | Q8 |

Page numbers are PDF page numbers of the file in `sources/`.
