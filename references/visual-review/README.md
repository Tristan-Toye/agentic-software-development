# Visual review: change graph

The model behind the visual review of a change (feature VR-01). Language-neutral.

| File | What it is |
|---|---|
| `graph.schema.json` | The change graph schema, version 1.1.0 (1.1.0 adds the `decision` view; every 1.0.0 graph stays valid): nodes, edges, groups, flows (before/after steps, lanes), status, evidence, resolution, view, tool versions in `meta`. |
| `fixtures/` | `w145-l0|l1|l2.json` (the W-145 change, migrated), `removed.json` (removed elements), `decision-retry.json` (a valid decision view: a failed webhook delivery retries, then is parked), `minimal.json`, `label-at-limit.json` (an 80-character node label, the limit; longer fails with `label_too_long`: node 80, group and flow 60, edge and lane 40, title 100), `invalid/<issue code>.json` (one per validator issue), `repo/` (the tiny tree the invalid fixtures cite). |
| `../../scripts/vr_graph.py` | Validator and budget check. |
| `../../scripts/vr_extract.py` | Extractor: the "before" graph of a Cargo workspace, a Python tree (`vr_extract_py.py`, stdlib `ast`) or a C# solution (`vr_extract_cs.py`, a dependency-free declaration parser, no dotnet); shared helpers in `vr_lang.py`. |
| `../../scripts/vr_diff.py` | Diff: merges a before and an after graph on the stable id (status added, changed, removed, context), filters it by mode, builds the plan-time after graph from two commits. |
| `../../scripts/vr_prepare.py` | The mechanical half of the Phase 3 diagram step (VR-05): extract before and after, merge, call-hierarchy candidates, into `.discovery/visual-review/<ID>/work/` (git-ignored by its own `.gitignore`). Language is detected (Cargo.toml is Rust, else the larger of .cs and .py; `--lang` forces it). A repo with none of the three exits 3 with `"status": "unsupported"` (Phase 3 logs `VISUAL-REVIEW: skipped — ...`). `sub-agents/diagram-author.md` then writes `l0.json`, `l1.json`, `l2.json` one level up; `validate_pipeline.py --pre-fanout` gates on them and on the `VISUAL-REVIEW:` build-log line, whose `arch <stem> <n>` entries match the drafts in `arch/` (only for a dossier with the `visual_review:` field, which the Phase 3 step sets). |
| `fixtures/ch-drift/` | Frozen trimmed copy of the 2026-11-12 config-harness workspace (cargo metadata, manifests, c2 file): the known failing case of `vr_c2_drift.py`. |
| `../../scripts/vr_c2_drift.py` | c2 drift: Cargo members and edges against the c2 containers file (VR-06). |
| `../../scripts/vr_map.py` | The living product map (view `map`): build, staleness check, recall, plan check, repo diagram list (VR-06). |
| `../../scripts/vr_metrics.py` | The tool-quality counters log (VR-06). |
| `../../scripts/flow_coverage.py` | Flow coverage: lists the runtime entry points with no sequence diagram (see Flow coverage below). |
| `../../scripts/vr_ra.py` | rust-analyzer call hierarchy client, used by the extractor's `--calls`. |

VR-03 renderer: `../../shared/questions/vrlayout.ts` lays a graph out with ELK on the dashboard server (vendored elkjs 0.12.1 in `shared/questions/vendor/elk/`); `GET /api/visual-review?dossier=ID[&repo=R]` reads `.discovery/visual-review/<ID>/l0|l1|l2.json` and returns graph, boxes and edge points per view; the run page draws them above the questions.

