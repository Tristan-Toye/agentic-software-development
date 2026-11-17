# D0 spike report: visual review

Status: MEASURED on 2026-11-12. No product code ships. Conventions: [../CONVENTIONS.md](../CONVENTIONS.md). Checklist: [../DISCUSSION.md](../DISCUSSION.md), section E. Overview: [../OVERVIEW.md](../OVERVIEW.md).

## Setup

- Machine: macOS (Darwin 27.2.0). Bun 1.4.2, Node v24.18.0, cargo 1.97.1, rust-analyzer 1.97.1 (8bab26f4 2026-07-14), elkjs 0.12.1, cargo-modules 0.27.0.
- Workspace studied: a scratch clone of config-harness, branch `development`, commit `56034559`.
- Raw data: [data/](data/). Scripts: [scripts/](scripts/). Throwaway outputs stayed outside the repo.
- Rule: every number below comes from a command in this report or from a file in `data/`.
- Limit: one machine, one run set. Times vary by about 10 percent between runs.

## Summary table

| # | Item | Result |
|---|---|---|
| 1 | elkjs on Bun | Works. 200 nodes lay out in 0.5 s cold and 0.3 s warm. Peak RSS 386 MB. One trap: the bundled build keeps the Bun process alive after the layout. |
| 1b | Layout stability | ELK model order does NOT keep nodes still. 96 to 100 percent of the unchanged nodes move after one added node. |
| 1c | How elkjs ships | Vendor `elk-api.js` plus `elk-worker.min.js` (1.6 MB, 472 KB gzipped) and run it in a Bun Worker. No `package.json`. |
| 2 | `cargo metadata` | 10 members, 0.65 to 1.03 s, 2.5 MB JSON. Crate set equals the 10 members. 16 normal workspace edges. |
| 3 | cargo-modules | Version 0.28.0 fails (needs rustc 1.98). Version 0.27.0 works: 6 s warm, 65 nodes and 245 edges at module level. |
| 4 | Call hierarchy | Works through the rust-analyzer LSP. `run_group` gives 3 workspace callees and 1 caller in 16 s. Peak RSS 3 GB. The Claude LSP tool had no Rust server. |
| 5 | W-145 fixture | 3 graph files, all valid. 29 nodes, 29 edges, 5 groups. 58 source anchors, 15 inferred. |
| 6 | SVG canvas | 48 lines, 5.3 KB of JavaScript. `@xyflow/system` adds 63.8 KB minified and a build step: SKIP. |
| 7 | Budgets | L1 fits (10 of 12). L2 is at the limit (12 of 12). L0 fits as a change view (7 of 15) and overflows as a whole map (37). |
| 8 | Drift script | stdlib script with a selftest. It names `fault`, `step-timing`, `sandbox-tool` and `workspace-hack`. |
| 9 | Recall | Crate 100 percent. Module 96 percent. Function name 94 percent. No call-edge recall was measured. |

## 1. elkjs on Bun

### 1.1 Layout time and memory

Method: a synthetic graph of N nodes in groups of 10. Each node has two ports (west in, east out). There are about 1.5 edges per node. Options: `elk.algorithm: layered`, `hierarchyHandling: INCLUDE_CHILDREN`, orthogonal ports fixed to a side. One fresh process per row. Five layouts per process. "Cold" is the first layout. "Warm" is the median of five. Peak RSS comes from `/usr/bin/time -l`. Source: [scripts/gen.mjs](scripts/gen.mjs), [scripts/bench.mjs](scripts/bench.mjs), results in [data/bench-results.jsonl](data/bench-results.jsonl).

| Runtime and mode | Nodes | Edges | Cold ms | Warm ms | Peak RSS MB |
|---|---|---|---|---|---|
| Bun, bundled (in thread) | 50 | 75 | 124 | 52 | 193 |
| Bun, bundled | 100 | 153 | 216 | 129 | 269 |
| Bun, bundled | 200 | 316 | 513 | 328 | 386 |
| Bun, Worker thread | 50 | 75 | 171 | 49 | 216 |
| Bun, Worker thread | 100 | 153 | 255 | 115 | 292 |
| Bun, Worker thread | 200 | 316 | 558 | 314 | 372 |
| Node, bundled | 50 | 75 | 111 | 46 | 183 |
| Node, bundled | 100 | 153 | 197 | 121 | 289 |
| Node, bundled | 200 | 316 | 460 | 329 | 339 |

