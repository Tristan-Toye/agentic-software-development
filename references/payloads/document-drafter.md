### document-drafter

`MODE: adr`:

```
MODE: adr
DECISIONS: |
  - decision: <one sentence, the orchestrator's words>
    evidence: |
      <verbatim quote + path:line, selected by the orchestrator>
FORMAT: |
  <the ADR shape from references/formats/adr.md, pasted verbatim — sections,
   front matter fields, validator rules>
TARGET_PATHS: /abs/path/repo-W-014/docs/adr/0031-flush-drain-lock.md
SCRUB: W-014, .discovery, repo-W-014
```

`MODE: pr`:

```
MODE: pr
DOSSIER-EXCERPTS: |
  <## Problem, ## Approach, ## Acceptance criteria — verbatim>
FORMAT: |
  <the PR description shape this repo uses>
TARGET_PATHS: /abs/path/repo/.discovery/pr-draft-W-014.md
          # committed mode: under X — /abs/path/repo-W-014/.discovery/pr-draft-W-014.md
SCRUB: W-014, .discovery/dossiers, repo-W-014
          # committed mode: repo-W-014 only — the dossier is inside the PR
```

The drafter writes `TARGET_PATHS` itself — its `Write` map admits
`docs/adr/`, `.discovery/`, and `.agent-staging/` only, so the files land
without an orchestrator hand-placement. Its scrub check runs over the
written bytes. You still re-grep the files for the `SCRUB` tokens before
anything leaves the machine: two checks, because a leaked dossier id is a
leaked local path.

## Field rules that matter

- **`SCRUB` lists every token that must not leave the machine.** The drafter
  greps its own output and you grep again before the description reaches the
  PR — two checks, because a leaked dossier id is a leaked local path.

---
