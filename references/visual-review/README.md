# Visual review: change graph

The model behind the visual review of a change (feature VR-01). Language-neutral.

| File | What it is |
|---|---|
| `graph.schema.json` | The change graph schema, version 1.0.0: nodes, edges, groups, flows (before/after steps, lanes), status, evidence, resolution, view, tool versions in `meta`. |
| `fixtures/` | `w145-l0|l1|l2.json` (the W-145 change, migrated), `removed.json` (removed elements), `minimal.json`, `label-at-limit.json` (an 80-character node label, the limit; longer fails with `label_too_long`: node 80, group and flow 60, edge and lane 40, title 100), `invalid/<issue code>.json` (one per validator issue), `repo/` (the tiny tree the invalid fixtures cite). |
| `../../scripts/vr_graph.py` | Validator and budget check. |
| `../../scripts/vr_extract.py` | Extractor: the "before" graph of a Cargo workspace. |
| `../../scripts/vr_diff.py` | Diff: merges a before and an after graph on the stable id (status added, changed, removed, context), filters it by mode, builds the plan-time after graph from two commits. |
| `../../scripts/vr_prepare.py` | The mechanical half of the Phase 3 diagram step (VR-05): extract before and after, merge, call-hierarchy candidates, into `.discovery/visual-review/<ID>/work/` (git-ignored by its own `.gitignore`). `sub-agents/diagram-author.md` then writes `l0.json`, `l1.json`, `l2.json` one level up; `validate_pipeline.py --pre-fanout` gates on them and on the `VISUAL-REVIEW:` build-log line (only for a dossier with the `visual_review:` field, which the Phase 3 step sets). |
| `fixtures/ch-drift/` | Frozen trimmed copy of the 2026-11-12 config-harness workspace (cargo metadata, manifests, c2 file): the known failing case of `vr_c2_drift.py`. |
| `../../scripts/vr_c2_drift.py` | c2 drift: Cargo members and edges against the c2 containers file (VR-06). |
| `../../scripts/vr_map.py` | The living product map (view `map`): build, staleness check, recall, plan check, repo diagram list (VR-06). |
| `../../scripts/vr_metrics.py` | The tool-quality counters log (VR-06). |
| `../../scripts/vr_ra.py` | rust-analyzer call hierarchy client, used by the extractor's `--calls`. |

VR-03 renderer: `../../shared/questions/vrlayout.ts` lays a graph out with ELK on the dashboard server (vendored elkjs 0.12.1 in `shared/questions/vendor/elk/`); `GET /api/visual-review?dossier=ID[&repo=R]` reads `.discovery/visual-review/<ID>/l0|l1|l2.json` and returns graph, boxes and edge points per view; the run page draws them above the questions.

VR-04 verdicts: the canvas inspector sets OK, Question, Change or Remove from scope (plus a comment) per element; `POST /api/visual-review/review` writes `.discovery/visual-review/<ID>/review.json` (shape: `review.schema.json`, each line anchored by `{diagram, element, graph_hash}`, so a verdict on an older graph shows as outdated). "Send review" answers the one product question through the ordinary answer path: A when every element is OK, otherwise B with element ids and comments; the orchestrator reads `review.json` on B. The run detail has a `diagrams` field (l0/l1/l2 paths, review counts). VR-06: the canvas shows a stale banner from `vr_map.py check`, and the repo view lists committed diagrams from `vr_map.py list`.

## Stable id rule

An id is derived from the kind plus the qualified name, never from a counter or an array position, so the same element has the same id before and after a change and VR-02 can join on it.

- `crate:harness`, `module:harness::tools::step`, `bin:platform::bin::llm_gateway`, `fn:harness::tools::step::run_group`
- An edge id is its kind plus its two end ids: `edge:depends:crate:a:crate:b`.
- Characters: `A-Za-z0-9_.:-`. Hand-made fixture ids may be short (`s1`), but must be unique.

## Views and budgets

`L0` features (15), `L1` steps (12), `L2` nodes (12). Groups are not counted (D0 report, section 7). `map` is the whole-repo product map and counts its folded first screen: at most 7 top-level groups (areas, plus any node outside a group) and at most 15 nodes inside any one group (the L0 limit applies when an area is opened); groups are counted there because the folded view is the budgeted one. `full` is an unbudgeted extractor graph; VR-05 picks the views a reviewer sees from it.

## CLI