VR-04 verdicts: the canvas inspector sets OK, Question, Change or Remove from scope (plus a comment) per element; `POST /api/visual-review/review` writes `.discovery/visual-review/<ID>/review.json` (shape: `review.schema.json`, each line anchored by `{diagram, element, graph_hash}`, so a verdict on an older graph shows as outdated; the hash is the approval key, the file without its pure metadata (`meta.commit`, `base_sha`, `branch`, `generated_at`, and `path`, `line`, `end_line`, `note` of source evidence, and any `path:LINE` anchor in a `detail` or `text`), so a moved drawn-on commit or a re-pointed code link keeps the approval and prints `approval carried: metadata only (<fields>)`, while a changed step, lane, arrow, label or element needs the owner again). "Send review" answers the one product question through the ordinary answer path: A when every element is OK, otherwise B with element ids and comments; the orchestrator reads `review.json` on B. The run detail has a `diagrams` field (l0/l1/l2 paths, the arch stems, review counts). VR-06: the canvas shows a stale banner from `vr_map.py check`, and the repo view lists committed diagrams from `vr_map.py list`.

**Architecture impact (the fourth part).** For every stem on the dossier's `Diagrams new:` line (end of `## Approach`) the review gets one more tab, titled by the stem, holding the NEW diagram as it will be committed: the draft `.discovery/visual-review/<ID>/arch/<stem>.json` (view `seq`, `c1` or `c4`; seq drawn in lanes, c1/c4 in the structure layout). Its verdicts are anchored `{diagram: "arch:<stem>", element, graph_hash}` and follow the L0/L1/L2 rules: the four verdicts, outdated on a changed draft, gone when the draft (or the node) is removed, and option A (Approved) only when every element of every view, arch drafts included, is OK on its current hash. The stem is checked against the files present in `arch/` (a plain name, no dot or slash; a symlink is not a view). Scope is new diagrams only: not the existing diagrams on `Diagrams affected:`, not the generated c2/c3 maps. Drafts: a sequence stem is written by `vr_promote.py --preview` from the `new_stem` flow of `l1.json`; a c1 or c4 stem is drafted by `diagram-author` (payload field `DIAGRAMS_NEW`) in the repo's c1/c4 JSON format, every element with `path:line` evidence or marked inferred, and must pass `vr_graph.py validate`. `vr_promote.py --check-approval` is the approval rule over the draft; Phase 9 `vr_promote.py` (no flag) copies the approved draft (dropping `remove` elements), re-anchors its evidence from `meta.base_sha`, validates, writes `docs/architecture/<stem>.json` and the `.md` stub and runs `vr_mermaid.py sync`. `vr_promote.py --shadow-all --dossier-id ID --repo PATH` (driver `vr_promote_preview`, shadow only, Phase 3) builds and validates a seq draft for every L1 flow with an added or changed step, as `--preview` would, and records the outcome and the drafts in the git common dir (`asd-drivers/driver-shadow/<ID>/`); nothing under the dossier changes and nothing reaches the owner's review.

**The sequence tab.** A sequence diagram (a `seq-*` stem, so a draft under `arch/` that `vr_promote.py --preview` wrote, or a committed `docs/architecture/seq-*.json`) is drawn as a real sequence diagram, not as a column of boxes. Participants are columns with a dashed lifeline. Each message is an arrow from the sender's lifeline to the receiver's (`seq.to`, or `to` on an L1 step), in order, with its label above the arrow. The line style follows the call kind: a call is solid with a filled head, a reply is dashed with an open head, an async message is solid with a half head, and a message to the same participant is a small loop beside its lifeline. Added, changed and removed messages are coloured like the other views (green, amber, red; unchanged ones grey) and the colours follow the light or dark theme. The messages are the elements: the same OK, Question, Change and Remove verdicts and follow-up anchors (`arch:<stem>` plus the message id) apply, and nothing new is stored. The preview draft needs no extra route: `/api/visual-review` already lists every `arch/<stem>.json` as a tab, and only plain `.json` files of that folder (not links, not other names) are served. The geometry is computed on the server (`seqLayout` in `shared/questions/vrlayout.ts`, tested in `shared/test/vrseq.test.ts`); the page only draws it.

## Every part is mandatory (stage 3)

