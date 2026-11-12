# Feature Location in Source Code: A Taxonomy and Survey

- **Source**: https://www.cs.wm.edu/~denys/pubs/JSME-FL-SurveyCRCV1.pdf (J. Software: Evolution and Process 25(1), DOI 10.1002/smr.567; preprint)
- **PDF**: `sources/feature-location-survey-dit-2013.pdf` (file path from the repo root: `visual-review/sources/feature-location-survey-dit-2013.pdf`)
- **Relevant to**: Taxonomy of techniques that map a feature to code. Informs the feature-to-code mapping that the product-level map needs.

## Summary
Feature location is the activity of identifying an initial location in the source code that implements a functionality (p1). The paper gives a taxonomy of feature location techniques and classifies 89 articles from 25 venues (p1). The authors describe how four authors classified each article individually and then resolved disagreements (p3). Techniques include dynamic analysis and information retrieval, among others (p3, taxonomy in section 3). The paper lists open issues and future directions (p1). An online appendix with benchmarks is at https://www.cs.wm.edu/semeru/data/feature-location-survey. The survey predates LLM techniques.

## Unique insights and approaches
- Feature location can combine several signals: execution traces of a feature, static structure and text search (p1, p3). A feature-to-code map should store which signal produced each link.
- A feature location result is a starting point. Impact analysis follows from it (search record at https://www.cs.wm.edu/~denys/pubs/JSME-FL-SurveyCRCV1.pdf). Treat a feature mapping as a seed to expand.
- The survey has benchmarks with gold sets and traces (appendix link above). They can test a mapping step.

## Content table for lookup
| Section / topic | Pages | What you find there | Use during design of |
|---|---|---|---|
| Abstract and scope | 1 | Definition, 89 articles | Q3 |
| Article selection and classification | 3 | Method | Evidence |
| Taxonomy (section 3) and surveyed techniques | 4-54 | Dimensions and classified work. Not read in detail for this document. | Q3 design |

Page numbers are PDF page numbers of the file in `sources/`.
