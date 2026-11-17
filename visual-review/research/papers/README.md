# Papers index - visual-review research

One document for each paper. Each document follows the template in `research/CONVENTIONS.md`.
PDFs are in `visual-review/sources/`. All PDFs in this index were downloaded.

Design questions:
Q1 abstraction levels and clutter control; Q2 grounding diagrams in code; Q3 mapping code to product features;
Q4 diagram diffs for change approval; Q5 sequence and behaviour diagrams; Q6 interactivity and navigation;
Q7 keeping diagrams in sync; Q8 LLM generation accuracy and verification; Q9 review and approval UX.

| Paper | Year | Link | Why it matters | Informs |
|---|---|---|---|---|
| [Software Reflexion Models: Bridging the Gap Between Source and High-Level Models](reflexion-models-murphy-1995.md) | 1995 | https://www.cs.ubc.ca/~murphy/papers/rm/fse95.html | Compares an intended model with extracted code facts. Gives the converge/diverge/absent vocabulary for diagram diffs. | Q2, Q4, Q7 |
| [Techniques for Reducing the Complexity of Object-Oriented Execution Traces](trace-complexity-reduction-hamou-lhadj-2003.md) | 2003 | https://webhome.cs.uvic.ca/~mstorey/vissoft2003/submissions/HamouLhadjReducingComplexity.pdf | Repeated call sequences and utilities make traces large. Remove or collapse them. | Q5, Q1 |
| [Hierarchical Edge Bundles: Visualization of Adjacency Relations in Hierarchical Data](hierarchical-edge-bundles-holten-2006.md) | 2006 | https://research.tue.nl/en/publications/hierarchical-edge-bundles-visualization-of-adjacency-relations-in/ | One bundling-strength parameter moves dependency edges between detail levels. | Q1, Q6 |
| [Animated Visualization of Software History using Evolution Storyboards](evolution-storyboards-beyer-hassan-2006.md) | 2006 | https://www.sosy-lab.org/research/pub/2006-WCRE.Animated_Visualization_of_Software_History_using_Evolution_Storyboards.pdf | Panel sequence of structural change with animation. A way to step through a diff. | Q4, Q6 |
| [Visualizing Software Systems as Cities](codecity-vissoft-2007.md) | 2007 | https://www.si.usi.ch/assets/publications/conf/vissoft/vissoft2007/WettelL07.pdf | Package-to-district and class-to-building mapping gives a stable spatial overview. | Q1, Q6 |
| [Expectations, Outcomes, and Challenges of Modern Code Review](modern-code-review-bacchelli-bird-2013.md) | 2013 | https://2013.icse-conferences.org/content/expectations-outcomes-and-challenges-modern-code-review.html | Understanding the change is the main review challenge. Tools support it badly. | Q9, Q3 |
| [Feature Location in Source Code: A Taxonomy and Survey](feature-location-survey-dit-2013.md) | 2013 | https://www.cs.wm.edu/~denys/pubs/JSME-FL-SurveyCRCV1.pdf | Taxonomy of ways to map a feature to code. Combines traces, static analysis and text. | Q3, Q2 |
| [Constructing Object Groups Corresponding to Concepts for Recovery of a Summarized Sequence Diagram](summarized-sequence-diagram-objects.md) | 2021 | https://arxiv.org/abs/2003.03237 | Summarises a sequence diagram by grouping lifelines into domain concepts. | Q5, Q1 |
| [Visual Integration of Static and Dynamic Software Analysis in Code Reviews via Software City Visualization](software-city-code-review.md) | 2024 | https://arxiv.org/abs/2408.08141 | Visualisation of a pull request inside the review workflow, with static and dynamic data. | Q3, Q4, Q5, Q9 |
| [Semantic Zoom and Mini-Maps for Software Cities](semantic-zoom-minimaps-software-cities.md) | 2025 | https://arxiv.org/abs/2510.00003 | Zoom level changes the representation. A mini-map keeps context. | Q1, Q6 |
| [Code Review Comprehension: Reviewing Strategies Seen Through Code Comprehension Theories](code-review-comprehension-strategies.md) | 2025 | https://arxiv.org/abs/2503.21455 | Reviewers build context, then compare expected and actual. Supports plan-first diagrams. | Q3, Q9 |
| [Generating Software Architecture Description from Source Code using Reverse Engineering and Large Language Model](sad-from-source-reverse-eng-llm.md) | 2025 | https://arxiv.org/abs/2511.05165 | Facts from tools, abstraction by the LLM as a filter, behaviour by a fixed prompt procedure. | Q2, Q5, Q1, Q8 |
| [Breaking the Alphabet: Rethinking File Ordering in Code Review](file-ordering-code-review.md) | 2026 | https://arxiv.org/abs/2609.04207 | Alphabetical file order is disliked. Reviewers want dependency-aware grouping. | Q9, Q1 |
| [ArchAgent: Scalable Legacy Software Architecture Recovery with LLMs](archagent-legacy-recovery.md) | 2026 | https://arxiv.org/abs/2601.13007 | Static references plus an LLM to make Mermaid multi-view diagrams. Has a public benchmark. | Q2, Q8, Q1 |
| [ASNER: Software Architecture Recovery with Adaptive Semantic Neighborhoods and Evidence-Grounded Refinement](asner-architecture-recovery.md) | 2026 | https://arxiv.org/abs/2610.36894 | Evidence-grounded LLM refinement of component clusters. Every assignment needs code evidence. | Q2, Q8, Q3 |
| [Towards LLM-Assisted Architecture Recovery for Real-World ROS 2 Systems: An Agent-Based Multi-Level Approach to Hierarchical Structural Architecture Reconstruction](llm-recovery-ros2-multilevel.md) | 2026 | https://arxiv.org/abs/2605.20055 | Multi-level staged recovery with fixed intermediate representations. | Q1, Q2, Q8 |
| [CIAO - Code In Architecture Out: Automated Software Architecture Documentation with Large Language Models](ciao-architecture-documentation.md) | 2026 | https://arxiv.org/abs/2604.08293 | C4-based LLM documentation from a repo. Diagram quality is the weak point. | Q7, Q8, Q1 |
| [AgenticAKM: Enroute to Agentic Architecture Knowledge Management](agenticakm.md) | 2026 | https://arxiv.org/abs/2602.04445 | Separate extract, retrieve, generate, validate agents for architecture knowledge. | Q7, Q8 |
| [Benchmarking and Evaluating VLMs for Software Architecture Diagram Understanding](vlm-architecture-diagram-understanding.md) | 2026 | https://arxiv.org/abs/2604.04009 | Vision models read relations in diagrams poorly. Keep structured data, not only images. | Q8, Q5, Q6 |

Gaps. Not found as open PDFs: Garcia et al., ASE 2013 comparison of architecture recovery techniques (IEEE only, not downloaded); CodeCity ICSE 2011 experiment (the PDF link failed). They are not documented here.