The Phase 3 review always has seven parts: L0, L1, L2, the decision flow, the sequence, architecture impact affected and
architecture impact new. Each is real content or an explicit statement. The author writes `parts.json` beside the graphs
(`schema_version "1.0.0"`; `plan.affected` and `plan.new`; `decision_none`, `sequence_none`, `affected_none`, `new_none`,
`unchanged: [{stem, reason}]`, `new_note`); a reason is 8 to 300 characters and never `none` or `<why>`. A part with content and a
statement saying there is none is a conflict. `vr_graph.py parts`, `check_payload.py --kind diagram-author`, the phase checkpoint and
`validate_pipeline.py` fail a review that wrote `parts.json` and lacks a part; a review from before this rule (no `parts.json`) is not
flagged, and a dossier line with a bare `Diagrams affected: none` is still accepted (the author supplies the reason).

**Affected diagrams.** The existing `docs/architecture/*.json` diagrams the change touches are the dossier's
`Diagrams affected:` stems plus the diagrams whose source anchors point into files the contract commit touches (the Phase 9 gate's
rule, `validate_pipeline.affected_diagrams_stage3`). The author drafts the updated version as `arch/<stem>.json` with
`meta.updates_existing: true` (`vr_promote.py --preview` stamps `meta.base_docs_hash`, the sha256 of the committed file); the owner reviews
it as before and after with the same verdicts. A diagram judged unchanged is `Diagram <stem> unchanged: <why>`. Phase 9 applies an
approved draft with `vr_promote.py --stem <stem>` and refuses with `DIVERGED` when the committed file changed since the stamp; the
gate prints a REVIEW line already answered at stage 3 as `SETTLED at stage 3`.

**All diagrams (preview).** `vr_preview_all.py REPO ID [--base REV]` regenerates the full set of a dossier already past stage 3
into `preview-all/` without model and without touching `review.json` or any approved file. The dashboard lists every part (a card with
its reason for each `none`) and shows the preview as a separate view with a comment box per part (`preview-all/comments.json`). A preview
is never an approval; Phase 9 ignores it.

## Stable id rule

An id is derived from the kind plus the qualified name, never from a counter or an array position, so the same element has the same id before and after a change and VR-02 can join on it.

- `crate:harness`, `module:harness::tools::step`, `bin:platform::bin::llm_gateway`, `fn:harness::tools::step::run_group`
- An edge id is its kind plus its two end ids: `edge:depends:crate:a:crate:b`.
- Characters: `A-Za-z0-9_.:-`. Hand-made fixture ids may be short (`s1`), but must be unique.

## Views and budgets

`L0` features (15), `L1` steps (12), `L2` nodes (12). Groups are not counted (D0 report, section 7). `map` is the whole-repo product map and counts its folded first screen: at most 7 top-level groups (areas, plus any node outside a group) and at most 15 nodes inside any one group (the L0 limit applies when an area is opened); groups are counted there because the folded view is the budgeted one. `full` is an unbudgeted extractor graph; VR-05 picks the views a reviewer sees from it.

### The decision view (`decision`)

A flow chart of the conditions a change adds or alters, for the owner who wants to see what happens when. Shapes: `terminal` (alias `stadium`), `process` (alias `rect`), `decision` (alias `diamond`), `io`, `junction` (alias `circle`). A terminal carries `terminal: "start" | "end"`; an end may carry `outcome: "ok" | "error"`; an `io` node may carry `io: "in" | "out"`; `step` names the L1 step a node restates (checked by `vr_graph.py validate --l1 l1.json`); an exit of a decision carries `branch` (`yes`, `no`, `case`, `default`, `error`, `loop`) and a `label`. Ids of decisions are stable: `decision:<fn>:<slug>`.

