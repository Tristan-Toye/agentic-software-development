# Q7. Pipeline placement

- **Question**: Where in `/plan` and `/work-on` does the diagram step run, which agent does it, what data goes in and out, and what gate checks it?
- **Status**: PROPOSED.

## Facts: the pipeline

All rows are from [current-state.md](current-state.md), B1 to B3. Paths are in the plugin repo.

| Fact | Evidence |
|---|---|
| `/plan` writes one dossier with `## Contract`, `## Work packages` (disjoint paths) and `## Acceptance criteria`. It writes `status: ready`. | `primary-agents/plan.md:266,313,326,412` |
| `/plan` runs a mechanical check (Phase 5) and a one-spawn plan review (Phase 6). | `primary-agents/plan.md:358-366` |
| `/work-on` Phase 2 materialises the contract as stub files in the base worktree and syncs the contract back into the dossier. | `references/work-on/phase-2.md:73-120` |
| Phase 3 runs the mechanical check, then spawns `contract-reviewer` before the fan-out. The reviewer sees only the stub files, the criteria and the checklist rules. | `references/work-on/phase-3.md:4-13,194-201` |
| `validate_pipeline.py --dossier <ID> --pre-fanout` refuses to pass when required build-log lines are missing. | `phase-3.md:17,87,117,131` |
| Fixing a contract after fan-out costs re-spawns. | `phase-3.md:194-210` |
| Phase 4 is the fan-out: unit-test authors, integration-test author and implementers run concurrently and blind. | `primary-agents/work-on.md:238` |
| Support agents get small mechanical tasks and return guidance docs (pointer, verbatim quote, neutral flag), never verdicts. | `phase-2.md:22-52` |
| The runner stores run files under `~/.agentic/runs/` as `<repo>.<id>.jsonl`, `.summary.md`, `.restart`. | `shared/questions/runstore.ts:10,119`; `server.ts:470` |
| The pipeline has a one-hop `blast-radius-scout` agent. | `phase-2.md:55-62` |
| The plugin has `nav` MCP tools (`code_find`, `code_outline`, `code_item`). | [code-graph-mcp-servers](../sources/code-graph-mcp-servers.md) |

Other facts:

- Reviewers compare the expected solution with the actual one, and start with context ([code-review-comprehension-strategies](../papers/code-review-comprehension-strategies.md)). This supports plan-first diagrams.
- A review tool must sit where the owner already decides ([software-city-code-review](../papers/software-city-code-review.md)). The decision point is the dashboard question.
- A link from the work item to the visualisation removes manual data collection. The dossier can link to its diagram ([software-city-code-review](../papers/software-city-code-review.md)).
- A separate validator agent role is better than a generator that checks itself ([agenticakm](../papers/agenticakm.md)). Understand-Anything has a graph-reviewer agent with a validation script ([understand-anything analysis](../../../inspiration-repos/analysis/understand-anything.md)).

## Proposed placement

**At the end of `/work-on` Phase 3**, after the contract-review fixes and before the Phase 4 fan-out. Reasons ([current-state.md](current-state.md), B3):

- At `/plan` the contract is only text. At Phase 2 it is real stubs, so a diagram can be grounded in compiling signatures.
- Phase 3 already holds the other pre-fanout gates. A `VISUAL-REVIEW:` line fits the same mechanism.
- A wrong plan found on a diagram costs the least here.

**Option, not required:** a light version in `/plan` Phase 6, so the owner sees the intent before any stub exists ([current-state.md](current-state.md), B3). Open question OQ-04.

**New agent: `diagram-author`.**

- It is a separate spawn from `contract-reviewer`. The reviewer is blind on purpose, so do not mix them ([current-state.md](current-state.md), B3).
- It returns a guidance-style document (pointers, quotes, flags). It does not rule ([current-state.md](current-state.md), B3).
- Model: the plugin's agent description says `contract-reviewer` is the one sub-agent on the large model. That description is not a corpus document. Diagram naming and grouping is a medium task. This document proposes the small or medium model. The model is set in the agent file. (Inference.)
- It has no write tool for product code. It writes only into the run diagram folder. (Inference, following the reviewer's read-only pattern.)

**Data in.**

1. The change graph "before": fresh extraction from the base worktree ([q3-extraction-from-rust.md](q3-extraction-from-rust.md)).
2. The stub files, as a diff against the base.
3. The dossier: problem, approach, acceptance criteria, work packages.
4. The feature-to-code table, if present ([q8-staleness-and-living-map.md](q8-staleness-and-living-map.md)).

**Data out.** Per run, in `~/.agentic/runs/<repo>.<id>.diagrams/` next to the summary and the JSONL ([current-state.md](current-state.md), B3):

- `graph.json`: the merged change graph with status and evidence.
- `diff.json`: added, removed and changed element IDs, with counts.
- `elements.json`: each element ID to `path:line`.
- A source diagram text (Mermaid export) and a rendered SVG for static use.

**Gate.**

- The dossier gets a build-log line `VISUAL-REVIEW: <ref> — <N> elements, <N> added, <N> changed, <N> questions`. `--pre-fanout` checks it ([current-state.md](current-state.md), B3).
- The validator runs before the owner sees anything: schema, IDs, evidence paths exist, budgets ([q3-extraction-from-rust.md](q3-extraction-from-rust.md), [q4-abstraction-and-clutter.md](q4-abstraction-and-clutter.md)), C4 checklist (title, legend, scope, names).
- Then the orchestrator asks one product question ([q6-approval-and-comments.md](q6-approval-and-comments.md)).

**Dashboard.** The run-detail JSON gains a `diagrams` field next to `summary`, `blocks`, `rulings` ([current-state.md](current-state.md), B2).

## Open points

- Size gate: does the visual review run on every build or above a size? `contract-reviewer` has no size gate. The phase 2 table uses gates for support agents ([current-state.md](current-state.md), B4).
- "Before" source: fresh each run, or the committed living map? See [q8-staleness-and-living-map.md](q8-staleness-and-living-map.md).
- Blocking: a pending owner answer blocks the run. A time-boxed default is possible by the provisional-ruling pattern but conflicts with the ask policy ([q6-approval-and-comments.md](q6-approval-and-comments.md)).
- The extractor is language-specific. The first target is Rust. How other languages plug in is not researched.

## Recommendation

**BUILD** a new `diagram-author` agent and run it at the end of Phase 3 as its own spawn, with a `VISUAL-REVIEW:` build-log line checked by `validate_pipeline.py --pre-fanout`.

**USE** the per-run folder `~/.agentic/runs/<repo>.<id>.diagrams/` for data, and a `diagrams` field in the run-detail JSON.

**USE** a separate validator script before the owner sees the diagram.

**SKIP** the `/plan` placement for the first version. Revisit it as OQ-04.

**SKIP** mixing the diagram step into the `contract-reviewer` spawn.

## Sources

- Synthesis input: [current-state.md](current-state.md), [review-ux-and-feedback.md](review-ux-and-feedback.md).
- Papers: [code-review-comprehension-strategies](../papers/code-review-comprehension-strategies.md), [software-city-code-review](../papers/software-city-code-review.md), [agenticakm](../papers/agenticakm.md).
- Analyses: [understand-anything](../../../inspiration-repos/analysis/understand-anything.md), [openarc](../../../inspiration-repos/analysis/openarc.md).
- Sources: [code-graph-mcp-servers](../sources/code-graph-mcp-servers.md).
