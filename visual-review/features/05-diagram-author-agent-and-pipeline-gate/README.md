# Feature 05: Diagram-author agent and pipeline gate

- **Case ID**: VR-05
- **Status**: BUILT
- **Dossier**: D5
- **Depends on**: VR-01, VR-02. VR-03 and VR-04 for the owner-facing part.
- **Design basis**: [Q7](../../research/synthesis/q7-pipeline-placement.md), [Q4](../../research/synthesis/q4-abstraction-and-clutter.md), [Q9](../../research/synthesis/q9-measuring-value.md).

## What it is

A new `diagram-author` agent that runs at the end of `/work-on` Phase 3, as its own spawn. A validator script checks its output. A `VISUAL-REVIEW:` build-log line is checked by `validate_pipeline.py --pre-fanout`. The run folder holds the output files. The run-detail JSON gets a `diagrams` field.

## In scope

- Agent file, payload, return format (guidance-style, no verdicts).
- Validator: schema, IDs, evidence paths, budgets, C4 checklist.
- Run folder `~/.agentic/runs/<repo>.<id>.diagrams/` with `graph.json`, `diff.json`, `elements.json`, a Mermaid export.
- The `--pre-fanout` check and the build-log line.
- Group A measures written to a sibling file.

## Out of scope

- The `/plan` placement (OQ-04).
- A size gate (OQ-06).
- Other languages.

## Documents

[purpose.md](purpose.md), [value.md](value.md), [behavior-scenarios.md](behavior-scenarios.md), [testability.md](testability.md), [verification-criteria.md](verification-criteria.md).