Rules (`vr_graph.py`, errors): exactly 1 start (`no_start`, `multiple_start`) and at least 1 end (`no_end`); an end has no exit (`end_has_exit`), any other node has one (`dead_end`); every node is reachable from the start (`unreachable_node`); a decision has 2 or 3 exits (`decision_exits`), each labelled (`unlabeled_branch`), the labels unique (`duplicate_branch_label`); a node that is not a decision has one exit (`fork_without_decision`); every cycle contains a decision (`unbounded_loop`); a decision label is at most 40 characters (`decision_label`) and cites `source` evidence, or every entry is `inferred` (`decision_evidence`); an unknown shape or a field on the wrong shape is `bad_shape`. Budget: 14 nodes (terminals and junctions count) and 5 decisions (`over_budget`: split into two diagrams). `vr_graph.py budget` also prints `paths`, the number of start-to-end routes (capped at 99; a loop is walked once). `to` and `call_kind` (the STEP_ROUTING rule) are for sequence drafts only and are never asked of a decision node. A merged graph (`vr_diff.py merge`) is the union of the before and the after flow, so the flow rules are not applied to it (they hold for `view --mode before` and `after`).

Owner decisions (2026-11-23): the decision view joins the Phase 3 diagrams the same way as L0/L1/L2: it is part of the owner's diagram approval in Phase 3, under the same approval and the same gate, and it is shown only when the change has branches. An approved decision diagram is review-only: it is NEVER promoted into `docs/architecture` (unlike `seq-*`), and `vr_promote.py` refuses it as an architecture draft (its views are `c1`, `c4`, `seq`). The diagram-author writes it only when the payload says `DECISION: yes`, the branch rule (below); the renderer draws it.

Branch rule and gate (mechanical): `validate_pipeline.py --dossier <ID> --branch-rule` prints `DECISION: yes|no`; yes when the dossier's `## Acceptance criteria` contain a branch word (`if`, `else`, `otherwise`, `unless`, `retry`, `fall back`, `on error|failure|timeout`, `error`, `fail`, `reject`, `invalid`, `timeout`). `check_payload.py --kind diagram-author` compares the payload's `DECISION` with it. The Phase 3 line is `VISUAL-REVIEW: approved — L0 <n>, L1 <n>, L2 <n>, decision <n>, arch ...`; `validate_pipeline.py --pre-fanout` requires, when `decision.json` exists, the `decision <n>` entry (equal to the file's node count), a valid file and an `ok` for every node in `review.json` on the current hash; when it does not exist and the rule says yes, the gate refuses unless the line says `decision none — <reason>` (the words are no condition of the changed behaviour). No file and no branch words: the gate is as before. `vr_graph.py` and `vr_promote.py` ignore the decision view for promotion: `ARCH_VIEWS` never lists it (selftest `decision:not-an-arch-view`). `F` (`vr_branches.py`, `vr_prepare` computing `DECISION` from the code's branches) will replace the wording rule.

Rendering (the dashboard, `shared/questions`): `decision.json` in the dossier directory is a fourth tab, "Decisions", between Flow and Components, shown only when the file exists (`tabViews`; `reviewViews`, the diagrams that carry verdicts, now also lists `decision` when the file exists, so it takes an OK/Question/Change/Remove per node, a follow-up anchor `decision`, and is part of Approve all; `hasVisualReview` is true for a dossier with only `decision.json`). It is laid out with ELK top to bottom (cycles broken depth first so a retry loop runs back up, the start pinned to the first layer, the yes and single exits straightened), with the shapes of the shape-aware renderer and the branch labels (`YES`, `NO`) on the edges. In Diff mode added, changed and removed nodes and branches are styled as in every other view, and the legend line reads "decisions a → b · paths a → b · new error ends n" (`vrDecisionStats`, the same numbers as `meta.diff.decision` that `vr_diff.py merge` writes: decisions and start-to-end paths of the before side and the after side, and the error ends the change adds). The Mermaid export is a `flowchart TD` with stadium `([..])`, diamond `{..}` and parallelogram `[/../]` shapes and `-- "label" -->` branches. Fixtures: `decision-retry-base.json` (the before graph), `decision-retry.json` (the after graph), `decision-retry-diff.json` (`vr_diff.py merge` of the two). A decision view is never an architecture draft and is never promoted into `docs/architecture` (`vr_promote.py` and `ARCH_VIEWS` list c1, c4 and seq only).

## CLI

