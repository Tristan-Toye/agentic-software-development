<!-- /work-on, Phase 9, the review packet. phase-9.md names it; read the one
     section with ref_section, never the whole file. -->

# /work-on Phase 9 — the review packet

### The review packet: six data producers, run before the PR

Run once, on the final head, after the suite verdict (step 2) and before the PR
description. The data is for the reviewer; nothing here gates the run.

```
python3 ${PLUGIN_ROOT}/scripts/review_packet.py all --dossier <ID> --base <BASE> \
    [--test-log <cargo/nextest/pytest output>] [--scope <blast-radius SCOPE file>] \
    [--layers <layer map>] [--migrations <glob>] --log
```

| File (under `.discovery/review-packet/<ID>/`) | What it holds | Source |
|---|---|---|
| `contract-diff.json` | signature changes, severity ERR / WARN / INFO, a `cannot_confirm` tier, a `not_caught` list | `vr_diff` merged graph; the base revision for the before side; `cargo-semver-checks` only if already installed |
| `criteria-evidence.json` | one row per acceptance criterion: tests, results, diff hunks, how each link was found, status covered / partial / no-evidence | the Phase 3 `(owner; env)` map, `PROMISE_CHECKLIST`, `CENSUS` and `SUITE` lines |
| `reading-order.json` | contract, then the riskiest package, then the rest grouped and ranked, with a `not_listed` count | work packages, `review_signals.py`, the blast-radius `SCOPE` |
| `coupling-delta.json` | new and removed `depends` edges, new cycles, layer breaks | `vr_diff` merged graph; layer breaks need `--layers` |
| `invariants.json` | invariants and pre-mortem failure modes, each anchored or `speculative` | the checklist's property lines, contract prose, `review_signals.py` markers, and the orchestrator's own |
| `migration-lint.json` | findings of a fixed rule list (MIG001 drop table, MIG002 drop column, MIG003 NOT NULL without default, MIG004 type change, MIG005 rename, MIG006 edit to a migration already on the base, MIG007 data backfill beside a schema change, MIG008 truncate), each a rule id at `path:line`; `-- lint:ignore MIGnnn` lists one under `suppressed`; skipped when the change touches no migration | the SQL files the change adds or edits, found like `merge_dossier.py`'s number-clash check (`migrations/NNNN_name.sql`; `--migrations GLOB` overrides) |

The orchestrator authors its own invariants and failure modes in the same turn
(no new sub-agent): write `invariants.authored.json` in the output directory as a
list of `{"kind": "invariant" | "pre-mortem", "text", "anchor", "check"}` before
the run. An `anchor` is a `path:line` that exists or `test:<name>` found in the
tree; anything else is kept as `speculative`.

Schemas: `references/visual-review/review-packet-<name>.schema.json`. A skipped
producer (no graph, a language the extractor does not cover, no git range) still
writes its file with `skipped`; read the receipt line, do not retry, and go on.
Never edit a produced file by hand; rerun the command.
