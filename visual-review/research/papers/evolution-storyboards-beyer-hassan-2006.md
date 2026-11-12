# Animated Visualization of Software History using Evolution Storyboards

- **Source**: https://www.sosy-lab.org/research/pub/2006-WCRE.Animated_Visualization_of_Software_History_using_Evolution_Storyboards.pdf (WCRE 2006)
- **PDF**: `sources/evolution-storyboards-beyer-hassan-2006.pdf` (file path from the repo root: `visual-review/sources/evolution-storyboards-beyer-hassan-2006.pdf`)
- **Relevant to**: Visualising structural change over time. Informs how a plan diff can be stepped through in panels.

## Summary
Static views of software history miss the dynamic nature of evolution (p1). The paper presents the evolution storyboard: a sequence of animated panels, one per time period, that highlight structural changes (p1). Each panel shows dependency information of one period, and artifacts are placed close together when they often change together (p1, p2). The tool extracts dependency graphs from version control repositories and computes the panels automatically (p1). The paper gives a step-by-step guide to read a storyboard (p5) and case studies including ArgoUML (p7-p9). Engineers can find good design, signs of structural decay and the spread of cross-cutting concerns (p1).

## Unique insights and approaches
- The storyboard sits between a static diagram and a full movie. The authors position it as easier to control than a movie (p1; also stated in the authors' slides at https://www.slideshare.net/slideshow/animated-visualization-of-software-history-using-software-evolution-storyboards/64518533). For visual-review, a change can be a short sequence of panels (before, change, after) the reviewer steps through.
- Layout comes from co-change data in version control (p2, p4). A co-change graph links the plan to history, so that new coupling is visible against past coupling.
- The paper provides a step-by-step reading guideline (p5). A diff view needs a similar reading guide, such as a legend with fixed meaning for added, removed and changed.

## Content table for lookup
| Section / topic | Pages | What you find there | Use during design of |
|---|---|---|---|
| Introduction and contributions | 1-2 | Storyboard concept and applications | Q4 |
| Graph model and visualisation | 3-4 | Co-change graphs, panel layout | Diff layout |
| Application guideline | 5-7 | Step-by-step reading | Reading guide (Q9) |
| Case studies | 7-9 | ArgoUML and others | Evidence |

Page numbers are PDF page numbers of the file in `sources/`.