```
scripts/vr_graph.py validate GRAPH.json [--root REPO] [--no-budget]   # issues as JSON, exit 1 on any
scripts/vr_graph.py budget GRAPH.json
scripts/vr_graph.py parts DIR [--decision yes|no] [--new stems|none] [--affected stems|none]   # stage 3: every part is content or a stated reason, exit 1 on any
scripts/vr_promote.py --write-previews --dossier-id ID --repo REPO   # the seq-preview/ of the sequence part, whatever the driver says
scripts/vr_preview_all.py REPO ID [--base REV]   # the whole set again, preview only, under .discovery/visual-review/ID/preview-all/
scripts/vr_extract.py python|csharp REPO [--layers 1,2,3] [--calls f1,f2] [--cfg-test] -o out.json   # layers: 1 projects, 2 modules/namespaces + types, 3 functions
scripts/vr_extract.py rust REPO [--layers 1,2] [--crates a,b] [--calls fn1,fn2] [--timeout 120] [--cfg-test] -o out.json
scripts/vr_diff.py merge --before B.json --after A.json [-o merged.json]   # issues as JSON on stderr, exit 1
scripts/vr_diff.py view merged.json --mode before|after|diff [-o out.json]
scripts/vr_diff.py after-from-git REPO --base REV --head REV [--layers 1,2] [--crates a,b] [--calls f,g] -o after.json
scripts/vr_prepare.py REPO ID --base REV --head REV [--crates a,b|all]   # default: crates derived from the changed files
scripts/vr_diff.py --selftest
scripts/vr_extract.py --selftest | --live REPO     # also vr_graph.py --selftest, vr_ra.py --selftest
```

`--crates a,b` runs layer 2 only for those crates; layer 1 stays the whole workspace, every other crate keeps its crate node, and `meta.layers.modules` gets `limited_to` and `skipped_crates`. `vr_prepare.py` derives the set from `git diff --name-only BASE HEAD` (a changed `Cargo.toml` or a file under `src/` maps to the package of the nearest manifest above it) and uses it for the before and the after side; `--crates all` turns the limit off. Merged graphs then hold module nodes only for the touched crates.

