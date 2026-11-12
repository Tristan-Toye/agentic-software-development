# Q8. Staleness and the living map

- **Question**: How does the dashboard show all product diagrams of a repo, and how does the system know a diagram is out of date?
- **Status**: PROPOSED.

## Facts: what config-harness shows

All rows are from [current-state.md](current-state.md), A2 and A3, checked on 2026-11-12.

| Check | Result |
|---|---|
| `module_graph.py check --against <file>` regenerates a c3 Mermaid block from `use` lines. CI runs it for six c3 files. | All six pass. |
| `check_diagram_anchors.py` checks each `crates/<path>.rs:<line>` anchor: file, line, backticked identifier. CI runs it with `if: always()`. | `OK 748 anchors`. |
| c2 text says "six members". | **Stale.** The workspace has 10 members. `fault` and `step-timing` appear in no diagram file. `step-timing` was added 2026-10-27. |
| c2 edge anchors `harness/Cargo.toml:63`, `graph-runtime/Cargo.toml:9`, `composition-root/Cargo.toml:27`. | **Stale.** They point to comments. Real lines: 82 and 14 for the first two. No script reads these anchors. |
| c1 "nine Docker Compose services". | Holds. |
| c4-classes-tools claims to cover every public trait, struct and enum in `tools/*.rs`. | **Partial.** 7 files are not named in the diagram. No script checks coverage. |
| Feature IDs in diagrams. | Only 6 of 32 files carry an `H-NN` token. |

Pattern: where a script checks the claim, the claim holds. Where no script checks it (workspace membership, edge anchors, completeness), drift exists. Anchors prove a named thing exists. They do not prove the diagram is complete.

## Facts: tools

- **Understand-Anything**: a fingerprint per file (content hash plus function, class and import signatures). A change classifier decides if a change is structural or cosmetic. A staleness banner says when the map is behind the code. The graph is committed to the repo (`.ua/`). ([understand-anything analysis](../../../inspiration-repos/analysis/understand-anything.md))
- **CodeBoarding**: `.codeboarding/` is tracked, with `fingerprint.json` and `static_analysis.sha`. Change detection diffs `{path: sha16}` maps with no git needed. Incremental update re-analyses only affected components against a baseline. ([codeboarding analysis](../../../inspiration-repos/analysis/codeboarding.md))
- **LikeC4**: a diff validates against the model. A removed element must exist, an added element must not. Use the same check to find a stale plan ([likec4 analysis](../../../inspiration-repos/analysis/likec4.md)).
- **architecture-check**: compares a declared model with a model derived from code and reports drift with `verified_static_fact | hypothesis | unknown` ([architecture-rule-checkers](../sources/architecture-rule-checkers.md)). It supports JS and TS only. Use the idea, not the tool.
- **CodeSee** was a hosted code-map service. The owner reports it shut down. This was **not verified** from a primary source. The lesson, "keep the map in the repo", holds either way ([code-visualisation-legacy-tools](../sources/code-visualisation-legacy-tools.md)).
- **CIAO** puts generated documentation in the target repo. Generation costs minutes ([ciao-architecture-documentation](../papers/ciao-architecture-documentation.md)).
- **Reflexion models** treat the mapping from model boxes to code as a stored, reviewable file. A diverging code is information ([reflexion-models-murphy-1995](../papers/reflexion-models-murphy-1995.md)).
- Pin all tool versions, because `ra_ap_*` breaks between releases ([cargo-modules analysis](../../../inspiration-repos/analysis/cargo-modules.md)).
- Recovered architecture helps agents localise code ([asner-architecture-recovery](../papers/asner-architecture-recovery.md)). So the map has a second use as context for the fan-out agents.

## Proposed design

**Two layers with different trust.**

| Layer | Source | Check |
|---|---|---|
| Structure (crate, module, edges) | Fresh extraction at run time. Never trusted from a committed copy. | Re-extracted each run, so no drift. |
| Product (features, one-line descriptions, feature-to-code links) | Committed in the target repo under `docs/architecture/` (config-harness already uses this folder, so its anchor checker applies). | Content hashes plus anchor check. |

