# Visual review: change graph

The model behind the visual review of a change (feature VR-01). Language-neutral.

| File | What it is |
|---|---|
| `graph.schema.json` | The change graph schema, version 1.0.0: nodes, edges, groups, flows (before/after steps, lanes), status, evidence, resolution, view, tool versions in `meta`. |
| `fixtures/` | `w145-l0|l1|l2.json` (the W-145 change, migrated), `removed.json` (removed elements), `minimal.json`, `invalid/<issue code>.json` (one per validator issue), `repo/` (the tiny tree the invalid fixtures cite). |
| `../../scripts/vr_graph.py` | Validator and budget check. |
| `../../scripts/vr_extract.py` | Extractor: the "before" graph of a Cargo workspace. |
| `../../scripts/vr_diff.py` | Diff: merges a before and an after graph on the stable id (status added, changed, removed, context), filters it by mode, builds the plan-time after graph from two commits. |
| `../../scripts/vr_prepare.py` | The mechanical half of the Phase 3 diagram step (VR-05): extract before and after, merge, call-hierarchy candidates, into `.discovery/visual-review/<ID>/work/` (git-ignored by its own `.gitignore`). `sub-agents/diagram-author.md` then writes `l0.json`, `l1.json`, `l2.json` one level up; `validate_pipeline.py --pre-fanout` gates on them and on the `VISUAL-REVIEW:` build-log line (dossiers created on or after 2026-11-12, or with a `visual_review:` field). |
| `fixtures/ch-drift/` | Frozen trimmed copy of the 2026-11-12 config-harness workspace (cargo metadata, manifests, c2 file): the known failing case of `vr_c2_drift.py`. |
| `../../scripts/vr_c2_drift.py` | c2 drift: Cargo members and edges against the c2 containers file (VR-06). |
| `../../scripts/vr_map.py` | The living product map (L0): build, staleness check, recall, plan check, repo diagram list (VR-06). |
| `../../scripts/vr_metrics.py` | The tool-quality counters log (VR-06). |
| `../../scripts/vr_ra.py` | rust-analyzer call hierarchy client, used by the extractor's `--calls`. |

VR-03 renderer: `../../shared/questions/vrlayout.ts` lays a graph out with ELK on the dashboard server (vendored elkjs 0.12.1 in `shared/questions/vendor/elk/`); `GET /api/visual-review?dossier=ID[&repo=R]` reads `.discovery/visual-review/<ID>/l0|l1|l2.json` and returns graph, boxes and edge points per view; the run page draws them above the questions.

## Stable id rule

An id is derived from the kind plus the qualified name, never from a counter or an array position, so the same element has the same id before and after a change and VR-02 can join on it.

- `crate:harness`, `module:harness::tools::step`, `bin:platform::bin::llm_gateway`, `fn:harness::tools::step::run_group`
- An edge id is its kind plus its two end ids: `edge:depends:crate:a:crate:b`.
- Characters: `A-Za-z0-9_.:-`. Hand-made fixture ids may be short (`s1`), but must be unique.

## Views and budgets

`L0` features (15), `L1` steps (12), `L2` nodes (12). Groups are not counted (D0 report, section 7). `full` is an unbudgeted extractor graph; VR-05 picks the views a reviewer sees from it.

## CLI

```
scripts/vr_graph.py validate GRAPH.json [--root REPO] [--no-budget]   # issues as JSON, exit 1 on any
scripts/vr_graph.py budget GRAPH.json
scripts/vr_extract.py rust REPO [--layers 1,2] [--calls fn1,fn2] [--timeout 120] [--cfg-test] -o out.json
scripts/vr_diff.py merge --before B.json --after A.json [-o merged.json]   # issues as JSON on stderr, exit 1
scripts/vr_diff.py view merged.json --mode before|after|diff [-o out.json]
scripts/vr_diff.py after-from-git REPO --base REV --head REV [--layers 1,2] -o after.json
scripts/vr_diff.py --selftest
scripts/vr_extract.py --selftest | --live REPO     # also vr_graph.py --selftest, vr_ra.py --selftest
```

Layer 2 needs `cargo-modules` 0.27.0; without it the layer is recorded as `skipped` in `meta.layers`. Python: `import vr_graph; vr_graph.load()`, `.validate(graph, root=None)`, `.budget(graph)`. `import vr_diff; vr_diff.merge(before, after)`, `.view(merged, mode)`, `.after_from_git(repo, base, head, layers)`; they raise `DiffError` (`.issues`) on `removed_element_missing`, `added_element_exists`, `over_budget` (a flow of more than 12 steps) and any validator issue.

## Living product map (VR-06)

```
scripts/vr_c2_drift.py REPO [--c2 PATH] [--ignore NAME ...] [--json]      # exit 0 clean, 1 drift, 2 error
scripts/vr_map.py build REPO -o MAP.json (--features H-01,H-04 | --touched REV,REV | --all) [--fresh] [--table FILE.md]
scripts/vr_map.py check REPO MAP.json [--json]                            # exit 1 when any element is stale
scripts/vr_map.py recall REPO MAP.json --changes REV1,REV2 [--expected FILE.json] [--include-tests]
scripts/vr_map.py plan-check BASE.json PLAN.json                          # exit 1 when the plan is stale
scripts/vr_map.py list REPO [--dir docs/architecture]
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
