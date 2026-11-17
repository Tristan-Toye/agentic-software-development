# Semantic Zoom and Mini-Maps for Software Cities

- **Source**: https://arxiv.org/abs/2510.00003
- **PDF**: `sources/semantic-zoom-minimaps-software-cities.pdf` (file path from the repo root: `visual-review/sources/semantic-zoom-minimaps-software-cities.pdf`)
- **Relevant to**: Semantic zoom and mini-map for visual scalability. Informs the zoom behaviour of the dashboard diagrams.

## Summary
The paper addresses visual scalability: the ability of a visualisation to show small and large amounts of data (p1). It presents two approaches in 3D software cities in the ExplorViz tool. Semantic zoom changes the graphical representation of the landscape with the distance of the virtual camera from objects (p1). The mini-map is a small two-dimensional top view (p1). The authors evaluate both in two separate user studies (p1, p5-p10). The results show that both are useful. They are most useful for large software landscapes and for collaborative exploration. The studies also show shortcomings, which are left for future work (p1). The related work section lists earlier semantic zoom work with levels of detail and visual indicators for orientation when zoomed in (p2).

## Unique insights and approaches
- Semantic zoom changes what is drawn, not only the scale (p1). Each zoom level shows a different representation. For visual-review, zoom in on a box shows its internal sub-diagram, which matches the C4-like abstraction levels.
- The mini-map keeps the global position visible while the user is zoomed in (p1, p5). This addresses the loss of context when a reviewer moves deep into one feature.
- Users ask for these features most for large systems and for shared exploration (p1). A map of the whole product with several reviewers needs them.

## Content table for lookup
| Section / topic | Pages | What you find there | Use during design of |
|---|---|---|---|
| Introduction and related work | 1-3 | Visual scalability; earlier semantic zoom | Q1 |
| Semantic zoom design | 3-5 | How the representation changes with distance | Zoom levels (Q1, Q6) |
| Mini-map design | 5 | Top-view projection | Navigation (Q6) |
| Evaluation of semantic zoom | 5-9 | User study results | Evidence for design |
| Evaluation of mini-map | 9-10 | User study results | Evidence for design |

Page numbers are PDF page numbers of the file in `sources/`.
