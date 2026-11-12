# Current state: config-harness diagrams and the plugin pipeline

- **Design questions covered**: Q2 (grounding in code), Q3 (code-to-feature mapping), Q5 (sequence diagrams), Q7 (living sync), Q9 (review and approval).
- **Method**: files read in this repo (the plugin worktree) and in the config-harness checkout (read only). Checks were run with the repo's own scripts on 2026-11-12.
- Cross-links: [review-ux-and-feedback.md](review-ux-and-feedback.md), [representations-beyond-uml.md](representations-beyond-uml.md).

Paths marked `CH:` are in `/Users/tristan.toye/Documents/personal/repos/config-harness`. Other paths are in this plugin repo.

## Part A. Existing diagrams in config-harness

### A1. Inventory

All diagrams live in `CH:docs/architecture/`. There are 32 diagram files and one prose threat model, 5057 lines in total, with 38 Mermaid blocks (`CH:docs/architecture/README.md:1-97`; counted with `grep -c`). Every diagram is Mermaid text in Markdown. No other diagram format exists in the repo outside `docs/architecture/` (searched for plantuml, drawio and svg).

| Group | Files | Format | Level | What it shows |
|---|---|---|---|---|
| c1 | `c1-context.md` | Mermaid | C4 context | The Lima VM, nine compose services, the ZFS pool, the developer (`c1-context.md:4,60-70`). |
| c2 | `c2-containers.md` | Mermaid `flowchart LR` | C4 container | The Cargo workspace crates and their dependency edges, each edge labelled with a `Cargo.toml:<line>` anchor. |
| c3 (6 files) | `c3-components-{protocol,harness,graph-runtime,platform,test-support,composition-root}.md` | Mermaid `graph LR` | C4 component | One crate's module graph, derived from `use` lines, plus a fan table. |
| c4 (8 files) | `c4-classes-{ports,transport,track-runner,tools,sandbox,secrets,composition-root,ops}.md` | Mermaid class diagrams | C4 code (UML-like) | Public traits, structs and enums of one area, each with one line anchor. |
| seq (16 files, 22 `sequenceDiagram` blocks) | `seq-turn.md`, `seq-tool-call.md`, `seq-risk-gate.md`, `seq-write-tool.md`, `seq-human-input.md`, `seq-nats-auth.md`, `seq-startup.md`, `seq-boot.md`, and eight more | Mermaid `sequenceDiagram` | Runtime flow | One call flow each, with `Note` anchors to code lines. |
| threat model | `threat-model-risk-gate.md` | Prose and tables | Security view | Assets, evasion classes, ownership boundary. |

The README says the folder answers "what the tree, as it stands, actually shapes into" and that it does not hold the why (ADRs) or what a feature is for (feature book) (`CH:docs/architecture/README.md:3-8`).

### A2. How the diagrams stay current

Two scripts enforce the two kinds of diagram that a script can check (`CH:docs/architecture/README.md:10-22`).

1. `scripts/module_graph.py check --against <file>` regenerates a c3 Mermaid block from `use` lines and fails on any diff. CI runs it for every `c3-components-*.md` (`CH:.github/workflows/ci.yml:1038`).
2. `scripts/check_diagram_anchors.py` reads each `crates/<path>.rs:<line>` anchor and confirms that the file exists, the line exists and the backticked identifier is still on that line. CI runs it with `if: always()` (`CH:.github/workflows/ci.yml:1143-1145`). It has a `--fix` mode (`ci.yml:1049`).

Result of running both on 2026-11-12: all six c3 files pass; `check_diagram_anchors.py` prints `OK 748 anchors`. The anchor script runs on every CI run, so line drift in c4 and seq files fails the build. This is the strongest grounding mechanism in the repo (Q2, Q7).

The c1 and c2 files have no script check. Their line numbers are "kept right by hand" (`CH:docs/architecture/README.md:24-29`; `c2-containers.md:3-8`).

The diagrams were written by the W-011 dossier and were updated by later dossiers. The last commits that touch the folder are from 2026-11-07 (`git log -- docs/architecture`). There is no generator for c1, c2, c4 and seq files: an agent writes them by hand and the anchor script checks them.

### A3. Staleness spot checks against `crates/`

