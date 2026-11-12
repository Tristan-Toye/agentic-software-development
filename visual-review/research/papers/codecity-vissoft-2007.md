# Visualizing Software Systems as Cities

- **Source**: https://www.si.usi.ch/assets/publications/conf/vissoft/vissoft2007/WettelL07.pdf (VISSOFT 2007)
- **PDF**: `sources/codecity-vissoft-2007.pdf` (file path from the repo root: `visual-review/sources/codecity-vissoft-2007.pdf`)
- **Relevant to**: Spatial metaphor for a system overview. Informs the whole-product map. A 3D city is probably not the target form, but the metric-to-shape mapping and the locality idea transfer.

## Summary
The paper presents CodeCity, a 3D visualisation in which an object-oriented system is a city (p1). The goal is a sense of locality, so that the viewer remembers where things are (p1). Classes are buildings and packages are districts (p1). Source code metrics map to building size and type (p1). In the ArgoUML example the building height is the number of methods and the width and length are the number of attributes (p2). The paper also covers layout, topology and navigation facilities. It tests the approach on ArgoUML, Azureus and VisualWorks, per the search record at https://www.si.usi.ch/assets/publications/conf/vissoft/vissoft2007/WettelL07.pdf. A later controlled experiment (ICSE 2011, https://2011.icse-conferences.org/content/software-systems-cities-controlled-experiment.html) reports higher task correctness and shorter completion time with CodeCity. That later paper is not in `sources/`.

## Unique insights and approaches
- A fixed containment hierarchy (package to class) becomes the spatial layout. This gives a stable place for each element across versions (p1). A stable layout is needed for a diff view, so that added and removed items appear at known positions.
- The authors say 3D visualisations struggle because of navigation and interaction problems, and 2D is proven useful (p1). For a dashboard page, this supports a 2D treemap-like city over a full 3D scene.
- Metrics encode shape, not text (p2). For visual-review, a feature box can carry size by lines or by changed-lines count.

## Content table for lookup
| Section / topic | Pages | What you find there | Use during design of |
|---|---|---|---|
| Abstract and motivation: locality | 1 | Why a city, 2D versus 3D | Q1, Q6 |
| Metric mapping and the ArgoUML city | 2 | Height, width, length, districts | Encoding choices |
| Layout, navigation, case studies | 3-8 | Not read in detail for this document | Layout stability |

Page numbers are PDF page numbers of the file in `sources/`.