Findings (strategy `NODES_AND_EDGES`; `NONE` is within 15 percent, see the data file):

- Bun and Node give the same times within noise. The Bun risk in [Q5](../../visual-review/research/synthesis/q5-rendering-stack.md) ("Bun is not documented") does not hold for layout.
- Time grows about 2.5 times for each doubling of nodes. All three sizes stay under 0.6 s. The planned `NODES_AND_EDGES` cost is small (up to 20 percent over `NONE` at 200 nodes).
- Memory is about 190 to 390 MB of peak RSS, mostly the layout heap. A Worker thread does not lower the peak.

### 1.2 Trap: the bundled build hangs the Bun process

- `import ELK from "elkjs/lib/elk.bundled.js"` then `await elk.layout(...)` works. The process then does not exit (idle, 0.14 s CPU in 8 s). Node exits at once.
- The cause is in the code: the bundle uses `typeof Worker < "u" ? Worker : void 0` ([elk.bundled.js](https://github.com/kieler/elkjs), searched with `grep -o`). Bun has a global `Worker`. `elk.terminateWorker()` then throws `this.worker.terminate is not a function`.
- Working fix 1: `elk-api.js` with `new ELK({ workerUrl: <path to elk-worker.min.js> })`. Bun starts a real Worker. `elk.terminateWorker()` ends it. Exit code 0. Script: [scripts/wk.mjs](scripts/wk.mjs).
- Working fix 2: call `process.exit(0)` after the layout (fine for a CLI, not for a server).
- Node fails the worker mode without the optional `web-worker` package. Context7 `/kieler/elkjs`: `web-worker` is an optional dependency; without it Node falls back to the non-worker build with a warning.
- Precedent: the dashboard already starts a Bun Worker in `shared/questions/lagwatch.ts:29` (`new Worker(new URL("./lagworker.ts", import.meta.url).href)`).

### 1.3 Layout stability

Question (DR-02, row 18): does `considerModelOrder.strategy: NODES_AND_EDGES` keep unchanged nodes in place when nodes are added?

Method: lay out N nodes ("before"). Add 1 or 3 nodes with 1 to 2 edges each ("after"). Compare the absolute position of every unchanged node. Five random attachments per cell. Three variants: `NONE`, `NODES_AND_EDGES`, and `NODES_AND_EDGES` plus `crossingMinimization.forceNodeModelOrder` (found through context7 `/websites/eclipse_dev_elk`). Source: [scripts/stab2.mjs](scripts/stab2.mjs), [data/stability-results.jsonl](data/stability-results.jsonl), [data/stability2-results.jsonl](data/stability2-results.jsonl).

| Nodes | Added | Strategy | Nodes moved over 1 px | Mean move px | X order kept | Y order kept |
|---|---|---|---|---|---|---|
| 50 | 3 | NONE | 100 % | 143 | 100 % | 48 % |
| 50 | 3 | NODES_AND_EDGES | 100 % | 166 | 100 % | 41 % |
| 100 | 1 | NONE | 100 % | 247 | 100 % | 59 % |
| 100 | 1 | NODES_AND_EDGES | 100 % | 219 | 100 % | 62 % |
| 100 | 3 | NONE | 100 % | 331 | 99 % | 54 % |
| 100 | 3 | NODES_AND_EDGES | 100 % | 290 | 99 % | 60 % |
| 200 | 3 | NONE | 100 % | 1578 | 97 % | 42 % |
| 200 | 3 | NODES_AND_EDGES | 100 % | 1428 | 97 % | 56 % |

- All 48 cells are in the data file. In every cell 96 to 100 percent of the unchanged nodes move by more than 1 px. A flat graph with no groups behaves the same (control: 96 to 100 percent, [data/control-results.jsonl](data/control-results.jsonl)).
- No strategy is clearly better. The difference between `NONE` and `NODES_AND_EDGES` is inside the noise. The result is the same above and below 100 nodes.
- The x order (layer order) holds at 97 to 100 percent. The y order (order inside a layer, group order) is near 50 percent, that is random.
- Caveat: my synthetic graphs are long chains (50 nodes make 3800 px width). A real change graph is smaller and shallower. A layout that ELK treats as globally optimised may still move less on real data. This is a first result, not a proof.
- Consequence: row 18 closes as "model order does not give stability". The merged-graph design of [Q2](../../visual-review/research/synthesis/q2-diff-model.md) does not need it: before and after live in one graph and one layout.
- Determinism holds: four runs on the same input give identical positions and edge routes ([scripts/det.mjs](scripts/det.mjs)). The raw JSON is NOT byte-identical, because ELK adds a `$H` hash field to every node. Strip keys that start with `$` before writing the file, or VC-05 (byte-identical output) fails.

### 1.4 How elkjs ships

Facts:

- The plugin has no `package.json` and no `bun.lock`. `shared/questions/*.ts` is 382 KB with no npm import (`wc -c shared/questions/*.ts`, `ls package.json`).
- `elkjs@0.12.1`: `lib/elk.bundled.js` 1,646,241 bytes (477,152 gzipped); `lib/elk-worker.min.js` 1,631,868 bytes (472,210 gzipped); `lib/elk-api.js` 9,782 bytes; whole package 7.9 MB on disk (`bun add elkjs`, `du -sh`).
- Licence: `package.json` says `"EPL-2.0 OR GPL-3.0-or-later"`. `LICENSE.md` holds the EPL-2.0 text.
- EPL-2.0 is a weak copyleft licence. Distributing the unmodified binary file needs the licence text and the notice that the source is available. It does not force the plugin code under EPL, because the plugin does not modify or link ELK source (read of EPL-2.0 section 3, in `LICENSE.md`; this is an engineering reading, not legal advice).

| Option | Size added to the repo | Build step | Offline | Risk |
|---|---|---|---|---|
| A. Vendor `elk-api.js` and `elk-worker.min.js` in `shared/questions/vendor/elk/` with `LICENSE.md` | 1.64 MB (about 0.48 MB gzipped in git) | none | yes | Plugin installs copy the tree per commit. 1.6 MB repeats. A version bump replaces one 1.6 MB blob in git. |
| B. `package.json` plus `bun install` at deploy | 2 small files | a new install step on every machine | no (needs registry) | A broken install disables layout. The plugin has no such step today. |
| C. Vendor `elk.bundled.js` and call it in the main thread | 1.65 MB | none | yes | The exit hang from section 1.2, and the layout blocks the dashboard event loop for up to 0.5 s. |
| D. Node child process | same as A or B | none | depends | Needs Node on the machine. Not needed: Bun works. |

USE option A. It needs no new build step, it works offline, and the Worker keeps the 0.3 to 0.6 s layout off the dashboard event loop. The cost is 1.6 MB of vendored code. Keep a `VENDORED.md` line with the version, the source URL and the SHA-256. Add `LICENSE.md` next to the files.

## 2. `cargo metadata`

Command: `cargo metadata --format-version 1` in the clone ([data](data/); raw JSON not copied, 2.5 MB).

| Item | Value |
|---|---|
| Workspace members | 10 (`composition-root`, `fault`, `graph-runtime`, `harness`, `platform`, `protocol`, `sandbox-tool`, `step-timing`, `test-support`, `workspace-hack`) |
| Member set equals `[workspace] members` in `Cargo.toml:3-14` | yes (VC-01 holds) |
| All packages in the resolve | 470 |
| Time, 3 runs | 1.03 s, 0.65 s, 0.80 s |
| Time with `--no-deps` | 0.07 s |
| JSON size | 2,529,046 bytes |
| Workspace edges (path dependencies) | 33 in total |
| - normal, excluding `workspace-hack` | 16 |
| - to `workspace-hack` | 6 |
| - dev | 11 (one is `platform -> platform`, a self dev edge) |

- The 16 normal edges: `composition-root` to `graph-runtime`, `harness`, `platform`, `protocol`, `step-timing`; `graph-runtime` to `protocol`; `harness` to `fault`, `protocol`, `sandbox-tool`, `step-timing`; `platform` to `fault`, `protocol`, `step-timing`; `sandbox-tool` to `protocol`; `test-support` to `protocol`, `sandbox-tool`.
- The c2 file draws 8 solid edges (`docs/architecture/c2-containers.md:24-37`). `cargo metadata` shows 16. Half of the real edges are missing from the diagram.
- The `Cargo.toml` line numbers in the c2 labels are stale: the file says `crates/harness/Cargo.toml:63` for `harness -> protocol`; the dependency is at line 82 now. The same holds for `composition-root -> harness` (31, now 39) and `platform -> protocol` (20, now 27). These are free drift findings for VR-06.
- The dependency edge is a manifest fact. VC-02 ("every crate edge equals a resolved workspace dependency") needs a rule for dev edges, self edges and `workspace-hack`. Proposal: drop `workspace-hack`, show dev edges dashed, drop self edges.

## 3. cargo-modules on `harness`

| Item | Value |
|---|---|
| First try | `cargo install cargo-modules --locked` fails: `cargo-modules 0.28.0 requires rustc 1.98.0 or newer, while the currently active rustc version is 1.97.1` (the repo pins 1.97.1 in `rust-toolchain.toml`) |
| Working version | `cargo install cargo-modules --version 0.27.0 --locked` (cargo said 0.27.0 supports rustc 1.95.0). Install: 2 min 56 s. |
| Command | `cargo modules dependencies -p harness --lib --no-fns --no-traits --no-types --no-externs --no-sysroot` |
| Time | 28.2 s first run (cold, build scripts), then 5.9 to 6.8 s (4 runs) |
| Output | DOT, 39,826 bytes; 65 nodes (64 modules and the crate root); 245 edges (64 `owns`, 181 `uses`) |
| With `--max-depth 2` | 64 nodes, 243 edges (no real reduction: the module tree is shallow) |
| With `--cfg-test` | 67 nodes, 252 edges (tests add 2 modules) |
| Full graph (functions, types, traits) | 610,513 bytes; 1,196 nodes; 2,696 edges; 6.3 s |
| Loads the workspace without error | yes; stderr is empty. Side effect: it builds the build scripts and proc macros and fills `target/` (443 MB after all runs). |
| Other crates (full graph, `--lib`) | `platform` 8.7 s, `protocol` 6.0 s, `composition-root` 7.2 s |

- Module nodes are `harness::tools::step` and so on. The output is a clean source for L2 components. The module list is small enough to fit a view (65 nodes), but 65 is far above the 12-node budget. A filter (`--focus-on`) or a rollup is needed.
- The tool has no call edges. In the full graph, `run_group` has `uses` edges to `ToolTurnStep`, `TurnSite` and `TrackTally` only. These are type references. No edge goes to `decide_call` or `settle_call`.
- Output is DOT. D1 needs a parser for DOT (the `"a" -> "b" [label="owns"...]` lines are regular), or the JSON helper from [Q3](../../visual-review/research/synthesis/q3-extraction-from-rust.md) layer 2. The module-level DOT is saved in [data/cargo-modules-harness-mods.dot](data/cargo-modules-harness-mods.dot).
- Pin: the pin must follow the repo toolchain. A pin of 0.27.0 is right for rustc 1.97.1. A toolchain bump to 1.98 allows 0.28.0.

## 4. rust-analyzer call hierarchy for `run_group`

`run_group` lives at `crates/harness/src/tools/step.rs:1816` (`grep -n 'fn run_group'`).

| Attempt | Result |
|---|---|
| Claude `LSP` tool, `prepareCallHierarchy` and `outgoingCalls` at line 1816, columns 20 and 16 | "No call hierarchy item found". The tool has no Rust server configured in this session. Flag to the user: the Rust LSP plugin is not available here. |
| `rust-analyzer` over LSP stdio, driven by [scripts/ra_calls.py](scripts/ra_calls.py) (stdlib Python) | Works |

Result of the stdio run (`textDocument/prepareCallHierarchy`, `callHierarchy/outgoingCalls`, `callHierarchy/incomingCalls`):

- Callees in the workspace: `decide_call` (`step.rs`), `run_slots_concurrently`, `settle_slots`. Callees in the standard library: `with_capacity`, `len`, `enumerate`, `iter`, `push` (to be filtered out by path).
- Callers: `run_calls`.
- Time until the first non-empty answer: 16.1 s from process start (4 polls); outgoing and incoming each took 0.1 s more.
- Cost: peak RSS 3,007 MB, 94 s of CPU (children, includes proc-macro and build-script runs).
- The answer matches the code. `run_calls` calls `run_group` at `step.rs:1756`. `run_group` calls the three phase functions.
- Line numbers: rust-analyzer reports the start of the doc comment (for example `decide_call` at 2106; the `fn` is at 2112). An evidence anchor must use the selection range, not the item range.
- `scip` and `analysis-stats` were not tried. The LSP route answers OQ-03 on its own.

Answer to OQ-03: call edges are possible. Cost is one rust-analyzer session per extraction (16 s, 3 GB) and about 0.1 s per query after that. It fits "on request for the functions of the change", not "for the whole repo". Static calls resolve. Trait-object calls give the trait method, not the implementation (not tested; VC-10 stays open).

## 5. W-145 fixture

Source: dossier `.discovery/dossiers/W-145-consecutive-read-only-tool-calls-of-one-response-run-concurrently.md` (354,284 bytes; read only `## Problem` lines 35-45, `## Approach` 46-90, `## Contract` start 91-100 and `## Work packages` 5144-5165). Every anchor was checked against the clone at `56034559` ([scripts/validate.mjs](scripts/validate.mjs): file exists and line is inside the file).

| File | Nodes | Groups | Edges | Source anchors | Inferred | Added / changed / removed / context |
|---|---|---|---|---|---|---|
| [fixtures/w145-l0.json](fixtures/w145-l0.json) | 7 | 0 | 6 | 4 | 12 | 2 / 3 / 0 / 8 |
| [fixtures/w145-l1.json](fixtures/w145-l1.json) | 10 | 0 (3 lanes) | 10 | 25 | 2 | 11 / 5 / 0 / 4 |
| [fixtures/w145-l2.json](fixtures/w145-l2.json) | 12 | 5 | 13 | 29 | 1 | 4 / 9 / 0 / 17 |

(The status counts include groups and edges.)

- L0 uses real feature ids from `FEATURE-BOOK.md:58-59`: H-01 (tool layer core), H-05 (gated git tools). The sketch in DISCUSSION C1 called H-06 "conversation compaction". `FEATURE-BOOK.md:52` says H-06 is the Postgres checkpoint schema. The fixture follows the feature book. The corpus contradicts itself here. (`seq-tool-call.md:9` ties H-06 to compaction.) Settings have no feature id; the node "Runtime settings" is a placeholder.
- L1 has 10 steps in 3 lanes (step, git tools, settings). Each changed step carries before and after text in `detail`.
- L2 has 5 crates as groups, 12 component nodes and 13 edges. Crate edges come from `cargo metadata` and cite the `Cargo.toml` line (82, 83, 86, 87, 39, 45, 27).
- No "removed" element exists. W-145 removes nothing; it moves and splits. The fixture therefore does not test the removed style. D2 needs a second fixture with a removal.
- L0 is 12 of 16 anchors "inferred". The feature-to-code map does not exist (confirmed in [Q1](../../visual-review/research/synthesis/q1-representations.md) audit note). L0 stays the weakest view.
- Schema: [graph.schema.json](graph.schema.json), draft 2020-12, `schema_version` 0.1.0. Design points:
  - One merged graph per view. Fields: `view`, `meta`, optional `lanes` and `groups`, `nodes`, `edges`.
  - Every element has `status` (added, changed, removed, context) and `evidence` (at least one). An evidence item is `{tier: "source", path, line?}` or `{tier: "inferred", note}`.
  - `resolution` (static, dispatch, inferred) is on nodes and edges.
  - `kind` is an open string. No field name is Rust specific (OQ-10). Rust words appear only in values.
  - Validated with ajv 8 (`bun add ajv`) plus four reference checks in `validate.mjs`: duplicate id, unknown group, unknown edge end, evidence path and line.
- The schema does not yet encode: the size budget (VC-03 `over_budget`), line ranges inside a file (`line_range_outside_file` is checked by script only), the whole-repo "before" graph, and verdict fields (VR-04).

## 6. SVG canvas demo

File: [demo.html](demo.html), built by [scripts/build_demo.py](scripts/build_demo.py) from [demo.template.html](demo.template.html). It inlines the fixtures and the ELK output ([fixtures/w145-l{0,1,2}.layout.json](fixtures/)). No network fetch, no framework.

- Positions: [scripts/layout.mjs](scripts/layout.mjs). ELK 0.12.1, layered, `NODES_AND_EDGES`, orthogonal routing, `shapeCoords` and `edgeCoords` set to `ROOT` (all positions absolute, no parent offsets). Layout time: L0 49 ms, L1 58 ms, L2 61 ms.
- Canvas features: three view tabs, status colour plus a text mark (+, ~, -, =), dashed edge for inferred, group boxes, pan (pointer drag), wheel zoom around the cursor, fit, inspector with `path:line` evidence. All text is set with `textContent`.
- Check: headless Chrome `--dump-dom` shows 17 rects and 26 polylines for L2 (5 groups plus 12 nodes; 13 edges drawn twice: hit area and line) and no uncaught error. I could not view the screenshot in this session. The L0 and L1 tabs were not rendered by the check; they use the same code.
- Size of the hand-written canvas: 48 lines, 5,274 bytes of JavaScript (between the `CANVAS-START` and `CANVAS-END` markers). The whole page is 27,332 bytes with the data inlined.
- Pan and zoom alone take 10 lines.
- `@xyflow/system` (context7 `/websites/deepwiki_xyflow_xyflow`: a framework-agnostic core built on `d3-zoom`, `d3-drag`, `d3-selection`): installed 0.0.84, MIT. `XYPanZoom` bundled and minified with `bun build --minify` is 63,809 bytes (12 times the whole hand-written canvas), pulls the d3 packages and needs a build step. It would add nothing the 10 lines do not give. Its extras (node dragging, connection handles) are out of scope for a read-only review canvas.
- How `page.ts` would bundle the canvas: `shared/questions/page.ts` is 130,467 bytes, one template string. 5.3 KB is 4 percent more. No bundler is needed; the canvas source is a plain string. Data arrives through the run-detail `diagrams` field.

Answer to OQ-02: USE the hand-written canvas. The canvas does not cost too much, so the React island is not needed for pan, zoom, select and inspect. Re-check when D3 adds collapse, mini-map and walkthrough mode (VR-03 scope; not built here).

## 7. Element budgets

| View | Budget | W-145 count | Verdict |
|---|---|---|---|
| L0 features (change view) | 15 | 7 | fits |
| L0 features (whole feature book) | 15 | 37 (ids in `FEATURE-BOOK.md:40-110`) | overflows by 22; needs a "touched plus 1 hop" filter |
| L1 steps | 12 | 10 | fits, 2 spare |
| L2 nodes | 12 | 12 nodes (17 with the 5 crate groups) | at the limit |

- L2 overflows with a small increase: one more touched module makes 13. The three recall changes in section 9 touched 5, 12 and 10 source files in 3 to 5 crates. Counted as one node per touched file plus the context crates, #330 gives about 13 plus 4 and overflows. Proposal: the L2 budget counts groups and nodes separately, or the L2 view shows crates only and opens components on demand.
- 10 workspace crates (without `workspace-hack`: 9) fit the 12-node budget as crate nodes.
- The L1 count depends on how I cut the steps. The dossier lists 3 phases, 2 setting steps and 1 env step. A larger dossier (W-141 style, 22 test files) would need a roll-up.

## 8. Drift script

File: [c2_drift.py](c2_drift.py). Stdlib only. `--selftest` passes (`selftest ok`). Exit code 0 equal, 1 drift, 2 error.

Run on the clone: `python3 c2_drift.py --repo <ch-clone>`:

```
members: 10  containers in c2: 6
missing from the diagram: fault, sandbox-tool, step-timing, workspace-hack
in the diagram, not a member: none
title says 6; members are 10: MISMATCH
DRIFT
```

- Expected in the checklist: `fault` and `step-timing`. The script also finds `sandbox-tool` (a real crate that `harness` and `test-support` depend on) and `workspace-hack` (a build helper that `--ignore workspace-hack` removes).
- It also checks the number word in the c2 title ("six") against the member count.
- Not covered: edges. The check in section 2 found 8 normal edges missing and 3 stale `Cargo.toml` line anchors. Add an edge mode in D6 from `cargo metadata` (no new tool).
- Parsing: members from the `members = [...]` array (comment-safe), containers from Mermaid nodes of the form `id["label"]`. A c2 file that names containers in another form needs a parser change.

## 9. Recall check

Three merged PRs that touched Rust source (`git diff --name-only <merge>^1 <merge>`). Source: [scripts/recall.py](scripts/recall.py), [data/recall-results.json](data/recall-results.json).

Method: for each PR, take the files in the diff (tests excluded). Check whether the extraction layers have a node for each touched element in the graph of `HEAD`:

- Crate recall: touched crates that are workspace members. Layer: `cargo metadata`.
- Module recall: touched `src/**/*.rs` files whose module (`a/b.rs` to `crate::a::b`, `mod.rs` to the parent, `lib.rs` to the root) is a node in `cargo modules dependencies --lib`. Layer: cargo-modules.
- Function recall: function names read from the `@@` hunk headers that exist as function nodes in the cargo-modules full graph. This is the ceiling for a call hierarchy: rust-analyzer can only answer for items it knows.

| PR (merge commit) | Crates touched | Crate recall | Source files | Module recall | Functions named in hunks | Function recall |
|---|---|---|---|---|---|---|
| #391 platform pool metrics (`c240c954`) | 3 | 3/3 | 5 | 5/5 | 7 | 7/7 |
| #330 llm key denylist (`7b433c2c`) | 5 | 5/5 | 13 | 12/13 | 7 | 6/7 |
| #340 git mirror maintenance (`d69d30d5`) | 5 | 5/5 | 10 | 10/10 | 5 | 5/5 |
| Total | 13 | 100 % | 28 | 27/28 = 96 % | 19 | 18/19 = 95 % |

- The one module miss is `crates/platform/src/bin/llm_gateway.rs`: a binary target, not part of `--lib`. The one function miss is in the same area. Proposal: run cargo-modules for `--bin` targets too.
- Limits of this number: it tests that a node EXISTS for each touched element. It does not test that the tool would have chosen it without the diff. The function names come from hunk headers, which name the function before the hunk, not always the changed one. The graph is from `HEAD`, not from the merge commit.
- Not measured: call-edge recall (no flow steps were reconstructed), L0 feature recall (no feature-to-code map exists, so I did not map features by hand) and the time to build such a map. These two numbers for OQ-07 and OQ-09 are still open.

## Recommendations for D1 to D6

### Extraction depth (OQ-03)

1. D1 builds crate level (`cargo metadata`) and module level (`cargo-modules` 0.27.0, `--lib`, plus `--bin`) as the default "before" graph. Both are fast (1 s and 6 s) and recall is 96 to 100 percent on 3 changes.
2. D1 adds the call-hierarchy query on request only, for the functions named in the contract stubs. The LSP route works (16 s, 3 GB, 0.1 s per query after). Do not run it for the whole repo.
3. Skip `scip`, `ra_ap_*` and tree-sitter for version 1. The LSP answer is enough for the 3 to 8 functions of a change.
4. Pin by the repo toolchain: cargo-modules 0.27.0 for rustc 1.97.1. Record the versions in `meta.tools` (VC-09). The proposed VC-08 time limit: 60 s for crate plus module layers on config-harness (measured 7 s warm, 29 s cold, with margin).
5. Parse DOT in D1 (regular line format) or wait for a JSON helper. Filter standard library callees from call hierarchy results by path.
6. Warn about the side effects: cargo-modules and rust-analyzer build scripts and proc macros; `target/` grew to 443 MB. Run them in a worktree with a warm `target/` or with a cache.

### How elkjs ships

1. Vendor `elk-api.js` and `elk-worker.min.js` of elkjs 0.12.1 in `shared/questions/vendor/elk/`, with `LICENSE.md`, a version note and a SHA-256. Cost: 1.64 MB, 0.48 MB gzipped.
2. Run ELK in a Bun Worker (`elk-api.js` with `workerUrl`), like `lagwatch.ts`. Do not use `elk.bundled.js` in the dashboard process (exit hang, event-loop block).
3. Run layout when the graph is written, store `layout` next to the graph and send positions as JSON (as [Q5](../../visual-review/research/synthesis/q5-rendering-stack.md) says). Strip `$`-prefixed keys.
4. Add the licence line to the plugin notice: EPL-2.0 for the unmodified file. Ask the owner to confirm (engineering reading only).

### Schema

1. Start from [graph.schema.json](graph.schema.json) 0.1.0. It validates the 3 fixtures. Add to D1: budget check in the validator, a `before`/`after` graph split for VR-02, `removed` fixture, a stable id rule (the diff in VR-02 joins by id), and the validator issue names of VC-03.
2. Keep the schema free of Rust words (OQ-10). Keep `kind` open.
3. Do not rely on ELK for stability. One merged graph, one layout per review. Cache the positions of the "before" graph only if a later review must look like an earlier one (not tested).

### Canvas (OQ-02)

1. D3 builds on the hand-written canvas (48 lines now). Do not add `@xyflow/system`.
2. D3 adds collapse, mini-map and walkthrough on the same pattern. Re-measure after that; if the canvas passes about 300 lines, look again at the island.

### Budgets

1. Keep 15, 12, 12. W-145 fits L1, sits at the limit at L2 and fits L0 only as a change view.
2. Define what counts: nodes, groups or both. This report counts nodes and shows the group count in brackets.
3. The L0 whole map (37 features) needs a filter. L0 recall cannot be measured until a feature-to-code map exists.
4. L2 needs a collapsed form (crates only) as the default and component nodes on demand.

### Risks

| Risk | Evidence | Mitigation |
|---|---|---|
| ELK moves every node when the graph changes | section 1.3 | One merged graph; accept re-layout; test on real data in D3 |
| Bun hang with `elk.bundled.js` | section 1.2 | Worker mode; test in CI that the process exits |
| cargo-modules pin breaks at a toolchain bump | section 3 | Pin by toolchain; fail loud |
| 3 GB and 16 s for call hierarchy | section 4 | On request only; one session per extraction |
| `target/` fill and build-script side effects | section 3 | Dedicated worktree or cache |
| L0 has no map | section 5, 9 | D6 owns it; D1 ships L1 and L2 first |
| c2 doc is stale (4 crates, 8 edges, 3 anchors) | sections 2, 8 | The D6 drift script fits at once; add an edge mode |
| Claude LSP tool has no Rust server here | section 4 | Use the stdio script; flag to the owner |
| Synthetic graphs are chains | section 1.3 | Repeat the stability test on real change graphs in D3 |

### Not done in D0

- No test on a trait-object call (VC-10).
- No `--bin` extraction run.
- No call-edge or L0 recall number.
- No visual review of the screenshot (the image read was blocked); structure was checked by DOM counts only.
