# Visual review — follow-ups

Work decided but not done yet. Each item says what to do, when, and how to know it is done.

## FU-01. Upgrade cargo-modules past the 0.27.0 pin

- **What:** `scripts/vr_extract.py` pins `CARGO_MODULES_PIN = "0.27.0"`. Any other version makes the extractor skip the
  module layer. 0.28.0 is out (checked 2026-11-13).
- **Owner decision (2026-11-13):** keep the pin for the first demo and record the upgrade as a separate step. The pin
  lives in the plugin, not in config-harness, so this is a plugin follow-up, not a config-harness dossier.
- **When:** after the first visual-review demo at the new stub.
- **How:**
  1. `cargo install cargo-modules --version 0.28.0 --locked` in a scratch environment.
  2. Run `vr_extract.py rust` on the config-harness scratch clone with both versions, at the same commit, and compare the
     module node and edge sets of every crate (stable ids).
  3. Run `vr_c3_agree.py` (VC-06) and `vr_extract.py --selftest` under the new version.
  4. If the sets match, or every difference is explained, move the pin and update the version mentions in
     `references/visual-review/README.md`.
- **Done when:** the pin names the new version, the selftests and VC-06 pass, and the comparison is recorded here.

## FU-02. Bound label length and stop silent truncation

- **Status:** done in 2b5bd76 (limits node 80, group and flow 60, edge and lane 40, title 100; code `label_too_long`).
- **What:** the canvas wraps a node label to 3 lines and drops the rest without a mark (`shared/questions/page.ts`, `vrWrap(...).slice(0, 3)`),
  and neither `graph.schema.json` nor `vr_graph.py` limits a label. Found in the Visual-Explainer review
  (`inspiration-repos/analysis/visual-explainer.md`).
- **When:** with the next change to the renderer or the validator.
- **How:** add a label length limit to `vr_graph.py validate` (a new issue code, a fixture for it, agent repair loop already
  handles it); in the canvas, end a clipped label with an ellipsis and keep the full label in the inspector.
- **Done when:** a 200-character label fails validation, a label near the limit shows no clipping on a 12-node fixture, and
  the full label is readable in the inspector.

## FU-03. Writing rules in the diagram-author prompt

- **Status:** done in 2b5bd76 (rules 5 to 7 of the agent; W-145 titles and edge labels reworded).
- **What:** the agent has one wording rule (product words for node labels). Visual-Explainer adds three that a reader notices:
  every edge label is a verb phrase, the graph title states the takeaway rather than the topic, one term per concept.
- **When:** the next time `sub-agents/diagram-author.md` is edited, or when the first demo shows vague edge or title text.
- **How:** add the three rules to the Rules list in both agent copies (`sub-agents/` and `claude/agents/`); keep "names and groups,
  nothing else" (a label is not a new edge). Compare titles and edge labels on the W-145 fixtures before and after.
- **Done when:** both copies carry the rules and the W-145 fixture edges and titles follow them.

## FU-04. Browser check of the rendered canvas

- **What:** nothing measures the rendered page: label size, overlapping text, text outside its box, sideways scroll on a phone,
  console errors. `testability.md` of VR-03 leaves the client check open. Visual-Explainer does this in `kit/look.mjs`.
- **When:** before the page is widely used, after FU-02. Needs an owner decision on a headless browser dependency.
- **How:** a selftest that opens the canvas on the W-145 fixtures at 390 and 1280 px and fails on a label under 12 px, two
  overlapping label boxes, a label wider than its node, a horizontal page scroll or a console error. Read the DOM; do not
  send screenshots to a model.
- **Done when:** the selftest passes on the fixtures and fails on one seeded overflow fixture.