```
scripts/vr_graph.py validate GRAPH.json [--root REPO] [--no-budget]   # issues as JSON, exit 1 on any
scripts/vr_graph.py budget GRAPH.json
scripts/vr_extract.py rust REPO [--layers 1,2] [--crates a,b] [--calls fn1,fn2] [--timeout 120] [--cfg-test] -o out.json
scripts/vr_diff.py merge --before B.json --after A.json [-o merged.json]   # issues as JSON on stderr, exit 1
scripts/vr_diff.py view merged.json --mode before|after|diff [-o out.json]
scripts/vr_diff.py after-from-git REPO --base REV --head REV [--layers 1,2] [--crates a,b] -o after.json
scripts/vr_prepare.py REPO ID --base REV --head REV [--crates a,b|all]   # default: crates derived from the changed files
scripts/vr_diff.py --selftest
scripts/vr_extract.py --selftest | --live REPO     # also vr_graph.py --selftest, vr_ra.py --selftest
```

`--crates a,b` runs layer 2 only for those crates; layer 1 stays the whole workspace, every other crate keeps its crate node, and `meta.layers.modules` gets `limited_to` and `skipped_crates`. `vr_prepare.py` derives the set from `git diff --name-only BASE HEAD` (a changed `Cargo.toml` or a file under `src/` maps to the package of the nearest manifest above it) and uses it for the before and the after side; `--crates all` turns the limit off. Merged graphs then hold module nodes only for the touched crates.

Layer 2 needs `cargo-modules` 0.27.0; without it the layer is recorded as `skipped` in `meta.layers`. Python: `import vr_graph; vr_graph.load()`, `.validate(graph, root=None)`, `.budget(graph)`. `import vr_diff; vr_diff.merge(before, after)`, `.view(merged, mode)`, `.after_from_git(repo, base, head, layers)`; they raise `DiffError` (`.issues`) on `removed_element_missing`, `added_element_exists`, `over_budget` (a flow of more than 12 steps) and any validator issue.

## Living product map (VR-06)

```
scripts/vr_c2_drift.py REPO [--c2 PATH] [--ignore NAME ...] [--json]      # exit 0 clean, 1 drift, 2 error
scripts/vr_map.py build REPO -o MAP.json (--features H-01,H-04 | --touched REV,REV | --all) [--fresh] [--table FILE.md]
scripts/vr_map.py check REPO MAP.json [--json]                            # exit 1 when any element is stale
scripts/vr_map.py recall REPO MAP.json --changes REV1,REV2 [--expected FILE.json] [--include-tests]
scripts/vr_map.py plan-check BASE.json PLAN.json                          # exit 1 when the plan is stale
scripts/vr_map.py list REPO [--dir docs/architecture]
scripts/vr_map.py refresh REPO [--since REV] [--full] [--no-calls] [--no-product-map] [--calls-limit S] [--json]
scripts/vr_refresh.py --selftest | map_refresh_pr.py --selftest
scripts/vr_c2_drift.py --selftest | vr_map.py --selftest | vr_metrics.py --selftest
```