**Python and C# extractors** (`vr_extract_py.py`, `vr_extract_cs.py`, issue #393). Same graph shape as the Rust path (view `full`, same schema), ids `project:`, `module:`, `type:`, `fn:` (the last `::` segment of an `fn:` id is the function name, so `vr_prepare` finds touched functions the same way). Layers: 1 projects (pyproject/setup or `.csproj`, `depends` from `ProjectReference`), 2 modules or namespaces and types (`contains`, `uses` for imports and extends/implements inside the repo), 3 functions and methods; call edges (`--calls`) only for the named functions and their direct callers and callees. Test code is left out unless `--cfg-test` (Python: `tests/`, `test_*.py`; C#: test projects). Honesty rule: a call is `static` when the name resolves to one definition (own type, `this`/`base`, a typed field, parameter or `new` local, a static type, an extension method), `dispatch` for an interface, abstract, virtual or overridden member, `inferred` when two or more candidates or no arity fits; anything that cannot be resolved inside the repository (calls on call results, lambdas, reflection, dependency-injection wiring, outside libraries, dynamic attributes) is not drawn, never guessed. C# is read by a declaration scanner (comments and strings blanked, bodies brace-matched), not by Roslyn: no dotnet and no third-party package is needed. A language with no extractor (Go, TypeScript, ...) keeps the `VISUAL-REVIEW: skipped` record.

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
scripts/vr_c2_drift.py --selftest | vr_map.py --selftest | vr_metrics.py --selftest | flow_coverage.py --selftest
scripts/vr_mermaid.py import FILE.md [--write] [--adopt] | render FILE.json | sync DIR | check DIR | --selftest   # exit 1 on a problem
bun shared/test/vr-canvas.browser.ts                  # FU-04: browser check of the rendered canvas (needs `bun install` in shared/ and Google Chrome; else SKIP, exit 0)
```

Canvas size (owner decision 2026-11-16, "readable minimum + pan"): the canvas opens fitted to its box but never below the scale at which the smallest label text renders at 12 px (read from the drawn text); a larger diagram overflows the box and starts at its top-left, and is panned inside the box (drag, touch drag, wheel or trackpad scroll) so the page itself never scrolls sideways. The "Fit all" button shows the whole diagram (labels may be small) and toggles to "Readable size"; the zoom buttons still work and may go below the minimum on request.

`shared/test/vr-canvas.browser.ts` (FU-04) opens the dashboard page on the W-145 fixtures at 390 and 1280 px in the installed Google Chrome (puppeteer-core, no browser download; `ASD_CHROME` overrides the path), reads the DOM of the three tabs and fails on a label rendered under 12 px, two overlapping label boxes, a label wider than or outside its node box, a horizontal page scroll or a console or page error. The 12 px rule applies to the opening state and after "Readable size", not in "Fit all"; the other rules apply everywhere. Two seeded fixtures (an unbreakable label, and a three-line label crowding a member count) must fail. No screenshots are taken or sent to a model; the server runs on an ephemeral port with every state dir in a temp folder. Without Chrome or puppeteer-core it prints one SKIP line and exits 0. It is not part of `bun test`.

Architecture diagrams (c1 to c4, sequence) are change-graph JSON beside their `.md`; the `.md`'s Mermaid block is generated from it (`vr_mermaid.py`). To change one: edit the JSON, run `vr_mermaid.py sync docs/architecture`, commit both; the prose stays hand-written. `vr_mermaid.py check docs/architecture` belongs in CI and in the close gate. `vr_c2_drift.py` and `vr_c3_agree.py` read the JSON when it exists and parse the Mermaid otherwise; the dashboard opens a `.md` as a graph when its JSON exists, as raw Mermaid otherwise.

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
- **A layer that did not run never deletes views**: when cargo-modules is missing or the wrong version, or a crate's target fails, the previous `modules-<crate>.json`, the `calls-<crate>.json` that depend on its module parts and `crate-calls.json` stay on disk and in the manifest (`layers.modules.kept`, and a `kept:` line in the output). A view is removed only when its crate left the workspace. Manifest entries are accepted as plain view file names only (`crate-map`, `crate-calls`, `modules-*`, `calls-*`, `product-map`); anything else (`../x`) is ignored with a warning.
- **Manifest and product map base**: `map-manifest.json` is rewritten only when a view or the list of views changed, so a stub cut with no map change leaves a clean tree and no pull request; it holds no timings. The product map's own base commit is `product_map_commit`, advanced only when the product map was refreshed (`--no-product-map`, a missing feature book or a map error leaves it), so a later refresh still sees the whole range.
- **Cache keys**: modules also cover the workspace `Cargo.toml`, `Cargo.lock` and target roots outside `src/` (`#[path]` modules outside `src/` are not tracked); untracked `.rs` files are hashed like tracked ones; the call salt includes the rust-analyzer version and the crate set. rust-analyzer runs with the module layer's target (`x86_64-unknown-linux-gnu`) and all features, so Linux-only code resolves on any host. A file whose answer looked like a server still indexing is asked once more at the end of the run, once the server has resolved something; only an entry still unconfirmed then is marked `provisional` and recomputed by the next run. Before the index is proven, only the first function of a file waits for it, so a file whose functions cannot resolve costs seconds, not minutes. The dependents of changed files are persisted (`pending-dependents.json`) at every stop, so a partial run does not lose them. Cache writes use a unique temp name per writer.
- **Limits**: whole-repo builds allow up to 8 h (`--calls-limit`), 300 s per file (`--file-limit`) and 1800 s for indexing (`--index-limit`); the 120 s VC-08 limit stays for per-dossier runs. rust-analyzer runs with build scripts, proc macros, check-on-save and cache priming off (`vr_ra.FAST_OPTIONS`), so calls that exist only in macro-expanded code are not seen.
- **Stub hook**: the refresh runs at two moments. `/stub` runs `map_refresh_pr.py --moment integrate --json` after the stub's pull request is open and before the full run (branch `chore/map-refresh-<date>-integrate`, pull request into the stub under integration, merged by the command after `wait_ci.py`; before the run because a `local-stack` receipt is bound to one head SHA, and again before a re-run after a product-code fix; a failure is logged `MAP-REFRESH: failed` and never blocks), so development gets current maps with the stub. `stub_target.py` also starts `map_refresh_pr.py` detached after cutting a new stub, as the fallback. It runs the refresh from the previous map's extraction commit in a temporary worktree of the new stub and opens a pull request `chore/map-refresh-<date>` into it (the refresh is bounded by `ASD_MAP_REFRESH_TIMEOUT`, default 3600 s, with a call-layer limit of 60% of it; a refresh whose only effect is deleting files is refused, not committed; opt-in: only when the repo already commits `map-manifest.json` or `crate-map.json`; `ASD_MAP_REFRESH=0` disables, `=1` forces). Never a push to development.
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

