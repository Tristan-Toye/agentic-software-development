# Phase docs split, compact script output, batched failing-test fixes

Measured 2026-11-09..12 over orchestrator transcripts.

## Problem

- The Opus orchestrator read a phase doc whole about 223 times (average 10 KB per read) against
  140 `ref_section` calls. `phase-4.md` (32,047 B on disk, about 23 KB per read) cost 15M cache-read
  tokens, `phase-6.md` (17,366 B) 11M, `phase-7.md` (18,158 B) 7.6M. Most of each read was detail a run
  never needed.
- Verbose script output stayed in context: `git_state.py` about 4.3 KB per call (6.5M total),
  `compose_payloads.py` 242 calls (6.4M), `dossier_edit.py section` 171 calls.
- Heavy dossiers respawned fixers: config-harness W-117 spawned 26 integration-test-authors, W-134 66
  subagents in correction loops (one spawn per failing test).

## What changed

- Core docs now hold what every run needs; the rest sits in the matching `phase-N-conditional.md`
  as `###` sections, reached by `ref_section` from a one-line `Detail:` pointer left where the text was.
  Every rule moved whole (`check_rule_moves.py`: LOST 0; the 9 ALLOWED sentences are pointer
  sentences rewritten to name the new place).

  | Doc | Before | After core | Conditional file |
  |---|---|---|---|
  | `references/work-on/phase-4.md` | 32,047 B | 10,795 B | 28,494 B |
  | `references/work-on/phase-6.md` | 17,366 B | 10,705 B | 19,881 B |
  | `references/work-on/phase-7.md` | 18,158 B | 9,601 B | 17,303 B |

- `git_state.py`: compact default (status counts plus 5 paths, diff-stat summary lines, 3 commits,
  about 1 KB); `--full` restores per-file rows and the 20/5 caps. `phase-9.md` uses `--full`.
- `compose_payloads.py`: one summary line per payload (`wrote P; filled N; left as @@FILL: ...`);
  `--verbose` prints the field list and the manifest line.
- `dossier_edit.py section`: capped at 120 lines (head; the last 120 for `## Build log`) with a
  trailing omitted-lines note; `--all` prints everything, `--lines N` sets the cap. `stub.md` and
  `fix-deferred.md` read the whole Build log with `--all`.
- Batched failing-test fixes only: `phase-6.md` "Rule on the whole round, then correct once per package"
  and `phase-6-conditional.md` "One agent per package per round, carrying all its failures". The
  implementer payload's `FAILURES:` holds one block per failing test of the package
  (`references/payloads/implementer.md`); `check_payload.py` selftest covers a multi-failure block.
  No caps were added.

## Measure after

- Same transcript sweep as above after the plugin lands: count whole phase-doc Reads versus
  `ref_section`, and cache-read tokens per phase doc (`scripts/token_report.py --since <merge time>`).
- Bytes per call of `git_state.py`, `compose_payloads.py`, `dossier_edit.py section`.
- Spawns per correction round and per dossier in `/work-on` build logs (`RETURNED:` lines); target:
  at most one fix agent per package per round.

## Sources

- `references/work-on/phase-6.md:70` (batching rule), `references/work-on/phase-6-conditional.md:44`
- `references/payloads/implementer.md` (FAILURES block), `scripts/check_payload.py:1813` (selftest)
- `scripts/git_state.py:183` (`run`, compact/full), `scripts/compose_payloads.py:1639` (`--verbose`)
- `scripts/dossier_edit.py:301` (`SECTION_CAP`, `section`)
- Pointer rewrites: `references/work-on/phase-{5,6,7,8}.md`, `primary-agents/work-on.md`
