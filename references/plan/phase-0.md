<!-- /plan, Phase 0. Read at the start of this phase; the rules that hold
     in every phase stay in primary-agents/plan.md. -->

## Phase 0 — Mode: is `.discovery/` committed?

Right after the time-logging hand-off, before you read `$ARGUMENTS`, settle
the one fact that decides where this run writes. Run it in the checkout
`/plan` was started from (`formats/README.md` § "Two modes for `.discovery/`"):

```bash
python3 ${PLUGIN_ROOT}/scripts/validate_pipeline.py --root . --mode
```

Its first line is the answer — `mode: local`, `mode: committed` or
`mode: conflict` — and the lines after it are the checks that mode relies on.

- **`local`** (the default) — `.discovery/` is untracked working state. Keep
  the **gitignore guarantee**: check `.discovery/` is in the repository's
  `.gitignore`, and add it if it is absent (`--mode` warns when it is). The
  dossier never lands in a commit, and the run works in this checkout.
- **`committed`** — the repository tracks its dossiers, so a dossier is a
  reviewable change like any other. The run writes the dossier in **its own
  worktree on a `plan/` branch** (made at the end of Phase 1) and ends by
  opening a PR with it (Phase 8). `--mode` warns for each of the three paths
  that stay local in either mode — `.discovery/analysis/`,
  `.discovery/pr-draft-*.md`, `.discovery/deferred-ledger.md` — when
  `.gitignore` does not cover it. Add the missing patterns to `.gitignore`
  inside the plan worktree once it exists, so the plan PR carries them.
- **`conflict`** (exit 1) — tracked files under `.discovery/` while
  `.gitignore` also ignores a new dossier: a repository in two minds, where
  the dossier would vanish from its PR. Stop and show the user the
  conflicting `.gitignore` line; never pick a side yourself (a real uncertainty, `--kind uncertainty`).

State the mode in one line before Phase 1. Every later phase names
`${RUN_ROOT}`: **the checkout `/plan` was started from in local mode, the plan
worktree in committed mode.** Every relative path from Phase 2 on resolves
against `${RUN_ROOT}`, and every payload path is its absolute expansion.
Then make the first checkpoint, `Phase 0 — plan: mode`, so the learned rules
for `plan` reach you before intake (`primary-agents/plan.md`, "Checkpoint each
phase").