- **Where**: the committed map is `docs/architecture/product-map.json` of the target repo (OQ-05 = A: the folder the anchor checker already covers). `build --table` also writes the feature-to-code table as Markdown, one anchor per row, which that checker reads.
- **Scope** (OQ-09 = B): the map grows per feature. `build` needs `--features`, `--touched` or `--all`, and merges into an existing output (`--fresh` replaces it).
- **Features** come from `FEATURE-BOOK.md` (`- ID: text` list lines). A feature is linked to every place its ID is written outside the spec folders (`research/`, `features/`, `FEATURE-BOOK.md`, and so on): signal `text`, resolution `inferred`, evidence `path:line`.
- **Staleness record**: `meta.staleness` holds, per element and span, the sha256 of the span lines (Rust also a comment-free hash and the hash of the file's public signatures), the extraction commit and the tool versions. `check` reports `STALE` (file gone, public signature changed, span changed), `cosmetic` (Rust comments and spacing only), `moved` (same text at another line). Its banner count is the number of stale elements.
- **Recall** (OQ-07): per merged change, the share of touched Rust source files that an element cites (file recall) or whose crate holds a cited file (unit recall). It sets no threshold: the owner sets one after an eval set of 10 changes exists.

### Metrics log

`vr_c2_drift.py` and `vr_map.py` append one JSON line per run to `REPO/.discovery/visual-review/metrics.jsonl` (`--metrics PATH` moves it, `--no-metrics` skips it). Tool-quality counters only (Q9 group A); the adoption counters (group B) are the dashboard's.

| Tool and event | Fields |
|---|---|
| `vr_c2_drift` `check` | members, missing_members, extra_containers, missing_edges, extra_edges, stale_anchors, drift, seconds |
| `vr_map` `build` | features, linked_features, evidence_entries, spans, inferred_share, evidence_valid, over_budget, seconds |
| `vr_map` `check` | elements, stale, cosmetic, moved, seconds |
| `vr_map` `recall` | changes, touched_source_files, file_recall, unit_recall, threshold (always null), seconds |

Each line also has `ts` (UTC) and `tool`, `event`.

### Whole-repo map: cache and incremental refresh

`vr_map.py refresh REPO` (implemented in `vr_refresh.py`) writes the committed whole-repo views into `docs/architecture/`: `crate-map.json` (crates and their dependencies), `modules-<crate>.json` (module tree per crate), `calls-<crate>.json` (module-to-module calls, other crates collapsed to crate nodes), `crate-calls.json` and the product map `product-map.json`, plus `map-manifest.json` (extraction commit, previous commit, node counts, what was reused). Every view is validated before it is written, and a view whose content did not change is not rewritten.

- **Structure is cached, not re-extracted each run** (this supersedes the Q8 "fresh each run" for the whole-repo map; per-dossier before-graphs still extract fresh within VC-08). The cache lives in `<git common dir>/asd-visual-review/cache/`, not in `.discovery/`: that folder is tracked in some repos and would be swept into commits (same reasoning as `asd-drivers/`).
- **Module layer**: one entry per crate, keyed by the sha256 of the git blob ids of its `Cargo.toml`, `build.rs` and `src/` files plus the cargo-modules and extractor versions. A crate that did not change is reused.
- **Call layer**: one entry per source file, keyed by its blob id. Only changed files go to rust-analyzer (and it is not started when nothing changed). Each file is stored as soon as it finishes, so a run that hits its limit resumes. Unchanged files whose cached callee moved or vanished in a changed file are recomputed too (reported as dependents). Incoming calls are the inverse of the outgoing calls of all files, so only outgoing calls are requested.
- **Every listed view opens on the canvas.** A `full` view (crate map, crate calls, `modules-<crate>`, `calls-<crate>`) opens in the structure (L2) layout, its tab named by the view kind and the heading by its title. `vr_refresh.py` gives a modules or calls view of more than 15 nodes groups to fold (one per top-level module, `Binaries`, `Other crates`, `Other modules`). A view with more than 18 visible nodes opens with its biggest top-level groups folded first until it fits (`openFolds` in `vrmap.ts`); the product map folds every area. An aggregated edge between folded boxes says what it merges: the shared label ("builds on ×3"), else the shared kind ("calls ×3"), else "N links". A folded graph of more than 8 edges shows one edge per pair of boxes with an arrow at both ends and both counts ("builds on ×3 | ×2"; the tooltip and inspector keep the evidence of both directions); the line width grows with the count. Nothing is hidden, so nothing needs a toggle.
- **Product map** (view `map`): first run `build --all`; later runs `build --touched` over the first-parent commits from the previous extraction commit to HEAD, plus new FEATURE-BOOK features; removed features are dropped. After either, `vr_map.organise` rebuilds the structure from the repo's files, so it is never stale against them: **areas** = one group per `OVERVIEW.md` that has `## ID - title` sections (label from its title; a feature whose ID prefix no OVERVIEW claims goes in "Other"); **edges** = one `builds on` edge per feature ID in a `Needs` cell of the `## Dependency map` table (evidence = that table row, path and line; text that is not a feature ID, an unknown ID and a self reference are dropped; `Feeds` is not read, it only repeats the other side); **stage** = the FEATURE-BOOK `### Stage N` section of the feature, written in the node's detail (`Build stage 1: ...`), not used for grouping. The title states the takeaway ("37 features in 4 product areas, built in 5 stages; ..."). The dashboard opens it with every area folded (`collapsed` in the diagram API; folded edges are aggregated as `x3`), so the first screen is the few areas; opening an area shows its features. The validator runs with the budget on for the committed map, no exceptions. `vr_map.py list` gives each committed view its real kind (`product-map`, `crate-map`, `crate-calls`, `modules`, `calls`) and puts the product map first.
- **Limits**: whole-repo builds allow up to 8 h (`--calls-limit`), 300 s per file (`--file-limit`) and 1800 s for indexing (`--index-limit`); the 120 s VC-08 limit stays for per-dossier runs. rust-analyzer runs with build scripts, proc macros, check-on-save and cache priming off (`vr_ra.FAST_OPTIONS`), so calls that exist only in macro-expanded code are not seen.
- **Stub hook**: `stub_target.py` starts `map_refresh_pr.py` detached after cutting a new stub. It runs the refresh from the previous map's extraction commit in a temporary worktree of the new stub and opens a pull request `chore/map-refresh-<date>` into it (opt-in: only when the repo already commits `map-manifest.json` or `crate-map.json`; `ASD_MAP_REFRESH=0` disables, `=1` forces). Never a push to development.
- **Exit and output**: prints `modules: N reused, M recomputed`, `calls: ...`, `product map: ...`; `--json` gives the same as data.

## c3 agreement (VC-06) and its parsing requirements

The cargo-modules flags of rules R1/R2 (`--all-features`, `--target x86_64-unknown-linux-gnu`) are one constant, `vr_extract.CM_FLAGS`: the module layer (`vr_extract`, so `vr_refresh`) and `vr_c3_agree.py` both use it, so feature-gated and linux-only modules are in `modules-<crate>.json` too, and the flags are part of the `vr_refresh` module-cache key (an older cache is recomputed).

`scripts/vr_c3_agree.py REPO --crate NAME` compares one crate's module edges from `cargo modules dependencies` with the c3 file `module_graph.py` writes. Agreement is `agreed / (declared or derived)`; the threshold is 95 percent and a run never passes on two empty sides. The two sides are made to describe the same thing by six rules, each documented in the script's docstring and covered by its selftest (fixtures in `fixtures/c3-agree/`). None hides a real difference; `--raw` switches all of them off.

| Rule | What it aligns | Why it is not hiding a difference |
|---|---|---|
| R1 target | cargo-modules runs for `x86_64-unknown-linux-gnu` | c3 reads every file, so a `target_os = "linux"` module must be analysed in; Linux is where the product runs |
| R2 features | `--all-features` | same: c3 reads feature-gated files |
| R3 tests | no `--cfg-test`, as `module_graph.py` drops `#[cfg(test)]` | both sides leave tests out |
| R4 cfg-gated nodes | a second `--cfg-test` run, used only for a declared module with no node (cargo-modules 0.27.0 drops `cfg(any(test, feature))` modules) | only edges touching such a module are taken; test-only edges are dropped |
| R5 granularity | a derived inline `mod x {}` rolls up to its deepest declared file module | c3 draws one node per file; a self-edge vanishes, a cross-file edge stays |
| R6 lib only | `::bin::` edges are dropped from c3 | the derived side is the lib target |

Edges to a `const`, `static` or macro are invisible to cargo-modules; `module_graph.py` draws them `-.->` and the check reports them under `unmodelled_edges` without counting them.

The c3 side follows the same reference (`scripts/module_graph.py`, config-harness): a `use` is read in every form (nested and multi-line trees, `self`, `super::`, renames, globs) and followed to the module that defines the item; a type named directly in a signature or field is an edge, a generic argument or a fn body is not; `#[cfg(test)]`, `#[cfg(doc)]` and `#[test]` code is skipped. `module_graph.py regen FILE...` rewrites the diagram and fan table.

### Close gate for /work-on

`validate_pipeline.py --c3-gate --base origin/<target>` runs in Phase 9, after the sync. For a repository with `docs/architecture/c3-components-*.md` it runs `vr_c3_agree.py` on every crate the branch's diff touched (a path under `crates/<crate>/`) and the generator freshness check (`scripts/module_graph.py check --against <file>`); a crate below 95 percent or with a stale file is a DEFECT and the session does not close. The repair is `module_graph.py regen` or a parser fix, never a lower threshold. It prints a `SKIP` line, and exits 0, when there are no c3 files, `cargo-modules` is missing, the generator is absent, or the touched crates have no c3 file. Untouched crates are not checked.

## Follow-ups

Work decided but not done yet is in [FOLLOW-UPS.md](FOLLOW-UPS.md) (FU-01: move the cargo-modules pin past 0.27.0 after the first demo).
