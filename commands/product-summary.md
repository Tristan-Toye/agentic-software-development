---
description: >-
  The product summary of a finished dossier: what the change adds or changes,
  for the product first and the code second, in a short page the owner can
  read in a minute. Written once the build is done (the runner runs it right
  after `/work-on` Phase 9 and stores it beside the run); the dashboard shows
  it above the dossier. A summary of what was built, not a review.
argument-hint: "<dossier ID, e.g. W-128>"
---

# /product-summary — what this dossier changed, in product terms

`$ARGUMENTS` is the dossier ID. Refuse when it names no dossier, or when the
dossier is not `done`, `pr` or `review`: there is nothing built to describe.

## Read, briefly

Read only what tells you what was built. Do not audit, do not re-run
anything, do not look for problems.

1. The dossier: title, `## Problem`, `## Approach`, the acceptance criteria,
   and the last lines of `## Build log` (the outcome, the deferred count).
   A finished `/work-on` removed its worktree: the dossier's own copy is on
   its build branch (`git show <branch>:.discovery/dossiers/<file>`), the
   checked-out branch has the pre-build one until the PR merges.
2. Its pull request: `gh pr view <pr> --json title,body,files` for the
   changed surface, and `gh pr diff <pr> --name-only` when the file list is
   cut.
3. Any ADR the build added (named in the build log or the PR body): its
   decision line only.

## Write

Plain sentences for a person reading on a phone; markdown bullets and at most
one small table. At most about 250 words. No commands, no SHAs, no log output;
a path, type or flag name only where it is the point (a new config key, a new
CLI flag, a new endpoint).

```
**In one line:** <what someone using the product can now do, or what now
behaves differently, and for whom>

### What changes for users and operators
- <behaviour, output, configuration, limits — what they will notice>

### Under the hood
- <the technical shape: new types, flows, storage, contracts — the few that
  matter to a developer picking this up>

### Not in this change
- <what was left out on purpose, and the deferred issues in one line:
  "N deferred, the worst: <title>">
```

Leave out a section that would be empty, except **In one line**. Describe what
the code does now, not how the run went: no stages, waves, arbitrations or
review rounds. Under the runner, end with `RUNNER: DONE`; the runner stores
everything above that line.