## Flow coverage

`scripts/flow_coverage.py --repo REPO [--json]` answers "which runtime entry points of this repo have no sequence diagram?" for a repo with `docs/architecture/seq-*.json`. Rust only for now (the signal table is per language, so C# can be added). Entry points: `fn main` in `src/main.rs` and `src/bin/*.rs`; axum `Router::new()` ... `.route("/path", ..)` (one entry per route); `pub async fn run(` taking a `shutdown` parameter; `pub fn register_*(` taking `&mut ToolRegistry`; `pub async fn serve_*_requests(`. Test code (`tests/`, `#[cfg(test)]`) is skipped. In a git work tree the candidate files are `git ls-files` (tracked and staged) plus untracked files that are not ignored, so a gitignored directory of cloned third-party code (such as `inspiration-repos/`) is never counted; a directory that is not a git work tree is walked.

A diagram anchors code as `path/file.rs:LINE` (optionally followed by a backticked identifier) anywhere in its JSON text, plus `evidence[].path`. An entry is `covered` when some diagram anchors a line inside the entry function (first line to its closing brace) or names the function in the same file, `partial` when a diagram anchors only the file, `uncovered` otherwise. A `register_*` tool registration counts as covered when a diagram anchors any line of its file (the tool body lives beside it). A `run` with a generic `F: Future` shutdown parameter matches too. The report also lists seq diagrams with zero anchors. Report mode always exits 0.

`--changed BASE_REF` limits the report to entry points in files changed by `git diff BASE_REF...HEAD` and exits 1 when an entry point that is NEW since the merge base (compared by path, kind and name against `git show <merge-base>:<path>`, renames followed) is uncovered and not exempt; older uncovered entries in touched files print as `REVIEW` lines and do not fail (the Phase 9 diagram gate always runs it and makes each such entry a DEFECT). A repo may commit `docs/architecture/flow-coverage.json`:

```json
{"exempt": [{"entry": "crates/x/src/bin/tool.rs::main", "why": "one-shot, no flow"}],
 "extra_signals": [{"name": "job", "regex": "fn (?P<name>job_\\w+)\\(", "glob": "crates/**/*.rs"}]}
```

`entry` is `<path>::<fn or route path>`; an exemption without `why` is an error (exit 2). One-shot binaries are not auto-exempt. `flow_coverage.py --selftest` checks the checker.

## Follow-ups

Work decided but not done yet is in [FOLLOW-UPS.md](FOLLOW-UPS.md) (FU-01: move the cargo-modules pin past 0.27.0 after the first demo).

Dashboard page layout (dossier redesign): the diagram toolbar is one row (view tabs, Before/After/Diff, Fit all, Walkthrough) plus a **More** menu holding zoom in, zoom out, Mermaid and Full screen. `/api/visual-review` and `/api/visual-review/layout` are cached in `server.ts` per dossier directory, keyed on the size and mtime of l0/l1/l2/decision.json, `arch/*.json` and `seq-preview/*.json` (`shared/questions/vrcache.ts`, 24 results, in-flight requests shared); an edit of any of those files is a miss, `review.json` content is always read fresh. The panes of the dossier page are a registry (`PANES` in `page.ts`), so a later review pane is one more entry.
