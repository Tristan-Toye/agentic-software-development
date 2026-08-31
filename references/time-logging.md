# Time logging — Tempo is the mechanism (do this first, once, at the top)

Time logging is **not** this plugin's job. It is done through the Twipe DevKit
**Tempo** integration — an external tool that lives outside this repo. This
plugin's commands only have to do one thing about time: **hand off to Tempo at
the very start, then get out of the way.** They never write durations to Jira
themselves and never keep their own clock in parallel with Tempo.

Every command quotes this file from a short preamble. The rules below are the
whole contract; the commands do not restate them.

## 1. It is the first action of the command — always

Before any command-specific work (before Phase 0, before selecting a
dossier), the orchestrator's **first step** is to invoke the
DevKit **`time-logging` skill** (e.g. `twipe-devkit-dev:time-logging`). That
skill offers time logging, settles the Jira ticket via Rovo, and starts the
Tempo session. Starting it first is the point: the clock then covers the whole
command, not just the later phases.

- This holds for **every** command in this plugin, read-only ones included.
  The skill asks the user first and never logs silently, so an offer on a quick
  `/open-work` costs one question the user can decline.
- If the Tempo capability is **not present** in the running environment (no
  `twipe_*` time tools, skill unavailable), skip time logging entirely and
  proceed — do **not** fall back to writing Jira time comments or a parallel
  clock. "No Tempo" means "no time logging" for that run.

## 2. Only the orchestrator does this — never a sub-agent

The command session that the user invoked owns the whole session lifecycle:
start at the top, `twipe_finalize_time_session` at the end (or
`twipe_cancel_time_session` to discard). **Sub-agents never start, finalize, or
reason about time logging** — they inherit the running session implicitly and
report work upward. A spawned `implementer`, reviewer, or any council seat
must not touch Tempo tools.

## 3. Tempo is the single source of truth for duration

When Tempo is active it is the **only** place work time is recorded. Therefore:

- **Do not** post "Investigation time: …" / "Implementation time: …" comments
  to Jira tickets or subtasks. Those were the old mechanism; with Tempo they
  double-count the same hours (a reader sees the Tempo worklog *and* a
  comment claiming the same duration).
- **Do not** call `addWorklogToJiraIssue`. A Tempo worklog already *is* a Jira
  worklog; a native Rovo worklog on top of it double-logs. The two mechanisms
  are mutually exclusive — Tempo wins whenever it is available.
- **There is no `work.time_log` field** (template 0.4 removed it — Tempo is
  the sole time source, full stop, no local fallback field to keep in sync).
  A pre-0.4 dossier may still carry a legacy `work.time_log` list on disk;
  treat it as historical data to read, never write to it again.

## 4. The tool owns the clock — agents do not invent timestamps

Every Tempo value is the **real** time of the call: `twipe_start_time_session`
stamps now, `twipe_finalize_time_session` stamps now and does all the
arithmetic (15-minute rounding, the 12:30–13:00 lunch break, fair-share across
concurrent sessions, sub-floor absorb-or-drop, block placement). Supply intent,
not durations; never reproduce or second-guess the math, and never hand-write an
`ended`/`started` timestamp — agents cannot read the wall clock reliably, so a
computed duration in a doc is untrustworthy. There is no retroactive logging and
no delete: if a session was missed or crossed a day boundary, run the work under
a fresh session now, or report it and let the user add the worklog by hand in
Tempo. Report the resulting block(s) back to the user.

## 5. Concurrent sessions are normal — do not treat every open one as stale

`twipe_time_session_status` may show several open sessions at once; the
fair-share model attributes overlapping wall-clock across them. So:

- An open session is a **leftover blocker** only when it is *yours from an
  earlier run for this same intended ticket*, or a *dead* session you can
  confirm is finished. Ask the user to finalize or cancel **those**.
- A session on a **different** ticket is **legitimately concurrent** — leave it
  alone. Do not propose finalizing or cancelling an unrelated active session
  just to open your own.
- Be aware that an orphaned concurrent session silently taxes every other
  session's fair share (it can pull a short session under the sub-floor and
  drop it). If you notice a session that looks orphaned, surface it to the user
  as a close-out candidate — don't cancel it on their behalf.

## 6. Ticket ordering — Tempo needs a key at start

`twipe_start_time_session(issueKey, issueId, authorAccountId)` requires a Jira
ticket the moment the session opens. Reconcile that with each command's phase
order:

- If the dossier already has a ticket (the `jira` field in its front matter),
  use it.
- If none exists yet, **settle the ticket first** — with the user's approval,
  via the Rovo flow in the `time-logging` skill — before opening the session.
  For `/work-on` this means the **Jira ticket is settled ahead of the build
  clock**, which puts Phase 1 before Phase 2. State that ordering when it
  happens.
- **One session covers the whole command.** This pipeline uses one ticket per
  dossier and no subtasks, so there is no second session to open. Finalize at
  the end of the run (`/work-on` Phase 9), and never run two sessions
  concurrently.

## 7. The DevKit skill is the source of the how-to

This file is the plugin-side contract. The operational detail (token setup,
exact tool signatures, the arithmetic) lives in the DevKit `time-logging` skill
and the `tempo` capability pack. Drive the tools through the skill; do not
duplicate its steps here.
