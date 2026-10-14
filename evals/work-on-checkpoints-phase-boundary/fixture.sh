#!/usr/bin/env bash
# A dossier in flight whose Build log records a finished Phase 1.
set -euo pipefail
mkdir -p .discovery/dossiers
cat > .discovery/dossiers/W-014-coalesce-flush-calls.md <<'DOSSIER'
---
id: W-014
title: Coalesce concurrent flush calls in FlushQueue
status: building
created: 2026-08-10
updated: 2026-08-11
anchors:
  - src/flush.py:41
baseline_commit: 3f2611f
jira: null
branch: fix/flush-coalescing
worktree: ../repo-W-014
pr: null
blocked_by: []
adrs: []
---

## Problem

Two concurrent `flush()` calls each drain the queue.

## Build log

- Phase 1 — contract written and committed (3f2611f), stubs compile, contract review clean.
DOSSIER
