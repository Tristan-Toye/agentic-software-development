# Review and approval UX for diagrams (Q9, with Q4 and Q6)

- **Design questions**: Q9 review and approval UX; Q4 diagram diffs; Q6 interactivity.
- **Evidence note**: links come from web searches on 2026-11-12. Search snippets were read, not full papers, unless stated. Claims that rest on a snippet say so.
- Cross-links: [current-state.md](current-state.md) (where the review step sits), [representations-beyond-uml.md](representations-beyond-uml.md).

## 1. What the owner needs

The owner approves a planned change before the fan-out. The owner needs to answer three questions fast:
1. Is this the product change I want? (scope and behaviour)
2. Is anything affected that I did not expect? (impact)
3. Which part do I doubt? (a way to say so at that part)

Today the dashboard supports a question with 2 to 4 options and a free-text follow-up, but no anchor to a thing on screen (`shared/questions/store.ts:6-30`; `shared/questions/server.ts:798-830`). See [current-state.md](current-state.md) section B2.

## 2. How review tools present change for approval

| Tool | How it presents change | How it records approval | Lesson for visual-review |
|---|---|---|---|
| GitHub rich diff for images | Three modes for images: 2-up (side by side, default), swipe (draggable divider), onion skin (cross-fade slider) ([GitHub docs: rendering and diffing images](https://docs.github.com/en/enterprise/2.17/user/github/managing-files-in-a-repository/rendering-and-diffing-images); [GitHub blog: image view modes](https://github.blog/2011-03-21-behold-image-view-modes/)). The new Files-changed page shows rendered previews for image and Markdown files, and SVG and Markdown can switch between source and rendered view ([GitHub changelog 2025-07-31](https://github.blog/changelog/2025-07-31-pull-request-files-changed-public-preview-experience-july-31-updates/)). | PR review: approve, comment, request changes. | A diagram stored as SVG gets a free source-vs-rendered toggle. GitHub shows no structural diff of a diagram; a search for Mermaid diff in PRs found nothing. The structural diff must be built (Q4). |
| Graphite stacked PRs | Small, stacked changes, so each PR stays "small and easily reviewable". A reviewer's change request updates one PR without touching the rest of the stack ([Graphite: best practices for reviewing stacks](https://graphite.dev/docs/best-practices-for-reviewing-stacks)). | Per-PR approval. | Split a change into reviewable slices. A diagram review can mirror the work packages: one diagram slice per package. |
| Gerrit | A change keeps one identity across rewrites (the Change-Id), so a reviewer sees the same change after a rebase ([Mergify on Gerrit Change-Id](https://mergify.com/blog/stacked-prs-without-a-new-git-workflow)). Gerrit also has an image-diff plugin ([Gerrit image-diff plugin](https://gerrit.googlesource.com/plugins/image-diff/)). | Label votes per patch set. | Give each diagram element a stable ID that survives regeneration, so comments stay attached across revisions. |
| Reviewable | Per-file review state. (A search for Reviewable documentation returned nothing; this row is from general knowledge and needs a source before it is relied on.) | Per-file "reviewed" marks. | Not verified. |
| Figma branching | Compare a branch with main in a before-and-after view; reviewers "approve or suggest further changes" with a comment; merge is all-or-nothing, with no partial merge ([Figma help: review branch changes](https://help.figma.com/hc/en-us/articles/5693123873687); [Figma help: merge branch](https://help.figma.com/hc/en-us/articles/5691189138839/)). | Approve or request changes per branch. | The whole-branch verdict is a limit. Per-element verdicts need a design of their own. |
| IcePanel | Diagrams are views onto one shared model. "Drafts" let a team design a future state next to the current one, compare proposals and merge them after a decision. "Versions" are read-only snapshots. "Flows" are step-by-step overlays that stay in sync with the diagram ([IcePanel versioning](https://docs.icepanel.io/advanced-features/versioning); [IcePanel diagramming](https://docs.icepanel.io/core-features/diagramming); [Flows vs UML sequence diagrams](https://icepanel.medium.com/comparison-icepanel-flows-vs-uml-sequence-diagrams-4b3b190051a0)). A search did not find a documented visual diff between two versions. | Draft merge after a decision. | The closest product model to the target: a draft of the future next to the current state, over one shared model. IcePanel is paid; the idea is reusable. |
| Structurizr diagram review | A review has a unique URL; reviewers add comments on diagrams. Reviews are immutable, with no resolve and no threads. The cloud version is paid ([Structurizr: diagram review](https://docs.structurizr.com/cloud/diagram-review); [on-premises](https://docs.structurizr.com/onpremises/diagram-review)). | Comments only. | A light review is easy to use, but without threads or resolve it does not close the loop. The dashboard already has threads (follow-up lines). |
| C4 diagram review checklist | A checklist for the reviewer: title, diagram type, scope, legend, element names and level of abstraction ([c4model.com checklist](https://c4model.com/diagrams/checklist); [Simon Brown, how to review an architecture diagram](https://dev.to/simonbrown/how-to-review-a-software-architecture-diagram-6p0)). | Reviewer judgement. | Run this checklist mechanically on the generated diagram before the owner sees it (title, legend, scope, every element named). |
| Architecture decision records | The author sets status `proposed` and opens a PR; the PR status mirrors the decision until acceptance. Other teams hold a review meeting with outcomes accept, rework or reject ([UK GDS: architecture decisions](https://gds-way.digital.cabinet-office.gov.uk/standards/architecture-decisions.html); [AWS ADR process](https://docs.aws.amazon.com/prescriptive-guidance/latest/architectural-decision-records/adr-process.html)). Statuses: proposed, accepted, deprecated, superseded. | Status field edit. | Three outcomes (accept, rework, reject) map to the dashboard options. This pipeline already extracts ADRs at Phase 8 (`primary-agents/work-on.md:254-262`). |

## 3. Visual diff patterns

Gleicher's design space for comparison has three layouts: juxtaposition (separate views), superposition (one overlaid view) and explicit encoding (draw the difference itself) ([Gleicher, Considerations for Visualizing Comparison](https://pages.graphics.cs.wisc.edu/VisSnacks/papers/comparison)). The 2021 review of 127 papers adds animated transition as temporal juxtaposition. It reports that superposition was the conventional choice for small differences, but recent studies show that juxtaposition can be as good or better ([L'Yi, Jo, Seo, Comparative Layouts Revisited](https://arxiv.org/pdf/2009.00192)). I read the search summary, not the full paper.

Patterns for a diagram diff, with the trade-off for this use:

| Pattern | How it works | Strength | Weakness |
|---|---|---|---|
| Explicit colour encoding (added green, removed red, changed amber, unchanged grey) on one merged diagram | Superposition plus explicit encoding | One view shows everything. Familiar from code diffs. | Colour alone fails for colour-blind users; add a second channel (outline style, icon, label "NEW"). Removed items must stay visible, so the diagram grows. |
| Side by side (before, after) | Juxtaposition | No clutter from removed items. Good when layouts differ much. | The eye must find the match. Needs linked highlight: hover an element in one view and the match lights up in the other. |
| Overlay with slider or swipe | Superposition | Good for same-layout diagrams (GitHub swipe and onion skin). | Needs a stable layout between before and after. Automatic layouts move nodes, so overlay breaks. |
| Animated transition | Temporal juxtaposition | Shows what moved and what appeared. | Easy to miss changes. Hard to point to a thing. Not good for approval, only for a first look. |
| Change summary list beside the diagram | Explicit encoding as text | Gives the reviewer a short list to tick through: "3 added, 1 changed, 2 removed". | Needs to link both ways to the diagram. |
| Impact highlight | Highlight the unchanged elements that touch a changed one (one hop) | Answers "what is affected?". Matches the one-hop blast radius the pipeline already computes (`blast-radius-scout`, `references/work-on/phase-2.md:55-62`). | Needs a dependency model; over-highlighting causes noise. |

Practical rule: keep the layout stable between before and after (same positions for unchanged elements). Stable layout makes overlay and side by side both work. This is a Q4 requirement on the diagram generator.

## 4. Anchored comments and per-element verdicts

Mechanism in the existing tools:
- A code review comment is anchored to a file and line. Figma comments are pins on the canvas (Figma branch review supports comments with approve and suggest-changes; [Figma help](https://help.figma.com/hc/en-us/articles/5693123873687)). Structurizr anchors reviewer comments to a diagram ([Structurizr review](https://docs.structurizr.com/cloud/diagram-review)).
- The dashboard store is append-only JSONL, and a `Line` already accepts extra keys (`shared/questions/store.ts:6-12`). An `anchor` key (diagram ID plus element ID) needs no store change.

Proposed design (a proposal, not an evidence-backed result):
1. Every diagram element has a stable ID, a one-line description in product words, and a list of `path:line` evidence.
2. The owner can click an element and choose one verdict: **OK**, **Question** (opens a follow-up line), **Change** (text says what), or **Remove from scope**. This is the Gerrit-label idea at element level.
3. Unreviewed elements stay visible as "not yet looked at". The run summary shows a count: "12 of 14 elements checked, 1 question, 1 change".
4. The overall decision stays one question with 2 to 4 options, as the store requires (`scripts/ask_owner.py:161-164`). Element verdicts feed the text of that answer. The rule that the first answer is final holds (`store.ts:105-127`), so changes should be sent as one batch at the end, not one by one.
5. A "Change" verdict creates a re-plan input, not a code edit: the orchestrator updates the contract, then regenerates the diagram. Regeneration keeps element IDs, so old comments stay attached (Gerrit Change-Id idea).

## 5. What reviewers actually catch (evidence)

- At Microsoft, defect finding was the top expected benefit of review, but reviews were less about defects than expected. The extra value was knowledge sharing, awareness and alternative solutions. Understanding the change was the central challenge, and tools supported it poorly ([Bacchelli and Bird, ICSE 2013](https://2013.icse-conferences.org/content/expectations-outcomes-and-challenges-modern-code-review.html)).
- In two open-source projects, 75 percent of the problems that review fixed were maintainability issues and 25 percent were functional ([Beller et al., MSR 2014](https://repository.tudelft.nl/record/uuid:8747d363-137b-4a9a-a02a-f8aebd26c674)).
- A commentary on a Microsoft study says about 15 percent of review comments point to a possible defect; the title "Code Reviews Do Not Find Bugs" is contested ([Czerwonka, Greiler, Tilford, ICSE 2015](https://www.microsoft.com/en-us/research/wp-content/uploads/2015/05/PID3556473.pdf); percent figure from a secondary commentary, [Entropic Thoughts](https://entropicthoughts.com/code-reviews-do-not-find-bugs)).
- At Google, review is light (often one reviewer, small changes) and has educational and consistency effects beyond bug finding ([Sadowski et al., ICSE-SEIP 2018](https://research.google/pubs/modern-code-review-a-case-study-at-google/)).

What this means for visual review (inference, not measured): reviewers find problems of understanding and design more than low-level defects. A diagram review before the fan-out targets exactly that class: wrong scope, missing feature, wrong flow. Evidence that diagram review itself catches more defects was not found in these searches. The visual-review capability should log its own measures (see section 7) so the owner can test its value.

## 6. Ideas for clearer owner feedback

1. **Product words first.** Every element label and every question uses feature language from the feature book (H-01 to H-13 in config-harness), with the code name in a tooltip. See [current-state.md](current-state.md) A4.
2. **A one-screen change summary above the diagram**: three lines, "adds / changes / removes", plus the count of affected neighbours. This is the dashboard's product summary pattern (`commands/product-summary.md:1-20`), moved before the build.
3. **Walkthrough mode for the flow diagram**: step through a sequence one message at a time, with a "this step is wrong" button per step. IcePanel Flows is the model ([IcePanel flows](https://icepanel.medium.com/comparison-icepanel-flows-vs-uml-sequence-diagrams-4b3b190051a0)).
4. **Ask a closed question, not an open one.** The ask policy wants 2 to 4 options with a recommendation (`references/owner-questions.md:58-63`). Use "A. Scope is right. B. Scope too large (pick elements to drop). C. Scope too small (say what is missing)."
5. **Show the cost of each choice.** The option field already has a `consequence` (`store.ts:14`). For visual review, the consequence is the changed work packages.
6. **Diagram health gate before the owner.** Run the C4 checklist (title, legend, scope, names) and a size budget mechanically. Do not send a diagram that fails; see the density budget in [representations-beyond-uml.md](representations-beyond-uml.md).
7. **Time-boxed default.** If the owner does not answer in N hours, continue with the recommendation and log it, as for provisional rulings (`references/work-on/phase-3.md:210-225`). Needs an owner decision because of the "Never ask for approval" rule (`references/owner-questions.md:96`).

## 7. Measures to record (so the claim can be tested)

- Time from diagram posted to owner answer.
- Count of element verdicts by type.
- Count of re-plans caused by a diagram answer.
- Count of later contract arbitrations or `CONTRACT-CHANGE:` returns on runs with a visual review, against runs without (`references/work-on/phase-3.md:194-210` names the cost of those).

## 8. Open points

- The "never ask for approval" policy needs a decision (see [current-state.md](current-state.md) B3).
- The page cannot load external scripts today (`shared/questions/page.ts:1`), so any interactive diagram view needs a bundled renderer.
- Reviewable and Graphite review UX were not verified beyond the search results above.