The choice of "fresh each run" for structure follows the CH finding: the unscripted parts drifted. It is a proposal. The corpus lists this as an open point ([current-state.md](current-state.md), B4).

**Staleness record** per diagram, stored in the committed copy:

- A hash of each file that backs an element (content hash).
- A hash of public signatures per file, so cosmetic edits do not mark a diagram stale (fingerprint idea).
- The tool versions and the commit of the last extraction (the `static_analysis.sha` idea).

**Stale test.** Re-hash the backing files. If a hash differs and the signature hash differs, mark the element stale. If only the content hash differs, mark it "cosmetic change". Show a staleness banner on the dashboard with the count.

**Plan staleness.** Between Phase 3 and merge, the base may move. Apply the LikeC4 rule to the planned diff: every `removed` element must exist in the current base, every `added` element must not.

**Drift check.** Compare the committed (declared) product layer with the freshly derived one. Report `converge | diverge | absent` ([q2-diff-model.md](q2-diff-model.md)). Report drift. Do not fix it silently.

**Update step.** After a merge, the changed elements update the committed copy. This is the "living map". The config-harness c2 drift (6 versus 10 members) is the case to cover first: a script that compares `Cargo.toml` `members` with the c2 file would have caught it.

**All diagrams of a repo on the dashboard.** The dashboard lists the committed product map (feature map, flows, containers) per repo, and per run it lists the change diagrams. The inputs do not say how the dashboard reads a target repo. This is a build decision.

**Feature-to-code table.** Seed from `FEATURE-BOOK.md` and `TRACEABILITY.md` (H-01 to H-13 for the harness) ([current-state.md](current-state.md), A4). An LLM proposes links. Each link keeps the signal that made it (static, trace, text) ([feature-location-survey-dit-2013](../papers/feature-location-survey-dit-2013.md)). Each link has `path:line` evidence ([asner-architecture-recovery](../papers/asner-architecture-recovery.md)).

## Open points

- Where is the committed copy: `docs/architecture/` of the target repo, or a new `.visual-review/` folder? OQ-05.
- Who updates it: the diagram-author at merge time, or a separate command? Not in the inputs.
- First-run cost for a whole-repo product map. Understand-Anything warns of a high token cost on the first full run ([understand-anything analysis](../../../inspiration-repos/analysis/understand-anything.md)). A per-feature path is cheaper. No measurement exists.
- CodeCity-style heat map of change over the product map: later, not first ([q1-representations.md](q1-representations.md)).

## Recommendation

**BUILD** a staleness record per diagram: file content hashes plus signature hashes, tool versions and the extraction commit. **USE** the Understand-Anything fingerprint idea and the CodeBoarding hash map.

**USE** fresh extraction for structure and a committed copy under `docs/architecture/` for the product layer.

**BUILD** a declared-versus-derived drift check that reports `converge | diverge | absent`.

**USE** the LikeC4 validation rule to detect a stale plan.

**BUILD** the config-harness fix first as the acceptance case: a script that compares `Cargo.toml` members with the c2 diagram.

**SKIP** a hosted map service and the architecture-check tool.

## Sources

- Synthesis input: [current-state.md](current-state.md).
- Analyses: [understand-anything](../../../inspiration-repos/analysis/understand-anything.md), [codeboarding](../../../inspiration-repos/analysis/codeboarding.md), [likec4](../../../inspiration-repos/analysis/likec4.md), [cargo-modules](../../../inspiration-repos/analysis/cargo-modules.md).
- Sources: [architecture-rule-checkers](../sources/architecture-rule-checkers.md), [code-visualisation-legacy-tools](../sources/code-visualisation-legacy-tools.md), [understand-anything](../sources/understand-anything.md), [codeboarding](../sources/codeboarding.md).
- Papers: [reflexion-models-murphy-1995](../papers/reflexion-models-murphy-1995.md), [ciao-architecture-documentation](../papers/ciao-architecture-documentation.md), [asner-architecture-recovery](../papers/asner-architecture-recovery.md), [feature-location-survey-dit-2013](../papers/feature-location-survey-dit-2013.md).
