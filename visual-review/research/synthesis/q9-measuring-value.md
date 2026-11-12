# Q9. How to measure whether it helps

- **Question**: Does a diagram review before the fan-out improve the outcome? How do we find out?
- **Status**: PROPOSED.

## Facts: what the evidence says

- **No evidence was found that diagram review catches more defects.** The synthesis author says so directly ([review-ux-and-feedback.md](review-ux-and-feedback.md), section 5). The software-city review paper is in progress and has no controlled evaluation of review outcomes ([software-city-code-review](../papers/software-city-code-review.md)).
- At Microsoft, review found fewer defects than expected. It gave knowledge transfer and awareness. Understanding the change was the main challenge ([modern-code-review-bacchelli-bird-2013](../papers/modern-code-review-bacchelli-bird-2013.md)).
- In two open-source projects, 75 percent of problems fixed by review were maintainability and 25 percent functional (Beller et al., via [review-ux-and-feedback.md](review-ux-and-feedback.md), section 5). A figure of about 15 percent of comments pointing to a possible defect comes from a secondary commentary, and the title of the study it describes is contested (same section).
- CodeCity gave higher correctness and shorter time in a controlled experiment of 41 participants. The ICSE 2011 PDF was not downloaded, so the claim is second-hand ([representations-beyond-uml.md](representations-beyond-uml.md), row 9; [papers README](../papers/README.md), gaps).
- The CIAO study had 22 developers. They found the documentation valuable and broadly accurate, with weak diagram quality ([ciao-architecture-documentation](../papers/ciao-architecture-documentation.md)). ArchAgent used 30 participants in an ablation and a public benchmark ([archagent-legacy-recovery](../papers/archagent-legacy-recovery.md)).
- Vision models misread relations. The best of 11 scored 70.18 percent ([vlm-architecture-diagram-understanding](../papers/vlm-architecture-diagram-understanding.md)). So accuracy checks must use the structured data, not a model that reads the picture.
- mermaidiff has an eval harness with 10 cases and expected answers. One scored run found 10 of 10 with 0 false positives ([mermaidiff analysis](../../../inspiration-repos/analysis/mermaidiff.md)). This is a method to copy. Its result is for a small set of cases.
- GitDiagram records tokens and attempts per run ([gitdiagram analysis](../../../inspiration-repos/analysis/gitdiagram.md)).
- The pipeline names the cost of contract arbitration and `CONTRACT-CHANGE:` returns ([current-state.md](current-state.md), B1).

What this means (inference): the diagram targets problems of understanding and design (wrong scope, missing feature, wrong flow). The pipeline already records late contract changes. That is the best existing proxy.

## Proposed measures

Three groups. Each measure is falsifiable: it has a number and a way to fail.

**A. Is the diagram correct? (tool quality)**

| Measure | How | Pass |
|---|---|---|
| Evidence coverage | Share of elements with a valid `path:line` or a stub or dossier reference. Computed by the validator. | 100 percent of non-`inferred` elements |
| Anchor validity | The anchor check on the committed copy ([current-state.md](current-state.md), A2) | 100 percent |
| False-positive rate | An eval set of changes with expected diagrams, as in mermaidiff. Count claims in the diagram that the code or dossier does not support. | 0 invented elements in the set |
| Recall of touched features | Eval changes with a known list of touched features. Count how many the L0 map marks. | Threshold set by the owner (OQ-07) |
| Inferred share | Share of elements marked `inferred` | Reported, no fixed pass |
| Extraction drift | Re-extract after merge. Count `diverge` and `absent` edges against the plan. | Reported per run |

**B. Does the owner use it? (adoption)**

| Measure | Source |
|---|---|
| Time from diagram posted to owner answer | [review-ux-and-feedback.md](review-ux-and-feedback.md), section 7 |
| Count of element verdicts by type, and share of elements left "not yet looked at" | same |
| Count of re-plans caused by a diagram answer | same |

**C. Does it change the outcome? (value)**

| Measure | How |
|---|---|
| Late contract changes | Count `CONTRACT-CHANGE:` returns and contract arbitrations on runs with a visual review, against runs without ([review-ux-and-feedback.md](review-ux-and-feedback.md), section 7). |
| Seeded-defect test (proposal) | Take past dossiers. Add a known scope error (a missing feature, a wrong flow step). Show the dossier to the owner with and without the diagram. Count caught errors. Small sample; report the counts, not a significance claim. |
| Cost | Tokens and agent attempts per diagram run, as GitDiagram's session audit does. Compare with the cost of a re-spawn after a contract fix ([current-state.md](current-state.md), B1). |

**Confounds.** Runs differ in size and difficulty. Do not compare two arbitrary sets. Alternate runs by a rule fixed in advance, or use the seeded-defect test. This is inference. The inputs do not describe a design.

**Logging.** The diagram-author writes the numbers of group A into `diff.json` or a sibling file. The dashboard writes group B. A weekly script reads group C from the build logs. (Proposal.)

## Recommendation

**BUILD** the measures in groups A and B into the first release. They cost little and need no comparison group.

**BUILD** a small eval set (start with 10 changes from past config-harness dossiers) with expected diagrams, copying the mermaidiff method.

**USE** the late-contract-change count as the main value measure, with the seeded-defect test as a one-time study.

**SKIP** any claim that the capability "catches more defects" until data exists.

## Sources

- Synthesis input: [review-ux-and-feedback.md](review-ux-and-feedback.md), [current-state.md](current-state.md), [representations-beyond-uml.md](representations-beyond-uml.md).
- Papers: [modern-code-review-bacchelli-bird-2013](../papers/modern-code-review-bacchelli-bird-2013.md), [software-city-code-review](../papers/software-city-code-review.md), [ciao-architecture-documentation](../papers/ciao-architecture-documentation.md), [archagent-legacy-recovery](../papers/archagent-legacy-recovery.md), [vlm-architecture-diagram-understanding](../papers/vlm-architecture-diagram-understanding.md), [codecity-vissoft-2007](../papers/codecity-vissoft-2007.md).
- Analyses: [mermaidiff](../../../inspiration-repos/analysis/mermaidiff.md), [gitdiagram](../../../inspiration-repos/analysis/gitdiagram.md).
