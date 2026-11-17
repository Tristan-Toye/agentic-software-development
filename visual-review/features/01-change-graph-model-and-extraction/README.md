# Feature 01: Change graph model and extraction

- **Case ID**: VR-01
- **Status**: BUILT. Schema `references/visual-review/graph.schema.json` (1.0.0), validator `scripts/vr_graph.py`, extractor `scripts/vr_extract.py` (+ `scripts/vr_ra.py`), fixtures in `references/visual-review/fixtures/`. See `references/visual-review/README.md`.
- **Dossier**: D1 (after the spike D0). See [../../../research/OVERVIEW.md](../../../research/OVERVIEW.md), "Sizing".
- **Depends on**: nothing.
- **Needed by**: VR-02, VR-03, VR-05, VR-06.
- **Design basis**: [Q2](../../research/synthesis/q2-diff-model.md), [Q3](../../research/synthesis/q3-extraction-from-rust.md), [Q4](../../research/synthesis/q4-abstraction-and-clutter.md).

## What it is

A JSON schema for one change graph, a validator for it, and a layered extractor that reads a Rust Cargo workspace and writes the "before" graph. The schema has nodes, edges, groups, flows, status, evidence and `resolution`. The extractor uses `cargo metadata` for crates and `cargo-modules` for modules. A call-hierarchy query fills flow steps on request.

## In scope

- The schema and its version field.
- The validator, with typed issues and a size budget check.
- The extractor for layers 1 and 2, and the on-demand layer 4 query.
- Pinned tool versions in the output metadata.

## Out of scope

- The "after" side and the diff (VR-02).
- Drawing (VR-03).
- LLM naming and grouping (VR-05 runs it; VR-01 only defines the fields it fills).
- Languages other than Rust.

## Documents

[purpose.md](purpose.md), [value.md](value.md), [behavior-scenarios.md](behavior-scenarios.md), [testability.md](testability.md), [verification-criteria.md](verification-criteria.md).