| # | Claim in the diagram | Check | Result |
|---|---|---|---|
| 1 | c2 title and text: "The Cargo workspace's six members" (`CH:c2-containers.md:1-3`) | `CH:Cargo.toml` `members` | STALE. The workspace has 10 members: `protocol`, `fault`, `step-timing`, `harness`, `graph-runtime`, `platform`, `test-support`, `composition-root`, `sandbox-tool`, `workspace-hack`. `fault` and `step-timing` appear in no diagram file (`grep -l` count 0). `step-timing` was added 2026-10-27 (`git log --diff-filter=A`). |
| 2 | c2 edge `harness -> protocol` anchored at `crates/harness/Cargo.toml:63` | `sed -n 63p` | STALE. Line 63 is a comment. The dependency is on line 82. The anchor `graph-runtime/Cargo.toml:9` also points to a comment (real line 14). The anchor `composition-root/Cargo.toml:27` points to a comment. No script reads these anchors. |
| 3 | c1 "nine Docker Compose services" (`c1-context.md:4`) | `CH:deploy/compose/docker-compose.yml` profiles | HOLDS. Seven `core` services plus `llm-gateway` and `fake-llm` (profile `test`) make nine. The three `sandbox-image` services are build-only and the file says so (`docker-compose.yml:1-12`). |
| 4 | All c4 and seq anchors | `check_diagram_anchors.py` | HOLDS: 748 of 748 anchors. |
| 5 | c3 module graphs | `module_graph.py check` | HOLDS for all six crates. |
| 6 | c4-classes-tools covers "every `pub trait`, `pub struct` and `pub enum` of `crates/harness/src/tools/*.rs`" (`README.md` file table) | Compare file names in `crates/harness/src/tools/` with names in the c4 file | PARTIAL. The files `ask_human.rs`, `boundary.rs`, `git_mirror.rs`, `mirror_maintenance.rs`, `red_corpus.rs`, `red_oracle.rs` and `util.rs` are not named in `c4-classes-tools.md`. Some may be covered under other names (`seq-human-input.md` covers `ask_human`). The coverage claim is not checked by any script. |

Summary of the pattern: where a script checks the claim, the claim holds. Where no script checks it (workspace membership, edge anchors, completeness), drift exists. Anchors prove that a named thing exists. They do not prove that the diagram is complete (Q7).

### A4. Mapping to features (Q3)

The feature book is `CH:FEATURE-BOOK.md` (408 lines, entry point). It has one folder per major feature. Harness features H-01 to H-13 live in `CH:config-harness/features/harness/` (13 sub-feature folders: `01-tool-layer` to `13-tool-adoption-steering`). Each sub-feature has `README.md`, `purpose.md`, `value.md`, `behavior-scenarios.md`, `testability.md` and `verification-criteria.md` (`FEATURE-BOOK.md:19-24`). A traceability matrix links each H-xx to pillars, principles and research (`CH:config-harness/features/harness/TRACEABILITY.md:1-25`). Agent-graph features (M, PF, UI) live in `CH:agent-graph/features/`.

The diagrams barely use feature IDs. Only 6 of 32 diagram files contain an `H-NN` token, and only H-01, H-05, H-06 and H-09 occur (`grep -o 'H-[0-9][0-9]'`). Example: `seq-conversation-compaction.md:3` says "(H-06, W-091)". The diagrams are organised by crate and by code flow, not by feature. There is no feature-to-code map in either direction. This is the main gap for the visual-review capability: the owner thinks in features, and the diagrams think in crates.

Dossier IDs (W-011, W-091, W-145) appear in the diagrams, so a partial link to the work history exists.

### A5. Related precedent: diagrams as review input

The W-011 dossier produced the folder and then an architecture review with proposals (`CH:.discovery/analysis/W-011-architecture-review.md`, `W-011-proposals/A-01..A-02, P-01..P-02`). Those proposals cite `docs/architecture/` files. So diagrams already serve as review evidence, after the build, not before it.

## Part B. The plugin pipeline and where a diagram step fits

### B1. Pipeline facts (with file:line)

