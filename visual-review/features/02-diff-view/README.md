# Feature 02: Diff view (as-is, changes, to-be)

- **Case ID**: VR-02
- **Status**: PROPOSED
- **Dossier**: D2
- **Depends on**: VR-01 (graph schema and validator).
- **Needed by**: VR-03, VR-04, VR-05.
- **Design basis**: [Q2](../../research/synthesis/q2-diff-model.md), [Q4](../../research/synthesis/q4-abstraction-and-clutter.md).

## What it is

A function that takes a "before" graph and a planned "after" graph and returns one merged graph. Every node and edge has `status: added | changed | removed | context`. The merged graph has one layout input. Three modes filter it: `as-is`, `changes`, `to-be`. A second function does the same for flows (ordered step lists) and collapses repeated steps.

## In scope

- Status computation, parent roll-up, mode filters, counts.
- The LikeC4 validation rule: `removed` must exist, `added` must not.
- Flow diff with a 12-step cap and run collapse.
- Plan-side marking: an element no stub signature proves is `inferred`.
- The change list data: "N added, N changed, N removed", with links to element IDs.

## Out of scope

- Layout and drawing (VR-03).
- The plan-versus-code reflexion check after the build. It is a candidate extension ([Q2](../../research/synthesis/q2-diff-model.md)); no case yet.

## Documents

[purpose.md](purpose.md), [value.md](value.md), [behavior-scenarios.md](behavior-scenarios.md), [testability.md](testability.md), [verification-criteria.md](verification-criteria.md).
