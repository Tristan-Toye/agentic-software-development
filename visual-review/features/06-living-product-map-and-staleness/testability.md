# Testability

- Hash and stale logic: pure functions with file fixtures.
- Members script: run on a frozen copy of the 2026-11-12 config-harness files as a fixture. This is a real, known failing case.
- Anchor validity: reuse the existing anchor checker (748 anchors OK on 2026-11-12) ([current-state](../../research/synthesis/current-state.md), A2).
- Seeded table: an LLM proposes links, so test the links mechanically (paths exist, lines exist), not the choice.
- Hard to test: first-run token cost. Measure it; no figure exists ([Q8](../../research/synthesis/q8-staleness-and-living-map.md)).