| Fact | Evidence |
|---|---|
| `/plan` writes one dossier with `## Contract` (signatures plus doc comments), `## Work packages` (disjoint paths) and `## Acceptance criteria`. It writes `status: ready`. | `primary-agents/plan.md:266` (Contract), `:313` (packages), `:326` (criteria), `:412` (ready). |
| `/plan` runs a mechanical check (Phase 5) and a one-spawn plan review (Phase 6) before `ready`. | `primary-agents/plan.md:358-366`. |
| `/work-on` Phase 2 materialises the contract as real stub files in the base worktree `X`, and lands the files back into the dossier with `dossier_edit.py sync-contract`. | `references/work-on/phase-2.md:73-120`, `primary-agents/work-on.md:230`. |
| `/work-on` Phase 3 runs the mechanical check, then spawns `contract-reviewer` "now, before the fan-out", with only the stub files, the criteria and the checklist rules. | `references/work-on/phase-3.md:4-13` (build-log line `CONTRACT-REVIEW:`), `:194-201`. |
| `validate_pipeline.py --dossier <ID> --pre-fanout` refuses to pass when required build-log lines are missing. | `references/work-on/phase-3.md:17,87,117,131`. |
| Phase 3 checks owned paths are disjoint. | `references/work-on/phase-3.md:25-41`. |
| Phase 4 is the fan-out: unit-test authors, integration-test author and implementers run concurrently and blind. | `primary-agents/work-on.md:238`. |
| Sub-agents use a payload. The orchestrator gives support agents small mechanical tasks and gets "guidance docs" (pointer, verbatim quote, neutral flag), never verdicts. | `references/work-on/phase-2.md:22-52`. |
| The runner starts commands and stores run files under `~/.agentic/runs/` as `<repo>.<id>.jsonl`, `.summary.md`, `.restart`. | `shared/questions/runstore.ts:10,119`; `shared/questions/server.ts:470`. |
| The owner ask policy: ask only design, product or real-uncertainty questions. "Never ask for approval." | `references/owner-questions.md:93-100`; `primary-agents/work-on.md:294`. |

### B2. Dashboard facts

