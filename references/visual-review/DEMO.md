# Visual review: demo runbook

Short and plain. The owner deploys the plugin first (the merged plugin must be what the dashboard runs from); nothing here restarts the LaunchAgent.

## 1. Prepare the facts (mechanical)

From the plugin's `scripts/`, against the target repo (a git checkout with the base and head commits):

```
python3 vr_prepare.py REPO W-145 --base BASE --head HEAD
```

- It derives the touched crates from `git diff --name-only BASE HEAD` and extracts only those for module detail on both sides (`--crates a,b` sets them, `--crates all` turns the limit off).
- It writes `REPO/.discovery/visual-review/W-145/work/` (`merged.json`, `candidates.json`, `summary.json`). Expect 2 to 3 minutes on a large workspace; the rust-analyzer call layer usually times out at 120 s (see limits).

## 2. Author the three views

The `diagram-author` agent (or you, following `sub-agents/diagram-author.md`) writes `l0.json`, `l1.json`, `l2.json` into `REPO/.discovery/visual-review/W-145/` and checks each with:

```
python3 vr_graph.py validate REPO/.discovery/visual-review/W-145/l1.json --root REPO
```

A ready-made set for W-145 is in `fixtures/demo-w145/`; copy it instead to skip steps 1 and 2:

```
mkdir -p REPO/.discovery/visual-review/W-145 && cp fixtures/demo-w145/l*.json REPO/.discovery/visual-review/W-145/
```

(It cites config-harness paths and lines at commit 30e77c94; use it with that checkout so the evidence paths resolve.)

## 3. Start a dashboard on a spare port

```
ASD_QUESTIONS_PORT=7399 ASD_RUNS_ROOTS=REPO ASD_QUESTIONS_NO_SLACK=1 ASD_QUESTIONS_NO_NOTIFY=1 ASD_RUNS_NO_GH=1 \
  bun run shared/questions/server.ts
```

Open `http://127.0.0.1:7399`, then the repo and run `W-145`. The repo must hold the dossier file `.discovery/dossiers/W-145.md`; the panel is found through the dossier's repo roots. Stop the server with Ctrl-C; the production dashboard on 7317 is untouched.

## 4. What to click

The panel sits above the dossier's questions.

- Tabs **Features** (L0), **Flow** (L1), **Components** (L2).
- Mode buttons **Before**, **After**, **Diff**: Before hides added elements, After hides removed ones, Diff shows all.
- Click a node: its evidence (path:line, or the inferred note) shows; a source line is the place to open in the code.

## 5. What each view shows

| View | Shows | Marks |
|---|---|---|
| Features | what the owner gets, in product words (up to 15) | `+` added, `~` changed, `-` removed, `=` context |
| Flow | one request through the changed code, in lanes and order, with before and after per step (up to 12) | same marks |
| Components | the crates and modules that change, grouped by crate (up to 12 nodes) | same marks |

## 6. Known limits

- The rust-analyzer call layer times out on a workspace of the size of config-harness: no `fn:` nodes, so the Flow view rests on the contract and on source lines the author read, not on a call hierarchy.
- Module detail only for the touched crates; other crates appear as crate nodes only.
- Rust only. The labels are written by a model: a label can be wrong, the evidence line is the check. A node marked `inferred` has no source line.
- L0 and L1 elements have no node in `merged.json`, so their status is the author's judgement.
- The demo set was authored once, by an agent run, on one change; it is a demonstration, not a measured quality.
