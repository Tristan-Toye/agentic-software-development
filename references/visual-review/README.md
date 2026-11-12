# Visual review: change graph

The model behind the visual review of a change (feature VR-01). Language-neutral.

| File | What it is |
|---|---|
| `graph.schema.json` | The change graph schema, version 1.0.0: nodes, edges, groups, flows (before/after steps, lanes), status, evidence, resolution, view, tool versions in `meta`. |
| `fixtures/` | `w145-l0|l1|l2.json` (the W-145 change, migrated), `removed.json` (removed elements), `minimal.json`, `invalid/<issue code>.json` (one per validator issue), `repo/` (the tiny tree the invalid fixtures cite). |
| `../../scripts/vr_graph.py` | Validator and budget check. |
| `../../scripts/vr_extract.py` | Extractor: the "before" graph of a Cargo workspace. |
| `../../scripts/vr_ra.py` | rust-analyzer call hierarchy client, used by the extractor's `--calls`. |

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
scripts/vr_extract.py --selftest | --live REPO     # also vr_graph.py --selftest, vr_ra.py --selftest
```

Layer 2 needs `cargo-modules` 0.27.0; without it the layer is recorded as `skipped` in `meta.layers`. Python: `import vr_graph; vr_graph.load()`, `.validate(graph, root=None)`, `.budget(graph)`.