- The dashboard is `shared/questions/server.ts` (HTTP and JSON API) and `shared/questions/page.ts` (one inline HTML, JS and CSS string, "no external resources"; dynamic text goes through `textContent`; the only `innerHTML` is the server's escaped Markdown render of a dossier) (`shared/questions/page.ts:1-3`).
- A question is an append-only JSONL file: `Question` has `options` (key, label, consequence), `recommendation`, `blocks`, `qkind`, `lines` and `answer` (`shared/questions/store.ts:6-30`). The first owner answer is final; later lines throw `AnsweredError` (`store.ts:105-127`).
- The owner answers with `POST /api/questions/:id/messages` with `kind` `followup` or `answer`, an optional `option` key and `text` (`shared/questions/server.ts:798-830`). Follow-up lines let the owner ask the agent a question without closing the question (`server.ts:812-813`). Slack mirrors the same store (`server.ts:125-137`).
- The agent asks with `ask_owner.py ask --option "A|label|consequence" ... --recommend A --blocks TEXT --kind design|product|uncertainty` (2 to 4 options) (`references/owner-questions.md:58-63`; `scripts/ask_owner.py:161-164`).
- Block rulings: a refused tool call becomes a `Block`; the owner presses once, always or deny. The server answers the agent's open question and, for "always", saves a rule (`shared/questions/server.ts:539-606`, `runstore.ts:119-146`, `shared/questions/rulings.ts:11-56`). This is a permission flow, not a design-review flow.
- Product summary: `/product-summary` runs after Phase 9 and writes `<key>.summary.md`. The dashboard renders it above the dossier (`commands/product-summary.md:1-20`; `scripts/run_dossier.py:1187-1210`; `shared/questions/server.ts:468-477`, endpoint `:607-622`). It is Markdown only; no diagram support.
- The run detail API returns `summary`, `blocks`, `rulings`, `blockQuestion`, `sections` in one JSON object (`server.ts:477`). A new `diagrams` field would sit next to them.
- There is no Mermaid, SVG or image renderer in the page (`grep -i 'mermaid\|<svg'` in `page.ts` finds nothing). A diagram view needs either a server-side render to SVG, or an exception to the "no external resources" rule.

### B3. Recommended integration point

Observation: the pipeline already has a gap between "contract written as stubs" (end of Phase 2) and "fan-out" (Phase 4). `contract-reviewer` sits there for contract text. The diagram step should sit next to it.

**Recommendation: a new Phase 3 step, "visual review", between the contract-review fixes and the Phase 4 fan-out.**

Why Phase 3 and not `/plan`:
- At `/plan` the contract is only text in the dossier. At Phase 2 it is real stubs, so a diagram can be grounded in compiling signatures (Q2).
- Phase 3 already holds the other pre-fan-out gates, and `validate_pipeline.py --pre-fanout` already enforces build-log lines, so a `VISUAL-REVIEW:` line fits the same mechanism (`phase-3.md:13-17`).
- Fixing a contract after fan-out costs re-spawns (`phase-3.md:194-210`). A wrong plan found on a diagram costs the least here.

Option to also run a light version in `/plan` Phase 6 (plan review), so the owner sees the intent before any stub exists. This is an open design choice; it is not required for the first version.

**Data in for the sub-agent (a new agent, for example `diagram-author`):**
1. `CONTRACT_PATHS`: the materialised stub files (signatures plus doc comments), the same input as `contract-reviewer` (`phase-3.md:196-199`).
2. The `## Work packages` table with owned paths, to group elements by package.
3. The acceptance criteria and the dossier `## Problem` and `## Approach`, so the diagram speaks about product behaviour. (Unlike `contract-reviewer`, this agent needs the dossier. `integration-test-author` is the precedent: it reads the dossier but not bodies.)
4. `baseline_commit` and the blast-radius list from `blast-radius-scout` (`phase-2.md:55-62`), to ground the "before" side and the changed region.
5. For the "before" side: a read-only view of existing code at `baseline_commit`, through `code-scout` or the `nav` tools, plus (when present) the repo's own `docs/architecture/` files and their anchors. For config-harness this reuses the existing 748 checked anchors.
6. The feature book entry (`FEATURE-BOOK.md`, the H-xx folder) to name the features.

The agent returns a guidance-style document (pointers, quotes, flags). It does not rule. This matches the delegation rule "evidence never verdicts" (`phase-2.md:22-30`).

**Storage:**
- Per run: `~/.agentic/runs/<repo>.<id>.diagrams/` next to `<key>.summary.md` and `<key>.jsonl` (`server.ts:470`). Files: a source diagram text, a rendered SVG, a `diff.json` with added, removed and changed element IDs, and an `elements.json` mapping each element ID to `path:line`.
- Durable copy (living map, Q7): committed in the target repo under `docs/architecture/` (config-harness already uses this folder), so the anchor checker covers it.
- The dossier gets a build-log line `VISUAL-REVIEW: <ref> — <N> elements, <N> added, <N> changed, <N> questions`, checked by `--pre-fanout`.

**Approval and question flow:**
1. The orchestrator writes the diagrams, then calls `ask_owner.py ask --kind product` with 2 to 4 options, for example "A. Matches intent, continue", "B. Change scope (reply says what)", "C. Stop and re-plan", with `--recommend A` and `--blocks "the fan-out"`.
2. The dashboard shows the diagrams above the question card. Today a question card shows text and options only (`server.ts:794-830`), so the page needs a diagram panel keyed by the run.
3. The owner uses the existing follow-up line to ask about a diagram element. The agent answers with `ask_owner.py reply`. The first answer closes the question (`store.ts:105-127`).
4. Element-anchored comments would add an `anchor` field to a JSONL line. `Line` already allows extra keys (`[k: string]: unknown`, `store.ts:6-12`), so the store format needs no change. The UI work is new.

**Policy conflict to resolve:** the ask policy says "Never ask for approval" (`owner-questions.md:96`). A visual review is an approval gate. The owner asked for it, so the policy needs a named exception, or the step must be framed as a product question ("is this what the feature should do?"), which the policy allows (`owner-questions.md:93`). A decision belongs in `research/OPEN-QUESTIONS.md`.

**Other constraints found:**
- The page has no external resources (`page.ts:1`). Render diagrams to SVG on the server, or inline a bundled renderer. This is a Q6 decision.
- `contract-reviewer` is blind on purpose. The new agent must not be mixed into that spawn; keep two spawns (`phase-3.md:194-201`).
- Under the runner, a pending owner answer blocks the run. A time-boxed default ("continue after N hours with the recommendation") may be wanted; the existing provisional-ruling pattern shows how (`phase-3.md:210-225`; `owner-questions.md:102-105`).

### B4. What is not known

- Whether the owner wants the visual review on every build or only above a size gate. `contract-reviewer` has no size gate; the phase 2 table uses gates for support agents (`phase-2.md:55-62`).
- Whether the "before" diagram is generated fresh at each run, or read from the committed living map. This depends on the Q7 research.
