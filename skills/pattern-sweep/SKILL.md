---
name: pattern-sweep
description: >-
  Interactive sweep of one repository for bad habits that agents copy and
  spread (swallowed errors, unwrap in production paths, sleeps in tests, copied
  helpers, magic numbers, TODO clusters, duplicated config parsing, mixed error
  types, dead flags, over-long files, one module doing X two ways). Ends with a
  ranked recommendation of /plan items, never starts /plan, and records the run
  so the dashboard shows when it last ran. Interactive sessions only.
argument-hint: "[repo name or path; empty = the repository of the current directory]"
---

# /pattern-sweep — find the habits that spread

Agents copy what already exists in a codebase, so one bad habit becomes many.
This sweep finds the habits that are spreading and recommends `/plan` items to
stop them. It reads; it writes nothing in the repository and never starts
`/plan`: the owner picks.

## Step 0 — interactive only

Refuse and stop, with one line, when any of these holds:

- `ASD_RUN_KEY` is set (the environment of a runner session: a dossier run, a
  stub run, auto-work). Check with `printenv ASD_RUN_KEY`.
- the session is headless (`claude -p`, no one to answer): the recommendation
  needs the owner to read it and choose.

Do not record a run in these cases.

## Step 1 — pick the repository

Use the argument, else the git top level of the current directory
(`git rev-parse --show-toplevel`). Name it R. If it is a worktree, say which
branch it is on; the sweep reads that tree as it is. Do not pull or switch.

## Step 2 — the evidence

```
python3 scripts/pattern_sweep.py scan --root R
```

(path relative to this plugin; one to ten seconds). The JSON holds, per
pattern, `count`, `files` (spread), `recent_files` (files that changed in the
last 90 days, so how alive the habit is), up to three `anchors` and a
`score` = spread x harm. It also holds `long_files`, `copied_blocks` (the same
six lines in three or more files) and `error_type_families`.

The scan is regex evidence, not a verdict. Language lens:

- **Rust** (config-harness): `unwrap`/`expect` outside tests, `let _ =` and
  `.ok();` that drop a `Result`, `Result<_, String>`, mixed `Box<dyn Error>`,
  `anyhow` and `thiserror` (drift when two or more families live side by side),
  `.clone()` to dodge borrows, `cfg(feature)` flags nobody turns on.
- **C# and terraform** (podcast-generation-engine): `catch (Exception)` and
  empty `catch`, `async void`, `.Result`/`.Wait()`; terraform open CIDRs,
  hard-coded account or AMI ids, literal `count`, unpinned versions,
  `ignore_changes`.
- Any language: `Thread.Sleep`/`sleep` in tests, magic numbers, TODO/FIXME
  clusters (3+ in one file), env or JSON read ad hoc in many places, files over
  800 lines.

## Step 3 — judge, with cheap reading

The counts over-report (tests inside `src`, generated code, deliberate
`expect` on an invariant). For the top 8 to 10 patterns by score, confirm by
reading the anchors. Use `code-scout` sub-agents for the reading (small model,
one question each, for example: "which of these 12 `unwrap` calls sit on a
path that handles external input, as path:line rows") and spawn them in one
message. Also check what the scan cannot count:

- **over-long functions**: ask a scout for the functions over about 80 lines in
  each `long_files` entry;
- **pattern drift**: one module doing X two ways (two retry helpers, two
  config loaders, two time sources); `git log -S` or `git log --since=90.days
  -- <path>` shows which way newer code goes;
- **how it spreads**: look at `git log --since=90.days --format=%h -S'<snippet>'`
  for the newest copies; a habit that appears in the last 30 days of commits
  is spreading, a habit only in old files is debt, not a habit.

Drop a finding when it is one file, old, and harmless. Keep it when it is in
many files, recent, and can lose data, hide a failure or flake a test.

## Step 4 — recommend (in chat)

Rank by spread x harm (the script's `score`, corrected by what you read).
Print at most 7 items, each:

```
N. <plan title, imperative>
   evidence: <count> in <files> files (<recent> touched in the last 90 days)
             path:line, path:line, path:line
   why it spreads: <the copy-me shape, for example "the helper lives in a
             test-support module every new test imports">
   fix shape: <the change, the seam, what a checker rule would assert>
   size: S | M | L
```

Then an optional **More** list: a checker rule or lint that stops regrowth
(a grep gate in the repo's own checks, one line each), a shared helper to
introduce first, a standard to add or tighten. End with:
"Say which items to take; I will not start /plan on my own." Never invent
counts: every number comes from the scan or from what a scout returned.

## Step 5 — record the run

After the recommendation is printed (also when it is "nothing worth a plan",
with 0 items):

```
python3 scripts/pattern_sweep.py record --root R --items N --summary "<one line: the top 2-3 titles>"
```

`N` is the number of recommended items. It writes
`~/.agentic/pattern-sweep/<repo>.json` (`ASD_AGENTIC_DIR` overrides the
directory); the dashboard's repo page shows "Pattern sweep: last run N days ago
(k items)", in alarm style when never run or older than 30 days. Do not record
before the owner has seen the recommendation.
