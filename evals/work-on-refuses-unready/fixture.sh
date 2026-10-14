#!/usr/bin/env bash
# A dossier that has not passed its plan review: status planned.
set -euo pipefail
mkdir -p .discovery/dossiers
cat > .discovery/dossiers/W-014-coalesce-flush-calls.md <<'DOSSIER'
---
id: W-014
title: Coalesce concurrent flush calls in FlushQueue
status: planned
created: 2026-08-10
updated: 2026-08-10
anchors:
  - src/flush.py:41
baseline_commit: 3f2611f
jira: null
branch: fix/flush-coalescing
worktree: null
pr: null
blocked_by: []
adrs: []
---

## Problem

Two concurrent `flush()` calls each drain the queue.
DOSSIER
