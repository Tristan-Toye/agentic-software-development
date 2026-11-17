# Feature 06: Living product map and staleness

- **Case ID**: VR-06
- **Status**: BUILT (scripts `vr_c2_drift.py`, `vr_map.py`, `vr_metrics.py`; the dashboard repo view and banner are still to be wired in VR-04/VR-05; see `references/visual-review/README.md`)
- **Dossier**: D6
- **Depends on**: VR-01, VR-03.
- **Design basis**: [Q8](../../research/synthesis/q8-staleness-and-living-map.md), [Q1](../../research/synthesis/q1-representations.md).

## What it is

A committed product map in the target repo (feature map, flows, containers) with a staleness record, and a dashboard view that lists all product diagrams of a repo. A drift script compares declared and derived. The first acceptance case is the config-harness c2 drift.

## In scope

- Committed copy under `docs/architecture/` (OQ-05 = A, taken by recommendation): `docs/architecture/product-map.json`.
- Staleness record: content hashes, signature hashes, tool versions, extraction commit.
- Stale test, plan-stale rule, banner with a count.
- Drift report: `converge | diverge | absent`.
- Feature-to-code table seeded from `FEATURE-BOOK.md` and `TRACEABILITY.md`.
- A script that compares `Cargo.toml` members with the c2 diagram.
- Dashboard "all diagrams" list per repo.

## Out of scope

- Heat map of change over the map. Later ([Q1](../../research/synthesis/q1-representations.md)).
- A hosted map service.

## Documents

[purpose.md](purpose.md), [value.md](value.md), [behavior-scenarios.md](behavior-scenarios.md), [testability.md](testability.md), [verification-criteria.md](verification-criteria.md).
