# Visual-Explainer (nicobailon/visual-explainer)

- **Repo**: https://github.com/nicobailon/visual-explainer (MIT, `inspiration-repos/visual-explainer/LICENSE`, copyright 2025 Nico Bailon)
- **Clone**: `inspiration-repos/visual-explainer/` (commit `9d5f880b7e8e0a9a80c890b8e1bb6918b68481ad`, 2026-11-04, v0.15.0 release)
- **Relevant to**: Q4 (clutter), Q5 (rendering), Q6 (review UX), Q8 (generation accuracy); features VR-03 and VR-05. Read as untrusted data; no script was run.

## What it is
A multi-host agent skill (Claude Code, Pi, MCP, Codex, Cursor and others) that turns "anything worth explaining" into one self-contained HTML page: architecture, diff review, plan review, fact-check, project recap, state machine, slide deck, narrated MP4. The skill is `plugins/visual-explainer/SKILL.md` plus `references/*.md`, ten slash commands in `commands/`, and a Node "kit" (`kit/render.mjs`, `kit/look.mjs`, `kit/kit.css`, `kit/kit.js`) that turns an agent-written `.src.html` into the final page and checks it in a browser. The agent hand-writes SVG; the kit supplies tokens, fonts, steppers, flow dots and custom tags (`<ve-flow>`, `<ve-bars>`, `<ve-ask>`).

It is a general explainer, not a change-review tool: no change graph, no stable ids, no status model, no layout engine (the agent places boxes on a grid by hand), no persistence of reviews. Its `diff-review` command is a prompt table of sections (verdict, file map, before/after SVG, risks, coupling, fixes) filled from `git diff`.

## Comparison with our capability (what we already have)
Covered by us: evidence on every claim (`sub-agents/diagram-author.md:152`, f05 VC-04), facts versus guesses (`inferred` tier, dashed edge, f03 VC-05), before/after in one set of coordinates (f03 VC-03), overview then detail (L0/L1/L2 budgets, `diagram-author.md:82-90`; their "over 12 nodes draw an overview"), state never colour only (f03 VC-04), per-change accept or leave (f04 element verdicts; their `<ve-ask>` fixes), step-through (f03 S8 walkthrough), product wording (`diagram-author.md:164`), a script-checked loop with three strikes (`diagram-author.md:118-167`), real server-side layout instead of hand-placed boxes (VR-03). Their pairwise eval (`eval/`) maps to the seeded-defect and false-positive set in `q9-measuring-value.md`.

## Ideas worth borrowing (not covered by us)
- **Rendered-page lint in a real browser** (`kit/look.mjs:39-170,300-325,407-423`): after render, headless Chromium measures the smallest SVG label at 390, 768 and 1280 px (fails under 12 px), text that overlaps other text, text that runs out of its drawing, sideways scroll, console errors, and headings with no gap. Our testability note leaves the client check open (`visual-review/features/03-dashboard-renderer/testability.md:4`); our canvas sets node text at 12 px and edge labels at 11 px (`shared/questions/page.ts:425,430`).
- **Silent label truncation is a defect**: they refuse a label that does not fit. Our canvas keeps 3 wrapped lines and drops the rest without a mark (`shared/questions/page.ts:430`), and the schema and validator set no label length (`references/visual-review/graph.schema.json:241`).
- **Writing rules for the generating agent** (`SKILL.md` "Show, don't tell" and "Words"): every arrow gets a verb (`writes`, `polls 30s`); the headline states the takeaway, not the topic; one term per concept; short active sentences; no hedges. Ours only fixes product words for node labels (`diagram-author.md:164`) and leaves edge labels and the graph title unguided.
- **Machine-walked state machines** (`references/machines.md`, `commands/state-machine.md`): import the real reducer, walk every reachable state, name dead ends, events that never fire, and rules the code breaks, then prove each with a replay script. Strong, but Rust-first extraction and a lifecycle view are not built (`q1-representations.md:27,38`); not recommended now.

## Ideas we already hold or reject
- Linked highlighting between prose terms and drawing parts, steppers with a caption per event citing the guard, flow dots, dimension lines, small multiples, waffle and bar tags: they serve explainer pages. Our change view has one claim per element and a walkthrough; none of these adds review value for a graph of added, changed and removed parts.
- Fact-check command (verified, corrected, unsupported, unverifiable strip): our validator and the anchor checker do this mechanically for every element (f05 VC-04, `q9-measuring-value.md` evidence coverage).
- Contact-sheet image for the agent to look at: rejected, vision models misread relations (best of 11 scored 70.18 percent, `q9-measuring-value.md:13`); our checks read structured data.
- Slides, MP4 video, PPTX export, themes, image generation, registers, anti-patterns list (no Inter, no neon): the dashboard owns its look; the author agent writes JSON, not HTML.
- Google Fonts and CDN libraries in the page (`kit/render.mjs`, Chart.js, three.js): rejected by f03 VC-01 (no foreign origin).
- Hand-drawn SVG by the agent: rejected by determinism and stable ids (f04 VC-05, f03 VC-03).
- Output as one static HTML file with no store: rejected, same reasons as OpenArc.

## What not to copy
- Any code from `kit/` or `plan/`: it is built for Node and many hosts, and ships a 678 KB vendored library (`kit/vendor/pele.mjs`) that would break the page size budget (f03 VC-09).
- The 80 percent STE writing level and the register system as such; take only the checkable rules above.

## License
MIT. Code and text can be reused with the copyright notice. The pages it generates load Google Fonts, which our dashboard must not do.
