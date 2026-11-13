# Open questions for the owner

Each question has options and a proposal. The proposal is not a decision. Status: OQ-01 and OQ-06 DECIDED by the owner (2026-11-12); the others taken by recommendation under the owner's autonomy instruction (2026-11-12), open to change.

## OQ-01. The "Never ask for approval" conflict

- Context: the ask policy says "Never ask for approval" (`references/owner-questions.md:96`). Product questions are allowed. A visual review is an approval gate ([Q6](../visual-review/research/synthesis/q6-approval-and-comments.md)).
- Audit note: code enforces the policy. `scripts/ask_owner.py` refuses approval-shaped questions under the runner (`APPROVAL_PATTERNS`, lines 135-168; off with `ASD_ASK_LINT=0`). Option B must pass that check. Option A needs a lint change.
- Options: A, a named exception. B, frame it as a product question. C, no gate.
- Proposal: B, plus one sentence in the policy that names the visual-review question as a product question.
- **Decision (owner, 2026-11-12): B.** The visual review is the product question "Is this what the feature should do?"; `references/owner-questions.md` gets one sentence naming it a product question, and the question must pass `ask_owner.py`'s approval lint unchanged. The run waits for the answer.
- **Owner decision 2026-11-13:** the visual-review question may use the word "approve"; the option labels stay "Approved" and "Changes requested" (VC-07 of VR-04 is waived).
- Blocks: [VR-04](../visual-review/features/04-approval-and-element-comments/README.md).

## OQ-02. React island or hand-written canvas

- Context: the dashboard has no framework ([Q5](../visual-review/research/synthesis/q5-rendering-stack.md)).
- Options: hand-written SVG with `@xyflow/system` helpers; `@xyflow/react` as an island.
- Proposal: hand-written first. Use the island if the D0 spike shows the canvas costs too much.
- **Taken by recommendation (owner's autonomy instruction, 2026-11-12):** A, the hand-written SVG canvas. D0 measured 5.3 KB of canvas code; `@xyflow/system` would add 63.8 KB and a build step ([d0/REPORT.md](d0/REPORT.md)).

## OQ-03. How deep the Rust extraction goes in version 1

- Context: layers run from `cargo metadata` to `ra_ap_*` ([Q3](../visual-review/research/synthesis/q3-extraction-from-rust.md)).
- Options: crate and module level only; add call edges with a language-server call hierarchy.
- Proposal: crate and module level, plus stub signatures from the diff. Add calls after the spike.
- **Taken by recommendation (owner's autonomy instruction, 2026-11-12):** crate and module level, plus calls on request (cargo metadata plus cargo-modules 0.27.0 by default; a rust-analyzer call hierarchy only for functions named with `--calls`).

## OQ-04. Also run a light version in `/plan`

- Context: [Q7](../visual-review/research/synthesis/q7-pipeline-placement.md).
- Options: Phase 3 only; Phase 3 plus a light plan-time view.
- Proposal: Phase 3 only for version 1.
- **Taken by recommendation (owner's autonomy instruction, 2026-11-12):** A, no light version in `/plan` for version 1.

## OQ-05. Where the committed map lives

- Context: [Q8](../visual-review/research/synthesis/q8-staleness-and-living-map.md).
- Options: `docs/architecture/` (the anchor checker already covers it); a new `.visual-review/` folder.
- Proposal: `docs/architecture/`.
- **Taken by recommendation (owner's autonomy instruction, 2026-11-12):** A. The map is `docs/architecture/product-map.json`. The anchor checker reads only `.md`, so `vr_map.py build --table` writes a checked feature-to-code table beside it, and `vr_map.py check` verifies the JSON.

## OQ-06. Size gate

- Context: contract review has no size gate ([Q7](../visual-review/research/synthesis/q7-pipeline-placement.md)).
- Options: every build; builds above a size; owner switch per dossier.
- Proposal: every build at first, so the measures in [Q9](../visual-review/research/synthesis/q9-measuring-value.md) have data. Add a gate later.
- **Decision (owner, 2026-11-12): every build** at first; revisit a size gate once the Q9 measures have data.

## OQ-07. Recall threshold for the L0 map

- Context: [Q9](../visual-review/research/synthesis/q9-measuring-value.md).
- Options: any value the owner picks, for example "the map marks 9 of 10 touched features".
- Proposal: set it after the first eval set of 10 changes exists.
- **Taken by recommendation (owner's autonomy instruction, 2026-11-12):** no threshold yet. `vr_map.py recall` measures file and crate recall; the threshold stays null until the eval set of 10 changes exists.

## OQ-08. Time-boxed default for an unanswered question

- Context: a pending answer blocks the run. A default conflicts with the ask policy ([Q6](../visual-review/research/synthesis/q6-approval-and-comments.md)).
- Options: block until answered; continue with the recommendation after N hours and log it.
- Proposal: block, until OQ-01 is settled.
- **Taken by recommendation (owner's autonomy instruction, 2026-11-12):** A, block until answered. This is consistent with OQ-01 = B.

## OQ-09. First-run scope of the product map

- Context: a whole-repo first run may cost many tokens. No measurement exists ([Q8](../visual-review/research/synthesis/q8-staleness-and-living-map.md)).
- Options: whole repo; per feature as changes touch it.
- Proposal: per feature.
- **Taken by recommendation (owner's autonomy instruction, 2026-11-12):** B, the map grows per feature. `vr_map.py build` needs `--features`, `--touched` or `--all`.

## OQ-10. Languages after Rust

- Context: the extractor is language-specific ([Q7](../visual-review/research/synthesis/q7-pipeline-placement.md)).
- Options: Rust only; a plug-in interface from day one.
- Proposal: Rust only. Define the graph schema language-neutral so a later plug-in fits.
- **Taken by recommendation (owner's autonomy instruction, 2026-11-12):** Rust only, with the graph schema language-neutral so a later plug-in fits (`extract(lang, ...)` dispatch with `rust` as the only entry).
