# Feature 03: Dashboard renderer

- **Case ID**: VR-03
- **Status**: BUILT (server layout, canvas with L0-L2 tabs, Before/After/Diff, pan and zoom, evidence, mermaid export; group open/close, mini-map and walkthrough not yet)
- **Dossier**: D3
- **Depends on**: VR-01, VR-02 (or fixtures of their output).
- **Needed by**: VR-04, VR-06.
- **Design basis**: [Q5](../../research/synthesis/q5-rendering-stack.md), [Q4](../../research/synthesis/q4-abstraction-and-clutter.md), [Q1](../../research/synthesis/q1-representations.md).

## What it is

A server-side layout step (ELK on the Bun server) and a client-side canvas on the run page of the dashboard. The server sends positions as JSON. The inline client script builds SVG with `createElementNS` and sets all text with `textContent`. The canvas has pan, zoom, mode switch, group open and close, a mini-map and an inspector that shows `path:line` evidence. A `diagrams` field joins the run-detail JSON.

## In scope

- ELK layout on the server, model order on, with the cheaper mode above a size limit.
- The canvas: select, pan, zoom, collapse, mini-map.
- Status marks with two channels, evidence badge, `inferred` edge style.
- The flow view (lanes, steps, walkthrough mode).
- A mermaid text export of the same model.

## Out of scope

- Comments and verdicts (VR-04).
- The browse-all view of the committed map (VR-06) beyond a list that uses the same canvas.
- A React island. That is an owner decision (OQ-02); this case assumes hand-written DOM.

## Documents

[purpose.md](purpose.md), [value.md](value.md), [behavior-scenarios.md](behavior-scenarios.md), [testability.md](testability.md), [verification-criteria.md](verification-criteria.md).
