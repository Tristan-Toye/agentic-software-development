# Visual Integration of Static and Dynamic Software Analysis in Code Reviews via Software City Visualization

- **Source**: https://arxiv.org/abs/2408.08141
- **PDF**: `sources/software-city-code-review.pdf` (file path from the repo root: `visual-review/sources/software-city-code-review.pdf`)
- **Relevant to**: Closest prior work: a visualisation for pull-request review. Informs the review page of the dashboard and the CI-driven pipeline.

## Summary
Software visualisation for code review is often a standalone application with static analysis only. Its goal is to show structural changes of a pull request (p1). Understanding the changed behaviour is still mostly done by reading code (p1). The paper presents a web-based design that combines static and dynamic analysis in software city visualisations (p1). It integrates with Git hosting services and starts from a CI pipeline (p2). Sections cover the CI initiation, static analysis, dynamic analysis and data processing (p2), the implementation on ExplorViz (p3) and the combined visual integration (p4). A developer can follow a link from the issue tracker to the city view (p1). The work is in progress, and the paper has no controlled evaluation of review outcomes. Section V discusses scalability and limitations (p4).

## Unique insights and approaches
- Tools must go beyond an alternative representation of the diff and fit into the developer workflow (p1). For visual-review, the diagram must appear where the owner already decides, which is the dashboard approval step.
- The approach combines static structure with dynamic traces so that a reviewer can run use cases of the changed system (p1, p2). For plan review before code exists, only the static and planned parts apply. The dynamic part applies after the build.
- A link from the work item to the visualisation removes manual data collection (p1). The dossier can link to its diagram in the same way.
- The paper names too much information in one view as a scalability limit and relies on filtering (p4). This confirms that filtering is a minimum requirement.

## Content table for lookup
| Section / topic | Pages | What you find there | Use during design of |
|---|---|---|---|
| Introduction and motivation | 1 | Gap in review visualisation | Q9 |
| Design: CI, static, dynamic analysis, processing | 2 | Pipeline architecture | Q2, Q5 |
| Implementation and combined views | 3-4 | ExplorViz extension | Q4 |
| Scalability and limitations; related work | 4-5 | Filtering; ChangeViz and other tools in the reference list (p6) | Q1, Q9 |

Page numbers are PDF page numbers of the file in `sources/`.
